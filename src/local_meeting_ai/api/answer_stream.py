from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any

from fastapi.responses import StreamingResponse

from local_meeting_ai.application.answer_stream import AnswerProgress


def answer_stream(
    run: Callable[[AnswerProgress, Callable[[], bool]], Awaitable[dict[str, Any]]],
    *,
    structured: bool = False,
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        cancelled = threading.Event()

        def emit(event: dict[str, Any]) -> None:
            if not cancelled.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, event)

        async def produce() -> None:
            try:
                result = await run(AnswerProgress(emit, structured=structured), cancelled.is_set)
                emit({"type": "done", "result": result})
            except asyncio.CancelledError:
                raise
            except Exception as error:
                emit({"type": "error", "message": str(error) or "Answer generation failed"})

        task = asyncio.create_task(produce())
        try:
            yield json.dumps({"type": "status", "phase": "preparing"}) + "\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    event = {"type": "status", "phase": "working"}
                yield json.dumps(event, ensure_ascii=True) + "\n"
                if event["type"] in {"done", "error"}:
                    break
        finally:
            cancelled.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        events(), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
