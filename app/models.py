"""Pydantic 데이터 모델 (API명세서 §3 기준).

- JSON은 camelCase (alias_generator=to_camel).
- 개인정보 필드는 스키마에 존재하지 않는다 (NFR-S1, 익명 세션).
- SHOW_PRODUCTS는 카탈로그 그라운딩 검증기를 붙인다 (설계 원칙 2, FR-C3):
  카탈로그에 없는 productId 또는 빈 reason이면 ValidationError.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """전 모델 공통 베이스 — camelCase 직렬화 + snake/camel 양쪽 입력 허용."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


# =========================================================================
# Enums
# =========================================================================
class SessionState(str, Enum):
    CREATED = "CREATED"
    BOUND = "BOUND"
    CLOSED = "CLOSED"
    TRANSFERRED = "TRANSFERRED"


class ProductCategory(str, Enum):
    TOP = "top"
    BOTTOM = "bottom"
    OUTER = "outer"
    SHIRT = "shirt"
    ACC = "acc"


class DeliveryStatus(str, Enum):
    REQUESTED = "REQUESTED"
    PICKING = "PICKING"
    ON_RAIL = "ON_RAIL"
    ENTERING = "ENTERING"
    SEALED = "SEALED"
    STAGING = "STAGING"
    READY = "READY"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ServiceDoor(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"


class CustomerDoor(str, Enum):
    LOCKED = "LOCKED"
    UNLOCKED = "UNLOCKED"
    OPEN = "OPEN"


class Light(str, Enum):
    OFF = "OFF"
    AMBER = "AMBER"
    WHITE = "WHITE"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


# =========================================================================
# Product — 목업 카탈로그 항목 (API명세서 §3.2)
# =========================================================================
class SizeStock(CamelModel):
    size: str
    stock: int = Field(ge=0)


class ProductTags(CamelModel):
    tpo: list[str] = Field(default_factory=list)
    formality: int = Field(ge=1, le=5)
    fit: str
    tone: list[str] = Field(default_factory=list)


class Availability(CamelModel):
    store: bool = True
    online: bool = True


class Product(CamelModel):
    product_id: str
    name: str
    category: ProductCategory
    color: str
    price: int = Field(ge=0)  # KRW 정수
    sizes: list[SizeStock] = Field(default_factory=list)
    tags: ProductTags
    style_note: str = ""
    review_summary: str = ""
    availability: Availability = Field(default_factory=Availability)
    image_url: str = ""

    def stock_for(self, size: str) -> int:
        for s in self.sizes:
            if s.size == size:
                return s.stock
        return 0

    def has_size_in_stock(self, size: str) -> bool:
        return self.stock_for(size) > 0


# =========================================================================
# Session — 익명 세션 (API명세서 §3.1)
# =========================================================================
class Keep(CamelModel):
    product_id: str
    size: str
    kept_at: str  # ISO8601 + KST


class Profile(CamelModel):
    # 확정 사이즈: {"outer": "L"} 처럼 카테고리→사이즈
    confirmed_sizes: dict[str, str] = Field(default_factory=dict)
    preferences: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class Session(CamelModel):
    session_id: str
    state: SessionState = SessionState.CREATED
    room_id: Optional[str] = None
    created_at: str  # ISO8601 + KST
    profile: Profile = Field(default_factory=Profile)
    keeps: list[Keep] = Field(default_factory=list)
    active_delivery_id: Optional[str] = None
    scanned_product_ids: list[str] = Field(default_factory=list)


# =========================================================================
# Delivery — 딜리버리 (API명세서 §3.3, 상태머신 소유)
# =========================================================================
class DeliveryHistoryEntry(CamelModel):
    status: DeliveryStatus
    at: str  # ISO8601 + KST


class Delivery(CamelModel):
    delivery_id: str
    session_id: str
    room_id: str
    product_id: str
    size: str
    status: DeliveryStatus = DeliveryStatus.REQUESTED
    requested_at: str
    history: list[DeliveryHistoryEntry] = Field(default_factory=list)


# =========================================================================
# WardrobeState — 워드로브 (API명세서 §3.4)
# =========================================================================
class WardrobeState(CamelModel):
    room_id: str
    service_door: ServiceDoor = ServiceDoor.CLOSED
    customer_door: CustomerDoor = CustomerDoor.LOCKED
    light: Light = Light.OFF
    current_delivery_id: Optional[str] = None


# =========================================================================
# HandoffTicket — 오프→온 전송, 스냅샷 동결 (API명세서 §3.6)
# =========================================================================
class HandoffKeepItem(CamelModel):
    product_id: str
    name: str
    size: str
    price: int
    tried: bool = False


class HandoffSnapshot(CamelModel):
    store_name: str
    confirmed_sizes: dict[str, str] = Field(default_factory=dict)
    keeps: list[HandoffKeepItem] = Field(default_factory=list)
    orders: list[dict[str, Any]] = Field(default_factory=list)


class HandoffTicket(CamelModel):
    code: str
    qr_url: str
    expires_at: str
    snapshot: HandoffSnapshot


# =========================================================================
# Message & UiAction — 대화 응답의 두 축 (API명세서 §3.5)
# =========================================================================
class Message(CamelModel):
    id: str
    role: MessageRole
    text: str
    created_at: str


class ProductRecommendation(CamelModel):
    """SHOW_PRODUCTS 카드 한 장 — reason 필수, 카탈로그 실존 productId만."""

    product_id: str
    size: str
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("추천 카드의 reason은 비어 있을 수 없어요.")
        return v

    @field_validator("product_id")
    @classmethod
    def _product_must_exist(cls, v: str) -> str:
        # 카탈로그 그라운딩 강제 — 지연 임포트로 순환 참조 회피.
        from app.services.catalog import catalog_service

        if not catalog_service.exists(v):
            raise ValueError(f"카탈로그에 없는 productId예요: {v}")
        return v


class ShowProducts(CamelModel):
    type: Literal["SHOW_PRODUCTS"] = "SHOW_PRODUCTS"
    items: list[ProductRecommendation]


class ShowChips(CamelModel):
    type: Literal["SHOW_CHIPS"] = "SHOW_CHIPS"
    chips: list[str]


class ShowDeliveryStatus(CamelModel):
    type: Literal["SHOW_DELIVERY_STATUS"] = "SHOW_DELIVERY_STATUS"
    delivery_id: str


class ShowKeepList(CamelModel):
    type: Literal["SHOW_KEEP_LIST"] = "SHOW_KEEP_LIST"
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class ShowCheckoutResult(CamelModel):
    type: Literal["SHOW_CHECKOUT_RESULT"] = "SHOW_CHECKOUT_RESULT"
    order_id: str
    total: int


class ShowHandoffQr(CamelModel):
    type: Literal["SHOW_HANDOFF_QR"] = "SHOW_HANDOFF_QR"
    qr_url: str
    expires_at: str


UiAction = Annotated[
    Union[
        ShowProducts,
        ShowChips,
        ShowDeliveryStatus,
        ShowKeepList,
        ShowCheckoutResult,
        ShowHandoffQr,
    ],
    Field(discriminator="type"),
]


# =========================================================================
# Catalog 검색 응답 (API명세서 §5.C)
# =========================================================================
class CatalogSearchResult(CamelModel):
    items: list[Product]
    total: int


# =========================================================================
# 요청/응답 스키마 (STEP 2 — 세션·딜리버리·킵)
# =========================================================================
class SessionCreateResponse(CamelModel):
    session_id: str
    state: SessionState
    created_at: str


class BindRequest(CamelModel):
    room_id: str


class WelcomeMessage(CamelModel):
    role: MessageRole = MessageRole.ASSISTANT
    text: str


class Welcome(CamelModel):
    message: WelcomeMessage
    ui_actions: list[UiAction] = Field(default_factory=list)


class BindResponse(CamelModel):
    session_id: str
    state: SessionState
    room_id: str
    welcome: Welcome


class CloseRequest(CamelModel):
    transfer: bool = False


class CloseResponse(CamelModel):
    session_id: str
    state: SessionState
    purged: bool


class DeliveryCreateRequest(CamelModel):
    product_id: str
    size: str


class DeliveryCreateResponse(CamelModel):
    delivery_id: str
    status: DeliveryStatus
    eta_seconds: int


class KeepCreateRequest(CamelModel):
    product_id: str
    size: str


class KeepsResponse(CamelModel):
    keeps: list[Keep]
    total: int


# =========================================================================
# 대화 (STEP 3 — 컨시어지)
# =========================================================================
class MessageCreateRequest(CamelModel):
    text: str


class MessagesResponse(CamelModel):
    """POST /messages 응답 — 이번 턴의 어시스턴트 메시지 + uiActions."""

    messages: list[Message]
    ui_actions: list[UiAction] = Field(default_factory=list)


class MessagesHistoryResponse(CamelModel):
    """GET /messages 응답 — 대화 이력."""

    messages: list[Message]


# =========================================================================
# 요청/응답 스키마 (STEP 4 — 핸드오프·결제·프로필·스캔·데모)
# =========================================================================
class CheckoutItem(CamelModel):
    product_id: str
    size: str


class CheckoutRequest(CamelModel):
    items: list[CheckoutItem] = Field(default_factory=list)
    method: str = "MIRROR"


class CheckoutResponse(CamelModel):
    order_id: str
    status: str
    total: int
    message: str


class ProfilePatchRequest(CamelModel):
    confirmed_sizes: Optional[dict[str, str]] = None
    preferences: Optional[list[str]] = None
    context: Optional[dict[str, Any]] = None


class ScanRequest(CamelModel):
    product_id: str


class ScanResponse(CamelModel):
    product: Product
    scanned_product_ids: list[str]


class DeliveryConfigRequest(CamelModel):
    # 단계별 소요(ms). 키는 상태명(PICKING/ON_RAIL/…), 값은 밀리초.
    delivery_step_ms: Optional[dict[str, int]] = None
    auto_advance: Optional[bool] = None
