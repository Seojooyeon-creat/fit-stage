"""카탈로그 라우트 (API명세서 §5.C) — 이번 단계 구현 대상.

GET /api/v1/catalog/products         상품 검색
GET /api/v1/catalog/products/{id}    단건 조회
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.errors import product_not_found
from app.models import CatalogSearchResult, Product
from app.services.catalog import catalog_service

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/products", response_model=CatalogSearchResult)
def search_products(
    q: Optional[str] = Query(None, description="이름·설명 키워드"),
    category: Optional[str] = Query(None, description="top|bottom|outer|shirt|acc"),
    tpo: Optional[str] = Query(None, description="TPO 태그 (예: interview)"),
    formality_min: Optional[int] = Query(
        None, alias="formalityMin", ge=1, le=5, description="격식 하한"
    ),
    formality_max: Optional[int] = Query(
        None, alias="formalityMax", ge=1, le=5, description="격식 상한"
    ),
    size: Optional[str] = Query(None, description="해당 사이즈 재고 있는 상품만"),
    max_price: Optional[int] = Query(
        None, alias="maxPrice", ge=0, description="가격 상한(KRW)"
    ),
    limit: int = Query(5, ge=0, le=100, description="반환 개수 (기본 5)"),
) -> CatalogSearchResult:
    items, total = catalog_service.search(
        q=q,
        category=category,
        tpo=tpo,
        formality_min=formality_min,
        formality_max=formality_max,
        size=size,
        max_price=max_price,
        limit=limit,
    )
    return CatalogSearchResult(items=items, total=total)


@router.get("/products/{product_id}", response_model=Product)
def get_product(product_id: str) -> Product:
    product = catalog_service.get(product_id)
    if product is None:
        raise product_not_found()
    return product
