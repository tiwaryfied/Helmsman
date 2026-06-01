"""Server-Sent Events helpers."""
from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi.responses import StreamingResponse


def _format(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def sse_response(generator: AsyncIterator[dict]) -> StreamingResponse:
    async def _wrap() -> AsyncIterator[bytes]:
        try:
            async for event in generator:
                yield _format(event).encode("utf-8")
        except Exception as exc:
            yield _format({"type": "error", "message": f"stream error: {exc}"}).encode("utf-8")
        yield b"data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        _wrap(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
