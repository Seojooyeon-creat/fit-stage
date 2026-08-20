"""FIT STAGE 백엔드 — FastAPI 엔트리포인트.

- Base URL: /api/v1 (API명세서 §2)
- 프론트(web/): /ui 로 정적 서빙 — 미러·워치·3D 워크스루·랜딩. 백엔드와 동일 오리진.
- CORS 전체 허용 (데모)
- 공통 에러 핸들러 등록 (§2 에러 규약)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# .env 로드 — 다른 앱 모듈이 os.getenv를 읽기 전에 최우선으로 실행.
# 키가 있으면 LLM 컨시어지, 없으면 세이프 모드로 동작한다.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.errors import register_error_handlers
from app.routes import (
    catalog,
    chat,
    checkout,
    deliveries,
    demo,
    events,
    handoff,
    keeps,
    sessions,
)

API_PREFIX = "/api/v1"

app = FastAPI(
    title="FIT STAGE API",
    version="0.1.0",
    description="오프라인 피팅 컨시어지 백엔드 (해커톤 데모)",
)

# CORS 전체 허용 (데모)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 공통 에러 핸들러
register_error_handlers(app)

# --- 라우트 등록 -----------------------------------------------------------
# 전 엔드포인트 활성 (STEP1~4)
app.include_router(catalog.router, prefix=API_PREFIX)
app.include_router(demo.router, prefix=API_PREFIX)
app.include_router(sessions.router, prefix=API_PREFIX)
app.include_router(deliveries.router, prefix=API_PREFIX)
app.include_router(keeps.router, prefix=API_PREFIX)
app.include_router(events.router, prefix=API_PREFIX)
app.include_router(chat.router, prefix=API_PREFIX)
app.include_router(checkout.router, prefix=API_PREFIX)
app.include_router(handoff.router, prefix=API_PREFIX)


@app.on_event("startup")
def _log_mode() -> None:
    if os.getenv("ANTHROPIC_API_KEY"):
        model = os.getenv("MODEL") or "claude-sonnet-4-6"
        logging.getLogger("fitstage").info("LLM 컨시어지 활성 (model=%s)", model)
    else:
        logging.getLogger("fitstage").info("세이프 모드 (ANTHROPIC_API_KEY 없음)")


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "llm": bool(os.getenv("ANTHROPIC_API_KEY")),
        "mode": "llm" if os.getenv("ANTHROPIC_API_KEY") else "safe",
    }


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


# --- 프론트 정적 서빙 (web/) — 미러·워치·3D·랜딩 ---------------------------
# 라우트/에러 핸들러 등록 이후에 마운트해야 API 경로가 가려지지 않는다.
_WEB_DIR = Path(__file__).resolve().parents[1] / "web"
if _WEB_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_WEB_DIR), html=True), name="ui")
