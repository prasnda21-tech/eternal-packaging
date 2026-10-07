"""Explicit unauthenticated HTTP schemas for public Customer Pricing / Catalog."""
from __future__ import annotations

from typing import List, Optional

from fastapi import Response
from fastapi.responses import Response as BinaryResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class PublicSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicCategory(PublicSchema):
    id: str
    name: str
    slug: str = ""
    display_order: int = 0
    image_url: Optional[str] = None


class PublicCatalogSettings(PublicSchema):
    catalog_name: str = ""
    hero_title: str = ""
    hero_subtitle: str = ""
    public_whatsapp_phone: str = ""
    catalog_enabled: bool = False


class PublicCategoryRef(PublicSchema):
    id: str
    name: str
    slug: str = ""


class PublicVariantSummary(PublicSchema):
    public_variant_id: str
    display_name: str
    unit: Optional[str] = None
    image_url: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None
    finishing: Optional[str] = None


class PublicGroupSummary(PublicSchema):
    public_id: str
    slug: Optional[str] = None
    name: str
    public_description: Optional[str] = Field(default=None, max_length=280)
    image_url: Optional[str] = None
    category: Optional[PublicCategoryRef] = None
    display_order: Optional[int] = None
    min_unit_price: Optional[float] = None
    variant_count: Optional[int] = None
    size_summary: Optional[str] = None
    search_text: Optional[str] = None
    variants: List[PublicVariantSummary]


class PublicGroupsResponse(PublicSchema):
    groups: List[PublicGroupSummary]


class PublicCatalogHomeResponse(PublicSchema):
    settings: PublicCatalogSettings
    categories: List[PublicCategory]
    groups: List[PublicGroupSummary]
    message: Optional[str] = None


class PublicTier(PublicSchema):
    min_qty: int
    max_qty: Optional[int] = None
    unit_price: float


class PublicGalleryImage(PublicSchema):
    id: str
    role: str
    role_label: str
    display_order: int = 0
    is_default: bool = False
    url: str


class PublicVariantDetail(PublicVariantSummary):
    minimum_quantity: int
    tiers: List[PublicTier]
    display_order: Optional[int] = None
    images: List[PublicGalleryImage] = []


class PublicContact(PublicSchema):
    public_whatsapp_phone: str = ""


class PublicGroupDetail(PublicSchema):
    public_id: str
    slug: Optional[str] = None
    name: str
    public_description: Optional[str] = Field(default=None, max_length=280)
    image_url: Optional[str] = None
    category: Optional[PublicCategoryRef] = None
    display_order: Optional[int] = None
    min_unit_price: Optional[float] = None
    variant_count: Optional[int] = None
    size_summary: Optional[str] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    variants: List[PublicVariantDetail]
    contact: Optional[PublicContact] = None


class PublicGroupResponse(PublicSchema):
    group: PublicGroupDetail


class PublicCalculateRequest(PublicSchema):
    group_public_id: StrictStr = Field(min_length=1, max_length=128)
    public_variant_id: StrictStr = Field(min_length=1, max_length=128)
    quantity: StrictInt


class PublicCalculatedGroup(PublicSchema):
    public_id: str
    name: str


class PublicCalculatedVariant(PublicVariantSummary):
    pass


class PublicSelectedTier(PublicSchema):
    min_qty: int
    max_qty: Optional[int] = None


class PublicCalculateResponse(PublicSchema):
    group: PublicCalculatedGroup
    variant: PublicCalculatedVariant
    quantity: int
    selected_tier: PublicSelectedTier
    unit_price: float
    total: float
    currency: str
    calculated_at: str


def register_public_customer_pricing_routes(api_router, *, get_db):
    from services import customer_pricing_public as public_pricing

    def no_store(response) -> None:
        response.headers["Cache-Control"] = "no-store"

    @api_router.get(
        "/public/catalog",
        response_model=PublicCatalogHomeResponse,
    )
    async def get_public_catalog_home(response: Response):
        no_store(response)
        return await public_pricing.get_public_catalog_home(get_db())

    @api_router.get(
        "/public/catalog/categories/{category_id}/image",
        response_class=BinaryResponse,
    )
    async def get_public_catalog_category_image(category_id: str):
        data, content_type, _filename = await public_pricing.get_public_category_image(
            get_db(), category_id
        )
        return BinaryResponse(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=300",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api_router.get(
        "/public/customer-pricing/groups",
        response_model=PublicGroupsResponse,
    )
    async def list_public_customer_pricing_groups(response: Response):
        no_store(response)
        return {"groups": await public_pricing.list_public_groups(get_db())}

    @api_router.get(
        "/public/customer-pricing/groups/{group_public_id}/image",
        response_class=BinaryResponse,
    )
    async def get_public_customer_pricing_group_image(group_public_id: str):
        data, content_type, _filename = (
            await public_pricing.get_public_group_image(
                get_db(), group_public_id
            )
        )
        return BinaryResponse(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=300",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api_router.get(
        "/public/customer-pricing/groups/{group_public_id}/variants/{public_variant_id}/image",
        response_class=BinaryResponse,
    )
    async def get_public_customer_pricing_variant_image(
        group_public_id: str, public_variant_id: str
    ):
        data, content_type, _filename = (
            await public_pricing.get_public_variant_image(
                get_db(), group_public_id, public_variant_id
            )
        )
        return BinaryResponse(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=300",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api_router.get(
        "/public/customer-pricing/groups/{group_public_id}/variants/{public_variant_id}/gallery/{image_id}/image",
        response_class=BinaryResponse,
    )
    async def get_public_customer_pricing_gallery_image(
        group_public_id: str, public_variant_id: str, image_id: str
    ):
        data, content_type, _filename = (
            await public_pricing.get_public_gallery_image(
                get_db(), group_public_id, public_variant_id, image_id
            )
        )
        return BinaryResponse(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=300",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api_router.get(
        "/public/customer-pricing/groups/by-slug/{slug}",
        response_model=PublicGroupResponse,
    )
    async def get_public_customer_pricing_group_by_slug(
        slug: str, response: Response
    ):
        no_store(response)
        return {
            "group": await public_pricing.get_public_group_by_slug(get_db(), slug)
        }

    @api_router.get(
        "/public/customer-pricing/groups/{group_public_id}",
        response_model=PublicGroupResponse,
    )
    async def get_public_customer_pricing_group(
        group_public_id: str, response: Response
    ):
        no_store(response)
        return {
            "group": await public_pricing.get_public_group(
                get_db(), group_public_id
            )
        }

    @api_router.post(
        "/public/customer-pricing/calculate",
        response_model=PublicCalculateResponse,
    )
    async def calculate_public_customer_price(
        payload: PublicCalculateRequest, response: Response
    ):
        no_store(response)
        return await public_pricing.calculate_public_price(
            get_db(),
            group_public_id=payload.group_public_id,
            public_variant_id=payload.public_variant_id,
            quantity=payload.quantity,
        )
