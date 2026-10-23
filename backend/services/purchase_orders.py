"""Purchase Order (Pesanan Pembelian).

HPP unit cost source is always this document's exact line unit_price.
Physical on-hand stock is never changed by creating a PO.

PO may be created:
- directly (no purchase request), or
- from a purchase request (optional reference).

PR remaining qty is reduced only for lines that keep the original request_line_id.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo.errors import OperationFailure, PyMongoError

from services.purchase_common import (
    PREFIX_PURCHASE_ORDER,
    actor_snapshot,
    actor_user_id,
    stamp_create,
    stamp_update,
    cancellation_fields,
    get_named_lock,
    insert_with_unique_number,
    is_cancelled,
    item_identity,
    line_subtotal,
    money_round,
    now_iso,
    parse_money,
    parse_qty,
    qty_le,
    remaining_qty,
    next_dated_seq,
    with_active_filter,
)
from services.purchase_branch import (
    BRANCH_ALREADY_SET_MSG,
    BRANCH_OUTCOME_UNKNOWN_MSG,
    assert_po_receipts_match_branch,
    branch_snapshot,
    load_active_branch,
    resolve_create_purchase_order_branch,
)
from services.purchase_requests import get_purchase_request
from services.physical_stock_engine import (
    attach_po_stock_snapshot,
    handle_purchase_order_created,
    precheck_purchase_order_lines,
)

COLLECTION = "purchase_orders"
GR_COLLECTION = "purchase_goods_receipts"
INV_COLLECTION = "purchase_invoices"
ALLOC_COLLECTION = "purchase_receipt_po_links"
AUDIT_COLLECTION = "audit_logs"
AUDIT_ACTION_BRANCH_SET = "purchase_order.branch.set"

# CAS: only mutate when Cabang Pembelian is still unset.
_BRANCH_UNSET_CAS = {
    "$or": [
        {"branch_id": {"$exists": False}},
        {"branch_id": None},
        {"branch_id": ""},
    ],
}

# Test-only: abort after CAS update, before audit insert (same txn).
_fail_after_branch_cas_before_audit = False
# Test-only: first CAS attempt raises TransientTransactionError (full txn retry).
_fail_cas_with_transient_once = False
# Test-only: counts transaction body invocations (TransientTransactionError retries).
_branch_txn_body_runs = 0


def set_fail_after_branch_cas_before_audit(enabled: bool) -> None:
    global _fail_after_branch_cas_before_audit
    _fail_after_branch_cas_before_audit = bool(enabled)


def set_fail_cas_with_transient_once(enabled: bool) -> None:
    global _fail_cas_with_transient_once
    _fail_cas_with_transient_once = bool(enabled)


def reset_branch_txn_body_runs() -> None:
    global _branch_txn_body_runs
    _branch_txn_body_runs = 0


def get_branch_txn_body_runs() -> int:
    return _branch_txn_body_runs


def _is_awaitable(value: Any) -> bool:
    return hasattr(value, "__await__")


async def _maybe_await(value: Any) -> Any:
    if _is_awaitable(value):
        return await value
    return value


async def _run_po_branch_transaction(session, txn_body) -> None:
    """Run txn_body with correct Mongo retry semantics.

    - TransientTransactionError: abort and re-run the full body (new txn).
    - UnknownTransactionCommitResult: retry commit only (body not re-run).

    Avoids Motor ``async with start_transaction()`` / ``with_transaction`` because
    those commit inside ``__aexit__``, which cannot retry Unknown commit results.
    """
    import time

    deadline = time.monotonic() + 120.0
    last_error: Optional[BaseException] = None
    while time.monotonic() < deadline:
        session.start_transaction()
        try:
            await txn_body(session)
            # Commit with UnknownTransactionCommitResult → retry commit only.
            while time.monotonic() < deadline:
                try:
                    await _maybe_await(session.commit_transaction())
                    return
                except PyMongoError as exc:
                    last_error = exc
                    if exc.has_error_label("UnknownTransactionCommitResult"):
                        continue
                    if exc.has_error_label("TransientTransactionError"):
                        break  # full txn retry
                    raise
            else:
                break
            # Transient on commit: abort if needed then full retry.
            if session.in_transaction:
                await _maybe_await(session.abort_transaction())
            continue
        except HTTPException:
            if session.in_transaction:
                await _maybe_await(session.abort_transaction())
            raise
        except PyMongoError as exc:
            last_error = exc
            if session.in_transaction:
                await _maybe_await(session.abort_transaction())
            if (
                exc.has_error_label("TransientTransactionError")
                and time.monotonic() < deadline
            ):
                continue
            raise
        except Exception:
            if session.in_transaction:
                await _maybe_await(session.abort_transaction())
            raise
    if last_error is not None:
        raise last_error
    raise HTTPException(
        409,
        "Penetapan Cabang Pembelian gagal karena perubahan bersamaan.",
    )


async def _received_qty_by_po_line(db, purchase_order_id: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    receipts = await db[GR_COLLECTION].find(
        with_active_filter({"purchase_order_id": purchase_order_id}),
        {"_id": 0, "lines": 1},
    ).to_list(2000)
    for rec in receipts:
        for li in rec.get("lines") or []:
            key = str(li.get("purchase_order_line_id") or "")
            if not key:
                continue
            out[key] = out.get(key, 0.0) + float(li.get("received_qty") or 0)
    allocs = await db[ALLOC_COLLECTION].find(
        with_active_filter({"purchase_order_id": purchase_order_id}),
        {"_id": 0, "purchase_order_line_id": 1, "allocated_qty": 1},
    ).to_list(5000)
    for a in allocs:
        key = str(a.get("purchase_order_line_id") or "")
        if not key:
            continue
        out[key] = out.get(key, 0.0) + float(a.get("allocated_qty") or 0)
    return out


async def _invoiced_qty_by_po_line(db, purchase_order_id: str, exclude_invoice_id: Optional[str] = None) -> Dict[str, float]:
    q: Dict[str, Any] = with_active_filter({"purchase_order_id": purchase_order_id})
    if exclude_invoice_id:
        q = with_active_filter({"purchase_order_id": purchase_order_id, "id": {"$ne": str(exclude_invoice_id)}})
    invoices = await db[INV_COLLECTION].find(q, {"_id": 0, "lines": 1}).to_list(2000)
    out: Dict[str, float] = {}
    for inv in invoices:
        for li in inv.get("lines") or []:
            key = str(li.get("purchase_order_line_id") or "")
            if not key:
                continue
            out[key] = out.get(key, 0.0) + float(li.get("invoice_qty") or li.get("quantity") or 0)
    return out


async def _has_early_invoice(db, purchase_order_id: str) -> bool:
    doc = await db[INV_COLLECTION].find_one(
        with_active_filter({"purchase_order_id": purchase_order_id, "precedes_goods_receipt": True}),
        {"_id": 1},
    )
    return bool(doc)


def _with_progress(
    doc: dict,
    received: Dict[str, float],
    invoiced: Dict[str, float],
    *,
    early_invoice: bool = False,
    returned: Optional[Dict[str, float]] = None,
) -> dict:
    out = dict(doc)
    lines = []
    rec_total = 0.0
    inv_total = 0.0
    ord_total = 0.0
    for li in out.get("lines") or []:
        row = dict(li)
        lid = str(row.get("line_id") or "")
        ordered = float(row.get("ordered_qty") or 0)
        rec = float(received.get(lid, 0.0))
        inv = float(invoiced.get(lid, 0.0))
        ret = float((returned or {}).get(lid, 0.0))
        net = max(0.0, rec - ret)
        row["cumulative_received_qty"] = rec
        row["confirmed_return_qty"] = ret
        row["net_received_qty"] = net
        row["remaining_receivable_qty"] = remaining_qty(ordered, rec)
        row["cumulative_invoiced_qty"] = inv
        row["remaining_invoice_qty"] = remaining_qty(ordered, inv)
        row["receipt_invoice_eligible_qty"] = min(
            float(row["remaining_invoice_qty"] or 0),
            max(0.0, net - inv),
        )
        row["item_identity"] = item_identity({**row, "purchase_order_id": out.get("id")})
        rec_total += rec
        inv_total += inv
        ord_total += ordered
        lines.append(row)
    out["lines"] = lines
    out["receiving_progress"] = {
        "ordered_qty": ord_total,
        "received_qty": rec_total,
        "remaining_qty": remaining_qty(ord_total, rec_total),
    }
    out["invoice_progress"] = {
        "ordered_qty": ord_total,
        "invoiced_qty": inv_total,
        "remaining_qty": remaining_qty(ord_total, inv_total),
    }
    if is_cancelled(out):
        recv_status = "DIBATALKAN"
        inv_status = "DIBATALKAN"
    else:
        if rec_total <= 0:
            recv_status = "BELUM DITERIMA"
        elif rec_total < ord_total:
            recv_status = "SEBAGIAN DITERIMA"
        else:
            recv_status = "DITERIMA"
        if inv_total <= 0:
            inv_status = "BELUM DIFAKTUR"
        elif inv_total < ord_total:
            inv_status = "SEBAGIAN DIFAKTUR"
        else:
            inv_status = "DIFAKTUR"
    out["receiving_status"] = recv_status
    out["invoice_status"] = inv_status
    out["status"] = recv_status
    out["precedes_goods_receipt"] = bool(early_invoice) and not is_cancelled(out)
    out["has_early_invoice"] = bool(early_invoice) and not is_cancelled(out)
    out["can_cancel"] = (not is_cancelled(out)) and rec_total <= 0 and inv_total <= 0
    if not out["can_cancel"] and not is_cancelled(out):
        if rec_total > 0:
            out["cancel_block_reason"] = "Pesanan tidak dapat dihapus karena barang sudah pernah diterima."
        elif inv_total > 0:
            out["cancel_block_reason"] = "Pesanan tidak dapat dihapus karena sudah memiliki faktur."
        else:
            out["cancel_block_reason"] = ""
    else:
        out["cancel_block_reason"] = ""
    return out


async def enrich_purchase_order(db, doc: dict) -> dict:
    from services.purchase_returns import confirmed_return_qty_by_po_line

    received = await _received_qty_by_po_line(db, doc["id"])
    invoiced = await _invoiced_qty_by_po_line(db, doc["id"])
    returned = await confirmed_return_qty_by_po_line(db, doc["id"])
    early = await _has_early_invoice(db, doc["id"])
    return _with_progress(doc, received, invoiced, early_invoice=early, returned=returned)


async def get_request_po_preview(db, request_id: str) -> dict:
    req = await get_purchase_request(db, request_id)
    if is_cancelled(req):
        raise HTTPException(409, "Permintaan sudah dibatalkan dan tidak dapat dijadikan Pesanan.")
    lines = []
    for li in req.get("lines") or []:
        remain = float(li.get("remaining_order_qty") or 0)
        lines.append({
            "line_idx": li.get("line_idx"),
            "line_id": li.get("line_id"),
            "request_line_id": li.get("line_id"),
            "request_line_idx": li.get("line_idx"),
            "master_item_id": li.get("master_item_id") or "",
            "accurate_item_id": li.get("accurate_item_id") or "",
            "item_code": li.get("item_code") or "",
            "item_name": li.get("item_name") or "",
            "unit": li.get("unit") or "pcs",
            "requested_qty": li.get("requested_qty") or 0,
            "cumulative_ordered_qty": li.get("cumulative_ordered_qty") or 0,
            "remaining_order_qty": remain,
            "ordered_qty": remain,
            "unit_price": 0.0,
            "line_subtotal": 0.0,
        })
    return {
        "request_id": req["id"],
        "request_number": req.get("request_number"),
        "request_date": req.get("request_date"),
        "notes": req.get("notes") or "",
        "lines": lines,
        "total_remaining_order_qty": req.get("total_remaining_order_qty") or 0,
    }


def _require_master_item(raw: dict, idx: int) -> str:
    mid = str(raw.get("master_item_id") or "").strip()
    if not mid:
        raise HTTPException(
            400,
            f"Baris ke-{idx + 1}: pilih item dari Master Item. Nama bebas tidak didukung.",
        )
    return mid


async def create_purchase_order(db, payload: Dict[str, Any], user: dict) -> dict:
    request_id = str(payload.get("request_id") or "").strip()
    supplier = str(payload.get("supplier") or payload.get("supplier_name") or "").strip()
    if not supplier:
        raise HTTPException(400, "Supplier wajib diisi.")

    async with get_named_lock("purchase_order"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                await handle_purchase_order_created(db, existing, user)
                return await enrich_purchase_order(db, existing)

        preview = None
        preview_by_id: Dict[str, dict] = {}
        if request_id:
            preview = await get_request_po_preview(db, request_id)
            preview_by_id = {str(l["request_line_id"]): l for l in preview["lines"]}

        input_lines = payload.get("lines") or payload.get("line_items") or []
        calculated = []
        total = 0.0
        seen_req = set()

        if input_lines:
            for idx, inp in enumerate(input_lines):
                if not isinstance(inp, dict):
                    raise HTTPException(400, f"Baris PO ke-{idx + 1} tidak valid.")
                req_line_id = str(inp.get("request_line_id") or "").strip()
                raw_qty = inp.get("ordered_qty", inp.get("quantity"))

                if req_line_id:
                    if not preview:
                        raise HTTPException(400, "Baris mengacu Permintaan tetapi request_id kosong.")
                    src = preview_by_id.get(req_line_id)
                    if src is None:
                        raise HTTPException(400, f"Baris Permintaan '{req_line_id}' tidak ditemukan pada permintaan ini.")
                    if req_line_id in seen_req:
                        raise HTTPException(400, "Baris Permintaan tidak boleh digabung atau diulang pada satu PO.")
                    seen_req.add(req_line_id)
                    qty = parse_qty(
                        raw_qty if raw_qty not in (None, "") else src["remaining_order_qty"],
                        f"Qty '{src['item_name']}'",
                    )
                    remain = float(src["remaining_order_qty"] or 0)
                    if not qty_le(qty, remain):
                        raise HTTPException(
                            400,
                            f"Qty pesanan ({qty}) untuk '{src['item_name']}' melebihi sisa Permintaan ({remain}).",
                        )
                    # Identity must stay on the request line item for PR fulfillment.
                    # If client swaps item away from PR line, drop request_line_id link.
                    mid = str(inp.get("master_item_id") or src["master_item_id"] or "").strip()
                    if mid and mid != str(src.get("master_item_id") or "").strip():
                        # swapped item → treat as direct line (does not fulfill PR)
                        req_line_id = ""
                        item_name = str(inp.get("item_name") or "").strip() or src["item_name"]
                        item_code = str(inp.get("item_code") or "").strip()
                        accurate_item_id = str(inp.get("accurate_item_id") or "").strip()
                        unit = str(inp.get("unit") or src["unit"] or "pcs").strip() or "pcs"
                        if not mid:
                            raise HTTPException(400, f"Baris ke-{idx + 1}: master_item_id wajib.")
                    else:
                        mid = str(src.get("master_item_id") or "").strip()
                        if not mid:
                            raise HTTPException(400, f"Baris Permintaan '{src['item_name']}' tidak punya Master Item.")
                        item_name = src["item_name"]
                        item_code = src["item_code"]
                        accurate_item_id = src["accurate_item_id"]
                        unit = src["unit"]
                    unit_price = parse_money(inp.get("unit_price"), f"Harga satuan '{item_name}'")
                    sub = line_subtotal(qty, unit_price)
                    total += sub
                    row = {
                        "line_idx": idx,
                        "line_id": str(uuid.uuid4()),
                        "master_item_id": mid,
                        "accurate_item_id": accurate_item_id,
                        "item_code": item_code,
                        "item_name": item_name,
                        "unit": unit,
                        "ordered_qty": qty,
                        "unit_price": unit_price,
                        "line_subtotal": sub,
                    }
                    if req_line_id:
                        row["request_line_id"] = src["request_line_id"]
                        row["request_line_idx"] = src["request_line_idx"]
                    calculated.append(row)
                else:
                    mid = _require_master_item(inp, idx)
                    item_name = str(inp.get("item_name") or inp.get("name") or "").strip()
                    if not item_name:
                        raise HTTPException(400, f"Nama item baris ke-{idx + 1} wajib diisi.")
                    qty = parse_qty(raw_qty, f"Qty '{item_name}'")
                    unit_price = parse_money(inp.get("unit_price"), f"Harga satuan '{item_name}'")
                    sub = line_subtotal(qty, unit_price)
                    total += sub
                    calculated.append({
                        "line_idx": idx,
                        "line_id": str(uuid.uuid4()),
                        "master_item_id": mid,
                        "accurate_item_id": str(inp.get("accurate_item_id") or "").strip(),
                        "item_code": str(inp.get("item_code") or "").strip(),
                        "item_name": item_name,
                        "unit": str(inp.get("unit") or "pcs").strip() or "pcs",
                        "ordered_qty": qty,
                        "unit_price": unit_price,
                        "line_subtotal": sub,
                    })
        elif preview:
            for src in preview["lines"]:
                qty = float(src["remaining_order_qty"] or 0)
                if qty <= 0:
                    continue
                mid = str(src.get("master_item_id") or "").strip()
                if not mid:
                    raise HTTPException(400, f"Baris Permintaan '{src['item_name']}' tidak punya Master Item.")
                calculated.append({
                    "line_idx": src["line_idx"],
                    "line_id": str(uuid.uuid4()),
                    "request_line_id": src["request_line_id"],
                    "request_line_idx": src["request_line_idx"],
                    "master_item_id": mid,
                    "accurate_item_id": src["accurate_item_id"],
                    "item_code": src["item_code"],
                    "item_name": src["item_name"],
                    "unit": src["unit"],
                    "ordered_qty": qty,
                    "unit_price": 0.0,
                    "line_subtotal": 0.0,
                })
        else:
            raise HTTPException(400, "Minimal satu baris Pesanan Pembelian wajib diisi.")

        if not calculated:
            raise HTTPException(400, "Tidak ada kuantitas yang dapat dipesan.")

        ts = now_iso()
        total = money_round(total)
        branch_fields = await resolve_create_purchase_order_branch(db, payload, user)
        doc = {
            "id": str(uuid.uuid4()),
            "request_id": preview["request_id"] if preview else "",
            "request_number": preview["request_number"] if preview else "",
            "supplier": supplier,
            "order_date": str(payload.get("order_date") or ts[:10]).strip()[:10],
            "notes": str(payload.get("notes") or "").strip(),
            "lines": calculated,
            "subtotal": total,
            "grand_total": total,
            **branch_fields,
            **stamp_create(user),
        }
        if idem:
            doc["idempotency_key"] = idem

        snapshot = await attach_po_stock_snapshot(db, doc)
        await precheck_purchase_order_lines(db, doc["lines"], snapshot)

        async def _alloc():
            return await next_dated_seq(db, COLLECTION, "po_number", PREFIX_PURCHASE_ORDER)

        saved = await insert_with_unique_number(db, COLLECTION, "po_number", doc, _alloc)
        await handle_purchase_order_created(db, saved, user)
        return await enrich_purchase_order(db, saved)


async def list_purchase_orders(
    db,
    q: Optional[str] = None,
    request_id: Optional[str] = None,
    eligible_for_invoice: bool = False,
    eligible_for_receipt: bool = False,
    include_cancelled: bool = False,
) -> List[dict]:
    filters: Dict[str, Any] = {}
    if request_id:
        filters["request_id"] = str(request_id)
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"request_number": {"$regex": needle, "$options": "i"}},
            {"supplier": {"$regex": needle, "$options": "i"}},
            {"lines.item_name": {"$regex": needle, "$options": "i"}},
        ]
    if not include_cancelled:
        filters = with_active_filter(filters)
    rows = await db[COLLECTION].find(filters, {"_id": 0}).sort("created_at", -1).to_list(1000)
    out = []
    for row in rows:
        enriched = await enrich_purchase_order(db, row)
        if eligible_for_invoice and float(enriched.get("invoice_progress", {}).get("remaining_qty") or 0) <= 0:
            continue
        if eligible_for_receipt and float(enriched.get("receiving_progress", {}).get("remaining_qty") or 0) <= 0:
            continue
        out.append(enriched)
    return out


async def get_purchase_order(db, po_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(po_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"po_number": str(po_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Pesanan Pembelian '{po_id}' tidak ditemukan.")
    return await enrich_purchase_order(db, doc)


async def set_purchase_order_branch(
    db, po_id: str, payload: Optional[dict], user: dict
) -> dict:
    """Set Cabang Pembelian on a legacy PO that has no branch_id (once).

    Multi-document transaction: CAS PO update + audit insert. Transient errors
    retry the full body; UnknownTransactionCommitResult retries commit only.
    Does not mutate supplier, lines, qty, price, or GR/invoice docs.
    """
    from services.branch_access import require_branch_access
    from services.physical_stock import (
        PhysicalStockUnavailableError,
        require_transaction_support,
    )

    data = dict(payload or {})
    reason = str(data.get("reason") or data.get("note") or "").strip()
    if len(reason) < 3:
        raise HTTPException(400, "Alasan perubahan wajib diisi (minimal 3 karakter).")
    target_branch_id = str(data.get("branch_id") or "").strip()
    if not target_branch_id:
        raise HTTPException(400, "branch_id wajib diisi.")

    async with get_named_lock("purchase_order"):
        doc = await db[COLLECTION].find_one({"id": str(po_id)}, {"_id": 0})
        if not doc:
            doc = await db[COLLECTION].find_one({"po_number": str(po_id)}, {"_id": 0})
        if not doc:
            raise HTTPException(404, f"Pesanan Pembelian '{po_id}' tidak ditemukan.")
        if is_cancelled(doc):
            raise HTTPException(400, "Pesanan sudah dibatalkan; cabang tidak dapat diubah.")

        existing_branch = str(doc.get("branch_id") or "").strip()
        if existing_branch:
            raise HTTPException(400, BRANCH_ALREADY_SET_MSG)

        branch = await load_active_branch(db, target_branch_id)
        await require_branch_access(db, user, branch["id"])
        # Preflight (non-authoritative): final GR check runs inside the txn.
        await assert_po_receipts_match_branch(db, doc["id"], branch["id"])

        try:
            await require_transaction_support(db.client)
        except PhysicalStockUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc

        snap = branch_snapshot(branch)
        before = {
            "branch_id": doc.get("branch_id") or None,
            "branch_code": doc.get("branch_code") or None,
            "branch_name": doc.get("branch_name") or None,
        }
        after = {
            "branch_id": snap["branch_id"],
            "branch_code": snap["branch_code"],
            "branch_name": snap["branch_name"],
        }
        actor = actor_snapshot(user)
        fields = {**snap, **stamp_update(user)}

        async def _txn_body(session):
            global _branch_txn_body_runs, _fail_cas_with_transient_once
            _branch_txn_body_runs += 1
            current = await db[COLLECTION].find_one(
                {"id": doc["id"]}, {"_id": 0}, session=session,
            )
            if not current:
                raise HTTPException(
                    404, f"Pesanan Pembelian '{po_id}' tidak ditemukan."
                )
            if is_cancelled(current):
                raise HTTPException(
                    400, "Pesanan sudah dibatalkan; cabang tidak dapat diubah."
                )
            if str(current.get("branch_id") or "").strip():
                raise HTTPException(400, BRANCH_ALREADY_SET_MSG)

            # Authoritative GR consistency under the transaction snapshot.
            await assert_po_receipts_match_branch(
                db, doc["id"], snap["branch_id"], session=session,
            )

            if _fail_cas_with_transient_once:
                _fail_cas_with_transient_once = False
                raise OperationFailure(
                    "forced transient before CAS",
                    112,
                    details={"errorLabels": ["TransientTransactionError"]},
                )

            result = await db[COLLECTION].update_one(
                {"id": doc["id"], **_BRANCH_UNSET_CAS},
                {"$set": fields},
                session=session,
            )
            if result.matched_count != 1:
                raise HTTPException(400, BRANCH_ALREADY_SET_MSG)

            if _fail_after_branch_cas_before_audit:
                raise RuntimeError("forced abort after branch CAS before audit")

            await db[AUDIT_COLLECTION].insert_one(
                {
                    "id": str(uuid.uuid4()),
                    "action": AUDIT_ACTION_BRANCH_SET,
                    "entity_type": "purchase_order",
                    "entity_id": doc["id"],
                    "po_number": doc.get("po_number"),
                    "actor_email": actor.get("email"),
                    "actor_role": actor.get("role"),
                    "actor_name": actor.get("name"),
                    "actor_id": actor_user_id(user),
                    "before": before,
                    "after": after,
                    "reason": reason,
                    "note": reason,
                    "at": now_iso(),
                },
                session=session,
            )

        try:
            async with await db.client.start_session() as session:
                await _run_po_branch_transaction(session, _txn_body)
        except HTTPException:
            raise
        except PyMongoError as exc:
            # Prefer observed state over assuming failure (esp. unknown commit).
            current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
            if current and str(current.get("branch_id") or "").strip() == snap["branch_id"]:
                audit_n = await db[AUDIT_COLLECTION].count_documents(
                    {"action": AUDIT_ACTION_BRANCH_SET, "entity_id": doc["id"]}
                )
                if audit_n >= 1:
                    return await get_purchase_order(db, doc["id"])
            if exc.has_error_label("UnknownTransactionCommitResult"):
                raise HTTPException(409, BRANCH_OUTCOME_UNKNOWN_MSG) from exc
            if exc.has_error_label("TransientTransactionError"):
                raise HTTPException(
                    409,
                    "Penetapan Cabang Pembelian gagal karena perubahan bersamaan.",
                ) from exc
            raise

        return await get_purchase_order(db, doc["id"])


async def cancel_purchase_order(db, po_id: str, user: dict, reason: str = "") -> dict:
    """Soft-cancel PO when no active receipt/invoice. Recalculates PR via active PO aggregate."""
    reason = str(reason or "").strip()

    async with get_named_lock("purchase_order"):
        doc = await db[COLLECTION].find_one({"id": str(po_id)}, {"_id": 0})
        if not doc:
            doc = await db[COLLECTION].find_one({"po_number": str(po_id)}, {"_id": 0})
        if not doc:
            raise HTTPException(404, f"Pesanan Pembelian '{po_id}' tidak ditemukan.")

        if is_cancelled(doc):
            return await enrich_purchase_order(db, doc)

        enriched = await enrich_purchase_order(db, doc)
        received = float((enriched.get("receiving_progress") or {}).get("received_qty") or 0)
        invoiced = float((enriched.get("invoice_progress") or {}).get("invoiced_qty") or 0)
        if received > 0:
            raise HTTPException(409, "Pesanan tidak dapat dihapus karena barang sudah pernah diterima.")
        if invoiced > 0:
            raise HTTPException(409, "Pesanan tidak dapat dihapus karena sudah memiliki faktur.")

        fields = cancellation_fields(user, reason)
        await db[COLLECTION].update_one({"id": doc["id"]}, {"$set": fields})
        doc.update(fields)
        return await enrich_purchase_order(db, doc)
