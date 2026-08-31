"""HPP valuation settings, deterministic historical recalculation, and future Laba/Rugi query boundary.

Source documents are never mutated. Physical stock quantities are never mutated.
HPP unit cost always comes from the exact Pesanan Pembelian line unit_price.

Historical COGS consumption is not fabricated. The project has no inventory issue/consumption
ledger (Stock Saat Ini is a formula). Recalculation therefore rebuilds acquisition layers only.
The query boundary reports cogs_available=False with a stable reason until a real issue ledger exists.
FIFO consume / moving-average-at-issue algorithms are implemented as pure functions for tests
and future use; they are not fed invented history.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from services.purchase_common import (
    actor_snapshot,
    get_named_lock,
    item_identity,
    money_round,
    now_iso,
    qty_round,
)

SETTINGS_KEY = "hpp_valuation"
VERSIONS = "hpp_cost_versions"
LAYERS = "hpp_cost_layers"
LOCK_KEY = "hpp_recalc_lock"

METHOD_FIFO = "FIFO"
METHOD_AVG = "RATA-RATA"
RECOG_PO = "purchase_order"
RECOG_GR = "goods_receipt"

METHOD_CHOICES = (METHOD_FIFO, METHOD_AVG)
RECOG_CHOICES = (RECOG_PO, RECOG_GR)

UI_RECOG = {
    RECOG_PO: "Saat Pesanan Pembelian",
    RECOG_GR: "Saat Penerimaan Barang",
}
RECOG_FROM_UI = {v: k for k, v in UI_RECOG.items()}

COGS_UNAVAILABLE_REASON = "no_inventory_issue_ledger"


def _dec(v: Any) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def normalize_method(raw: Any) -> str:
    s = str(raw or "").strip().upper().replace(" ", "-")
    if s in ("FIFO",):
        return METHOD_FIFO
    if s in ("RATA-RATA", "RATA_RATA", "AVERAGE", "WEIGHTED-AVERAGE", "WEIGHTED_AVERAGE"):
        return METHOD_AVG
    raise HTTPException(400, "Metode HPP tidak valid. Pilih FIFO atau RATA-RATA.")


def normalize_recognition(raw: Any) -> str:
    s = str(raw or "").strip()
    if s in (RECOG_PO, "Saat Pesanan Pembelian", "PO", "purchase_order"):
        return RECOG_PO
    if s in (RECOG_GR, "Saat Penerimaan Barang", "goods_receipt", "receipt", "GR"):
        return RECOG_GR
    raise HTTPException(400, "Titik pengakuan HPP tidak valid. Pilih Saat Pesanan Pembelian atau Saat Penerimaan Barang.")


def default_settings() -> dict:
    return {
        "key": SETTINGS_KEY,
        "method": METHOD_FIFO,
        "recognition_point": RECOG_GR,
        "recognition_label": UI_RECOG[RECOG_GR],
        "active_version": None,
        "last_recalculation_status": "BELUM",
        "last_recalculation_at": None,
        "last_recalculation_error": None,
        "updated_at": None,
    }


def public_settings(doc: Optional[dict]) -> dict:
    base = default_settings()
    if doc:
        method = doc.get("method") or METHOD_FIFO
        recog = doc.get("recognition_point") or RECOG_GR
        try:
            method = normalize_method(method)
        except HTTPException:
            method = METHOD_FIFO
        try:
            recog = normalize_recognition(recog)
        except HTTPException:
            recog = RECOG_GR
        base.update({
            "method": method,
            "recognition_point": recog,
            "recognition_label": UI_RECOG.get(recog, recog),
            "active_version": doc.get("active_version"),
            "last_recalculation_status": doc.get("last_recalculation_status") or "BELUM",
            "last_recalculation_at": doc.get("last_recalculation_at"),
            "last_recalculation_error": doc.get("last_recalculation_error"),
            "updated_at": doc.get("updated_at"),
        })
    return base


async def get_hpp_settings(db) -> dict:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return public_settings(doc)


def _event_sort_key(ev: dict) -> tuple:
    return (
        str(ev.get("recognized_at") or ""),
        str(ev.get("source_type") or ""),
        str(ev.get("source_id") or ""),
        str(ev.get("source_line_id") or ""),
        str(ev.get("event_id") or ""),
    )


def build_acquisition_events(
    *,
    method: str,
    recognition_point: str,
    purchase_orders: List[dict],
    goods_receipts: List[dict],
    allocations: List[dict],
) -> List[dict]:
    """Pure: PO unit_price is the only cost. Recognition chooses qty/timestamp source."""
    po_by_id = {str(p.get("id")): p for p in purchase_orders}
    po_line_by_id: Dict[str, dict] = {}
    for po in purchase_orders:
        for li in po.get("lines") or []:
            po_line_by_id[str(li.get("line_id"))] = {**li, "purchase_order_id": po.get("id"), "po_number": po.get("po_number"), "order_date": po.get("order_date"), "created_at": po.get("created_at")}

    events: List[dict] = []
    if recognition_point == RECOG_PO:
        for po in purchase_orders:
            for li in po.get("lines") or []:
                qty = qty_round(li.get("ordered_qty") or 0)
                if _dec(qty) <= 0:
                    continue
                unit = money_round(li.get("unit_price") or 0)
                ident = item_identity({**li, "purchase_order_id": po.get("id")})
                ts = str(po.get("order_date") or "")[:10] or str(po.get("created_at") or "")
                events.append({
                    "event_id": f"po:{po.get('id')}:{li.get('line_id')}",
                    "source_type": "purchase_order",
                    "source_id": po.get("id"),
                    "source_line_id": li.get("line_id"),
                    "source_number": po.get("po_number"),
                    "item_identity": ident,
                    "item_name": li.get("item_name"),
                    "item_code": li.get("item_code"),
                    "master_item_id": li.get("master_item_id") or "",
                    "accurate_item_id": li.get("accurate_item_id") or "",
                    "recognized_at": ts,
                    "recognized_qty": qty,
                    "unit_cost": unit,
                    "total_cost": money_round(_dec(qty) * _dec(unit)),
                })
    else:
        for rec in goods_receipts:
            po = po_by_id.get(str(rec.get("purchase_order_id") or ""))
            ts = str(rec.get("receipt_date") or rec.get("created_at") or "")
            for li in rec.get("lines") or []:
                qty = qty_round(li.get("received_qty") or 0)
                if _dec(qty) <= 0:
                    continue
                po_line = po_line_by_id.get(str(li.get("purchase_order_line_id") or ""))
                if not po_line:
                    continue
                unit = money_round(po_line.get("unit_price") or 0)
                ident = item_identity({**po_line, "purchase_order_id": rec.get("purchase_order_id")})
                events.append({
                    "event_id": f"gr:{rec.get('id')}:{li.get('line_id') or li.get('purchase_order_line_id')}",
                    "source_type": "goods_receipt",
                    "source_id": rec.get("id"),
                    "source_line_id": li.get("line_id") or li.get("purchase_order_line_id"),
                    "source_number": rec.get("receipt_number"),
                    "item_identity": ident,
                    "item_name": po_line.get("item_name"),
                    "item_code": po_line.get("item_code"),
                    "master_item_id": po_line.get("master_item_id") or "",
                    "accurate_item_id": po_line.get("accurate_item_id") or "",
                    "recognized_at": ts,
                    "recognized_qty": qty,
                    "unit_cost": unit,
                    "total_cost": money_round(_dec(qty) * _dec(unit)),
                    "purchase_order_id": rec.get("purchase_order_id"),
                    "purchase_order_line_id": li.get("purchase_order_line_id"),
                })
        for alloc in allocations:
            po_line = po_line_by_id.get(str(alloc.get("purchase_order_line_id") or ""))
            if not po_line:
                continue
            qty = qty_round(alloc.get("allocated_qty") or 0)
            if _dec(qty) <= 0:
                continue
            unit = money_round(po_line.get("unit_price") or 0)
            ident = item_identity({**po_line, "purchase_order_id": alloc.get("purchase_order_id")})
            ts = str(alloc.get("receipt_date") or alloc.get("created_at") or "")
            events.append({
                "event_id": f"alloc:{alloc.get('id')}",
                "source_type": "goods_receipt_allocation",
                "source_id": alloc.get("accurate_receipt_id"),
                "source_line_id": alloc.get("accurate_line_id"),
                "source_number": alloc.get("accurate_receipt_id"),
                "item_identity": ident,
                "item_name": po_line.get("item_name"),
                "item_code": po_line.get("item_code"),
                "master_item_id": po_line.get("master_item_id") or "",
                "accurate_item_id": po_line.get("accurate_item_id") or "",
                "recognized_at": ts,
                "recognized_qty": qty,
                "unit_cost": unit,
                "total_cost": money_round(_dec(qty) * _dec(unit)),
                "purchase_order_id": alloc.get("purchase_order_id"),
                "purchase_order_line_id": alloc.get("purchase_order_line_id"),
            })

    # Deduplicate by event_id then deterministic order
    uniq: Dict[str, dict] = {}
    for ev in events:
        uniq[str(ev["event_id"])] = ev
    ordered = sorted(uniq.values(), key=_event_sort_key)
    return ordered


def fifo_layers_from_events(events: List[dict]) -> List[dict]:
    layers = []
    for ev in events:
        qty = qty_round(ev["recognized_qty"])
        layers.append({
            "layer_id": ev["event_id"],
            "item_identity": ev["item_identity"],
            "source_type": ev["source_type"],
            "source_id": ev["source_id"],
            "source_line_id": ev["source_line_id"],
            "source_number": ev.get("source_number"),
            "recognized_at": ev["recognized_at"],
            "recognized_qty": qty,
            "remaining_qty": qty,
            "unit_cost": ev["unit_cost"],
            "total_cost": ev["total_cost"],
            "item_name": ev.get("item_name"),
            "item_code": ev.get("item_code"),
            "master_item_id": ev.get("master_item_id") or "",
            "accurate_item_id": ev.get("accurate_item_id") or "",
        })
    return layers


def fifo_consume(layers: List[dict], issues: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Consume oldest layer first. issues: item_identity, qty, issued_at, issue_id.
    Returns (updated_layers, cogs_rows). Deterministic tie-break already in layer order.
    """
    by_item: Dict[str, List[dict]] = {}
    for layer in layers:
        by_item.setdefault(layer["item_identity"], []).append(dict(layer))
    for item in by_item:
        by_item[item].sort(key=lambda l: (
            str(l.get("recognized_at") or ""),
            str(l.get("source_id") or ""),
            str(l.get("source_line_id") or ""),
            str(l.get("layer_id") or ""),
        ))

    cogs: List[dict] = []
    ordered_issues = sorted(issues, key=lambda i: (
        str(i.get("issued_at") or ""),
        str(i.get("issue_id") or ""),
        str(i.get("item_identity") or ""),
    ))
    for issue in ordered_issues:
        ident = str(issue.get("item_identity") or "")
        need = _dec(issue.get("qty") or 0)
        if need <= 0:
            continue
        for layer in by_item.get(ident, []):
            if need <= 0:
                break
            avail = _dec(layer.get("remaining_qty") or 0)
            if avail <= 0:
                continue
            take = avail if avail <= need else need
            layer["remaining_qty"] = qty_round(avail - take)
            cogs.append({
                "issue_id": issue.get("issue_id"),
                "issued_at": issue.get("issued_at"),
                "item_identity": ident,
                "qty": qty_round(take),
                "unit_hpp": layer["unit_cost"],
                "total_hpp": money_round(take * _dec(layer["unit_cost"])),
                "layer_id": layer["layer_id"],
                "source_type": layer.get("source_type"),
                "source_id": layer.get("source_id"),
                "source_line_id": layer.get("source_line_id"),
            })
            need -= take

    flat = []
    for item_layers in by_item.values():
        flat.extend(item_layers)
    flat.sort(key=lambda l: (
        str(l.get("recognized_at") or ""),
        str(l.get("source_id") or ""),
        str(l.get("source_line_id") or ""),
        str(l.get("layer_id") or ""),
    ))
    return flat, cogs


