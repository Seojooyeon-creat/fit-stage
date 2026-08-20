"""LLM 도구 ↔ 서비스 함수 매핑 (API명세서 §6).

도구는 HTTP를 거치지 않고 같은 서버의 서비스 함수를 직접 호출한다 (단일 소스).
컨시어지(LLM)와 세이프 모드가 공유하는 실행 계층.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.errors import ApiError
from app.models import (
    KeepsResponse,
    ProductRecommendation,
    Session,
    ShowChips,
    ShowCheckoutResult,
    ShowDeliveryStatus,
    ShowHandoffQr,
    ShowKeepList,
    ShowProducts,
    UiAction,
)
from app.services.catalog import catalog_service
from app.services.delivery import delivery_service
from app.services.handoff import handoff_service
from app.services.keep import keep_service
from app.services.order import order_service
from app.services.profile import profile_service
from app.services.room import room_service

logger = logging.getLogger("fitstage.tools")

DEFAULT_CHIPS = ["다음 주 면접이에요", "데이트 룩 보여줘", "그냥 둘러볼게요"]


@dataclass
class ToolContext:
    """한 턴 동안의 도구 호출 부수효과를 모아 uiActions로 조립한다."""

    session: Session
    delivery_id: Optional[str] = None
    keep_result: Optional[KeepsResponse] = None
    order: Optional[dict] = None
    handoff: Optional[Any] = None
    tool_calls: list[str] = field(default_factory=list)


# =========================================================================
# Anthropic 도구 정의 (present 포함)
# =========================================================================
TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "search_catalog",
        "description": "카탈로그에서 상품을 검색한다. 추천은 반드시 이 결과의 productId로만 해야 한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "이름·설명 키워드"},
                "category": {
                    "type": "string",
                    "enum": ["top", "bottom", "outer", "shirt", "acc"],
                },
                "tpo": {"type": "string", "description": "예: interview, office, date, casual"},
                "formalityMin": {"type": "integer", "minimum": 1, "maximum": 5},
                "formalityMax": {"type": "integer", "minimum": 1, "maximum": 5},
                "size": {"type": "string", "description": "해당 사이즈 재고 있는 상품만"},
                "maxPrice": {"type": "integer"},
                "limit": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "request_delivery",
        "description": "상품을 방 워드로브로 보내는 딜리버리를 시작한다. 이후 진행은 화면(SSE)이 갱신한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "productId": {"type": "string"},
                "size": {"type": "string"},
            },
            "required": ["productId", "size"],
        },
    },
    {
        "name": "wardrobe_state",
        "description": "현재 방 워드로브(문·조명) 상태를 조회한다.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "keep_item",
        "description": "상품을 구매 후보(킵) 목록에 담는다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "productId": {"type": "string"},
                "size": {"type": "string"},
            },
            "required": ["productId", "size"],
        },
    },
    {
        "name": "session_profile",
        "description": "대화에서 파악한 확정 사이즈·취향·맥락을 프로필에 누적한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmedSizes": {
                    "type": "object",
                    "description": '예: {"outer": "L"}',
                    "additionalProperties": {"type": "string"},
                },
                "preferences": {"type": "array", "items": {"type": "string"}},
                "context": {"type": "object"},
            },
        },
    },
    {
        "name": "checkout",
        "description": "방 안에서 결제한다 (목업, 항상 성공).",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "productId": {"type": "string"},
                            "size": {"type": "string"},
                        },
                    },
                }
            },
        },
    },
    {
        "name": "transfer_session",
        "description": "세션 스냅샷을 QR 티켓으로 발급해 폰으로 이어받게 한다.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "present",
        "description": "손님에게 보일 최종 응답. 모든 턴은 이 도구로 마무리한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "짧고 조용한 한국어 응답"},
                "chips": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "다음 행동 칩 2~3개",
                },
                "products": {
                    "type": "array",
                    "description": "추천 카드 (없으면 생략)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "productId": {"type": "string"},
                            "size": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["productId", "size", "reason"],
                    },
                },
            },
            "required": ["text"],
        },
    },
]


# =========================================================================
# 부수효과 도구 실행 (present 제외)
# =========================================================================
def execute_tool(name: str, tool_input: dict[str, Any], ctx: ToolContext) -> dict:
    """도구를 실행하고 LLM에 돌려줄 결과(JSON 직렬화 가능한 dict)를 반환한다.

    도메인 에러(재고 없음 등)는 예외로 던지지 않고 결과에 담아 LLM이 대안을 제시하게 한다.
    """
    ctx.tool_calls.append(name)
    session = ctx.session
    try:
        if name == "search_catalog":
            items, total = catalog_service.search(
                q=tool_input.get("q"),
                category=tool_input.get("category"),
                tpo=tool_input.get("tpo"),
                formality_min=tool_input.get("formalityMin"),
                formality_max=tool_input.get("formalityMax"),
                size=tool_input.get("size"),
                max_price=tool_input.get("maxPrice"),
                limit=int(tool_input.get("limit", 5)),
            )
            return {
                "items": [p.model_dump(by_alias=True) for p in items],
                "total": total,
            }

        if name == "request_delivery":
            delivery, eta = delivery_service.create(
                session, tool_input["productId"], tool_input["size"]
            )
            ctx.delivery_id = delivery.delivery_id
            return {
                "deliveryId": delivery.delivery_id,
                "status": delivery.status.value,
                "etaSeconds": eta,
            }

        if name == "wardrobe_state":
            w = room_service.get_wardrobe(session.room_id)
            return w.model_dump(by_alias=True)

        if name == "keep_item":
            result = keep_service.add(
                session.session_id, tool_input["productId"], tool_input["size"]
            )
            ctx.keep_result = result
            return result.model_dump(by_alias=True)

        if name == "session_profile":
            profile = profile_service.update(
                session,
                confirmed_sizes=tool_input.get("confirmedSizes"),
                preferences=tool_input.get("preferences"),
                context=tool_input.get("context"),
            )
            return profile.model_dump(by_alias=True)

        if name == "checkout":
            order = order_service.checkout(session, tool_input.get("items"))
            ctx.order = order
            return order

        if name == "transfer_session":
            ticket = handoff_service.issue(session)
            ctx.handoff = ticket
            return ticket.model_dump(by_alias=True)

        return {"error": "UNKNOWN_TOOL", "message": f"알 수 없는 도구: {name}"}

    except ApiError as e:
        # 재고 없음·진행 중 등 → LLM이 대안을 제시하도록 결과에 담는다
        logger.info("tool %s domain error: %s", name, e.code)
        return {"error": e.code, "message": e.message}


# =========================================================================
# uiActions 조립 (컨시어지·세이프 모드 공용)
# =========================================================================
def build_ui_actions(
    ctx: ToolContext,
    products: Optional[list[ProductRecommendation]] = None,
    chips: Optional[list[str]] = None,
) -> list[UiAction]:
    actions: list[UiAction] = []

    if products:
        actions.append(ShowProducts(items=products))  # 그라운딩 검증기 동작
    if ctx.delivery_id:
        actions.append(ShowDeliveryStatus(delivery_id=ctx.delivery_id))
    if ctx.keep_result is not None:
        actions.append(
            ShowKeepList(
                items=[k.model_dump(by_alias=True) for k in ctx.keep_result.keeps],
                total=ctx.keep_result.total,
            )
        )
    if ctx.order is not None:
        actions.append(
            ShowCheckoutResult(order_id=ctx.order["orderId"], total=ctx.order["total"])
        )
    if ctx.handoff is not None:
        actions.append(
            ShowHandoffQr(qr_url=ctx.handoff.qr_url, expires_at=ctx.handoff.expires_at)
        )

    actions.append(ShowChips(chips=chips if chips else list(DEFAULT_CHIPS)))
    return actions
