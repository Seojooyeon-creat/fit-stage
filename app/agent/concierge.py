"""컨시어지 — 유일한 지능 진입점의 LLM tool-use 루프 (플로우차트 ②).

POST /messages 한 번 안에서 검색→판단→도구 실행을 완주한 뒤 text + uiActions를 만든다.
LLM은 도구를 통해서만 세계와 상호작용하며, 상태머신 전이는 만들 수 없다 (request_delivery는 "생성"만).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx
from pydantic import ValidationError

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import (
    TOOL_DEFS,
    ToolContext,
    build_ui_actions,
    execute_tool,
)
from app.models import Message, MessageRole, MessagesResponse, ProductRecommendation
from app.services.session import session_service

logger = logging.getLogger("fitstage.concierge")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024
MAX_ITERATIONS = 6  # tool-use 루프 상한 (무한 루프 방지)

# 세션별 LLM 연속 실패 카운터 (세이프 모드 전환 판단용)
_failures: dict[str, int] = {}


class LLMError(RuntimeError):
    """LLM API 실패·타임아웃."""


def is_llm_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def record_failure(session_id: str) -> int:
    _failures[session_id] = _failures.get(session_id, 0) + 1
    return _failures[session_id]


def reset_failures(session_id: str) -> None:
    _failures.pop(session_id, None)


def reset() -> None:
    _failures.clear()


async def _call_anthropic(messages: list[dict[str, Any]]) -> dict[str, Any]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    model = os.getenv("MODEL") or DEFAULT_MODEL
    payload = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": messages,
        "tools": TOOL_DEFS,
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(ANTHROPIC_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            raise LLMError(f"anthropic {resp.status_code}: {resp.text[:200]}")
        return resp.json()
    except httpx.HTTPError as e:
        raise LLMError(str(e)) from e


def _validate_products(raw: Optional[list[dict]]) -> list[ProductRecommendation]:
    """present.products를 그라운딩 검증한다 (카탈로그 밖 productId·빈 reason이면 실패)."""
    if not raw:
        return []
    return [ProductRecommendation.model_validate(item) for item in raw]


async def run(session, text: str) -> MessagesResponse:
    """대화 한 턴을 완주한다. 실패 시 LLMError를 던진다 (호출부가 세이프 모드/503 판단)."""
    sid = session.session_id
    transcript = session_service.get_transcript(sid)
    transcript.append({"role": "user", "content": text})

    ctx = ToolContext(session=session)
    final_text = ""
    products: list[ProductRecommendation] = []
    chips: Optional[list[str]] = None
    finalized = False

    for _ in range(MAX_ITERATIONS):
        resp = await _call_anthropic(transcript)
        content = resp.get("content", [])
        transcript.append({"role": "assistant", "content": content})

        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses:
            # present 없이 종료 — 텍스트만 사용 (폴백)
            final_text = " ".join(
                b.get("text", "") for b in content if b.get("type") == "text"
            ).strip()
            break

        tool_results: list[dict[str, Any]] = []
        for tu in tool_uses:
            name = tu.get("name")
            tu_id = tu.get("id")
            tool_input = tu.get("input", {}) or {}

            if name == "present":
                try:
                    products = _validate_products(tool_input.get("products"))
                except ValidationError as e:
                    # 그라운딩 위반 → 도구 에러로 돌려주고 다시 시도하게 한다
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu_id,
                            "is_error": True,
                            "content": (
                                "카탈로그에 없는 상품이 포함됐어요. "
                                f"search_catalog 결과의 productId만 사용하세요. ({e.error_count()}건)"
                            ),
                        }
                    )
                    continue
                final_text = (tool_input.get("text") or "").strip()
                chips = tool_input.get("chips")
                finalized = True
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu_id, "content": "presented"}
                )
            else:
                result = execute_tool(name, tool_input, ctx)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        transcript.append({"role": "user", "content": tool_results})
        if finalized:
            break

    session_service.save_transcript(sid, transcript)

    if not final_text:
        final_text = "다시 한번 말씀해 주시겠어요?"

    ui_actions = build_ui_actions(ctx, products=products, chips=chips)
    message: Message = session_service.add_message(sid, MessageRole.ASSISTANT, final_text)
    logger.info("concierge turn session=%s tools=%s", sid, ctx.tool_calls)
    return MessagesResponse(messages=[message], ui_actions=ui_actions)
