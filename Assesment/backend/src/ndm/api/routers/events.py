"""The server-sent event stream (AD9).

One stream carries every event; the browser filters by project and scan.
"""

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ndm.api.events import EventHub

router = APIRouter(tags=["events"])

# A comment line is sent when nothing else was, so that proxies keep the
# connection open and a dead one is noticed.
KEEP_ALIVE_SECONDS = 15


async def _stream(hub: EventHub) -> AsyncIterator[str]:
    queue = hub.subscribe()
    try:
        yield ": connected\n\n"
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=KEEP_ALIVE_SECONDS)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                return
            kind, body = item
            yield f"event: {kind}\ndata: {body}\n\n"
    finally:
        hub.unsubscribe(queue)


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Scan state and progress, and changed hosts, as they happen. Nothing is replayed."""
    hub: EventHub = request.app.state.events
    return StreamingResponse(
        _stream(hub),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
