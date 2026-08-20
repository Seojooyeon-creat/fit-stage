#!/usr/bin/env bash
#
# FIT STAGE — S1~S6 데모 시퀀스 완주 스크립트 (API명세서 §7).
#
# 세션 생성 → 대화·추천 → 딜리버리(대기 중 대화 포함) → 리빌 → 킵·결제 → 핸드오프.
# LLM 키가 없으면 세이프 모드로 전체 시연이 그대로 완주된다.
#
# 사용법:  bash scripts/demo.sh
#   - 서버가 없으면 uvicorn을 자동 기동한다 (종료 시 정리).
#   - 각 단계 응답을 출력하고, 실패(HTTP >= 400) 시 즉시 중단·원인을 표시한다.
#
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${BASE_URL:-http://localhost:8000}"
API="$BASE/api/v1"
SSE_LOG="$(mktemp -t fitstage-sse.XXXXXX)"
SERVER_PID=""
SSE_PID=""

# 세이프 모드로 시연 (키가 있으면 LLM 경로, 없으면 세이프 모드 — 둘 다 동일 완주)
unset ANTHROPIC_API_KEY || true

# ---------- 유틸 ----------------------------------------------------------
c_reset="\033[0m"; c_head="\033[1;36m"; c_ok="\033[1;32m"; c_err="\033[1;31m"; c_dim="\033[2m"

step()  { echo -e "\n${c_head}━━━ $* ━━━${c_reset}"; }
ok()    { echo -e "${c_ok}  ✓ $*${c_reset}"; }
note()  { echo -e "${c_dim}  $*${c_reset}"; }
die()   { echo -e "${c_err}  ✗ $*${c_reset}"; exit 1; }

pretty() { python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin),ensure_ascii=False,indent=2))" 2>/dev/null || cat; }
pyget()  { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)"; }

LAST_BODY=""
call() {  # call METHOD PATH [JSON]
  local method="$1" path="$2" data="${3:-}"
  local args=(-s -w $'\n%{http_code}' -X "$method" "$API$path" -H 'Content-Type: application/json')
  [ -n "$data" ] && args+=(-d "$data")
  local out code body
  out="$(curl "${args[@]}")"
  code="$(printf '%s' "$out" | tail -n1)"
  body="$(printf '%s' "$out" | sed '$d')"
  printf '%s\n' "$body" | pretty
  if [ "$code" -ge 400 ]; then
    die "HTTP $code — $method $path 에서 중단"
  fi
  LAST_BODY="$body"
}

cleanup() {
  [ -n "$SSE_PID" ] && kill "$SSE_PID" 2>/dev/null || true
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
  rm -f "$SSE_LOG"
}
trap cleanup EXIT

# ---------- 서버 준비 -----------------------------------------------------
if ! curl -sf "$BASE/health" >/dev/null 2>&1; then
  note "서버가 없어 uvicorn을 기동합니다 (세이프 모드)..."
  [ -d .venv ] && source .venv/bin/activate || true
  uvicorn app.main:app --port 8000 --log-level warning &
  SERVER_PID=$!
  for _ in $(seq 1 30); do curl -sf "$BASE/health" >/dev/null 2>&1 && break; sleep 0.5; done
  curl -sf "$BASE/health" >/dev/null 2>&1 || die "서버 기동 실패"
fi
ok "서버 준비 완료: $BASE"

# 재현성: 초기화 + 연출 타이밍 단축(스크립트용)
step "DEMO RESET (재현성)"
call POST /demo/reset
call PATCH /demo/config '{"deliveryStepMs":{"PICKING":700,"ON_RAIL":700,"ENTERING":700,"SEALED":700,"STAGING":700,"READY":700},"autoAdvance":true}'

# ---------- S1: 웰컴 ------------------------------------------------------
step "S1  세션 생성 → 방 바인딩 → SSE 구독"
call POST /sessions
SID="$(printf '%s' "$LAST_BODY" | pyget "d['sessionId']")"
ok "sessionId=$SID"

call POST "/sessions/$SID/bind" '{"roomId":"room01"}'

# SSE 구독 (백그라운드) — 미러가 bind 직후 구독
curl -sN "$API/sessions/$SID/events" > "$SSE_LOG" 2>/dev/null &
SSE_PID=$!
sleep 0.5
ok "SSE 구독 시작 (PID $SSE_PID)"

# ---------- S2: 대화·추천 ------------------------------------------------
step "S2  대화 → 그라운딩 추천 (카드 3장 + 이유)"
call POST "/sessions/$SID/messages" '{"text":"다음 주 면접인데, 너무 정장 같지는 않았으면 해요"}'

# ---------- S3: 딜리버리 (대기 중 대화 포함) ------------------------------
step "S3  \"한 치수 크게\" → 딜리버리 시작 (실제 request_delivery)"
call POST "/sessions/$SID/messages" '{"text":"1번 한 치수 크게 입어볼게요"}'
DID="$(printf '%s' "$LAST_BODY" | pyget "next(a['deliveryId'] for a in d['uiActions'] if a['type']=='SHOW_DELIVERY_STATUS')")"
ok "deliveryId=$DID (진행은 SSE가 담당)"

note "── 채널 분리 증명: 딜리버리 진행 중 새 발화 (REST) ──"
call POST "/sessions/$SID/messages" '{"text":"기다리는 동안 데이트 룩도 보여줘"}'
ok "딜리버리 진행 중에도 대화가 막히지 않음 (FR-D5)"

# ---------- S4: 리빌 -----------------------------------------------------
step "S4  리빌 — SSE wardrobe.ready 수신"
for _ in $(seq 1 40); do
  grep -q "wardrobe.ready" "$SSE_LOG" 2>/dev/null && break
  sleep 0.5
done
grep -q "wardrobe.ready" "$SSE_LOG" 2>/dev/null || die "wardrobe.ready 미수신"
ok "wardrobe.ready 수신 — 준비 완료(리빌)"
echo "  받은 SSE 이벤트:"
grep '^event:' "$SSE_LOG" | sed 's/^/    /' | sort | uniq -c
note "워드로브 최종 상태:"
call GET /rooms/room01/wardrobe

# ---------- S5: 킵·결제 --------------------------------------------------
step "S5  킵 → 결제"
call POST "/sessions/$SID/messages" '{"text":"이걸로 킵할게요"}'
call POST "/sessions/$SID/messages" '{"text":"지금 결제할게요"}'

# ---------- S6: 핸드오프 -------------------------------------------------
step "S6  핸드오프 발급 → 폰 조회 → 세션 전송(close transfer:true)"
call POST "/sessions/$SID/handoff"
CODE="$(printf '%s' "$LAST_BODY" | pyget "d['code']")"
ok "handoff code=$CODE"

note "── 폰에서 이어받기 (GET /handoff/{code}) → handoff.claimed 발행 ──"
call GET "/handoff/$CODE"

call POST "/sessions/$SID/close" '{"transfer":true}'
ok "세션 TRANSFERRED — 미러는 웰컴 화면으로 복귀"

note "── 스냅샷 동결 증명: 세션 종료 후에도 폰 화면 성립 ──"
call GET "/handoff/$CODE"

echo -e "\n${c_ok}━━━ S1~S6 데모 완주 ✓ ━━━${c_reset}"
