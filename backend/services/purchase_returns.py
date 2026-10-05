"""Canonical purchase return against an exact goods-receipt line.

Draft has no stock effect. Confirmation posts R2 PURCHASE_RETURN (-qty) in one
transaction. Cancellation of a confirmed return appends R2 REVERSAL and keeps
the original movement. Financial supplier credit is not created here.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError, PyMongoError

from services.purchase_common import (
    PREFIX_PURCHASE_RETURN,
    cancellation_fields,
    derive_child_number,
    get_named_lock,
    is_cancelled,
    now_iso,
    parse_qty,
    qty_le,
    stamp_create,
    stamp_update,
    with_active_filter,
)

COLLECTION = "purchase_returns"
RECEIPT_COLLECTION = "purchase_goods_receipts"
INVOICE_COLLECTION = "purchase_invoices"

STATUS_DRAFT = "DRAFT"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_CANCELLED = "CANCELLED"
STATUS_REVERSED = "REVERSED"

_fail_after_r2_movements = False


class _AlreadyConfirmed(Exception):
    """Abort the in-flight transaction and return the winning confirmed document."""


def set_fail_after_r2_movements(enabled: bool) -> None:
    global _fail_after_r2_movements
    _fail_after_r2_movements = bool(enabled)


def _whole_qty(value: Any, label: str) -> int:
    number = float(parse_qty(value, label))
    if not number.is_integer():
        raise HTTPException(400, f"{label} harus bilangan bulat.")
    qty = int(number)
    if qty <= 0:
        raise HTTPException(400, f"{label} harus lebih besar dari 0.")
    return qty


def _line_fingerprint(lines: List[dict]) -> tuple:
    rows = []
    for line in lines or []:
        rows.append((
            str(line.get("purchase_goods_receipt_line_id") or ""),
            int(line.get("return_qty") or 0),
        ))
    return tuple(sorted(rows))


def _same_create(existing: dict, spec_lines: List[dict], purchase_order_id: str) -> bool:
    return (
        str(existing.get("purchase_order_id") or "") == str(purchase_order_id)
        and _line_fingerprint(existing.get("lines") or []) == _line_fingerprint(spec_lines)
    )


async def confirmed_return_qty_by_po_line(db, purchase_order_id: str) -> Dict[str, float]:
    rows = await db[COLLECTION].find(
        {"purchase_order_id": str(purchase_order_id), "status": STATUS_CONFIRMED, "cancelled": {"$ne": True}},
        {"_id": 0, "lines": 1},
    ).to_list(2000)
    out: Dict[str, float] = {}
    for doc in rows:
        for line in doc.get("lines") or []:
            key = str(line.get("purchase_order_line_id") or "")
            if not key:
                continue
            out[key] = out.get(key, 0.0) + float(line.get("return_qty") or 0)
    return out


async def confirmed_return_qty_by_receipt_line(db, receipt_id: str) -> Dict[str, float]:
    rows = await db[COLLECTION].find(
        {
            "status": STATUS_CONFIRMED,
            "cancelled": {"$ne": True},
            "lines.purchase_goods_receipt_id": str(receipt_id),
        },
        {"_id": 0, "lines": 1},
    ).to_list(2000)
    out: Dict[str, float] = {}
    for doc in rows:
        for line in doc.get("lines") or []:
            if str(line.get("purchase_goods_receipt_id") or "") != str(receipt_id):
                continue
            key = str(line.get("purchase_goods_receipt_line_id") or "")
            if not key:
                continue
            out[key] = out.get(key, 0.0) + float(line.get("return_qty") or 0)
    return out


def _returnable(received_qty: Any, returned_qty: Any) -> int:
    received = int(float(received_qty or 0))
    returned = int(float(returned_qty or 0))
    return max(0, received - returned)


async def _active_invoice_trace(db, session, purchase_order_id: str, purchase_order_line_id: str) -> dict:
    invoices = await db[INVOICE_COLLECTION].find(
        with_active_filter({"purchase_order_id": str(purchase_order_id)}),
        {"_id": 0, "id": 1, "invoice_number": 1, "lines": 1},
        session=session,
    ).to_list(2000)
    related = []
    for invoice in invoices:
        for line in invoice.get("lines") or []:
            if str(line.get("purchase_order_line_id") or "") != str(purchase_order_line_id):
                continue
            related.append({
                "purchase_invoice_id": invoice.get("id"),
                "invoice_number": invoice.get("invoice_number"),
                "purchase_invoice_line_id": line.get("line_id"),
                "invoice_qty": line.get("invoice_qty") or line.get("quantity") or 0,
                "purchase_order_line_id": purchase_order_line_id,
            })
    return {
        "related_invoice_lines": related,
        "invoice_ownership_ambiguous": len(related) > 1,
    }


def _r2_http(exc: Exception) -> None:
    from services.physical_stock import (
        InsufficientPhysicalStockError,
        PhysicalStockAbortError,
        PhysicalStockConflictError,
        PhysicalStockValidationError,
    )
    if isinstance(exc, PhysicalStockAbortError):
        raise exc
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


async def _load_receipt_line(db, receipt_id: str, line_id: str) -> tuple:
    receipt = await db[RECEIPT_COLLECTION].find_one({"id": str(receipt_id)}, {"_id": 0})
    if not receipt:
        raise HTTPException(400, "Penerimaan barang sumber tidak ditemukan.")
    if is_cancelled(receipt):
        raise HTTPException(400, "Penerimaan sudah dibatalkan; retur tidak dapat dibuat.")
    match = None
    for line in receipt.get("lines") or []:
        if str(line.get("line_id") or "") == str(line_id):
            match = line
            break
    if match is None:
        raise HTTPException(400, "Baris penerimaan sumber tidak dapat diidentifikasi.")
    if not str(match.get("master_item_id") or "").strip():
        raise HTTPException(400, "Barang pada baris penerimaan tidak dapat diidentifikasi.")
    if not str(match.get("warehouse_id") or receipt.get("warehouse_id") or "").strip():
        raise HTTPException(400, "Gudang penerimaan tidak dapat diidentifikasi.")
    if not str(match.get("branch_id") or receipt.get("branch_id") or "").strip():
        raise HTTPException(400, "Cabang penerimaan tidak dapat diidentifikasi.")
    if not str(match.get("purchase_order_line_id") or "").strip() or not str(receipt.get("purchase_order_id") or "").strip():
        raise HTTPException(400, "Baris pesanan pembelian sumber tidak dapat diidentifikasi.")
    return receipt, match


async def _claim_return_qty(db, session, receipt_id: str, line_id: str, received_qty: int, qty: int) -> bool:
    await db[RECEIPT_COLLECTION].update_one(
        {"id": receipt_id},
        {"$set": {"lines.$[line].confirmed_return_qty": 0}},
        array_filters=[{"line.line_id": line_id, "line.confirmed_return_qty": {"$exists": False}}],
        session=session,
    )
    result = await db[RECEIPT_COLLECTION].update_one(
        {"id": receipt_id},
        {"$inc": {"lines.$[line].confirmed_return_qty": int(qty)}},
        array_filters=[{
            "line.line_id": line_id,
            "line.confirmed_return_qty": {"$lte": int(received_qty) - int(qty)},
        }],
        session=session,
    )
    return result.modified_count == 1


async def _release_return_qty(db, session, receipt_id: str, line_id: str, qty: int) -> bool:
    result = await db[RECEIPT_COLLECTION].update_one(
        {"id": receipt_id},
        {"$inc": {"lines.$[line].confirmed_return_qty": -int(qty)}},
        array_filters=[{
            "line.line_id": line_id,
            "line.confirmed_return_qty": {"$gte": int(qty)},
        }],
        session=session,
    )
    return result.modified_count == 1


async def create_purchase_return(db, payload: Dict[str, Any], user: dict) -> dict:
    if str(payload.get("warehouse_id") or "").strip() or str(payload.get("branch_id") or "").strip():
        raise HTTPException(400, "Gudang retur mengikuti gudang penerimaan dan tidak dapat dipilih ulang.")
    input_lines = payload.get("lines") or payload.get("line_items") or []
    if not input_lines:
        raise HTTPException(400, "Minimal satu baris retur wajib diisi.")
    async with get_named_lock("purchase_return_create"):
        idem = str(payload.get("idempotency_key") or "").strip()
        calculated = []
        seen = set()
        purchase_order_id = ""
        po_number = ""
        supplier = ""
        receipt_ids = []
        for inp in input_lines:
            if str(inp.get("warehouse_id") or "").strip() or str(inp.get("branch_id") or "").strip():
                raise HTTPException(400, "Gudang retur mengikuti gudang penerimaan dan tidak dapat dipilih ulang.")
            receipt_id = str(inp.get("purchase_goods_receipt_id") or inp.get("receipt_id") or "").strip()
            receipt_line_id = str(inp.get("purchase_goods_receipt_line_id") or inp.get("receipt_line_id") or "").strip()
            if not receipt_id or not receipt_line_id:
                raise HTTPException(400, "Setiap baris retur wajib merujuk baris penerimaan yang pasti.")
            if receipt_line_id in seen:
                raise HTTPException(400, "Baris penerimaan yang sama tidak boleh diulang pada satu retur.")
            seen.add(receipt_line_id)
            receipt, source = await _load_receipt_line(db, receipt_id, receipt_line_id)
            po_id = str(receipt.get("purchase_order_id") or "")
            if purchase_order_id and po_id != purchase_order_id:
                raise HTTPException(400, "Semua baris retur harus berasal dari Pesanan Pembelian yang sama.")
            purchase_order_id = po_id
            po_number = receipt.get("po_number") or po_number
            supplier = receipt.get("supplier") or supplier
            qty = _whole_qty(inp.get("return_qty", inp.get("quantity")), f"Qty retur '{source.get('item_name') or 'barang'}'")
            already = float((await confirmed_return_qty_by_receipt_line(db, receipt_id)).get(receipt_line_id, 0))
            remain = _returnable(source.get("received_qty"), already)
            if qty > remain:
                raise HTTPException(
                    400,
                    f"Qty retur ({qty}) melebihi qty yang masih dapat diretur ({remain}).",
                )
            branch_id = str(source.get("branch_id") or receipt.get("branch_id"))
            warehouse_id = str(source.get("warehouse_id") or receipt.get("warehouse_id"))
            if receipt_id not in receipt_ids:
                receipt_ids.append(receipt_id)
            calculated.append({
                "line_id": str(uuid.uuid4()),
                "purchase_goods_receipt_id": receipt["id"],
                "purchase_goods_receipt_line_id": str(source.get("line_id")),
                "purchase_order_id": po_id,
                "purchase_order_line_id": str(source.get("purchase_order_line_id")),
                "master_item_id": str(source.get("master_item_id")),
                "branch_id": branch_id,
                "warehouse_id": warehouse_id,
                "item_code": source.get("item_code") or "",
                "item_name": source.get("item_name") or "",
                "unit": source.get("unit") or "PCS",
                "received_qty": int(float(source.get("received_qty") or 0)),
                "return_qty": qty,
                "movement_id": None,
                "invoice_trace": None,
            })
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                if _same_create(existing, calculated, purchase_order_id):
                    return existing
                raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
        existing_count = await db[COLLECTION].count_documents({"purchase_order_id": purchase_order_id})
        doc = {
            "id": str(uuid.uuid4()),
            "purchase_order_id": purchase_order_id,
            "po_number": po_number,
            "supplier": supplier,
            "supplier_name": supplier,
            "purchase_goods_receipt_id": receipt_ids[0] if len(receipt_ids) == 1 else None,
            "purchase_goods_receipt_ids": receipt_ids,
            "return_date": str(payload.get("return_date") or now_iso()[:10]).strip()[:10],
            "status": STATUS_DRAFT,
            "cancelled": False,
            "notes": str(payload.get("notes") or "").strip(),
            "reason": str(payload.get("reason") or "").strip(),
            "lines": calculated,
            **stamp_create(user),
        }
        if idem:
            doc["idempotency_key"] = idem
        number_index = existing_count + 1
        for _attempt in range(5):
            candidate = derive_child_number(po_number, PREFIX_PURCHASE_RETURN, number_index)
            while await db[COLLECTION].find_one({"return_number": candidate}, {"_id": 1}):
                number_index += 1
                candidate = derive_child_number(po_number, PREFIX_PURCHASE_RETURN, number_index)
            doc["return_number"] = candidate
            number_index += 1
            try:
                await db[COLLECTION].insert_one(dict(doc))
                doc.pop("_id", None)
                return doc
            except DuplicateKeyError as exc:
                if doc.get("idempotency_key") and "idempotency_key" in str(exc):
                    existing = await db[COLLECTION].find_one({"idempotency_key": doc["idempotency_key"]}, {"_id": 0})
                    if existing and _same_create(existing, calculated, purchase_order_id):
                        return existing
                    raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda") from exc
                if "return_number" in str(exc):
                    continue
                raise HTTPException(409, "Dokumen duplikat terdeteksi.") from exc
        raise HTTPException(409, "Gagal menyimpan nomor retur pembelian.")


async def get_purchase_return(db, return_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(return_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"return_number": str(return_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Retur pembelian tidak ditemukan.")
    return doc


async def list_purchase_returns(
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
            {"return_number": {"$regex": needle, "$options": "i"}},
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"supplier": {"$regex": needle, "$options": "i"}},
        ]
    query = filters if include_cancelled else with_active_filter(filters)
    return await db[COLLECTION].find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)


async def _confirm_once(db, doc: dict, confirmation_key: str, user: dict) -> dict:
    from services.physical_stock import (
        PhysicalStockAbortError,
        _client,
        apply_purchase_return_lines,
        require_transaction_support,
    )

    actor_id = str((user or {}).get("id") or (user or {}).get("email") or "user")
    await require_transaction_support(_client(db))
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0}, session=session)
            if not current:
                raise HTTPException(404, "Retur pembelian tidak ditemukan.")
            if current.get("status") == STATUS_CONFIRMED and not is_cancelled(current):
                if confirmation_key and current.get("confirmation_key") not in (None, "", confirmation_key):
                    raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
                if confirmation_key and current.get("confirmation_key") == confirmation_key:
                    if _line_fingerprint(current.get("lines") or []) != _line_fingerprint(doc.get("lines") or []):
                        raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
                return current
            if current.get("status") != STATUS_DRAFT or is_cancelled(current):
                raise HTTPException(400, "Hanya retur berstatus draf yang dapat dikonfirmasi.")
            if confirmation_key:
                owner = await db[COLLECTION].find_one(
                    {"confirmation_key": confirmation_key},
                    {"_id": 0, "id": 1, "lines": 1},
                    session=session,
                )
                if owner and owner.get("id") != current["id"]:
                    raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
            prepared = []
            for line in current.get("lines") or []:
                receipt = await db[RECEIPT_COLLECTION].find_one(
                    {"id": line["purchase_goods_receipt_id"]},
                    {"_id": 0},
                    session=session,
                )
                if not receipt or is_cancelled(receipt):
                    raise HTTPException(400, "Penerimaan sumber tidak lagi aktif.")
                source = None
                for raw in receipt.get("lines") or []:
                    if str(raw.get("line_id") or "") == str(line.get("purchase_goods_receipt_line_id") or ""):
                        source = raw
                        break
                if source is None:
                    raise HTTPException(400, "Baris penerimaan sumber tidak dapat diidentifikasi.")
                if str(source.get("master_item_id") or "") != str(line.get("master_item_id") or ""):
                    raise HTTPException(400, "Barang pada baris penerimaan tidak dapat diidentifikasi.")
                warehouse_id = str(source.get("warehouse_id") or receipt.get("warehouse_id") or "")
                branch_id = str(source.get("branch_id") or receipt.get("branch_id") or "")
                if warehouse_id != str(line.get("warehouse_id") or "") or not warehouse_id:
                    raise HTTPException(400, "Gudang penerimaan tidak dapat diidentifikasi.")
                if branch_id != str(line.get("branch_id") or "") or not branch_id:
                    raise HTTPException(400, "Cabang penerimaan tidak dapat diidentifikasi.")
                received_qty = int(float(source.get("received_qty") or 0))
                qty = int(line.get("return_qty") or 0)
                claimed = await _claim_return_qty(
                    db, session, receipt["id"], str(source.get("line_id")), received_qty, qty,
                )
                if not claimed:
                    fresh = await db[COLLECTION].find_one({"id": current["id"]}, {"_id": 0, "status": 1}, session=session)
                    if fresh and fresh.get("status") == STATUS_CONFIRMED:
                        raise _AlreadyConfirmed()
                    raise HTTPException(400, f"Qty retur ({qty}) melebihi qty yang masih dapat diretur.")
                trace = await _active_invoice_trace(
                    db, session, current["purchase_order_id"], line["purchase_order_line_id"],
                )
                prepared.append({**line, "invoice_trace": trace})
            movements = await apply_purchase_return_lines(
                db, session, {**current, "lines": prepared}, actor_id=actor_id,
            )
            if len(movements) != len(prepared):
                raise HTTPException(409, "Retur tidak dapat dikonfirmasi tanpa movement stok fisik.")
            if _fail_after_r2_movements:
                raise PhysicalStockAbortError("forced purchase return r2 abort")
            for line, movement in zip(prepared, movements):
                line["movement_id"] = movement["id"]
            from services.purchase_return_finance import (
                _header_financial_status,
                financialize_unambiguous_lines,
            )
            prepared = await financialize_unambiguous_lines(db, session, current, prepared, user)
            fields = {
                "status": STATUS_CONFIRMED,
                "financial_status": _header_financial_status(prepared),
                "lines": prepared,
                "confirmed_at": now_iso(),
                "confirmed_by": actor_id,
                **stamp_update(user),
            }
            if confirmation_key:
                fields["confirmation_key"] = confirmation_key
            result = await db[COLLECTION].update_one(
                {"id": current["id"], "status": STATUS_DRAFT},
                {"$set": fields},
                session=session,
            )
            if result.matched_count != 1:
                raise _AlreadyConfirmed()
            saved = await db[COLLECTION].find_one({"id": current["id"]}, {"_id": 0}, session=session)
            return saved


async def confirm_purchase_return(db, return_id: str, payload: Optional[Dict[str, Any]], user: dict) -> dict:
    payload = payload or {}
    if str(payload.get("warehouse_id") or "").strip():
        raise HTTPException(400, "Gudang retur mengikuti gudang penerimaan dan tidak dapat dipilih ulang.")
    doc = await get_purchase_return(db, return_id)
    confirmation_key = str(payload.get("idempotency_key") or payload.get("confirmation_key") or "").strip()
    incoming = payload.get("lines") or payload.get("line_items")
    if incoming and _line_fingerprint(incoming) != _line_fingerprint(doc.get("lines") or []):
        raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
    if doc.get("status") == STATUS_CONFIRMED and not is_cancelled(doc):
        if confirmation_key and doc.get("confirmation_key") not in (None, "", confirmation_key):
            raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
        return doc
    last_error = None
    for _attempt in range(5):
        try:
            return await _confirm_once(db, doc, confirmation_key, user)
        except _AlreadyConfirmed:
            current = await get_purchase_return(db, doc["id"])
            if current.get("status") == STATUS_CONFIRMED and not is_cancelled(current):
                return current
            raise HTTPException(409, "Konfirmasi retur gagal karena perubahan bersamaan.")
        except HTTPException:
            raise
        except Exception as exc:
            if _retryable_mongo(exc):
                last_error = exc
                continue
            _r2_http(exc)
    raise HTTPException(409, f"Gagal mengonfirmasi retur pembelian. {last_error}")


async def _reverse_once(db, doc: dict, user: dict, reason: str) -> dict:
    from services.physical_stock import (
        LEDGER_COLLECTION,
        MOVEMENT_PURCHASE_RETURN,
        PhysicalStockAbortError,
        _client,
        reverse_movement_in_session,
    )

    actor_id = str((user or {}).get("id") or (user or {}).get("email") or "user")
    originals = await db[LEDGER_COLLECTION].find(
        {"movement_type": MOVEMENT_PURCHASE_RETURN, "metadata.purchase_return_id": doc["id"]},
        {"_id": 0, "id": 1},
    ).to_list(500)
    if not originals:
        raise HTTPException(409, "Retur tidak memiliki movement stok fisik yang dapat dibatalkan.")
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0}, session=session)
            if not current:
                raise HTTPException(404, "Retur pembelian tidak ditemukan.")
            if current.get("status") == STATUS_REVERSED or (
                is_cancelled(current) and current.get("status") != STATUS_CONFIRMED
            ):
                return current
            if current.get("status") != STATUS_CONFIRMED:
                raise HTTPException(400, "Hanya retur terkonfirmasi yang dapat direversal.")
            reversal_ids = []
            for original in originals:
                movement = await reverse_movement_in_session(
                    db,
                    session,
                    original["id"],
                    actor_id=actor_id,
                    idempotency_key=f"purchase-return-reversal:{doc['id']}:{original['id']}",
                    reason=reason or "Pembatalan retur pembelian",
                )
                reversal_ids.append(movement["id"])
            for line in current.get("lines") or []:
                released = await _release_return_qty(
                    db,
                    session,
                    line["purchase_goods_receipt_id"],
                    line["purchase_goods_receipt_line_id"],
                    int(line.get("return_qty") or 0),
                )
                if not released:
                    raise HTTPException(409, "Pembatalan retur gagal karena kuantitas retur tidak tersimpan utuh.")
            from services.purchase_return_finance import reverse_return_financials
            await reverse_return_financials(db, session, current, user)
            if _fail_after_r2_movements:
                raise PhysicalStockAbortError("forced purchase return reversal abort")
            fields = cancellation_fields(user, reason)
            fields.update({
                "status": STATUS_REVERSED,
                "reversal_movement_ids": reversal_ids,
                **stamp_update(user),
            })
            result = await db[COLLECTION].update_one(
                {"id": doc["id"], "status": STATUS_CONFIRMED},
                {"$set": fields},
                session=session,
            )
            if result.matched_count != 1:
                raise _AlreadyConfirmed()
            return await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0}, session=session)


async def cancel_purchase_return(db, return_id: str, user: dict, reason: str = "") -> dict:
    doc = await get_purchase_return(db, return_id)
    if doc.get("status") == STATUS_REVERSED or (doc.get("status") == STATUS_CANCELLED and is_cancelled(doc)):
        return doc
    if doc.get("status") == STATUS_DRAFT:
        fields = cancellation_fields(user, reason)
        fields["status"] = STATUS_CANCELLED
        result = await db[COLLECTION].update_one(
            {"id": doc["id"], "status": STATUS_DRAFT},
            {"$set": fields},
        )
        if result.matched_count != 1:
            current = await get_purchase_return(db, doc["id"])
            if current.get("status") in (STATUS_CANCELLED, STATUS_REVERSED):
                return current
            raise HTTPException(409, "Pembatalan draf retur gagal karena perubahan bersamaan.")
        return await get_purchase_return(db, doc["id"])
    if doc.get("status") != STATUS_CONFIRMED:
        raise HTTPException(400, "Status retur tidak dapat dibatalkan.")
    last_error = None
    for _attempt in range(5):
        try:
            return await _reverse_once(db, doc, user, reason)
        except _AlreadyConfirmed:
            current = await get_purchase_return(db, doc["id"])
            if current.get("status") == STATUS_REVERSED:
                return current
            raise HTTPException(409, "Pembatalan retur gagal karena perubahan bersamaan.")
        except HTTPException:
            raise
        except Exception as exc:
            if _retryable_mongo(exc):
                last_error = exc
                continue
            _r2_http(exc)
    raise HTTPException(409, f"Gagal membatalkan retur pembelian. {last_error}")
