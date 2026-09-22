"""Inventory HPP valuation cutover.

Baseline plus future goods-receipt recognition events. Historical PO/GR
documents are not replayed once a cutover is ACTIVE.

This module does not write customer pricing, product_stock_state, or
stock_movements.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.hpp_valuation import (
    COGS_UNAVAILABLE_REASON,
    COST_BASIS_UOM,
    FINANCIAL_INVENTORY_BASIS_GR,
    FINANCIAL_INVENTORY_MODEL_VERSION,
    LAYERS,
    METHOD_AVG,
    RECOG_GR,
    SETTINGS_KEY,
    UI_RECOG,
    VERSIONS,
    count_financial_eligible_acquisitions,
    financial_uom_normalization_valid,
    get_hpp_settings,
    moving_average_from_events,
    normalize_method,
    normalize_recognition,
)
from services.inventory_accounting import (
    CANONICAL_GR,
    InvalidUnitConversionError,
    financial_inventory_item_key,
    financial_uom_snapshot,
    resolve_conversion_from_setting,
)
from services.purchase_common import actor_snapshot, item_identity, money_round, now_iso, qty_round

CUTOVERS = "hpp_valuation_cutovers"
BASELINES = "hpp_valuation_baseline_lines"
EVENTS = "hpp_recognition_events"
CLAIMS = "hpp_cutover_claims"
CLAIM_CONFIRM = "hpp_cutover"
CLAIM_RECALC = "hpp_cutover_recalc"

STATUS_BUILDING = "BUILDING"
STATUS_ACTIVE = "ACTIVE"
STATUS_FAILED = "FAILED"
STATUS_NEEDS_RECOVERY = "NEEDS_RECOVERY"
STATUS_RECOGNIZED = "RECOGNIZED"
STATUS_NEEDS_COST = "NEEDS_COST"
STATUS_NEEDS_UOM = "NEEDS_UOM"
STATUS_PENDING = "PENDING"
STATUS_REVERSED = "REVERSED"
STATUS_OK = "OK"
STATUS_QTY_MISMATCH = "QTY_MISMATCH"
STATUS_MISSING_COST = "MISSING_COST"

SOURCE_OPENING = "OPENING_HPP"
SOURCE_GOODS_RECEIPT = "GOODS_RECEIPT"
SOURCE_GOODS_RECEIPT_REVERSAL = "GOODS_RECEIPT_REVERSAL"
REASON_NEGATIVE = "NEGATIVE_PHYSICAL_STOCK"

LEASE_SECONDS = 120


def _dec(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _expires(seconds: int = LEASE_SECONDS) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _http(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message, **extra})


def _public(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {key: value for key, value in doc.items() if key != "_id"}


async def ensure_indexes(db) -> None:
    await db[CUTOVERS].create_index(
        "client_request_id",
        unique=True,
        partialFilterExpression={"client_request_id": {"$type": "string"}},
        name="uniq_hpp_cutover_client_request",
    )
    await db[CUTOVERS].create_index(
        "active_marker",
        unique=True,
        partialFilterExpression={"active_marker": "ACTIVE"},
        name="uniq_active_hpp_cutover",
    )
    await db[BASELINES].create_index(
        [("cutover_id", 1), ("master_item_id", 1)],
        unique=True,
        name="uniq_hpp_baseline_sku",
    )
    await db[EVENTS].create_index("source_key", unique=True, name="uniq_hpp_recognition_source_key")
    await db[EVENTS].create_index([("cutover_id", 1), ("recognized_at", 1)], name="hpp_recognition_cutover_time")
    for claim_id in (CLAIM_CONFIRM, CLAIM_RECALC):
        await db[CLAIMS].update_one(
            {"_id": claim_id},
            {"$setOnInsert": {"state": "IDLE", "lease_owner": None, "lease_expires_at": None, "client_request_id": None}},
            upsert=True,
        )


async def get_active_cutover(db) -> Optional[dict]:
    return await db[CUTOVERS].find_one({"status": STATUS_ACTIVE}, {"_id": 0})


async def _settings_doc(db) -> Optional[dict]:
    return await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})


def _settings_conflict(current: dict, payload: dict) -> Optional[str]:
    expected_method = normalize_method(payload.get("expected_method"))
    expected_recognition = normalize_recognition(
        payload.get("expected_recognition_point") or payload.get("expected_recognition")
    )
    expected_version = payload.get("expected_active_version")
    expected_version = None if expected_version in ("", None) else str(expected_version)
    current_version = current.get("active_version") or None
    if current.get("method") != expected_method or current.get("recognition_point") != expected_recognition:
        return "expected settings do not match the stored HPP settings"
    if current_version != expected_version:
        return "expected active_version does not match the stored pointer"
    if (current.get("method"), current.get("recognition_point")) != (METHOD_AVG, RECOG_GR):
        if not payload.get("acknowledge_settings_change"):
            return "target RATA-RATA / goods_receipt requires explicit acknowledgement"
    return None


async def _read_stock_qty(db, master_item_id: str) -> tuple:
    mid = str(master_item_id or "").strip()
    state = await db.product_stock_state.find_one(
        {"$or": [{"_id": mid}, {"master_item_id": mid}]},
        {"_id": 0},
    )
    if not state:
        return 0.0, {}
    return float(state.get("total_qty") or 0), state


def _baseline_from_stock(cutover_id: str, raw: dict, physical: float, state: dict) -> dict:
    mid = str(raw.get("master_item_id") or "").strip()
    aid = str(raw.get("accurate_item_id") or state.get("accurate_item_id") or "").strip()
    identity = item_identity({"master_item_id": mid, "accurate_item_id": aid})
    unit_raw = raw.get("opening_unit_cost")
    unit = None
    if unit_raw not in (None, ""):
        unit = money_round(unit_raw)
    base_uom = str(raw.get("base_uom") or state.get("base_uom") or "PCS")
    if physical < 0:
        valuation_qty = 0.0
        total = 0.0
        status = STATUS_QTY_MISMATCH
        reason = REASON_NEGATIVE
        unit_cost = unit
    elif physical == 0:
        valuation_qty = 0.0
        total = 0.0
        status = STATUS_OK
        reason = None
        unit_cost = unit
    elif unit is None or _dec(unit) <= 0:
        valuation_qty = 0.0
        total = 0.0
        status = STATUS_NEEDS_COST
        reason = STATUS_MISSING_COST
        unit_cost = None
    else:
        valuation_qty = qty_round(physical)
        total = money_round(_dec(valuation_qty) * _dec(unit))
        status = STATUS_OK
        reason = None
        unit_cost = unit
    return {
        "cutover_id": cutover_id,
        "master_item_id": mid,
        "item_identity": identity,
        "accurate_item_id": aid,
        "item_code": raw.get("item_code") or "",
        "item_name": raw.get("item_name") or raw.get("item_code") or mid,
        "stock_fisik_snapshot": qty_round(physical),
        "opening_unit_cost": unit_cost,
        "opening_total_cost": total,
        "valuation_qty": valuation_qty,
        "base_uom": base_uom,
        "source": SOURCE_OPENING,
        "hpp_status": status,
        "reconciliation_reason": reason,
    }


def _events_from_baseline_and_recognition(cutover: dict, lines: List[dict], recognitions: List[dict]) -> List[dict]:
    """Baseline sorts first. Recognition uses post time, never receipt_date."""
    baseline_at = str(cutover.get("created_at") or "")
    events: List[dict] = []
    for line in lines:
        if _dec(line.get("valuation_qty")) <= 0 or _dec(line.get("opening_unit_cost")) <= 0:
            continue
        events.append({
            "event_id": f"opening:{cutover['cutover_id']}:{line['master_item_id']}",
            "item_identity": line["item_identity"],
            "source_type": SOURCE_OPENING,
            "source_id": cutover["cutover_id"],
            "source_line_id": line["master_item_id"],
            "source_number": cutover["cutover_id"],
            "recognized_at": baseline_at,
            "recognized_qty": line["valuation_qty"],
            "unit_cost": line["opening_unit_cost"],
            "master_item_id": line.get("master_item_id") or "",
            "accurate_item_id": line.get("accurate_item_id") or "",
            "item_name": line.get("item_name"),
            "item_code": line.get("item_code"),
        })
    future = []
    for rec in recognitions:
        if rec.get("cutover_id") != cutover.get("cutover_id"):
            continue
        if rec.get("status") != STATUS_RECOGNIZED:
            continue
        if _dec(rec.get("qty")) <= 0 or _dec(rec.get("unit_cost")) <= 0:
            continue
        posted = str(rec.get("recognized_at") or "")
        if posted <= baseline_at:
            posted = baseline_at + "~"
        future.append({
            "event_id": rec["event_id"],
            "item_identity": rec["item_identity"],
            "source_type": SOURCE_GOODS_RECEIPT,
            "source_id": rec.get("source_key"),
            "source_line_id": rec.get("source_key"),
            "source_number": rec.get("source_key"),
            "recognized_at": posted,
            "recognized_qty": rec["qty"],
            "unit_cost": rec["unit_cost"],
            "total_cost": rec.get("total_cost"),
            "master_item_id": rec.get("master_item_id") or "",
            "accurate_item_id": rec.get("accurate_item_id") or "",
            "item_name": rec.get("item_name"),
            "item_code": rec.get("item_code"),
            "financial_inventory_item_key": rec.get("financial_inventory_item_key"),
            "canonical_source_type": rec.get("canonical_source_type"),
            "canonical_source_id": rec.get("canonical_source_id"),
            "financial_inventory_eligible": rec.get("financial_inventory_eligible"),
            "financial_ineligibility_reason": rec.get("financial_ineligibility_reason"),
            "financial_ineligibility_reasons": rec.get("financial_ineligibility_reasons") or [],
            "transaction_qty": rec.get("transaction_qty"),
            "transaction_uom": rec.get("transaction_uom"),
            "conversion_factor": rec.get("conversion_factor"),
            "base_qty": rec.get("base_qty"),
            "base_uom": rec.get("base_uom"),
            "transaction_unit_cost": rec.get("transaction_unit_cost"),
            "base_unit_cost": rec.get("base_unit_cost"),
        })
    future.sort(key=lambda row: (str(row.get("recognized_at") or ""), str(row.get("event_id") or "")))
    return events + future


async def _acquire_lease(db, claim_id: str, owner: str, request_id: Optional[str]) -> dict:
    now = now_iso()
    claimed = await db[CLAIMS].find_one_and_update(
        {
            "_id": claim_id,
            "$or": [
                {"state": {"$in": ["IDLE", "FAILED", STATUS_NEEDS_RECOVERY]}},
                {"state": STATUS_BUILDING, "lease_expires_at": {"$lt": now}},
            ],
        },
        {"$set": {
            "state": STATUS_BUILDING,
            "lease_owner": owner,
            "client_request_id": request_id,
            "lease_expires_at": _expires(),
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed or claimed.get("lease_owner") != owner or claimed.get("client_request_id") != request_id:
        raise _http(409, "CONFLICT", "Cutover HPP sedang diproses oleh proses lain.")
    return claimed


async def _release_lease(db, claim_id: str, owner: str, request_id: Optional[str], state: str) -> None:
    await db[CLAIMS].update_one(
        {"_id": claim_id, "lease_owner": owner, "client_request_id": request_id},
        {"$set": {"state": state, "lease_expires_at": None}},
    )


async def recover_stale_cutover_lease(db, *, now: Optional[str] = None) -> dict:
    """Expired BUILDING lease becomes NEEDS_RECOVERY. Never promotes ACTIVE."""
    stamp = now or now_iso()
    claim = await db[CLAIMS].find_one({"_id": CLAIM_CONFIRM})
    if not claim or claim.get("state") != STATUS_BUILDING:
        return {"changed": False, "status": (claim or {}).get("state") or "IDLE"}
    if str(claim.get("lease_expires_at") or "") >= stamp:
        return {"changed": False, "status": STATUS_BUILDING}
    request_id = claim.get("client_request_id")
    await db[CUTOVERS].update_many(
        {"client_request_id": request_id, "status": STATUS_BUILDING},
        {"$set": {"status": STATUS_NEEDS_RECOVERY, "recovered_at": stamp}},
    )
    await db[VERSIONS].update_many(
        {"cutover_client_request_id": request_id, "status": STATUS_BUILDING},
        {"$set": {"status": STATUS_NEEDS_RECOVERY, "recovered_at": stamp}},
    )
    await db[CLAIMS].update_one(
        {"_id": CLAIM_CONFIRM, "state": STATUS_BUILDING, "lease_expires_at": {"$lt": stamp}},
        {"$set": {"state": STATUS_NEEDS_RECOVERY, "lease_expires_at": None}},
    )
    active = await db[CUTOVERS].count_documents({"status": STATUS_ACTIVE, "client_request_id": request_id})
    return {"changed": True, "status": STATUS_NEEDS_RECOVERY, "activated": active}


def _running_position(events: List[dict]) -> Dict[str, dict]:
    _layers, _cogs, state = moving_average_from_events(events)
    positions = {}
    for ident, row in state.items():
        qty = row["qty"]
        average = row["average_cost"]
        positions[ident] = {
            "valuation_qty": float(qty),
            "current_hpp": money_round(average) if qty else 0.0,
            "exact_average": average,
            "valuation_value": money_round(qty * average) if qty else 0.0,
        }
    return positions


async def list_reconciliation(db, cutover_id: Optional[str] = None) -> List[dict]:
    cutover = None
    if cutover_id:
        cutover = await db[CUTOVERS].find_one({"cutover_id": cutover_id}, {"_id": 0})
    if not cutover:
        cutover = await get_active_cutover(db)
    if not cutover and cutover_id:
        cutover = await db[CUTOVERS].find_one({"cutover_id": cutover_id}, {"_id": 0})
    if not cutover:
        return []
    lines = await db[BASELINES].find({"cutover_id": cutover["cutover_id"]}, {"_id": 0}).to_list(5000)
    recognitions = await db[EVENTS].find({"cutover_id": cutover["cutover_id"]}, {"_id": 0}).to_list(20000)
    events = _events_from_baseline_and_recognition(cutover, lines, recognitions)
    positions = _running_position(events) if events else {}
    event_statuses: Dict[str, set] = {}
    event_reasons: Dict[str, str] = {}
    for recognition in recognitions:
        mid = str(recognition.get("master_item_id") or "")
        if mid:
            event_statuses.setdefault(mid, set()).add(recognition.get("status"))
            status = recognition.get("status")
            if status in {STATUS_FAILED, STATUS_PENDING}:
                event_reasons[mid] = STATUS_FAILED
            elif status == STATUS_NEEDS_UOM and event_reasons.get(mid) != STATUS_FAILED:
                event_reasons[mid] = STATUS_NEEDS_UOM
            elif status == STATUS_NEEDS_COST and not event_reasons.get(mid):
                event_reasons[mid] = STATUS_NEEDS_COST
    rows = []
    for line in lines:
        mid = line["master_item_id"]
        stock_qty, _state = await _read_stock_qty(db, mid)
        pos = positions.get(line["item_identity"]) or {
            "valuation_qty": 0.0,
            "current_hpp": 0.0,
            "exact_average": Decimal("0"),
            "valuation_value": 0.0,
        }
        difference = float(Decimal(str(stock_qty)) - Decimal(str(pos["valuation_qty"])))
        if line.get("hpp_status") == STATUS_QTY_MISMATCH:
            status = STATUS_QTY_MISMATCH
        elif STATUS_FAILED in event_statuses.get(mid, set()) or STATUS_PENDING in event_statuses.get(mid, set()):
            status = STATUS_FAILED
        elif STATUS_NEEDS_UOM in event_statuses.get(mid, set()):
            status = STATUS_NEEDS_UOM
        elif line.get("hpp_status") == STATUS_NEEDS_COST or STATUS_NEEDS_COST in event_statuses.get(mid, set()):
            status = STATUS_NEEDS_COST
        elif line.get("hpp_status") == STATUS_MISSING_COST:
            status = STATUS_MISSING_COST
        elif abs(difference) > 1e-6:
            status = STATUS_QTY_MISMATCH
        elif cutover.get("status") == STATUS_BUILDING:
            status = STATUS_BUILDING
        elif cutover.get("status") == STATUS_FAILED:
            status = STATUS_FAILED
        else:
            status = STATUS_OK
        rows.append({
            "master_item_id": mid,
            "item_identity": line.get("item_identity"),
            "stock_fisik": stock_qty,
            "valuation_qty": pos["valuation_qty"],
            "difference": difference,
            "current_hpp": pos["current_hpp"],
            "exact_average": str(pos["exact_average"]),
            "valuation_value": pos["valuation_value"],
            "hpp_status": status,
            "reconciliation_reason": event_reasons.get(mid) or line.get("reconciliation_reason"),
            "cutover_status": cutover.get("status"),
        })
    return rows


async def _fail_closed(
    db,
    *,
    version_id: str,
    prev_active: Optional[str],
    cutover_id: str,
    message: str,
    activate_cutover: bool,
) -> None:
    """Keep the previous settings pointer. Never report success after a failed build."""
    settings = await _settings_doc(db)
    pointed = (settings or {}).get("active_version")
    if pointed == version_id:
        await db[VERSIONS].update_one(
            {"id": version_id},
            {"$set": {"status": STATUS_NEEDS_RECOVERY, "error": message, "recalculated_at": now_iso()}},
        )
        await db[CUTOVERS].update_one(
            {"cutover_id": cutover_id, "status": {"$in": [STATUS_BUILDING, STATUS_ACTIVE]}},
            {"$set": {"status": STATUS_NEEDS_RECOVERY, "error": message}, "$unset": {"active_marker": ""}},
        )
        return
    await db[VERSIONS].update_one(
        {"id": version_id, "status": {"$in": [STATUS_BUILDING, STATUS_ACTIVE]}},
        {"$set": {"status": STATUS_FAILED, "error": message, "recalculated_at": now_iso()}},
    )
    if prev_active and prev_active != version_id:
        await db[VERSIONS].update_one(
            {"id": prev_active, "status": "SUPERSEDED", "superseded_by": version_id},
            {"$set": {"status": STATUS_ACTIVE}, "$unset": {"superseded_at": "", "superseded_by": ""}},
        )
    await _mark_failed_pointer(db, prev_active, message)
    if activate_cutover:
        await db[CUTOVERS].update_one(
            {"cutover_id": cutover_id, "status": STATUS_BUILDING},
            {"$set": {"status": STATUS_FAILED, "error": message}, "$unset": {"active_marker": ""}},
        )


async def _mark_failed_pointer(db, prev_active: Optional[str], message: str) -> None:
    filt: Dict[str, Any] = {"key": SETTINGS_KEY}
    if prev_active is None:
        filt["$or"] = [{"active_version": None}, {"active_version": {"$exists": False}}]
    else:
        filt["active_version"] = prev_active
    await db.app_settings.update_one(
        filt,
        {"$set": {
            "last_recalculation_status": STATUS_FAILED,
            "last_recalculation_error": message,
            "last_recalculation_at": now_iso(),
        }},
    )


async def _cas_settings(db, prev_active: Optional[str], updates: dict) -> None:
    if prev_active is None:
        existing = await _settings_doc(db)
        if existing is None:
            try:
                await db.app_settings.insert_one({"key": SETTINGS_KEY, **updates})
                return
            except DuplicateKeyError:
                raise _http(409, "SETTINGS_CONFLICT", "Pointer HPP berubah sebelum aktivasi.")
        result = await db.app_settings.update_one(
            {"key": SETTINGS_KEY, "$or": [{"active_version": None}, {"active_version": {"$exists": False}}]},
            {"$set": updates},
        )
    else:
        result = await db.app_settings.update_one(
            {"key": SETTINGS_KEY, "active_version": prev_active},
            {"$set": updates},
        )
    if result.matched_count != 1:
        raise _http(409, "SETTINGS_CONFLICT", "Pointer HPP berubah sebelum aktivasi.")


async def rebuild_cutover_valuation(
    db,
    cutover: dict,
    *,
    user: Optional[dict] = None,
    issues: Optional[List[dict]] = None,
    requested_method: str = METHOD_AVG,
    requested_recognition: str = RECOG_GR,
    activate_cutover: bool = False,
) -> dict:
    if normalize_method(requested_method) != METHOD_AVG or normalize_recognition(requested_recognition) != RECOG_GR:
        raise _http(409, "SETTINGS_CONFLICT", "Cutover aktif hanya dihitung sebagai RATA-RATA pada penerimaan barang.")
    owner = str((actor_snapshot(user) or {}).get("id") or uuid.uuid4())
    request_token = f"recalc:{cutover['cutover_id']}:{uuid.uuid4()}"
    await _acquire_lease(db, CLAIM_RECALC, owner, request_token)
    prev = await _settings_doc(db)
    prev_active = (prev or {}).get("active_version")
    version_id = str(uuid.uuid4())
    last = await db[VERSIONS].find({}, {"_id": 0, "version": 1}).sort("version", -1).to_list(1)
    next_ver = int((last[0].get("version") if last else 0) or 0) + 1
    try:
        lines = await db[BASELINES].find({"cutover_id": cutover["cutover_id"]}, {"_id": 0}).to_list(5000)
        recognitions = await db[EVENTS].find(
            {"cutover_id": cutover["cutover_id"]},
            {"_id": 0},
        ).to_list(20000)
        events = _events_from_baseline_and_recognition(cutover, lines, recognitions)
        uom_valid = financial_uom_normalization_valid(events)
        eligible_count = count_financial_eligible_acquisitions(events)
        layers, cogs, _state = moving_average_from_events(events, issues or [])
        await db[VERSIONS].insert_one({
            "id": version_id,
            "version": next_ver,
            "method": METHOD_AVG,
            "recognition_point": RECOG_GR,
            "status": STATUS_BUILDING,
            "created_at": now_iso(),
            "cutover_id": cutover["cutover_id"],
            "cutover_client_request_id": cutover.get("client_request_id"),
            "created_by": actor_snapshot(user),
            "layer_count": 0,
            "event_count": len(events),
        })
        layer_docs = []
        for layer in layers:
            layer_docs.append({
                **layer,
                "calculation_version": version_id,
                "version": next_ver,
                "method": METHOD_AVG,
                "recognition_point": RECOG_GR,
                "cost_basis_uom": COST_BASIS_UOM,
                "financial_cost_basis_uom": COST_BASIS_UOM,
                "financial_inventory_model_version": FINANCIAL_INVENTORY_MODEL_VERSION,
            })
        if layer_docs:
            await db[LAYERS].insert_many(layer_docs)
        seen_ids = [str(row.get("layer_id") or "") for row in layer_docs]
        if len(seen_ids) != len(set(seen_ids)):
            raise RuntimeError("duplicate derived cost layers")
        rows = await list_reconciliation(db, cutover["cutover_id"])
        blocking = [
            row for row in rows
            if row["hpp_status"] in {STATUS_QTY_MISMATCH, STATUS_NEEDS_COST, STATUS_MISSING_COST, STATUS_FAILED}
            and not (row["hpp_status"] == STATUS_QTY_MISMATCH and _dec(row["stock_fisik"]) < 0 and row["valuation_qty"] == 0)
        ]
        # Negative stock is an explicit mismatch and blocks activation of a new cutover.
        negative_block = [row for row in rows if row.get("reconciliation_reason") == REASON_NEGATIVE]
        if activate_cutover and (blocking or negative_block):
            raise RuntimeError("reconciliation blocked activation")
        if not activate_cutover:
            bad_positive = [
                row for row in rows
                if row["hpp_status"] == STATUS_QTY_MISMATCH and _dec(row["stock_fisik"]) >= 0
            ]
            if bad_positive:
                raise RuntimeError("valuation qty does not match stock fisik")
        flipped = await db[VERSIONS].update_one(
            {"id": version_id, "status": STATUS_BUILDING},
            {"$set": {
                "status": STATUS_ACTIVE,
                "recalculated_at": now_iso(),
                "layer_count": len(layer_docs),
                "event_count": len(events),
                "cogs_row_count": len(cogs),
                "cogs_available": bool(issues),
                "cogs_unavailable_reason": None if issues else COGS_UNAVAILABLE_REASON,
                "cost_basis_uom": COST_BASIS_UOM,
                "financial_cost_basis_uom": COST_BASIS_UOM,
                "financial_inventory_model_version": FINANCIAL_INVENTORY_MODEL_VERSION,
                "financial_uom_normalization_valid": uom_valid,
                "financial_inventory_basis": FINANCIAL_INVENTORY_BASIS_GR,
                "financial_eligible_acquisition_count": eligible_count,
                "error": None,
            }},
        )
        if flipped.modified_count != 1:
            raise RuntimeError("version activation CAS failed")
        if prev_active and prev_active != version_id:
            await db[VERSIONS].update_one(
                {"id": prev_active, "status": STATUS_ACTIVE},
                {"$set": {"status": "SUPERSEDED", "superseded_at": now_iso(), "superseded_by": version_id}},
            )
        await _cas_settings(db, prev_active, {
            "key": SETTINGS_KEY,
            "method": METHOD_AVG,
            "recognition_point": RECOG_GR,
            "recognition_label": UI_RECOG[RECOG_GR],
            "active_version": version_id,
            "last_recalculation_status": "SUCCESS",
            "last_recalculation_at": now_iso(),
            "last_recalculation_error": None,
            "updated_at": now_iso(),
            "updated_by": actor_snapshot(user),
        })
        if activate_cutover:
            try:
                activated = await db[CUTOVERS].update_one(
                    {"cutover_id": cutover["cutover_id"], "status": STATUS_BUILDING},
                    {"$set": {
                        "status": STATUS_ACTIVE,
                        "activated_at": now_iso(),
                        "baseline_version_id": version_id,
                        "active_marker": "ACTIVE",
                    }},
                )
            except DuplicateKeyError:
                raise _http(409, "CONFLICT", "Cutover HPP aktif sudah dibuat oleh proses lain.")
            if activated.modified_count != 1:
                raise RuntimeError("cutover activation CAS failed")
        settings = await get_hpp_settings(db)
        version = await db[VERSIONS].find_one({"id": version_id}, {"_id": 0})
        active_count = await db[VERSIONS].count_documents({"status": STATUS_ACTIVE})
        pointed = await db[VERSIONS].find_one({"id": settings.get("active_version")}, {"_id": 0})
        if (
            settings.get("active_version") != version_id
            or not version
            or version.get("status") != STATUS_ACTIVE
            or not pointed
            or pointed.get("status") != STATUS_ACTIVE
            or active_count != 1
        ):
            raise RuntimeError("activation left settings or version status inconsistent")
        return settings
    except HTTPException as exc:
        await _fail_closed(
            db,
            version_id=version_id,
            prev_active=prev_active,
            cutover_id=cutover["cutover_id"],
            message=str(getattr(exc, "detail", exc)),
            activate_cutover=activate_cutover,
        )
        raise
    except Exception as exc:
        await _fail_closed(
            db,
            version_id=version_id,
            prev_active=prev_active,
            cutover_id=cutover["cutover_id"],
            message=str(exc),
            activate_cutover=activate_cutover,
        )
        raise HTTPException(500, {
            "code": STATUS_FAILED,
            "message": "Perhitungan cutover HPP gagal. Versi aktif sebelumnya dipertahankan.",
            "error": str(exc),
        })
    finally:
        await _release_lease(db, CLAIM_RECALC, owner, request_token, "IDLE")


async def confirm_cutover(db, payload: dict, user: Optional[dict] = None) -> dict:
    await ensure_indexes(db)
    await recover_stale_cutover_lease(db)
    current = await get_hpp_settings(db)
    conflict = _settings_conflict(current, payload or {})
    if conflict:
        raise _http(409, "SETTINGS_CONFLICT", conflict)
    request_id = str((payload or {}).get("client_request_id") or "").strip()
    if not request_id:
        raise HTTPException(400, "client_request_id wajib diisi.")
    existing = await db[CUTOVERS].find_one({"client_request_id": request_id}, {"_id": 0})
    if existing:
        if existing.get("status") == STATUS_ACTIVE:
            return {
                "cutover": existing,
                "settings": await get_hpp_settings(db),
                "reconciliation": await list_reconciliation(db, existing["cutover_id"]),
                "idempotent": True,
            }
        raise _http(409, "CONFLICT", "client_request_id sudah dipakai dan belum ACTIVE.", status=existing.get("status"))
    raw_lines = (payload or {}).get("lines") or []
    if not isinstance(raw_lines, list) or not raw_lines:
        raise HTTPException(400, "Minimal satu baris baseline wajib diisi.")
    owner = str((actor_snapshot(user) or {}).get("id") or uuid.uuid4())
    await _acquire_lease(db, CLAIM_CONFIRM, owner, request_id)
    active = await get_active_cutover(db)
    if active:
        await _release_lease(db, CLAIM_CONFIRM, owner, request_id, "IDLE")
        if active.get("client_request_id") == request_id:
            return {
                "cutover": active,
                "settings": await get_hpp_settings(db),
                "reconciliation": await list_reconciliation(db, active["cutover_id"]),
                "idempotent": True,
            }
        raise _http(409, "CONFLICT", "Cutover HPP aktif sudah dibuat oleh proses lain.")
    cutover_id = str(uuid.uuid4())
    created = now_iso()
    cutover = {
        "cutover_id": cutover_id,
        "status": STATUS_BUILDING,
        "method": METHOD_AVG,
        "recognition_point": RECOG_GR,
        "baseline_version_id": None,
        "created_by": actor_snapshot(user),
        "created_at": created,
        "activated_at": None,
        "client_request_id": request_id,
        "lease_owner": owner,
        "lease_expires_at": _expires(),
    }
    success = False
    try:
        try:
            await db[CUTOVERS].insert_one(dict(cutover))
        except DuplicateKeyError:
            raise _http(409, "CONFLICT", "Cutover aktif atau client_request_id bentrok.")
        baselines = []
        for raw in raw_lines:
            if not isinstance(raw, dict):
                raise HTTPException(400, "Baris baseline tidak valid.")
            mid = str(raw.get("master_item_id") or "").strip()
            if not mid:
                raise HTTPException(400, "master_item_id wajib diisi.")
            physical, state = await _read_stock_qty(db, mid)
            baselines.append(_baseline_from_stock(cutover_id, raw, physical, state))
        if baselines:
            await db[BASELINES].insert_many(baselines)
        invalid = [row for row in baselines if row["hpp_status"] != STATUS_OK]
        if invalid:
            await db[CUTOVERS].update_one(
                {"cutover_id": cutover_id, "status": STATUS_BUILDING},
                {"$set": {"status": STATUS_FAILED, "error": "baseline tidak valid"}},
            )
            raise _http(
                409,
                invalid[0]["hpp_status"],
                "Baseline HPP tidak valid. Cutover tidak diaktifkan.",
                rows=[{
                    "master_item_id": row["master_item_id"],
                    "hpp_status": row["hpp_status"],
                    "reconciliation_reason": row["reconciliation_reason"],
                } for row in invalid],
            )
        settings = await rebuild_cutover_valuation(
            db,
            cutover,
            user=user,
            requested_method=METHOD_AVG,
            requested_recognition=RECOG_GR,
            activate_cutover=True,
        )
        saved = await db[CUTOVERS].find_one({"cutover_id": cutover_id}, {"_id": 0})
        if not saved or saved.get("status") != STATUS_ACTIVE or settings.get("active_version") != saved.get("baseline_version_id"):
            await db[CUTOVERS].update_one(
                {"cutover_id": cutover_id},
                {"$set": {"status": STATUS_NEEDS_RECOVERY}},
            )
            raise HTTPException(500, {
                "code": STATUS_NEEDS_RECOVERY,
                "message": "Aktivasi cutover tidak konsisten.",
            })
        success = True
        return {
            "cutover": saved,
            "settings": settings,
            "reconciliation": await list_reconciliation(db, cutover_id),
            "idempotent": False,
        }
    except HTTPException:
        if not success:
            await db[CUTOVERS].update_one(
                {"cutover_id": cutover_id, "status": STATUS_BUILDING},
                {"$set": {"status": STATUS_FAILED}},
            )
        raise
    finally:
        await _release_lease(
            db,
            CLAIM_CONFIRM,
            owner,
            request_id,
            "IDLE" if success else STATUS_FAILED,
        )


def _line_cost(line: dict) -> Optional[float]:
    raw = line.get("unit_price")
    if raw in (None, ""):
        return None
    try:
        cost = money_round(raw)
    except Exception:
        return None
    if _dec(cost) <= 0:
        return None
    return cost


async def _canonical_receipt_line(
    db,
    *,
    receipt: dict,
    line: dict,
    po_lines: Dict[str, dict],
) -> dict:
    """Resolve one GR line to BASE UOM using the canonical accounting helper."""
    po_line_id = str(line.get("purchase_order_line_id") or "").strip()
    po_line = po_lines.get(po_line_id) or {}
    mid = str(line.get("master_item_id") or po_line.get("master_item_id") or "").strip()
    aid = str(line.get("accurate_item_id") or po_line.get("accurate_item_id") or "").strip()
    master = await db.master_items.find_one({"id": mid}, {"_id": 0}) if mid else None
    setting = await db.product_unit_settings.find_one({"master_item_id": mid}, {"_id": 0}) if mid else None
    transaction_uom = line.get("unit") or po_line.get("unit") or (master or {}).get("unit")
    cost = _line_cost(po_line)
    qty = qty_round(line.get("received_qty") or line.get("quantity") or 0)
    common = {
        "master_item_id": mid,
        "accurate_item_id": aid,
        "item_identity": item_identity({"master_item_id": mid, "accurate_item_id": aid}),
        "item_code": line.get("item_code") or po_line.get("item_code") or "",
        "item_name": line.get("item_name") or po_line.get("item_name") or "",
        "purchase_order_id": receipt.get("purchase_order_id"),
        "purchase_order_line_id": po_line_id,
        "transaction_qty": qty,
        "transaction_uom": transaction_uom,
        "transaction_unit_cost": cost,
        "canonical_source_type": CANONICAL_GR,
        "canonical_source_id": receipt.get("id"),
        "financial_inventory_item_key": financial_inventory_item_key({"master_item_id": mid}),
    }
    if cost is None:
        return {
            **common,
            "status": STATUS_NEEDS_COST,
            "reason": STATUS_NEEDS_COST,
            "qty": None,
            "unit_cost": None,
            "total_cost": None,
            "financial_inventory_eligible": False,
            "financial_ineligibility_reason": STATUS_NEEDS_COST,
            "financial_ineligibility_reasons": [STATUS_NEEDS_COST],
        }
    if not master:
        return {
            **common,
            "status": STATUS_NEEDS_UOM,
            "reason": STATUS_NEEDS_UOM,
            "qty": None,
            "unit_cost": None,
            "total_cost": None,
            "financial_inventory_eligible": False,
            "financial_ineligibility_reason": STATUS_NEEDS_UOM,
            "financial_ineligibility_reasons": [STATUS_NEEDS_UOM],
        }
    factor, tx_uom, base_uom, conversion_error = resolve_conversion_from_setting(
        master=master,
        setting=setting,
        transaction_uom=transaction_uom,
    )
    if conversion_error:
        return {
            **common,
            "transaction_uom": tx_uom,
            "base_uom": base_uom,
            "status": STATUS_NEEDS_UOM,
            "reason": STATUS_NEEDS_UOM,
            "qty": None,
            "unit_cost": None,
            "total_cost": None,
            "financial_inventory_eligible": False,
            "financial_ineligibility_reason": STATUS_NEEDS_UOM,
            "financial_ineligibility_reasons": [STATUS_NEEDS_UOM],
        }
    try:
        snapshot = financial_uom_snapshot(
            transaction_qty=qty,
            transaction_uom=tx_uom,
            conversion_factor=factor,
            base_uom=base_uom,
            transaction_unit_cost=cost,
        )
    except InvalidUnitConversionError:
        return {
            **common,
            "transaction_uom": tx_uom,
            "base_uom": base_uom,
            "status": STATUS_NEEDS_UOM,
            "reason": STATUS_NEEDS_UOM,
            "qty": None,
            "unit_cost": None,
            "total_cost": None,
            "financial_inventory_eligible": False,
            "financial_ineligibility_reason": STATUS_NEEDS_UOM,
            "financial_ineligibility_reasons": [STATUS_NEEDS_UOM],
        }
    return {
        **common,
        **snapshot,
        "status": STATUS_PENDING,
        "reason": None,
        "qty": snapshot["base_qty"],
        "unit_cost": snapshot["base_unit_cost"],
        "total_cost": snapshot["total_cost"],
        "financial_inventory_eligible": True,
        "financial_ineligibility_reason": None,
        "financial_ineligibility_reasons": [],
    }


async def recognize_posted_goods_receipt(db, receipt: dict, user: Optional[dict] = None) -> List[dict]:
    """Record future GR cost after the receipt document is stored.

    Does not write product_stock_state or stock_movements. No-op without an
    ACTIVE cutover. Invalid PO unit_price is stored as NEEDS_COST, never Rp0.
    """
    active = await get_active_cutover(db)
    if not active:
        return []
    receipt_id = str(receipt.get("id") or "").strip()
    if not receipt_id:
        return []
    posted_at = now_iso()
    receipt_date = str(receipt.get("receipt_date") or "")[:10]
    po = await db.purchase_orders.find_one(
        {"id": str(receipt.get("purchase_order_id") or "")},
        {"_id": 0, "lines": 1},
    )
    po_lines = {
        str(row.get("line_id") or ""): row
        for row in ((po or {}).get("lines") or [])
    }
    stored: List[dict] = []
    ready_ids: List[str] = []
    processing_token = str(uuid.uuid4())
    busy_valid_event = False
    for line in receipt.get("lines") or []:
        line_id = str(line.get("line_id") or line.get("purchase_order_line_id") or "").strip()
        if not line_id:
            continue
        source_key = f"gr:{receipt_id}:{line_id}"
        existing = await db[EVENTS].find_one({"source_key": source_key}, {"_id": 0})
        reversal = await db[EVENTS].find_one(
            {"source_key": f"gr-reversal:{receipt_id}:{line_id}"},
            {"_id": 0, "status": 1},
        )
        if (
            existing
            and (
                existing.get("status") in {STATUS_RECOGNIZED, STATUS_REVERSED}
                or existing.get("failure_action") == "REVERSE"
                or reversal is not None
            )
        ):
            stored.append(existing)
            continue
        canonical = await _canonical_receipt_line(
            db,
            receipt=receipt,
            line=line,
            po_lines=po_lines,
        )
        event_id = str((existing or {}).get("event_id") or uuid.uuid4())
        doc = {
            "event_id": event_id,
            "source_type": SOURCE_GOODS_RECEIPT,
            "source_key": source_key,
            **canonical,
            "status": canonical["status"],
            "recognized_at": (existing or {}).get("recognized_at") or posted_at,
            "receipt_date": receipt_date,
            "cutover_id": active["cutover_id"],
            "created_at": (existing or {}).get("created_at") or posted_at,
            "updated_at": posted_at,
            "last_error": None,
            "failure_action": None,
        }
        if existing:
            await db[EVENTS].update_one(
                {"source_key": source_key, "status": existing.get("status")},
                {"$set": doc},
            )
        else:
            try:
                await db[EVENTS].insert_one(dict(doc))
            except DuplicateKeyError:
                raced = await db[EVENTS].find_one({"source_key": source_key}, {"_id": 0})
                if raced:
                    stored.append(raced)
                continue
        if canonical["status"] == STATUS_PENDING:
            claimed = await db[EVENTS].find_one_and_update(
                {
                    "event_id": event_id,
                    "status": STATUS_PENDING,
                    "$or": [
                        {"processing_token": {"$exists": False}},
                        {"processing_token": None},
                        {"processing_lease_expires_at": {"$lt": posted_at}},
                    ],
                },
                {"$set": {
                    "processing_token": processing_token,
                    "processing_lease_expires_at": _expires(),
                    "updated_at": posted_at,
                }},
                return_document=ReturnDocument.AFTER,
            )
            if claimed:
                ready_ids.append(event_id)
            else:
                busy_valid_event = True
        stored.append({key: value for key, value in doc.items() if key != "_id"})
    if busy_valid_event and not ready_ids:
        raise _http(
            409,
            STATUS_PENDING,
            "Recognition HPP untuk penerimaan ini sedang diproses. Retry aman.",
        )
    if ready_ids:
        await db[EVENTS].update_many(
            {
                "event_id": {"$in": ready_ids},
                "status": STATUS_PENDING,
                "processing_token": processing_token,
            },
            {"$set": {"status": STATUS_RECOGNIZED, "recognized_at": posted_at, "updated_at": posted_at}},
        )
        try:
            await rebuild_cutover_valuation(
                db,
                active,
                user=user,
                requested_method=METHOD_AVG,
                requested_recognition=RECOG_GR,
                activate_cutover=False,
            )
        except Exception as exc:
            await db[EVENTS].update_many(
                {
                    "event_id": {"$in": ready_ids},
                    "status": STATUS_RECOGNIZED,
                    "processing_token": processing_token,
                },
                {"$set": {
                    "status": STATUS_FAILED,
                    "failure_action": "RECOGNIZE",
                    "last_error": str(exc),
                    "updated_at": now_iso(),
                }, "$unset": {
                    "processing_token": "",
                    "processing_lease_expires_at": "",
                }},
            )
            raise
        await db[EVENTS].update_many(
            {
                "event_id": {"$in": ready_ids},
                "status": STATUS_RECOGNIZED,
                "processing_token": processing_token,
            },
            {
                "$set": {"last_error": None, "failure_action": None, "updated_at": now_iso()},
                "$unset": {
                    "processing_token": "",
                    "processing_lease_expires_at": "",
                },
            },
        )
    return await db[EVENTS].find(
        {"source_key": {"$in": [
            f"gr:{receipt_id}:{str(line.get('line_id') or line.get('purchase_order_line_id') or '').strip()}"
            for line in receipt.get("lines") or []
            if str(line.get("line_id") or line.get("purchase_order_line_id") or "").strip()
        ]}},
        {"_id": 0},
    ).to_list(5000)


async def reverse_goods_receipt_hpp(
    db,
    receipt: dict,
    user: Optional[dict] = None,
) -> List[dict]:
    """Audit and reverse GR HPP exactly once without deleting the source event."""
    receipt_id = str(receipt.get("id") or "").strip()
    if not receipt_id:
        return []
    active = await get_active_cutover(db)
    actor = actor_snapshot(user)
    stamp = now_iso()
    reversal_ids: List[str] = []
    original_ids: List[str] = []
    requires_rebuild = False
    for line in receipt.get("lines") or []:
        line_id = str(line.get("line_id") or line.get("purchase_order_line_id") or "").strip()
        if not line_id:
            continue
        original_key = f"gr:{receipt_id}:{line_id}"
        original = await db[EVENTS].find_one({"source_key": original_key}, {"_id": 0})
        if not original:
            continue
        reversal_key = f"gr-reversal:{receipt_id}:{line_id}"
        reversal = await db[EVENTS].find_one({"source_key": reversal_key}, {"_id": 0})
        if reversal and reversal.get("status") == STATUS_REVERSED:
            continue
        was_recognized = (
            original.get("status") == STATUS_RECOGNIZED
            or (
                original.get("status") == STATUS_FAILED
                and original.get("failure_action") == "REVERSE"
                and original.get("reversal_was_recognized") is True
            )
        )
        reversal_id = str((reversal or {}).get("event_id") or uuid.uuid4())
        reversal_doc = {
            "event_id": reversal_id,
            "source_type": SOURCE_GOODS_RECEIPT_REVERSAL,
            "source_key": reversal_key,
            "original_event_id": original.get("event_id"),
            "original_source_key": original_key,
            "source_receipt_id": receipt_id,
            "source_line_id": line_id,
            "master_item_id": original.get("master_item_id"),
            "item_identity": original.get("item_identity"),
            "qty": original.get("qty"),
            "unit_cost": original.get("unit_cost"),
            "total_cost": original.get("total_cost"),
            "status": STATUS_PENDING,
            "recognized_at": stamp,
            "receipt_date": original.get("receipt_date"),
            "cutover_id": original.get("cutover_id"),
            "created_at": (reversal or {}).get("created_at") or stamp,
            "updated_at": stamp,
            "created_by": actor,
        }
        if reversal:
            await db[EVENTS].update_one(
                {"source_key": reversal_key, "status": reversal.get("status")},
                {"$set": reversal_doc},
            )
        else:
            try:
                await db[EVENTS].insert_one(dict(reversal_doc))
            except DuplicateKeyError:
                continue
        await db[EVENTS].update_one(
            {"event_id": original["event_id"]},
            {"$set": {
                "status": STATUS_REVERSED,
                "reversed_at": stamp,
                "reversed_by": actor,
                "reversal_event_id": reversal_id,
                "reversal_was_recognized": was_recognized,
                "failure_action": None,
                "last_error": None,
                "updated_at": stamp,
            }},
        )
        reversal_ids.append(reversal_id)
        original_ids.append(original["event_id"])
        requires_rebuild = requires_rebuild or was_recognized
    if not reversal_ids:
        return []
    try:
        if requires_rebuild and active:
            await rebuild_cutover_valuation(
                db,
                active,
                user=user,
                requested_method=METHOD_AVG,
                requested_recognition=RECOG_GR,
                activate_cutover=False,
            )
    except Exception as exc:
        await db[EVENTS].update_many(
            {"event_id": {"$in": original_ids}},
            {"$set": {
                "status": STATUS_FAILED,
                "failure_action": "REVERSE",
                "last_error": str(exc),
                "updated_at": now_iso(),
            }},
        )
        await db[EVENTS].update_many(
            {"event_id": {"$in": reversal_ids}},
            {"$set": {
                "status": STATUS_FAILED,
                "failure_action": "REVERSE",
                "last_error": str(exc),
                "updated_at": now_iso(),
            }},
        )
        raise
    await db[EVENTS].update_many(
        {"event_id": {"$in": reversal_ids}, "status": STATUS_PENDING},
        {"$set": {"status": STATUS_REVERSED, "updated_at": now_iso()}},
    )
    return await db[EVENTS].find(
        {"event_id": {"$in": reversal_ids}},
        {"_id": 0},
    ).to_list(5000)
