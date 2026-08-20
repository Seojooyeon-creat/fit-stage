"""딜리버리·워드로브 라우트 (API명세서 §5.D).

POST /sessions/{id}/deliveries               딜리버리 요청 (상태머신 시작)
GET  /sessions/{id}/deliveries/{deliveryId}  딜리버리 조회 (history 포함)
GET  /rooms/{roomId}/wardrobe                 워드로브 상태 조회
"""
from __future__ import annotations

from fastapi import APIRouter

from app.errors import delivery_not_found
from app.models import (
    Delivery,
    DeliveryCreateRequest,
    DeliveryCreateResponse,
    WardrobeState,
)
from app.services.delivery import delivery_service
from app.services.room import room_service
from app.services.session import session_service

router = APIRouter(tags=["deliveries"])


@router.post(
    "/sessions/{session_id}/deliveries",
    status_code=201,
    response_model=DeliveryCreateResponse,
)
async def create_delivery(
    session_id: str, body: DeliveryCreateRequest
) -> DeliveryCreateResponse:
    session = session_service.require(session_id)
    delivery, eta = delivery_service.create(session, body.product_id, body.size)
    return DeliveryCreateResponse(
        delivery_id=delivery.delivery_id, status=delivery.status, eta_seconds=eta
    )


@router.get(
    "/sessions/{session_id}/deliveries/{delivery_id}", response_model=Delivery
)
def get_delivery(session_id: str, delivery_id: str) -> Delivery:
    session_service.require(session_id)
    d = delivery_service.get(session_id, delivery_id)
    if d is None:
        raise delivery_not_found()
    return d


@router.post(
    "/sessions/{session_id}/deliveries/{delivery_id}/cancel", response_model=Delivery
)
async def cancel_delivery(session_id: str, delivery_id: str) -> Delivery:
    session_service.require(session_id)
    return delivery_service.cancel(session_id, delivery_id)


@router.get("/rooms/{room_id}/wardrobe", response_model=WardrobeState)
def get_wardrobe(room_id: str) -> WardrobeState:
    return room_service.get_wardrobe(room_id)
