# FIT STAGE — Backend (FastAPI)

> 손님이 방을 나가지 않는 오프라인 피팅 라운지. 컨시어지(LLM)와 대화하면 옷이 워드로브로 배달되고, 마음에 든 핏은 QR로 폰에 이어받는다 — **익명·삭제가 기본**인 AI 피팅 경험의 백엔드.

스펙 원본은 [`docs/`](docs/)에 있으며(기능명세서·API명세서·플로우차트 PDF), **문서가 항상 우선**한다.

---

## 아키텍처 — 1 브레인 + 도구 + 상태머신

```
        표면(무지능)                     서버                         외부
   ┌──────────────────┐        ┌───────────────────────────┐
   │  미러(태블릿 웹)  │─REST──▶│  FastAPI                  │
   │  폰(핸드오프 후)  │◀─SSE──┤   ┌─────────────────────┐ │      ┌──────────────┐
   │  워치(스캔)       │        │   │ 컨시어지 (유일 지능) │─┼─tool─▶│ Anthropic    │
   └──────────────────┘        │   │  = LLM tool-use      │ │ use  │ (tool use)   │
                               │   └──────────┬──────────┘ │      └──────────────┘
   자연어 진입점은 하나:        │              │ 도구=서비스 함수 직접 호출(단일 소스)
   POST /sessions/{id}/messages │              ▼            │
                               │   ┌─────────────────────┐ │
                               │   │ 딜리버리 상태머신     │ │   판단은 AI · 물리는 상태머신
                               │   │ (서버 타이머, 결정론) │─┼─SSE▶ delivery.updated / wardrobe.*
                               │   └─────────────────────┘ │
                               │   인메모리 저장소 · 목업 카탈로그(JSON)
                               └───────────────────────────┘
```

**5대 설계 원칙 (전 구간 강제)**
1. **지능 진입점은 하나** — 자연어를 받는 API는 `POST /api/v1/sessions/{id}/messages` 단 하나. 나머지는 전부 도구 실행·상태 조회.
2. **카탈로그 그라운딩** — 추천은 실존 `productId` + 필수 `reason`만. 스키마(`SHOW_PRODUCTS` 검증기)가 카탈로그 밖 상품을 표현 불가하게 강제.
3. **판단은 AI, 물리는 상태머신** — 문·레일·조명 전이는 서버 타이머만 수행. LLM은 딜리버리 "생성(REQUESTED)" 하나만 만들 수 있고 단계를 건너뛸 수 없다.
4. **표면(미러·폰·워치)은 무지능** — REST 호출 + SSE 구독만 하는 얇은 렌더러.
5. **익명 세션, 삭제가 기본값** — 개인정보 필드가 스키마에 없다. 퇴실 시 소거가 기본, 핸드오프(스냅샷 동결)가 유일한 예외.

### ▶ "AI 설계"의 증거 파일
- **[`app/agent/concierge.py`](app/agent/concierge.py)** — LLM tool-use 루프 (검색→판단→도구 실행→`present`로 응답 조립, 그라운딩 검증).
- **[`app/agent/prompts.py`](app/agent/prompts.py)** — 컨시어지 시스템 프롬프트 (버틀러 인격·그라운딩·되묻기·망설임 감지·스캔 활용).
- **[`app/agent/tools.py`](app/agent/tools.py)** — 도구 7종 정의 + 서비스 함수 매핑(단일 소스) + uiActions 조립.
- **[`app/agent/safemode.py`](app/agent/safemode.py)** — 키 없음/LLM 실패 시 폴백. 대본이되 **도구 호출은 진짜로** 한다.

---

## 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

bash scripts/run.sh        # 서버:  http://localhost:8000  (Swagger: /docs)
pytest -q                  # 테스트 (62개)
bash scripts/demo.sh       # S1~S6 데모 시퀀스 자동 완주 (서버 자동 기동)
```

### 프론트엔드 (통합 UI) — 백엔드와 동일 오리진에서 서빙
서버를 켠 뒤 브라우저에서 **http://localhost:8000/** 접속 → 랜딩(레이아웃은 `FitStage_front` 참고).

| 화면 | URL | 연동 |
|---|---|---|
| 랜딩 | `/ui/` | 미러·워치·**3D 워크스루** 진입 카드 |
| 피팅룸 미러 | `/ui/mirror.html` | **라이브** — 실제 컨시어지(`/messages`) + SSE 딜리버리 + 킵/결제/핸드오프 |
| 워치 | `/ui/watch.html` | **라이브** — NFC 스캔·KEEP·TAG TO SEND(실제 딜리버리) + 진행 SSE |
| 3D 워크스루 | `/ui/walk.html` | v16 1인칭 three.js 워크스루 (내 디자인 그대로) |

- 미러·워치는 `localStorage`로 **하나의 익명 세션을 공유**한다(밴드 모델) — 워치에서 보낸 옷이 미러 세션에 반영된다.
- 원본 디자인 파일(`fit-stage-*.html`)은 참고용으로 그대로 두고, 백엔드에 연동한 버전을 `web/`에 두었다. 글씨 크기를 시연용으로 크게 키웠다.
- 키가 없으면 프론트도 세이프 모드로 그대로 완주된다(같은 백엔드).

### .env (선택)
```ini
ANTHROPIC_API_KEY=       # 있으면 LLM 컨시어지, 없으면 세이프 모드
MODEL=claude-sonnet-4-6  # 기본값
```
> **키가 없어도 전체 시연이 그대로 완주된다.** 키가 없으면 서버는 자동으로 **세이프 모드**(서버 소재 대본, 실제 도구 호출)로 동작해 S1~S6를 칩 터치만으로 완주할 수 있다. `.env`는 `.gitignore`에 포함되어 커밋되지 않으며, 키는 코드 어디에도 하드코딩되어 있지 않다.

---

## 데모 시퀀스 (S1~S6) — curl 예시

`scripts/demo.sh`가 아래를 자동 완주한다. `BASE=http://localhost:8000/api/v1`.

