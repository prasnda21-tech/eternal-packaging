"""Durable stock obligations saved with operational evidence, never before it.

The immutable intents live in the same order write as the source evidence.
Separate processing receipts are disposable projections; a failed receipt write
cannot erase an obligation. No historical evidence is automatically backfilled.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging

from fastapi import HTTPException

FIELD = "operational_stock_intents"
RECEIPTS = "operational_stock_receipts"
PENDING = "operational_stock_pending"
log = logging.getLogger(__name__)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def event_rows(order, stage):
    if stage == "operator":
        return [(str(s.get("id") or i), e, [{"line_idx": s.get("line_idx"), "qty": e.get("qty")}])
                for i, s in enumerate(order.get("operator_splits") or [])
                for e in s.get("progress_entries") or []]
    if stage == "packing":
        return [(str(e.get("line_idx")), e, [e]) for e in (order.get("packing_info") or {}).get("events") or []]
    if stage == "loading":
        return [("", e, e.get("lines") or []) for e in order.get("loading_confirmations") or []]
    if stage == "delivery":
        return [("", e, e.get("items") or []) for e in order.get("delivery_confirmations") or []]
    raise ValueError("Unknown operational stock stage")


def identity(scope, record):
    return scope + ":" + str(record.get("action_id") or record.get("id") or "")


def collect_intents(before, after, stage, user):
    """Pure: called after operational validation and before its optimistic write."""
    snap = after.get("physical_stock_snapshot") or {}
    trigger = {"packing": "packing_completed", "loading": "loading_confirmation", "delivery": "delivery_confirmed"}
    if not snap.get("enabled") or (stage != "operator" and snap.get("stock_out_trigger") != trigger[stage]):
        return {}
    seen = {identity(scope, rec) for scope, rec, _ in event_rows(before, stage)}
    intents = {}
    for scope, rec, rows in event_rows(after, stage):
        key = identity(scope, rec)
        if key in seen or rec.get("legacy") or rec.get("cancelled") or rec.get("replaced"):
            continue
        if not (rec.get("id") or rec.get("action_id")):
            raise ValueError("Operational event must have a durable identity")
        token = hashlib.sha256(f"{after['id']}:{stage}:{key}".encode()).hexdigest()
        items = []
        for row in rows:
            idx = row.get("line_idx")
            try:
                idx = int(idx)
                line = after["line_items"][idx] if idx >= 0 else {}
            except (TypeError, ValueError, IndexError, KeyError):
                line = {}
            qty = float(row.get("qty", row.get("quantity", 0)) or 0)
            if stage == "packing" and not (before.get("packing_info") or {}).get("events") and "packed_qty_after" not in rec:
                from quantity_rules import packed_qty_for_line
                qty = max(0, qty - packed_qty_for_line(before, idx))
            if qty <= 0:
                continue
            is_print = str(line.get("production_process") or "").lower() != "non_print"
            if stage == "operator" and not is_print:
                continue
            items.append({"line_idx": idx, "line_id": line.get("line_id"),
                          "master_item_id": line.get("master_item_id"),
                          "accurate_item_id": line.get("accurate_item_id"),
                          "quantity": qty, "unit": line.get("unit") or "PCS",
                          "is_print": is_print})
        if not items:
            continue
        intents[token] = {"id": token, "order_id": after["id"], "po_number": after.get("po_number"),
                          "stage": stage, "source_key": key, "source_id": rec.get("id"),
                          "action_id": rec.get("action_id"), "shipment_id": rec.get("shipment_id"),
                          "created_at": stamp(), "items": items, "source_fingerprint": json.dumps(rec, sort_keys=True, default=str),
                          "actor": {k: user.get(k) for k in ("id", "email", "name")},
                          "protocol": 1}
    return intents


def with_intents(before, after, stage, user):
    intents = collect_intents(before, after, stage, user)
    after["_new_operational_stock_intents"] = intents
    # Never merge stale queue state into a full-document $set.
    return intents


def persist_fields(order):
    return {f"{FIELD}.{key}": value for key, value in order.pop("_new_operational_stock_intents", {}).items()}


async def summary(db, order):
    tokens = set(order.get(FIELD) or {})
    async for delivery in db.sales_deliveries.find({"po_id": order["id"], FIELD: {"$exists": True}}, {FIELD: 1}):
        tokens.update(delivery.get(FIELD) or {})
    if not tokens:
        return {"pending": 0, "status": "not_applicable"}
    completed = await db[RECEIPTS].count_documents({"_id": {"$in": list(tokens)}, "status": "applied"})
    pending = max(0, len(tokens) - completed)
    return {"pending": pending, "status": "pending" if pending else "applied",
            "message": "Progres tersimpan · pencatatan stok tertunda" if pending else "Pencatatan stok selesai"}


async def process_intent(db, order_id, intent_id, source="orders"):
    """Apply only persisted obligations through the durable stock engine.

    Never release a foreign claim. A missing opening is review-only: creating an
    opening later may already include these goods, so retry must not invent them.
    """
    from services import physical_stock_engine as engine
    from services.product_stock_state import get_product_stock_state
    if source not in {"orders", "sales_deliveries"}:
        raise HTTPException(400, "Sumber mutasi tidak valid")
    order = await db[source].find_one({"id": order_id}, {"_id": 0})
    task = ((order or {}).get(FIELD) or {}).get(intent_id)
    if not task:
        raise HTTPException(404, "Mutasi operasional tidak ditemukan")
    receipt = await db[RECEIPTS].find_one({"_id": intent_id})
    if receipt and (receipt.get("source_deletion_requested") or receipt.get("status") == "review_required"):
        return receipt
    if receipt and receipt.get("status") == "applied":
        await db[source].update_one({"id": order_id}, {"$pull": {PENDING: intent_id}})
        return receipt
    outcome = {"order_id": order_id, "source": source, "intent_id": intent_id, "updated_at": stamp(), "status": "pending"}
    try:
        if order.get("cancelled") or order.get("is_cancelled"):
            raise HTTPException(409, "Sumber dibatalkan; perlu tinjauan stok")
        if task["stage"] == "delivery":
            slots = (order.get("delivery_confirm_fence") or {}).get("ops") or {}
            legacy_fence = any(v.get("action_id") == task.get("action_id") for v in slots.values() if isinstance(v, dict))
            legacy_op = await db.delivery_confirm_operations.find_one({
                "order_id": task["order_id"], "action_id": task.get("action_id"),
                "$or": [{"fence_protocol": {"$ne": 1}}, {"holders": {"$gt": 0}}, {"claimed_masters.0": {"$exists": True}}],
            })
            if legacy_fence or legacy_op:
                raise HTTPException(409, {"review_required": True, "message": "Operasi memakai klaim stok lama; tinjau sebelum pencatatan agar tidak terpotong dua kali"})
        if source == "orders":
            sources = {identity(scope, rec): json.dumps(rec, sort_keys=True, default=str)
                       for scope, rec, _ in event_rows(order, task["stage"])}
            source_ok = sources.get(task["source_key"]) == task["source_fingerprint"]
        else:
            source_ok = json.dumps(order.get("line_items"), sort_keys=True, default=str) == task["source_fingerprint"]
        if not source_ok:
            raise HTTPException(409, "Bukti operasional berubah; perlu tinjauan stok")
        # SJ intents live on the delivery document, but print prerequisites live on its PO.
        dependency_order = order if source == "orders" else await db.orders.find_one({"id": task["order_id"]})
        if not dependency_order or dependency_order.get("cancelled"):
            raise HTTPException(409, "PO sumber tidak aktif; perlu tinjauan stok")
        # Respect earlier conversion obligations on this order before stock-out.
        if task["stage"] != "operator":
            mids = {x.get("master_item_id") for x in task["items"]}
            for other_id, other in (dependency_order.get(FIELD) or {}).items():
                if (other.get("stage") == "operator" and other.get("created_at", "") <= task["created_at"]
                        and any(x.get("master_item_id") in mids for x in other["items"])):
                    done = await db[RECEIPTS].find_one({"_id": other_id, "status": "applied"})
                    if not done:
                        raise HTTPException(409, "Menunggu pencatatan hasil cetak")
        for i, item in enumerate(task["items"]):
            mid = item.get("master_item_id")
            if not mid:
                raise HTTPException(409, "Identitas master belum pasti; perlu tinjauan")
            event_id = f"operational:{intent_id}:{i}"
            existing = await engine._movement_by_event(db, event_id)
            if existing:
                continue
            state = await get_product_stock_state(db, mid)
            if not state or not state.get("initialized") or state.get("status") != "INITIALIZED":
                raise HTTPException(409, "Saldo awal belum tersedia; perlu tinjauan sebelum menerapkan histori")
            opened = str(state.get("initialized_at") or "")
            if opened and opened > task["created_at"]:
                raise HTTPException(409, "Saldo awal dibuat sesudah pekerjaan; perlu tinjauan agar tidak terhitung ganda")
            pending = state.get("pending_event") or {}
            if pending and pending.get("event_id") != event_id:
                if pending.get("reference_type") != "operational_stock_intent" and pending.get("action_id") == task.get("action_id") and task.get("action_id"):
                    raise HTTPException(409, {"review_required": True, "message": "Klaim lama untuk pekerjaan yang sama perlu tinjauan; tidak membuat debit baru"})
                raise HTTPException(409, "Menunggu mutasi stok lain; klaimnya tidak dilepas")
            if not pending:
                from services.product_unit_conversion import get_product_unit_setting, normalize_uom
                setting = await get_product_unit_setting(db, mid)
                base = normalize_uom(state.get("base_uom") or "PCS")
                tx = normalize_uom(item["unit"])
                if tx == base:
                    conversion = (item["quantity"], 1.0, base, tx)
                else:
                    if (not setting or normalize_uom(setting.get("transaction_uom")) != tx
                            or normalize_uom(setting.get("base_uom")) != base
                            or str(setting.get("updated_at") or "") > task["created_at"]
                            or float(setting.get("conversion_factor") or 0) <= 0):
                        raise HTTPException(409, "Konversi satuan pada waktu pekerjaan belum pasti; perlu tinjauan")
                    factor = float(setting["conversion_factor"])
                    conversion = (item["quantity"] * factor, factor, base, tx)
                await engine.claim_durable_stock_event(
                    db, event_id=event_id, master_item_id=mid,
                    movement_type=engine.MOVEMENT_STATUS_CETAK if task["stage"] == "operator" else engine.MOVEMENT_PENGIRIMAN,
                    transaction_qty=item["quantity"], transaction_uom=item["unit"],
                    source_entity="BELUM_DICETAK" if task["stage"] == "operator" or not item["is_print"] else "SUDAH_DICETAK",
                    destination_entity="SUDAH_DICETAK" if task["stage"] == "operator" else "CUSTOMER",
                    reference_type="operational_stock_intent", reference_id=order_id,
                    reference_number=task.get("po_number") or order_id,
                    order_id=task["order_id"], action_id=intent_id, line_idx=item["line_idx"], line_id=item.get("line_id"),
                    is_print=item["is_print"], user=task["actor"], conversion_snapshot=conversion, now=task["created_at"],
                )
            await engine.finalize_durable_stock_event(db, event_id=event_id, master_item_id=mid, user=task["actor"])
        outcome.update(status="applied", reason=None)
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("review_required"):
            outcome.update(status="review_required", reason=detail["message"])
        else:
            outcome.update(status="pending", reason=str(detail))
    # Unexpected errors leave the immutable obligation intact and surface to the
    # worker/API; they are never represented as a completed stock operation.
    await db[RECEIPTS].update_one({"_id": intent_id}, {"$setOnInsert": {"status": "pending"}}, upsert=True)
    await db[RECEIPTS].update_one(
        {"_id": intent_id, "status": {"$ne": "applied"}, "source_deletion_requested": {"$ne": True}},
        {"$set": outcome})
    saved = await db[RECEIPTS].find_one({"_id": intent_id})
    if saved.get("status") == "applied":
        await db[source].update_one({"id": order_id}, {"$pull": {PENDING: intent_id}})
    return saved


async def prepare_delivery(db, order, shipment_id, action_id):
    """Bind idempotency without acquiring any stock holder/lease."""
    from quantity_rules import canonical_shipment_id, resolve_delivery_shipment
    from services.physical_stock_engine import _reject_explicit_action_conflict, _ensure_action_binding, _acquire_omitted_action
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    sh, error = resolve_delivery_shipment(order, shipment_id)
    if error:
        raise HTTPException(400, error)
    sid = canonical_shipment_id(sh) if sh else ""
    token = str(action_id or "").strip()
    if token:
        await _reject_explicit_action_conflict(db, order, token, sid)
        await _ensure_action_binding(db, order["id"], sid, token)
    else:
        if not sid:
            raise HTTPException(400, "Identitas Surat Jalan diperlukan")
        token = await _acquire_omitted_action(db, order["id"], sid)
    return {"action_id": token, "shipment_id": sid, "order": order}


async def pending_rows(db, limit=100):
    """Read-only view; no retry/recovery side effects."""
    result = []
    for source in ("orders", "sales_deliveries"):
        async for order in db[source].find({FIELD: {"$exists": True}}, {"_id": 0, "id": 1, "po_number": 1, FIELD: 1}):
            for token, task in (order.get(FIELD) or {}).items():
                receipt = await db[RECEIPTS].find_one({"_id": token}, {"_id": 0}) or {}
                if receipt.get("status") == "applied":
                    continue
                result.append({"source": source, "order_id": order["id"], "po_number": task.get("po_number") or order.get("po_number"),
                               "intent_id": token, "stage": task["stage"], "items": task["items"],
                               "created_at": task["created_at"], "status": receipt.get("status", "pending"),
                               "reason": receipt.get("reason", "Menunggu pencatatan stok")})
                if len(result) >= limit:
                    return result
    seen = {row["intent_id"] for row in result}
    async for receipt in db[RECEIPTS].find({"source_deletion_requested": True}).limit(limit):
        if receipt["_id"] not in seen:
            task = receipt["task"]
            result.append({"source": receipt["source"], "order_id": receipt["order_id"],
                           "intent_id": receipt["_id"], "stage": task["stage"], "items": task["items"],
                           "po_number": task.get("po_number"), "status": "review_required", "reason": receipt["reason"]})
            if len(result) >= limit:
                break
    return result


def attach_sales_delivery_intent(order, delivery, user):
    snap = order.get("physical_stock_snapshot") or {}
    if not snap.get("enabled") or snap.get("stock_out_trigger") != "sales_delivery":
        return
    # The source obligation is committed in the same insert as the SJ itself.
    probe = deepcopy(order)
    record = {"id": delivery["id"], "action_id": delivery["id"], "items": []}
    for row in delivery.get("line_items") or []:
        record["items"].append({"line_idx": row.get("line_idx"), "qty": row.get("qty", row.get("quantity"))})
    probe["physical_stock_snapshot"] = {"enabled": True, "stock_out_trigger": "delivery_confirmed"}
    before = deepcopy(probe)
    probe.setdefault("delivery_confirmations", []).append(record)
    intents = collect_intents(before, probe, "delivery", user)
    for task in intents.values():
        task["stage"] = "sales_delivery"
        task["source_fingerprint"] = json.dumps(delivery.get("line_items"), sort_keys=True, default=str)
    delivery[FIELD] = intents
    delivery[PENDING] = list(intents)


async def archive_before_delete(db, document, source):
    """Preserve stock audit evidence when an existing business delete is allowed.

    Deletion never silently erases a delayed obligation or reverses stock. A
    concurrent worker may already have applied it: either result needs review.
    No automatic replay is allowed after deletion has been requested.
    """
    for token, task in (document.get(FIELD) or {}).items():
        await db[RECEIPTS].update_one({"_id": token}, {
            "$setOnInsert": {"created_at": stamp()},
            "$set": {"source": source, "order_id": document["id"], "intent_id": token,
                     "task": task, "source_deletion_requested": True, "status": "review_required",
                     "reason": "Sumber dihapus; periksa mutasi yang sudah atau belum diterapkan", "updated_at": stamp()},
        }, upsert=True)


async def worker(db):
    """Resume persisted pending IDs; a failed database poll does not kill the loop."""
    import asyncio
    while True:
        try:
            for source in ("orders", "sales_deliveries"):
                cursor = db[source].find({PENDING + ".0": {"$exists": True}},
                                         {"_id": 0, "id": 1, FIELD: 1, PENDING: 1}).batch_size(25)
                async for order in cursor:
                    tasks = [(token, order[FIELD][token]) for token in order.get(PENDING, []) if token in order.get(FIELD, {})]
                    tasks.sort(key=lambda row: (row[1]["stage"] != "operator", row[1]["created_at"]))
                    for token, task in tasks:
                        try:
                            await process_intent(db, order["id"], token, source)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            log.exception("Operational stock intent remains pending: %s", token)
                        await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Operational stock queue poll failed; will retry")
        await asyncio.sleep(15)
