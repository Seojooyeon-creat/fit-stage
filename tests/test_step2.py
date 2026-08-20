"""STEP 2 테스트 — 세션 라이프사이클 · 딜리버리 상태머신 · SSE · 킵.

필수 테스트:
  1. 에어락 불변식: 어떤 전이 시퀀스로도 두 문 동시 OPEN 불가
  2. 상태머신 순서: 7단계 건너뛰기·역행 불가
  3. 방당 활성 딜리버리 1건: 진행 중 새 요청 → 409
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import CustomerDoor, Light, ServiceDoor
from app.services.delivery import ORDER, delivery_service
from app.services.room import AirlockViolation, room_service
from app.services.session import session_service
from app.sse import event_bus, is_close_sentinel

client = TestClient(app)
PREFIX = "/api/v1"


@pytest.fixture(autouse=True)
def _reset():
    """각 테스트 전후 데모 리셋 (독립성 보장)."""
    client.post(f"{PREFIX}/demo/reset")
    yield
    client.post(f"{PREFIX}/demo/reset")


# --- 헬퍼 --------------------------------------------------------------
def _new_bound_session(room_id: str = "room01") -> str:
    sid = client.post(f"{PREFIX}/sessions").json()["sessionId"]
    client.post(f"{PREFIX}/sessions/{sid}/bind", json={"roomId": room_id})
    return sid


async def _run_delivery(
    product_id: str = "p_1001",
    size: str = "M",
    step: float = 0.02,
    room: str = "room01",
):
    """딜리버리를 상태머신 완주까지 직접 구동한다 (asyncio 루프에서 타이머 진행).

    TestClient 포털 루프는 백그라운드 태스크를 진행시키지 않으므로,
    상태머신·SSE 검증은 서비스 계층을 asyncio로 직접 구동한다.
    반환: (session, delivery, 발행된 이벤트명 목록, 이벤트별 워드로브 스냅샷)
    """
    delivery_service.reset()
    session_service.reset()
    room_service.reset()
    delivery_service.set_step_seconds(step)

    s = session_service.create()
    session_service.bind(s.session_id, room)
    q = event_bus.subscribe(s.session_id)

    delivery, _eta = delivery_service.create(s, product_id, size)

    events: list[str] = []
    snapshots: list[tuple] = []
    while True:
        item = await asyncio.wait_for(q.get(), timeout=3)
        if is_close_sentinel(item):
            break
        events.append(item["event"])
        w = room_service.get_wardrobe(room)
        snapshots.append((w.service_door, w.customer_door, w.light))
        if item["event"] == "wardrobe.ready":
            break
    event_bus.unsubscribe(s.session_id, q)
    return s, delivery, events, snapshots


# =====================================================================
# 세션 라이프사이클
# =====================================================================
def test_create_session():
    r = client.post(f"{PREFIX}/sessions")
    assert r.status_code == 201
    b = r.json()
    assert b["state"] == "CREATED"
    assert b["sessionId"].startswith("s_")
    assert len(b["sessionId"]) >= 8  # s_ + 6자 이상


def test_bind_returns_welcome():
    sid = client.post(f"{PREFIX}/sessions").json()["sessionId"]
    r = client.post(f"{PREFIX}/sessions/{sid}/bind", json={"roomId": "room01"})
    assert r.status_code == 200
    b = r.json()
    assert b["state"] == "BOUND"
    assert b["roomId"] == "room01"
    assert b["welcome"]["message"]["text"]
    chips = b["welcome"]["uiActions"][0]
    assert chips["type"] == "SHOW_CHIPS"
    assert len(chips["chips"]) >= 2


def test_room_occupied():
    _new_bound_session("room01")
    sid2 = client.post(f"{PREFIX}/sessions").json()["sessionId"]
    r = client.post(f"{PREFIX}/sessions/{sid2}/bind", json={"roomId": "room01"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ROOM_OCCUPIED"


def test_get_snapshot():
    sid = _new_bound_session()
    b = client.get(f"{PREFIX}/sessions/{sid}").json()
    assert b["sessionId"] == sid
    assert b["state"] == "BOUND"
    assert "scannedProductIds" in b


def test_close_purges_session():
    sid = _new_bound_session()
    r = client.post(f"{PREFIX}/sessions/{sid}/close", json={"transfer": False})
    assert r.status_code == 200
    assert r.json() == {"sessionId": sid, "state": "CLOSED", "purged": True}
    # 소거 후 조회는 404
    assert client.get(f"{PREFIX}/sessions/{sid}").status_code == 404


def test_close_transfer_keeps_session():
    sid = _new_bound_session()
    r = client.post(f"{PREFIX}/sessions/{sid}/close", json={"transfer": True})
    assert r.json()["state"] == "TRANSFERRED"
    assert r.json()["purged"] is False
    # 방은 해제되어 재입실 가능
    sid2 = client.post(f"{PREFIX}/sessions").json()["sessionId"]
    assert (
        client.post(f"{PREFIX}/sessions/{sid2}/bind", json={"roomId": "room01"}).status_code
        == 200
    )


def test_get_unknown_session_404():
    r = client.get(f"{PREFIX}/sessions/s_ghost")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SESSION_NOT_FOUND"


# =====================================================================
# 딜리버리 상태머신
# =====================================================================
def test_delivery_create_requires_bound_session():
    sid = client.post(f"{PREFIX}/sessions").json()["sessionId"]  # CREATED, not bound
    r = client.post(
        f"{PREFIX}/sessions/{sid}/deliveries", json={"productId": "p_1001", "size": "M"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_STATE"


def test_delivery_out_of_stock():
    sid = _new_bound_session()
    # p_1002 튤립 실크 셔츠 L은 품절
    r = client.post(
        f"{PREFIX}/sessions/{sid}/deliveries", json={"productId": "p_1002", "size": "L"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "OUT_OF_STOCK"


def test_delivery_unknown_product():
    sid = _new_bound_session()
    r = client.post(
        f"{PREFIX}/sessions/{sid}/deliveries", json={"productId": "p_x", "size": "M"}
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


def test_delivery_eta():
    sid = _new_bound_session()
    r = client.post(
        f"{PREFIX}/sessions/{sid}/deliveries", json={"productId": "p_1001", "size": "M"}
    )
    assert r.status_code == 201
    b = r.json()
    assert b["status"] == "REQUESTED"
    assert b["etaSeconds"] == 12  # 기본 합계 11.5초 → ceil 12


# --- 필수 3: 방당 활성 딜리버리 1건 ------------------------------------
def test_one_active_delivery_per_room():
    # 진행 중(REQUESTED, 비종료 상태) 새 요청 → 409 (동기 검사)
    sid = _new_bound_session()
    r1 = client.post(
        f"{PREFIX}/sessions/{sid}/deliveries", json={"productId": "p_1001", "size": "M"}
    )
    assert r1.status_code == 201
    r2 = client.post(
        f"{PREFIX}/sessions/{sid}/deliveries", json={"productId": "p_1003", "size": "M"}
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "DELIVERY_IN_PROGRESS"


def test_room_freed_after_ready():
    # 종료 상태(READY) 도달 후에는 방이 비어 새 딜리버리 가능
    async def scenario():
        s, _d, _events, _snaps = await _run_delivery(step=0.02)
        assert delivery_service.active_delivery_for_room("room01") is None
        d2, _ = delivery_service.create(s, "p_1003", "M")  # 다시 요청 가능
        status = d2.status
        delivery_service.reset()  # 두 번째 타이머 정리
        return status

    assert asyncio.run(scenario()).value == "REQUESTED"


# --- 필수 2: 상태머신 순서 (건너뛰기·역행 불가) ------------------------
def test_state_machine_order():
    async def scenario():
        _s, d, _events, _snaps = await _run_delivery(step=0.02)
        return d.history

    history = asyncio.run(scenario())
    statuses = [h.status.value for h in history]
    expected = [s.value for s in ORDER]  # REQUESTED..READY, 정확히 7단계
    assert statuses == expected, statuses

    # 이력 시각이 단조 증가 (역행 없음)
    ats = [h.at for h in history]
    assert ats == sorted(ats)


# --- 필수 1: 에어락 불변식 --------------------------------------------
def test_airlock_setter_rejects_both_open():
    # serviceDoor OPEN + customerDoor OPEN 은 코드 레벨에서 차단
    room_service.reset_room("room01")
    with pytest.raises(AirlockViolation):
        room_service.set_wardrobe(
            "room01",
            service_door=ServiceDoor.OPEN,
            customer_door=CustomerDoor.OPEN,
        )
    with pytest.raises(AirlockViolation):
        room_service.set_wardrobe(
            "room01",
            service_door=ServiceDoor.OPEN,
            customer_door=CustomerDoor.UNLOCKED,
        )


def test_airlock_holds_across_full_delivery_run():
    async def scenario():
        _s, _d, events, snaps = await _run_delivery(step=0.03)
        return events, snaps

    events, snaps = asyncio.run(scenario())

    assert snaps, "워드로브 스냅샷이 수집되지 않음"
    saw_service_open = False
    for service, customer, _light in snaps:
        # ★ 두 문 동시 OPEN 은 어떤 순간에도 존재하지 않는다
        if service == ServiceDoor.OPEN:
            saw_service_open = True
            assert customer == CustomerDoor.LOCKED
    assert saw_service_open, "문① 개방(ENTERING) 구간을 관측하지 못함"

    # 최종 리빌 상태
    final_service, final_customer, final_light = snaps[-1]
    assert final_service == ServiceDoor.CLOSED
    assert final_customer == CustomerDoor.UNLOCKED
    assert final_light == Light.WHITE


def test_wardrobe_transition_table_preserves_invariant():
    """상태머신이 사용하는 각 워드로브 전이가 불변식을 유지하는지 직접 검증."""
    room_service.reset_room("room01")
    # ENTERING: 문① 개방 (고객문 LOCKED)
    w = room_service.set_wardrobe(
        "room01", service_door=ServiceDoor.OPEN, customer_door=CustomerDoor.LOCKED
    )
    assert not (w.service_door == ServiceDoor.OPEN and w.customer_door != CustomerDoor.LOCKED)
    # SEALED
    room_service.set_wardrobe("room01", service_door=ServiceDoor.CLOSED)
    # STAGING
    room_service.set_wardrobe("room01", light=Light.AMBER)
    # READY
    w = room_service.set_wardrobe(
        "room01", light=Light.WHITE, customer_door=CustomerDoor.UNLOCKED
    )
    assert w.service_door == ServiceDoor.CLOSED  # 문② 열릴 땐 문① 닫혀 있음


# =====================================================================
# 킵
# =====================================================================
def test_keep_add_and_total():
    sid = _new_bound_session()
    r = client.post(
        f"{PREFIX}/sessions/{sid}/keeps", json={"productId": "p_1001", "size": "M"}
    )
    assert r.status_code == 201
    b = r.json()
    assert len(b["keeps"]) == 1
    assert b["total"] == 698000
    # 두 번째 킵 → 합계 누적
    b2 = client.post(
        f"{PREFIX}/sessions/{sid}/keeps", json={"productId": "p_1002", "size": "M"}
    ).json()
    assert b2["total"] == 698000 + 258000
    # 세션 스냅샷에도 반영
    snap = client.get(f"{PREFIX}/sessions/{sid}").json()
    assert len(snap["keeps"]) == 2


def test_keep_remove():
    sid = _new_bound_session()
    client.post(f"{PREFIX}/sessions/{sid}/keeps", json={"productId": "p_1001", "size": "M"})
    r = client.delete(f"{PREFIX}/sessions/{sid}/keeps/p_1001")
    assert r.status_code == 200
    assert r.json()["keeps"] == []
    assert r.json()["total"] == 0


# =====================================================================
# SSE — 이벤트 버스를 asyncio로 직접 구동 (TestClient 스트리밍 데드락 회피)
# =====================================================================
def test_sse_delivery_and_ready_events():
    async def scenario() -> list[str]:
        delivery_service.reset()
        session_service.reset()
        room_service.reset()
        delivery_service.set_step_seconds(0.03)

        s = session_service.create()
        session_service.bind(s.session_id, "room01")
        q = event_bus.subscribe(s.session_id)  # bind 직후 구독

        delivery_service.create(s, "p_1001", "M")  # 상태머신 시작

        events: list[str] = []
        while True:
            item = await asyncio.wait_for(q.get(), timeout=3)
            if is_close_sentinel(item):
                break
            events.append(item["event"])
            if item["event"] == "wardrobe.ready":
                break
        event_bus.unsubscribe(s.session_id, q)
        return events

    events = asyncio.run(scenario())
    assert "delivery.updated" in events
    assert "wardrobe.updated" in events
    assert "wardrobe.ready" in events
    # 리빌은 항상 마지막 (준비 완료 트리거)
    assert events[-1] == "wardrobe.ready"


def test_sse_session_closed_ends_stream():
    async def scenario() -> list:
        delivery_service.reset()
        session_service.reset()
        room_service.reset()

        s = session_service.create()
        session_service.bind(s.session_id, "room01")
        q = event_bus.subscribe(s.session_id)

        session_service.close(s.session_id, transfer=False)  # 발행 + 스트림 종료

        events: list = []
        while True:
            item = await asyncio.wait_for(q.get(), timeout=2)
            if is_close_sentinel(item):
                events.append("__stream_end__")
                break
            events.append(item["event"])
        return events

    events = asyncio.run(scenario())
    assert "session.closed" in events
    assert events[-1] == "__stream_end__"  # session.closed 직후 스트림 종료