```bash
# S1  세션 생성 → 방 바인딩 → SSE 구독
SID=$(curl -s -X POST $BASE/sessions | python3 -c "import sys,json;print(json.load(sys.stdin)['sessionId'])")
curl -s -X POST $BASE/sessions/$SID/bind -H 'Content-Type: application/json' -d '{"roomId":"room01"}'
curl -N  $BASE/sessions/$SID/events &          # 미러가 구독하는 SSE 스트림

# S2  대화 → 그라운딩 추천 (카드 3장 + 이유)
curl -s -X POST $BASE/sessions/$SID/messages -H 'Content-Type: application/json' \
  -d '{"text":"다음 주 면접인데, 너무 정장 같지는 않았으면 해요"}'

# S3  "한 치수 크게" → 딜리버리 시작 (진행은 SSE). 대기 중에도 대화 지속(채널 분리)
curl -s -X POST $BASE/sessions/$SID/messages -H 'Content-Type: application/json' \
  -d '{"text":"1번 한 치수 크게 입어볼게요"}'
curl -s -X POST $BASE/sessions/$SID/messages -H 'Content-Type: application/json' \
  -d '{"text":"기다리는 동안 데이트 룩도 보여줘"}'

# S4  리빌 — SSE로 delivery.updated ×N → wardrobe.updated(앰버→화이트) → wardrobe.ready

# S5  킵 → 결제
curl -s -X POST $BASE/sessions/$SID/messages -H 'Content-Type: application/json' -d '{"text":"이걸로 킵할게요"}'
curl -s -X POST $BASE/sessions/$SID/messages -H 'Content-Type: application/json' -d '{"text":"지금 결제할게요"}'

# S6  핸드오프 발급 → 폰 조회 → 세션 전송
CODE=$(curl -s -X POST $BASE/sessions/$SID/handoff | python3 -c "import sys,json;print(json.load(sys.stdin)['code'])")
curl -s $BASE/handoff/$CODE                      # 폰: 스냅샷만으로 렌더 (세션 소거 후에도 성립)
curl -s -X POST $BASE/sessions/$SID/close -H 'Content-Type: application/json' -d '{"transfer":true}'
```

---

## 엔드포인트

| 분류 | 메서드·경로 | 설명 |
|---|---|---|
| Session | `POST /sessions` | 익명 세션 생성(밴드 발급) → `CREATED` |
| Session | `POST /sessions/{id}/bind` | 방 바인딩 + 웰컴(칩). 점유 중 `409 ROOM_OCCUPIED` |
| Session | `GET /sessions/{id}` | 세션 스냅샷 (소거 시 `404`) |
| Session | `POST /sessions/{id}/close` | `transfer:false` 전량 소거 / `true` 티켓 유지·`TRANSFERRED` |
| **Chat** | **`POST /sessions/{id}/messages`** | **유일한 지능 진입점** — tool-use 완주 → `{messages, uiActions}` |
| Chat | `GET /sessions/{id}/messages?after=` | 대화 이력 (복구·동기화) |
| Catalog | `GET /catalog/products` | 검색 (`q, category, tpo, formalityMin/Max, size, maxPrice, limit`) |
| Catalog | `GET /catalog/products/{id}` | 단건 조회 |
| Delivery | `POST /sessions/{id}/deliveries` | 딜리버리 요청 → 상태머신 시작 |
| Delivery | `GET /sessions/{id}/deliveries/{id}` | 조회 (`history[]` 포함) |
| Delivery | `POST /sessions/{id}/deliveries/{id}/cancel` | 취소(READY 이전만) |
| Delivery | `GET /rooms/{roomId}/wardrobe` | 워드로브(문·조명) 상태 |
| Keep | `POST /sessions/{id}/keeps` · `DELETE .../keeps/{pid}` | 킵 추가/해제 |
| Profile | `GET·PATCH /sessions/{id}/profile` | 프로필 조회/부분 갱신 |
| Checkout | `POST /sessions/{id}/checkout` | 결제(목업) — 주문 기록 |
| Handoff | `POST /sessions/{id}/handoff` | 티켓 발급(스냅샷 동결, 10분) |
| Handoff | `GET /handoff/{code}` | 폰 이어받기 (만료 `410`) |
| Scan | `POST /sessions/{id}/scans` | 워치 NFC 태깅 — Product 전체 반환 |
| SSE | `GET /sessions/{id}/events` | 세션 단위 이벤트 스트림 |
| Demo | `POST /demo/reset` | 세션·딜리버리 소거 + 방·재고 원복 |
| Demo | `POST /demo/deliveries/{id}/advance` | 수동 진행(피칭 타이밍) |
| Demo | `PATCH /demo/config` | `{deliveryStepMs, autoAdvance}` |

