"""Questions about the knowledge base itself: which files and tickets exist, how many, what was
uploaded or indexed recently. A search can't answer these (it only returns the few best passages),
so they are answered from the real file list in the storage account and the search index.

Examples: "what are the recent tickets uploaded?", "how many VPN tickets in the last month?",
"is everything indexed?"
"""
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from assistant import fast_completion, source_details

log = logging.getLogger("chatbot.inventory")

MAX_BLOBS = int(os.environ.get("INVENTORY_MAX_FILES", "20000"))        # files listed from storage
MAX_INDEX_CHUNKS = int(os.environ.get("INVENTORY_MAX_CHUNKS", "100000"))  # index entries scanned
CACHE_SECONDS = int(os.environ.get("INVENTORY_CACHE_SECONDS", "300"))     # file list is reused for 5 minutes
MAX_ROWS = int(os.environ.get("INVENTORY_MAX_ROWS", "20"))                # rows shown in an answer
MAX_READS = int(os.environ.get("INVENTORY_MAX_READS", "500"))             # ticket files opened to read dates/details
READ_BYTES = 16 * 1024                                                    # only the start of each file is read
TEXT_EXTENSIONS = (".txt", ".md", ".log", ".json", ".csv", ".html", ".htm", ".xml", ".eml")
DAY_FIRST = os.environ.get("DATE_DAY_FIRST", "true").lower() != "false"   # 03/04/2026 = 3 April

# Cheap first filter, so normal "how do I fix..." questions don't pay for an extra model call
_HINT = re.compile(
    r"(?i)\b(how many|count|number of|total|list( all| the| of)?|recent(ly)?|latest|newest|new (tickets|files|documents)|"
    r"last \d+|past \d+|last (week|month|year|few)|since|this (week|month|year)|uploaded|indexed|indexing|added|ingested)\b")
_COLLECTION = re.compile(
    r"(?i)\b(tickets?|incidents?|cases?|files?|documents?|docs|runbooks?|articles?|wikis?|kb|knowledge ?base|"
    r"storage( account)?|index(ed|er)?|uploads?)\b")


def looks_like_inventory(question: str) -> bool:
    return len(question) <= 300 and bool(_HINT.search(question) and _COLLECTION.search(question))


PARSE_PROMPT = """Decide whether a support engineer is asking about the knowledge base ITSELF (which files or
tickets exist, how many there are, what was uploaded or indexed recently, counts over a period) rather
than asking how to solve a problem. "Which ticket fixed error 809?" is a problem question (false).
Today is {today}.

Reply with JSON only:
{{"inventory": true|false,
  "action": "list" | "count" | "status",
  "kind": "ticket" | "runbook" | "document" | "any",
  "topic": "<product or keyword to match, e.g. VPN; empty if none>",
  "days": <how many days back, e.g. last month = 30, last week = 7, this month = days since the 1st; null if no period>,
  "date_basis": "uploaded" | "created" | "closed"}}
- action "status" = whether files are indexed / searchable / pending.
- date_basis: "closed" when they ask about tickets closed / resolved / fixed in a period; "uploaded" when they ask
  about uploading, adding or indexing; otherwise "created" (tickets created / opened / raised).
- Use the earlier conversation to resolve follow-ups like "and last week?"."""


def parse_inventory_question(openai_client, question: str, history: list[dict]) -> dict | None:
    """Fast-model check and parse. Returns a spec dict, or None for a normal question."""
    convo = "\n".join(f"{m['role'].upper()}: {str(m['content'])[:300]}" for m in history[-4:]) or "(none)"
    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    try:
        response = fast_completion(
            openai_client,
            messages=[{"role": "system", "content": PARSE_PROMPT.format(today=today)},
                      {"role": "user", "content": f"Earlier conversation:\n{convo}\n\nLatest message:\n{question}"}],
            temperature=0, max_completion_tokens=120, response_format={"type": "json_object"})
        spec = json.loads(response.choices[0].message.content or "{}")
    except Exception as e:
        log.warning("Inventory question check failed, treating as a normal question: %s: %s", type(e).__name__, e)
        return None
    if spec.get("inventory") is not True:
        return None
    days = spec.get("days")
    return {
        "action": spec.get("action") if spec.get("action") in ("list", "count", "status") else "list",
        "kind": spec.get("kind") if spec.get("kind") in ("ticket", "runbook", "document") else "any",
        "topic": re.sub(r"[^\w .\-/]", "", str(spec.get("topic") or ""))[:60].strip(),
        "days": int(days) if isinstance(days, (int, float)) and 0 < days <= 3660 else None,
        "date_basis": spec.get("date_basis") if spec.get("date_basis") in ("uploaded", "closed") else "created",
    }


