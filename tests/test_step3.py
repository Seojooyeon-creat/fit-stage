"""STEP 3 테스트 — 컨시어지 tool-use + 세이프 모드.

필수:
  - 그라운딩: SHOW_PRODUCTS에 카탈로그 밖 productId → 검증 실패
  - 세이프 모드: 키 없이 messages 왕복으로 추천→딜리버리 생성까지 도달
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent import concierge
from app.main import app
from app.services.catalog import catalog_service

client = TestClient(app)
PREFIX = "/api/v1"


@pytest.fixture(autouse=True)
def _no_key_and_reset(monkeypatch):
    # LLM 키 제거 → 세이프 모드 강제
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client.post(f"{PREFIX}/demo/reset")
    yield
    client.post(f"{PREFIX}/demo/reset")


def _bound() -> str:
    sid = client.post(f"{PREFIX}/sessions").json()["sessionId"]
    client.post(f"{PREFIX}/sessions/{sid}/bind", json={"roomId": "room01"})
    return sid


def _actions_by_type(body: dict) -> dict:
    return {a["type"]: a for a in body["uiActions"]}


# =====================================================================
# 그라운딩
# =====================================================================
def test_grounding_rejects_unknown_product():
    from app.models import ShowProducts

    with pytest.raises(ValidationError):
        ShowProducts(items=[{"productId": "p_ghost", "size": "M", "reason": "없는 상품"}])


def test_grounding_rejects_empty_reason():
    from app.models import ShowProducts

    with pytest.raises(ValidationError):
        ShowProducts(items=[{"productId": "p_1001", "size": "M", "reason": ""}])


def test_concierge_validate_products_grounding():
    # present.products 검증기 — 카탈로그 밖 productId면 실패
    with pytest.raises(ValidationError):
        concierge._validate_products(
            [{"productId": "p_nope", "size": "M", "reason": "x"}]
        )
    ok = concierge._validate_products(
        [{"productId": "p_1001", "size": "M", "reason": "어깨선이 부드러워요"}]
    )
    assert ok[0].product_id == "p_1001"


# =====================================================================
# 세이프 모드 — 필수: 추천 → 딜리버리 생성
# =====================================================================
def test_safemode_recommends_grounded_cards():
    sid = _bound()
    r = client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "다음 주 면접이에요"})
    assert r.status_code == 200
    body = r.json()
    acts = _actions_by_type(body)

    assert "SHOW_PRODUCTS" in acts
    assert "SHOW_CHIPS" in acts
    items = acts["SHOW_PRODUCTS"]["items"]
    assert len(items) == 3
    for it in items:
        # 카탈로그 실존 productId + 비어 있지 않은 reason (그라운딩)
        assert catalog_service.exists(it["productId"])
        assert it["reason"].strip()
    assert len(acts["SHOW_CHIPS"]["chips"]) >= 2
    # 어시스턴트 메시지 존재
    assert body["messages"][0]["role"] == "assistant"
    assert body["messages"][0]["text"]


def test_safemode_creates_real_delivery():
    sid = _bound()
    # 1) 추천
    client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "다음 주 면접이에요"})
    # 2) "한 치수 크게" → 실제 request_delivery 호출
    r = client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "1번 한 치수 크게 입어볼게요"})
    assert r.status_code == 200
    acts = _actions_by_type(r.json())
    assert "SHOW_DELIVERY_STATUS" in acts, r.json()
    did = acts["SHOW_DELIVERY_STATUS"]["deliveryId"]
    assert did.startswith("d_")

    # 딜리버리가 실제로 생성됐는지 (도구 호출은 진짜다)
    dv = client.get(f"{PREFIX}/sessions/{sid}/deliveries/{did}")
    assert dv.status_code == 200
    assert dv.json()["status"] in (
        "REQUESTED", "PICKING", "ON_RAIL", "ENTERING", "SEALED", "STAGING", "READY"
    )


def test_safemode_full_chain_via_chips():
    """칩 기반으로 S2~S6 완주 (추천→딜리버리→킵→결제→핸드오프)."""
    sid = _bound()
    client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "면접 룩 보여줘"})
    d = _actions_by_type(
        client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "1번 입어볼게요"}).json()
    )
    assert "SHOW_DELIVERY_STATUS" in d

    k = _actions_by_type(
        client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "킵할게요"}).json()
    )
    assert "SHOW_KEEP_LIST" in k
    assert k["SHOW_KEEP_LIST"]["total"] > 0

    c = _actions_by_type(
        client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "결제할게요"}).json()
    )
    assert "SHOW_CHECKOUT_RESULT" in c
    assert c["SHOW_CHECKOUT_RESULT"]["orderId"].startswith("o_")

    h = _actions_by_type(
        client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "폰으로 보내주세요"}).json()
    )
    assert "SHOW_HANDOFF_QR" in h
    assert h["SHOW_HANDOFF_QR"]["qrUrl"]


def test_conversation_continues_during_delivery():
    """딜리버리 진행 중에도 새 발화가 정상 처리된다 (FR-D5)."""
    sid = _bound()
    client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "면접 룩 보여줘"})
    client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "1번 입어볼게요"})
    # 딜리버리 진행 중 새 발화 (추천 요청)
    r = client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "데이트 룩도 보여줘"})
    assert r.status_code == 200
    assert "SHOW_CHIPS" in _actions_by_type(r.json())


def test_every_response_has_chips():
    sid = _bound()
    for text in ["안녕하세요", "면접이에요", "고민되네요"]:
        body = client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": text}).json()
        assert "SHOW_CHIPS" in _actions_by_type(body)


# =====================================================================
# 대화 이력 · 사전조건
# =====================================================================
def test_message_history():
    sid = _bound()
    client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "면접이에요"})
    r = client.get(f"{PREFIX}/sessions/{sid}/messages")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant"]


def test_message_requires_bound_session():
    sid = client.post(f"{PREFIX}/sessions").json()["sessionId"]  # CREATED
    r = client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "안녕"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_STATE"


def test_message_history_purged_on_close():
    sid = _bound()
    client.post(f"{PREFIX}/sessions/{sid}/messages", json={"text": "면접이에요"})
    client.post(f"{PREFIX}/sessions/{sid}/close", json={"transfer": False})
    # 소거된 세션 → 404
    assert client.get(f"{PREFIX}/sessions/{sid}/messages").status_code == 404
