"""공통 유틸리티 — 시간(KST)·ID 생성."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

# ID 접두사 규약 (API명세서 §2)
#   s_(세션) p_(상품) d_(딜리버리) o_(주문) h_(핸드오프) m_(메시지)
_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def now_kst() -> datetime:
    """현재 시각을 KST(+09:00) aware datetime으로 반환."""
    return datetime.now(KST)


def now_kst_iso() -> str:
    """ISO 8601 + KST 오프셋 문자열 (예: 2026-08-19T14:30:00+09:00)."""
    return now_kst().isoformat(timespec="seconds")


def gen_id(prefix: str, length: int = 6) -> str:
    """추측 불가능한 ID 생성. 접두사 + 랜덤 문자열."""
    body = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return f"{prefix}_{body}"
