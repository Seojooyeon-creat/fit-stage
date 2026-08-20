"""프로필 서비스 (API명세서 §5.E / §6, 도구: session_profile).

대화에서 추출한 정형 프로필(확정 사이즈·취향·맥락)을 세션에 누적한다.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.errors import invalid_state, session_closed
from app.models import Profile, SessionState
from app.sse import event_bus

logger = logging.getLogger("fitstage.profile")


class ProfileService:
    def update(
        self,
        session,
        *,
        confirmed_sizes: Optional[dict[str, str]] = None,
        preferences: Optional[list[str]] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> Profile:
        if session.state in (SessionState.CLOSED, SessionState.TRANSFERRED):
            raise session_closed()
        if session.state != SessionState.BOUND:
            raise invalid_state("먼저 방에 입실해 주세요.")

        p = session.profile
        if confirmed_sizes:
            p.confirmed_sizes.update(confirmed_sizes)
        if preferences:
            for pref in preferences:
                if pref and pref not in p.preferences:
                    p.preferences.append(pref)
        if context:
            p.context.update(context)

        event_bus.publish(
            session.session_id, "profile.updated", p.model_dump(by_alias=True)
        )
        logger.info("profile updated session=%s", session.session_id)
        return p


profile_service = ProfileService()
