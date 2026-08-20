"""공통 에러 규약 (API명세서 §2).

모든 에러는 동일한 형태로 반환한다:
    {"error": {"code": "OUT_OF_STOCK", "message": "L 사이즈 재고가 없어요."}}
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """도메인 에러. code·message·http_status를 담아 공통 형태로 직렬화된다."""

    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


# --- 편의 팩토리 (API명세서 §2 에러 표) ---------------------------------
def session_not_found(message: str = "세션을 찾을 수 없어요.") -> ApiError:
    return ApiError("SESSION_NOT_FOUND", message, 404)


def product_not_found(message: str = "해당 상품을 찾을 수 없어요.") -> ApiError:
    return ApiError("PRODUCT_NOT_FOUND", message, 404)


def delivery_not_found(message: str = "해당 딜리버리를 찾을 수 없어요.") -> ApiError:
    return ApiError("DELIVERY_NOT_FOUND", message, 404)


def handoff_not_found(message: str = "핸드오프 코드를 찾을 수 없어요.") -> ApiError:
    return ApiError("HANDOFF_NOT_FOUND", message, 404)


def session_closed(message: str = "이미 종료된 세션이에요.") -> ApiError:
    return ApiError("SESSION_CLOSED", message, 409)


def room_occupied(message: str = "다른 방을 안내해 드릴게요.") -> ApiError:
    return ApiError("ROOM_OCCUPIED", message, 409)


def delivery_in_progress(
    message: str = "지금 옷이 오고 있어요 — 도착 후 바로 준비할게요.",
) -> ApiError:
    return ApiError("DELIVERY_IN_PROGRESS", message, 409)


def out_of_stock(message: str = "요청하신 사이즈 재고가 없어요.") -> ApiError:
    return ApiError("OUT_OF_STOCK", message, 409)


def invalid_state(message: str = "지금은 처리할 수 없는 상태예요.") -> ApiError:
    return ApiError("INVALID_STATE", message, 409)


def handoff_expired(message: str = "세션이 만료됐어요.") -> ApiError:
    return ApiError("HANDOFF_EXPIRED", message, 410)


def llm_unavailable(message: str = "다시 한번 말씀해 주시겠어요?") -> ApiError:
    return ApiError("LLM_UNAVAILABLE", message, 503)


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    """공통 에러 핸들러를 앱에 등록한다."""

    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI 기본 422를 공통 형태로 래핑 (VALIDATION_ERROR)
        detail = exc.errors()
        first = detail[0] if detail else {}
        loc = ".".join(str(p) for p in first.get("loc", []))
        msg = first.get("msg", "요청 형식이 올바르지 않아요.")
        message = f"{loc}: {msg}" if loc else msg
        return JSONResponse(
            status_code=422,
            content=_error_body("VALIDATION_ERROR", message),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 라우팅 404 등 프레임워크 기본 에러도 공통 형태로 통일
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(code, message),
        )
