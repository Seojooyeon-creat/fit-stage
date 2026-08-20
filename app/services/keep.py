"""킵 서비스 (API명세서 §5.E) — 구매 후보 보관 목록.

킵은 세션에 귀속된다. 추가/해제 시 전체 목록+합계를 반환하고 keep.updated를 발행한다.
"""
from __future__ import annotations

import logging

from app.errors import invalid_state, product_not_found, session_closed
from app.models import Keep, KeepsResponse, SessionState
from app.services.catalog import catalog_service
from app.services.session import session_service
from app.sse import event_bus
from app.util import now_kst_iso

logger = logging.getLogger("fitstage.keep")


class KeepService:
    def _require_bound(self, session_id: str):
        s = session_service.require(session_id)
        if s.state in (SessionState.CLOSED, SessionState.TRANSFERRED):
            raise session_closed()
        if s.state != SessionState.BOUND:
            raise invalid_state("먼저 방에 입실해 주세요.")
        return s

    def _total(self, session) -> int:
        total = 0
        for k in session.keeps:
            p = catalog_service.get(k.product_id)
            if p is not None:
                total += p.price
        return total

    def _response(self, session) -> KeepsResponse:
        return KeepsResponse(keeps=list(session.keeps), total=self._total(session))

    def _publish(self, session) -> None:
        resp = self._response(session)
        event_bus.publish(
            session.session_id,
            "keep.updated",
            resp.model_dump(by_alias=True),
        )

    def add(self, session_id: str, product_id: str, size: str) -> KeepsResponse:
        s = self._require_bound(session_id)
        if catalog_service.get(product_id) is None:
            raise product_not_found()
        # 같은 상품은 최신 사이즈로 갱신 (중복 방지)
        s.keeps = [k for k in s.keeps if k.product_id != product_id]
        s.keeps.append(Keep(product_id=product_id, size=size, kept_at=now_kst_iso()))
        self._publish(s)
        logger.info("keep add %s (%s) session=%s", product_id, size, session_id)
        return self._response(s)

    def remove(self, session_id: str, product_id: str) -> KeepsResponse:
        s = self._require_bound(session_id)
        s.keeps = [k for k in s.keeps if k.product_id != product_id]
        self._publish(s)
        logger.info("keep remove %s session=%s", product_id, session_id)
        return self._response(s)


keep_service = KeepService()
