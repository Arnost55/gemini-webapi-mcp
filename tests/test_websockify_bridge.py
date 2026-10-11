import asyncio

from gemini_webapi_mcp import _WebsockifyCompat


def _scope(*, query: str, origin: str = "") -> dict:
    headers = []
    if origin:
        headers.append((b"origin", origin.encode()))
    return {
        "type": "websocket",
        "path": "/websockify",
        "query_string": query.encode(),
        "headers": headers,
    }


def _run_bridge(scope: dict) -> list[dict]:
    events: list[dict] = []

    async def receive() -> dict:
        return {"type": "websocket.disconnect"}

    async def send(message: dict) -> None:
        events.append(message)

    asyncio.run(_WebsockifyCompat(None)._bridge(scope, receive, send))
    return events


def test_websockify_requires_capability_token() -> None:
    events = _run_bridge(_scope(query=""))

    assert events == [
        {
            "type": "websocket.close",
            "code": 1008,
            "reason": "viewer token required",
        }
    ]


def test_websockify_rejects_untrusted_origin(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_PUBLIC_URL", "https://gemini.example")

    events = _run_bridge(
        _scope(query="token=opaque-capability", origin="https://evil.example")
    )

    assert events == [
        {
            "type": "websocket.close",
            "code": 1008,
            "reason": "origin not allowed",
        }
    ]
