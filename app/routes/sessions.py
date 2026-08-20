"""세션 라우트 (API명세서 §5.A).

POST /sessions               밴드 발급 (익명 세션 생성)
POST /sessions/{id}/bind     방 바인딩 (밴드 탭 = 입실) + 웰컴
GET  /sessions/{id}          세션 스냅샷 (화면 복구)
POST /sessions/{id}/close    퇴실·소거
"""
from __future__ import annotations

from fastapi import APIRouter

from app.errors import product_not_found, session_closed
from app.models import (
    BindRequest,
    BindResponse,
    CloseRequest,
    CloseResponse,
    ScanRequest,
    ScanResponse,
    Session,
    SessionCreateResponse,
    SessionState,
)
from app.services.catalog import catalog_service
from app.services.session import session_service

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", status_code=201, response_model=SessionCreateResponse)
def create_session() -> SessionCreateResponse:
    s = session_service.create()
    return SessionCreateResponse(
        session_id=s.session_id, state=s.state, created_at=s.created_at
    )


@router.post("/{session_id}/bind", response_model=BindResponse)
def bind_session(session_id: str, body: BindRequest) -> BindResponse:
    welcome = session_service.bind(session_id, body.room_id)
    return BindResponse(
        session_id=session_id,
        state=session_service.require(session_id).state,
        room_id=body.room_id,
        welcome=welcome,
    )


@router.get("/{session_id}", response_model=Session)
def get_session(session_id: str) -> Session:
    return session_service.require(session_id)


@router.post("/{session_id}/close", response_model=CloseResponse)
async def close_session(session_id: str, body: CloseRequest) -> CloseResponse:
    final_state, purged = session_service.close(session_id, body.transfer)
    return CloseResponse(session_id=session_id, state=final_state, purged=purged)


@router.post("/{session_id}/scans", response_model=ScanResponse)
def scan_product(session_id: str, body: ScanRequest) -> ScanResponse:
    """플로어 NFC 태깅 시뮬 — 워치 시뮬레이터가 렌더할 Product 전체를 반환한다.

    scannedProductIds에 누적되어 컨시어지가 "매장에서 보신 ○○" 맥락으로 활용한다.
    """
    session = session_service.require(session_id)
    if session.state in (SessionState.CLOSED, SessionState.TRANSFERRED):
        raise session_closed()
    product = catalog_service.get(body.product_id)
    if product is None:
        raise product_not_found()
    if body.product_id not in session.scanned_product_ids:
        session.scanned_product_ids.append(body.product_id)
    return ScanResponse(product=product, scanned_product_ids=session.scanned_product_ids)
