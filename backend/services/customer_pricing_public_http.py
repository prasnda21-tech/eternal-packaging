"""Explicit unauthenticated HTTP schemas for public Customer Pricing."""
from __future__ import annotations

from typing import List, Optional

from fastapi import Response
from fastapi.responses import Response as BinaryResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class PublicSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicVariantSummary(PublicSchema):
    public_variant_id: str
    display_name: str
    unit: Optional[str] = None
    image_url: Optional[str] = None


class PublicGroupSummary(PublicSchema):
    public_id: str
    name: str
    public_description: Optional[str] = Field(default=None, max_length=280)
    image_url: Optional[str] = None
    variants: List[PublicVariantSummary]


class PublicGroupsResponse(PublicSchema):
    groups: List[PublicGroupSummary]


class PublicTier(PublicSchema):
    min_qty: int
    max_qty: Optional[int] = None
    unit_price: float


class PublicVariantDetail(PublicVariantSummary):
    minimum_quantity: int
    tiers: List[PublicTier]


class PublicGroupDetail(PublicSchema):
    public_id: str
    name: str
    public_description: Optional[str] = Field(default=None, max_length=280)
    image_url: Optional[str] = None
    variants: List[PublicVariantDetail]


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
