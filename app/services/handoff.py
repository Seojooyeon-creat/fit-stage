"""핸드오프 서비스 (API명세서 §3.6, §5.G) — 스냅샷 동결, 오프→온 연결.

발급 시점에 세션 데이터(확정 사이즈·킵·주문)를 snapshot으로 동결 복사한다.
이후 세션이 소거되어도 폰 화면은 티켓만으로 성립한다 ("삭제는 기본, 핸드오프는 예외").
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from app.models import (
    HandoffKeepItem,
    HandoffSnapshot,
    HandoffTicket,
)
from app.services.catalog import catalog_service
from app.util import gen_id, now_kst

logger = logging.getLogger("fitstage.handoff")

STORE_NAME = "성수 매장 · ROOM 01"
TICKET_TTL_MINUTES = 10


@dataclass
class _TicketRecord:
    ticket: HandoffTicket
    session_id: str
    expiry: datetime
    claimed: bool = False


class HandoffService:
    def __init__(self) -> None:
        self._records: dict[str, _TicketRecord] = {}

    def issue(
        self,
        session,
        orders: Optional[list[dict[str, Any]]] = None,
        host: str = "http://localhost:8000",
        ttl_minutes: int = TICKET_TTL_MINUTES,
    ) -> HandoffTicket:
        code = gen_id("h")
        expiry = now_kst() + timedelta(minutes=ttl_minutes)

        keeps: list[HandoffKeepItem] = []
        for k in session.keeps:
            p = catalog_service.get(k.product_id)
            keeps.append(
                HandoffKeepItem(
                    product_id=k.product_id,
                    name=p.name if p else k.product_id,
                    size=k.size,
                    price=p.price if p else 0,
                    tried=True,
                )
            )

        ticket = HandoffTicket(
            code=code,
            qr_url=f"{host}/take/{code}",
            expires_at=expiry.isoformat(timespec="seconds"),
            snapshot=HandoffSnapshot(
                store_name=STORE_NAME,
                confirmed_sizes=dict(session.profile.confirmed_sizes),
                keeps=keeps,
                orders=list(orders or []),
            ),
        )
        self._records[code] = _TicketRecord(
            ticket=ticket, session_id=session.session_id, expiry=expiry
        )
        logger.info("handoff issued session=%s code=%s", session.session_id, code)
        return ticket

    def get_record(self, code: str) -> Optional[_TicketRecord]:
        return self._records.get(code)

    def is_expired(self, code: str) -> bool:
        rec = self._records.get(code)
        if rec is None:
            return False
        return now_kst() > rec.expiry

    def claim(self, code: str) -> bool:
        """최초 조회 여부를 반환 (True면 이번이 첫 클레임)."""
        rec = self._records.get(code)
        if rec is None:
            return False
        first = not rec.claimed
        rec.claimed = True
        return first

    def drop(self, code: str) -> None:
        self._records.pop(code, None)

    def invalidate_session(self, session_id: str) -> None:
        """해당 세션이 발급한 티켓을 모두 무효화 (close transfer=false)."""
        for code in [c for c, r in self._records.items() if r.session_id == session_id]:
            self._records.pop(code, None)

    def reset(self) -> None:
        self._records.clear()


handoff_service = HandoffService()
