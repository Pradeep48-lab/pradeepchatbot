"""Search Microsoft Learn through Microsoft's public Learn MCP server.

The server (https://learn.microsoft.com/api/mcp) is free, needs no key, and
speaks the Model Context Protocol over Streamable HTTP. This module is a
small dependency-free client (just httpx) that does the three calls needed:
initialize -> tools/list (to find the tool's argument name) -> tools/call.

Any failure (network, timeout, protocol change) is logged and returns an
empty list, so the chatbot keeps answering from the internal knowledge base.
"""
import json
import os
import re

import httpx

LEARN_MCP_URL = os.environ.get("LEARN_MCP_URL", "https://learn.microsoft.com/api/mcp")
LEARN_SEARCH_TOOL = "microsoft_docs_search"
LEARN_TIMEOUT_SECONDS = float(os.environ.get("LEARN_TIMEOUT_SECONDS", "10"))
LEARN_MAX_RESULTS = int(os.environ.get("LEARN_MAX_RESULTS", "3"))
LEARN_MAX_CHARS_PER_RESULT = 2500

_PROTOCOL_VERSION = "2025-06-18"
_BASE_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _parse_rpc(response: httpx.Response, request_id: int) -> dict:
    """Return the JSON-RPC message for request_id from a JSON or SSE response."""
    response.raise_for_status()
    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if not payload:
                    continue
                message = json.loads(payload)
                if message.get("id") == request_id:
                    return message
        raise ValueError("No JSON-RPC response found in event stream")
    return response.json()


def _result(message: dict) -> dict:
    if "error" in message:
        raise RuntimeError(f"MCP error: {message['error']}")
    return message["result"]


_URL_RE = re.compile(r"https://learn\.microsoft\.com/[^\s)\]\"'>]+")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https://learn\.microsoft\.com/[^\s)]+)\)")


def _items_from_json(data) -> list[dict]:
    """Accept a list of results, {"results": [...]}, or a single result object."""
    if isinstance(data, dict):
        for key in ("results", "items", "documents", "value"):
            if isinstance(data.get(key), list):
                return [d for d in data[key] if isinstance(d, dict)]
        return [data]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def _to_result(item: dict) -> dict:
    return {
        "title": item.get("title") or item.get("name") or "Microsoft Learn",
        "url": item.get("contentUrl") or item.get("url") or item.get("link") or "",
        "content": (item.get("content") or item.get("snippet") or item.get("description") or "")[:LEARN_MAX_CHARS_PER_RESULT],
    }


def _results_from_text(text: str) -> list[dict]:
    """Fallback for plain-text/Markdown output: pull out the Learn links it contains."""
    results, seen = [], set()
    for title, url in _MD_LINK_RE.findall(text):
        if url not in seen:
            seen.add(url)
            results.append({"title": title, "url": url, "content": ""})
    for url in _URL_RE.findall(text):
        url = url.rstrip(".,;")
        if url not in seen:
            seen.add(url)
            results.append({"title": url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").capitalize(), "url": url, "content": ""})
    if results:
        # Give the model the full text as context on the first result
        results[0]["content"] = text[:LEARN_MAX_CHARS_PER_RESULT * LEARN_MAX_RESULTS]
    elif text.strip():
        results.append({"title": "Microsoft Learn", "url": "", "content": text[:LEARN_MAX_CHARS_PER_RESULT]})
    return results


def _normalise(call_result: dict) -> list[dict]:
    """Turn a tools/call result into [{title, url, content}], whatever shape it comes in."""
    # 1. Structured output (newer MCP servers)
    structured = call_result.get("structuredContent")
    if structured:
        items = _items_from_json(structured)
        if isinstance(structured, dict) and len(items) == 1 and "result" in structured:
            try:
                items = _items_from_json(json.loads(structured["result"]))
            except (TypeError, json.JSONDecodeError):
                pass
        results = [_to_result(i) for i in items if i.get("contentUrl") or i.get("url") or i.get("content")]
        if results:
            return results

    # 2. Text parts: either one JSON document, or one JSON object per part
    texts = [p.get("text", "") for p in call_result.get("content", []) if p.get("type") == "text"]
    results = []
    for text in texts:
        try:
            results.extend(_to_result(i) for i in _items_from_json(json.loads(text)))
        except (TypeError, json.JSONDecodeError):
            results.extend(_results_from_text(text))
    return results


async def _call_search(query: str) -> dict:
    """Run microsoft_docs_search and return the raw MCP tools/call result (raises on failure)."""
    async with httpx.AsyncClient(timeout=LEARN_TIMEOUT_SECONDS) as client:
        headers = dict(_BASE_HEADERS)

        init = await client.post(LEARN_MCP_URL, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "azure-ms-cloud-chatbot", "version": "1.0"},
            },
        })
        init_result = _result(_parse_rpc(init, 1))
        session_id = init.headers.get("mcp-session-id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        headers["MCP-Protocol-Version"] = init_result.get("protocolVersion", _PROTOCOL_VERSION)

        await client.post(LEARN_MCP_URL, headers=headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })

        # Discover the argument name at runtime, as Microsoft recommends.
        tools = _result(_parse_rpc(await client.post(LEARN_MCP_URL, headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
        }), 2)).get("tools", [])
        tool = next((t for t in tools if t.get("name") == LEARN_SEARCH_TOOL), None)
        if tool is None:
            raise RuntimeError(f"Tool '{LEARN_SEARCH_TOOL}' not offered by {LEARN_MCP_URL}")
        schema = tool.get("inputSchema") or {}
        arg_name = (schema.get("required") or list((schema.get("properties") or {}).keys()) or ["query"])[0]

        call = _result(_parse_rpc(await client.post(LEARN_MCP_URL, headers=headers, json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": LEARN_SEARCH_TOOL, "arguments": {arg_name: query}},
        }), 3))

        if session_id:
            try:
                await client.delete(LEARN_MCP_URL, headers=headers)
            except httpx.HTTPError:
                pass

        return call


async def search_learn(query: str) -> list[dict]:
    """Return up to LEARN_MAX_RESULTS Microsoft Learn results for the query."""
    try:
        results = _normalise(await _call_search(query))
        results = [r for r in results if r["url"] or r["content"]][:LEARN_MAX_RESULTS]
        print(f"Microsoft Learn search: {len(results)} result(s), "
              f"{sum(1 for r in results if r['url'])} with links")
        return results
    except Exception as e:  # never break the chat because Learn is unavailable
        print(f"Microsoft Learn search failed: {type(e).__name__}: {e}")
        return []


async def learn_diagnostics(query: str) -> dict:
    """What the Learn server returned and how it was parsed (for the /learn-test page)."""
    try:
        call = await _call_search(query)
        return {
            "ok": True,
            "endpoint": LEARN_MCP_URL,
            "parsed_results": _normalise(call)[:LEARN_MAX_RESULTS],
            "raw_response_preview": json.dumps(call, ensure_ascii=False)[:4000],
        }
    except Exception as e:
        return {"ok": False, "endpoint": LEARN_MCP_URL, "error": f"{type(e).__name__}: {e}"}
