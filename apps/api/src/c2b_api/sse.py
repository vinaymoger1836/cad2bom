"""Server-Sent Events helpers: a replayable job stream and a public demo ticker."""

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request
from fastapi.responses import StreamingResponse

from c2b_jobs import repo
from c2b_jobs.repo import Pool

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",  # tell proxies not to buffer
    "Connection": "keep-alive",
}
POLL_S = 0.5
KEEPALIVE_S = 15.0
MAX_STREAM_S = 15 * 60


def frame(event: str, data: object, event_id: int | None = None) -> str:
    out = ""
    if event_id is not None:
        out += f"id: {event_id}\n"
    out += f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
    return out


async def job_stream(request: Request, pool: Pool, job_id: UUID, after: int) -> AsyncIterator[str]:
    """Replay events after `after`, then follow new ones until the client leaves."""
    last = after
    idle = 0.0
    elapsed = 0.0
    yield ": stream open\n\n"
    while elapsed < MAX_STREAM_S:
        if await request.is_disconnected():
            return
        events = await repo.events_after(pool, job_id, last)
        for ev in events:
            last = ev.id
            yield frame(ev.kind, ev.model_dump(mode="json"), ev.id)
        if events:
            idle = 0.0
            continue
        await asyncio.sleep(POLL_S)
        idle += POLL_S
        elapsed += POLL_S
        if idle >= KEEPALIVE_S:
            idle = 0.0
            yield ": keepalive\n\n"


def job_stream_response(
    request: Request, pool: Pool, job_id: UUID, after: int
) -> StreamingResponse:
    return StreamingResponse(
        job_stream(request, pool, job_id, after),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


DEMO_STEPS = [
    "Reading the CAD file",
    "Finding the parts",
    "Recognising standard parts",
    "Working out materials and masses",
    "Building the structure",
    "Checking for gaps",
    "Preparing questions for you",
    "Scoring confidence",
    "Almost there",
    "Done (this was only a demo)",
]


async def demo_stream(request: Request) -> AsyncIterator[str]:
    """Public, data-free ticker that shows the progress pattern. Capped at ten events."""
    total = len(DEMO_STEPS)
    for i, step in enumerate(DEMO_STEPS, start=1):
        if await request.is_disconnected():
            return
        yield frame("progress", {"step": i, "total": total, "message": step}, i)
        await asyncio.sleep(0.4)
    yield frame("done", {"total": total}, total + 1)


def demo_response(request: Request) -> StreamingResponse:
    return StreamingResponse(
        demo_stream(request), media_type="text/event-stream", headers=SSE_HEADERS
    )
