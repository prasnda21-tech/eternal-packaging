"""FastAPI routes for delivery batches, vehicles, depot, and route plans.

Registered onto the existing /api router. Authorization is permission-based.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.delivery_batches import (
    STATUS_CANCELLED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    actor_id,
    actor_name,
    apply_stop_updates,
    build_batch,
    mark_published,
    public_batch,
    upsert_driver_ready,
    validate_for_publish,
)
from services.delivery_depot import SETTINGS_KEY as DEPOT_KEY
from services.delivery_depot import depot_from_settings_doc, normalize_depot
from services.delivery_routes import (
    KIND_OPTIMIZED_DRAFT,
    KIND_REOPTIMIZED,
    ROUTE_BERJALAN,
    ROUTE_DRAFT,
    ROUTE_TERJADWAL,
    RouteError,
    apply_optimize_result,
    assignment_for_driver,
    build_optimize_request,
    confirm_plan,
    confirm_summary,
    edit_draft,
    mark_stop_delivered,
    mark_stop_rescheduled,
    my_route_view,
    public_plan,
    start_assignment,
)
from services.delivery_stops import (
    MONGO_QUEUE_FILTER,
    QUEUE_PROJECTION,
    build_queue_stops,
    occupied_stop_keys,
    parse_location,
)
from services.delivery_vehicles import normalize_vehicle, public_vehicle
from services.route_optimizer.base import RouteOptimizerError, RouteOptimizerNotConfigured
from services.route_optimizer.factory import get_route_optimizer


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
        return build_queue_stops(docs, occupied_keys=occ)

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

    async def _save_order_location(order_id: str, loc: dict) -> None:
        parsed = parse_location(loc)
        if parsed.get("lat") is None or parsed.get("lng") is None:
            return
        await db.orders.update_one(
            {"id": order_id},
            {"$set": {"delivery_location": {
                "lat": parsed["lat"],
                "lng": parsed["lng"],
                "place_id": parsed.get("place_id"),
                "formatted_address": parsed.get("formatted_address"),
            }}},
        )

    # ---------- Queue / batch ----------
    @api_router.get("/delivery/queue")
    async def delivery_queue(user=Depends(current_user)):
        await require_any_perm(user, "delivery.schedule.view", "delivery.schedule.create", "schedule.view")
        rows = await _queue_rows()
        return {"rows": rows, "total": len(rows)}

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
                    await _save_order_location(by_id[sid]["order_id"], extra.get("location"))
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
            "nav.driver_route",
        )
        q: Dict[str, Any] = {"status": {"$ne": STATUS_CANCELLED}}
        is_dispatcher = await _can(
            user, "delivery.schedule.view", "delivery.schedule.create", "delivery.schedule.publish",
            "delivery.route.optimize", "delivery.route.confirm",
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
            "nav.driver_route",
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
            "delivery.route.optimize", "delivery.route.edit", "delivery.route.confirm",
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
        await require_perm(user, "delivery.schedule.publish")
        batch = await _get_batch(batch_id)
        try:
            mark_published(batch, user)
        except ValueError as e:
            raise HTTPException(400, str(e))
        await _persist_batch(batch)
        return {"batch": public_batch(batch)}

    async def _resolve_vehicle(payload_vehicle: Optional[dict], vehicle_id: Optional[str], user: dict) -> dict:
        vid = (vehicle_id or (payload_vehicle or {}).get("id") or "").strip()
        if vid:
            existing = await _vehicle_doc(vid)
            if existing:
                return existing
        raw = dict(payload_vehicle or {})
        if vehicle_id:
            raw["id"] = vehicle_id
        plate = str(raw.get("plate") or raw.get("vehicle_plate") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not plate and not name and not vid:
            raise HTTPException(400, "Pilih atau isi kendaraan (nama/plat).")
        veh = normalize_vehicle(raw)
        await db.delivery_vehicles.update_one({"id": veh["id"]}, {"$set": veh}, upsert=True)
        return veh

    @api_router.post("/delivery/batches/{batch_id}/ready")
    async def driver_ready(batch_id: str, payload: ReadyPayload, user=Depends(current_user)):
        """Siap Berangkat. Nama driver ALWAYS from authenticated account."""
        await require_any_perm(user, "delivery.update", "delivery.vehicle.manage")
        batch = await _get_batch(batch_id)
        if batch.get("status") != STATUS_PUBLISHED:
            raise HTTPException(400, "Batch belum dipublish ke Driver.")
        requested = str(payload.driver_user_id or "").strip()
        me = _uid(user)
        if requested and requested != me:
            if not await _can(user, "delivery.schedule.publish", "delivery.route.confirm"):
                raise HTTPException(403, "Nama Driver tidak dapat diubah. Gunakan akun login Anda.")
            target = await _lookup_user(requested)
            if not target:
                raise HTTPException(400, "Driver tidak ditemukan.")
            driver_id = str(target.get("id") or requested)
            driver_nm = str(target.get("name") or target.get("email") or "")
        else:
            driver_id = me
            driver_nm = actor_name(user)
        veh = await _resolve_vehicle(payload.vehicle, payload.vehicle_id, user)
        upsert_driver_ready(batch, driver_user_id=driver_id, driver_name=driver_nm, vehicle=veh)
        await _persist_batch(batch)
        return {"batch": public_batch(batch), "me": {"id": driver_id, "name": driver_nm}}

    @api_router.post("/delivery/batches/{batch_id}/optimize")
    async def optimize_batch(batch_id: str, user=Depends(current_user)):
        await require_perm(user, "delivery.route.optimize")
        batch = await _get_batch(batch_id)
        if batch.get("status") != STATUS_PUBLISHED:
            raise HTTPException(400, "Publish batch ke Driver sebelum optimize.")
        depot = await _depot()
        if not depot.get("configured"):
            raise HTTPException(400, "Lengkapi data Gudang (nama + koordinat) di Pengaturan.")
        req = build_optimize_request(batch, depot)
        if not req.vehicles:
            raise HTTPException(400, "Belum ada Driver/Kendaraan yang Siap Berangkat.")
        try:
            provider = get_route_optimizer()
            result = provider.optimize(req)
        except RouteOptimizerNotConfigured as e:
            raise HTTPException(503, str(e))
        except RouteOptimizerError as e:
            raise HTTPException(502, str(e))
        existing = None
        if batch.get("route_plan_id"):
            existing = await db.delivery_route_plans.find_one({"id": batch["route_plan_id"]}, {"_id": 0})
            if existing and existing.get("status") not in (ROUTE_DRAFT,):
                kind = KIND_REOPTIMIZED
            else:
                kind = KIND_OPTIMIZED_DRAFT if not existing else KIND_REOPTIMIZED
        else:
            kind = KIND_OPTIMIZED_DRAFT
        plan = apply_optimize_result(batch, result, user=user, kind=kind, existing_plan=existing)
        plan["depot"] = depot
        await _persist_plan(plan)
        batch["route_plan_id"] = plan["id"]
        await _persist_batch(batch)
        return {"plan": public_plan(plan), "batch": public_batch(batch)}

    @api_router.post("/delivery/batches/{batch_id}/optimize-remaining")
    async def optimize_remaining(batch_id: str, user=Depends(current_user)):
        await require_perm(user, "delivery.route.optimize")
        batch = await _get_batch(batch_id)
        depot = await _depot()
        if not depot.get("configured"):
            raise HTTPException(400, "Lengkapi data Gudang (nama + koordinat) di Pengaturan.")
        existing = None
        if batch.get("route_plan_id"):
            existing = await db.delivery_route_plans.find_one({"id": batch["route_plan_id"]}, {"_id": 0})
        req = build_optimize_request(batch, depot, remaining_only=True)
        if existing:
            delivered_ids = set()
            for r in existing.get("routes") or []:
                for s in r.get("stops") or []:
                    if s.get("status") == "delivered":
                        delivered_ids.add(s.get("stop_id"))
            req.stops = [s for s in req.stops if s.stop_id not in delivered_ids]
        try:
            result = get_route_optimizer().optimize(req)
        except RouteOptimizerNotConfigured as e:
            raise HTTPException(503, str(e))
        except RouteOptimizerError as e:
            raise HTTPException(502, str(e))
        plan = apply_optimize_result(
            batch, result, user=user, kind=KIND_REOPTIMIZED, existing_plan=existing,
        )
        plan["depot"] = depot
        await _persist_plan(plan)
        batch["route_plan_id"] = plan["id"]
        await _persist_batch(batch)
        return {"plan": public_plan(plan)}

    # ---------- Route plans ----------
    @api_router.get("/delivery/route-plans/{plan_id}")
    async def get_route_plan(plan_id: str, user=Depends(current_user)):
        await require_any_perm(
            user,
            "delivery.route.optimize",
            "delivery.route.edit",
            "delivery.route.confirm",
            "delivery.update",
            "delivery.schedule.view",
            "nav.driver_route",
        )
        plan = await _get_plan(plan_id)
        driver_only = not await _can(
            user, "delivery.schedule.view", "delivery.route.optimize",
            "delivery.route.edit", "delivery.route.confirm",
        )
        uid = _uid(user)
        if driver_only:
            if not assignment_for_driver(plan, uid):
                raise HTTPException(403, "Anda tidak memiliki rute ini.")
            return {"plan": public_plan(plan, driver_user_id=uid), "me": {"id": uid, "name": actor_name(user)}}
        return {"plan": public_plan(plan), "me": {"id": uid, "name": actor_name(user)}}

    @api_router.patch("/delivery/route-plans/{plan_id}")
    async def patch_route_plan(plan_id: str, payload: EditPlanPayload, user=Depends(current_user)):
        await require_perm(user, "delivery.route.edit")
        plan = await _get_plan(plan_id)
        try:
            edit_draft(plan, payload.model_dump(), user)
        except RouteError as e:
            raise HTTPException(400, str(e))
        await _persist_plan(plan)
        return {"plan": public_plan(plan)}

    @api_router.get("/delivery/route-plans/{plan_id}/confirm-preview")
    async def confirm_preview(plan_id: str, user=Depends(current_user)):
        await require_any_perm(user, "delivery.route.confirm", "delivery.route.optimize")
        plan = await _get_plan(plan_id)
        batch = await _get_batch(plan.get("batch_id"))
        return {"summary": confirm_summary(plan, batch)}

    @api_router.post("/delivery/route-plans/{plan_id}/confirm")
    async def confirm_route_plan(plan_id: str, user=Depends(current_user)):
        await require_perm(user, "delivery.route.confirm")
        plan = await _get_plan(plan_id)
        try:
            confirm_plan(plan, user)
        except RouteError as e:
            raise HTTPException(400, str(e))
        await _persist_plan(plan)
        return {"plan": public_plan(plan)}

    @api_router.post("/delivery/route-plans/{plan_id}/start")
    async def start_route_plan(plan_id: str, user=Depends(current_user)):
        await require_perm(user, "delivery.update")
        plan = await _get_plan(plan_id)
        uid = _uid(user)
        try:
            start_assignment(plan, driver_user_id=uid, user=user)
        except RouteError as e:
            raise HTTPException(403 if "tidak memiliki" in str(e).lower() else 400, str(e))
        await _persist_plan(plan)
        asg = assignment_for_driver(plan, uid)
        return {"plan": public_plan(plan, driver_user_id=uid), "route": my_route_view(plan, asg, user)}

    @api_router.get("/delivery/my-routes")
    async def my_routes(user=Depends(current_user)):
        await require_any_perm(user, "delivery.update", "nav.driver_route")
        uid = _uid(user)
        docs = await db.delivery_route_plans.find(
            {
                "status": {"$in": [ROUTE_TERJADWAL, ROUTE_BERJALAN, "selesai"]},
                "routes.driver_user_id": uid,
            },
            {"_id": 0},
        ).sort("delivery_date", -1).to_list(100)
        rows = []
        for plan in docs:
            asg = assignment_for_driver(plan, uid)
            if asg:
                rows.append(my_route_view(plan, asg, user))
        published = await db.delivery_batches.find(
            {"status": STATUS_PUBLISHED},
            {"_id": 0},
        ).sort("created_at", -1).to_list(50)
        return {
            "me": {"id": uid, "name": actor_name(user)},
            "routes": rows,
            "published_batches": [public_batch(b) for b in published],
        }

    # ---------- Vehicles / depot / drivers ----------
    @api_router.get("/delivery/vehicles")
    async def list_vehicles(user=Depends(current_user)):
        await require_any_perm(user, "delivery.update", "delivery.vehicle.manage", "delivery.route.optimize")
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
        await require_any_perm(user, "delivery.schedule.publish", "delivery.route.optimize", "delivery.vehicle.manage")
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
            "delivery.schedule.view", "delivery.route.optimize", "delivery.update",
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