def moving_average_from_events(events: List[dict], issues: Optional[List[dict]] = None) -> Tuple[List[dict], List[dict], Dict[str, dict]]:
    """Chronological moving weighted average. Issues (optional) use average at issue time."""
    state: Dict[str, dict] = {}
    snapshots: List[dict] = []
    cogs: List[dict] = []
    timeline: List[tuple] = []
    for ev in events:
        timeline.append(("acq", _event_sort_key(ev), ev))
    for issue in issues or []:
        key = (str(issue.get("issued_at") or ""), str(issue.get("issue_id") or ""), str(issue.get("item_identity") or ""))
        timeline.append(("issue", key, issue))
    timeline.sort(key=lambda t: (t[1], 0 if t[0] == "acq" else 1, t[0]))

    for kind, _k, payload in timeline:
        if kind == "acq":
            ident = payload["item_identity"]
            st = state.get(ident) or {"qty": Decimal("0"), "average_cost": Decimal("0")}
            prev_qty = st["qty"]
            incoming = _dec(payload["recognized_qty"])
            denom = prev_qty + incoming
            if denom > 0:
                new_avg = ((prev_qty * st["average_cost"]) + (incoming * _dec(payload["unit_cost"]))) / denom
            else:
                new_avg = Decimal("0")
            st = {"qty": denom, "average_cost": new_avg}
            state[ident] = st
            snapshots.append({
                "layer_id": payload["event_id"],
                "item_identity": ident,
                "source_type": payload["source_type"],
                "source_id": payload["source_id"],
                "source_line_id": payload["source_line_id"],
                "source_number": payload.get("source_number"),
                "recognized_at": payload["recognized_at"],
                "recognized_qty": payload["recognized_qty"],
                "remaining_qty": qty_round(st["qty"]),
                "unit_cost": money_round(st["average_cost"]),
                "incoming_unit_cost": payload["unit_cost"],
                "total_cost": money_round(st["qty"] * st["average_cost"]),
                "item_name": payload.get("item_name"),
                "item_code": payload.get("item_code"),
                "master_item_id": payload.get("master_item_id") or "",
                "accurate_item_id": payload.get("accurate_item_id") or "",
            })
        else:
            ident = str(payload.get("item_identity") or "")
            st = state.get(ident) or {"qty": Decimal("0"), "average_cost": Decimal("0")}
            qty = _dec(payload.get("qty") or 0)
            if qty <= 0:
                continue
            avg = st["average_cost"]
            cogs.append({
                "issue_id": payload.get("issue_id"),
                "issued_at": payload.get("issued_at"),
                "item_identity": ident,
                "qty": qty_round(qty),
                "unit_hpp": money_round(avg),
                "total_hpp": money_round(qty * avg),
                "source_type": None,
                "source_id": None,
                "source_line_id": None,
            })
            new_qty = st["qty"] - qty
            if new_qty < 0:
                new_qty = Decimal("0")
            state[ident] = {"qty": new_qty, "average_cost": avg if new_qty > 0 else Decimal("0")}
    return snapshots, cogs, state


