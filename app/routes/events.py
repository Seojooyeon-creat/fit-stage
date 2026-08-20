"""SSE 이벤트 라우트 (API명세서 §5.H).

GET /sessions/{id}/events  세션 단위 이벤트 스트림 (sse-starlette)
미러가 bind 직후 구독. 채널이 하나라 미러 구현이 단순해진다.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.services.session import session_service
from app.sse import event_bus, is_close_sentinel

router = APIRouter(prefix="/sessions", tags=["events"])

PING_INTERVAL_SECONDS = 15


async def _event_generator(request: Request, session_id: str):
    q = event_bus.subscribe(session_id)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.wait_for(q.get(), timeout=PING_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                # 15초 keep-alive
                yield {"event": "ping", "data": ""}
                continue
            if is_close_sentinel(item):
                break  # session.closed 이후 스트림 종료
            yield {
                "event": item["event"],
                "data": json.dumps(item["data"], ensure_ascii=False),
            }
    finally:
        event_bus.unsubscribe(session_id, q)


@router.get("/{session_id}/events")
async def stream_events(session_id: str, request: Request) -> EventSourceResponse:
    session_service.require(session_id)  # 없으면 404 SESSION_NOT_FOUND
    return EventSourceResponse(_event_generator(request, session_id))
