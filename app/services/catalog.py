"""카탈로그 서비스 — 그라운딩의 단일 소스 (API명세서 §6).

에이전트의 search_catalog 도구와 GET /catalog/products가 공유하는 로직.
목업 카탈로그(data/catalog.json)를 인메모리에 적재하고 검색/조회/재고복원을 담당한다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from app.models import Product

# data/catalog.json 경로 (repo 루트 기준)
_CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "catalog.json"


class CatalogService:
    def __init__(self, path: Path = _CATALOG_PATH):
        self._path = path
        self._products: dict[str, Product] = {}
        self.reload()

    # --- 적재·복원 ------------------------------------------------------
    def reload(self) -> None:
        """파일에서 카탈로그를 다시 읽어 재고를 원복한다 (demo/reset용)."""
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._products = {
            item["productId"]: Product.model_validate(item) for item in raw
        }

    # --- 조회 ----------------------------------------------------------
    def exists(self, product_id: str) -> bool:
        return product_id in self._products

    def get(self, product_id: str) -> Optional[Product]:
        return self._products.get(product_id)

    def all(self) -> list[Product]:
        return list(self._products.values())

    # --- 검색 (GET /catalog/products) ----------------------------------
    def search(
        self,
        *,
        q: Optional[str] = None,
        category: Optional[str] = None,
        tpo: Optional[str] = None,
        formality_min: Optional[int] = None,
        formality_max: Optional[int] = None,
        size: Optional[str] = None,
        max_price: Optional[int] = None,
        limit: int = 5,
    ) -> tuple[list[Product], int]:
        """필터를 적용해 (limit 적용된 items, 전체 매칭 수)를 반환."""
        results = self.all()

        if q:
            needle = q.strip().lower()
            results = [
                p
                for p in results
                if needle in p.name.lower()
                or needle in p.style_note.lower()
                or needle in p.review_summary.lower()
            ]

        if category:
            results = [p for p in results if p.category.value == category]

        if tpo:
            results = [p for p in results if tpo in p.tags.tpo]

        if formality_min is not None:
            results = [p for p in results if p.tags.formality >= formality_min]

        if formality_max is not None:
            results = [p for p in results if p.tags.formality <= formality_max]

        if size:
            # 해당 사이즈에 재고가 있는 상품만 (API명세서 §5.C)
            results = [p for p in results if p.has_size_in_stock(size)]

        if max_price is not None:
            results = [p for p in results if p.price <= max_price]

        total = len(results)
        if limit is not None and limit >= 0:
            results = results[:limit]
        return results, total


# 프로세스 전역 싱글턴 (인메모리 저장소)
catalog_service = CatalogService()
