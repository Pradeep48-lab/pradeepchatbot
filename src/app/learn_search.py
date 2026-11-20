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


def _normalise(text: str) -> list[dict]:
    """Turn the tool's text output into [{title, url, content}]."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return [{"title": "Microsoft Learn", "url": "", "content": text}] if text else []

    if isinstance(data, dict):
        data = data.get("results") or data.get("items") or [data]

    results = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title") or "Microsoft Learn",
            "url": item.get("contentUrl") or item.get("url") or "",
            "content": (item.get("content") or "")[:LEARN_MAX_CHARS_PER_RESULT],
        })
    return results


async def search_learn(query: str) -> list[dict]:
    """Return up to LEARN_MAX_RESULTS Microsoft Learn results for the query."""
    try:
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
                print(f"Learn MCP: tool '{LEARN_SEARCH_TOOL}' not found")
                return []
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

            text = "\n".join(
                part.get("text", "") for part in call.get("content", []) if part.get("type") == "text"
            )
            return _normalise(text)[:LEARN_MAX_RESULTS]
    except Exception as e:  # never break the chat because Learn is unavailable
        print(f"Microsoft Learn search failed: {e}")
        return []
