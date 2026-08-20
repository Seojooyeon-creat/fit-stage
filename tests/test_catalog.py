"""카탈로그 검색·단건 조회 + SHOW_PRODUCTS 그라운딩 검증기 테스트."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models import ShowProducts

client = TestClient(app)

PREFIX = "/api/v1"


def test_list_all_products():
    r = client.get(f"{PREFIX}/catalog/products", params={"limit": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 14
    assert len(body["items"]) == 14
    # camelCase 직렬화 확인
    assert "productId" in body["items"][0]
    assert "styleNote" in body["items"][0]


def test_default_limit_is_5():
    r = client.get(f"{PREFIX}/catalog/products")
    body = r.json()
    assert len(body["items"]) == 5
    assert body["total"] == 14  # total은 필터 후 전체 매칭 수


def test_filter_interview_semiformal():
    # "면접인데 너무 정장 같지 않게" = formality 3~4 + tpo interview
    r = client.get(
        f"{PREFIX}/catalog/products",
        params={"tpo": "interview", "formalityMin": 3, "formalityMax": 4, "limit": 100},
    )
    body = r.json()
    assert body["total"] >= 3  # 면접 시나리오용 상품 3개 이상
    for item in body["items"]:
        assert 3 <= item["tags"]["formality"] <= 4
        assert "interview" in item["tags"]["tpo"]


def test_filter_by_category():
    r = client.get(
        f"{PREFIX}/catalog/products", params={"category": "outer", "limit": 100}
    )
    body = r.json()
    assert all(i["category"] == "outer" for i in body["items"])
    assert body["total"] == 3


def test_size_filter_excludes_out_of_stock():
    # p_1002(튤립 실크 셔츠) L 사이즈는 품절 → size=L 검색에서 제외
    r = client.get(
        f"{PREFIX}/catalog/products", params={"size": "L", "limit": 100}
    )
    ids = [i["productId"] for i in r.json()["items"]]
    assert "p_1002" not in ids


def test_max_price():
    r = client.get(
        f"{PREFIX}/catalog/products", params={"maxPrice": 200000, "limit": 100}
    )
    assert all(i["price"] <= 200000 for i in r.json()["items"])


def test_query_keyword():
    r = client.get(f"{PREFIX}/catalog/products", params={"q": "재킷", "limit": 100})
    ids = [i["productId"] for i in r.json()["items"]]
    assert "p_1001" in ids  # 스트럭처드 트러커 재킷


def test_required_products_exist():
    for pid in ("p_1001", "p_1002", "p_1003", "p_1004"):
        r = client.get(f"{PREFIX}/catalog/products/{pid}")
        assert r.status_code == 200, pid


def test_get_single_product():
    r = client.get(f"{PREFIX}/catalog/products/p_1004")
    body = r.json()
    assert body["name"] == "울 오버코트"
    assert body["price"] == 1180000
    assert body["color"] == "burgundy"


def test_get_unknown_product_404():
    r = client.get(f"{PREFIX}/catalog/products/p_nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


def test_validation_error_shape():
    # formalityMin > 5 → 공통 VALIDATION_ERROR 형태
    r = client.get(f"{PREFIX}/catalog/products", params={"formalityMin": 9})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# --- SHOW_PRODUCTS 그라운딩 검증기 (설계 원칙 2, FR-C3) ------------------
def test_show_products_accepts_valid_card():
    action = ShowProducts(
        items=[{"productId": "p_1001", "size": "M", "reason": "어깨선이 부드러워요"}]
    )
    assert action.items[0].product_id == "p_1001"


def test_show_products_rejects_unknown_product():
    with pytest.raises(ValidationError):
        ShowProducts(
            items=[{"productId": "p_ghost", "size": "M", "reason": "존재하지 않는 상품"}]
        )


def test_show_products_rejects_empty_reason():
    with pytest.raises(ValidationError):
        ShowProducts(items=[{"productId": "p_1001", "size": "M", "reason": "  "}])


# --- demo/reset ---------------------------------------------------------
def test_demo_reset_restores_stock():
    r = client.post(f"{PREFIX}/demo/reset")
    assert r.status_code == 200
    assert r.json()["reset"] is True
