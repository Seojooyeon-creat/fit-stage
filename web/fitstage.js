/* FIT STAGE — 공유 프론트 헬퍼.
 * 미러·워치가 하나의 익명 세션을 공유한다(밴드 모델: localStorage로 sessionId 공유).
 * 백엔드(FastAPI)와 동일 오리진에서 서빙되므로 API_BASE는 현재 오리진 + /api/v1.
 */
(function (global) {
  const API = `${location.origin}/api/v1`;
  const ROOM = "room01";
  const LS_KEY = "fitstage.sessionId";

  async function api(path, opts) {
    const res = await fetch(`${API}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (!res.ok) {
      let body = {};
      try { body = await res.json(); } catch (e) {}
      const err = new Error((body.error && body.error.message) || res.statusText);
      err.code = body.error && body.error.code;
      err.status = res.status;
      throw err;
    }
    if (res.status === 204) return null;
    return res.json();
  }

  /** 세션 확보: 저장된 세션이 유효하면 재사용(밴드 공유), 아니면 새로 발급+입실. */
  async function ensureSession() {
    const saved = localStorage.getItem(LS_KEY);
    if (saved) {
      try {
        const s = await api(`/sessions/${saved}`);
        if (s && s.state === "BOUND") return s;
      } catch (e) { /* stale → 재발급 */ }
    }
    return await createAndBind(true);
  }

  /** 세션 생성 + room01 입실. 방이 점유돼 있으면(단일룸 데모) 리셋 후 1회 재시도. */
  async function createAndBind(allowReset) {
    const created = await api("/sessions", { method: "POST" });
    try {
      await api(`/sessions/${created.sessionId}/bind`, {
        method: "POST",
        body: JSON.stringify({ roomId: ROOM }),
      });
    } catch (e) {
      if (e.code === "ROOM_OCCUPIED" && allowReset) {
        // 오래된 세션이 방을 점유 → 데모 리셋으로 자가 복구 후 재시도
        await api("/demo/reset", { method: "POST" });
        return createAndBind(false); // 리셋으로 방금 만든 세션도 소거되므로 새로 생성
      }
      throw e;
    }
    localStorage.setItem(LS_KEY, created.sessionId);
    return await api(`/sessions/${created.sessionId}`);
  }

  function clearSession() { localStorage.removeItem(LS_KEY); }

  /** 색상 → 스와치 그라디언트 */
  const SWATCH = {
    camel: ["#C19A6B", "#8f6b41"], ivory: ["#EDE4D3", "#cfc4ae"],
    charcoal: ["#3a3835", "#232120"], burgundy: ["#6E2B36", "#4a1d26"],
    navy: ["#2b3a52", "#18202e"], skyblue: ["#aecbe0", "#7fa6c4"],
    oatmeal: ["#E5DAC6", "#c9bda3"], beige: ["#E4D5BC", "#c8b492"],
    gray: ["#6a6a6a", "#3f3f3f"], brown: ["#7a5230", "#4f331c"],
    black: ["#2a2a2a", "#141414"], white: ["#f2f2f2", "#d8d8d8"],
  };
  function grad(color) {
    const c = SWATCH[(color || "").toLowerCase()] || ["#6a6a6a", "#3f3f3f"];
    return `linear-gradient(160deg,${c[0]},${c[1]})`;
  }

  function krw(n) { return "₩ " + Number(n || 0).toLocaleString("ko-KR"); }

  /** 세션 SSE 구독 — handlers = {"delivery.updated":fn, ...} */
  function subscribe(sessionId, handlers) {
    const es = new EventSource(`${API}/sessions/${sessionId}/events`);
    Object.keys(handlers).forEach((ev) => {
      es.addEventListener(ev, (e) => {
        let data = {};
        try { data = JSON.parse(e.data); } catch (x) {}
        handlers[ev](data);
      });
    });
    return es;
  }

  global.FitStage = { API, ROOM, api, ensureSession, clearSession, grad, krw, subscribe };
})(window);
