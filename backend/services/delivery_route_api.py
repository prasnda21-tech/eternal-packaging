"""FastAPI routes for delivery batches, vehicles, depot, and route plans.

Registered onto the existing /api router. Authorization is permission-based.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.delivery_batches import (
    STATUS_CANCELLED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    actor_id,
    actor_name,
    apply_stop_updates,
    build_batch,
    public_batch,
)
from services.delivery_depot import SETTINGS_KEY as DEPOT_KEY
from services.delivery_depot import depot_from_settings_doc, normalize_depot
from services.delivery_routes import (
    ROUTE_BERJALAN,
    ROUTE_DRAFT,
    ROUTE_TERJADWAL,
    assignment_for_driver,
    mark_stop_delivered,
    mark_stop_rescheduled,
    public_plan,
)
from services.delivery_stops import (
    MONGO_QUEUE_FILTER,
    QUEUE_PROJECTION,
    build_queue_stops,
    occupied_stop_keys,
    parse_location,
)
from services.delivery_outlets import (
    COLLECTION as OUTLETS_COLLECTION,
    apply_location_to_stops,
    build_outlet_doc,
    customer_key,
    order_set_from_outlet,
    outlet_key,
    plan_is_historical,
    public_outlet,
)
from services.delivery_vehicles import normalize_vehicle, public_vehicle
from services.places_provider.base import PlacesNotConfigured, PlacesProviderError
from services.places_provider.factory import get_places_provider


ROUTE_OPTIMIZATION_INACTIVE = "Route optimization bukan lagi workflow aktif. Data historis tetap tersimpan."
MY_ROUTE_INACTIVE = "Rute Saya bukan lagi workflow aktif."

_hooks: dict = {}


class CreateBatchPayload(BaseModel):
    delivery_date: str
    stop_ids: List[str]
    notes: Optional[str] = None
    stops: Optional[List[dict]] = None


class PatchBatchPayload(BaseModel):
    delivery_date: Optional[str] = None
    notes: Optional[str] = None
    stops: Optional[List[dict]] = None


class ReadyPayload(BaseModel):
    vehicle_id: Optional[str] = None
    vehicle: Optional[dict] = None
    driver_user_id: Optional[str] = None  # ignored unless dispatcher + real user


class ReadyForPayload(BaseModel):
    driver_user_id: str
    vehicle_id: Optional[str] = None
    vehicle: Optional[dict] = None


class EditPlanPayload(BaseModel):
    moves: Optional[List[dict]] = None
    reorder: Optional[List[dict]] = None


class DepotPayload(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    place_id: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class VehiclePayload(BaseModel):
    name: Optional[str] = None
    plate: Optional[str] = None
    capacity_dus: Optional[int] = None
    active: Optional[bool] = None


class SaveOutletPayload(BaseModel):
    order_id: str
    batch_id: Optional[str] = None
    stop_id: Optional[str] = None
    name: Optional[str] = None
    place_id: Optional[str] = None
    google_place_id: Optional[str] = None
    formatted_address: Optional[str] = None
    lat: float
    lng: float


class PatchOutletPayload(BaseModel):
    name: Optional[str] = None
    place_id: Optional[str] = None
    google_place_id: Optional[str] = None
    formatted_address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


def register(api_router: APIRouter, deps: dict) -> None:
    db = deps["db"]
    current_user = deps["current_user"]
    require_perm = deps["require_perm"]
    require_any_perm = deps["require_any_perm"]
    user_permissions = deps["user_permissions"]
    Depends = deps["Depends"]

    async def _eff(user: dict):
        return await user_permissions(user)

    def _uid(user: dict) -> str:
        return actor_id(user)

    async def _can(user: dict, *perms: str) -> bool:
        if user.get("role") == "admin":
            return True
        eff = await _eff(user)
        return any(p in eff for p in perms)

    async def _occupied() -> set:
        batches = await db.delivery_batches.find(
            {"status": {"$in": [STATUS_DRAFT, STATUS_PUBLISHED]}},
            {"stops.stop_id": 1, "status": 1, "_id": 0},
        ).to_list(1000)
        keys = occupied_stop_keys(batches)
        plans = await db.delivery_route_plans.find(
            {"status": {"$in": [ROUTE_DRAFT, ROUTE_TERJADWAL, ROUTE_BERJALAN]}},
            {"routes.stops.stop_id": 1, "unassigned.stop_id": 1, "_id": 0},
        ).to_list(500)
        for p in plans:
            for r in p.get("routes") or []:
                for s in r.get("stops") or []:
                    if s.get("stop_id"):
                        keys.add(str(s["stop_id"]))
            for s in p.get("unassigned") or []:
                if s.get("stop_id") and s.get("status") not in ("rescheduled", "removed"):
                    keys.add(str(s["stop_id"]))
        return keys

    async def _queue_rows():
        docs = await db.orders.find(MONGO_QUEUE_FILTER, QUEUE_PROJECTION).to_list(2000)
        occ = await _occupied()
        outlet_ids = [str(d.get("delivery_outlet_id")).strip() for d in docs if d.get("delivery_outlet_id")]
        cust_names = list({
            str(d.get("customer_name") or d.get("customer") or "").strip()
            for d in docs
            if (d.get("customer_name") or d.get("customer"))
        })
        cust_keys = list({customer_key(n) for n in cust_names if n})
        query: Dict[str, Any] = {}
        clauses = []
        if outlet_ids:
            clauses.append({"id": {"$in": outlet_ids}})
        if cust_keys:
            clauses.append({"customer_key": {"$in": cust_keys}})
        if clauses:
            query = {"$or": clauses} if len(clauses) > 1 else clauses[0]
            outlets = await db[OUTLETS_COLLECTION].find(query, {"_id": 0}).to_list(2000)
        else:
            outlets = []
        return build_queue_stops(docs, occupied_keys=occ, outlets=outlets)

    async def _get_batch(batch_id: str) -> dict:
        doc = await db.delivery_batches.find_one({"id": batch_id}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "Batch pengiriman tidak ditemukan")
        return doc

    async def _get_plan(plan_id: str) -> dict:
        doc = await db.delivery_route_plans.find_one({"id": plan_id}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "Rencana rute tidak ditemukan")
        return doc

    async def _depot() -> dict:
        doc = await db.app_settings.find_one({"key": DEPOT_KEY}, {"_id": 0})
        return depot_from_settings_doc(doc)

    async def _vehicle_doc(vehicle_id: str) -> Optional[dict]:
        return await db.delivery_vehicles.find_one({"id": vehicle_id}, {"_id": 0})

    async def _lookup_user(uid: str) -> Optional[dict]:
        if not uid:
            return None
        return await db.users.find_one(
            {"$or": [{"id": uid}, {"email": uid}]},
            {"_id": 0, "password_hash": 0},
        )

    async def _persist_batch(doc: dict) -> dict:
        await db.delivery_batches.update_one({"id": doc["id"]}, {"$set": doc}, upsert=True)
        return doc

    async def _persist_plan(doc: dict) -> dict:
        await db.delivery_route_plans.update_one({"id": doc["id"]}, {"$set": doc}, upsert=True)
        return doc

    async def _require_location_edit(user: dict):
        await require_any_perm(user, "delivery.location.manage", "delivery.schedule.create")

    async def _apply_outlet_live(outlet: dict, *, order_id: Optional[str] = None, batch_id: Optional[str] = None) -> None:
        """Push master location to orders + draft batches/plans. Never rewrite confirmed routes."""
        oid = str(order_id or "").strip()
        fields = order_set_from_outlet(outlet)
        if oid:
            await db.orders.update_one({"id": oid}, {"$set": fields})
        # Reuse: same customer + same outlet name, or already linked.
        name = outlet.get("customer_name") or ""
        label = outlet.get("name") or ""
        reuse_filter: Dict[str, Any] = {
            "$or": [
                {"delivery_outlet_id": outlet.get("id")},
                {
                    "$and": [
                        {"$or": [{"customer": name}, {"customer_name": name}]},
                        {"outlet": label},
                    ]
                },
            ]
        }
        if oid:
            reuse_filter = {"$and": [{"id": {"$ne": oid}}, reuse_filter]}
        await db.orders.update_many(reuse_filter, {"$set": fields})

        batch_q: Dict[str, Any] = {"status": {"$in": [STATUS_DRAFT, STATUS_PUBLISHED]}}
        if batch_id:
            batch_q = {"id": batch_id, "status": {"$in": [STATUS_DRAFT, STATUS_PUBLISHED]}}
        batches = await db.delivery_batches.find(batch_q, {"_id": 0}).to_list(200)
        for batch in batches:
            if apply_location_to_stops(batch.get("stops") or [], order_id=oid or None, outlet=outlet):
                await _persist_batch(batch)
            plan_id = batch.get("route_plan_id")
            if not plan_id:
                continue
            plan = await db.delivery_route_plans.find_one({"id": plan_id}, {"_id": 0})
            if not plan or plan_is_historical(plan):
                continue
            changed = 0
            for r in plan.get("routes") or []:
                changed += apply_location_to_stops(r.get("stops") or [], order_id=oid or None, outlet=outlet)
            changed += apply_location_to_stops(plan.get("unassigned") or [], order_id=oid or None, outlet=outlet)
            if changed:
                await _persist_plan(plan)

    async def _upsert_outlet_for_order(order: dict, loc: dict, *, name: Optional[str], user: dict) -> dict:
        parsed = parse_location(loc)
        if parsed.get("lat") is None or parsed.get("lng") is None:
            raise HTTPException(400, "Lokasi wajib punya latitude dan longitude.")
        customer_name = str(order.get("customer_name") or order.get("customer") or "").strip()
        label = (name or loc.get("outlet_name") or order.get("outlet") or customer_name or "Utama").strip()
        existing = None
        existing_id = str(order.get("delivery_outlet_id") or loc.get("outlet_id") or "").strip()
        if existing_id:
            existing = await db[OUTLETS_COLLECTION].find_one({"id": existing_id}, {"_id": 0})
        if existing is None:
            existing = await db[OUTLETS_COLLECTION].find_one({
                "customer_key": customer_key(customer_name),
                "outlet_key": outlet_key(label),
            }, {"_id": 0})
        outlet = build_outlet_doc(
            existing=existing,
            customer_name=customer_name,
            customer_id=order.get("customer_id"),
            name=label,
            location={
                "lat": parsed.get("lat"),
                "lng": parsed.get("lng"),
                "place_id": parsed.get("place_id") or loc.get("google_place_id"),
                "formatted_address": parsed.get("formatted_address"),
            },
            user=user,
        )
        await db[OUTLETS_COLLECTION].update_one({"id": outlet["id"]}, {"$set": outlet}, upsert=True)
        return outlet

    async def _save_order_location(order_id: str, loc: dict, user: Optional[dict] = None) -> None:
        order = await db.orders.find_one({"id": order_id}, {"_id": 0, "id": 1, "customer": 1, "customer_name": 1, "customer_id": 1, "outlet": 1, "delivery_outlet_id": 1})
        if not order:
            return
        parsed = parse_location(loc)
        if parsed.get("lat") is None or parsed.get("lng") is None:
            return
        outlet = await _upsert_outlet_for_order(order, loc, name=loc.get("outlet_name"), user=user or {})
        await _apply_outlet_live(outlet, order_id=order_id)

    # ---------- Queue / batch ----------
    @api_router.get("/delivery/queue")
    async def delivery_queue(user=Depends(current_user)):
        await require_any_perm(user, "delivery.schedule.view", "delivery.schedule.create", "schedule.view")
        rows = await _queue_rows()
        return {"rows": rows, "total": len(rows)}

    @api_router.get("/delivery/places/search")
    async def places_search(q: str = Query("", min_length=0), user=Depends(current_user)):
        await _require_location_edit(user)
        query = (q or "").strip()
        if len(query) < 2:
            return {"provider": "none", "suggestions": []}
        try:
            provider = get_places_provider()
            result = provider.search(query)
        except PlacesNotConfigured as e:
            raise HTTPException(503, str(e))
        except PlacesProviderError as e:
            raise HTTPException(502, str(e))
        return {
            "provider": result.provider,
            "suggestions": [
                {"place_id": s.place_id, "label": s.label, "formatted_address": s.formatted_address}
                for s in result.suggestions
            ],
        }

    @api_router.get("/delivery/places/details")
    async def places_details(place_id: str = Query(...), user=Depends(current_user)):
        await _require_location_edit(user)
        pid = (place_id or "").strip()
        if not pid:
            raise HTTPException(400, "place_id wajib.")
        try:
            provider = get_places_provider()
            det = provider.details(pid)
        except PlacesNotConfigured as e:
            raise HTTPException(503, str(e))
        except PlacesProviderError as e:
            raise HTTPException(502, str(e))
        if not det:
            raise HTTPException(404, "Tempat tidak ditemukan")
        return {
            "place": {
                "place_id": det.place_id,
                "google_place_id": det.place_id,
                "name": det.name,
                "formatted_address": det.formatted_address,
                "lat": det.lat,
                "lng": det.lng,
            }
        }

    @api_router.get("/delivery/outlets")
    async def list_outlets(customer: Optional[str] = None, user=Depends(current_user)):
        await require_any_perm(
            user, "delivery.location.manage", "delivery.schedule.create", "delivery.schedule.view",
        )
        q: Dict[str, Any] = {}
        if customer:
            q["customer_key"] = customer_key(customer)
        docs = await db[OUTLETS_COLLECTION].find(q, {"_id": 0}).to_list(500)
        return {"outlets": [public_outlet(d) for d in docs]}

    @api_router.post("/delivery/outlets")
    async def save_outlet(payload: SaveOutletPayload, user=Depends(current_user)):
        await _require_location_edit(user)
        order = await db.orders.find_one(
            {"id": payload.order_id},
            {"_id": 0, "id": 1, "customer": 1, "customer_name": 1, "customer_id": 1, "outlet": 1, "delivery_outlet_id": 1, "address": 1},
        )
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")
        loc = {
            "lat": payload.lat,
            "lng": payload.lng,
            "place_id": payload.google_place_id or payload.place_id,
            "formatted_address": payload.formatted_address,
            "outlet_name": payload.name,
        }
        outlet = await _upsert_outlet_for_order(order, loc, name=payload.name, user=user)
        await _apply_outlet_live(outlet, order_id=payload.order_id, batch_id=payload.batch_id)
        return {"outlet": public_outlet(outlet)}

    @api_router.patch("/delivery/outlets/{outlet_id}")
    async def patch_outlet(outlet_id: str, payload: PatchOutletPayload, user=Depends(current_user)):
        await _require_location_edit(user)
        existing = await db[OUTLETS_COLLECTION].find_one({"id": outlet_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Lokasi outlet tidak ditemukan")
        data = payload.model_dump(exclude_unset=True)
        loc = {
            "lat": data.get("lat", existing.get("lat")),
            "lng": data.get("lng", existing.get("lng")),
            "place_id": data.get("google_place_id") or data.get("place_id") or existing.get("place_id") or existing.get("google_place_id"),
            "formatted_address": data.get("formatted_address", existing.get("formatted_address")),
        }
        try:
            outlet = build_outlet_doc(
                existing=existing,
                customer_name=existing.get("customer_name") or "",
                customer_id=existing.get("customer_id"),
                name=data.get("name") or existing.get("name") or "",
                location=loc,
                user=user,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        await db[OUTLETS_COLLECTION].update_one({"id": outlet_id}, {"$set": outlet})
        await _apply_outlet_live(outlet)
        return {"outlet": public_outlet(outlet)}

    @api_router.post("/delivery/batches")
    async def create_delivery_batch(payload: CreateBatchPayload, user=Depends(current_user)):
        await require_perm(user, "delivery.schedule.create")
        date_key = (payload.delivery_date or "").strip()[:10]
        if len(date_key) != 10:
            raise HTTPException(400, "Tanggal pengiriman wajib diisi (YYYY-MM-DD).")
        wanted = [str(s).strip() for s in (payload.stop_ids or []) if str(s).strip()]
        if not wanted:
            raise HTTPException(400, "Pilih minimal satu stop SIAP DIKIRIM.")
        queue = await _queue_rows()
        by_id = {r["stop_id"]: r for r in queue}
        missing = [sid for sid in wanted if sid not in by_id]
        if missing:
            raise HTTPException(400, f"Stop tidak ada di antrian SIAP DIKIRIM: {missing[0]}")
        selected = [by_id[sid] for sid in wanted]
        if any(r.get("occupied") for r in selected):
            raise HTTPException(400, "Stop sudah masuk batch aktif.")
        edits = {}
        for extra in payload.stops or []:
            sid = str((extra or {}).get("stop_id") or "")
            if sid:
                edits[sid] = extra
                if extra.get("location") and by_id.get(sid):
                    await _save_order_location(by_id[sid]["order_id"], extra.get("location"), user)
        batch = build_batch(
            delivery_date=date_key,
            queue_rows=selected,
            stop_edits=edits,
            user=user,
            notes=payload.notes or "",
        )
        await _persist_batch(batch)
        return {"batch": public_batch(batch)}

    @api_router.get("/delivery/batches")
    async def list_delivery_batches(user=Depends(current_user)):
        await require_any_perm(
            user,
            "delivery.schedule.view",
            "delivery.schedule.create",
            "delivery.update",
        )
        q: Dict[str, Any] = {"status": {"$ne": STATUS_CANCELLED}}
        is_dispatcher = await _can(
            user, "delivery.schedule.view", "delivery.schedule.create", "delivery.schedule.publish",
        )
        if not is_dispatcher:
            q["status"] = STATUS_PUBLISHED
        docs = await db.delivery_batches.find(q, {"_id": 0}).sort("created_at", -1).to_list(200)
        return {"rows": [public_batch(d) for d in docs], "total": len(docs)}

    @api_router.get("/delivery/batches/{batch_id}")
    async def get_delivery_batch(batch_id: str, user=Depends(current_user)):
        await require_any_perm(
            user,
            "delivery.schedule.view",
            "delivery.schedule.create",
            "delivery.update",
        )
        batch = await _get_batch(batch_id)
        if batch.get("status") != STATUS_PUBLISHED:
            if not await _can(user, "delivery.schedule.view", "delivery.schedule.create", "delivery.schedule.publish"):
                raise HTTPException(403, "Batch belum dipublish ke Driver.")
        plan = None
        if batch.get("route_plan_id"):
            plan = await db.delivery_route_plans.find_one({"id": batch["route_plan_id"]}, {"_id": 0})
        driver_only = not await _can(
            user, "delivery.schedule.view", "delivery.schedule.create",
        )
        return {
            "batch": public_batch(batch),
            "plan": public_plan(plan, driver_user_id=_uid(user) if driver_only and plan else None) if plan else None,
            "depot": await _depot(),
            "me": {"id": _uid(user), "name": actor_name(user)},
        }

    @api_router.patch("/delivery/batches/{batch_id}")
    async def patch_delivery_batch(batch_id: str, payload: PatchBatchPayload, user=Depends(current_user)):
        await require_any_perm(user, "delivery.schedule.create", "delivery.schedule.publish")
        batch = await _get_batch(batch_id)
        if batch.get("status") not in (STATUS_DRAFT, STATUS_PUBLISHED):
            raise HTTPException(400, "Batch ini tidak dapat diubah.")
        if payload.delivery_date:
            batch["delivery_date"] = payload.delivery_date.strip()[:10]
        if payload.notes is not None:
            batch["notes"] = payload.notes
        if payload.stops:
            apply_stop_updates(batch, payload.stops)
            for extra in payload.stops:
                if extra.get("location") and extra.get("stop_id"):
                    st = next((s for s in batch.get("stops") or [] if s.get("stop_id") == extra.get("stop_id")), None)
                    if st:
                        await _save_order_location(st.get("order_id"), extra.get("location"))
        await _persist_batch(batch)
        return {"batch": public_batch(batch)}

    @api_router.post("/delivery/batches/{batch_id}/publish")
    async def publish_delivery_batch(batch_id: str, user=Depends(current_user)):
        await require_any_perm(user, "delivery.schedule.create", "delivery.schedule.publish")
        raise HTTPException(400, ROUTE_OPTIMIZATION_INACTIVE)

    @api_router.post("/delivery/batches/{batch_id}/ready")
    async def driver_ready(batch_id: str, payload: ReadyPayload, user=Depends(current_user)):
        await require_any_perm(user, "delivery.update", "delivery.vehicle.manage")
        raise HTTPException(400, ROUTE_OPTIMIZATION_INACTIVE)

    @api_router.post("/delivery/batches/{batch_id}/optimize")
    async def optimize_batch(batch_id: str, user=Depends(current_user)):
        await require_any_perm(
            user, "delivery.schedule.view", "delivery.schedule.create", "delivery.update",
        )
        raise HTTPException(400, ROUTE_OPTIMIZATION_INACTIVE)

    @api_router.post("/delivery/batches/{batch_id}/optimize-remaining")
    async def optimize_remaining(batch_id: str, user=Depends(current_user)):
        await require_any_perm(
            user, "delivery.schedule.view", "delivery.schedule.create", "delivery.update",
        )
        raise HTTPException(400, ROUTE_OPTIMIZATION_INACTIVE)

    # ---------- Route plans (historical read; mutations inactive) ----------
    @api_router.get("/delivery/route-plans/{plan_id}")
    async def get_route_plan(plan_id: str, user=Depends(current_user)):
        await require_any_perm(
            user,
            "delivery.update",
            "delivery.schedule.view",
            "delivery.schedule.create",
        )
        plan = await _get_plan(plan_id)
        driver_only = not await _can(user, "delivery.schedule.view", "delivery.schedule.create")
        uid = _uid(user)
        if driver_only:
            if not assignment_for_driver(plan, uid):
                raise HTTPException(403, "Anda tidak memiliki rute ini.")
            return {"plan": public_plan(plan, driver_user_id=uid), "me": {"id": uid, "name": actor_name(user)}}
        return {"plan": public_plan(plan), "me": {"id": uid, "name": actor_name(user)}}

    @api_router.patch("/delivery/route-plans/{plan_id}")
    async def patch_route_plan(plan_id: str, payload: EditPlanPayload, user=Depends(current_user)):
        await require_any_perm(user, "delivery.schedule.create", "delivery.schedule.view")
        raise HTTPException(400, ROUTE_OPTIMIZATION_INACTIVE)

    @api_router.get("/delivery/route-plans/{plan_id}/confirm-preview")
    async def confirm_preview(plan_id: str, user=Depends(current_user)):
        await require_any_perm(user, "delivery.schedule.create", "delivery.schedule.view")
        raise HTTPException(400, ROUTE_OPTIMIZATION_INACTIVE)

    @api_router.post("/delivery/route-plans/{plan_id}/confirm")
    async def confirm_route_plan(plan_id: str, user=Depends(current_user)):
        await require_any_perm(user, "delivery.schedule.create", "delivery.schedule.view")
        raise HTTPException(400, ROUTE_OPTIMIZATION_INACTIVE)

    @api_router.post("/delivery/route-plans/{plan_id}/start")
    async def start_route_plan(plan_id: str, user=Depends(current_user)):
        await require_perm(user, "delivery.update")
        raise HTTPException(400, MY_ROUTE_INACTIVE)

    @api_router.get("/delivery/my-routes")
    async def my_routes(user=Depends(current_user)):
        await require_perm(user, "delivery.update")
        raise HTTPException(400, MY_ROUTE_INACTIVE)

    # ---------- Vehicles / depot / drivers ----------
    @api_router.get("/delivery/vehicles")
    async def list_vehicles(user=Depends(current_user)):
        await require_any_perm(user, "delivery.update", "delivery.vehicle.manage")
        docs = await db.delivery_vehicles.find({"active": {"$ne": False}}, {"_id": 0}).to_list(200)
        return {"vehicles": [public_vehicle(d) for d in docs]}

    @api_router.post("/delivery/vehicles")
    async def create_vehicle(payload: VehiclePayload, user=Depends(current_user)):
        await require_any_perm(user, "delivery.vehicle.manage", "delivery.update")
        veh = normalize_vehicle(payload.model_dump())
        if not veh.get("plate") and not veh.get("name"):
            raise HTTPException(400, "Nama atau plat kendaraan wajib.")
        await db.delivery_vehicles.update_one({"id": veh["id"]}, {"$set": veh}, upsert=True)
        return {"vehicle": public_vehicle(veh)}

    @api_router.patch("/delivery/vehicles/{vehicle_id}")
    async def patch_vehicle(vehicle_id: str, payload: VehiclePayload, user=Depends(current_user)):
        await require_perm(user, "delivery.vehicle.manage")
        existing = await _vehicle_doc(vehicle_id)
        if not existing:
            raise HTTPException(404, "Kendaraan tidak ditemukan")
        veh = normalize_vehicle(payload.model_dump(), existing=existing)
        await db.delivery_vehicles.update_one({"id": vehicle_id}, {"$set": veh})
        return {"vehicle": public_vehicle(veh)}

    @api_router.get("/delivery/drivers")
    async def list_delivery_drivers(user=Depends(current_user)):
        await require_any_perm(user, "delivery.schedule.create", "delivery.vehicle.manage")
        docs = await db.users.find(
            {"status": {"$ne": "inactive"}},
            {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1},
        ).to_list(200)
        allowed_roles = {"driver", "admin", "head_production", "head_operations"}
        return {"drivers": [
            {"id": d.get("id"), "name": d.get("name") or d.get("email"), "email": d.get("email"), "role": d.get("role")}
            for d in docs if d.get("role") in allowed_roles
        ]}

    @api_router.get("/settings/depot")
    async def get_depot(user=Depends(current_user)):
        await require_any_perm(
            user, "settings.depot.view", "settings.depot.manage",
            "delivery.schedule.view", "delivery.update",
        )
        return {"depot": await _depot()}

    @api_router.put("/settings/depot")
    async def put_depot(payload: DepotPayload, user=Depends(current_user)):
        await require_perm(user, "settings.depot.manage")
        depot = normalize_depot(payload.model_dump())
        doc = {"key": DEPOT_KEY, **depot}
        await db.app_settings.update_one({"key": DEPOT_KEY}, {"$set": doc}, upsert=True)
        return {"depot": depot}

    async def on_delivery_confirmed(order: dict) -> None:
        oid = str(order.get("id") or "")
        if not oid:
            return
        plans = await db.delivery_route_plans.find(
            {"status": {"$in": [ROUTE_TERJADWAL, ROUTE_BERJALAN]}, "routes.stops.order_id": oid},
            {"_id": 0},
        ).to_list(20)
        for plan in plans:
            if mark_stop_delivered(plan, order_id=oid):
                await _persist_plan(plan)

    async def on_rescheduled(order: dict, shipment_id: Optional[str] = None) -> None:
        oid = str(order.get("id") or "")
        plans = await db.delivery_route_plans.find(
            {"status": {"$in": [ROUTE_DRAFT, ROUTE_TERJADWAL, ROUTE_BERJALAN]}, "routes.stops.order_id": oid},
            {"_id": 0},
        ).to_list(20)
        for plan in plans:
            started = plan.get("status") == ROUTE_BERJALAN
            if mark_stop_rescheduled(plan, order_id=oid, shipment_id=shipment_id, route_started=started):
                await _persist_plan(plan)

    deps["on_delivery_confirmed"] = on_delivery_confirmed
    deps["on_rescheduled"] = on_rescheduled
    global _hooks
    _hooks = {
        "on_delivery_confirmed": on_delivery_confirmed,
        "on_rescheduled": on_rescheduled,
    }
