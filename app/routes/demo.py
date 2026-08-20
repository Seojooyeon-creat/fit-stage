"""데모 운영 라우트 (API명세서 §5.I, 심사 화면 비노출).

POST /api/v1/demo/reset — 세션·딜리버리 전부 삭제 + 방·재고 원복.
이것만으로 30초 내 S1부터 재시연 가능해야 한다 (FR-O1, NFR-R1).
다음 단계: /demo/deliveries/{id}/advance, /demo/config, /scans.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.agent import concierge, safemode
from app.models import Delivery, DeliveryConfigRequest
from app.services.catalog import catalog_service
from app.services.delivery import delivery_service
from app.services.handoff import handoff_service
from app.services.room import room_service
from app.services.session import session_service

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/reset")
async def reset_demo() -> dict:
    """데모 전체 리셋 (FR-O1)."""
    delivery_service.reset()      # 진행 중 타이머 취소 + 딜리버리 삭제
    session_service.reset()       # 세션·바인딩·대화 삭제 + 모든 SSE 스트림 종료
    room_service.reset()          # 워드로브 상태 초기화
    handoff_service.reset()       # 발급된 핸드오프 티켓 소거
    catalog_service.reload()      # 카탈로그 재고 원복
    delivery_service.reset_step_seconds()  # 연출 타이밍 기본값 복원
    concierge.reset()             # LLM 실패 카운터 초기화
    safemode.reset()              # 세이프 모드 대화 컨텍스트 초기화
    return {
        "reset": True,
        "sessionsCleared": True,
        "deliveriesCleared": True,
        "catalogReloaded": True,
    }


@router.post("/deliveries/{delivery_id}/advance", response_model=Delivery)
async def advance_delivery(delivery_id: str) -> Delivery:
    """자동 타이머 대신 수동으로 다음 단계 진행 (피칭 중 리빌 타이밍 제어)."""
    return delivery_service.advance(delivery_id)


@router.patch("/config")
def set_config(body: DeliveryConfigRequest) -> dict:
    """연출 타이밍/자동 진행 설정."""
    delivery_service.set_config(
        step_ms=body.delivery_step_ms, auto_advance=body.auto_advance
    )
    return {
        "autoAdvance": delivery_service.auto_advance,
        "stepSeconds": {
            k.value: v for k, v in delivery_service.step_seconds.items()
        },
    }
