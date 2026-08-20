"""결제 서비스 (API명세서 §5.F / §6, 도구: checkout) — 목업(항상 성공).

결제하면 주문을 세션에 기록하고(핸드오프 스냅샷 orders에 포함), 구매한 항목은
킵 목록에서 주문으로 이동시킨다 (FR-P1).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.catalog import catalog_service
from app.sse import event_bus
from app.util import gen_id

logger = logging.getLogger("fitstage.order")


class OrderService:
    def checkout(
        self,
        session,
        items: Optional[list[dict[str, Any]]] = None,
        method: str = "MIRROR",
    ) -> dict:
        # 지연 임포트로 순환 참조 회피 (session_service ↔ order_service)
        from app.services.session import session_service

        # items 미지정 시 세션 킵 목록을 주문으로 전환
        if not items:
            items = [{"productId": k.product_id, "size": k.size} for k in session.keeps]

        total = 0
        for it in items:
            p = catalog_service.get(it.get("productId"))
            if p is not None:
                total += p.price

        order = {
            "orderId": gen_id("o"),
            "status": "PAID",
            "total": total,
            "items": items,
            "method": method,
            "message": "쇼핑백은 나가시는 길에 준비해 둘게요.",
        }
        session_service.add_order(session.session_id, order)

        # 구매한 항목은 킵 → 주문으로 이동
        purchased = {it.get("productId") for it in items}
        if purchased and session.keeps:
            session.keeps = [k for k in session.keeps if k.product_id not in purchased]
            event_bus.publish(
                session.session_id,
                "keep.updated",
                {
                    "keeps": [k.model_dump(by_alias=True) for k in session.keeps],
                    "total": sum(
                        catalog_service.get(k.product_id).price
                        for k in session.keeps
                        if catalog_service.get(k.product_id)
                    ),
                },
            )

        logger.info(
            "checkout session=%s order=%s total=%s", session.session_id, order["orderId"], total
        )
        return order


order_service = OrderService()
