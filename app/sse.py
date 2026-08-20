"""SSE 인프라 (API명세서 §5.H).

세션 단위 이벤트 스트림 하나로 통일한다. 이벤트 종류:
  delivery.updated · wardrobe.updated · wardrobe.ready · keep.updated ·
  profile.updated · handoff.claimed · session.closed · ping(15초)

모든 publish/subscribe는 이벤트 루프 스레드에서 호출된다는 전제
(비동기 라우트 핸들러 및 asyncio 타이머 태스크). asyncio.Queue 사용.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

# 스트림 종료를 알리는 센티넬
_CLOSE = object()


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(session_id, set()).add(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(session_id)
        if subs is not None:
            subs.discard(q)
            if not subs:
                self._subs.pop(session_id, None)

    def publish(self, session_id: str, event: str, data: dict[str, Any]) -> None:
        for q in list(self._subs.get(session_id, ())):
            q.put_nowait({"event": event, "data": data})

    def close_stream(self, session_id: str) -> None:
        """해당 세션 구독자들에게 스트림 종료를 알린다 (session.closed 이후)."""
        for q in list(self._subs.get(session_id, ())):
            q.put_nowait(_CLOSE)

    def close_all(self) -> None:
        """전 세션 스트림 종료 (demo/reset)."""
        for session_id in list(self._subs.keys()):
            self.close_stream(session_id)


def is_close_sentinel(item: Any) -> bool:
    return item is _CLOSE


# 프로세스 전역 이벤트 버스
event_bus = EventBus()
