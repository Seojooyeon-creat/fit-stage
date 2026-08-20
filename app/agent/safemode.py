"""세이프 모드 — 서버 소재 폴백 (기능명세서 NFR-R3).

키가 없거나 LLM이 2회 연속 실패하면 자동 전환된다. 사전 정의 대본으로 동작하되
도구 호출은 진짜로 한다 — 대본인 건 "말"뿐이다. 칩 기반으로 S1~S6 완주 가능.

인사 → 면접 추천 카드 3장(+이유) → "한 치수 크게" 처리(실제 request_delivery) →
킵 → 결제 유도 → 핸드오프.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.agent.tools import ToolContext, build_ui_actions, execute_tool
from app.errors import ApiError
from app.models import (
    MessageRole,
    MessagesResponse,
    Product,
    ProductRecommendation,
)
from app.services.catalog import catalog_service
from app.services.session import session_service

logger = logging.getLogger("fitstage.safemode")

# 세션별 마지막 추천 목록 (["1번"] 지칭·딜리버리 대상 선택용)
_last_products: dict[str, list[str]] = {}

# 카드별 고정 이유 (면접 세미포멀 시나리오). 없으면 styleNote로 대체.
_REASONS = {
    "p_1001": "어깨선이 부드러워 딱딱해 보이지 않아요",
    "p_1002": "칼라가 낮아 넥타이 없이도 격식이 서요",
    "p_1003": "재킷과 톤을 맞추면 정돈된 실루엣이 돼요",
    "p_1009": "각 잡힌 라인이 확실한 격식을 만들어 줘요",
}

RECOMMEND_CHIPS = ["1번 입어볼게요", "한 치수 크게", "킵해 둘게요"]
DELIVERY_CHIPS = ["킵해 둘게요", "결제할게요", "다른 것도 볼게요"]
KEEP_CHIPS = ["결제할게요", "폰으로 보내 주세요", "더 볼게요"]
CHECKOUT_CHIPS = ["폰으로 보내 주세요", "그냥 나갈게요"]
HANDOFF_CHIPS = ["QR 스캔했어요", "그냥 나갈게요"]
GREETING_CHIPS = ["다음 주 면접이에요", "데이트 룩 보여 주세요", "그냥 둘러볼게요"]


def reset() -> None:
    _last_products.clear()


def _has(text: str, *keywords: str) -> bool:
    return any(k in text for k in keywords)


def _first_in_stock(product: Product, *, bigger: bool = False, smaller: bool = False) -> Optional[str]:
    in_stock = [s.size for s in product.sizes if s.stock > 0]
    if not in_stock:
        return None
    if bigger:
        return in_stock[-1]
    if smaller:
        return in_stock[0]
    return in_stock[0]


def _reason_for(product: Product) -> str:
    if product.product_id in _REASONS:
        return _REASONS[product.product_id]
    note = (product.style_note or "").split(".")[0].strip()
    return note or "이 자리에 잘 어울려요"


def _pick_number(text: str) -> int:
    for i, token in enumerate(("1번", "2번", "3번")):
        if token in text:
            return i
    return 0


def _infer_tpo(text: str):
    """발화에서 TPO를 추정해 (검색 tpo, 안내 문구)를 돌려준다.

    None이면 조건 없이 대표 상품을 보여준다(둘러보기).
    """
    if _has(text, "데이트", "소개팅", "약속"):
        return "date", "데이트에 어울리는 무드로 — 세 가지를 골라 두었어요."
    if _has(text, "면접", "정장", "격식", "발표"):
        return "interview", "격식은 갖추되, 부드러운 인상으로 — 세 가지를 골라 두었어요."
    if _has(text, "오피스", "회사", "출근", "업무", "미팅"):
        return "office", "단정한 오피스 룩으로 — 세 가지를 골라 두었어요."
    if _has(text, "캐주얼", "주말", "편한", "편하게", "데일리"):
        return "casual", "편하게 입기 좋은 것들로 골라 봤어요."
    if _has(text, "둘러", "구경"):
        return None, "천천히 둘러보세요 — 요즘 잘 나가는 것들이에요."
    # 기본: 면접 세미포멀 시나리오
    return "interview", "격식은 갖추되, 부드러운 인상으로 — 세 가지를 골라 두었어요."


def _finish(session, text: str, ctx: ToolContext, products=None, chips=None) -> MessagesResponse:
    ui = build_ui_actions(ctx, products=products, chips=chips)
    message = session_service.add_message(session.session_id, MessageRole.ASSISTANT, text)
    return MessagesResponse(messages=[message], ui_actions=ui)


def handle(session, text: str) -> MessagesResponse:
    """세이프 모드 한 턴. 칩/발화 의도로 대본을 분기한다."""
    sid = session.session_id
    t = (text or "").strip()
    ctx = ToolContext(session=session)

    # --- 핸드오프 ---
    if _has(t, "폰", "핸드오프", "보내", "전달", "이어"):
        try:
            execute_tool("transfer_session", {}, ctx)
            msg = "오늘의 세션을 폰으로 담아 드릴게요. 편히 이어서 보세요."
            return _finish(session, msg, ctx, chips=HANDOFF_CHIPS)
        except ApiError as e:
            return _finish(session, e.message, ctx, chips=GREETING_CHIPS)

    # --- 결제 ---
    if _has(t, "결제", "구매", "살게", "살래", "계산"):
        execute_tool("checkout", {}, ctx)
        msg = "결제를 마쳤어요. 쇼핑백은 나가시는 길에 준비해 둘게요."
        return _finish(session, msg, ctx, chips=CHECKOUT_CHIPS)

    # --- 킵 ---
    if _has(t, "킵", "담아", "보관", "찜"):
        pid = (_last_products.get(sid) or ["p_1001"])[_pick_number(t)] if _last_products.get(sid) else "p_1001"
        product = catalog_service.get(pid)
        size = _first_in_stock(product) if product else "M"
        try:
            execute_tool("keep_item", {"productId": pid, "size": size}, ctx)
            msg = "킵에 담아 두었어요. 천천히 결정하셔도 좋아요."
            return _finish(session, msg, ctx, chips=KEEP_CHIPS)
        except ApiError as e:
            return _finish(session, e.message, ctx, chips=KEEP_CHIPS)

    # --- 딜리버리 (입어보기 / 한 치수 크게) ---
    if _has(t, "입어", "1번", "2번", "3번", "한 치수", "크게", "작게", "이걸로", "이거", "가져다"):
        picks = _last_products.get(sid) or ["p_1001"]
        idx = min(_pick_number(t), len(picks) - 1)
        pid = picks[idx]
        product = catalog_service.get(pid)
        bigger = _has(t, "크게")
        smaller = _has(t, "작게")
        size = _first_in_stock(product, bigger=bigger, smaller=smaller) if product else "M"
        try:
            execute_tool(
                "request_delivery", {"productId": pid, "size": size}, ctx
            )
            msg = "바로 가져다 드릴게요. 곧 워드로브에 닿아요."
            return _finish(session, msg, ctx, chips=DELIVERY_CHIPS)
        except ApiError as e:
            # 재고 없음·진행 중 등 → 담백히 안내
            return _finish(session, e.message, ctx, chips=DELIVERY_CHIPS)

    # --- 추천 (입력에 맞는 TPO로 검색) ---
    if _has(t, "면접", "추천", "옷", "자리", "보여", "룩", "정장", "격식", "더", "다른",
            "데이트", "소개팅", "약속", "오피스", "회사", "출근", "업무", "미팅",
            "캐주얼", "주말", "편한", "편하게", "데일리", "둘러", "구경"):
        tpo, msg = _infer_tpo(t)
        items, _total = catalog_service.search(tpo=tpo, limit=3) if tpo else catalog_service.search(limit=3)
        if not items:  # 결과 빈약 → 조건 완화
            items, _total = catalog_service.search(limit=3)
        _last_products[sid] = [p.product_id for p in items]
        products = [
            ProductRecommendation(
                product_id=p.product_id,
                size=_first_in_stock(p) or (p.sizes[0].size if p.sizes else "M"),
                reason=_reason_for(p),
            )
            for p in items
        ]
        return _finish(session, msg, ctx, products=products, chips=RECOMMEND_CHIPS)

    # --- 인사/기본 ---
    msg = "어서 오세요. 어떤 옷을 찾으시나요?"
    return _finish(session, msg, ctx, chips=GREETING_CHIPS)
