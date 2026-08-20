"""핸드오프 라우트 (API명세서 §5.G) — 오프→온 연결 (심사 Q3 "연결성").

POST /sessions/{id}/handoff  티켓 발급 (스냅샷 동결)
GET  /handoff/{code}         폰에서 세션 이어받기 (스냅샷만으로 성립)
"""
from __future__ import annotations

from fastapi import APIRouter

from app.errors import handoff_expired, handoff_not_found, session_closed
from app.models import HandoffTicket, SessionState
from app.services.handoff import handoff_service
from app.services.session import session_service
from app.sse import event_bus

router = APIRouter(tags=["handoff"])


@router.post(
    "/sessions/{session_id}/handoff", status_code=201, response_model=HandoffTicket
)
def issue_handoff(session_id: str) -> HandoffTicket:
    session = session_service.require(session_id)
    if session.state == SessionState.CLOSED:
        raise session_closed()
    orders = session_service.get_orders(session_id)
    return handoff_service.issue(session, orders=orders)


@router.get("/handoff/{code}", response_model=HandoffTicket)
def take_handoff(code: str) -> HandoffTicket:
    rec = handoff_service.get_record(code)
    if rec is None:
        raise handoff_not_found()
    if handoff_service.is_expired(code):
        handoff_service.drop(code)
        raise handoff_expired()

    # 최초 조회를 클레임으로 간주 → 미러에 handoff.claimed 발행
    if handoff_service.claim(code):
        event_bus.publish(rec.session_id, "handoff.claimed", {"code": code})

    # 폰 화면은 이 스냅샷만으로 렌더된다 (세션 소거 후에도 동작)
    return rec.ticket
