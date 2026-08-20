"""결제 라우트 (API명세서 §5.F).

POST /sessions/{id}/checkout  방내 결제(목업, 항상 성공)
"""
from __future__ import annotations

from fastapi import APIRouter

from app.errors import invalid_state, session_closed
from app.models import CheckoutRequest, CheckoutResponse, SessionState
from app.services.order import order_service
from app.services.session import session_service

router = APIRouter(prefix="/sessions", tags=["checkout"])


@router.post("/{session_id}/checkout", response_model=CheckoutResponse)
async def checkout(session_id: str, body: CheckoutRequest) -> CheckoutResponse:
    session = session_service.require(session_id)
    if session.state in (SessionState.CLOSED, SessionState.TRANSFERRED):
        raise session_closed()
    if session.state != SessionState.BOUND:
        raise invalid_state("먼저 방에 입실해 주세요.")

    items = [it.model_dump(by_alias=True) for it in body.items]
    order = order_service.checkout(session, items or None, method=body.method)
    return CheckoutResponse(
        order_id=order["orderId"],
        status=order["status"],
        total=order["total"],
        message=order["message"],
    )
