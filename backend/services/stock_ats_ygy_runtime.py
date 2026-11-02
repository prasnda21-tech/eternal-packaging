"""YGY-MAIN Accurate ATS cutover — read overlay for Stock Saat Ini.

Does NOT write physical_stock_balances, ledger, or activate production.
Baseline draft is Owner-defined free blank stock (not physical/opname).
Overlay READY only when evidence_complete and projector succeeds.
Otherwise fail closed with explicit coverage gaps (no invented ATS).

Mode B attribution uses services.mode_b_legacy_demand (Accurate Live map
and/or historical digit rules from active app_settings) — never hardcoded
branch token sets in this module.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from services.stock_ats_cutover import (
    Baseline,
    CutoverError,
    Event,
    Reservation,
    project_available,
    quantity,
    timestamp,
)

YGY_WAREHOUSE_ID = "b3d0bf36-1140-4eac-b0f7-b5c924ed0d24"
YGY_WAREHOUSE_CODE = "YGY-MAIN"
YGY_BRANCH_ID = "5c92529c-3df8-4d9f-b290-a5c94fa51903"

CUTOFF_AT = "2026-11-02T23:22:00+07:00"
CUTOFF_AT_UTC = "2026-11-02T16:22:00Z"

LEDGER_COLLECTION = "physical_stock_ledger"
MOVEMENT_OPENING_BALANCE = "OPENING_BALANCE"
MOVEMENT_PURCHASE_RECEIPT = "PURCHASE_RECEIPT"
MOVEMENT_PURCHASE_RETURN = "PURCHASE_RETURN"
MOVEMENT_SALES_DELIVERY = "SALES_DELIVERY"
MOVEMENT_STOCK_OPNAME_ADJUSTMENT = "STOCK_OPNAME_ADJUSTMENT"
MOVEMENT_MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
MOVEMENT_WAREHOUSE_TRANSFER_OUT = "WAREHOUSE_TRANSFER_OUT"
MOVEMENT_WAREHOUSE_TRANSFER_IN = "WAREHOUSE_TRANSFER_IN"
MOVEMENT_REVERSAL = "REVERSAL"
MOVEMENT_PRODUCTION_CONSUMPTION = "PRODUCTION_CONSUMPTION"
MOVEMENT_PRODUCTION_OUTPUT = "PRODUCTION_OUTPUT"

# Post-cutoff ledger kinds with explicit ATS policy in this adapter.
_MAPPED_LEDGER_KINDS = frozenset({
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_REVERSAL,
})
# Present on YGY WH after cutoff → evidence incomplete until Owner maps them.
_UNMAPPED_LEDGER_KINDS = frozenset({
    MOVEMENT_PURCHASE_RETURN,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_WAREHOUSE_TRANSFER_OUT,
    MOVEMENT_WAREHOUSE_TRANSFER_IN,
    MOVEMENT_PRODUCTION_CONSUMPTION,
    MOVEMENT_PRODUCTION_OUTPUT,
    MOVEMENT_OPENING_BALANCE,
})

REASON_ATS_COVERAGE_GAP = "ATS_EVENT_COVERAGE_GAP"
REASON_ATS_NOT_IN_BASELINE = "ATS_NOT_IN_BASELINE_FILE"
REASON_ATS_INACTIVE_MASTER = "ATS_INACTIVE_MASTER_EXCLUDED"
REASON_ATS_MODE_OFF = "ATS_CUTOVER_MODE_OFF"
REASON_ATS_WRONG_WAREHOUSE = "ATS_WAREHOUSE_NOT_YGY_MAIN"
SOURCE_ATS = "ACCURATE_ATS_CUTOVER"

# off | draft_read | activated — activated still requires evidence_complete.
def ats_ygy_mode() -> str:
    return str(os.environ.get("ETERNAL_ATS_YGY_MODE") or "off").strip().lower()


def ats_ygy_enabled() -> bool:
    return ats_ygy_mode() in ("draft_read", "activated")


def default_baseline_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "ats_baseline_ygy_main_draft_162.json"


_BASELINE_CACHE: Optional[Dict[str, Any]] = None


def load_baseline_manifest(path: Optional[Path] = None) -> Dict[str, Any]:
    global _BASELINE_CACHE
    p = path or default_baseline_path()
    if _BASELINE_CACHE is not None and path is None:
        return _BASELINE_CACHE
    raw = json.loads(Path(p).read_text())
    if str(raw.get("warehouse_id") or "") != YGY_WAREHOUSE_ID:
        raise CutoverError("Baseline warehouse_id must be YGY-MAIN")
    if str(raw.get("cutoff_at") or "") != CUTOFF_AT:
        raise CutoverError("Baseline cutoff_at must match Owner decision")
    # Integrity: recompute sha over sorted eligible identity if file embeds source_sha256
    sha = str(raw.get("source_sha256") or "")
    if len(sha) != 64:
        raise CutoverError("Baseline source_sha256 required")
    if path is None:
        _BASELINE_CACHE = raw
    return raw


def eligible_rows(manifest: Optional[Dict[str, Any]] = None) -> Dict[str, dict]:
    man = manifest or load_baseline_manifest()
    out: Dict[str, dict] = {}
    for row in man.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if not row.get("eligible_master"):
            continue
        mid = str(row.get("master_item_id") or "").strip()
        if not mid:
            continue
        out[mid] = row
    return out


def baseline_for_master(master_item_id: str, *, batch_id: str = "ygy-ats-2026-11-02") -> Optional[Baseline]:
    rows = eligible_rows()
    mid = str(master_item_id or "").strip()
    row = rows.get(mid)
    if not row:
        return None
    man = load_baseline_manifest()
    return Baseline(
        batch_id=batch_id,
        warehouse_id=YGY_WAREHOUSE_ID,
        master_item_id=mid,
        unit=str(row.get("satuan") or "PCS"),
        available_qty=quantity(row.get("accurate_ats")),
        cutoff_at=str(man.get("cutoff_at") or CUTOFF_AT),
        source_sha256=str(man.get("source_sha256")),
    )


def order_matches_ygy_mode_b(order: dict, settings: Optional[dict] = None) -> bool:
    """True when Mode B attributes the order to YGY org branch.

    Uses official order_attributed_to_branch + active/sanitized settings.
    Without settings, only exact org UUID match counts (fail-closed).
    """
    from services.mode_b_legacy_demand import order_attributed_to_branch

    return order_attributed_to_branch(order, YGY_BRANCH_ID, settings)


def coverage_gaps_for_live_ro(*, has_stock_allocations: bool, event_stream_complete: bool) -> List[str]:
    gaps: List[str] = []
    if not has_stock_allocations:
        gaps.append(
            "LIVE_RO_ZERO_STOCK_ALLOCATIONS: cannot WH-scope open PO from allocations; "
            "Owner ATS baseline adopts free stock at cutoff — pre-cutoff PO must not re-cut ATS "
            "unless explicit opening Reservation keys are supplied"
        )
    if not event_stream_complete:
        gaps.append(
            "POST_CUTOFF_EVENT_STREAM_INCOMPLETE: projector refuses READY until canonical "
            "RESERVE/SHIP_RESERVED/RELEASE/RECEIVE_FREE/ISSUE_FREE/REVERSE coverage is verified"
        )
    gaps.append(
        "ADOPTION_POLICY_PROPOSAL: treat Accurate ATS @ cutoff as free blank baseline; "
        "reservations_at_cutoff empty unless Owner supplies WH-proven opening reservations; "
        "map only post-cutoff mutations with warehouse_id==YGY-MAIN"
    )
    gaps.append(
        "OLD_PO_CANCEL_SKIP_CONSEQUENCE: refusing RELEASE on pre-cutoff cancel avoids "
        "inflating ATS, but free stock may remain UNDERSTATED because commitment was "
        "already absorbed into the Owner baseline and is not restored by cancel"
    )
    return gaps


def _reservation_key(order_id: str, line: dict, line_idx: int) -> str:
    lid = str(line.get("line_id") or line.get("id") or "").strip()
    if lid:
        return f"{order_id}:{lid}"
    return f"{order_id}:idx:{int(line_idx)}"


def _order_cancelled(order: dict) -> bool:
    if order.get("cancelled") or order.get("is_cancelled"):
        return True
    status = str(order.get("status") or order.get("status_label") or "").strip().lower()
    return status in ("cancelled", "dibatalkan", "void")


def _line_cancelled(line: dict) -> bool:
    return bool(line.get("cancelled") or line.get("is_cancelled"))


def _parse_event_time(value: Any) -> Optional[Any]:
    from services.mode_b_legacy_demand import parse_iso_datetime

    return parse_iso_datetime(value)


def _qty_abs(value: Any) -> Decimal:
    try:
        qty = quantity(value)
    except CutoverError:
        return Decimal(0)
    return abs(qty)


def _empty_master_pack() -> Dict[str, Any]:
    return {
        "events": [],
        "reservations_at_cutoff": [],
        "evidence_complete": True,
        "gaps": [],
        "skipped": [],
    }


def _append_gap(pack: dict, message: str) -> None:
    if message not in pack["gaps"]:
        pack["gaps"].append(message)
    pack["evidence_complete"] = False


def _append_skip(pack: dict, message: str) -> None:
    pack["skipped"].append(message)


def _norm_unit(value: Any) -> str:
    return str(value or "").strip().upper()


def _line_transaction_unit(line: dict) -> str:
    return _norm_unit(
        line.get("unit")
        or line.get("satuan")
        or line.get("transaction_uom")
        or line.get("uom")
    )


def _ygy_allocation_qty(line: dict, *, warehouse_id: str, baseline_unit: str) -> Dict[str, Any]:
    """Return YGY-MAIN allocation qty only when unit/warehouse evidence is clear.

    Does not invent YGY scope from branch attribution alone.
    """
    from services.stock_allocation import is_inventory_line, qty_num

    if not is_inventory_line(line):
        return {"qty": Decimal(0), "gap": None, "skip_non_inventory": True}
    allocs = line.get("stock_allocations")
    if not isinstance(allocs, list) or not allocs:
        return {
            "qty": Decimal(0),
            "gap": "RESERVE_NO_WAREHOUSE_ALLOCATION: post-cutoff inventory line "
            "lacks stock_allocations — refuse inventing YGY-MAIN reserve",
            "skip_non_inventory": False,
        }
    line_unit = _line_transaction_unit(line)
    base_unit = _norm_unit(baseline_unit)
    if line_unit and base_unit and line_unit != base_unit:
        return {
            "qty": Decimal(0),
            "gap": f"UNIT_MISMATCH_LINE:{line_unit}!={base_unit}",
            "skip_non_inventory": False,
        }
    ygy_qty = Decimal(0)
    other_wh = False
    ambiguous = False
    for entry in allocs:
        if not isinstance(entry, dict):
            ambiguous = True
            continue
        wid = str(entry.get("warehouse_id") or "").strip()
        if not wid:
            ambiguous = True
            continue
        alloc_unit = _norm_unit(entry.get("unit") or line_unit or base_unit)
        if base_unit and alloc_unit and alloc_unit != base_unit:
            return {
                "qty": Decimal(0),
                "gap": f"UNIT_MISMATCH_ALLOCATION:{alloc_unit}!={base_unit}",
                "skip_non_inventory": False,
            }
        qty = qty_num(entry.get("qty") if entry.get("qty") is not None else entry.get("quantity"))
        if qty <= 0:
            continue
        if wid == warehouse_id:
            ygy_qty += quantity(qty)
        else:
            other_wh = True
    if ambiguous and ygy_qty <= 0:
        return {
            "qty": Decimal(0),
            "gap": "ALLOCATION_AMBIGUOUS: missing/invalid warehouse_id on allocation rows",
            "skip_non_inventory": False,
        }
    if ygy_qty > 0:
        return {"qty": ygy_qty, "gap": None, "skip_non_inventory": False}
    if other_wh:
        # Explicit other-warehouse demand — do not cut YGY ATS.
        return {"qty": Decimal(0), "gap": None, "skip_non_inventory": False, "other_wh_only": True}
    return {
        "qty": Decimal(0),
        "gap": "RESERVE_NO_YGY_ALLOCATION_QTY: allocations present but none for YGY-MAIN",
        "skip_non_inventory": False,
    }


async def collect_ats_stream_for_ygy(
    db,
    *,
    master_item_ids: Sequence[str],
    as_of: str,
    warehouse_id: str = YGY_WAREHOUSE_ID,
    branch_id: str = YGY_BRANCH_ID,
) -> Dict[str, Any]:
    """Build post-cutoff ATS events from official orders + YGY WH ledger.

    RESERVE requires YGY-MAIN stock_allocations evidence (qty + matching unit);
    branch attribution alone never invents a YGY warehouse reserve.

    Adoption: reservations_at_cutoff is always empty unless a future Owner
    snapshot supplies WH-proven keys. Pre-cutoff PO ship/cancel never become
    ISSUE_FREE / RELEASE (would re-cut or inflate baseline). Skipping old-PO
    cancel does NOT prove free stock is correct — baseline may stay understated.

    REVERSAL maps any previously mapped event (SHIP / PURCHASE_RECEIPT / …);
    unknown reference_id is an explicit coverage gap.

    evidence_complete is True only when every post-cutoff YGY ledger row for
    that master is mapped or intentionally skipped under adoption — never
    forced True to hide unmapped movements.
    """
    from services.mode_b_legacy_demand import (
        filter_orders_for_branch,
        get_mode_b_legacy_demand_settings,
        mongo_order_branch_clauses,
    )

    mids = [str(m).strip() for m in master_item_ids if str(m or "").strip()]
    wid = str(warehouse_id or "").strip()
    bid = str(branch_id or "").strip() or YGY_BRANCH_ID
    as_of_dt = timestamp(as_of)
    cutoff_dt = timestamp(CUTOFF_AT)

    by_master: Dict[str, Dict[str, Any]] = {mid: _empty_master_pack() for mid in mids}
    if wid != YGY_WAREHOUSE_ID:
        for pack in by_master.values():
            _append_gap(pack, f"ATS_WAREHOUSE_SCOPE: collector refused non-YGY warehouse {wid}")
        return {
            "by_master": by_master,
            "reservations_at_cutoff": [],
            "mode_b_settings": None,
            "cutoff_at": CUTOFF_AT,
            "as_of": as_of,
            "warehouse_id": wid,
        }

    if not mids:
        return {
            "by_master": {},
            "reservations_at_cutoff": [],
            "mode_b_settings": None,
            "cutoff_at": CUTOFF_AT,
            "as_of": as_of,
            "warehouse_id": wid,
        }

    settings = await get_mode_b_legacy_demand_settings(db)
    elig = eligible_rows()
    unit_by_mid = {
        mid: str((elig.get(mid) or {}).get("satuan") or "PCS")
        for mid in mids
    }
    # Master item_type for SERVICE/NON_INVENTORY gating when line omits type.
    master_type_docs = await db.master_items.find(
        {"id": {"$in": mids}},
        {"_id": 0, "id": 1, "item_type": 1, "unit": 1},
    ).to_list(max(len(mids), 1))
    master_type_by_id = {
        str(d.get("id") or "").strip(): str(d.get("item_type") or "").strip().upper()
        for d in master_type_docs
        if str(d.get("id") or "").strip()
    }

    order_query: Dict[str, Any] = {"$or": mongo_order_branch_clauses(bid, settings)}
    raw_orders = await db.orders.find(order_query, {"_id": 0}).to_list(20000)
    orders = filter_orders_for_branch(raw_orders, bid, settings)
    order_by_id = {
        str(o.get("id") or "").strip(): o
        for o in orders
        if str(o.get("id") or "").strip()
    }

    # Post-cutoff reservation identities — only YGY-MAIN allocation evidence.
    post_cutoff_keys: Dict[str, Dict[str, Any]] = {}
    raw_events: List[dict] = []  # pending Event fields before sequence assign

    for order in orders:
        oid = str(order.get("id") or "").strip()
        if not oid:
            continue
        created = _parse_event_time(order.get("created_at") or order.get("imported_at"))
        if created is None:
            for idx, line in enumerate(order.get("line_items") or []):
                if not isinstance(line, dict):
                    continue
                mid = str(line.get("master_item_id") or "").strip()
                if mid in by_master:
                    _append_gap(
                        by_master[mid],
                        f"ORDER_TIME_UNPARSEABLE:{oid}: cannot classify pre/post cutoff",
                    )
            continue
        is_post = created > cutoff_dt
        for idx, line in enumerate(order.get("line_items") or []):
            if not isinstance(line, dict):
                continue
            mid = str(line.get("master_item_id") or "").strip()
            if mid not in by_master:
                continue
            pack = by_master[mid]
            # Enrich item_type from master when line omits it.
            line_for_type = dict(line)
            if not str(line_for_type.get("item_type") or "").strip():
                mt = master_type_by_id.get(mid) or ""
                if mt:
                    line_for_type["item_type"] = mt
            if not is_post:
                continue
            baseline_unit = unit_by_mid.get(mid) or "PCS"
            alloc = _ygy_allocation_qty(
                line_for_type, warehouse_id=wid, baseline_unit=baseline_unit,
            )
            if alloc.get("skip_non_inventory"):
                _append_skip(
                    pack,
                    f"SKIP_NON_INVENTORY_LINE:{oid}:{_reservation_key(oid, line, idx)}",
                )
                continue
            if alloc.get("gap"):
                _append_gap(pack, f"{alloc['gap']}:order={oid}")
                continue
            reserved_qty = quantity(alloc["qty"])
            if reserved_qty <= 0:
                if alloc.get("other_wh_only"):
                    _append_skip(
                        pack,
                        f"SKIP_OTHER_WAREHOUSE_ALLOCATION:{oid}:"
                        f"{_reservation_key(oid, line, idx)}: no YGY-MAIN cut",
                    )
                continue
            key = _reservation_key(oid, line, idx)
            unit = baseline_unit
            post_cutoff_keys[key] = {
                "master_item_id": mid,
                "reserved_qty": reserved_qty,
                "unit": unit,
                "order_id": oid,
                "created_at": created.isoformat(),
            }
            raw_created = str(order.get("created_at") or order.get("imported_at") or "").strip()
            occurred = raw_created or created.isoformat()
            raw_events.append({
                "event_id": f"reserve:{key}",
                "warehouse_id": wid,
                "master_item_id": mid,
                "unit": unit,
                "occurred_at": occurred,
                "kind": mapped_kind("NEW_PO_LINE_RESERVE"),
                "qty": reserved_qty,
                "reservation_key": key,
                "reverses_event_id": "",
                "business_code": "NEW_PO_LINE_RESERVE",
            })

    # YGY warehouse ledger after cutoff (canonical physical mutations).
    ledger_docs = await db[LEDGER_COLLECTION].find(
        {
            "warehouse_id": wid,
            "master_item_id": {"$in": mids},
        },
        {"_id": 0},
    ).to_list(50000)

    # movement_id → mapped ATS event_id (ship / recv / …)
    mapped_event_by_movement: Dict[str, str] = {}
    # Ships for RELEASE net calc: list of {movement_id, key, qty, occurred_at}
    ship_rows: List[dict] = []
    reversal_docs: List[dict] = []

    def _ledger_in_window(doc: dict) -> Optional[tuple]:
        mid = str(doc.get("master_item_id") or "").strip()
        if mid not in by_master:
            return None
        pack = by_master[mid]
        occurred_raw = str(doc.get("occurred_at") or doc.get("created_at") or "").strip()
        occurred_dt = _parse_event_time(occurred_raw)
        if occurred_dt is None:
            _append_gap(pack, f"LEDGER_TIME_UNPARSEABLE:{doc.get('id')}")
            return None
        if occurred_dt <= cutoff_dt or occurred_dt > as_of_dt:
            return None
        movement_id = str(doc.get("id") or "").strip()
        mtype = str(doc.get("movement_type") or "").strip()
        if not movement_id:
            _append_gap(pack, f"LEDGER_MISSING_ID:{mtype}")
            return None
        return mid, pack, unit_by_mid.get(mid) or "PCS", mtype, occurred_raw, movement_id, occurred_dt

    def _ledger_unit_ok(pack: dict, meta: dict, baseline_unit: str, movement_id: str) -> bool:
        tx_unit = _norm_unit(meta.get("unit") or meta.get("satuan") or meta.get("uom"))
        base = _norm_unit(baseline_unit)
        if tx_unit and base and tx_unit != base:
            _append_gap(
                pack,
                f"UNIT_MISMATCH_LEDGER:{movement_id}:{tx_unit}!={base}",
            )
            return False
        return True

    # Pass 1: non-reversal mapped/unmapped ledger rows.
    for doc in ledger_docs:
        mtype = str(doc.get("movement_type") or "").strip()
        if mtype == MOVEMENT_REVERSAL:
            reversal_docs.append(doc)
            continue
        parsed = _ledger_in_window(doc)
        if parsed is None:
            continue
        mid, pack, unit, mtype, occurred_raw, movement_id, occurred_dt = parsed
        meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        qty = _qty_abs(doc.get("qty_delta"))

        if mtype in _UNMAPPED_LEDGER_KINDS:
            _append_gap(
                pack,
                f"UNMAPPED_LEDGER:{mtype}:{movement_id}: explicit ATS policy required",
            )
            continue
        if mtype not in _MAPPED_LEDGER_KINDS:
            _append_gap(
                pack,
                f"UNKNOWN_LEDGER:{mtype or 'EMPTY'}:{movement_id}: unclassified movement",
            )
            continue
        if not _ledger_unit_ok(pack, meta, unit, movement_id):
            continue

        if mtype == MOVEMENT_PURCHASE_RECEIPT:
            if qty <= 0:
                _append_gap(pack, f"PURCHASE_RECEIPT_BAD_QTY:{movement_id}")
                continue
            recv_eid = f"recv:{movement_id}"
            mapped_event_by_movement[movement_id] = recv_eid
            raw_events.append({
                "event_id": recv_eid,
                "warehouse_id": wid,
                "master_item_id": mid,
                "unit": unit,
                "occurred_at": occurred_raw,
                "kind": mapped_kind("PURCHASE_RECEIPT_UNRESERVED"),
                "qty": qty,
                "reservation_key": "",
                "reverses_event_id": "",
                "business_code": "PURCHASE_RECEIPT_UNRESERVED",
            })
            continue

        if mtype == MOVEMENT_SALES_DELIVERY:
            if qty <= 0:
                _append_gap(pack, f"SALES_DELIVERY_BAD_QTY:{movement_id}")
                continue
            oid = str(meta.get("order_id") or "").strip()
            line_id = str(meta.get("line_id") or "").strip()
            raw_idx = meta.get("line_idx")
            key = ""
            if oid and line_id:
                key = f"{oid}:{line_id}"
            elif oid and raw_idx is not None and str(raw_idx).strip() != "":
                try:
                    key = f"{oid}:idx:{int(raw_idx)}"
                except (TypeError, ValueError):
                    key = ""
            if key and key in post_cutoff_keys:
                ship_eid = f"ship:{movement_id}"
                mapped_event_by_movement[movement_id] = ship_eid
                ship_rows.append({
                    "movement_id": movement_id,
                    "event_id": ship_eid,
                    "key": key,
                    "qty": qty,
                    "occurred_at": occurred_dt,
                    "occurred_raw": occurred_raw,
                })
                raw_events.append({
                    "event_id": ship_eid,
                    "warehouse_id": wid,
                    "master_item_id": mid,
                    "unit": unit,
                    "occurred_at": occurred_raw,
                    "kind": mapped_kind("SHIP_AGAINST_RESERVATION"),
                    "qty": qty,
                    "reservation_key": key,
                    "reverses_event_id": "",
                    "business_code": "SHIP_AGAINST_RESERVATION",
                })
                continue

            # Pre-cutoff / adoption: do NOT ISSUE_FREE (would re-cut baseline).
            order = order_by_id.get(oid) if oid else None
            if order is not None:
                created = _parse_event_time(
                    order.get("created_at") or order.get("imported_at")
                )
                if created is not None and created <= cutoff_dt:
                    _append_skip(
                        pack,
                        f"SKIP_OLD_PO_SHIP:{movement_id}:order={oid}:key={key or '-'}",
                    )
                    continue
            _append_gap(
                pack,
                f"UNATTRIBUTED_OR_UNRESERVED_SHIP:{movement_id}:order={oid or '-'}"
                f": refuse inferred ISSUE_FREE",
            )
            continue

    # Pass 2: reversals of ANY mapped event (ship, purchase receipt, …).
    # Unknown reference_id → explicit gap (never silent skip).
    reverse_at_by_movement: Dict[str, Any] = {}
    for doc in reversal_docs:
        parsed = _ledger_in_window(doc)
        if parsed is None:
            continue
        mid, pack, unit, _mtype, occurred_raw, movement_id, occurred_dt = parsed
        ref = str(doc.get("reference_id") or "").strip()
        if not ref:
            _append_gap(pack, f"REVERSAL_MISSING_REFERENCE:{movement_id}")
            continue
        target_eid = mapped_event_by_movement.get(ref)
        if target_eid:
            reverse_at_by_movement[ref] = occurred_dt
            raw_events.append({
                "event_id": f"rev:{movement_id}",
                "warehouse_id": wid,
                "master_item_id": mid,
                "unit": unit,
                "occurred_at": occurred_raw,
                "kind": mapped_kind("LEDGER_REVERSAL"),
                "qty": Decimal(0),
                "reservation_key": "",
                "reverses_event_id": target_eid,
                "business_code": "LEDGER_REVERSAL",
            })
        else:
            _append_gap(
                pack,
                f"REVERSAL_UNKNOWN_REFERENCE:{movement_id}:ref={ref}: "
                "not mapped to ATS event (incl. skipped old-PO / unmapped kinds)",
            )

    # Cancel / release remaining only for post-cutoff YGY reservations.
    # Remaining = reserved − NET shipped at/before cancel (ships reversed at/before
    # cancel do not consume reservation).
    for key, info in post_cutoff_keys.items():
        mid = info["master_item_id"]
        pack = by_master[mid]
        order = order_by_id.get(info["order_id"])
        if order is None:
            continue
        cancelled = _order_cancelled(order)
        line_cancelled = False
        cancel_time_raw = str(
            order.get("cancelled_at") or order.get("updated_at") or ""
        ).strip()
        for idx, line in enumerate(order.get("line_items") or []):
            if not isinstance(line, dict):
                continue
            if _reservation_key(info["order_id"], line, idx) != key:
                continue
            if _line_cancelled(line):
                line_cancelled = True
                cancel_time_raw = str(
                    line.get("cancelled_at")
                    or order.get("cancelled_at")
                    or order.get("updated_at")
                    or ""
                ).strip()
            break
        if not (cancelled or line_cancelled):
            continue
        cancel_dt = _parse_event_time(cancel_time_raw)
        if cancel_dt is None or cancel_dt <= cutoff_dt or cancel_dt > as_of_dt:
            if cancel_dt is None and (cancelled or line_cancelled):
                _append_gap(
                    pack,
                    f"CANCEL_TIME_UNPARSEABLE:{key}: cannot RELEASE remaining",
                )
            continue
        net_shipped = Decimal(0)
        for ship in ship_rows:
            if ship["key"] != key:
                continue
            if ship["occurred_at"] > cancel_dt:
                continue
            rev_at = reverse_at_by_movement.get(ship["movement_id"])
            if rev_at is not None and rev_at <= cancel_dt:
                continue  # reversed before/at cancel → not consuming reservation
            net_shipped += quantity(ship["qty"])
        remaining = quantity(info["reserved_qty"]) - net_shipped
        if remaining < 0:
            _append_gap(
                pack,
                f"OVERSHIP_VS_RESERVE:{key}:net_shipped={net_shipped}:"
                f"reserved={info['reserved_qty']}",
            )
            continue
        if remaining == 0:
            continue
        raw_events.append({
            "event_id": f"release:{key}",
            "warehouse_id": wid,
            "master_item_id": mid,
            "unit": info["unit"],
            "occurred_at": cancel_time_raw,
            "kind": mapped_kind("CANCEL_OR_RELEASE_REMAINING"),
            "qty": remaining,
            "reservation_key": key,
            "reverses_event_id": "",
            "business_code": "CANCEL_OR_RELEASE_REMAINING",
        })

    # Pre-cutoff cancel: refuse RELEASE inflate. Explicit consequence: free ATS
    # may remain understated because baseline already absorbed old commitment.
    for order in orders:
        oid = str(order.get("id") or "").strip()
        created = _parse_event_time(order.get("created_at") or order.get("imported_at"))
        if created is None or created > cutoff_dt:
            continue
        if not (_order_cancelled(order) or any(
            isinstance(ln, dict) and _line_cancelled(ln)
            for ln in (order.get("line_items") or [])
        )):
            continue
        cancel_dt = _parse_event_time(
            order.get("cancelled_at") or order.get("updated_at")
        )
        if cancel_dt is None or cancel_dt <= cutoff_dt or cancel_dt > as_of_dt:
            continue
        for idx, line in enumerate(order.get("line_items") or []):
            if not isinstance(line, dict):
                continue
            mid = str(line.get("master_item_id") or "").strip()
            if mid not in by_master:
                continue
            key = _reservation_key(oid, line, idx)
            if key in post_cutoff_keys:
                continue
            msg = (
                f"SKIP_OLD_PO_CANCEL_NO_OPENING_RESERVATION:{key}: refuse RELEASE inflate; "
                "CONSEQUENCE: free ATS may remain UNDERSTATED vs true free stock — "
                "pre-cutoff commitment was adopted into baseline and cancel cannot restore it"
            )
            _append_skip(by_master[mid], msg)
            # Do not mark READY while free stock may be understated.
            _append_gap(
                by_master[mid],
                f"OLD_PO_CANCEL_UNDERSTATED_FREE_STOCK:{key}: "
                "no proven opening reservation to RELEASE; ATS remains UNAVAILABLE",
            )

    # Assign canonical sequences per master (time, event_id).
    events_by_mid: Dict[str, List[dict]] = {mid: [] for mid in mids}
    for raw in raw_events:
        events_by_mid.setdefault(raw["master_item_id"], []).append(raw)

    for mid, pending in events_by_mid.items():
        pack = by_master[mid]
        pending_sorted = sorted(
            pending,
            key=lambda e: (timestamp(e["occurred_at"]), e["event_id"]),
        )
        built: List[Event] = []
        for seq, raw in enumerate(pending_sorted):
            # Drop events outside (cutoff, as_of] — projector would ignore too,
            # but keep stream tight for evidence.
            try:
                ot = timestamp(raw["occurred_at"])
            except CutoverError:
                _append_gap(pack, f"EVENT_TIME_INVALID:{raw.get('event_id')}")
                continue
            if not (cutoff_dt < ot <= as_of_dt):
                continue
            built.append(
                Event(
                    event_id=raw["event_id"],
                    warehouse_id=raw["warehouse_id"],
                    master_item_id=raw["master_item_id"],
                    unit=raw["unit"],
                    occurred_at=raw["occurred_at"],
                    sequence=seq,
                    kind=raw["kind"],
                    qty=quantity(raw["qty"]),
                    reservation_key=raw.get("reservation_key") or "",
                    reverses_event_id=raw.get("reverses_event_id") or "",
                )
            )
        pack["events"] = built
        pack["reservations_at_cutoff"] = []

    return {
        "by_master": by_master,
        "reservations_at_cutoff": [],
        "mode_b_settings": settings,
        "cutoff_at": CUTOFF_AT,
        "as_of": as_of,
        "warehouse_id": wid,
        "branch_id": bid,
        "post_cutoff_reservation_count": len(post_cutoff_keys),
        "order_count": len(orders),
    }


def project_master_ats(
    *,
    master_item_id: str,
    as_of: str,
    reservations_at_cutoff: Iterable[Reservation] = (),
    events: Iterable[Event] = (),
    evidence_complete: bool,
) -> Dict[str, Any]:
    base = baseline_for_master(master_item_id)
    if base is None:
        man = load_baseline_manifest()
        inactive = {
            str(r.get("master_item_id"))
            for r in (man.get("rows") or [])
            if isinstance(r, dict) and not r.get("eligible_master")
        }
        if str(master_item_id) in inactive:
            return {
                "status": "EXCLUDED",
                "reason": REASON_ATS_INACTIVE_MASTER,
                "available_qty": None,
                "source": SOURCE_ATS,
            }
        return {
            "status": "EXCLUDED",
            "reason": REASON_ATS_NOT_IN_BASELINE,
            "available_qty": None,
            "source": SOURCE_ATS,
            "note": "Rows outside ATS file are not auto-zeroed",
        }
    try:
        projected = project_available(
            base,
            reservations_at_cutoff=reservations_at_cutoff,
            events=events,
            evidence_complete=evidence_complete,
            as_of=as_of,
        )
    except CutoverError as exc:
        return {
            "status": "UNAVAILABLE",
            "reason": REASON_ATS_COVERAGE_GAP,
            "error": str(exc),
            "available_qty": None,
            "source": SOURCE_ATS,
            "cutoff_at": base.cutoff_at,
        }
    return {
        "status": "READY",
        "reason": None,
        "available_qty": float(Decimal(projected["available_qty"])),
        "available_qty_str": projected["available_qty"],
        "remaining_reservations": projected["remaining_reservations"],
        "effects": projected["effects"],
        "source": SOURCE_ATS,
        "cutoff_at": projected["cutoff_at"],
        "as_of": projected["as_of"],
        "physical_stock_verified": False,
    }


def apply_ats_overlay_to_ssi_row(
    row: dict,
    *,
    warehouse_id: str,
    as_of: str,
    reservations_at_cutoff: Iterable[Reservation] = (),
    events: Iterable[Event] = (),
    evidence_complete: bool = False,
    coverage_gaps: Optional[Sequence[str]] = None,
    skipped: Optional[Sequence[str]] = None,
) -> dict:
    """Overlay stock_dapat_dijual / qty_siap_jual for YGY ATS when enabled.

    Never mutates physical_qty / balances. Leaves polos/tercetak verification alone.
    evidence_complete must come from the collector — never force True to hide gaps.
    """
    if not ats_ygy_enabled():
        row["ats_cutover_status"] = "OFF"
        row["ats_cutover_reason"] = REASON_ATS_MODE_OFF
        return row
    wid = str(warehouse_id or "").strip()
    if wid != YGY_WAREHOUSE_ID:
        row["ats_cutover_status"] = "SKIPPED"
        row["ats_cutover_reason"] = REASON_ATS_WRONG_WAREHOUSE
        return row
    mid = str(row.get("master_item_id") or row.get("id") or "").strip()
    result = project_master_ats(
        master_item_id=mid,
        as_of=as_of,
        reservations_at_cutoff=reservations_at_cutoff,
        events=events,
        evidence_complete=evidence_complete,
    )
    row["ats_cutover_status"] = result.get("status")
    row["ats_cutover_reason"] = result.get("reason")
    row["ats_cutover_source"] = SOURCE_ATS
    row["ats_cutover_cutoff_at"] = result.get("cutoff_at") or CUTOFF_AT
    if skipped:
        row["ats_cutover_skipped"] = list(skipped)
    if result.get("status") == "READY":
        qty = result.get("available_qty")
        row["stock_dapat_dijual"] = qty
        row["qty_siap_jual"] = qty
        row["stock_dapat_dijual_status"] = "READY"
        row["qty_siap_jual_status"] = "READY"
        row["qty_siap_jual_reason"] = None
        row["qty_siap_jual_note"] = (
            "ATS cutover YGY-MAIN: Accurate free blank @ Owner cutoff; "
            "post-cutoff events projected; physical_stock not rewritten"
        )
        row["ats_cutover_available_qty"] = qty
        row["physical_stock_verified"] = False
    elif result.get("status") == "UNAVAILABLE":
        row["stock_dapat_dijual"] = None
        row["qty_siap_jual"] = None
        row["stock_dapat_dijual_status"] = "UNVERIFIED"
        row["qty_siap_jual_status"] = "UNVERIFIED"
        row["qty_siap_jual_reason"] = REASON_ATS_COVERAGE_GAP
        row["qty_siap_jual_note"] = result.get("error") or "ATS evidence incomplete"
        if coverage_gaps:
            row["ats_coverage_gaps"] = list(coverage_gaps)
        else:
            row["ats_coverage_gaps"] = coverage_gaps_for_live_ro(
                has_stock_allocations=False,
                event_stream_complete=False,
            )
    # EXCLUDED: leave legacy SSI figures; do not force zero
    return row


def map_business_event(
    *,
    kind: str,
    event_id: str,
    warehouse_id: str,
    master_item_id: str,
    unit: str,
    occurred_at: str,
    sequence: int,
    qty: Any,
    reservation_key: str = "",
    reverses_event_id: str = "",
) -> Event:
    """Explicit mapping only — unknown kinds raise at project time."""
    return Event(
        event_id=event_id,
        warehouse_id=warehouse_id,
        master_item_id=master_item_id,
        unit=unit,
        occurred_at=occurred_at,
        sequence=sequence,
        kind=kind,
        qty=quantity(qty),
        reservation_key=reservation_key,
        reverses_event_id=reverses_event_id,
    )


# Explicit post-cutoff mutation → projector kind (Owner-required catalogue).
EVENT_KIND_MAP = {
    "NEW_PO_LINE_RESERVE": "RESERVE",
    "SHIP_AGAINST_RESERVATION": "SHIP_RESERVED",
    "CANCEL_OR_RELEASE_REMAINING": "RELEASE",
    "PURCHASE_RECEIPT_UNRESERVED": "RECEIVE_FREE",
    "UNRESERVED_ISSUE_OR_ADJUST_OUT": "ISSUE_FREE",
    "LEDGER_REVERSAL": "REVERSE",
}


def mapped_kind(business_code: str) -> str:
    kind = EVENT_KIND_MAP.get(str(business_code or "").strip())
    if not kind:
        raise CutoverError(f"Unmapped business event requires explicit policy: {business_code}")
    return kind
