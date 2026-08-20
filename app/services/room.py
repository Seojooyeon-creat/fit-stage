"""방/워드로브 서비스 (API명세서 §3.4) — 에어락 불변식의 소유자.

★ 에어락 불변식 (FR-D3): serviceDoor == OPEN 인 동안 customerDoor는 반드시 LOCKED.
   코드 레벨에서 강제 — 위반하는 상태 전이는 AirlockViolation 예외로 차단된다.
   두 문이 동시에 열리는 상태는 어떤 API 호출 조합으로도 만들 수 없다.
"""
from __future__ import annotations

from typing import Optional

from app.models import CustomerDoor, Light, ServiceDoor, WardrobeState

_UNSET = object()  # current_delivery_id에서 None(=해제)과 "변경 없음"을 구분


class AirlockViolation(RuntimeError):
    """두 문이 동시에 열리는(에어락 붕괴) 전이 시도."""


class RoomService:
    def __init__(self) -> None:
        self._wardrobes: dict[str, WardrobeState] = {}

    def ensure(self, room_id: str) -> WardrobeState:
        if room_id not in self._wardrobes:
            # 기본 idle 상태: 서비스문 닫힘 · 고객문 잠김 · 조명 꺼짐
            self._wardrobes[room_id] = WardrobeState(room_id=room_id)
        return self._wardrobes[room_id]

    def get_wardrobe(self, room_id: str) -> WardrobeState:
        return self.ensure(room_id)

    def set_wardrobe(
        self,
        room_id: str,
        *,
        service_door: Optional[ServiceDoor] = None,
        customer_door: Optional[CustomerDoor] = None,
        light: Optional[Light] = None,
        current_delivery_id=_UNSET,
    ) -> WardrobeState:
        """워드로브 상태를 갱신한다. 에어락 불변식을 위반하면 예외."""
        w = self.ensure(room_id)
        new_service = service_door if service_door is not None else w.service_door
        new_customer = customer_door if customer_door is not None else w.customer_door
        new_light = light if light is not None else w.light

        # ★ 에어락 불변식 강제
        if new_service == ServiceDoor.OPEN and new_customer != CustomerDoor.LOCKED:
            raise AirlockViolation(
                "에어락 위반: serviceDoor가 OPEN인 동안 customerDoor는 반드시 LOCKED여야 합니다."
            )

        w.service_door = new_service
        w.customer_door = new_customer
        w.light = new_light
        if current_delivery_id is not _UNSET:
            w.current_delivery_id = current_delivery_id
        return w

    def reset_room(self, room_id: str) -> WardrobeState:
        """방 워드로브를 idle 상태로 되돌린다."""
        return self.set_wardrobe(
            room_id,
            service_door=ServiceDoor.CLOSED,
            customer_door=CustomerDoor.LOCKED,
            light=Light.OFF,
            current_delivery_id=None,
        )

    def reset(self) -> None:
        self._wardrobes.clear()


room_service = RoomService()
