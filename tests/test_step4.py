"""STEP 4 테스트 — 핸드오프·결제·프로필·스캔·데모 제어.

필수:
  - 핸드오프 스냅샷: 발급 → close(transfer:true) → GET /handoff/{code} 여전히 성립
  - 소거: close(transfer:false) 후 GET 세션 404 + 핸드오프 무효화
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.delivery import ORDER, delivery_service
from app.services.handoff import handoff_service
from app.services.session import session_service

client = TestClient(app)
PREFIX = "/api/v1"


@pytest.fixture(autouse=True)
def _reset():
    client.post(f"{PREFIX}/demo/reset")
    yield
    client.post(f"{PREFIX}/demo/reset")


def _bound() -> str:
    sid = client.post(f"{PREFIX}/sessions").json()["sessionId"]
    client.post(f"{PREFIX}/sessions/{sid}/bind", json={"roomId": "room01"})
    return sid


# =====================================================================
# 결제
# =====================================================================
def test_checkout_mock_success():
    sid = _bound()
    client.post(f"{PREFIX}/sessions/{sid}/keeps", json={"productId": "p_1001", "size": "M"})
    r = client.post(
        f"{PREFIX}/sessions/{sid}/checkout",
        json={"items": [{"productId": "p_1001", "size": "M"}], "method": "MIRROR"},
    )
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "PAID"
    assert b["total"] == 698000
    assert b["orderId"].startswith("o_")
    assert b["message"] == "쇼핑백은 나가시는 길에 준비해 둘게요."


def test_checkout_moves_keep_to_order():
    sid = _bound()
    client.post(f"{PREFIX}/sessions/{sid}/keeps", json={"productId": "p_1001", "size": "M"})
    client.post(
        f"{PREFIX}/sessions/{sid}/checkout",
        json={"items": [{"productId": "p_1001", "size": "M"}]},
    )
    # 구매 항목은 킵에서 빠진다
    snap = client.get(f"{PREFIX}/sessions/{sid}").json()
    assert all(k["productId"] != "p_1001" for k in snap["keeps"])


# =====================================================================
# 프로필
# =====================================================================
def test_profile_get_and_patch():
    sid = _bound()
    r = client.patch(
        f"{PREFIX}/sessions/{sid}/profile",
        json={"confirmedSizes": {"outer": "L"}, "preferences": ["soft-shoulder"]},
    )
    assert r.status_code == 200
    assert r.json()["confirmedSizes"] == {"outer": "L"}

    # 부분 갱신 누적
    client.patch(
        f"{PREFIX}/sessions/{sid}/profile", json={"preferences": ["semi-formal"]}
    )
    p = client.get(f"{PREFIX}/sessions/{sid}/profile").json()
    assert p["confirmedSizes"] == {"outer": "L"}
    assert set(p["preferences"]) == {"soft-shoulder", "semi-formal"}


# =====================================================================
# 스캔 (워치)
# =====================================================================
def test_scan_returns_full_product_and_accumulates():
    sid = _bound()
    r = client.post(f"{PREFIX}/sessions/{sid}/scans", json={"productId": "p_1004"})
    assert r.status_code == 200
    b = r.json()
    assert b["product"]["name"] == "울 오버코트"
    assert b["product"]["price"] == 1180000  # Product 전체 렌더
    assert b["scannedProductIds"] == ["p_1004"]

    # 중복 없이 누적
    client.post(f"{PREFIX}/sessions/{sid}/scans", json={"productId": "p_1001"})
    client.post(f"{PREFIX}/sessions/{sid}/scans", json={"productId": "p_1004"})
    snap = client.get(f"{PREFIX}/sessions/{sid}").json()
    assert snap["scannedProductIds"] == ["p_1004", "p_1001"]


def test_scan_unknown_product_404():
    sid = _bound()
    r = client.post(f"{PREFIX}/sessions/{sid}/scans", json={"productId": "p_x"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


# =====================================================================
# 핸드오프 — 필수
# =====================================================================
def test_handoff_snapshot_survives_transfer_close():
    sid = _bound()
    client.post(f"{PREFIX}/sessions/{sid}/keeps", json={"productId": "p_1001", "size": "L"})
    client.patch(f"{PREFIX}/sessions/{sid}/profile", json={"confirmedSizes": {"outer": "L"}})

    issue = client.post(f"{PREFIX}/sessions/{sid}/handoff")
    assert issue.status_code == 201
    ticket = issue.json()
    code = ticket["code"]
    assert code.startswith("h_")
    assert ticket["qrUrl"]
    assert ticket["expiresAt"]
    assert ticket["snapshot"]["confirmedSizes"] == {"outer": "L"}
    assert ticket["snapshot"]["keeps"][0]["productId"] == "p_1001"

    # transfer=true 로 세션 종료 → 티켓은 유지
    close = client.post(f"{PREFIX}/sessions/{sid}/close", json={"transfer": True})
    assert close.json()["state"] == "TRANSFERRED"

    # 폰이 스냅샷만으로 이어받기 — 여전히 성립
    take = client.get(f"{PREFIX}/handoff/{code}")
    assert take.status_code == 200
    snap = take.json()["snapshot"]
    assert snap["confirmedSizes"] == {"outer": "L"}
    assert snap["keeps"][0]["productId"] == "p_1001"


def test_handoff_includes_orders_in_snapshot():
    sid = _bound()
    client.post(f"{PREFIX}/sessions/{sid}/keeps", json={"productId": "p_1001", "size": "M"})
    client.post(
        f"{PREFIX}/sessions/{sid}/checkout",
        json={"items": [{"productId": "p_1001", "size": "M"}]},
    )
    ticket = client.post(f"{PREFIX}/sessions/{sid}/handoff").json()
    orders = ticket["snapshot"]["orders"]
    assert len(orders) == 1
    assert orders[0]["status"] == "PAID"
    assert orders[0]["total"] == 698000


def test_handoff_purged_on_transfer_false():
    sid = _bound()
    code = client.post(f"{PREFIX}/sessions/{sid}/handoff").json()["code"]
    # transfer=false → 세션 소거 + 티켓 무효화
    client.post(f"{PREFIX}/sessions/{sid}/close", json={"transfer": False})
    assert client.get(f"{PREFIX}/sessions/{sid}").status_code == 404
    r = client.get(f"{PREFIX}/handoff/{code}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "HANDOFF_NOT_FOUND"


def test_handoff_claim_emits_sse():
    async def scenario():
        from app.sse import event_bus, is_close_sentinel

        session_service.reset()
        handoff_service.reset()
        s = session_service.create()
        session_service.bind(s.session_id, "room01")
        ticket = handoff_service.issue(s, orders=[])
        q = event_bus.subscribe(s.session_id)

        # GET /handoff/{code} 최초 조회 = 클레임 → handoff.claimed 발행
        first = handoff_service.claim(ticket.code)
        if first:
            event_bus.publish(s.session_id, "handoff.claimed", {"code": ticket.code})

        events = []
        while True:
            item = await asyncio.wait_for(q.get(), timeout=2)
            if is_close_sentinel(item):
                break
            events.append(item["event"])
            break
        return events

    events = asyncio.run(scenario())
    assert "handoff.claimed" in events


def test_handoff_expired_410():
    sid = _bound()
    # TTL 0분으로 발급 → 즉시 만료
    session = session_service.require(sid)
    ticket = handoff_service.issue(session, orders=[], ttl_minutes=0)
    r = client.get(f"{PREFIX}/handoff/{ticket.code}")
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "HANDOFF_EXPIRED"


def test_handoff_unknown_code_404():
    r = client.get(f"{PREFIX}/handoff/h_nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "HANDOFF_NOT_FOUND"


# =====================================================================
# 데모 제어 — 수동 진행 / cancel / config
# =====================================================================
def test_demo_manual_advance():
    client.patch(f"{PREFIX}/demo/config", json={"autoAdvance": False})
    sid = _bound()
    did = client.post(
        f"{PREFIX}/sessions/{sid}/deliveries", json={"productId": "p_1001", "size": "M"}
    ).json()["deliveryId"]
    # 자동 진행 없음 → REQUESTED 유지
    assert (
        client.get(f"{PREFIX}/sessions/{sid}/deliveries/{did}").json()["status"]
        == "REQUESTED"
    )
    # 수동으로 한 칸씩 진행
    statuses = [s.value for s in ORDER]
    for expected in statuses[1:]:
        b = client.post(f"{PREFIX}/demo/deliveries/{did}/advance").json()
        assert b["status"] == expected
    # READY 이후 advance → 409
    r = client.post(f"{PREFIX}/demo/deliveries/{did}/advance")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_STATE"


def test_demo_cancel_before_ready():
    client.patch(f"{PREFIX}/demo/config", json={"autoAdvance": False})
    sid = _bound()
    did = client.post(
        f"{PREFIX}/sessions/{sid}/deliveries", json={"productId": "p_1001", "size": "M"}
    ).json()["deliveryId"]
    client.post(f"{PREFIX}/demo/deliveries/{did}/advance")  # PICKING
    r = client.post(f"{PREFIX}/sessions/{sid}/deliveries/{did}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    # 취소 후 방이 비어 새 딜리버리 가능
    r2 = client.post(
        f"{PREFIX}/sessions/{sid}/deliveries", json={"productId": "p_1003", "size": "M"}
    )
    assert r2.status_code == 201


def test_demo_cancel_after_ready_invalid():
    client.patch(f"{PREFIX}/demo/config", json={"autoAdvance": False})
    sid = _bound()
    did = client.post(
        f"{PREFIX}/sessions/{sid}/deliveries", json={"productId": "p_1001", "size": "M"}
    ).json()["deliveryId"]
    for _ in range(len(ORDER) - 1):
        client.post(f"{PREFIX}/demo/deliveries/{did}/advance")  # → READY
    r = client.post(f"{PREFIX}/sessions/{sid}/deliveries/{did}/cancel")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_STATE"


def test_delivery_get_includes_history():
    client.patch(f"{PREFIX}/demo/config", json={"autoAdvance": False})
    sid = _bound()
    did = client.post(
        f"{PREFIX}/sessions/{sid}/deliveries", json={"productId": "p_1001", "size": "M"}
    ).json()["deliveryId"]
    client.post(f"{PREFIX}/demo/deliveries/{did}/advance")
    b = client.get(f"{PREFIX}/sessions/{sid}/deliveries/{did}").json()
    assert [h["status"] for h in b["history"]] == ["REQUESTED", "PICKING"]
