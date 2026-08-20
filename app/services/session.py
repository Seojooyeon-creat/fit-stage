"""세션 라이프사이클 서비스 (API명세서 §3.1, §5.A / 플로우차트 ⑤).

CREATED (밴드 발급) → BOUND (방 바인딩) → CLOSED (전량 소거) / TRANSFERRED (티켓만 생존)
익명 세션 — 개인정보 없음. 삭제가 기본값.
"""
from __future__ import annotations

import logging
from typing import Optional

from typing import Any

from app.errors import invalid_state, room_occupied, session_closed, session_not_found
from app.models import (
    Message,
    MessageRole,
    Session,
    SessionState,
    ShowChips,
    Welcome,
    WelcomeMessage,
)
from app.services.delivery import delivery_service
from app.services.handoff import handoff_service
from app.services.room import room_service
from app.sse import event_bus
from app.util import gen_id, now_kst_iso

logger = logging.getLogger("fitstage.session")

# 정적 웰컴 문구 (API명세서 §5.A) — 개인화는 다음 단계(P1)
WELCOME_TEXT = "어서 오세요. 오늘은 어떤 자리를 위한 옷인가요?"
WELCOME_CHIPS = ["다음 주 면접이에요", "데이트 룩 추천해줘", "그냥 둘러볼게요"]


class SessionService:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._room_bindings: dict[str, str] = {}  # room_id -> session_id
        # 대화 이력 (세션 컨텍스트) — 삭제가 기본값이므로 세션과 함께 소거된다
        self._transcripts: dict[str, list[dict[str, Any]]] = {}  # LLM 원본 메시지
        self._messages: dict[str, list[Message]] = {}  # 표시용 Message[]
        self._orders: dict[str, list[dict[str, Any]]] = {}  # 결제 주문 (핸드오프 스냅샷용)

    # --- 조회 ----------------------------------------------------------
    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def require(self, session_id: str) -> Session:
        s = self._sessions.get(session_id)
        if s is None:
            raise session_not_found()
        return s

    # --- 생성 (밴드 발급) ----------------------------------------------
    def create(self) -> Session:
        session_id = gen_id("s")
        session = Session(
            session_id=session_id,
            state=SessionState.CREATED,
            created_at=now_kst_iso(),
        )
        self._sessions[session_id] = session
        logger.info("session %s created", session_id)
        return session

    # --- 방 바인딩 (밴드 탭 = 입실) ------------------------------------
    def bind(self, session_id: str, room_id: str) -> Welcome:
        s = self.require(session_id)
        if s.state in (SessionState.CLOSED, SessionState.TRANSFERRED):
            raise session_closed()
        if s.state == SessionState.BOUND:
            raise invalid_state("이미 입실한 세션이에요.")

        occupant = self._room_bindings.get(room_id)
        if occupant is not None and occupant != session_id:
            raise room_occupied()

        s.state = SessionState.BOUND
        s.room_id = room_id
        self._room_bindings[room_id] = session_id
        room_service.reset_room(room_id)

        logger.info("session %s bound to %s", session_id, room_id)
        return Welcome(
            message=WelcomeMessage(role=MessageRole.ASSISTANT, text=WELCOME_TEXT),
            ui_actions=[ShowChips(chips=list(WELCOME_CHIPS))],
        )

    # --- 퇴실·소거 -----------------------------------------------------
    def close(self, session_id: str, transfer: bool) -> tuple[SessionState, bool]:
        """세션을 종료한다. (최종 상태, purged 여부)를 반환."""
        s = self.require(session_id)

        # 진행 중 딜리버리 정리 + 워드로브 원복 + 방 바인딩 해제
        delivery_service.cancel_session(session_id)
        if s.room_id is not None:
            room_service.reset_room(s.room_id)
            self._room_bindings.pop(s.room_id, None)
        s.active_delivery_id = None

        if transfer:
            # 발급된 핸드오프 티켓(스냅샷)은 유지 — 세션은 소거해도 폰 화면이 성립
            s.state = SessionState.TRANSFERRED
            final_state = SessionState.TRANSFERRED
            purged = False
        else:
            # 대화·킵·프로필 전량 소거 (기본값) + 발급된 핸드오프 티켓 무효화
            s.state = SessionState.CLOSED
            final_state = SessionState.CLOSED
            purged = True
            handoff_service.invalidate_session(session_id)

        # 대화 이력·주문은 두 경우 모두 소거 (삭제가 기본값)
        self._purge_conversation(session_id)
        self._orders.pop(session_id, None)

        # SSE session.closed 발행 후 스트림 종료
        event_bus.publish(session_id, "session.closed", {"purged": purged})
        event_bus.close_stream(session_id)

        if purged:
            self._sessions.pop(session_id, None)

        logger.info("session %s closed (transfer=%s purged=%s)", session_id, transfer, purged)
        return final_state, purged

    # --- 대화 이력 (세션 컨텍스트) -------------------------------------
    def add_message(self, session_id: str, role: MessageRole, text: str) -> Message:
        m = Message(id=gen_id("m"), role=role, text=text, created_at=now_kst_iso())
        self._messages.setdefault(session_id, []).append(m)
        return m

    def get_messages(self, session_id: str, after: str | None = None) -> list[Message]:
        msgs = self._messages.get(session_id, [])
        if after:
            for i, m in enumerate(msgs):
                if m.id == after:
                    return msgs[i + 1 :]
            return []
        return list(msgs)

    def get_transcript(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._transcripts.get(session_id, []))

    def save_transcript(self, session_id: str, transcript: list[dict[str, Any]]) -> None:
        self._transcripts[session_id] = transcript

    def _purge_conversation(self, session_id: str) -> None:
        self._transcripts.pop(session_id, None)
        self._messages.pop(session_id, None)

    # --- 주문 (핸드오프 스냅샷 재료) -----------------------------------
    def add_order(self, session_id: str, order: dict[str, Any]) -> None:
        self._orders.setdefault(session_id, []).append(order)

    def get_orders(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._orders.get(session_id, []))

    # --- 리셋 (demo/reset) ---------------------------------------------
    def reset(self) -> None:
        event_bus.close_all()
        self._sessions.clear()
        self._room_bindings.clear()
        self._transcripts.clear()
        self._messages.clear()
        self._orders.clear()


session_service = SessionService()