# ---------------------------------------------------------------------------
# File list (storage) and indexed files (search index), cached for a few minutes
# ---------------------------------------------------------------------------
_cache: dict = {}
_cache_lock = threading.Lock()


def _cached(key: str, loader):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def list_files(source_container) -> tuple[list[dict], bool]:
    """[{name, uploaded, size}], truncated?  Needs Storage Blob Data Reader (already used for /source)."""
    def load():
        files, truncated = [], False
        for blob in source_container.list_blobs():
            if len(files) >= MAX_BLOBS:
                truncated = True
                break
            if blob.name.endswith("/"):
                continue
            files.append({"name": blob.name,
                          "uploaded": blob.creation_time or blob.last_modified,
                          "size": blob.size or 0})
        return files, truncated
    return _cached("files", load)


def indexed_files(search_client, blob_path) -> set | None:
    """Paths of files that have at least one entry in the search index; None if the index is too big to scan."""
    def load():
        seen, count = set(), 0
        for doc in search_client.search(search_text="*", select=["parent_id", "title"], top=MAX_INDEX_CHUNKS + 1):
            count += 1
            if count > MAX_INDEX_CHUNKS:
                return None
            seen.add(blob_path(doc.get("parent_id"), doc.get("title") or ""))
        return seen
    return _cached("indexed", load)


def topic_matches(search_client, blob_path, topic: str) -> dict:
    """{path: text} for files whose name or indexed text contains the topic keywords (all of them)."""
    out: dict[str, str] = {}
    for doc in search_client.search(search_text=topic, search_mode="all",
                                    select=["parent_id", "title", "content"], top=1000):
        path = blob_path(doc.get("parent_id"), doc.get("title") or "")
        out[path] = out.get(path, "") + "\n" + str(doc.get("content") or "")[:4000]
    return out


# ---------------------------------------------------------------------------
# Dates written inside tickets ("Created: 12 Mar 2026", "Date closed - 2026-03-14")
# ---------------------------------------------------------------------------
_TICKET_DATE = re.compile(
    r"(?im)^[ \t]*(created(?: on| date)?|date created|opened(?: on)?|reported(?: on)?|"
    r"date closed|closed(?: on| date)?|resolved(?: on)?)[ \t]*[:\-][ \t]*(.{6,40}?)[ \t]*$")
_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y",
            "%B %d %Y", "%d-%b-%Y", "%d-%B-%Y", "%d %b, %Y", "%d %B, %Y"]


