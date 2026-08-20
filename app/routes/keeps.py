"""킵·프로필 라우트 (API명세서 §5.E).

POST   /sessions/{id}/keeps               킵 추가
DELETE /sessions/{id}/keeps/{productId}   킵 해제
GET    /sessions/{id}/profile             프로필 조회
PATCH  /sessions/{id}/profile             프로필 부분 갱신 (에이전트 도구 전용)
"""
from __future__ import annotations

from fastapi import APIRouter

from app.models import KeepCreateRequest, KeepsResponse, Profile, ProfilePatchRequest
from app.services.keep import keep_service
from app.services.profile import profile_service
from app.services.session import session_service

router = APIRouter(prefix="/sessions", tags=["keeps"])


@router.post("/{session_id}/keeps", status_code=201, response_model=KeepsResponse)
async def add_keep(session_id: str, body: KeepCreateRequest) -> KeepsResponse:
    return keep_service.add(session_id, body.product_id, body.size)


@router.delete("/{session_id}/keeps/{product_id}", response_model=KeepsResponse)
async def remove_keep(session_id: str, product_id: str) -> KeepsResponse:
    return keep_service.remove(session_id, product_id)


@router.get("/{session_id}/profile", response_model=Profile)
def get_profile(session_id: str) -> Profile:
    return session_service.require(session_id).profile


@router.patch("/{session_id}/profile", response_model=Profile)
async def patch_profile(session_id: str, body: ProfilePatchRequest) -> Profile:
    session = session_service.require(session_id)
    return profile_service.update(
        session,
        confirmed_sizes=body.confirmed_sizes,
        preferences=body.preferences,
        context=body.context,
    )
