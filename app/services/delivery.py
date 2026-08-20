"""딜리버리 상태머신 (API명세서 §3.3, 플로우차트 ③) — 스펙의 핵심.

판단은 AI, 물리는 상태머신. LLM/클라이언트는 "생성(REQUESTED)" 하나만 만들 수 있고
이후 7단계 전이는 서버 타이머(asyncio task)가 결정론적으로 진행한다 — 건너뛰기·역행 불가.

전이 순서와 기본 소요:
  REQUESTED →(1.5s)→ PICKING →(2s)→ ON_RAIL →(3s)→ ENTERING
  →(1.5s)→ SEALED →(1.5s)→ STAGING →(2s)→ READY   (합계 ~11.5초)

워드로브 연동:
  ENTERING: serviceDoor=OPEN (customerDoor는 반드시 LOCKED — 에어락)
  SEALED:   serviceDoor=CLOSED
  STAGING:  light=AMBER (점등 시작)
  READY:    light=WHITE, customerDoor=UNLOCKED (화이트 정착·문② 해제 = 리빌)
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Optional

from app.errors import (
    delivery_in_progress,
    delivery_not_found,
    invalid_state,
    out_of_stock,
    product_not_found,
    session_closed,
)
from app.models import (
    CustomerDoor,
    Delivery,
    DeliveryHistoryEntry,
    DeliveryStatus,
    Light,
    ServiceDoor,
    Session,
    SessionState,
)
from app.services.catalog import catalog_service
from app.services.room import room_service
from app.sse import event_bus
from app.util import gen_id, now_kst_iso

logger = logging.getLogger("fitstage.delivery")

# 7단계 순서 (stepIndex 계산용)
ORDER: list[DeliveryStatus] = [
    DeliveryStatus.REQUESTED,
    DeliveryStatus.PICKING,
    DeliveryStatus.ON_RAIL,
    DeliveryStatus.ENTERING,
    DeliveryStatus.SEALED,
    DeliveryStatus.STAGING,
    DeliveryStatus.READY,
]
TOTAL_STEPS = len(ORDER)

# 종료 상태 (활성 딜리버리 판정 시 제외)
TERMINAL = {DeliveryStatus.READY, DeliveryStatus.CANCELLED, DeliveryStatus.FAILED}

# 한국어 라벨 (delivery.updated 이벤트)
LABELS: dict[DeliveryStatus, str] = {
    DeliveryStatus.REQUESTED: "요청 접수",
    DeliveryStatus.PICKING: "창고 피킹",
    DeliveryStatus.ON_RAIL: "레일 이동",
    DeliveryStatus.ENTERING: "문① 개방·진입",
    DeliveryStatus.SEALED: "문① 닫힘",
    DeliveryStatus.STAGING: "점등",
    DeliveryStatus.READY: "준비 완료",
    DeliveryStatus.CANCELLED: "취소",
    DeliveryStatus.FAILED: "실패",
}

# 각 목표 상태로 전이하기까지 (이전 상태에서) 대기하는 기본 시간(초).
# 추후 /demo/config로 조정 가능하도록 설정값으로 분리.
DEFAULT_STEP_SECONDS: dict[DeliveryStatus, float] = {
    DeliveryStatus.PICKING: 1.5,
    DeliveryStatus.ON_RAIL: 2.0,
    DeliveryStatus.ENTERING: 3.0,
    DeliveryStatus.SEALED: 1.5,
    DeliveryStatus.STAGING: 1.5,
    DeliveryStatus.READY: 2.0,
}


def _step_index(status: DeliveryStatus) -> int:
    try:
        return ORDER.index(status) + 1
    except ValueError:
        return TOTAL_STEPS


class DeliveryService:
    def __init__(self) -> None:
        self._deliveries: dict[str, Delivery] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self.step_seconds: dict[DeliveryStatus, float] = dict(DEFAULT_STEP_SECONDS)
        self.auto_advance: bool = True  # False면 타이머 대신 수동 advance로 진행

    # --- 설정 (테스트/데모용) ------------------------------------------
    def set_step_seconds(self, seconds: float) -> None:
        """전 단계 소요 시간을 동일 값으로 설정 (테스트 가속용)."""
        self.step_seconds = {k: seconds for k in DEFAULT_STEP_SECONDS}

    def reset_step_seconds(self) -> None:
        self.step_seconds = dict(DEFAULT_STEP_SECONDS)

    def set_config(
        self,
        step_ms: Optional[dict[str, int]] = None,
        auto_advance: Optional[bool] = None,
    ) -> None:
        """연출 타이밍/자동 진행 설정 (PATCH /demo/config)."""
        if step_ms:
            for name, ms in step_ms.items():
                try:
                    st = DeliveryStatus(name)
                except ValueError:
                    continue
                if st in self.step_seconds:
                    self.step_seconds[st] = ms / 1000.0
        if auto_advance is not None:
            self.auto_advance = auto_advance

    def eta_seconds(self) -> int:
        return math.ceil(sum(self.step_seconds.values()))

    # --- 조회 ----------------------------------------------------------
    def get(self, session_id: str, delivery_id: str) -> Optional[Delivery]:
        d = self._deliveries.get(delivery_id)
        if d is None or d.session_id != session_id:
            return None
        return d

    def active_delivery_for_room(self, room_id: str) -> Optional[Delivery]:
        for d in self._deliveries.values():
            if d.room_id == room_id and d.status not in TERMINAL:
                return d
        return None

    # --- 생성 (상태머신 시작) ------------------------------------------
    def create(self, session: Session, product_id: str, size: str) -> tuple[Delivery, int]:
        # 사전 검사 (플로우차트/§5.D)
        if session.state in (SessionState.CLOSED, SessionState.TRANSFERRED):
            raise session_closed()
        if session.state != SessionState.BOUND:
            raise invalid_state("먼저 방에 입실해 주세요.")

        room_id = session.room_id
        assert room_id is not None  # BOUND면 항상 존재

        # 방당 활성 딜리버리 1건
        if self.active_delivery_for_room(room_id) is not None:
            raise delivery_in_progress()

        product = catalog_service.get(product_id)
        if product is None:
            raise product_not_found()
        if not product.has_size_in_stock(size):
            raise out_of_stock(f"{size} 사이즈 재고가 없어요.")

        now = now_kst_iso()
        delivery_id = gen_id("d")
        delivery = Delivery(
            delivery_id=delivery_id,
            session_id=session.session_id,
            room_id=room_id,
            product_id=product_id,
            size=size,
            status=DeliveryStatus.REQUESTED,
            requested_at=now,
            history=[DeliveryHistoryEntry(status=DeliveryStatus.REQUESTED, at=now)],
        )
        self._deliveries[delivery_id] = delivery
        session.active_delivery_id = delivery_id

        # 워드로브를 idle로 정렬하고 현재 딜리버리 연결 (반복 딜리버리 대비)
        room_service.reset_room(room_id)
        room_service.set_wardrobe(room_id, current_delivery_id=delivery_id)
        self._publish_wardrobe(session.session_id, room_id)

        # REQUESTED 발행
        self._publish_delivery(session.session_id, delivery)

        # 서버 타이머 시작 (asyncio) — 반드시 실행 중인 루프에서 호출됨.
        # auto_advance=False면 타이머 없이 수동 advance(데모 제어)로만 진행.
        if self.auto_advance:
            self._tasks[delivery_id] = asyncio.create_task(self._run(delivery_id))

        logger.info("delivery %s created (session=%s room=%s)", delivery_id, session.session_id, room_id)
        return delivery, self.eta_seconds()

    # --- 자동 진행 (타이머) --------------------------------------------
    async def _run(self, delivery_id: str) -> None:
        try:
            for target in ORDER[1:]:  # PICKING..READY
                await asyncio.sleep(self.step_seconds[target])
                d = self._deliveries.get(delivery_id)
                if d is None or d.status in TERMINAL:
                    return  # 취소/삭제됨
                self._transition(d, target)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - 방어적
            logger.exception("delivery %s failed", delivery_id)
            d = self._deliveries.get(delivery_id)
            if d is not None and d.status not in TERMINAL:
                d.status = DeliveryStatus.FAILED
                d.history.append(
                    DeliveryHistoryEntry(status=DeliveryStatus.FAILED, at=now_kst_iso())
                )
                self._publish_delivery(d.session_id, d)
        finally:
            self._tasks.pop(delivery_id, None)

    def _transition(self, d: Delivery, target: DeliveryStatus) -> None:
        d.status = target
        d.history.append(DeliveryHistoryEntry(status=target, at=now_kst_iso()))

        wardrobe_changed = True
        if target == DeliveryStatus.ENTERING:
            # 문① 개방 — 이때 고객문은 반드시 LOCKED (에어락)
            room_service.set_wardrobe(
                d.room_id,
                service_door=ServiceDoor.OPEN,
                customer_door=CustomerDoor.LOCKED,
            )
        elif target == DeliveryStatus.SEALED:
            room_service.set_wardrobe(d.room_id, service_door=ServiceDoor.CLOSED)
        elif target == DeliveryStatus.STAGING:
            room_service.set_wardrobe(d.room_id, light=Light.AMBER)
        elif target == DeliveryStatus.READY:
            # 화이트 정착 · 문② 해제 = 리빌
            room_service.set_wardrobe(
                d.room_id,
                light=Light.WHITE,
                customer_door=CustomerDoor.UNLOCKED,
            )
        else:
            wardrobe_changed = False

        logger.info("delivery %s -> %s", d.delivery_id, target.value)
        self._publish_delivery(d.session_id, d)
        if wardrobe_changed:
            self._publish_wardrobe(d.session_id, d.room_id)
        if target == DeliveryStatus.READY:
            event_bus.publish(
                d.session_id,
                "wardrobe.ready",
                {"deliveryId": d.delivery_id, "productId": d.product_id, "size": d.size},
            )

    # --- SSE 페이로드 --------------------------------------------------
    def _publish_delivery(self, session_id: str, d: Delivery) -> None:
        event_bus.publish(
            session_id,
            "delivery.updated",
            {
                "deliveryId": d.delivery_id,
                "status": d.status.value,
                "stepIndex": _step_index(d.status),
                "totalSteps": TOTAL_STEPS,
                "label": LABELS.get(d.status, d.status.value),
            },
        )

    def _publish_wardrobe(self, session_id: str, room_id: str) -> None:
        w = room_service.get_wardrobe(room_id)
        event_bus.publish(
            session_id,
            "wardrobe.updated",
            {
                "roomId": w.room_id,
                "serviceDoor": w.service_door.value,
                "customerDoor": w.customer_door.value,
                "light": w.light.value,
            },
        )

    # --- 수동 진행 (데모 제어) -----------------------------------------
    def advance(self, delivery_id: str) -> Delivery:
        """타이머 대신 다음 단계로 한 칸 진행한다 (POST /demo/.../advance)."""
        d = self._deliveries.get(delivery_id)
        if d is None:
            raise delivery_not_found()
        if d.status in TERMINAL:
            raise invalid_state("더 진행할 수 없는 상태예요.")
        # 자동 타이머와의 이중 진행 방지
        task = self._tasks.pop(delivery_id, None)
        if task is not None and not task.done():
            task.cancel()
        nxt = ORDER[ORDER.index(d.status) + 1]
        self._transition(d, nxt)
        return d

    # --- 취소 (READY 이전만) -------------------------------------------
    def cancel(self, session_id: str, delivery_id: str) -> Delivery:
        d = self.get(session_id, delivery_id)
        if d is None:
            raise delivery_not_found()
        if d.status in TERMINAL:
            # READY 포함 — 준비 완료/종료 상태는 취소 불가
            raise invalid_state("지금은 취소할 수 없어요.")
        task = self._tasks.pop(delivery_id, None)
        if task is not None and not task.done():
            task.cancel()
        d.status = DeliveryStatus.CANCELLED
        d.history.append(
            DeliveryHistoryEntry(status=DeliveryStatus.CANCELLED, at=now_kst_iso())
        )
        room_service.reset_room(d.room_id)
        self._publish_delivery(session_id, d)
        self._publish_wardrobe(session_id, d.room_id)
        logger.info("delivery %s cancelled", delivery_id)
        return d

    # --- 세션 종료 정리·리셋 -------------------------------------------
    def cancel_session(self, session_id: str) -> None:
        """세션 소거 시 진행 중인 딜리버리 타이머를 정리한다."""
        for delivery_id, d in list(self._deliveries.items()):
            if d.session_id == session_id and d.status not in TERMINAL:
                d.status = DeliveryStatus.CANCELLED
                task = self._tasks.pop(delivery_id, None)
                if task is not None and not task.done():
                    task.cancel()

    def reset(self) -> None:
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self._deliveries.clear()
        self.auto_advance = True


delivery_service = DeliveryService()