def parse_date(text: str) -> datetime | None:
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text.strip().rstrip("."))
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    head = re.split(r"[ T]\d{1,2}:\d{2}", text)[0].strip()
    slash = ["%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y"]
    for fmt in _FORMATS + (slash if DAY_FIRST else slash[1:2] + slash[0:1] + slash[3:] + slash[2:3]):
        try:
            return datetime.strptime(head, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def ticket_dates(text: str) -> dict:
    """{"created": datetime|None, "closed": datetime|None} from the ticket's own date lines."""
    found = {}
    for m in _TICKET_DATE.finditer(text or ""):
        key = "created" if re.match(r"(?i)(created|date created|opened|reported)", m.group(1)) else "closed"
        if found.get(key) is None:
            found[key] = parse_date(m.group(2))
    return found


_ISSUE = re.compile(r"(?im)^[ \t]*(?:issue|problem|summary|subject|symptoms?|description)[ \t]*[:\-][ \t]*(.*)$")
_RESOLUTION = re.compile(r"(?im)^[ \t]*(?:resolution|solution|fix(?: applied)?|resolved by|root cause)[ \t]*[:\-][ \t]*(.*)$")


def _line_after(match, text: str) -> str:
    """The value after 'Issue:' or, if that line is empty, the next non-empty line."""
    value = match.group(1).strip()
    if not value:
        rest = [l.strip() for l in text[match.end():].splitlines() if l.strip()]
        value = rest[0] if rest else ""
    return value


def ticket_summary(text: str) -> dict:
    """{"issue", "resolution"}: short versions of the ticket's Issue / Resolution lines."""
    out = {}
    for key, rx in (("issue", _ISSUE), ("resolution", _RESOLUTION)):
        m = rx.search(text or "")
        value = _line_after(m, text) if m else ""
        out[key] = (value[:117] + "…") if len(value) > 120 else value
    return out


_text_cache: dict = {}


def read_heads(source_container, rows: list[dict]) -> None:
    """Read the first 16 KB of each text file (in parallel) into row["text"]. Cached per file version."""
    def read(r):
        key = (r["name"], r["uploaded"])
        if key in _text_cache:
            return _text_cache[key]
        try:
            data = source_container.get_blob_client(r["name"]).download_blob(offset=0, length=READ_BYTES).readall()
            text = data.decode("utf-8", errors="replace")
        except Exception as e:
            log.warning("Reading %s failed: %s: %s", r["name"], type(e).__name__, e)
            return None
        if len(_text_cache) > 5000:
            _text_cache.clear()
        _text_cache[key] = text
        return text
    todo = [r for r in rows if r["name"].lower().endswith(TEXT_EXTENSIONS)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for r, text in zip(todo, pool.map(read, todo)):
            if text:
                r["text"] = text + ("\n" + r["text"] if r.get("text") else "")


# ---------------------------------------------------------------------------
# Answer
# ---------------------------------------------------------------------------
_KIND_WORD = {"ticket": "tickets", "runbook": "runbooks", "document": "documents", "any": "files"}


def _fmt(d: datetime | None) -> str:
    return d.strftime("%d %b %Y") if d else "—"


def _cell(text: str | None) -> str:
    text = re.sub(r"[`|\r\n]", " ", str(text or "")).strip()
    return f"`{text}`" if text else "—"


def answer_inventory(spec: dict, source_container, search_client, blob_path) -> str:
    if source_container is None:
        return ("### File list not available\n\nListing the knowledge base files isn't configured "
                "(AZURE_STORAGE_ACCOUNT_URL is not set).")
    try:
        files, truncated = list_files(source_container)
    except Exception as e:
        log.warning("Listing storage files failed: %s: %s", type(e).__name__, e)
        return ("### File list not available\n\nI couldn't read the file list from the storage account. "
                "The app needs the **Storage Blob Data Reader** role on the `documents` container.")
    try:
        indexed = indexed_files(search_client, blob_path)
    except Exception as e:
        log.warning("Reading the search index failed: %s: %s", type(e).__name__, e)
        indexed = None

    now = datetime.now(timezone.utc)
    word = _KIND_WORD[spec["kind"]]

    if spec["action"] == "status":
        return _status_answer(files, truncated, indexed, now)

    # Which files: type, topic
    rows = []
    for f in files:
        details = source_details(os.path.basename(f["name"]), [])
        rows.append({**f, "kind": details["kind"], "text": None})
    kind_note = ""
    if spec["kind"] != "any":
        typed = [r for r in rows if r["kind"].lower() == spec["kind"]]
        if typed:
            rows = typed
        else:
            kind_note = f" None of the file names look like {word}, so all files are counted."
            word = "files"
    topic = spec["topic"]
    if topic:
        try:
            matches = topic_matches(search_client, blob_path, topic)
        except Exception as e:
            log.warning("Topic search failed: %s: %s", type(e).__name__, e)
            matches = {}
        low = topic.lower()
        rows = [{**r, "text": matches.get(r["name"])} for r in rows
                if r["name"] in matches or low in r["name"].lower()]

    # Which date: the ticket's own Created / Closed line, or the upload date
    basis = spec["date_basis"]
    since = now - timedelta(days=spec["days"]) if spec["days"] else None
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    this_month = bool(since) and abs((since.date() - month_start.date()).days) <= 1
    if this_month:
        since = month_start                                    # "this month" = since the 1st
    read_note = ""
    if basis != "uploaded" or spec["kind"] == "ticket":
        # A ticket is uploaded after it was created or closed, so only files uploaded since the start of the
        # period can match; without a period, the most recently uploaded ones are opened.
        candidates = sorted([r for r in rows if not since or (r["uploaded"] and r["uploaded"] >= since)],
                            key=lambda r: r["uploaded"], reverse=True)
        limit = MAX_READS if since else MAX_ROWS * 2
        if since and len(candidates) > limit:
            read_note = f" Only the {limit} most recently uploaded files were opened."
        if since:
            rows = candidates
        read_heads(source_container, candidates[:limit])
    own_dates = 0
    for r in rows:
        own = ticket_dates(r["text"]).get(basis) if basis != "uploaded" and r["text"] else None
        r["date"], r["own_date"] = own or r["uploaded"], bool(own)
        r.update(ticket_summary(r["text"]) if r["text"] else {"issue": "", "resolution": ""})
        own_dates += bool(own)
    if since:
        rows = [r for r in rows if r["date"] and r["date"] >= since]
    rows.sort(key=lambda r: r["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    verb = {"closed": " closed", "created": " created", "uploaded": " uploaded"}[basis]
    about = f" about “{topic}”" if topic else ""
    period = " this month" if this_month else f" in the last {spec['days']} days" if spec["days"] else ""
    since_text = f" (since {_fmt(since)})" if since else ""
    title = (f"{topic} {word}" if topic else f"{'Recent ' if not since else ''}{word}") + (verb + period if since else "")
    lines = [f"### {title[:1].upper() + title[1:]}", ""]
    if not rows:
        lines.append(f"I found **no {word}**{about}{verb if since else ''}{period}{since_text} in the knowledge base.{kind_note}")
    else:
        noun = word if len(rows) != 1 else word.rstrip("s")
        lines.append(f"**{len(rows)} {noun}**{about}{verb if since else ''}{period}{since_text}.{kind_note}")
        shown = rows[:MAX_ROWS]
        details = any(r["issue"] or r["resolution"] for r in shown)
        date_head = {"closed": "Closed", "created": "Created", "uploaded": "Uploaded"}[basis]
        # With Issue/Resolution the table is wide, so "not indexed yet" goes in a line below it instead
        head = ["File", date_head, "Product"] + (["Issue", "Resolution"] if details else ["Indexed"])
        lines += ["", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        for r in shown:
            product = source_details(os.path.basename(r["name"]), [{"content": r["text"] or ""}]).get("product")
            state = "—" if indexed is None else ("Yes" if r["name"] in indexed else "Not yet")
            date = _fmt(r["date"]).replace(" ", "\u00a0") + ("" if r["own_date"] or basis == "uploaded" else " (uploaded)")
            cells = [_cell(r["name"]), date, _plain(product)]
            if details:
                cells += [_plain(r["issue"]), _plain(r["resolution"])]
            lines.append("| " + " | ".join(cells + ([] if details else [state])) + " |")
        if len(rows) > MAX_ROWS:
            lines.append(f"\n…and {len(rows) - MAX_ROWS} more.")
        pending = [r["name"] for r in shown if indexed is not None and r["name"] not in indexed]
        if details and pending:
            lines.append("\n**Not indexed yet** (the chatbot can't search these until the indexer's next run): "
                         + ", ".join(_cell(n) for n in pending))

    notes = ["Counted from the file list in the storage account"
             + (f" (first {MAX_BLOBS} files only)" if truncated else "") + "." + read_note]
    if topic:
        notes.append(f"A file counts when its name or text contains “{topic}”.")
    if basis != "uploaded" and rows:
        line = "Date closed" if basis == "closed" else "Created"
        notes.append(f"Dates come from each ticket's “{line}:” line; “(uploaded)” means the file has no such line, so its upload date was used."
                     if own_dates else f"None of these files has a “{line}:” line, so upload dates were used.")
    lines += ["", "_" + " ".join(notes) + "_"]
    return "\n".join(lines)


def _plain(text: str | None) -> str:
    """Table cell text from a ticket: no table breaks, links or HTML."""
    text = re.sub(r"<[^>]{0,200}>", "", str(text or ""))           # drop HTML tags
    text = re.sub(r"[|\r\n<>\[\]`*_]", " ", text)
    return re.sub(r"\s+", " ", text).strip() or "—"


def _status_answer(files, truncated, indexed, now) -> str:
    lines = ["### Knowledge base status", ""]
    lines.append(f"- **{len(files)}{'+' if truncated else ''} files** in the storage account")
    if indexed is None:
        lines.append("- Indexed: the index is too large to compare file by file here; check the indexer in the portal.")
        pending = []
    else:
        pending = [f for f in files if f["name"] not in indexed]
        lines.append(f"- **{len(files) - len(pending)} indexed** and searchable")
        lines.append(f"- **{len(pending)} not indexed yet**" + (" (new files are picked up on the indexer's next run)" if pending else ""))
    recent = [f for f in files if f["uploaded"] and f["uploaded"] >= now - timedelta(days=7)]
    lines.append(f"- **{len(recent)} uploaded** in the last 7 days")
    latest = max((f["uploaded"] for f in files if f["uploaded"]), default=None)
    if latest:
        lines.append(f"- Latest upload: {latest.strftime('%d %b %Y %H:%M')} UTC")
    if pending:
        lines += ["", "**Not indexed yet:**", ""]
        for f in sorted(pending, key=lambda f: f["uploaded"] or now, reverse=True)[:MAX_ROWS]:
            lines.append(f"- {_cell(f['name'])}, uploaded {_fmt(f['uploaded'])}")
        if len(pending) > MAX_ROWS:
            lines.append(f"- …and {len(pending) - MAX_ROWS} more")
        lines += ["", "If a file stays here after the next indexer run, check the indexer's errors in the portal "
                  "(unsupported format, file too large, or empty file)."]
    return "\n".join(lines)
