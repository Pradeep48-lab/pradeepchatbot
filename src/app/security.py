"""Security helpers for the chatbot API.

- RateLimiter: per-user request limits (per minute and per day)
- SecurityHeadersMiddleware: CSP, HSTS, clickjacking and MIME-sniffing protection
- check_prompt_shields: Azure AI Content Safety Prompt Shields for the user's
  question (jailbreaks) and the retrieved documents (indirect prompt injection)
"""
import asyncio
import logging
import os
import time
from collections import defaultdict, deque

import httpx
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger("chatbot.security")


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
class RateLimiter:
    """Sliding-window limits per user.

    State lives in this process's memory, so limits apply per App Service
    instance. That is exact on a single instance (B1) and approximate when
    scaled out; move to Redis or API Management if you scale out.
    """

    def __init__(self, per_minute: int, per_day: int):
        self.per_minute = per_minute
        self.per_day = per_day
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, user_id: str) -> str | None:
        """Record a request. Returns an error message if the user is over a limit."""
        now = time.monotonic()
        hits = self._hits[user_id]
        while hits and now - hits[0] > 86_400:
            hits.popleft()

        last_minute = sum(1 for t in hits if now - t <= 60)
        if self.per_minute and last_minute >= self.per_minute:
            return f"You're sending questions too quickly. The limit is {self.per_minute} per minute; please wait a moment."
        if self.per_day and len(hits) >= self.per_day:
            return f"You've reached the daily limit of {self.per_day} questions. Please try again tomorrow."

        hits.append(now)
        return None


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/static/"):
            # Scripts and styles: the browser may keep a copy but must check with the server on every
            # page load (a cheap 304 when unchanged), so a new deployment shows up immediately.
            response.headers["Cache-Control"] = "no-cache"
        else:
            # The page and API responses contain private chat data: never cache them
            response.headers["Cache-Control"] = "no-store"
        return response


# ---------------------------------------------------------------------------
# Prompt Shields (Azure AI Content Safety)
# ---------------------------------------------------------------------------
CONTENT_SAFETY_ENDPOINT = os.environ.get("AZURE_CONTENT_SAFETY_ENDPOINT", "").rstrip("/")
PROMPT_SHIELD_API_VERSION = os.environ.get("PROMPT_SHIELD_API_VERSION", "2024-09-01")
# If Content Safety is unreachable: "true" = answer anyway (logged), "false" = refuse.
PROMPT_SHIELD_FAIL_OPEN = os.environ.get("PROMPT_SHIELD_FAIL_OPEN", "true").lower() == "true"
# Conservative input caps for the Prompt Shields API
_MAX_PROMPT_CHARS = 10_000
_MAX_DOCUMENTS = 5
_MAX_DOCUMENT_CHARS_TOTAL = 10_000


class ShieldResult:
    def __init__(self, prompt_attack=False, flagged_documents=None, checked=False, error=None):
        self.prompt_attack = prompt_attack
        self.flagged_documents = flagged_documents or set()  # indexes into the documents list
        self.checked = checked
        self.error = error


async def check_prompt_shields(credential, user_prompt: str, documents: list[str]) -> ShieldResult:
    """Ask Prompt Shields whether the prompt or any document contains an attack.

    Only the first _MAX_DOCUMENTS documents are analysed (trimmed to fit the
    API's size limit); the caller should not pass more than that.
    """
    if not CONTENT_SAFETY_ENDPOINT:
        return ShieldResult(checked=False, error="not configured")

    docs = documents[:_MAX_DOCUMENTS]
    per_doc = _MAX_DOCUMENT_CHARS_TOTAL // max(len(docs), 1)
    body = {
        "userPrompt": user_prompt[:_MAX_PROMPT_CHARS],
        "documents": [d[:per_doc] for d in docs],
    }
    try:
        token = await asyncio.to_thread(credential.get_token, "https://cognitiveservices.azure.com/.default")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{CONTENT_SAFETY_ENDPOINT}/contentsafety/text:shieldPrompt",
                params={"api-version": PROMPT_SHIELD_API_VERSION},
                headers={"Authorization": f"Bearer {token.token}", "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            data = response.json()
        flagged = {
            i for i, analysis in enumerate(data.get("documentsAnalysis") or [])
            if analysis.get("attackDetected")
        }
        return ShieldResult(
            prompt_attack=bool((data.get("userPromptAnalysis") or {}).get("attackDetected")),
            flagged_documents=flagged,
            checked=True,
        )
    except Exception as e:
        log.warning("Prompt Shields check failed: %s: %s", type(e).__name__, e)
        return ShieldResult(checked=False, error=f"{type(e).__name__}")
