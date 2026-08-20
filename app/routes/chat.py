"""컨시어지 대화 라우트 (API명세서 §5.B) — 유일한 지능 진입점.

POST /sessions/{id}/messages  대화 턴 (LLM tool-use 완주 → text + uiActions)
GET  /sessions/{id}/messages  대화 이력 (복구·동기화)

폴백 정책 (기능명세서 FR-C1 / NFR-R3):
  - 키 없음 → 즉시 세이프 모드
  - LLM 1회 실패 → 503 LLM_UNAVAILABLE (재시도 유도)
  - LLM 2회 연속 실패 → 세이프 모드
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.agent import concierge, safemode
from app.agent.concierge import LLMError, is_llm_available
from app.errors import invalid_state, llm_unavailable, session_closed
from app.models import (
    MessageCreateRequest,
    MessageRole,
    MessagesHistoryResponse,
    MessagesResponse,
    SessionState,
)
from app.services.session import session_service

router = APIRouter(prefix="/sessions", tags=["chat"])


def _ensure_active(session) -> None:
    if session.state in (SessionState.CLOSED, SessionState.TRANSFERRED):
        raise session_closed()
    if session.state != SessionState.BOUND:
        raise invalid_state("먼저 방에 입실해 주세요.")


@router.post("/{session_id}/messages", response_model=MessagesResponse)
async def post_message(session_id: str, body: MessageCreateRequest) -> MessagesResponse:
    session = session_service.require(session_id)
    _ensure_active(session)

    # 사용자 발화 기록 (표시용)
    session_service.add_message(session_id, MessageRole.USER, body.text)

    # 키 없음 → 세이프 모드
    if not is_llm_available():
        return safemode.handle(session, body.text)

    # LLM 경로 (실패 시 폴백 정책)
    try:
        response = await concierge.run(session, body.text)
    except LLMError:
        n = concierge.record_failure(session_id)
        if n >= 2:
            concierge.reset_failures(session_id)
            return safemode.handle(session, body.text)
        raise llm_unavailable()
    else:
        concierge.reset_failures(session_id)
        return response


@router.get("/{session_id}/messages", response_model=MessagesHistoryResponse)
def get_messages(
    session_id: str, after: Optional[str] = Query(None)
) -> MessagesHistoryResponse:
    session_service.require(session_id)
    return MessagesHistoryResponse(
        messages=session_service.get_messages(session_id, after=after)
    )
