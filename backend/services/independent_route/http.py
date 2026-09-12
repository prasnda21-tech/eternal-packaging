"""HTTP routes for the independent Route Optimizer."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.independent_route.constants import (
    PERM_CREATE,
    PERM_EDIT_OWN,
    PERM_MANAGE,
    PERM_NAV,
    PERM_OPTIMIZE,
    PERM_SETTINGS_MANAGE,
    PERM_SETTINGS_VIEW,
    PERM_VIEW,
)
from services.independent_route import places as places_svc
from services.independent_route import settings as settings_svc
from services.independent_route import store
from services.independent_route import delivery_bridge


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


class ScheduleReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_ids: List[str] = Field(default_factory=list)


class SchedulePreviewFleetsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_ids: List[str] = Field(default_factory=list)
    fleet_count: int


class ScheduleOptimizePayload(BaseModel):
    """Create+optimize from schedule.

    Legacy clients: `{ order_ids }` only → isolated driver-grouped path.
    New UI: `{ order_ids, fleet_count: 1|2 }` → driverless 1/2-armada create.
    """
    model_config = ConfigDict(extra="allow")
    order_ids: List[str] = Field(default_factory=list)
    fleet_count: Optional[int] = None


def _actor(user: dict) -> dict:
    return store.actor_from_user(user)


def register_independent_route_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    require_any_perm,
    user_permissions,
    Depends,
):
    async def _perms(user) -> set:
        return await user_permissions(user)

    async def _require_view(user):
        await require_any_perm(user, PERM_VIEW, PERM_CREATE, PERM_EDIT_OWN, PERM_OPTIMIZE, PERM_MANAGE, PERM_NAV)

    @api_router.get("/route-optimizer/settings")
    async def get_settings(user=Depends(current_user)):
        await require_any_perm(user, PERM_SETTINGS_VIEW, PERM_SETTINGS_MANAGE, PERM_VIEW, PERM_MANAGE, PERM_OPTIMIZE)
        return {"settings": await settings_svc.load_settings(get_db())}

    @api_router.put("/route-optimizer/settings")
    async def put_settings(payload: LoosePayload, user=Depends(current_user)):
        await require_perm(user, PERM_SETTINGS_MANAGE)
        cfg = await settings_svc.save_settings(get_db(), payload.model_dump(), _actor(user))
        return {"settings": cfg}

    @api_router.get("/route-optimizer/outlets/search")
    async def search_outlets(q: str = "", user=Depends(current_user)):
        await _require_view(user)
        return {"candidates": await store.search_stop_candidates(get_db(), q)}

    @api_router.get("/route-optimizer/places/status")
    async def places_status(user=Depends(current_user)):
        await _require_view(user)
        status = places_svc.places_status()
        if not status["configured"]:
            status["message"] = "Google Maps belum dikonfigurasi."
            status["required_config"] = "GOOGLE_PLACES_API_KEY"
            status["required_config_fallback"] = "GOOGLE_MAPS_API_KEY"
        return status

    @api_router.get("/route-optimizer/places/search")
    async def places_search(q: str = "", user=Depends(current_user)):
        await _require_view(user)
        result = places_svc.search_places(q)
        if not result.get("configured"):
            result["required_config"] = "GOOGLE_PLACES_API_KEY"
            result["required_config_fallback"] = "GOOGLE_MAPS_API_KEY"
        return result

    @api_router.get("/route-optimizer/places/details")
    async def places_details(place_id: str = "", user=Depends(current_user)):
        await _require_view(user)
        return places_svc.place_details(place_id)

    @api_router.get("/route-optimizer/routes")
    async def list_routes(user=Depends(current_user)):
        await _require_view(user)
        return {"routes": await store.list_routes(get_db(), _actor(user), await _perms(user))}

    @api_router.post("/route-optimizer/routes")
    async def create_route(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_CREATE, PERM_MANAGE)
        return {"route": await store.create_route(get_db(), payload.model_dump(), _actor(user))}

    @api_router.get("/route-optimizer/routes/{route_id}")
    async def get_route(route_id: str, user=Depends(current_user)):
        await _require_view(user)
        return {"route": await store.get_route(get_db(), route_id, _actor(user), await _perms(user))}

    @api_router.patch("/route-optimizer/routes/{route_id}")
    async def patch_route(route_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_EDIT_OWN, PERM_MANAGE)
        return {"route": await store.update_route(get_db(), route_id, payload.model_dump(), _actor(user), await _perms(user))}

    @api_router.delete("/route-optimizer/routes/{route_id}")
    async def delete_route(route_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_EDIT_OWN, PERM_MANAGE)
        return await store.soft_delete_route(get_db(), route_id, _actor(user), await _perms(user))

    @api_router.post("/route-optimizer/routes/{route_id}/stops")
    async def add_stop(route_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_EDIT_OWN, PERM_CREATE, PERM_MANAGE)
        return await store.add_stop(get_db(), route_id, payload.model_dump(), _actor(user), await _perms(user))

    @api_router.patch("/route-optimizer/routes/{route_id}/stops/{stop_id}")
    async def patch_stop(route_id: str, stop_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_EDIT_OWN, PERM_MANAGE)
        return await store.update_stop(get_db(), route_id, stop_id, payload.model_dump(), _actor(user), await _perms(user))

    @api_router.delete("/route-optimizer/routes/{route_id}/stops/{stop_id}")
    async def remove_stop(route_id: str, stop_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_EDIT_OWN, PERM_MANAGE)
        return await store.delete_stop(get_db(), route_id, stop_id, _actor(user), await _perms(user))

    @api_router.post("/route-optimizer/routes/{route_id}/optimize")
    async def optimize_route(route_id: str, payload: Optional[LoosePayload] = None, user=Depends(current_user)):
        await require_perm(user, PERM_OPTIMIZE)
        body = payload.model_dump() if payload else {}
        return await store.optimize(get_db(), route_id, body, _actor(user), await _perms(user))

    @api_router.post("/route-optimizer/routes/{route_id}/reorder")
    async def reorder_route(route_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_EDIT_OWN, PERM_MANAGE)
        return await store.reorder_stops(get_db(), route_id, payload.model_dump(), _actor(user), await _perms(user))

    @api_router.get("/route-optimizer/routes/{route_id}/versions")
    async def route_versions(route_id: str, user=Depends(current_user)):
        await _require_view(user)
        return {"versions": await store.list_versions(get_db(), route_id, _actor(user), await _perms(user))}

    # ---- Delivery schedule bridge (read review / mutate optimize) ----

    @api_router.get("/route-optimizer/from-schedule/review")
    async def schedule_review_get(ids: str = "", user=Depends(current_user)):
        """Pure read review for exact order IDs (comma-separated). ZERO writes."""
        await require_any_perm(user, PERM_VIEW, PERM_CREATE, PERM_OPTIMIZE, PERM_MANAGE, PERM_NAV)
        order_ids = [p.strip() for p in str(ids or "").split(",") if p.strip()]
        return await delivery_bridge.build_review_rows(get_db(), order_ids)

    @api_router.post("/route-optimizer/from-schedule/review")
    async def schedule_review_post(payload: ScheduleReviewPayload, user=Depends(current_user)):
        """Pure read review via POST body for large ID lists. ZERO writes."""
        await require_any_perm(user, PERM_VIEW, PERM_CREATE, PERM_OPTIMIZE, PERM_MANAGE, PERM_NAV)
        return await delivery_bridge.build_review_rows(get_db(), list(payload.order_ids or []))

    @api_router.post("/route-optimizer/from-schedule/preview-fleets")
    async def schedule_preview_fleets(payload: SchedulePreviewFleetsPayload, user=Depends(current_user)):
        """Non-persistent 1/2-armada optimization preview. ZERO route writes."""
        await require_any_perm(user, PERM_VIEW, PERM_CREATE, PERM_OPTIMIZE, PERM_MANAGE, PERM_NAV)
        return await delivery_bridge.preview_fleets_from_schedule(
            get_db(),
            list(payload.order_ids or []),
            fleet_count=payload.fleet_count,
        )

    @api_router.post("/route-optimizer/from-schedule/optimize")
    async def schedule_optimize(payload: ScheduleOptimizePayload, user=Depends(current_user)):
        """One-stage create+optimize from Jadwal Pengiriman.

        When fleet_count is 1 or 2: driverless persist of exactly that many routes.
        When fleet_count is omitted: legacy isolated path for cached/old clients.
        """
        await require_any_perm(user, PERM_CREATE, PERM_MANAGE)
        await require_perm(user, PERM_OPTIMIZE)
        if payload.fleet_count is not None:
            return await delivery_bridge.create_fleets_from_schedule(
                get_db(),
                list(payload.order_ids or []),
                fleet_count=payload.fleet_count,
                actor=_actor(user),
                perms=await _perms(user),
            )
        return await delivery_bridge.create_and_optimize_from_schedule_legacy(
            get_db(),
            order_ids=list(payload.order_ids or []),
            actor=_actor(user),
            perms=await _perms(user),
        )

    @api_router.get("/route-optimizer/from-schedule/status")
    async def schedule_opt_status(ids: str = "", user=Depends(current_user)):
        """Pure read optimization status map. ZERO writes."""
        await require_any_perm(
            user,
            PERM_VIEW,
            PERM_CREATE,
            PERM_OPTIMIZE,
            PERM_MANAGE,
            PERM_NAV,
            "delivery.schedule.view",
            "nav.delivery_schedule",
            "schedule.view",
        )
        order_ids = [p.strip() for p in str(ids or "").split(",") if p.strip()]
        status_map = await delivery_bridge.optimization_status_map(get_db(), order_ids)
        return {"statuses": status_map}