### 딜리버리 상태머신 (에어락 안무)
```
REQUESTED →1.5s→ PICKING →2s→ ON_RAIL →3s→ ENTERING →1.5s→ SEALED →1.5s→ STAGING →2s→ READY
                                        문① OPEN         문① CLOSED      조명 AMBER    조명 WHITE·문② UNLOCK
```
- **★ 에어락 불변식**: `serviceDoor==OPEN`인 동안 `customerDoor`는 반드시 `LOCKED`. 코드 레벨(`app/services/room.py`)에서 강제하며 위반 시 `AirlockViolation`. 두 문 동시 OPEN은 어떤 호출 조합으로도 불가.
- **SSE 이벤트**: `delivery.updated` · `wardrobe.updated` · `wardrobe.ready` · `keep.updated` · `profile.updated` · `handoff.claimed` · `session.closed` · `ping`(15초).

---

## 공통 규약

- Base URL `/api/v1` · JSON **camelCase** · 시간 ISO8601+KST · 금액 KRW 정수
- ID 접두사: `s_`세션 `p_`상품 `d_`딜리버리 `o_`주문 `h_`핸드오프 `m_`메시지
- 에러는 전부 `{"error":{"code","message"}}` 형태로 통일

| HTTP | code | 상황 |
|---|---|---|
| 404 | `SESSION_NOT_FOUND` | 존재하지 않거나 소거된 세션 |
| 404 | `PRODUCT_NOT_FOUND` / `DELIVERY_NOT_FOUND` / `HANDOFF_NOT_FOUND` | 리소스 없음 |
| 409 | `SESSION_CLOSED` | 종료된 세션 조작 |
| 409 | `ROOM_OCCUPIED` | 이미 바인딩된 방 |
| 409 | `DELIVERY_IN_PROGRESS` | 방당 활성 딜리버리 1건 위반 |
| 409 | `OUT_OF_STOCK` | 요청 사이즈 재고 없음 |
| 409 | `INVALID_STATE` | 상태머신 순서 위반 |
| 410 | `HANDOFF_EXPIRED` | 만료된 핸드오프 코드 |
| 422 | `VALIDATION_ERROR` | 필드 누락·형식 오류 |
| 503 | `LLM_UNAVAILABLE` | LLM 실패/타임아웃 (1회 실패 시 반환, 2회 연속 → 세이프 모드) |

---

## 스택 · 레포 구조

Python 3.11+ · FastAPI · uvicorn · pydantic v2 · sse-starlette · httpx · pytest.
저장소는 인메모리 딕셔너리(**서버 재시작 = 전체 리셋**, 의도된 동작). CORS 전체 허용(데모).

```
app/
  main.py            FastAPI 엔트리 (전 라우트 활성)
  models.py          pydantic 모델 전부 (SHOW_PRODUCTS 그라운딩 검증기 포함)
  errors.py          공통 에러 규약
  sse.py             세션 단위 이벤트 버스
  util.py            시간(KST)·ID 유틸
  routes/            sessions chat catalog deliveries keeps checkout handoff events demo
  services/          session catalog delivery room keep profile order handoff  ← 도구=서비스 함수(단일 소스)
  agent/             concierge tools prompts safemode              ← "AI 설계"의 증거
data/catalog.json    목업 카탈로그 14종
web/                 통합 프론트 (FastAPI가 /ui 로 서빙)
  index.html         랜딩 (레이아웃: FitStage_front 참고)
  mirror.html        피팅룸 미러 — 백엔드 라이브 연동
  watch.html         워치 — 백엔드 라이브 연동
  walk.html          v16 3D 워크스루 (원본 디자인 그대로)
  fitstage.js        공유 헬퍼 (세션 공유·SSE·색상·통화)
tests/               pytest (test_catalog / test_step2 / test_step3 / test_step4)
scripts/             run.sh(서버) · demo.sh(S1~S6 완주)
docs/                기능명세서·API명세서·플로우차트 (스펙 원본, 우선)
fit-stage-*.html     원본 디자인 파일 (참고용, 미변경) · FitStage_front/ (레이아웃 참고용 React 프로토타입)
```

## 스펙 문서
- [기능명세서](docs/기능명세서.pdf) · [API명세서](docs/API명세서.pdf) · [플로우차트](docs/Flowchart.pdf)
