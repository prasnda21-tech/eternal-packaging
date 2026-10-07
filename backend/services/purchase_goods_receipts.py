"""PO-linked goods receipts shown on existing Penerimaan Barang page.

Does NOT write Accurate receipt documents.
Does NOT change physical Stock Saat Ini (Accurate aggregate remains source of truth).
Over-receipt vs exact PO line is rejected.
Accurate receipt lines may be allocated to exact PO lines via overlay collection.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.purchase_common import (
    PREFIX_GOODS_RECEIPT,
    actor_snapshot,
    cancellation_fields,
    derive_child_number,
    get_named_lock,
    is_cancelled,
    now_iso,
    parse_qty,
    qty_le,
    stamp_create,
    with_active_filter,
)
from services.purchase_orders import get_purchase_order
from pymongo.errors import DuplicateKeyError, PyMongoError

from services.physical_stock_engine import (
    execute_durable_stock_event,
)
from services.stock_ledger import (
    MOVEMENT_KOREKSI,
    MOVEMENT_PENERIMAAN,
    MOVEMENT_REVERSAL_PENERIMAAN,
    MOVEMENT_STOK_MASUK,
    record_stock_movement,
    safe_float,
)

COLLECTION = "purchase_goods_receipts"
ALLOC_COLLECTION = "purchase_receipt_po_links"
INV_COLLECTION = "purchase_invoices"
DEFAULT_RECEIPT_CANCEL_REASON = "Pembatalan penerimaan"

_fail_before_receipt_insert = False
_fail_after_r2_movements = False
_fail_before_cancel_update = False


def set_fail_before_receipt_insert(enabled: bool) -> None:
    global _fail_before_receipt_insert
    _fail_before_receipt_insert = bool(enabled)


def set_fail_after_r2_movements(enabled: bool) -> None:
    global _fail_after_r2_movements
    _fail_after_r2_movements = bool(enabled)


def set_fail_before_cancel_update(enabled: bool) -> None:
    global _fail_before_cancel_update
    _fail_before_cancel_update = bool(enabled)


def _whole_received_qty(value: Any) -> int:
    number = float(value)
    if not number.is_integer():
        raise HTTPException(400, "Qty diterima harus bilangan bulat.")
    qty = int(number)
    if qty <= 0:
        raise HTTPException(400, "Qty diterima harus lebih besar dari 0.")
    return qty


def _receipt_line_identity(lines: List[dict]) -> List[tuple]:
    rows = []
    for line in lines or []:
        rows.append((
            str(line.get("purchase_order_line_id") or ""),
            str(line.get("master_item_id") or ""),
            _whole_received_qty(line.get("received_qty")),
        ))
    return sorted(rows)


def _same_receipt_payload(existing: dict, spec: dict) -> bool:
    if str(existing.get("purchase_order_id") or "") != spec["purchase_order_id"]:
        return False
    if str(existing.get("branch_id") or "") != spec["branch_id"]:
        return False
    if str(existing.get("warehouse_id") or "") != spec["warehouse_id"]:
        return False
    return _receipt_line_identity(existing.get("lines") or []) == spec["lines"]




async def _active_invoice_count_for_receipt(db, receipt_id: str) -> int:
    return await db[INV_COLLECTION].count_documents(
        with_active_filter({
            "$or": [
                {"receipt_id": str(receipt_id)},
                {"goods_receipt_id": str(receipt_id)},
            ],
        })
    )


async def _stock_reversal_plan(db, receipt_id: str) -> Dict[str, Any]:
    """Return unreversed receipt movements and validate a safe physical reversal."""
    rid = str(receipt_id)
    originals = await db.stock_movements.find({
        "reference_type": "goods_receipt",
        "reference_id": rid,
        "movement_type": {"$in": [MOVEMENT_PENERIMAAN, MOVEMENT_STOK_MASUK]},
        "status": {"$nin": ["VOID", "CANCELLED"]},
    }, {"_id": 0}).to_list(None)
    reversals = await db.stock_movements.find({
        "movement_type": MOVEMENT_REVERSAL_PENERIMAAN,
        "goods_receipt_id": rid,
    }, {"_id": 0}).to_list(None)
    reversed_ids = {
        str(m.get("reversal_of_movement_id") or "")
        for m in reversals
        if m.get("reversal_of_movement_id")
    }
    unreversed_originals = [
        m for m in originals
        if str(m.get("id") or m.get("event_key") or "") not in reversed_ids
    ]
    pending_originals: List[dict] = []
    superseded_originals: List[dict] = []
    for movement in unreversed_originals:
        barrier = await db.stock_movements.find_one(
            {
                "master_item_id": movement.get("master_item_id"),
                "created_at": {"$gt": movement.get("created_at")},
                "movement_type": MOVEMENT_KOREKSI,
                "reference_type": {
                    "$in": ["stock_opname", "opening_balance_correction"],
                },
                "status": {"$nin": ["VOID", "CANCELLED"]},
            },
            {"_id": 0, "id": 1, "event_key": 1, "created_at": 1, "reference_type": 1},
            sort=[("created_at", 1)],
        )
        if barrier:
            superseded_originals.append({**movement, "_absolute_barrier": barrier})
        else:
            pending_originals.append(movement)

    required: Dict[str, float] = {}
    for movement in pending_originals:
        mid = str(movement.get("master_item_id") or "").strip()
        qty = abs(safe_float(movement.get("delta", movement.get("base_qty", 0))))
        if mid and qty > 0:
            required[mid] = safe_float(required.get(mid, 0)) + qty

    unsafe_reason = ""
    for mid, qty in required.items():
        state = await db.product_stock_state.find_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}]},
            {"_id": 0},
        )
        if not state or not state.get("initialized") or state.get("status") != "INITIALIZED":
            unsafe_reason = f"Stok V2 item '{mid}' belum initialized."
            break
        pending = state.get("pending_event")
        if pending:
            unsafe_reason = f"Ada mutasi stock lain yang sedang berjalan untuk item '{mid}'."
            break
        total = safe_float(state.get("total_qty"))
        unprinted = safe_float(state.get("unprinted_qty"))
        if total + 1e-9 < qty or unprinted + 1e-9 < qty:
            unsafe_reason = (
                f"Stock fisik belum dicetak item '{mid}' tidak cukup untuk reversal "
                f"(dibutuhkan {qty:g}, tersedia {min(total, unprinted):g})."
            )
            break

    return {
        "movement_count": len(originals),
        "reversal_count": len(reversals),
        "pending_originals": pending_originals,
        "superseded_originals": superseded_originals,
        "blocked": bool(unsafe_reason),
        "block_reason": unsafe_reason,
    }


async def _stock_dependency_for_receipt(db, receipt_id: str) -> Dict[str, Any]:
    plan = await _stock_reversal_plan(db, receipt_id)
    return {
        "movement_count": plan["movement_count"],
        "reversal_count": plan["reversal_count"],
        "blocked": plan["blocked"],
        "block_reason": plan["block_reason"],
        "requires_reversal": bool(
            plan["pending_originals"] or plan["superseded_originals"]
        ),
    }


async def enrich_goods_receipt(db, doc: dict) -> dict:
    out = dict(doc)
    rid = str(out.get("id") or "")
    if is_cancelled(out):
        out["status"] = "CANCELLED"
        out["can_cancel"] = False
        out["cancel_block_reason"] = "Penerimaan sudah dibatalkan."
        return out

    inv_count = await _active_invoice_count_for_receipt(db, rid) if rid else 0
    stock = await _stock_dependency_for_receipt(db, rid) if rid else {"blocked": False}
    out["status"] = "ACTIVE"
    if inv_count > 0:
        out["can_cancel"] = False
        out["cancel_block_reason"] = (
            "Penerimaan tidak dapat dihapus karena sudah memiliki faktur. "
            "Batalkan faktur terkait terlebih dahulu."
        )
    elif stock.get("blocked"):
        out["can_cancel"] = False
        out["cancel_block_reason"] = stock.get("block_reason") or "Reversal stock belum aman."
    else:
        out["can_cancel"] = True
        out["cancel_block_reason"] = ""
    out["active_invoice_count"] = inv_count
    out["stock_dependency"] = stock
    return out


async def get_purchase_goods_receipt(db, receipt_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(receipt_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Penerimaan Barang '{receipt_id}' tidak ditemukan.")
    out = await enrich_goods_receipt(db, doc)
    from services.purchase_goods_receipt_posting import attach_goods_receipt_accounting_fields

    await attach_goods_receipt_accounting_fields(db, [out])
    return out


async def apply_receipt_stock_reversals(
    db,
    receipt_key: str,
    *,
    receipt_number: str = "",
    supplier: str = "SUPPLIER",
    reason: str = "",
    user: Optional[dict] = None,
    order_id: Optional[str] = None,
) -> List[str]:
    """Append idempotent REVERSAL_PENERIMAAN for one receipt key (PO GR or MANUAL).

    Honors absolute-adjustment barrier (stock_opname / opening_balance_correction):
    superseded originals get audit-only reversal with effective_delta 0.
    """
    rid = str(receipt_key or "").strip()
    if not rid:
        return []
    why = str(reason or "").strip()
    plan = await _stock_reversal_plan(db, rid)
    if plan.get("blocked"):
        raise HTTPException(
            409,
            plan.get("block_reason") or "Reversal stock Penerimaan Barang tidak aman.",
        )

    supplier_name = str(supplier or "SUPPLIER").strip() or "SUPPLIER"
    rc_num = str(receipt_number or "").strip()
    reversal_ids: List[str] = []

    for movement in plan.get("superseded_originals") or []:
        original_id = str(movement.get("id") or movement.get("event_key") or "").strip()
        mid = str(movement.get("master_item_id") or "").strip()
        if not original_id or not mid:
            continue
        reversal_id = f"stock_reverse:goods_receipt:{rid}:{original_id}"
        state = await db.product_stock_state.find_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}]},
            {"_id": 0},
        )
        current_total = safe_float((state or {}).get("total_qty"))
        barrier = movement.get("_absolute_barrier") or {}
        reversed_movement = await record_stock_movement(
            db,
            movement_type=MOVEMENT_REVERSAL_PENERIMAAN,
            master_item_id=mid,
            accurate_item_id=movement.get("accurate_item_id"),
            transaction_qty=0,
            transaction_uom=movement.get("base_uom") or movement.get("transaction_uom"),
            source_entity="GUDANG_UTAMA",
            destination_entity=supplier_name,
            reference_number=rc_num,
            reference_type="goods_receipt_reversal",
            reference_id=rid,
            description=(
                f"Pembatalan Penerimaan Barang tanpa perubahan stock "
                f"(superseded oleh adjustment absolut): {why}"
            ),
            order_id=order_id,
            line_idx=movement.get("line_idx"),
            line_id=movement.get("line_id"),
            delta_unprinted=0,
            delta_printed=0,
            event_key=reversal_id,
            movement_id=reversal_id,
            balance_before=current_total,
            balance_after=current_total,
            unprinted_before=safe_float((state or {}).get("unprinted_qty")),
            unprinted_after=safe_float((state or {}).get("unprinted_qty")),
            printed_before=safe_float((state or {}).get("printed_qty")),
            printed_after=safe_float((state or {}).get("printed_qty")),
            change_metadata={
                "goods_receipt_id": rid,
                "reversal_of_movement_id": original_id,
                "cancel_reason": why,
                "effective_delta": 0,
                "superseded_by_absolute_adjustment": True,
                "superseded_by_movement_id": (
                    barrier.get("id") or barrier.get("event_key")
                ),
            },
            user=user,
        )
        reversal_ids.append(str(reversed_movement.get("id") or reversal_id))

    for movement in plan.get("pending_originals") or []:
        original_id = str(movement.get("id") or movement.get("event_key") or "").strip()
        mid = str(movement.get("master_item_id") or "").strip()
        qty = abs(safe_float(movement.get("delta", movement.get("base_qty", 0))))
        if not original_id or not mid or qty <= 0:
            continue
        reversal_id = f"stock_reverse:goods_receipt:{rid}:{original_id}"
        reversed_movement = await execute_durable_stock_event(
            db,
            event_id=reversal_id,
            master_item_id=mid,
            movement_type=MOVEMENT_REVERSAL_PENERIMAAN,
            transaction_qty=-qty,
            transaction_uom=movement.get("base_uom") or movement.get("transaction_uom"),
            source_entity="GUDANG_UTAMA",
            destination_entity=supplier_name,
            reference_number=rc_num,
            reference_type="goods_receipt_reversal",
            reference_id=rid,
            description=f"Pembatalan Penerimaan Barang: {why}",
            order_id=order_id,
            line_idx=movement.get("line_idx"),
            line_id=movement.get("line_id"),
            change_metadata={
                "goods_receipt_id": rid,
                "reversal_of_movement_id": original_id,
                "cancel_reason": why,
                "effective_delta": -qty,
                "superseded_by_absolute_adjustment": False,
            },
            user=user,
        )
        reversal_ids.append(str(reversed_movement.get("id") or reversal_id))

    existing_reversals = await db.stock_movements.find({
        "movement_type": MOVEMENT_REVERSAL_PENERIMAAN,
        "goods_receipt_id": rid,
    }, {"_id": 0, "id": 1}).to_list(None)
    return list(dict.fromkeys(
        reversal_ids + [str(m.get("id") or "") for m in existing_reversals if m.get("id")]
    ))


async def cancel_purchase_goods_receipt(
    db,
    receipt_id: str,
    user: dict,
    reason: str = "",
    *,
    claim_token: Optional[str] = None,
    accounting_reversal_owned: bool = False,
) -> dict:
    """Soft-cancel PO-linked goods receipt.

    - Never hard-deletes
    - Blocks when active invoices or unsafe downstream stock use exists
    - Appends an idempotent reversal for every posted receipt movement
    """
    reason = str(reason or "").strip()

    async with get_named_lock("purchase_goods_receipt"):
        doc = await db[COLLECTION].find_one({"id": str(receipt_id)}, {"_id": 0})
        if not doc:
            doc = await db[COLLECTION].find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
        if not doc:
            raise HTTPException(404, f"Penerimaan Barang '{receipt_id}' tidak ditemukan.")

        if is_cancelled(doc):
            return await get_purchase_goods_receipt(db, doc["id"])

        from services.purchase_goods_receipt_posting import (
            ACCOUNTING_REVERSED,
            CANCEL_CLAIM_MESSAGE,
            CANCEL_IN_PROGRESS_MESSAGE,
            RECOVERY_REQUIRED_CODE,
            RECOVERY_REQUIRED_MESSAGE,
            REVERSE_CLAIM_MESSAGE,
            accounting_status_from_journal,
            acquire_receipt_cancel_claim,
            cancel_claim_is_active,
            clear_receipt_cancel_claim,
            find_goods_receipt_source_journal,
            posting_claim_is_active,
            raise_if_journal_blocks_cancel,
            recovery_required,
            reverse_claim_is_active,
        )

        token = ""
        if not accounting_reversal_owned:
            if recovery_required(doc):
                raise HTTPException(409, {
                    "code": RECOVERY_REQUIRED_CODE,
                    "message": RECOVERY_REQUIRED_MESSAGE,
                })
            existing_journal = await find_goods_receipt_source_journal(db, doc["id"])
            if accounting_status_from_journal(existing_journal) == ACCOUNTING_REVERSED:
                raise HTTPException(409, {
                    "code": RECOVERY_REQUIRED_CODE,
                    "message": RECOVERY_REQUIRED_MESSAGE,
                })
            claimed = await acquire_receipt_cancel_claim(db, doc["id"], token=claim_token)
            if claimed is None:
                current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
                if current and is_cancelled(current):
                    return await get_purchase_goods_receipt(db, doc["id"])
                if current and posting_claim_is_active(current):
                    raise HTTPException(409, CANCEL_CLAIM_MESSAGE)
                if current and reverse_claim_is_active(current):
                    raise HTTPException(409, REVERSE_CLAIM_MESSAGE)
                if current and cancel_claim_is_active(current):
                    raise HTTPException(409, CANCEL_IN_PROGRESS_MESSAGE)
                raced = await find_goods_receipt_source_journal(db, doc["id"])
                raise_if_journal_blocks_cancel(raced)
                raise HTTPException(409, "Pembatalan penerimaan gagal karena perubahan bersamaan.")
            token = str(claimed.get("receipt_cancel_claim") or "")

        try:
            if not accounting_reversal_owned:
                journal = await find_goods_receipt_source_journal(db, doc["id"])
                raise_if_journal_blocks_cancel(journal)
                current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0}) or doc
                if posting_claim_is_active(current):
                    raise HTTPException(409, CANCEL_CLAIM_MESSAGE)
                if reverse_claim_is_active(current):
                    raise HTTPException(409, REVERSE_CLAIM_MESSAGE)
            return await _execute_operational_goods_receipt_cancel(db, doc, user, reason)
        finally:
            if token:
                await clear_receipt_cancel_claim(db, doc["id"], token=token)


async def _execute_operational_goods_receipt_cancel(
    db,
    doc: dict,
    user: dict,
    reason: str,
) -> dict:
        inv_count = await _active_invoice_count_for_receipt(db, doc["id"])
        if inv_count > 0:
            raise HTTPException(
                409,
                "Penerimaan tidak dapat dihapus karena sudah memiliki faktur. "
                "Batalkan faktur terkait terlebih dahulu.",
            )
        reason = str(reason or "").strip() or DEFAULT_RECEIPT_CANCEL_REASON

        if doc.get("r2_physical") is True:
            return await _cancel_r2_goods_receipt(db, doc, user, reason)

        reversal_ids = await apply_receipt_stock_reversals(
            db,
            doc["id"],
            receipt_number=doc.get("receipt_number") or "",
            supplier=doc.get("supplier") or "SUPPLIER",
            reason=reason,
            user=user,
            order_id=doc.get("purchase_order_id"),
        )
        # HPP reversal is durable and idempotent. Run it after Stock V2 reversal
        # and before marking the receipt cancelled so retry can finish only the
        # incomplete HPP side without creating another receipt or stock event.
        from services.hpp_cutover import reverse_goods_receipt_hpp
        await reverse_goods_receipt_hpp(db, doc, user)
        fields = cancellation_fields(user, reason)
        fields.update({
            "status": "CANCELLED",
            "reversal_movement_id": reversal_ids[0] if reversal_ids else None,
            "reversal_movement_ids": reversal_ids,
        })
        await db[COLLECTION].update_one(
            {"id": doc["id"], "cancelled": {"$ne": True}},
            {"$set": fields},
        )
        return await get_purchase_goods_receipt(db, doc["id"])


def _r2_http(exc: Exception) -> None:
    from services.physical_stock import (
        InsufficientPhysicalStockError,
        PhysicalStockConflictError,
        PhysicalStockValidationError,
    )
    if isinstance(exc, InsufficientPhysicalStockError):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, PhysicalStockConflictError):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, PhysicalStockValidationError):
        raise HTTPException(400, str(exc)) from exc
    raise exc


def _retryable_mongo(exc: BaseException) -> bool:
    if isinstance(exc, DuplicateKeyError):
        return True
    if isinstance(exc, PyMongoError) and (
        exc.has_error_label("TransientTransactionError")
        or exc.has_error_label("UnknownTransactionCommitResult")
    ):
        return True
    return getattr(exc, "code", None) in (112, 251)


async def _commit_r2_goods_receipt(
    db,
    doc: dict,
    *,
    user: dict,
    po_number: str,
    existing_count: int,
    warehouse_id: Optional[str],
) -> dict:
    from services.physical_stock import (
        PhysicalStockAbortError,
        _client,
        apply_purchase_receipt_lines,
        require_transaction_support,
        resolve_purchase_receipt_warehouse,
    )
    from services.r2_go_live import require_r2_authority_active

    actor_id = str((user or {}).get("id") or (user or {}).get("email") or "user")
    await require_r2_authority_active(db)
    await require_transaction_support(_client(db))
    spec_lines = _receipt_line_identity(doc.get("lines") or [])
    last_error = None
    number_index = existing_count + 1
    for _attempt in range(5):
        candidate = derive_child_number(po_number, PREFIX_GOODS_RECEIPT, number_index)
        while await db[COLLECTION].find_one({"receipt_number": candidate}, {"_id": 1}):
            number_index += 1
            candidate = derive_child_number(po_number, PREFIX_GOODS_RECEIPT, number_index)
        doc["receipt_number"] = candidate
        number_index += 1
        try:
            async with await _client(db).start_session() as session:
                async with session.start_transaction():
                    branch_id, resolved_warehouse = await resolve_purchase_receipt_warehouse(
                        db, session, warehouse_id=warehouse_id,
                    )
                    doc["branch_id"] = branch_id
                    doc["warehouse_id"] = resolved_warehouse
                    doc["r2_physical"] = True
                    for line in doc["lines"]:
                        line["branch_id"] = branch_id
                        line["warehouse_id"] = resolved_warehouse
                        line["received_qty"] = _whole_received_qty(line["received_qty"])
                    if doc.get("idempotency_key"):
                        existing = await db[COLLECTION].find_one(
                            {"idempotency_key": doc["idempotency_key"]},
                            {"_id": 0},
                            session=session,
                        )
                        if existing:
                            spec = {
                                "purchase_order_id": doc["purchase_order_id"],
                                "branch_id": branch_id,
                                "warehouse_id": resolved_warehouse,
                                "lines": spec_lines,
                            }
                            if not _same_receipt_payload(existing, spec):
                                from services.physical_stock import PhysicalStockConflictError
                                raise PhysicalStockConflictError(
                                    "Idempotency key sudah digunakan dengan payload yang berbeda"
                                )
                            return existing
                    if _fail_before_receipt_insert:
                        raise PhysicalStockAbortError("forced receipt persist abort")
                    await db[COLLECTION].insert_one(dict(doc), session=session)
                    await apply_purchase_receipt_lines(db, session, doc, actor_id=actor_id)
                    if _fail_after_r2_movements:
                        raise PhysicalStockAbortError("forced receipt r2 abort")
            doc.pop("_id", None)
            return doc
        except DuplicateKeyError as exc:
            last_error = exc
            if doc.get("idempotency_key") and "idempotency_key" in str(exc):
                existing = await db[COLLECTION].find_one(
                    {"idempotency_key": doc["idempotency_key"]},
                    {"_id": 0},
                )
                if existing:
                    spec = {
                        "purchase_order_id": doc["purchase_order_id"],
                        "branch_id": doc.get("branch_id") or "",
                        "warehouse_id": doc.get("warehouse_id") or "",
                        "lines": spec_lines,
                    }
                    if _same_receipt_payload(existing, spec):
                        return existing
                    raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda") from exc
            if "receipt_number" in str(exc):
                continue
            raise HTTPException(409, "Dokumen duplikat terdeteksi.") from exc
        except (PhysicalStockAbortError, HTTPException):
            raise
        except Exception as exc:
            if isinstance(exc, PyMongoError) and _retryable_mongo(exc):
                last_error = exc
                continue
            _r2_http(exc)
    raise HTTPException(409, f"Gagal menyimpan penerimaan. {last_error}")


async def _cancel_r2_goods_receipt(db, doc: dict, user: dict, reason: str) -> dict:
    from services.physical_stock import (
        LEDGER_COLLECTION,
        MOVEMENT_PURCHASE_RECEIPT,
        PhysicalStockAbortError,
        _client,
        reverse_movement_in_session,
    )

    actor_id = str((user or {}).get("id") or (user or {}).get("email") or "user")
    originals = await db[LEDGER_COLLECTION].find(
        {
            "movement_type": MOVEMENT_PURCHASE_RECEIPT,
            "metadata.receipt_id": doc["id"],
        },
        {"_id": 0, "id": 1},
    ).to_list(500)
    if not originals:
        raise HTTPException(409, "Penerimaan tidak memiliki movement stok fisik yang dapat dibatalkan.")
    reversal_ids = []
    try:
        async with await _client(db).start_session() as session:
            async with session.start_transaction():
                current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0}, session=session)
                if current and is_cancelled(current):
                    return await get_purchase_goods_receipt(db, doc["id"])
                for original in originals:
                    movement = await reverse_movement_in_session(
                        db,
                        session,
                        original["id"],
                        actor_id=actor_id,
                        idempotency_key=f"purchase-receipt-reversal:{doc['id']}:{original['id']}",
                        reason=reason,
                    )
                    reversal_ids.append(movement["id"])
                if _fail_before_cancel_update:
                    raise PhysicalStockAbortError("forced receipt cancel abort")
                fields = cancellation_fields(user, reason)
                fields.update({
                    "status": "CANCELLED",
                    "reversal_movement_id": reversal_ids[0] if reversal_ids else None,
                    "reversal_movement_ids": reversal_ids,
                })
                result = await db[COLLECTION].update_one(
                    {"id": doc["id"], "cancelled": {"$ne": True}},
                    {"$set": fields},
                    session=session,
                )
                if result.matched_count != 1:
                    raise HTTPException(409, "Pembatalan penerimaan gagal karena perubahan bersamaan.")
    except (PhysicalStockAbortError, HTTPException):
        raise
    except Exception as exc:
        _r2_http(exc)
    from services.hpp_cutover import reverse_goods_receipt_hpp
    await reverse_goods_receipt_hpp(db, doc, user)
    return await get_purchase_goods_receipt(db, doc["id"])


async def get_po_receipt_preview(db, purchase_order_id: str) -> dict:
    po = await get_purchase_order(db, purchase_order_id)
    lines = []
    for li in po.get("lines") or []:
        remain = float(li.get("remaining_receivable_qty") or 0)
        lines.append({
            "purchase_order_line_id": li.get("line_id"),
            "line_idx": li.get("line_idx"),
            "line_id": li.get("line_id"),
            "request_line_id": li.get("request_line_id"),
            "master_item_id": li.get("master_item_id") or "",
            "accurate_item_id": li.get("accurate_item_id") or "",
            "item_code": li.get("item_code") or "",
            "item_name": li.get("item_name") or "",
            "unit": li.get("unit") or "pcs",
            "ordered_qty": li.get("ordered_qty") or 0,
            "cumulative_received_qty": li.get("cumulative_received_qty") or 0,
            "remaining_receivable_qty": remain,
            "received_qty": remain,
            "unit_price": li.get("unit_price") or 0,
        })
    return {
        "purchase_order_id": po["id"],
        "po_number": po.get("po_number"),
        "request_id": po.get("request_id"),
        "request_number": po.get("request_number"),
        "supplier": po.get("supplier"),
        "order_date": po.get("order_date"),
        "lines": lines,
        "receiving_progress": po.get("receiving_progress") or {},
    }


async def create_purchase_goods_receipt(db, payload: Dict[str, Any], user: dict) -> dict:
    po_id = str(payload.get("purchase_order_id") or "").strip()
    if not po_id:
        raise HTTPException(400, "ID Pesanan Pembelian (purchase_order_id) wajib diisi.")

    async with get_named_lock("purchase_goods_receipt"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                retry_lines = []
                for inp in payload.get("lines") or payload.get("line_items") or []:
                    po_line_id = str(inp.get("purchase_order_line_id") or inp.get("line_id") or "").strip()
                    raw_qty = inp.get("received_qty", inp.get("quantity"))
                    if not po_line_id or raw_qty in (None, ""):
                        continue
                    retry_lines.append((po_line_id, _whole_received_qty(raw_qty)))
                existing_lines = [
                    (
                        str(line.get("purchase_order_line_id") or ""),
                        _whole_received_qty(line.get("received_qty")),
                    )
                    for line in (existing.get("lines") or [])
                ]
                requested_wh = str(payload.get("warehouse_id") or "").strip()
                same_lines = sorted(existing_lines) == sorted(retry_lines)
                same_po = str(existing.get("purchase_order_id") or "") == po_id
                same_wh = (not requested_wh) or requested_wh == str(existing.get("warehouse_id") or "")
                if not (same_po and same_lines and same_wh):
                    raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
                from services.hpp_cutover import recognize_posted_goods_receipt
                await recognize_posted_goods_receipt(db, existing, user)
                out = await enrich_goods_receipt(db, existing)
                from services.purchase_goods_receipt_posting import attach_goods_receipt_accounting_fields
                await attach_goods_receipt_accounting_fields(db, [out])
                return out

        preview = await get_po_receipt_preview(db, po_id)
        po_live = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0, "cancelled": 1, "receiving_status": 1})
        if is_cancelled(po_live) or str((po_live or {}).get("receiving_status") or "").upper() == "DIBATALKAN":
            raise HTTPException(400, "Pesanan sudah dibatalkan; penerimaan tidak dapat dibuat.")
        by_line = {str(l["purchase_order_line_id"]): l for l in preview["lines"]}
        input_lines = payload.get("lines") or payload.get("line_items") or []
        if not input_lines:
            raise HTTPException(400, "Minimal satu baris penerimaan wajib diisi.")

        calculated = []
        seen = set()
        for inp in input_lines:
            po_line_id = str(inp.get("purchase_order_line_id") or inp.get("line_id") or "").strip()
            src = by_line.get(po_line_id)
            if src is None:
                raise HTTPException(400, f"Baris PO '{po_line_id}' tidak ditemukan.")
            if po_line_id in seen:
                raise HTTPException(400, "Baris PO tidak boleh digabung pada satu penerimaan.")
            seen.add(po_line_id)
            raw_qty = inp.get("received_qty", inp.get("quantity"))
            if raw_qty in (None, ""):
                continue
            qty = _whole_received_qty(parse_qty(raw_qty, f"Qty diterima '{src['item_name']}'"))
            remain = float(src["remaining_receivable_qty"] or 0)
            if not qty_le(qty, remain):
                raise HTTPException(
                    400,
                    f"Qty diterima ({qty}) untuk '{src['item_name']}' melebihi sisa PO ({remain}).",
                )
            calculated.append({
                "line_idx": src["line_idx"],
                "line_id": str(uuid.uuid4()),
                "purchase_order_line_id": src["purchase_order_line_id"],
                "request_line_id": src.get("request_line_id") or "",
                "master_item_id": src["master_item_id"],
                "accurate_item_id": src["accurate_item_id"],
                "item_code": src["item_code"],
                "item_name": src["item_name"],
                "unit": src["unit"],
                "received_qty": qty,
                "unit_price": src["unit_price"],
            })

        if not calculated:
            raise HTTPException(400, "Total kuantitas diterima harus lebih besar dari 0.")

        ts = now_iso()
        existing_count = await db[COLLECTION].count_documents({"purchase_order_id": preview["purchase_order_id"]})

        doc = {
            "id": str(uuid.uuid4()),
            "purchase_order_id": preview["purchase_order_id"],
            "po_number": preview["po_number"],
            "request_id": preview.get("request_id"),
            "request_number": preview.get("request_number"),
            "supplier": preview.get("supplier"),
            "receipt_date": str(payload.get("receipt_date") or ts[:10]).strip()[:10],
            "notes": str(payload.get("notes") or "").strip(),
            "source": "po_linked",
            "lines": calculated,
            **stamp_create(user),
        }
        if idem:
            doc["idempotency_key"] = idem

        po_doc = await db.purchase_orders.find_one({"id": preview["purchase_order_id"]}, {"_id": 0})
        doc["physical_stock_snapshot"] = (po_doc or {}).get("physical_stock_snapshot")
        saved = await _commit_r2_goods_receipt(
            db,
            doc,
            user=user,
            po_number=preview["po_number"],
            existing_count=existing_count,
            warehouse_id=str(payload.get("warehouse_id") or "").strip() or None,
        )
        from services.hpp_cutover import register_baseline_participants, recognize_posted_goods_receipt
        await register_baseline_participants(db, saved)
        await recognize_posted_goods_receipt(db, saved, user)
        out = await enrich_goods_receipt(db, saved)
        from services.purchase_goods_receipt_posting import attach_goods_receipt_accounting_fields

        await attach_goods_receipt_accounting_fields(db, [out])
        return out


async def list_purchase_goods_receipts(
    db,
    q: Optional[str] = None,
    purchase_order_id: Optional[str] = None,
    include_cancelled: bool = False,
) -> List[dict]:
    filters: Dict[str, Any] = {}
    if purchase_order_id:
        filters["purchase_order_id"] = str(purchase_order_id)
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"receipt_number": {"$regex": needle, "$options": "i"}},
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"supplier": {"$regex": needle, "$options": "i"}},
        ]
    query = filters if include_cancelled else with_active_filter(filters)
    rows = await db[COLLECTION].find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)
    enriched = [await enrich_goods_receipt(db, r) for r in rows]
    from services.purchase_goods_receipt_posting import attach_goods_receipt_accounting_fields

    await attach_goods_receipt_accounting_fields(db, enriched)
    return enriched


async def allocate_accurate_receipt_line(db, payload: Dict[str, Any], user: dict) -> dict:
    """Overlay only — Accurate receipt documents are not mutated."""
    accurate_receipt_id = str(payload.get("accurate_receipt_id") or "").strip()
    accurate_line_id = str(payload.get("accurate_line_id") or "").strip()
    po_id = str(payload.get("purchase_order_id") or "").strip()
    po_line_id = str(payload.get("purchase_order_line_id") or "").strip()
    if not all([accurate_receipt_id, accurate_line_id, po_id, po_line_id]):
        raise HTTPException(400, "accurate_receipt_id, accurate_line_id, purchase_order_id, dan purchase_order_line_id wajib.")

    async with get_named_lock("purchase_goods_receipt"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[ALLOC_COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                return existing

        preview = await get_po_receipt_preview(db, po_id)
        src = next((l for l in preview["lines"] if str(l["purchase_order_line_id"]) == po_line_id), None)
        if src is None:
            raise HTTPException(400, "Baris Pesanan Pembelian tidak ditemukan.")
        qty = parse_qty(payload.get("allocated_qty", payload.get("received_qty")), "Qty alokasi")
        remain = float(src["remaining_receivable_qty"] or 0)
        if not qty_le(qty, remain):
            raise HTTPException(400, f"Qty alokasi ({qty}) melebihi sisa PO ({remain}).")

        ts = now_iso()
        doc = {
            "id": str(uuid.uuid4()),
            "accurate_receipt_id": accurate_receipt_id,
            "accurate_line_id": accurate_line_id,
            "purchase_order_id": preview["purchase_order_id"],
            "purchase_order_line_id": po_line_id,
            "po_number": preview["po_number"],
            "allocated_qty": qty,
            "unit_price": src["unit_price"],
            "receipt_date": str(payload.get("receipt_date") or ts[:10]).strip()[:10],
            "created_at": ts,
            "created_by": actor_snapshot(user),
        }
        if idem:
            doc["idempotency_key"] = idem
        await db[ALLOC_COLLECTION].insert_one(doc)
        doc.pop("_id", None)
        return doc
