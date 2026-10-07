"""Warehouse-wide frozen stock opname against R2 physical balances.

Book quantities are snapshotted from physical_stock_balances at cutoff.
Confirmed differences append STOCK_OPNAME_ADJUSTMENT physical movements and
canonical valuation events when a valid HPP already exists. No GL posting.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import Body, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from pymongo.errors import DuplicateKeyError, PyMongoError

from services.inventory_accounting import ITEM_TYPE_SERVICE
from services.purchase_common import next_dated_seq

COLLECTION = "stock_opname_sessions"
PREFIX = "SOP"

STATUS_DRAFT = "DRAFT"
STATUS_COUNTING = "COUNTING"
STATUS_REVIEW = "REVIEW"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_CANCELLED = "CANCELLED"
ACTIVE_STATUSES = (STATUS_DRAFT, STATUS_COUNTING, STATUS_REVIEW)
FROZEN_STATUSES = (STATUS_COUNTING, STATUS_REVIEW)

_fail_after_adjustments = False


class StockOpnameJsonBody(BaseModel):
    """JSON body for session create, counts, confirm, and cancel.

    No required fields. Callers keep sending the existing payload keys.
    """

    model_config = ConfigDict(extra="allow")


class _AlreadyConfirmed(Exception):
    pass


def set_fail_after_adjustments(enabled: bool) -> None:
    global _fail_after_adjustments
    _fail_after_adjustments = bool(enabled)


def _actor(user: Optional[dict]) -> str:
    user = user or {}
    return str(user.get("id") or user.get("email") or user.get("user_id") or "user")


def _now():
    from services.physical_stock import now_iso
    return now_iso()


def _number(value: Any) -> float:
    from services.physical_stock import _qty_num
    return _qty_num(value)


def present_opname_line(line: dict) -> dict:
    """Display fields from the cutoff snapshot. Counts stay blank until entered."""
    row = dict(line or {})
    pesanan = _number(row.get("qty_pesanan"))
    belum = _number(row.get("qty_belum_terkirim"))
    row["qty_pesanan"] = pesanan
    row["qty_belum_terkirim"] = belum
    count = row.get("physical_count_qty")
    if count is None or count == "":
        row["physical_count_qty"] = None
        row["difference_qty"] = None
        row["qty_tersedia_setelah"] = None
        return row
    counted = int(count)
    book = int(row.get("book_qty") or 0)
    row["physical_count_qty"] = counted
    row["difference_qty"] = counted - book
    row["qty_tersedia_setelah"] = counted - pesanan - belum
    return row


def _public(doc: Optional[dict]) -> dict:
    if not doc:
        raise HTTPException(404, "Sesi stock opname tidak ditemukan.")
    doc = dict(doc)
    doc.pop("_id", None)
    lines = [present_opname_line(line) for line in doc.get("lines") or []]
    doc["lines"] = lines
    doc["item_count"] = len(lines)
    doc["difference_count"] = sum(
        1 for line in lines if line.get("difference_qty") not in (None, 0)
    )
    return doc


async def _snapshot_order_buckets(db, mongo_session, lines: List[dict]) -> None:
    """Freeze Qty Pesanan and Qty Belum Terkirim with the same rules as Stock Saat Ini."""
    from services.physical_stock import (
        LEDGER_COLLECTION,
        MASTER_ITEMS_COLLECTION,
        MOVEMENT_SALES_DELIVERY,
        _qty_num,
        aggregate_operational_orders,
        operational_availability,
    )

    masters = await db[MASTER_ITEMS_COLLECTION].find(
        {},
        {"_id": 0, "id": 1, "accurate_item_id": 1},
        session=mongo_session,
    ).to_list(20000)
    counts: Dict[str, int] = {}
    owner: Dict[str, str] = {}
    for row in masters:
        accurate_id = str(row.get("accurate_item_id") or "").strip()
        master_id = str(row.get("id") or "").strip()
        if not accurate_id or not master_id:
            continue
        counts[accurate_id] = counts.get(accurate_id, 0) + 1
        owner[accurate_id] = master_id
    owners = {accurate_id: master_id for accurate_id, master_id in owner.items() if counts[accurate_id] == 1}
    orders = await db.orders.find({}, {"_id": 0}, session=mongo_session).to_list(20000)
    progress = aggregate_operational_orders(orders, owners)
    ids = [str(line.get("master_item_id") or "") for line in lines if line.get("master_item_id")]
    movements = []
    if ids:
        movements = await db[LEDGER_COLLECTION].find(
            {"movement_type": MOVEMENT_SALES_DELIVERY, "master_item_id": {"$in": ids}},
            {"_id": 0, "master_item_id": 1, "qty_delta": 1},
            session=mongo_session,
        ).to_list(20000)
    delivered: Dict[str, float] = {}
    for row in movements:
        delta = _qty_num(row.get("qty_delta"))
        if delta >= 0:
            continue
        mid = str(row.get("master_item_id") or "")
        delivered[mid] = _qty_num(delivered.get(mid, 0)) + abs(delta)
    for line in lines:
        mid = str(line.get("master_item_id") or "")
        demand = progress.get(mid) or {"ordered_qty": 0.0, "completed_qty": 0.0}
        buckets = operational_availability(
            physical_qty=line.get("book_qty") or 0,
            ordered_qty=demand["ordered_qty"],
            completed_qty=demand["completed_qty"],
            r2_delivered_qty=delivered.get(mid, 0),
        )
        line["qty_pesanan"] = buckets["qty_pesanan"]
        line["qty_belum_terkirim"] = buckets["qty_belum_terkirim"]


def parse_count_qty(value: Any) -> int:
    """Non-negative whole quantity. Blank is not zero. No new rounding rule."""
    if value is None:
        raise HTTPException(400, "Hitungan kosong bukan nol. Isi 0 secara eksplisit.")
    if isinstance(value, bool):
        raise HTTPException(400, "Kuantitas hitung harus bilangan bulat >= 0.")
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            raise HTTPException(400, "Hitungan kosong bukan nol. Isi 0 secara eksplisit.")
        if text.startswith("-"):
            raise HTTPException(400, "Kuantitas hitung tidak boleh negatif.")
        if not text.isdigit():
            raise HTTPException(400, "Kuantitas hitung harus bilangan bulat >= 0.")
        return int(text)
    if isinstance(value, int):
        qty = value
    elif isinstance(value, float) and value.is_integer() and abs(value) != float("inf"):
        qty = int(value)
    else:
        raise HTTPException(400, "Kuantitas hitung harus bilangan bulat >= 0.")
    if qty < 0:
        raise HTTPException(400, "Kuantitas hitung tidak boleh negatif.")
    return qty


def _counted(line: dict) -> bool:
    return line.get("physical_count_qty") is not None and bool(line.get("counted_at"))


def _require_complete(lines: List[dict]) -> None:
    missing = [line for line in lines if not _counted(line)]
    if missing:
        raise HTTPException(400, "Semua baris harus dihitung sebelum review. Kosong bukan nol.")


async def ensure_indexes(db) -> None:
    coll = db[COLLECTION]
    await coll.create_index("id", unique=True)
    await coll.create_index("opname_number", unique=True)
    await coll.create_index("created_at")
    await coll.create_index(
        "active_warehouse_key",
        unique=True,
        name="stock_opname_one_active_warehouse",
        partialFilterExpression={"active_warehouse_key": {"$type": "string"}},
    )
    await coll.create_index(
        "idempotency_key",
        unique=True,
        name="stock_opname_idempotency_key",
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    await coll.create_index(
        "confirmation_key",
        unique=True,
        name="stock_opname_confirmation_key",
        partialFilterExpression={"confirmation_key": {"$type": "string"}},
    )


async def _warehouse_branch(db, *, branch_id: str, warehouse_id: str) -> tuple:
    from services.physical_stock import BRANCHES_COLLECTION, WAREHOUSES_COLLECTION

    branch_id = str(branch_id or "").strip()
    warehouse_id = str(warehouse_id or "").strip()
    if not warehouse_id:
        raise HTTPException(400, "Gudang wajib dipilih.")
    if not branch_id:
        raise HTTPException(400, "Cabang wajib dipilih.")
    warehouse = await db[WAREHOUSES_COLLECTION].find_one({"id": warehouse_id}, {"_id": 0})
    if not warehouse:
        raise HTTPException(400, "Gudang tidak ditemukan.")
    if warehouse.get("active") is not True:
        raise HTTPException(400, "Gudang tidak aktif.")
    if str(warehouse.get("branch_id") or "") != branch_id:
        raise HTTPException(400, "Gudang tidak berada pada cabang ini.")
    branch = await db[BRANCHES_COLLECTION].find_one({"id": branch_id}, {"_id": 0})
    if not branch:
        raise HTTPException(400, "Cabang tidak ditemukan.")
    if branch.get("active") is not True:
        raise HTTPException(400, "Cabang tidak aktif.")
    return branch, warehouse


def _line_snapshot(master: dict, book_qty: int, base_uom: str) -> dict:
    return {
        "line_id": str(uuid.uuid4()),
        "master_item_id": master.get("id"),
        "item_code": master.get("item_code") or "",
        "item_name": master.get("item_name") or "",
        "base_uom": base_uom or "PCS",
        "book_qty": int(book_qty),
        "physical_count_qty": None,
        "difference_qty": None,
        "counted_at": None,
        "counted_by": None,
        "note": "",
    }


async def _book_lines(db, session, *, warehouse_id: str) -> List[dict]:
    from services.physical_stock import (
        BALANCE_COLLECTION,
        MASTER_ITEMS_COLLECTION,
        UNIT_SETTINGS_COLLECTION,
    )

    balances = await db[BALANCE_COLLECTION].find(
        {"warehouse_id": warehouse_id},
        {"_id": 0, "master_item_id": 1, "physical_qty": 1},
        session=session,
    ).to_list(20000)
    book = {}
    for row in balances:
        mid = str(row.get("master_item_id") or "").strip()
        if mid:
            book[mid] = int(row.get("physical_qty") or 0)
    masters = await db[MASTER_ITEMS_COLLECTION].find(
        {},
        {"_id": 0, "id": 1, "item_code": 1, "item_name": 1, "unit": 1, "item_type": 1, "is_active": 1},
        session=session,
    ).to_list(20000)
    by_id = {str(row.get("id")): row for row in masters if row.get("id")}
    selected = set(book)
    for mid, master in by_id.items():
        if master.get("is_active") is False:
            continue
        if str(master.get("item_type") or "").strip().upper() == ITEM_TYPE_SERVICE:
            continue
        selected.add(mid)
    settings = []
    if selected:
        settings = await db[UNIT_SETTINGS_COLLECTION].find(
            {"master_item_id": {"$in": list(selected)}},
            {"_id": 0, "master_item_id": 1, "base_uom": 1},
            session=session,
        ).to_list(len(selected))
    units = {
        row.get("master_item_id"): str(row.get("base_uom") or "").strip().upper()
        for row in settings
        if row.get("base_uom")
    }
    lines = []
    for mid in selected:
        master = by_id.get(mid) or {"id": mid}
        base = units.get(mid) or str(master.get("unit") or "PCS").strip().upper() or "PCS"
        lines.append(_line_snapshot(master, book.get(mid, 0), base))
    lines.sort(key=lambda row: (
        str(row.get("item_code") or ""),
        str(row.get("item_name") or ""),
        str(row.get("master_item_id") or ""),
    ))
    await _snapshot_order_buckets(db, session, lines)
    return lines


async def create_stock_opname_session(db, payload: Optional[dict], user: dict) -> dict:
    payload = payload or {}
    branch, warehouse = await _warehouse_branch(
        db,
        branch_id=payload.get("branch_id"),
        warehouse_id=payload.get("warehouse_id"),
    )
    key = str(payload.get("idempotency_key") or "").strip()
    if key:
        existing = await db[COLLECTION].find_one({"idempotency_key": key}, {"_id": 0})
        if existing:
            if existing.get("warehouse_id") != warehouse["id"] or existing.get("branch_id") != branch["id"]:
                raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
            return existing
    active = await db[COLLECTION].find_one(
        {"warehouse_id": warehouse["id"], "status": {"$in": list(ACTIVE_STATUSES)}},
        {"_id": 0, "id": 1, "opname_number": 1},
    )
    if active:
        raise HTTPException(409, "Sudah ada sesi stock opname aktif untuk gudang ini.")
    stamp = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "branch_code": branch.get("code") or "",
        "branch_name": branch.get("name") or "",
        "warehouse_code": warehouse.get("code") or "",
        "warehouse_name": warehouse.get("name") or "",
        "status": STATUS_DRAFT,
        "cutoff_at": None,
        "created_at": stamp,
        "created_by": _actor(user),
        "reviewed_at": None,
        "reviewed_by": None,
        "confirmed_at": None,
        "confirmed_by": None,
        "cancelled_at": None,
        "cancelled_by": None,
        "notes": str(payload.get("notes") or "").strip(),
        "lines": [],
        "active_warehouse_key": warehouse["id"],
    }
    if key:
        doc["idempotency_key"] = key

    last_error = None
    for _attempt in range(25):
        doc["opname_number"] = await next_dated_seq(db, COLLECTION, "opname_number", PREFIX)
        try:
            await db[COLLECTION].insert_one(dict(doc))
            doc.pop("_id", None)
            return _public(doc)
        except DuplicateKeyError as exc:
            last_error = exc
            message = str(exc)
            if "active_warehouse_key" in message:
                raise HTTPException(409, "Sudah ada sesi stock opname aktif untuk gudang ini.") from exc
            if key and "idempotency_key" in message:
                existing = await db[COLLECTION].find_one({"idempotency_key": key}, {"_id": 0})
                if existing:
                    if existing.get("warehouse_id") != warehouse["id"] or existing.get("branch_id") != branch["id"]:
                        raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
                    return existing
                raise HTTPException(409, "Dokumen duplikat terdeteksi.") from exc
            if "opname_number" in message:
                continue
            raise HTTPException(409, "Dokumen duplikat terdeteksi.") from exc
    raise HTTPException(409, f"Gagal menyimpan nomor stock opname. {last_error}")


async def get_stock_opname_session(db, session_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(session_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"opname_number": str(session_id)}, {"_id": 0})
    return _public(doc)


async def list_stock_opname_sessions(db, warehouse_id: Optional[str] = None) -> List[dict]:
    query: Dict[str, Any] = {}
    if warehouse_id:
        query["warehouse_id"] = str(warehouse_id)
    docs = await db[COLLECTION].find(query, {"_id": 0}).sort("created_at", -1).to_list(200)
    return [_public(doc) for doc in docs]


async def start_stock_opname_cutoff(db, session_id: str, user: dict) -> dict:
    from services.physical_stock import _client, require_transaction_support

    await require_transaction_support(_client(db))
    doc = await get_stock_opname_session(db, session_id)
    if doc["status"] in (STATUS_COUNTING, STATUS_REVIEW, STATUS_CONFIRMED):
        if doc["status"] == STATUS_CONFIRMED:
            raise HTTPException(400, "Sesi stock opname sudah dikonfirmasi.")
        return doc
    if doc["status"] == STATUS_CANCELLED:
        raise HTTPException(400, "Sesi stock opname sudah dibatalkan.")
    if doc["status"] != STATUS_DRAFT:
        raise HTTPException(400, "Cutoff hanya dapat dimulai dari draf.")
    async with await _client(db).start_session() as mongo_session:
        async with mongo_session.start_transaction():
            current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0}, session=mongo_session)
            if not current:
                raise HTTPException(404, "Sesi stock opname tidak ditemukan.")
            if current.get("status") in (STATUS_COUNTING, STATUS_REVIEW):
                return _public(current)
            if current.get("status") != STATUS_DRAFT:
                raise HTTPException(400, "Cutoff hanya dapat dimulai dari draf.")
            lines = await _book_lines(db, mongo_session, warehouse_id=current["warehouse_id"])
            cutoff_at = _now()
            result = await db[COLLECTION].update_one(
                {"id": current["id"], "status": STATUS_DRAFT},
                {"$set": {
                    "status": STATUS_COUNTING,
                    "cutoff_at": cutoff_at,
                    "lines": lines,
                    "updated_at": cutoff_at,
                }},
                session=mongo_session,
            )
            if result.matched_count != 1:
                fresh = await db[COLLECTION].find_one({"id": current["id"]}, {"_id": 0}, session=mongo_session)
                if fresh and fresh.get("status") in (STATUS_COUNTING, STATUS_REVIEW):
                    return _public(fresh)
                raise HTTPException(409, "Cutoff stock opname gagal karena perubahan bersamaan.")
            saved = await db[COLLECTION].find_one({"id": current["id"]}, {"_id": 0}, session=mongo_session)
            return _public(saved)


async def record_stock_opname_counts(db, session_id: str, payload: Optional[dict], user: dict) -> dict:
    payload = payload or {}
    incoming = payload.get("lines") or payload.get("counts") or []
    if isinstance(payload.get("line_id"), str) or "physical_count_qty" in payload:
        incoming = [payload]
    if not isinstance(incoming, list) or not incoming:
        raise HTTPException(400, "Minimal satu baris hitungan.")
    doc = await get_stock_opname_session(db, session_id)
    if doc["status"] != STATUS_COUNTING:
        raise HTTPException(400, "Hitungan hanya dapat diisi selama status COUNTING.")
    by_id = {str(line.get("line_id")): dict(line) for line in doc.get("lines") or []}
    seen = set()
    actor = _actor(user)
    stamp = _now()
    for raw in incoming:
        if not isinstance(raw, dict):
            raise HTTPException(400, "Baris hitungan tidak valid.")
        line_id = str(raw.get("line_id") or "").strip()
        if not line_id or line_id not in by_id:
            raise HTTPException(400, "Baris stock opname tidak ditemukan.")
        if line_id in seen:
            raise HTTPException(400, "Baris hitungan duplikat.")
        seen.add(line_id)
        if "physical_count_qty" not in raw or raw.get("physical_count_qty") is None or raw.get("physical_count_qty") == "":
            raise HTTPException(400, "Hitungan kosong bukan nol. Isi 0 secara eksplisit.")
        qty = parse_count_qty(raw.get("physical_count_qty"))
        line = by_id[line_id]
        book = int(line.get("book_qty") or 0)
        line["physical_count_qty"] = qty
        line["difference_qty"] = qty - book
        line["counted_at"] = stamp
        line["counted_by"] = actor
        if "note" in raw or "reason" in raw:
            line["note"] = str(raw.get("note") if "note" in raw else raw.get("reason") or "").strip()
        line["book_qty"] = book
    lines = list(by_id.values())
    lines.sort(key=lambda row: (
        str(row.get("item_code") or ""),
        str(row.get("item_name") or ""),
        str(row.get("master_item_id") or ""),
    ))
    result = await db[COLLECTION].update_one(
        {"id": doc["id"], "status": STATUS_COUNTING},
        {"$set": {"lines": lines, "updated_at": stamp}},
    )
    if result.matched_count != 1:
        raise HTTPException(409, "Hitungan gagal karena status sesi berubah.")
    return await get_stock_opname_session(db, doc["id"])


async def submit_stock_opname_review(db, session_id: str, user: dict) -> dict:
    doc = await get_stock_opname_session(db, session_id)
    if doc["status"] == STATUS_REVIEW:
        return doc
    if doc["status"] != STATUS_COUNTING:
        raise HTTPException(400, "Review hanya dapat diajukan dari status COUNTING.")
    _require_complete(doc.get("lines") or [])
    stamp = _now()
    result = await db[COLLECTION].update_one(
        {"id": doc["id"], "status": STATUS_COUNTING},
        {"$set": {
            "status": STATUS_REVIEW,
            "reviewed_at": stamp,
            "reviewed_by": _actor(user),
            "updated_at": stamp,
        }},
    )
    if result.matched_count != 1:
        fresh = await get_stock_opname_session(db, doc["id"])
        if fresh["status"] == STATUS_REVIEW:
            return fresh
        raise HTTPException(409, "Review gagal karena perubahan bersamaan.")
    return await get_stock_opname_session(db, doc["id"])


def _count_fingerprint(lines: List[dict]) -> tuple:
    rows = []
    for line in lines or []:
        rows.append((
            str(line.get("line_id") or line.get("master_item_id") or ""),
            None if line.get("physical_count_qty") is None else int(line.get("physical_count_qty")),
        ))
    return tuple(sorted(rows))


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, DuplicateKeyError):
        return True
    if isinstance(exc, PyMongoError) and (
        exc.has_error_label("TransientTransactionError")
        or exc.has_error_label("UnknownTransactionCommitResult")
    ):
        return True
    return getattr(exc, "code", None) in (112, 251)


async def _confirm_once(db, doc: dict, confirmation_key: str, user: dict) -> dict:
    from services.hpp_cutover import (
        _current_canonical_hpp_snapshot,
        prepare_stock_opname_valuation,
    )
    from services.physical_stock import (
        PhysicalStockAbortError,
        _client,
        apply_stock_opname_adjustments,
        require_transaction_support,
    )

    actor = _actor(user)
    await require_transaction_support(_client(db))
    async with await _client(db).start_session() as mongo_session:
        async with mongo_session.start_transaction():
            current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0}, session=mongo_session)
            if not current:
                raise HTTPException(404, "Sesi stock opname tidak ditemukan.")
            if current.get("status") == STATUS_CONFIRMED:
                stored_key = str(current.get("confirmation_key") or "")
                if confirmation_key and stored_key and stored_key != confirmation_key:
                    raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
                return _public(current)
            if current.get("status") != STATUS_REVIEW:
                raise HTTPException(400, "Konfirmasi hanya dapat dilakukan dari status REVIEW.")
            if confirmation_key:
                owner = await db[COLLECTION].find_one(
                    {"confirmation_key": confirmation_key},
                    {"_id": 0, "id": 1},
                    session=mongo_session,
                )
                if owner and owner.get("id") != current["id"]:
                    raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
            lines = [dict(line) for line in current.get("lines") or []]
            _require_complete(lines)
            for line in lines:
                qty = int(line["physical_count_qty"])
                book = int(line.get("book_qty") or 0)
                line["difference_qty"] = qty - book
            # Capture canonical HPP before physical adjustment mutates R2 balances.
            hpp_by_item: Dict[str, Any] = {}
            for line in lines:
                if int(line.get("difference_qty") or 0) == 0:
                    continue
                mid = str(line.get("master_item_id") or "")
                if mid and mid not in hpp_by_item:
                    hpp_by_item[mid] = await _current_canonical_hpp_snapshot(
                        db, mid, session=mongo_session,
                    )
            occurred_at = _now()
            movements = await apply_stock_opname_adjustments(
                db,
                mongo_session,
                current,
                lines,
                actor_id=actor,
                occurred_at=occurred_at,
            )
            expected = [line for line in lines if int(line.get("difference_qty") or 0) != 0]
            if len(movements) != len(expected):
                raise HTTPException(409, "Konfirmasi stock opname tidak lengkap.")
            if _fail_after_adjustments:
                raise PhysicalStockAbortError("forced stock opname confirm abort")
            by_line = {}
            for movement in movements:
                meta = movement.get("metadata") if isinstance(movement.get("metadata"), dict) else {}
                by_line[str(meta.get("stock_opname_line_id") or "")] = movement.get("id")
            for line in lines:
                line["movement_id"] = by_line.get(str(line.get("line_id") or ""))
            lines = await prepare_stock_opname_valuation(
                db,
                mongo_session,
                current,
                lines,
                hpp_by_item=hpp_by_item,
            )
            fields = {
                "status": STATUS_CONFIRMED,
                "lines": lines,
                "confirmed_at": occurred_at,
                "confirmed_by": actor,
                "updated_at": occurred_at,
            }
            if confirmation_key:
                fields["confirmation_key"] = confirmation_key
            result = await db[COLLECTION].update_one(
                {"id": current["id"], "status": STATUS_REVIEW},
                {"$set": fields, "$unset": {"active_warehouse_key": ""}},
                session=mongo_session,
            )
            if result.matched_count != 1:
                raise _AlreadyConfirmed()
            saved = await db[COLLECTION].find_one({"id": current["id"]}, {"_id": 0}, session=mongo_session)
            return _public(saved)


async def confirm_stock_opname_session(db, session_id: str, payload: Optional[dict], user: dict) -> dict:
    from services.hpp_cutover import ensure_indexes, finalize_stock_opname_valuation
    from services.r2_go_live import require_r2_authority_active

    payload = payload or {}
    await require_r2_authority_active(db)
    await ensure_indexes(db)
    doc = await get_stock_opname_session(db, session_id)
    confirmation_key = str(payload.get("idempotency_key") or payload.get("confirmation_key") or "").strip()
    incoming = payload.get("lines")
    if incoming:
        if _count_fingerprint(incoming) != _count_fingerprint(doc.get("lines") or []):
            raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
    if doc["status"] == STATUS_CONFIRMED:
        stored_key = str(doc.get("confirmation_key") or "")
        if confirmation_key and stored_key and stored_key != confirmation_key:
            raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
        await finalize_stock_opname_valuation(db, doc, user)
        return await get_stock_opname_session(db, doc["id"])
    if doc["status"] != STATUS_REVIEW:
        raise HTTPException(400, "Konfirmasi hanya dapat dilakukan dari status REVIEW.")
    last_error = None
    saved = None
    for _attempt in range(5):
        try:
            saved = await _confirm_once(db, doc, confirmation_key, user)
            break
        except _AlreadyConfirmed:
            current = await get_stock_opname_session(db, doc["id"])
            if current["status"] == STATUS_CONFIRMED:
                if confirmation_key and current.get("confirmation_key") not in (None, "", confirmation_key):
                    raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
                await finalize_stock_opname_valuation(db, current, user)
                return await get_stock_opname_session(db, current["id"])
            raise HTTPException(409, "Konfirmasi stock opname gagal karena perubahan bersamaan.")
        except HTTPException:
            raise
        except Exception as exc:
            from services.physical_stock import (
                InsufficientPhysicalStockError,
                PhysicalStockAbortError,
                PhysicalStockConflictError,
                PhysicalStockValidationError,
                StockOpnameInProgressError,
            )
            if isinstance(exc, PhysicalStockAbortError):
                raise
            if isinstance(exc, (StockOpnameInProgressError, PhysicalStockConflictError, InsufficientPhysicalStockError)):
                raise HTTPException(409, str(exc)) from exc
            if isinstance(exc, PhysicalStockValidationError):
                raise HTTPException(400, str(exc)) from exc
            if _retryable(exc):
                last_error = exc
                continue
            raise
    if saved is None:
        raise HTTPException(409, f"Gagal mengonfirmasi stock opname. {last_error}")
    await finalize_stock_opname_valuation(db, saved, user)
    return await get_stock_opname_session(db, saved["id"])


async def cancel_stock_opname_session(db, session_id: str, payload: Optional[dict], user: dict) -> dict:
    payload = payload or {}
    doc = await get_stock_opname_session(db, session_id)
    if doc["status"] == STATUS_CONFIRMED:
        raise HTTPException(409, "Stock opname terkonfirmasi tidak dapat dibatalkan atau dihapus.")
    if doc["status"] == STATUS_CANCELLED:
        return doc
    if doc["status"] not in ACTIVE_STATUSES:
        raise HTTPException(400, "Sesi stock opname tidak dapat dibatalkan.")
    stamp = _now()
    result = await db[COLLECTION].update_one(
        {"id": doc["id"], "status": {"$in": list(ACTIVE_STATUSES)}},
        {
            "$set": {
                "status": STATUS_CANCELLED,
                "cancelled_at": stamp,
                "cancelled_by": _actor(user),
                "cancel_reason": str(payload.get("reason") or payload.get("notes") or "").strip(),
                "updated_at": stamp,
            },
            "$unset": {"active_warehouse_key": ""},
        },
    )
    if result.matched_count != 1:
        fresh = await get_stock_opname_session(db, doc["id"])
        if fresh["status"] == STATUS_CANCELLED:
            return fresh
        if fresh["status"] == STATUS_CONFIRMED:
            raise HTTPException(409, "Stock opname terkonfirmasi tidak dapat dibatalkan atau dihapus.")
        raise HTTPException(409, "Pembatalan stock opname gagal karena perubahan bersamaan.")
    return await get_stock_opname_session(db, doc["id"])


def register_stock_opname_routes(api_router, *, get_db, current_user, require_perm, Depends):
    @api_router.get("/stock-opname-sessions")
    async def stock_opname_sessions_list(
        warehouse_id: Optional[str] = None,
        user=Depends(require_perm("master_items.view")),
    ):
        return await list_stock_opname_sessions(get_db(), warehouse_id)

    @api_router.get("/stock-opname-sessions/{session_id}")
    async def stock_opname_sessions_get(
        session_id: str,
        user=Depends(require_perm("master_items.view")),
    ):
        return await get_stock_opname_session(get_db(), session_id)

    @api_router.post("/stock-opname-sessions")
    async def stock_opname_sessions_create(
        payload: StockOpnameJsonBody = Body(...),
        user=Depends(current_user),
    ):
        return await create_stock_opname_session(get_db(), payload.model_dump(), user)

    @api_router.post("/stock-opname-sessions/{session_id}/cutoff")
    async def stock_opname_sessions_cutoff(
        session_id: str,
        user=Depends(current_user),
    ):
        return await start_stock_opname_cutoff(get_db(), session_id, user)

    @api_router.post("/stock-opname-sessions/{session_id}/counts")
    async def stock_opname_sessions_counts(
        session_id: str,
        payload: StockOpnameJsonBody = Body(...),
        user=Depends(current_user),
    ):
        return await record_stock_opname_counts(get_db(), session_id, payload.model_dump(), user)

    @api_router.post("/stock-opname-sessions/{session_id}/review")
    async def stock_opname_sessions_review(
        session_id: str,
        user=Depends(current_user),
    ):
        return await submit_stock_opname_review(get_db(), session_id, user)

    @api_router.post("/stock-opname-sessions/{session_id}/confirm")
    async def stock_opname_sessions_confirm(
        session_id: str,
        payload: StockOpnameJsonBody = Body(...),
        user=Depends(current_user),
    ):
        return await confirm_stock_opname_session(get_db(), session_id, payload.model_dump(), user)

    @api_router.post("/stock-opname-sessions/{session_id}/cancel")
    async def stock_opname_sessions_cancel(
        session_id: str,
        payload: StockOpnameJsonBody = Body(...),
        user=Depends(current_user),
    ):
        return await cancel_stock_opname_session(get_db(), session_id, payload.model_dump(), user)

    @api_router.get("/stock-opname-sessions/{session_id}/count-template")
    async def stock_opname_sessions_count_template(
        session_id: str,
        user=Depends(require_perm("master_items.view")),
    ):
        from fastapi.responses import Response
        from services.stock_opname_count_import import build_count_template_xlsx

        session = await get_stock_opname_session(get_db(), session_id)
        if session["status"] not in (STATUS_COUNTING, STATUS_REVIEW, STATUS_CONFIRMED):
            raise HTTPException(400, "Template hitung tersedia setelah cutoff.")
        content = build_count_template_xlsx(session)
        filename = f"hitung-fisik-{session.get('opname_number') or session_id}.xlsx"
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @api_router.post("/stock-opname-sessions/{session_id}/count-import")
    async def stock_opname_sessions_count_import(
        session_id: str,
        file: UploadFile = File(...),
        user=Depends(current_user),
    ):
        from services.stock_opname_count_import import CountImportError, import_physical_counts

        data = await file.read()
        if not data:
            raise HTTPException(400, "File kosong")
        try:
            return await import_physical_counts(get_db(), session_id, data, user)
        except CountImportError as exc:
            raise HTTPException(400, exc.message) from exc
