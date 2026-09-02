"""HTTP routes for the independent Route Optimizer."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

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


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


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