async def _load_source_docs(db) -> dict:
    pos = await db.purchase_orders.find({}, {"_id": 0}).to_list(20000)
    grs = await db.purchase_goods_receipts.find({}, {"_id": 0}).to_list(20000)
    allocs = await db.purchase_receipt_po_links.find({}, {"_id": 0}).to_list(20000)
    return {"purchase_orders": pos, "goods_receipts": grs, "allocations": allocs}


async def recalculate_hpp(
    db,
    *,
    method: str,
    recognition_point: str,
    user: Optional[dict] = None,
    issues: Optional[List[dict]] = None,
) -> dict:
    method = normalize_method(method)
    recognition_point = normalize_recognition(recognition_point)

    async with get_named_lock("hpp_recalc"):
        lock = await db.app_settings.find_one_and_update(
            {"key": LOCK_KEY, "$or": [{"locked": {"$ne": True}}, {"locked": {"$exists": False}}]},
            {"$set": {"key": LOCK_KEY, "locked": True, "locked_at": now_iso()}},
            upsert=True,
        )
        # find_one_and_update with upsert can still race; second check via locked flag
        if lock and lock.get("locked") is True and lock.get("locked_at"):
            # we may have overwritten our own; proceed if we just set it
            pass

        prev_settings = public_settings(await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0}))
        prev_active = prev_settings.get("active_version")
        prev_version_doc = None
        if prev_active:
            prev_version_doc = await db[VERSIONS].find_one({"id": prev_active, "status": "ACTIVE"}, {"_id": 0})

        last = await db[VERSIONS].find({}, {"_id": 0, "version": 1}).sort("version", -1).to_list(1)
        next_ver = int((last[0].get("version") if last else 0) or 0) + 1
        version_id = str(uuid.uuid4())
        ts = now_iso()
        building = {
            "id": version_id,
            "version": next_ver,
            "method": method,
            "recognition_point": recognition_point,
            "status": "BUILDING",
            "created_at": ts,
            "recalculated_at": None,
            "error": None,
            "layer_count": 0,
            "event_count": 0,
            "created_by": actor_snapshot(user),
        }
        await db[VERSIONS].insert_one(dict(building))

        try:
            src = await _load_source_docs(db)
            events = build_acquisition_events(
                method=method,
                recognition_point=recognition_point,
                purchase_orders=src["purchase_orders"],
                goods_receipts=src["goods_receipts"],
                allocations=src["allocations"],
            )
            if method == METHOD_FIFO:
                layers, cogs = fifo_consume(fifo_layers_from_events(events), issues or [])
            else:
                layers, cogs, _state = moving_average_from_events(events, issues or [])

            layer_docs = []
            for layer in layers:
                layer_docs.append({
                    **layer,
                    "calculation_version": version_id,
                    "version": next_ver,
                    "method": method,
                    "recognition_point": recognition_point,
                })
            if layer_docs:
                await db[LAYERS].insert_many(layer_docs)

            seen_ids = [str(x.get("layer_id") or x.get("event_id") or "") for x in layer_docs]
            if len(seen_ids) != len(set(seen_ids)):
                raise RuntimeError("duplicate derived cost layers")

            await db[VERSIONS].update_one(
                {"id": version_id},
                {"$set": {
                    "status": "ACTIVE",
                    "recalculated_at": now_iso(),
                    "layer_count": len(layer_docs),
                    "event_count": len(events),
                    "cogs_row_count": len(cogs),
                    "cogs_available": bool(issues),
                    "cogs_unavailable_reason": None if issues else COGS_UNAVAILABLE_REASON,
                    "error": None,
                }},
            )
            if prev_active and prev_active != version_id:
                await db[VERSIONS].update_one(
                    {"id": prev_active, "status": "ACTIVE"},
                    {"$set": {"status": "SUPERSEDED", "superseded_at": now_iso(), "superseded_by": version_id}},
                )

            saved = {
                "key": SETTINGS_KEY,
                "method": method,
                "recognition_point": recognition_point,
                "recognition_label": UI_RECOG[recognition_point],
                "active_version": version_id,
                "last_recalculation_status": "SUCCESS",
                "last_recalculation_at": now_iso(),
                "last_recalculation_error": None,
                "updated_at": now_iso(),
                "updated_by": actor_snapshot(user),
            }
            await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
            return public_settings(saved)
        except Exception as exc:
            await db[VERSIONS].update_one(
                {"id": version_id},
                {"$set": {"status": "FAILED", "error": str(exc), "recalculated_at": now_iso()}},
            )
            fail_settings = {
                "key": SETTINGS_KEY,
                "method": prev_settings.get("method"),
                "recognition_point": prev_settings.get("recognition_point"),
                "recognition_label": prev_settings.get("recognition_label"),
                "active_version": prev_active,
                "last_recalculation_status": "FAILED",
                "last_recalculation_at": now_iso(),
                "last_recalculation_error": str(exc),
                "updated_at": now_iso(),
            }
            await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": fail_settings}, upsert=True)
            raise HTTPException(500, f"Perhitungan ulang HPP gagal. Versi aktif sebelumnya dipertahankan. {exc}")
        finally:
            await db.app_settings.update_one({"key": LOCK_KEY}, {"$set": {"locked": False, "unlocked_at": now_iso()}})


