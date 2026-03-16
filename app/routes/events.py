"""Server-Sent Events endpoint — streams live scan and worker status."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.app_state import AppState
from app.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])

type _Auth = Annotated[str, Depends(require_auth)]

# Max seconds between heartbeat frames so the browser doesn't time out.
_HEARTBEAT_INTERVAL = 5.0


@router.get("")
async def sse_stream(
    request: Request,
    _: _Auth,
) -> StreamingResponse:
    """Stream application state as Server-Sent Events.

    The client uses ``fetch`` with a ``ReadableStream`` so that HTTP Basic Auth
    headers can be included (native ``EventSource`` does not support them).
    """
    app_state: AppState = request.app.state.app_state

    async def generate() -> AsyncIterator[bytes]:
        q = app_state.subscribe()
        try:
            # Send current state immediately on connect.
            yield _event(app_state)

            while True:
                if await request.is_disconnected():
                    break
                try:
                    await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    pass  # heartbeat — fall through and re-send state
                except asyncio.CancelledError:
                    break
                yield _event(app_state)
        finally:
            app_state.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx/proxy buffering
        },
    )


def _event(app_state: AppState) -> bytes:
    payload = json.dumps(app_state.to_dict())
    return f"data: {payload}\n\n".encode()