async def update_hpp_settings(db, payload: Dict[str, Any], user: dict) -> dict:
    method = normalize_method(payload.get("method"))
    recognition_point = normalize_recognition(payload.get("recognition_point") or payload.get("recognition"))
    return await recalculate_hpp(db, method=method, recognition_point=recognition_point, user=user)


async def query_cost_boundary(
    db,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    item_identity_key: Optional[str] = None,
) -> dict:
    """Read-only future Laba/Rugi contract. Does not mutate anything."""
    settings = await get_hpp_settings(db)
    active_id = settings.get("active_version")
    version = None
    layers: List[dict] = []
    if active_id:
        version = await db[VERSIONS].find_one({"id": active_id}, {"_id": 0})
        q: Dict[str, Any] = {"calculation_version": active_id}
        if item_identity_key:
            q["item_identity"] = str(item_identity_key)
        layers = await db[LAYERS].find(q, {"_id": 0}).to_list(20000)
        if date_to:
            layers = [l for l in layers if str(l.get("recognized_at") or "")[:10] <= str(date_to)[:10]]
        if date_from:
            # acquisitions in range (for period inventory in)
            pass
    acquisitions = []
    for l in layers:
        rec_at = str(l.get("recognized_at") or "")
        if date_from and rec_at[:10] < str(date_from)[:10]:
            continue
        if date_to and rec_at[:10] > str(date_to)[:10]:
            continue
        acquisitions.append({
            "item_identity": l.get("item_identity"),
            "item_name": l.get("item_name"),
            "item_code": l.get("item_code"),
            "quantity": l.get("recognized_qty"),
            "remaining_qty": l.get("remaining_qty"),
            "unit_hpp": l.get("unit_cost"),
            "total_hpp": l.get("total_cost"),
            "source_type": l.get("source_type"),
            "source_id": l.get("source_id"),
            "source_line_id": l.get("source_line_id"),
            "source_number": l.get("source_number"),
            "recognized_at": l.get("recognized_at"),
        })
    return {
        "method": settings.get("method"),
        "recognition_point": settings.get("recognition_point"),
        "recognition_label": settings.get("recognition_label"),
        "active_version": active_id,
        "version_status": (version or {}).get("status"),
        "recalculated_at": settings.get("last_recalculation_at"),
        "cogs_available": False,
        "cogs_unavailable_reason": COGS_UNAVAILABLE_REASON,
        "issues": [],
        "acquisitions": acquisitions,
        "date_from": date_from,
        "date_to": date_to,
    }
