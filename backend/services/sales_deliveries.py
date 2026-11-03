"""Sales Delivery (Pengiriman Pesanan) Service.

Manages backend-authoritative sales delivery notes (Surat Jalan Penjualan)
linked stably to Sales Orders (Pesanan Penjualan / PO).
Supports partial shipments, multi-line integrity, concurrency/duplicate safety,
and PDF document rendering.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services import storage as obj_storage
from services.surat_jalan_files import (
    SOURCE_SYSTEM_GENERATED,
    SOURCE_TYPE_PENGIRIMAN,
    find_generated_sj_file,
    generated_file_is_real,
    generated_sj_file_id,
)
from services.physical_stock_engine import (
    handle_sales_delivery_created,
    precheck_sales_delivery,
    should_claim_sales_delivery_stock,
    claim_sales_delivery_stock,
    release_sales_delivery_stock,
    finalize_sales_delivery_stock,
)
from services.delivery_schedule import persistable_delivery_location
from services.sales_activity.util import normalize_whatsapp
from services.sales_delivery_sales_identity import (
    load_proven_sales_master_labels,
    po_sales_snapshot,
    resolve_sj_sales_from_orders,
    validate_orders_sales_identity_compatible,
)
from services.sales_delivery_allocations import (
    ALLOCATION_COMMIT_PENDING_FIELD,
    activate_delivery_allocation_commit,
    active_deliveries_query_for_po,
    allocation_fingerprint,
    assess_multi_source_slice_fulfillment,
    claim_source_line_allocation,
    commit_claimed_allocations,
    delivery_is_allocation_pending,
    delivery_is_operationally_active,
    delivery_source_po_ids,
    is_multi_source_delivery,
    mongo_transactions_available,
    normalize_delivery_source_allocations,
    order_price_snapshot,
    reconcile_pending_orphan_delivery,
    release_claimed_allocations,
    release_source_line_allocation,
    require_operational_sales_delivery,
    require_reserved_allocations,
    shipped_qty_by_source_line,
    slice_lines_for_po,
    validate_orders_merge_compatible,
)

logger = logging.getLogger(__name__)

_delivery_locks: Dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}
_MULTI_PO_ATOMICITY_UNAVAILABLE = (
    "Penggabungan beberapa PO ke satu Surat Jalan memerlukan transaksi database atomik. "
    "Lingkungan saat ini tidak mendukung transaksi MongoDB — pembuatan SJ multi-PO ditolak (fail-closed)."
)


def _get_delivery_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _delivery_locks:
        _delivery_locks[loop] = asyncio.Lock()
    return _delivery_locks[loop]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_base_sj_number(po_number: str) -> str:
    """Derives base SJ number from PO number while preserving main sequence."""
    pn = (po_number or "").strip()
    if not pn:
        return "SJ/" + datetime.now(timezone.utc).strftime("%d/%m/%y/001")
    
    # 1. Match PO/04/09/26/001 or SO/04/09/26/001 -> SJ/04/09/26/001
    # 2. Match PO/2026/08/30/000123 or SO/2026/08/30/000123 -> SJ/2026/08/30/000123
    # 3. Match PO-20260830-001 or SO-20260830-001 -> SJ-20260830-001
    # 4. Match PO.2026.08.001 -> SJ.2026.08.001
    # 5. Match PO 12345 -> SJ 12345
    # 6. Match PO12345 -> SJ12345
    m = re.match(r'^(?:PO|SO)([\/\.\-\s_]?)(.*)$', pn, re.IGNORECASE)
    if m:
        sep = m.group(1)
        rest = m.group(2)
        return f"SJ{sep}{rest}"
    
    # Check other alphabetic prefixes with separator
    m2 = re.match(r'^[A-Za-z]+([\/\.\-\s_])(.*)$', pn)
    if m2:
        sep = m2.group(1)
        rest = m2.group(2)
        return f"SJ{sep}{rest}"
    
    # Plain number or other pattern
    return f"SJ-{pn}"


def derive_sj_number(po_number: str, shipment_index: int) -> str:
    """Computes backend-authoritative SJ number for a specific shipment index.
    
    Index 1: base SJ (e.g. SJ/04/09/26/001 or SJ-20260830-001)
    Index 2: base SJ + "_2"
    Index 3: base SJ + "_3"
    """
    base = derive_base_sj_number(po_number)
    if shipment_index <= 1:
        return base
    return f"{base}_{shipment_index}"


# Dated SJ forms already produced by derive_base_sj_number.
_SJ_YMD_RE = re.compile(r"^(SJ/)(\d{4})/(\d{2})/(\d{2})/(\d+)(_\d+)?$", re.I)
_SJ_DMY_RE = re.compile(r"^(SJ/)(\d{2})/(\d{2})/(\d{2})/(\d+)(_\d+)?$", re.I)


def parse_sj_number_parts(sj_number: str) -> Optional[Dict[str, Any]]:
    text = (sj_number or "").strip()
    m = _SJ_YMD_RE.match(text)
    style = "ymd" if m else "dmy"
    if not m:
        m = _SJ_DMY_RE.match(text)
    if not m:
        return None
    a, b, c, seq = int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5)
    y, mo, d = (a, b, c) if style == "ymd" else (2000 + c, b, a)
    try:
        datetime(y, mo, d)
    except ValueError:
        return None
    return {
        "style": style, "date_key": f"{y:04d}-{mo:02d}-{d:02d}",
        "seq": int(seq), "width": len(seq), "suffix": text[m.end(5):],
    }


def sj_number_date_key(sj_number: str) -> Optional[str]:
    parts = parse_sj_number_parts(sj_number)
    return parts["date_key"] if parts else None


def _format_sj_for_date(parts: Dict[str, Any], date_key: str, sequence: Optional[int] = None) -> str:
    dt = datetime.strptime(date_key, "%Y-%m-%d")
    seq = int(parts["seq"] if sequence is None else sequence)
    width = max(int(parts["width"]), len(str(seq)))
    stamp = dt.strftime("%d/%m/%y") if parts["style"] == "dmy" else dt.strftime("%Y/%m/%d")
    return f"SJ/{stamp}/{seq:0{width}d}{parts.get('suffix') or ''}"


async def allocate_sj_number_for_date(
    db, current_sj: str, date_key: str, *, exclude_id: Optional[str] = None,
) -> str:
    """Unique SJ for a new date: unique index + max+1 prefix scan."""
    current = str(current_sj or "").strip()
    parts = parse_sj_number_parts(current)
    if not parts or parts["date_key"] == date_key:
        return current
    dt = datetime.strptime(date_key, "%Y-%m-%d")
    prefix = f"SJ/{dt.strftime('%d/%m/%y')}/" if parts["style"] == "dmy" else f"SJ/{date_key.replace('-', '/')}/"
    exclude = str(exclude_id or "").strip()

    async def taken(num: str) -> bool:
        q: Dict[str, Any] = {"sj_number": num, "is_cancelled": {"$ne": True}}
        if exclude:
            q["id"] = {"$ne": exclude}
        return bool(await db.sales_deliveries.find_one(q, {"_id": 1}))

    candidate = _format_sj_for_date(parts, date_key)
    if not await taken(candidate):
        return candidate
    max_seq = 0
    async for doc in db.sales_deliveries.find(
        {"sj_number": {"$regex": f"^{re.escape(prefix)}\\d+"}, "is_cancelled": {"$ne": True}},
        {"sj_number": 1, "id": 1, "_id": 0},
    ):
        if exclude and str(doc.get("id") or "") == exclude:
            continue
        m = re.match(r"^(\d+)", str(doc.get("sj_number") or "")[len(prefix):])
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    nxt = max_seq + 1
    for _ in range(16):
        candidate = _format_sj_for_date(parts, date_key, nxt)
        if not await taken(candidate):
            return candidate
        nxt += 1
    raise HTTPException(409, "Gagal mengalokasikan nomor Surat Jalan unik untuk tanggal kirim baru.")


def _stamp_sj_on_order(order: dict, sid: str, old_sj: str, new_sj: str, date_key: str) -> None:
    for sh in order.get("shipments") or []:
        if isinstance(sh, dict) and str(sh.get("id") or sh.get("sales_delivery_id") or "") == sid:
            sh["number"] = new_sj
            if sh.get("sj_number"):
                sh["sj_number"] = new_sj
            sh["delivery_date"] = date_key
    for f in order.get("surat_jalan_files") or []:
        if not isinstance(f, dict):
            continue
        if str(f.get("shipment_id") or f.get("id") or "") != sid and str(f.get("sj_number") or "") != old_sj:
            continue
        f["sj_number"] = new_sj
        if old_sj and old_sj in str(f.get("name") or ""):
            f["name"] = str(f.get("name")).replace(old_sj, new_sj)
    if str(order.get("delivery_note_number") or "") == old_sj:
        order["delivery_note_number"] = new_sj


async def sync_sj_numbers_after_reschedule(
    db, order: dict, *, previous_date: Optional[str], new_date: Optional[str],
    shipment_id: Optional[str] = None, user: Optional[dict] = None,
) -> List[Dict[str, str]]:
    """Reschedule-only: claim a unique SJ when the scheduled date actually changes."""
    from services.delivery_schedule import is_delivered, parse_date_key
    from quantity_rules import shipment_is_active

    date_key = parse_date_key(new_date)
    if not date_key or parse_date_key(previous_date) == date_key:
        return []
    if is_delivered(order) or str(order.get("delivery_status") or "") == "Terkirim":
        return []

    want = str(shipment_id or "").strip()
    changes: List[Dict[str, str]] = []
    async with _get_delivery_lock():
        for sh in order.get("shipments") or []:
            if not isinstance(sh, dict):
                continue
            sid = str(sh.get("id") or sh.get("sales_delivery_id") or "").strip()
            if not sid or (want and sid != want) or not shipment_is_active(sh):
                continue
            if str(sh.get("delivery_status") or "") == "Terkirim" or sh.get("delivered_at"):
                continue
            delivery = await db.sales_deliveries.find_one(
                {"id": sid, "is_cancelled": {"$ne": True}}, {"_id": 0},
            )
            old_sj = str((delivery or {}).get("sj_number") or sh.get("number") or sh.get("sj_number") or "").strip()
            if not parse_sj_number_parts(old_sj) or sj_number_date_key(old_sj) == date_key:
                sh["delivery_date"] = date_key
                continue
            claimed = None
            for _ in range(8):
                new_sj = await allocate_sj_number_for_date(db, old_sj, date_key, exclude_id=sid)
                if not delivery:
                    claimed = new_sj
                    break
                try:
                    await db.sales_deliveries.update_one(
                        {"id": sid},
                        {"$set": {"sj_number": new_sj, "delivery_date": date_key, "updated_at": now_iso()}},
                    )
                    claimed = new_sj
                    break
                except DuplicateKeyError:
                    continue
            if not claimed:
                raise HTTPException(409, "Nomor Surat Jalan bentrok. Penjadwalan ulang tidak disimpan.")
            _stamp_sj_on_order(order, sid, old_sj, claimed, date_key)
            changes.append({"delivery_id": sid, "old_sj": old_sj, "new_sj": claimed})

    for change in changes:
        delivery = await db.sales_deliveries.find_one({"id": change["delivery_id"]}, {"_id": 0})
        if delivery:
            await ensure_generated_sj_pdf(db, order, delivery, user or {}, force=True)
    return changes


def delivery_lines_fingerprint(lines: List[dict]) -> Tuple[Tuple[int, int, str], ...]:
    """Exact-line identity fingerprint. Same-name products stay separate."""
    rows = []
    for ln in lines or []:
        if not isinstance(ln, dict):
            continue
        try:
            qty = int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity") or 0)
        except (ValueError, TypeError):
            qty = 0
        if qty <= 0:
            continue
        try:
            idx = int(ln.get("line_idx", 0))
        except (ValueError, TypeError):
            idx = 0
        rows.append((idx, qty, str(ln.get("line_id") or "")))
    return tuple(sorted(rows))


def build_generated_sj_file_entry(delivery: dict, user: dict, storage_path: str, created_at: str) -> dict:
    file_id = generated_sj_file_id(delivery.get("id") or delivery.get("delivery_id"))
    sj_number = str(delivery.get("sj_number") or "").strip()
    lines = []
    for ln in delivery.get("line_items") or []:
        if not isinstance(ln, dict):
            continue
        try:
            qty = int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity") or 0)
        except (ValueError, TypeError):
            qty = 0
        if qty <= 0:
            continue
        try:
            idx = int(ln.get("line_idx", 0))
        except (ValueError, TypeError):
            idx = 0
        lines.append({
            "line_idx": idx,
            "line_id": ln.get("line_id"),
            "qty": qty,
            "name": ln.get("name"),
            "unit": ln.get("unit") or "pcs",
            "item_code": ln.get("item_code"),
            "color": ln.get("color"),
            "production_process": ln.get("production_process"),
        })
    return {
        "id": file_id,
        "name": f"Surat Jalan {sj_number}.pdf" if sj_number else "Surat Jalan.pdf",
        "content_type": "application/pdf",
        "uploaded_by": (user or {}).get("email") or "system",
        "uploaded_by_name": (user or {}).get("name") or (user or {}).get("email") or "Sistem",
        "uploaded_by_role": (user or {}).get("role") or "",
        "uploaded_at": created_at,
        "shipment_id": file_id,
        "sj_number": sj_number or None,
        "lines": lines,
        "source": SOURCE_SYSTEM_GENERATED,
        "source_type": SOURCE_TYPE_PENGIRIMAN,
        "storage_path": storage_path,
    }


async def ensure_generated_sj_pdf(db, order: dict, delivery: dict, user: dict, *, force: bool = False) -> Tuple[Optional[dict], Optional[str]]:
    """Generate the real SJ PDF, store it, and register the authoritative file.

    Idempotent: same shipment always maps to the same file id / object key.
    Never writes fake surat_jalan_files metadata when storage/PDF fails.
    force=True overwrites the stored PDF after an SJ number / delivery_date change.
    Provisional (allocation_commit_pending) SJ cannot be printed as official docs.
    """
    shipment_id = str(delivery.get("id") or delivery.get("delivery_id") or "").strip()
    po_id = str(delivery.get("po_id") or (order or {}).get("id") or "").strip()
    if not shipment_id or not po_id:
        return None, "shipment_identity_missing"

    # Canonical gate: refuse pending even if a cached file already exists.
    try:
        await require_operational_sales_delivery(db, shipment_id, attempt_reconcile=True)
    except HTTPException as exc:
        if exc.status_code in (404, 409):
            return None, "allocation_pending_or_unavailable"
        raise
    if delivery_is_allocation_pending(delivery):
        return None, "allocation_pending_or_unavailable"

    fresh = await db.orders.find_one({"id": po_id}, {"_id": 0}) or order or {}
    existing = find_generated_sj_file(fresh, shipment_id)
    if not force and generated_file_is_real(existing):
        rec = await db.files.find_one({"id": existing.get("id"), "is_deleted": {"$ne": True}}, {"_id": 0})
        if rec and rec.get("storage_path"):
            return existing, None

    try:
        from services.print_design_settings import get_print_design_for_pdf
        design = await get_print_design_for_pdf(db, branch_id=(delivery or {}).get("branch_id"))
        pdf_bytes = generate_sales_delivery_pdf_bytes(delivery, None, design=design)
    except Exception:
        logger.exception("Failed to generate Surat Jalan PDF for shipment %s", shipment_id)
        return None, "pdf_generate_failed"

    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        logger.error("Generated Surat Jalan bytes are not a real PDF for shipment %s", shipment_id)
        return None, "pdf_invalid"

    file_id = generated_sj_file_id(shipment_id)
    storage_path = f"{obj_storage.APP_NAME}/surat-jalan/{po_id}/{file_id}.pdf"
    try:
        stored = obj_storage.put_object(storage_path, pdf_bytes, "application/pdf")
        storage_path = stored.get("path") or storage_path
    except Exception:
        logger.exception("Surat Jalan object storage write failed for shipment %s", shipment_id)
        return None, "storage_failed"

    stamp = now_iso()
    entry = build_generated_sj_file_entry(delivery, user or {}, storage_path, stamp)
    file_doc = {
        "id": file_id,
        "storage_path": storage_path,
        "original_filename": entry["name"],
        "content_type": "application/pdf",
        "size": (stored.get("size") if isinstance(stored, dict) else None) or len(pdf_bytes),
        "kind": "surat_jalan",
        "order_id": po_id,
        "shipment_id": shipment_id,
        "sj_number": entry.get("sj_number"),
        "source": SOURCE_SYSTEM_GENERATED,
        "source_type": SOURCE_TYPE_PENGIRIMAN,
        "uploaded_by": entry.get("uploaded_by"),
        "uploaded_at": stamp,
        "is_deleted": False,
    }
    try:
        await db.files.insert_one(file_doc)
    except DuplicateKeyError:
        await db.files.update_one(
            {"id": file_id},
            {"$set": {
                "storage_path": storage_path,
                "original_filename": entry["name"],
                "content_type": "application/pdf",
                "size": file_doc["size"],
                "kind": "surat_jalan",
                "order_id": po_id,
                "shipment_id": shipment_id,
                "sj_number": entry.get("sj_number"),
                "source": SOURCE_SYSTEM_GENERATED,
                "source_type": SOURCE_TYPE_PENGIRIMAN,
                "is_deleted": False,
            }},
        )

    await db.orders.update_one(
        {"id": po_id, "surat_jalan_files.id": {"$ne": file_id}},
        {"$push": {"surat_jalan_files": entry}},
    )
    await db.orders.update_one(
        {"id": po_id, "surat_jalan_files.id": file_id},
        {"$set": {
            "surat_jalan_files.$.storage_path": storage_path,
            "surat_jalan_files.$.content_type": "application/pdf",
            "surat_jalan_files.$.name": entry["name"],
            "surat_jalan_files.$.source": SOURCE_SYSTEM_GENERATED,
            "surat_jalan_files.$.source_type": SOURCE_TYPE_PENGIRIMAN,
            "surat_jalan_files.$.shipment_id": shipment_id,
            "surat_jalan_files.$.sj_number": entry.get("sj_number"),
            "surat_jalan_files.$.lines": entry.get("lines") or [],
        }},
    )
    await db.orders.update_one(
        {"id": po_id},
        {"$set": {"shipments.$[sh].file_id": file_id}},
        array_filters=[{"sh.id": shipment_id}],
    )
    await db.sales_deliveries.update_one(
        {"id": shipment_id},
        {"$set": {"generated_sj_file_id": file_id, "updated_at": stamp}},
    )
    return entry, None


def _order_has_shipment_id(order: Optional[dict], delivery_id: str) -> bool:
    want = str(delivery_id or "").strip()
    if not want or not isinstance(order, dict):
        return False
    for sh in order.get("shipments") or []:
        if not isinstance(sh, dict):
            continue
        if str(sh.get("id") or "").strip() == want:
            return True
        if str(sh.get("sales_delivery_id") or "").strip() == want:
            return True
    return False


async def ensure_order_shipment_synced(
    db,
    po_id: str,
    shipment_entry: dict,
    *,
    stamp: Optional[dict] = None,
    session=None,
) -> None:
    """Ensure orders.shipments carries this sales_delivery before create reports success.

    Read-after-write verification. Does not delete the sales_deliveries document on
    failure (stock/audit side effects already possible). Raises instead of false success.
    """
    delivery_id = str((shipment_entry or {}).get("id") or "").strip()
    oid = str(po_id or "").strip()
    if not delivery_id or not oid:
        raise HTTPException(500, "Gagal menyinkronkan Surat Jalan ke PO (identitas kosong).")

    kw = {"session": session} if session is not None else {}
    set_fields = dict(stamp or {})
    result = await db.orders.update_one(
        {
            "id": oid,
            "shipments.id": {"$ne": delivery_id},
            "shipments.sales_delivery_id": {"$ne": delivery_id},
        },
        {
            "$push": {"shipments": shipment_entry},
            **({"$set": set_fields} if set_fields else {}),
        },
        **kw,
    )
    if getattr(result, "matched_count", 0) or getattr(result, "modified_count", 0):
        return

    fresh = await db.orders.find_one(
        {"id": oid},
        {"_id": 0, "shipments.id": 1, "shipments.sales_delivery_id": 1},
        **kw,
    )
    if _order_has_shipment_id(fresh, delivery_id):
        # Idempotent: already projected (concurrent push or prior attempt).
        if set_fields:
            await db.orders.update_one({"id": oid}, {"$set": set_fields}, **kw)
        return
    if not fresh:
        raise HTTPException(
            500,
            "Pengiriman Pesanan tersimpan, tetapi PO tidak ditemukan untuk sinkronisasi Surat Jalan. "
            "Jangan anggap sukses — periksa PO lalu coba lagi.",
        )
    # Order exists but filter missed (unusual). Retry push guarded only by id presence check above.
    result2 = await db.orders.update_one(
        {
            "id": oid,
            "shipments.id": {"$ne": delivery_id},
            "shipments.sales_delivery_id": {"$ne": delivery_id},
        },
        {
            "$push": {"shipments": shipment_entry},
            **({"$set": set_fields} if set_fields else {}),
        },
        **kw,
    )
    fresh2 = await db.orders.find_one(
        {"id": oid},
        {"_id": 0, "shipments.id": 1, "shipments.sales_delivery_id": 1},
        **kw,
    )
    if _order_has_shipment_id(fresh2, delivery_id) or getattr(result2, "modified_count", 0):
        return
    raise HTTPException(
        500,
        "Pengiriman Pesanan tersimpan, tetapi gagal menyinkronkan ke daftar Surat Jalan PO. "
        "Jangan anggap sukses — muat ulang / coba lagi agar Jadwal Pengiriman konsisten.",
    )


async def _reuse_existing_delivery_if_retry(db, order: dict, validated_lines: List[dict], user: dict) -> Optional[dict]:
    """If a matching shipment exists without a real generated PDF, complete that document.

    Does not collapse later legitimate partials that happen to use the same qty.
    """
    want = delivery_lines_fingerprint(validated_lines)
    if not want:
        return None
    po_id = str(order.get("id") or "")
    existing = await db.sales_deliveries.find(
        {"po_id": po_id, "is_cancelled": {"$ne": True}},
        {"_id": 0},
    ).sort("shipment_index", 1).to_list(1000)
    if not existing:
        return None

    fresh_order = await db.orders.find_one({"id": po_id}, {"_id": 0}) or order
    for d in existing:
        if delivery_lines_fingerprint(d.get("line_items")) != want:
            continue
        sid = str(d.get("id") or "").strip()
        gen = find_generated_sj_file(fresh_order, d.get("id"))
        pdf_real = generated_file_is_real(gen)
        missing_ship = bool(sid) and not _order_has_shipment_id(fresh_order, sid)
        # Fully complete same-fingerprint document → not a retry target.
        if pdf_real and not missing_ship:
            continue
        # Incomplete prior attempt (sync and/or PDF): repair under existing reuse contract.
        if missing_ship:
            repair_entry = {
                "id": sid,
                "number": d.get("sj_number"),
                "shipment_index": d.get("shipment_index"),
                "delivery_date": d.get("delivery_date"),
                "customer": d.get("customer") or d.get("customer_name"),
                "outlet": d.get("outlet") or d.get("outlet_name"),
                "sales": d.get("sales") or d.get("sales_name"),
                "recipient": d.get("recipient") or d.get("recipient_name"),
                "address": d.get("address") or d.get("shipping_address"),
                "delivery_zone": d.get("delivery_zone") or d.get("shipping_zone"),
                "expedition": d.get("expedition") or d.get("expedition_or_vehicle"),
                "notes": d.get("notes") or d.get("delivery_note"),
                "lines": [
                    {
                        "line_idx": ln.get("line_idx"),
                        "line_id": ln.get("line_id"),
                        "qty": ln.get("qty") if ln.get("qty") is not None else ln.get("quantity"),
                        "name": ln.get("name"),
                        "unit": ln.get("unit") or "pcs",
                        "color": ln.get("color"),
                        "production_process": ln.get("production_process"),
                    }
                    for ln in (d.get("line_items") or [])
                    if isinstance(ln, dict)
                ],
                "total_qty": d.get("total_qty"),
                "created_at": d.get("created_at"),
                "created_by": d.get("created_by") or "",
                "sales_delivery_id": sid,
            }
            if d.get("branch_id"):
                repair_entry["branch_id"] = d.get("branch_id")
            if d.get("delivery_location"):
                repair_entry["delivery_location"] = d.get("delivery_location")
            await ensure_order_shipment_synced(
                db,
                po_id,
                repair_entry,
                stamp={
                    "delivery_note_number": d.get("sj_number"),
                    "delivery_note_created_at": d.get("created_at") or now_iso(),
                    "delivery_note_created_by": d.get("created_by") or "",
                    "delivery_note_created_by_name": d.get("created_by_name") or "",
                },
            )
            fresh_order = await db.orders.find_one({"id": po_id}, {"_id": 0}) or fresh_order
        if not pdf_real:
            entry, _err = await ensure_generated_sj_pdf(db, fresh_order, d, user)
            if entry:
                d["generated_sj_file_id"] = entry["id"]
        return d
    return None


_DETAIL_EXCLUDED_SHIPMENT_STATUSES = ("superseded", "cancelled", "deleted")


def summarize_po_delivery_quantities(
    order: dict,
    shipments: Optional[list],
    *,
    po_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Quantity block shared by Detail PO and the sales-order list gate.

    This is the existing get_po_delivery_summary math, including its shipment
    filter, line_idx fallback, and raw multi-unit sum. It does not decide a
    new business rule.
    """
    existing_deliveries: List[dict] = []
    for doc in shipments or []:
        if not isinstance(doc, dict):
            continue
        if doc.get("is_cancelled") is True:
            continue
        if doc.get(ALLOCATION_COMMIT_PENDING_FIELD) is True:
            continue
        status = str(doc.get("status") or "").strip().lower()
        if status in _DETAIL_EXCLUDED_SHIPMENT_STATUSES:
            continue
        if doc.get("cancelled"):
            continue
        existing_deliveries.append(doc)

    line_po_id = po_id if po_id is not None else order.get("id")
    shipped_by_line_idx = shipped_qty_by_source_line(existing_deliveries, str(line_po_id or ""))

    raw_lines = order.get("line_items") or []
    lines_summary = []
    total_ordered = 0
    total_shipped = 0

    for idx, li in enumerate(raw_lines):
        ordered_q = int(li.get("quantity") or 0)
        shipped_q = shipped_by_line_idx.get(idx, 0)
        remain_q = max(0, ordered_q - shipped_q)

        total_ordered += ordered_q
        total_shipped += shipped_q

        stable_line_id = li.get("line_id") or f"{line_po_id}_line_{idx}"
        lines_summary.append({
            "line_idx": idx,
            "line_id": stable_line_id,
            "item_code": li.get("item_code"),
            "name": li.get("name") or f"Item #{idx+1}",
            "unit": li.get("unit") or "pcs",
            "color": li.get("color"),
            "production_process": li.get("production_process") or "print",
            "ordered_qty": ordered_q,
            "shipped_qty": shipped_q,
            "remaining_qty": remain_q,
            "notes": li.get("notes") or "",
        })

    total_remaining = max(0, total_ordered - total_shipped)
    is_cancelled = bool(order.get("cancelled"))

    all_lines_fulfilled = len(raw_lines) > 0 and all(
        shipped_by_line_idx.get(idx, 0) >= int(li.get("quantity") or 0)
        for idx, li in enumerate(raw_lines)
    )

    if is_cancelled:
        delivery_status = "DIBATALKAN"
    elif all_lines_fulfilled or (total_ordered > 0 and total_shipped >= total_ordered):
        delivery_status = "SELESAI"
    else:
        delivery_status = "SEBAGIAN"

    return {
        "lines": lines_summary,
        "deliveries": existing_deliveries,
        "total_ordered": total_ordered,
        "total_shipped": total_shipped,
        "total_remaining": total_remaining,
        "delivery_status": delivery_status,
        "can_create_delivery": (not is_cancelled and total_remaining > 0),
        "shipment_count": len(existing_deliveries),
    }


async def get_po_delivery_summary(db, po_id_or_doc: Any) -> Dict[str, Any]:
    """Calculate delivered & remaining quantities per line item for a PO.
    
    Priority for header fields:
    1. Value snapshot from exact PO
    2. Fallback to master customer/outlet if PO field is empty
    3. Empty string if not found anywhere (allows manual entry)
    """
    if isinstance(po_id_or_doc, dict):
        order = po_id_or_doc
    else:
        order = await db.orders.find_one({"id": str(po_id_or_doc)}, {"_id": 0})
        if not order:
            # Also try po_number lookup if uuid lookup fails
            order = await db.orders.find_one({"po_number": str(po_id_or_doc)}, {"_id": 0})
        if not order:
            raise HTTPException(404, f"Pesanan Penjualan (PO) '{po_id_or_doc}' tidak ditemukan.")

    po_id = order.get("id")
    po_number = order.get("po_number")

    # 1. Base snapshot values from PO
    customer_val = (order.get("customer") or order.get("customer_name") or "").strip()
    outlet_val = (order.get("outlet") or order.get("outlet_name") or "").strip()
    recipient_val = (order.get("recipient") or order.get("pic") or order.get("recipient_name") or "").strip()
    address_val = (order.get("address") or order.get("shipping_address") or order.get("delivery_address") or "").strip()
    zone_val = (order.get("delivery_zone") or order.get("zona_kirim") or order.get("zona") or order.get("shipping_zone") or "").strip()
    sales_val = (order.get("sales") or order.get("seller") or order.get("sales_rep") or order.get("sales_name") or "").strip()
    expedition_val = (order.get("expedition") or order.get("ekspedisi") or order.get("expedition_or_vehicle") or "Internal fleet").strip()
    notes_val = (order.get("notes") or order.get("delivery_note") or "").strip()

    # 2. Fallback to master delivery_outlets or recent orders if fields are empty
    if customer_val and (not address_val or not outlet_val or not recipient_val or not zone_val):
        master_outlet = None
        if outlet_val:
            master_outlet = await db.delivery_outlets.find_one({
                "customer_name": {"$regex": f"^{re.escape(customer_val)}$", "$options": "i"},
                "name": {"$regex": f"^{re.escape(outlet_val)}$", "$options": "i"}
            }, {"_id": 0})
        if not master_outlet:
            master_outlet = await db.delivery_outlets.find_one({
                "customer_name": {"$regex": f"^{re.escape(customer_val)}$", "$options": "i"}
            }, {"_id": 0})

        if master_outlet:
            if not address_val:
                address_val = (master_outlet.get("formatted_address") or master_outlet.get("address") or "").strip()
            if not outlet_val:
                outlet_val = (master_outlet.get("name") or "").strip()
            if not recipient_val:
                recipient_val = (master_outlet.get("recipient") or "").strip()
            if not zone_val:
                zone_val = (master_outlet.get("delivery_zone") or "").strip()

        # Secondary fallback: address/recipient/zone from recent same-customer PO.
        # Do NOT borrow Sales — SJ Sales must follow this PO's own snapshot only.
        if not address_val or not recipient_val or not zone_val:
            try:
                recent_cust_po = await db.orders.find_one(
                    {
                        "customer": {"$regex": f"^{re.escape(customer_val)}$", "$options": "i"},
                        "cancelled": {"$ne": True},
                        "id": {"$ne": po_id}
                    },
                    {"_id": 0},
                    sort=[("created_at", -1)]
                )
            except Exception:
                recent_cust_po = None
            
            if recent_cust_po:
                if not address_val:
                    address_val = (recent_cust_po.get("address") or "").strip()
                if not recipient_val:
                    recipient_val = (recent_cust_po.get("recipient") or "").strip()
                if not zone_val:
                    zone_val = (recent_cust_po.get("delivery_zone") or "").strip()

    # Active SJ that allocate from this PO (legacy po_id and multi-source membership).
    # summarize_po_delivery_quantities filters cancelled/status and attributes by source.
    deliveries_cursor = db.sales_deliveries.find(
        active_deliveries_query_for_po(str(po_id)),
        {"_id": 0},
    ).sort("shipment_index", 1)
    fetched_deliveries = await deliveries_cursor.to_list(1000)
    qty = summarize_po_delivery_quantities(order, fetched_deliveries, po_id=po_id)
    existing_deliveries = qty["deliveries"]
    lines_summary = qty["lines"]
    total_ordered = qty["total_ordered"]
    total_shipped = qty["total_shipped"]
    total_remaining = qty["total_remaining"]
    delivery_status = qty["delivery_status"]

    next_shipment_index, suggested_sj = await allocate_sales_delivery_identity(db, po_id, po_number)

    from services.customer_identity import extract_stable_customer_id
    return {
        "po_id": po_id,
        "po_number": po_number,
        "order_date": order.get("order_date"),
        "customer": customer_val,
        "customer_name": customer_val,
        "customer_id": extract_stable_customer_id(order),
        "customer_phone": (order.get("customer_phone") or "").strip(),
        "outlet": outlet_val,
        "outlet_name": outlet_val,
        "recipient": recipient_val,
        "recipient_name": recipient_val,
        "address": address_val,
        "shipping_address": address_val,
        "delivery_zone": zone_val,
        "shipping_zone": zone_val,
        "sales": sales_val,
        "sales_name": sales_val,
        "expedition": expedition_val,
        "expedition_or_vehicle": expedition_val,
        "notes": notes_val,
        "delivery_note": notes_val,
        "created_by_user": order.get("created_by_user") or order.get("imported_by_name") or "",
        "lines": lines_summary,
        "total_ordered": total_ordered,
        "total_shipped": total_shipped,
        "total_remaining": total_remaining,
        "delivery_status": delivery_status,
        "next_shipment_index": next_shipment_index,
        "suggested_sj_number": suggested_sj,
        "can_create_delivery": qty["can_create_delivery"],
        "deliveries": existing_deliveries,
        "delivery_location": persistable_delivery_location(order.get("delivery_location")),
    }


_SJ_ALLOCATION_ATTEMPTS = 8
_CONCURRENT_CONFLICT = "Terjadi konflik transaksi bersamaan. Silakan muat ulang dan coba lagi."


async def allocate_sales_delivery_identity(db, po_id: str, po_number: str) -> Tuple[int, str]:
    """Next free (shipment_index, sj_number) under the unique indexes.

    Active-row count is not that cursor. A deleted earlier Surat Jalan leaves
    the higher shipment_index in place, and sj_number is unique across POs.
    Repeating count+1 inserts the same key and the retry reports a false 409.
    """
    max_index = 0
    existing = await db.sales_deliveries.find(
        {"po_id": po_id},
        {"shipment_index": 1, "_id": 0},
    ).to_list(1000)
    for doc in existing:
        try:
            idx = int((doc or {}).get("shipment_index") or 0)
        except (TypeError, ValueError):
            idx = 0
        if idx > max_index:
            max_index = idx

    shipment_index = max_index + 1
    for _ in range(_SJ_ALLOCATION_ATTEMPTS):
        sj = derive_sj_number(po_number, shipment_index)
        index_taken = await db.sales_deliveries.find_one(
            {"po_id": po_id, "shipment_index": shipment_index},
            {"_id": 1},
        )
        sj_taken = await db.sales_deliveries.find_one({"sj_number": sj}, {"_id": 1})
        if not index_taken and not sj_taken:
            return shipment_index, sj
        shipment_index += 1
    raise HTTPException(
        409,
        "Nomor Surat Jalan tidak dapat dialokasikan karena bentrok dengan dokumen yang sudah ada.",
    )


def _assert_fresh_delivery_qty(summary: Dict[str, Any], validated_lines: List[dict]) -> None:
    """Reject a retry that would overship after another delivery committed."""
    if not summary.get("can_create_delivery"):
        raise HTTPException(
            400,
            "Kuantitas PO ini sudah dipenuhi oleh pengiriman lain yang selesai terlebih dahulu.",
        )
    by_idx = {l["line_idx"]: l for l in summary.get("lines") or []}
    for item in validated_lines:
        target = by_idx.get(item["line_idx"]) or {}
        remain = int(target.get("remaining_qty") or 0)
        unit = target.get("unit") or "pcs"
        if int(item["qty"]) > remain:
            name = target.get("name") or "item"
            raise HTTPException(
                400,
                f"Kuantitas pengiriman untuk '{name}' ({item['qty']} {unit}) "
                f"melebihi sisa PO yang tersedia ({remain} {unit}).",
            )


_RECIPIENT_PHONE_SOURCES = frozenset({"customer", "manual"})


def resolve_delivery_recipient_phone(order: dict, payload: dict) -> Tuple[Optional[str], Optional[str]]:
    """Snapshot phone for one Surat Jalan.

    Customer mode reads orders.customer_phone and ignores the client number.
    Manual mode stores the admin input. Empty is allowed. A non-empty manual
    number that cannot be normalized is rejected. An existing customer number
    that cannot be normalized is stored as text with no WhatsApp digits.
    """
    source = str((payload or {}).get("recipient_phone_source") or "manual").strip().lower()
    if source not in _RECIPIENT_PHONE_SOURCES:
        raise HTTPException(400, "Sumber nomor WhatsApp penerima tidak valid")
    if source == "customer":
        raw = str((order or {}).get("customer_phone") or "").strip()
        if not raw:
            return None, None
        return raw, normalize_whatsapp(raw)
    raw_in = (payload or {}).get("recipient_phone")
    if raw_in is None:
        return None, None
    text = str(raw_in).strip()
    if not text:
        return None, None
    if len(text) > 40:
        raise HTTPException(400, "Nomor WhatsApp penerima tidak valid")
    wa = normalize_whatsapp(text)
    if not wa:
        raise HTTPException(400, "Nomor WhatsApp penerima tidak valid")
    return text, wa


def _payload_source_allocation_rows(payload: Dict[str, Any]) -> List[dict]:
    """Normalize create payload into allocation rows (may be empty)."""
    rows: List[dict] = []
    for raw in payload.get("source_allocations") or []:
        if not isinstance(raw, dict):
            continue
        rows.append(dict(raw))
    # line_items may already carry source_po_id for multi-source clients
    if not rows:
        for raw in payload.get("line_items") or []:
            if not isinstance(raw, dict):
                continue
            if not str(raw.get("source_po_id") or "").strip():
                continue
            rows.append({
                "source_po_id": raw.get("source_po_id"),
                "source_line_id": raw.get("source_line_id") or raw.get("line_id"),
                "source_line_idx": raw.get("source_line_idx", raw.get("line_idx")),
                "allocated_qty": raw.get("allocated_qty", raw.get("qty", raw.get("quantity"))),
                "notes": raw.get("notes"),
            })
    return rows


def _enrich_single_po_lines(order: dict, validated_lines: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Stamp source_* on single-PO lines and build source_allocations for Stage 2."""
    po_id = str(order.get("id") or "")
    po_number = str(order.get("po_number") or "")
    raw_lines = order.get("line_items") or []
    enriched = []
    allocations = []
    for item in validated_lines:
        idx = int(item.get("line_idx", 0))
        order_line = raw_lines[idx] if 0 <= idx < len(raw_lines) and isinstance(raw_lines[idx], dict) else {}
        price = order_price_snapshot(order_line)
        currency = str(order.get("currency") or price.get("currency") or "").strip()
        row = {
            **item,
            "source_po_id": po_id,
            "source_po_number": po_number,
            "source_line_id": str(item.get("line_id") or ""),
            "source_line_idx": idx,
            "master_item_id": order_line.get("master_item_id") or item.get("master_item_id"),
            "unit_price": price.get("unit_price"),
            "discount_amount": price.get("discount_amount"),
            "ppn_percent": price.get("ppn_percent"),
            "currency": currency or price.get("currency") or "",
        }
        enriched.append(row)
        allocations.append({
            "source_po_id": po_id,
            "source_po_number": po_number,
            "source_line_id": row["source_line_id"],
            "source_line_idx": idx,
            "master_item_id": row.get("master_item_id") or "",
            "item_code": row.get("item_code"),
            "name": row.get("name"),
            "unit": row.get("unit") or "pcs",
            "allocated_qty": int(row.get("qty") or 0),
            "unit_price": row.get("unit_price"),
            "discount_amount": row.get("discount_amount"),
            "ppn_percent": row.get("ppn_percent"),
            "currency": row.get("currency") or "",
        })
    return enriched, allocations


async def create_sales_delivery(db, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Create a new Sales Delivery (Pengiriman Pesanan) backend-authoritatively.

    Single-PO path remains the default API. Multi-PO uses source_allocations and
    requires Mongo transactions (fail-closed when unavailable).
    """
    alloc_rows = _payload_source_allocation_rows(payload or {})
    distinct_sources = {
        str(r.get("source_po_id") or "").strip()
        for r in alloc_rows
        if str(r.get("source_po_id") or "").strip()
    }
    if len(distinct_sources) > 1:
        return await _create_multi_source_sales_delivery(db, payload, user, alloc_rows)

    po_id = str(payload.get("po_id") or "").strip()
    if not po_id and len(distinct_sources) == 1:
        po_id = next(iter(distinct_sources))
    if not po_id:
        raise HTTPException(400, "ID Pesanan Penjualan (po_id) wajib diisi.")

    from services.operational_branch import require_document_branch
    branch_doc = await db.orders.find_one({"id": po_id}, {"_id": 0})
    if not branch_doc:
        branch_doc = await db.orders.find_one({"po_number": po_id}, {"_id": 0})
    if branch_doc:
        await require_document_branch(db, user, branch_doc)
    async with _get_delivery_lock():
        # 1. Fetch current PO state
        order = await db.orders.find_one({"id": po_id}, {"_id": 0})
        if not order:
            order = await db.orders.find_one({"po_number": po_id}, {"_id": 0})
        if not order:
            raise HTTPException(404, f"Pesanan Penjualan dengan ID '{po_id}' tidak ditemukan.")

        if order.get("cancelled"):
            raise HTTPException(400, "Pesanan Penjualan ini sudah dibatalkan.")

        recipient_phone, recipient_phone_wa = resolve_delivery_recipient_phone(order, payload)

        # 2. Get latest delivery summary and remaining quantities
        summary = await get_po_delivery_summary(db, order)
        lines_summary_by_idx = {l["line_idx"]: l for l in summary["lines"]}

        # 3. Validate line items payload (legacy line_items OR single-source allocations)
        input_lines = payload.get("line_items") or []
        if not input_lines and alloc_rows:
            input_lines = [
                {
                    "line_idx": r.get("source_line_idx", r.get("line_idx", 0)),
                    "line_id": r.get("source_line_id") or r.get("line_id"),
                    "qty": r.get("allocated_qty", r.get("qty", r.get("quantity"))),
                    "notes": r.get("notes"),
                }
                for r in alloc_rows
            ]
        if not input_lines:
            raise HTTPException(400, "Minimal 1 line item pengiriman harus memiliki kuantitas > 0.")

        validated_lines = []
        total_delivery_qty = 0

        for item in input_lines:
            try:
                line_idx = int(item.get("line_idx", 0))
            except (ValueError, TypeError):
                line_idx = 0

            target_line = lines_summary_by_idx.get(line_idx)
            if not target_line:
                raise HTTPException(400, f"Line item #{line_idx + 1} tidak ditemukan pada Pesanan Penjualan.")

            # Reject client attempt to spoof or alter exact line identity
            client_line_id = str(item.get("line_id") or "").strip()
            if client_line_id and client_line_id != str(target_line["line_id"]):
                raise HTTPException(400, f"Identitas line item #{line_idx + 1} tidak cocok dengan PO.")

            try:
                qty_to_ship = int(item.get("qty") if item.get("qty") is not None else item.get("quantity", 0))
            except (ValueError, TypeError):
                qty_to_ship = 0

            if qty_to_ship <= 0:
                continue

            total_delivery_qty += qty_to_ship
            validated_lines.append({
                "line_idx": line_idx,
                "line_id": target_line["line_id"],
                "item_code": target_line["item_code"],
                "name": target_line["name"],
                "unit": target_line["unit"],
                "color": target_line["color"],
                "production_process": target_line["production_process"],
                "ordered_qty": target_line["ordered_qty"],
                "qty": qty_to_ship,
                "notes": (item.get("notes") or "").strip(),
            })

        if not validated_lines or total_delivery_qty <= 0:
            raise HTTPException(400, "Total kuantitas pengiriman sekarang harus lebih besar dari 0.")

        reused = await _reuse_existing_delivery_if_retry(db, order, validated_lines, user)
        if reused:
            return reused

        if not summary["can_create_delivery"]:
            raise HTTPException(400, "Seluruh kuantitas Pesanan Penjualan ini sudah selesai dibuatkan Pengiriman Pesanan.")

        for item in validated_lines:
            line_idx = item["line_idx"]
            target_line = lines_summary_by_idx[line_idx]
            qty_to_ship = item["qty"]
            remain = target_line["remaining_qty"]
            if qty_to_ship > remain:
                raise HTTPException(
                    400,
                    f"Kuantitas pengiriman untuk '{target_line['name']}' ({qty_to_ship} {target_line['unit']}) "
                    f"melebihi sisa PO yang tersedia ({remain} {target_line['unit']})."
                )

            # Packing/Loading are NOT prerequisites for creating/preparing Pengiriman Pesanan.
            # Physical readiness is enforced later (Loading confirm, photo/gallery, confirm-delivered).

        validated_lines, source_allocations = _enrich_single_po_lines(order, validated_lines)

        # 4. Determine shipment index and SJ number backend-authoritatively
        now = now_iso()
        delivery_date = str(payload.get("delivery_date") or now[:10]).strip()
        po_number = order.get("po_number")

        # Resolve edited snapshot values with fallback to PO snapshot.
        # Sales is server-authoritative from the associated PO — never client override.
        validate_orders_sales_identity_compatible([order])
        final_customer = summary["customer"]
        final_outlet = str(payload.get("outlet") if payload.get("outlet") is not None else summary["outlet"]).strip()
        final_recipient = str(payload.get("recipient") if payload.get("recipient") is not None else summary["recipient"]).strip()
        final_address = str(payload.get("address") if payload.get("address") is not None else summary["address"]).strip()
        final_zone = str(payload.get("delivery_zone") if payload.get("delivery_zone") is not None else summary["delivery_zone"]).strip()
        final_sales, _final_sales_master_id = resolve_sj_sales_from_orders([order])
        final_expedition = str(payload.get("expedition") if payload.get("expedition") is not None else summary["expedition"]).strip()
        final_notes = str(payload.get("notes") if payload.get("notes") is not None else "").strip()
        sj_location = persistable_delivery_location(payload.get("delivery_location"))

        # Retry loop in case of race condition / concurrent submission
        max_retries = 3
        for attempt in range(max_retries):
            shipment_index, authoritative_sj = await allocate_sales_delivery_identity(
                db, summary["po_id"], po_number
            )

            delivery_id = str(uuid.uuid4())
            delivery_doc = {
                "id": delivery_id,
                "delivery_id": delivery_id,
                "po_id": summary["po_id"],
                "po_number": po_number,
                "source_po_ids": [summary["po_id"]],
                "is_multi_source": False,
                "source_allocations": source_allocations,
                "sj_number": authoritative_sj,
                "shipment_index": shipment_index,
                "delivery_date": delivery_date,
                "customer": final_customer,
                "customer_name": final_customer,
                "outlet": final_outlet,
                "outlet_name": final_outlet,
                "recipient": final_recipient,
                "recipient_name": final_recipient,
                "address": final_address,
                "shipping_address": final_address,
                "delivery_zone": final_zone,
                "shipping_zone": final_zone,
                "sales": final_sales,
                "sales_name": final_sales,
                "expedition": final_expedition,
                "expedition_or_vehicle": final_expedition,
                "notes": final_notes,
                "delivery_note": final_notes,
                "recipient_phone": recipient_phone,
                "recipient_phone_wa": recipient_phone_wa,
                "created_by": user.get("email") or "",
                "created_by_name": user.get("name") or user.get("email") or "",
                "created_by_role": user.get("role") or "",
                "created_at": now,
                "updated_at": now,
                "line_items": validated_lines,
                "total_qty": total_delivery_qty,
                "item_count": len(validated_lines),
                "status": "SELESAI" if (summary["total_remaining"] - total_delivery_qty <= 0) else "SEBAGIAN",
                "is_cancelled": False,
                # Invisible to remaining/seed until claim commit succeeds (Race A fence).
                ALLOCATION_COMMIT_PENDING_FIELD: True,
                "physical_stock_snapshot": order.get("physical_stock_snapshot"),
            }
            from services.operational_branch import document_branch
            delivery_doc["operational_branch_id"] = document_branch(order)
            order_branch_id = str(order.get("branch_id") or "").strip()
            if order_branch_id.isdigit():
                delivery_doc["branch_id"] = order_branch_id
            if sj_location:
                delivery_doc["delivery_location"] = sj_location

            # Pre-check physical stock availability before persisting delivery
            await precheck_sales_delivery(db, order, validated_lines)

            claimed_stock = None
            if await should_claim_sales_delivery_stock(db, order):
                claimed_stock = await claim_sales_delivery_stock(
                    db, order, validated_lines, delivery_id, user
                )

            # Unified allocation counter (same gate as multi-PO) — cross-process safe.
            line_claims: List[dict] = []
            order_lines = order.get("line_items") or []
            try:
                for ln in validated_lines:
                    idx = int(ln.get("line_idx", 0))
                    order_line = (
                        order_lines[idx]
                        if 0 <= idx < len(order_lines) and isinstance(order_lines[idx], dict)
                        else {}
                    )
                    ordered_qty = int(
                        order_line.get("quantity")
                        if order_line.get("quantity") is not None
                        else order_line.get("qty") or ln.get("ordered_qty") or 0
                    )
                    claim = await claim_source_line_allocation(
                        db,
                        source_po_id=str(summary["po_id"]),
                        source_line_id=str(ln.get("source_line_id") or ln.get("line_id") or ""),
                        source_line_idx=idx,
                        ordered_qty=ordered_qty,
                        qty=int(ln.get("qty") or 0),
                        sales_delivery_id=delivery_id,
                    )
                    line_claims.append(claim)
            except Exception:
                await release_claimed_allocations(db, line_claims)
                if claimed_stock:
                    await release_sales_delivery_stock(db, claimed_stock)
                raise

            try:
                # Fence: refuse persist if lease was stolen / healed while we waited.
                await require_reserved_allocations(db, line_claims)
                await db.sales_deliveries.insert_one(delivery_doc)
            except DuplicateKeyError:
                await release_claimed_allocations(db, line_claims)
                if claimed_stock:
                    await release_sales_delivery_stock(db, claimed_stock)
                # Real race: another insert took this key. Do not reuse a stale
                # count, and do not ship more than the quantity still remaining.
                fresh_summary = await get_po_delivery_summary(db, order)
                _assert_fresh_delivery_qty(fresh_summary, validated_lines)
                summary = fresh_summary
                if attempt == max_retries - 1:
                    raise HTTPException(409, _CONCURRENT_CONFLICT)
                continue
            except HTTPException:
                await release_claimed_allocations(db, line_claims)
                if claimed_stock:
                    await release_sales_delivery_stock(db, claimed_stock)
                raise
            except Exception:
                # Insert did not persist — release claims so qty is not stuck.
                await release_claimed_allocations(db, line_claims)
                if claimed_stock:
                    await release_sales_delivery_stock(db, claimed_stock)
                raise

            # SJ document exists → commit reservations (idempotent; healer also promotes).
            # Fail-closed: any non-committed claim raises → delete provisional SJ.
            try:
                await commit_claimed_allocations(db, line_claims)
                await activate_delivery_allocation_commit(db, delivery_id)
                delivery_doc.pop(ALLOCATION_COMMIT_PENDING_FIELD, None)
            except Exception:
                await release_claimed_allocations(db, line_claims)
                if claimed_stock:
                    await release_sales_delivery_stock(db, claimed_stock)
                await db.sales_deliveries.delete_one({"id": delivery_id})
                raise

            delivery_doc.pop("_id", None)
            try:
                # Auto-register corresponding Surat Jalan into PO records
                shipment_entry = {
                    "id": delivery_id,
                    "number": authoritative_sj,
                    "shipment_index": shipment_index,
                    "delivery_date": delivery_date,
                    "customer": final_customer,
                    "outlet": final_outlet,
                    "sales": final_sales,
                    "recipient": final_recipient,
                    "address": final_address,
                    "delivery_zone": final_zone,
                    "expedition": final_expedition,
                    "notes": final_notes,
                    "recipient_phone": recipient_phone,
                    "recipient_phone_wa": recipient_phone_wa,
                    "lines": [
                        {
                            "line_idx": ln["line_idx"],
                            "line_id": ln["line_id"],
                            "qty": ln["qty"],
                            "name": ln["name"],
                            "unit": ln.get("unit") or "pcs",
                            "color": ln.get("color"),
                            "production_process": ln.get("production_process"),
                        }
                        for ln in validated_lines
                    ],
                    "total_qty": total_delivery_qty,
                    "created_at": now,
                    "created_by": user.get("email") or "",
                    "sales_delivery_id": delivery_id,
                }
                if delivery_doc.get("branch_id"):
                    shipment_entry["branch_id"] = delivery_doc["branch_id"]
                shipment_entry["operational_branch_id"] = delivery_doc["operational_branch_id"]
                if sj_location:
                    shipment_entry["delivery_location"] = sj_location

                # Must prove orders.shipments sync before reporting create success.
                await ensure_order_shipment_synced(
                    db,
                    summary["po_id"],
                    shipment_entry,
                    stamp={
                        "delivery_note_number": authoritative_sj,
                        "delivery_note_created_at": now,
                        "delivery_note_created_by": user.get("email") or "",
                        "delivery_note_created_by_name": user.get("name") or user.get("email") or "",
                    },
                )

                file_entry, pdf_err = await ensure_generated_sj_pdf(db, order, delivery_doc, user)
                if file_entry:
                    delivery_doc["generated_sj_file_id"] = file_entry["id"]
                elif pdf_err:
                    delivery_doc["generated_sj_file_error"] = pdf_err
                    logger.warning(
                        "Pengiriman %s saved without generated SJ PDF (%s); retry can complete the document",
                        authoritative_sj,
                        pdf_err,
                    )

                if claimed_stock:
                    await finalize_sales_delivery_stock(db, claimed_stock, user)
                else:
                    await handle_sales_delivery_created(db, order, delivery_doc, user)
                try:
                    from services.notifications import emit_surat_jalan_created_notifications
                    await emit_surat_jalan_created_notifications(
                        db,
                        order=order,
                        shipment=shipment_entry,
                    )
                except Exception:
                    logger.exception("surat jalan created notifications failed")
                return delivery_doc
            except Exception:
                # After sales_deliveries insert: keep SJ + allocation claims so
                # orphan-reuse retry can repair shipment/PDF under the same id.
                # Only release ephemeral stock reservations (not line counters).
                if claimed_stock:
                    await release_sales_delivery_stock(db, claimed_stock)
                raise

        raise HTTPException(500, "Gagal membuat Pengiriman Pesanan.")


async def _create_multi_source_sales_delivery(
    db,
    payload: Dict[str, Any],
    user: dict,
    alloc_rows: List[dict],
) -> Dict[str, Any]:
    """Create one SJ from multiple source POs with item-level allocations.

    Fail-closed when Mongo transactions are unavailable. All source shipment
    mirrors succeed together with the sales_deliveries insert, or none persist.
    """
    from services.operational_branch import document_branch, require_document_branch

    # Atomicity: per-line allocation counters (conditional $inc) work on standalone
    # Mongo. Optional multi-doc transaction wraps insert+shipment sync when available.

    # Group requested qty by (source_po_id, source_line_idx/line_id)
    requested: List[dict] = []
    for raw in alloc_rows:
        source_po_id = str(raw.get("source_po_id") or "").strip()
        if not source_po_id:
            raise HTTPException(400, "source_po_id wajib diisi pada setiap alokasi.")
        try:
            qty = int(
                raw.get("allocated_qty")
                if raw.get("allocated_qty") is not None
                else raw.get("qty") if raw.get("qty") is not None else raw.get("quantity", 0)
            )
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        try:
            source_line_idx = int(raw.get("source_line_idx") if raw.get("source_line_idx") is not None else raw.get("line_idx", 0))
        except (TypeError, ValueError):
            source_line_idx = 0
        requested.append({
            "source_po_id": source_po_id,
            "source_line_id": str(raw.get("source_line_id") or raw.get("line_id") or "").strip(),
            "source_line_idx": source_line_idx,
            "qty": qty,
            "notes": str(raw.get("notes") or "").strip(),
        })
    if not requested:
        raise HTTPException(400, "Total kuantitas pengiriman sekarang harus lebih besar dari 0.")

    source_ids = list(dict.fromkeys(r["source_po_id"] for r in requested))
    if len(source_ids) < 2:
        # Caller should have used single-PO path; keep fail-safe without re-entering multi.
        single_payload = dict(payload or {})
        single_payload["po_id"] = source_ids[0] if source_ids else single_payload.get("po_id")
        single_payload.pop("source_allocations", None)
        # Rebuild legacy line_items from requested rows for the single PO.
        single_pid = str(single_payload.get("po_id") or "")
        single_payload["line_items"] = [
            {
                "line_idx": r["source_line_idx"],
                "line_id": r.get("source_line_id"),
                "qty": r["qty"],
                "notes": r.get("notes") or "",
            }
            for r in requested
            if r["source_po_id"] == single_pid
        ]
        return await create_sales_delivery(db, single_payload, user)

    async with _get_delivery_lock():
        orders: Dict[str, dict] = {}
        for pid in source_ids:
            order = await db.orders.find_one({"id": pid}, {"_id": 0})
            if not order:
                order = await db.orders.find_one({"po_number": pid}, {"_id": 0})
            if not order:
                raise HTTPException(404, f"Pesanan Penjualan dengan ID '{pid}' tidak ditemukan.")
            await require_document_branch(db, user, order)
            orders[str(order["id"])] = order
            # Rewrite requested ids to canonical order.id when looked up by po_number
            for row in requested:
                if row["source_po_id"] == pid:
                    row["source_po_id"] = str(order["id"])
        source_ids = list(orders.keys())
        source_orders = [orders[pid] for pid in source_ids]
        validate_orders_merge_compatible(source_orders)
        proven_master_labels = await load_proven_sales_master_labels(db, source_orders)
        validate_orders_sales_identity_compatible(
            source_orders,
            proven_master_labels=proven_master_labels,
        )

        summaries: Dict[str, dict] = {}
        for pid in source_ids:
            summaries[pid] = await get_po_delivery_summary(db, orders[pid])

        # Detect duplicate allocation keys in one request
        seen_keys = set()
        validated_lines: List[dict] = []
        source_allocations: List[dict] = []
        total_delivery_qty = 0
        remaining_by_po: Dict[str, int] = {
            pid: int(summaries[pid].get("total_remaining") or 0) for pid in source_ids
        }

        for row in requested:
            pid = row["source_po_id"]
            order = orders[pid]
            summary = summaries[pid]
            by_idx = {l["line_idx"]: l for l in summary.get("lines") or []}
            target = by_idx.get(row["source_line_idx"])
            if not target:
                raise HTTPException(
                    400,
                    f"Line item #{row['source_line_idx'] + 1} tidak ditemukan pada PO {order.get('po_number')}.",
                )
            client_line_id = row["source_line_id"]
            if client_line_id and client_line_id != str(target["line_id"]):
                raise HTTPException(
                    400,
                    f"Identitas line item tidak cocok dengan PO {order.get('po_number')}.",
                )
            key = (pid, str(target["line_id"]))
            if key in seen_keys:
                raise HTTPException(
                    400,
                    f"Alokasi ganda untuk item '{target['name']}' pada PO {order.get('po_number')} ditolak.",
                )
            seen_keys.add(key)
            remain = int(target.get("remaining_qty") or 0)
            if row["qty"] > remain:
                raise HTTPException(
                    400,
                    f"Kuantitas pengiriman untuk '{target['name']}' dari PO {order.get('po_number')} "
                    f"({row['qty']} {target['unit']}) melebihi sisa ({remain} {target['unit']}).",
                )
            raw_lines = order.get("line_items") or []
            order_line = (
                raw_lines[row["source_line_idx"]]
                if 0 <= row["source_line_idx"] < len(raw_lines)
                and isinstance(raw_lines[row["source_line_idx"]], dict)
                else {}
            )
            price = order_price_snapshot(order_line)
            currency = str(order.get("currency") or price.get("currency") or "").strip()
            line_doc = {
                "line_idx": len(validated_lines),
                "line_id": f"{pid}:{target['line_id']}",
                "source_po_id": pid,
                "source_po_number": order.get("po_number"),
                "source_line_id": target["line_id"],
                "source_line_idx": row["source_line_idx"],
                "master_item_id": order_line.get("master_item_id"),
                "item_code": target.get("item_code"),
                "name": target.get("name"),
                "unit": target.get("unit") or "pcs",
                "color": target.get("color"),
                "production_process": target.get("production_process"),
                "ordered_qty": target.get("ordered_qty"),
                "qty": row["qty"],
                "notes": row["notes"],
                "unit_price": price.get("unit_price"),
                "discount_amount": price.get("discount_amount"),
                "ppn_percent": price.get("ppn_percent"),
                "currency": currency,
            }
            validated_lines.append(line_doc)
            source_allocations.append({
                "source_po_id": pid,
                "source_po_number": order.get("po_number"),
                "source_line_id": target["line_id"],
                "source_line_idx": row["source_line_idx"],
                "master_item_id": order_line.get("master_item_id") or "",
                "item_code": target.get("item_code"),
                "name": target.get("name"),
                "unit": target.get("unit") or "pcs",
                "allocated_qty": row["qty"],
                "unit_price": price.get("unit_price"),
                "discount_amount": price.get("discount_amount"),
                "ppn_percent": price.get("ppn_percent"),
                "currency": currency,
            })
            total_delivery_qty += row["qty"]
            remaining_by_po[pid] = max(0, remaining_by_po[pid] - row["qty"])

        # Primary PO = explicit payload.po_id if part of sources, else first source.
        primary_hint = str(payload.get("po_id") or "").strip()
        primary_id = primary_hint if primary_hint in orders else source_ids[0]
        primary_order = orders[primary_id]
        primary_summary = summaries[primary_id]

        # Idempotent retry: identical fingerprint + incomplete PDF/sync on primary.
        want_fp = allocation_fingerprint(source_allocations)
        existing_multi = await db.sales_deliveries.find(
            {
                "is_cancelled": {"$ne": True},
                "is_multi_source": True,
                "source_po_ids": {"$all": source_ids},
            },
            {"_id": 0},
        ).sort("created_at", -1).to_list(50)
        for d in existing_multi:
            if allocation_fingerprint(normalize_delivery_source_allocations(d)) != want_fp:
                continue
            # Reuse only incomplete prior attempts (same contract as single-PO).
            sid = str(d.get("id") or "").strip()
            fresh_primary = await db.orders.find_one({"id": primary_id}, {"_id": 0}) or primary_order
            gen = find_generated_sj_file(fresh_primary, sid)
            pdf_real = generated_file_is_real(gen)
            missing_any = False
            for pid in source_ids:
                ord_doc = await db.orders.find_one({"id": pid}, {"_id": 0}) or orders[pid]
                if not _order_has_shipment_id(ord_doc, sid):
                    missing_any = True
                    break
            if pdf_real and not missing_any:
                continue
            # Repair mirrors for every source then return.
            for pid in source_ids:
                ord_doc = await db.orders.find_one({"id": pid}, {"_id": 0}) or orders[pid]
                if _order_has_shipment_id(ord_doc, sid):
                    continue
                slice_lines = slice_lines_for_po(d, pid)
                repair_entry = {
                    "id": sid,
                    "number": d.get("sj_number"),
                    "shipment_index": d.get("shipment_index"),
                    "delivery_date": d.get("delivery_date"),
                    "customer": d.get("customer"),
                    "outlet": d.get("outlet"),
                    "sales": d.get("sales"),
                    "recipient": d.get("recipient"),
                    "address": d.get("address"),
                    "delivery_zone": d.get("delivery_zone"),
                    "expedition": d.get("expedition"),
                    "notes": d.get("notes"),
                    "lines": slice_lines,
                    "total_qty": sum(int(x.get("qty") or 0) for x in slice_lines),
                    "created_at": d.get("created_at"),
                    "created_by": d.get("created_by") or "",
                    "sales_delivery_id": sid,
                    "source_po_ids": list(d.get("source_po_ids") or source_ids),
                    "is_multi_source": True,
                }
                await ensure_order_shipment_synced(
                    db,
                    pid,
                    repair_entry,
                    stamp={
                        "delivery_note_number": d.get("sj_number"),
                        "delivery_note_created_at": d.get("created_at") or now_iso(),
                        "delivery_note_created_by": d.get("created_by") or "",
                        "delivery_note_created_by_name": d.get("created_by_name") or "",
                    },
                )
            if not pdf_real:
                await ensure_generated_sj_pdf(db, primary_order, d, user)
            d.pop("_id", None)
            return d

        recipient_phone, recipient_phone_wa = resolve_delivery_recipient_phone(primary_order, payload)
        now = now_iso()
        delivery_date = str(payload.get("delivery_date") or now[:10]).strip()
        po_number = primary_order.get("po_number")
        final_customer = primary_summary["customer"]
        final_outlet = str(payload.get("outlet") if payload.get("outlet") is not None else primary_summary["outlet"]).strip()
        final_recipient = str(payload.get("recipient") if payload.get("recipient") is not None else primary_summary["recipient"]).strip()
        final_address = str(payload.get("address") if payload.get("address") is not None else primary_summary["address"]).strip()
        final_zone = str(payload.get("delivery_zone") if payload.get("delivery_zone") is not None else primary_summary["delivery_zone"]).strip()
        # Sales from validated source POs only — ignore client payload.sales.
        final_sales, _final_sales_master_id = resolve_sj_sales_from_orders(
            source_orders,
            proven_master_labels=proven_master_labels,
        )
        final_expedition = str(
            payload.get("expedition") if payload.get("expedition") is not None else primary_summary["expedition"]
        ).strip()
        final_notes = str(payload.get("notes") if payload.get("notes") is not None else "").strip()
        sj_location = persistable_delivery_location(payload.get("delivery_location"))

        # Precheck stock per source order (legacy PSS path only when enabled).
        for pid in source_ids:
            mapped = [
                {**ln, "line_idx": ln["source_line_idx"], "line_id": ln["source_line_id"]}
                for ln in validated_lines
                if ln.get("source_po_id") == pid
            ]
            await precheck_sales_delivery(db, orders[pid], mapped)

        max_retries = 3
        for attempt in range(max_retries):
            shipment_index, authoritative_sj = await allocate_sales_delivery_identity(
                db, primary_id, po_number
            )
            delivery_id = str(uuid.uuid4())

            # Stock claims (optional PSS) — use final delivery_id; release on failure.
            claimed_by_po: Dict[str, Any] = {}
            try:
                for pid in source_ids:
                    if not await should_claim_sales_delivery_stock(db, orders[pid]):
                        continue
                    po_lines = [
                        {**ln, "line_idx": ln["source_line_idx"], "line_id": ln["source_line_id"]}
                        for ln in validated_lines
                        if ln.get("source_po_id") == pid
                    ]
                    claimed_by_po[pid] = await claim_sales_delivery_stock(
                        db, orders[pid], po_lines, delivery_id, user
                    )
            except Exception:
                for claim in claimed_by_po.values():
                    if claim:
                        await release_sales_delivery_stock(db, claim)
                raise

            all_remaining_zero = all(remaining_by_po[pid] <= 0 for pid in source_ids)
            delivery_doc = {
                "id": delivery_id,
                "delivery_id": delivery_id,
                "po_id": primary_id,
                "po_number": po_number,
                "source_po_ids": source_ids,
                "is_multi_source": True,
                "source_allocations": source_allocations,
                "sj_number": authoritative_sj,
                "shipment_index": shipment_index,
                "delivery_date": delivery_date,
                "customer": final_customer,
                "customer_name": final_customer,
                "outlet": final_outlet,
                "outlet_name": final_outlet,
                "recipient": final_recipient,
                "recipient_name": final_recipient,
                "address": final_address,
                "shipping_address": final_address,
                "delivery_zone": final_zone,
                "shipping_zone": final_zone,
                "sales": final_sales,
                "sales_name": final_sales,
                "expedition": final_expedition,
                "expedition_or_vehicle": final_expedition,
                "notes": final_notes,
                "delivery_note": final_notes,
                "recipient_phone": recipient_phone,
                "recipient_phone_wa": recipient_phone_wa,
                "created_by": user.get("email") or "",
                "created_by_name": user.get("name") or user.get("email") or "",
                "created_by_role": user.get("role") or "",
                "created_at": now,
                "updated_at": now,
                "line_items": validated_lines,
                "total_qty": total_delivery_qty,
                "item_count": len(validated_lines),
                "status": "SELESAI" if all_remaining_zero else "SEBAGIAN",
                "is_cancelled": False,
                ALLOCATION_COMMIT_PENDING_FIELD: True,
                "physical_stock_snapshot": primary_order.get("physical_stock_snapshot"),
            }
            delivery_doc["operational_branch_id"] = document_branch(primary_order)
            order_branch_id = str(primary_order.get("branch_id") or "").strip()
            if order_branch_id.isdigit():
                delivery_doc["branch_id"] = order_branch_id
            if sj_location:
                delivery_doc["delivery_location"] = sj_location

            stamp = {
                "delivery_note_number": authoritative_sj,
                "delivery_note_created_at": now,
                "delivery_note_created_by": user.get("email") or "",
                "delivery_note_created_by_name": user.get("name") or user.get("email") or "",
            }

            def _shipment_for(pid: str) -> dict:
                slice_lines = slice_lines_for_po(delivery_doc, pid)
                entry = {
                    "id": delivery_id,
                    "number": authoritative_sj,
                    "shipment_index": shipment_index,
                    "delivery_date": delivery_date,
                    "customer": final_customer,
                    "outlet": final_outlet,
                    "sales": final_sales,
                    "recipient": final_recipient,
                    "address": final_address,
                    "delivery_zone": final_zone,
                    "expedition": final_expedition,
                    "notes": final_notes,
                    "recipient_phone": recipient_phone,
                    "recipient_phone_wa": recipient_phone_wa,
                    "lines": slice_lines,
                    "total_qty": sum(int(x.get("qty") or 0) for x in slice_lines),
                    "created_at": now,
                    "created_by": user.get("email") or "",
                    "sales_delivery_id": delivery_id,
                    "source_po_ids": source_ids,
                    "is_multi_source": True,
                }
                if delivery_doc.get("branch_id"):
                    entry["branch_id"] = delivery_doc["branch_id"]
                entry["operational_branch_id"] = delivery_doc["operational_branch_id"]
                if sj_location:
                    entry["delivery_location"] = sj_location
                return entry

            # 1) Claim per-line remaining (atomic conditional writes — standalone-safe).
            line_claims: List[dict] = []
            try:
                for ln in validated_lines:
                    claim = await claim_source_line_allocation(
                        db,
                        source_po_id=ln["source_po_id"],
                        source_line_id=str(ln["source_line_id"]),
                        source_line_idx=int(ln["source_line_idx"]),
                        ordered_qty=int(ln.get("ordered_qty") or 0),
                        qty=int(ln["qty"]),
                        sales_delivery_id=delivery_id,
                    )
                    line_claims.append(claim)
            except Exception:
                await release_claimed_allocations(db, line_claims)
                for claim in claimed_by_po.values():
                    if claim:
                        await release_sales_delivery_stock(db, claim)
                raise

            # 2) Persist SJ + sync all source shipment mirrors.
            # Prefer a multi-doc transaction when the deployment supports it; otherwise
            # sequential writes with compensating delete/release on failure (fail-closed
            # if compensation cannot restore a consistent state).
            async def _compensate_provisional_multi() -> None:
                await release_claimed_allocations(db, line_claims)
                for claim in claimed_by_po.values():
                    if claim:
                        await release_sales_delivery_stock(db, claim)
                await db.sales_deliveries.delete_one({"id": delivery_id})
                for pid in source_ids:
                    await db.orders.update_one(
                        {"id": pid},
                        {"$pull": {"shipments": {"sales_delivery_id": delivery_id}}},
                    )
                    await db.orders.update_one(
                        {"id": pid},
                        {"$pull": {"shipments": {"id": delivery_id}}},
                    )

            async def _persist_without_txn() -> None:
                """Standalone: commit+activate first; publish mirrors only when SJ is active."""
                await db.sales_deliveries.insert_one(delivery_doc)
                synced: List[str] = []
                try:
                    await commit_claimed_allocations(db, line_claims)
                    await activate_delivery_allocation_commit(db, delivery_id)
                    for pid in source_ids:
                        await ensure_order_shipment_synced(
                            db, pid, _shipment_for(pid), stamp=stamp,
                        )
                        synced.append(pid)
                except Exception:
                    for pid in synced:
                        await db.orders.update_one(
                            {"id": pid},
                            {"$pull": {"shipments": {"sales_delivery_id": delivery_id}}},
                        )
                        await db.orders.update_one(
                            {"id": pid},
                            {"$pull": {"shipments": {"id": delivery_id}}},
                        )
                    # Do not delete SJ before the outer compensation releases
                    # committed allocation claims. If this worker crashes here,
                    # the pending SJ must remain discoverable for safe recovery.
                    # The outer handler releases claims first, then deletes SJ.
                    raise

            try:
                await require_reserved_allocations(db, line_claims)
            except Exception:
                await release_claimed_allocations(db, line_claims)
                for claim in claimed_by_po.values():
                    if claim:
                        await release_sales_delivery_stock(db, claim)
                raise

            use_txn = await mongo_transactions_available(db)
            try:
                if use_txn:
                    try:
                        client = db.client
                        async with await client.start_session() as session:
                            session.start_transaction()
                            try:
                                # Claim commit + activate before mirrors: abort leaves
                                # no durable SJ/mirror for the loser.
                                await db.sales_deliveries.insert_one(delivery_doc, session=session)
                                await commit_claimed_allocations(
                                    db, line_claims, session=session,
                                )
                                await activate_delivery_allocation_commit(
                                    db, delivery_id, session=session,
                                )
                                for pid in source_ids:
                                    await ensure_order_shipment_synced(
                                        db, pid, _shipment_for(pid), stamp=stamp, session=session,
                                    )
                                await session.commit_transaction()
                            except Exception:
                                if session.in_transaction:
                                    await session.abort_transaction()
                                raise
                    except Exception as txn_exc:
                        msg = str(txn_exc).lower()
                        if "transaction" in msg or "replica" in msg or "illegal" in msg:
                            await _persist_without_txn()
                        else:
                            raise
                else:
                    await _persist_without_txn()
                delivery_doc.pop(ALLOCATION_COMMIT_PENDING_FIELD, None)
            except DuplicateKeyError:
                await _compensate_provisional_multi()
                if attempt == max_retries - 1:
                    raise HTTPException(409, _CONCURRENT_CONFLICT)
                continue
            except HTTPException:
                await _compensate_provisional_multi()
                raise
            except Exception:
                await _compensate_provisional_multi()
                raise

            delivery_doc.pop("_id", None)
            try:
                file_entry, pdf_err = await ensure_generated_sj_pdf(db, primary_order, delivery_doc, user)
                if file_entry:
                    delivery_doc["generated_sj_file_id"] = file_entry["id"]
                elif pdf_err:
                    delivery_doc["generated_sj_file_error"] = pdf_err
                for pid, claim in claimed_by_po.items():
                    if claim:
                        await finalize_sales_delivery_stock(db, claim, user)
                    else:
                        await handle_sales_delivery_created(
                            db,
                            orders[pid],
                            {
                                **delivery_doc,
                                "line_items": [
                                    {**ln, "line_idx": ln["source_line_idx"], "line_id": ln["source_line_id"]}
                                    for ln in validated_lines
                                    if ln.get("source_po_id") == pid
                                ],
                            },
                            user,
                        )
                try:
                    from services.notifications import emit_surat_jalan_created_notifications
                    await emit_surat_jalan_created_notifications(
                        db,
                        order=primary_order,
                        shipment=_shipment_for(primary_id),
                    )
                except Exception:
                    logger.exception("surat jalan created notifications failed")
                return delivery_doc
            except Exception:
                for claim in claimed_by_po.values():
                    if claim:
                        await release_sales_delivery_stock(db, claim)
                raise

        raise HTTPException(500, "Gagal membuat Pengiriman Pesanan multi-PO.")


DELIVERY_LIST_SORT = [("created_at", -1), ("id", -1)]
DELIVERY_LIST_HARD_MAX = 50000
DELIVERY_DEFAULT_PAGE_SIZE = 50
DELIVERY_PAGE_SIZE_MAX = 100
DELIVERY_PAGE_SIZES = frozenset({10, 25, 50, 100})


async def _po_delivery_statuses(db, po_ids: List[str]) -> Dict[str, str]:
    """Authoritative SELESAI/SEBAGIAN from parent PO fulfillment (same rules as before).

    Includes multi-source SJ allocations attributed to each PO via source_line_idx.
    """
    po_statuses: Dict[str, str] = {}
    if not po_ids:
        return po_statuses
    orders_map = {
        o["id"]: o
        async for o in db.orders.find({"id": {"$in": po_ids}}, {"_id": 0})
    }
    deliveries = await db.sales_deliveries.find(
        {
            "is_cancelled": {"$ne": True},
            ALLOCATION_COMMIT_PENDING_FIELD: {"$ne": True},
            "$or": [
                {"po_id": {"$in": po_ids}},
                {"source_po_ids": {"$in": po_ids}},
                {"line_items.source_po_id": {"$in": po_ids}},
            ],
        },
        {"_id": 0},
    ).to_list(5000)

    for pid, ord_doc in orders_map.items():
        raw_lines = ord_doc.get("line_items") or []
        tot_ord = sum(int(l.get("quantity") or 0) for l in raw_lines)
        shipped_map = shipped_qty_by_source_line(deliveries, pid)
        tot_ship = sum(shipped_map.values())
        all_fulfilled = len(raw_lines) > 0 and all(
            shipped_map.get(idx, 0) >= int(l.get("quantity") or 0)
            for idx, l in enumerate(raw_lines)
        )
        if all_fulfilled or (tot_ord > 0 and tot_ship >= tot_ord):
            po_statuses[pid] = "SELESAI"
        else:
            po_statuses[pid] = "SEBAGIAN"
    return po_statuses


def _normalize_delivery_list_status(raw: Any, *, fallback: str = "SELESAI") -> str:
    derived = str(raw or fallback).strip().upper() or fallback
    if derived not in ("SELESAI", "SEBAGIAN"):
        return "SELESAI" if derived == "TERKIRIM" else "SEBAGIAN"
    return derived


async def list_sales_deliveries(
    db,
    q: Optional[str] = None,
    po_id: Optional[str] = None,
    status: Optional[str] = None,
    delivery_date_preset: Optional[str] = None,
    created_by: Optional[str] = None,
    page: Any = 1,
    page_size: Any = DELIVERY_DEFAULT_PAGE_SIZE,
    branch_query: Optional[dict] = None,
) -> Dict[str, Any]:
    """List sales deliveries with search/filter, status counts, and server pagination.

    Status SEBAGIAN/SELESAI is derived from parent PO fulfillment (unchanged business rule).
    Counts are computed for the same q/date/created_by context without the status filter.
    Full documents are loaded only for the requested page (light projection for classify/count).
    """
    from services.sales_date_presets import match_shipping_date
    from services.sales_list_tabs import parse_page_params

    try:
        page_num, size = parse_page_params(
            page, page_size if page_size is not None else DELIVERY_DEFAULT_PAGE_SIZE
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if size > DELIVERY_PAGE_SIZE_MAX:
        raise HTTPException(400, "Halaman atau ukuran halaman tidak valid.")

    # Operational list only — provisional allocation_commit_pending excluded.
    query: dict = {
        "is_cancelled": {"$ne": True},
        ALLOCATION_COMMIT_PENDING_FIELD: {"$ne": True},
    }
    and_clauses: List[dict] = []
    if branch_query:
        and_clauses.append(branch_query)

    if po_id and po_id.strip():
        pid = po_id.strip()
        and_clauses.append({
            "$or": [
                {"po_id": pid},
                {"source_po_ids": pid},
                {"line_items.source_po_id": pid},
            ]
        })

    created_by_key = str(created_by or "").strip()
    if created_by_key:
        query["created_by"] = created_by_key

    if q and q.strip():
        needle = re.escape(q.strip())
        and_clauses.append({
            "$or": [
                {"sj_number": {"$regex": needle, "$options": "i"}},
                {"po_number": {"$regex": needle, "$options": "i"}},
                {"customer": {"$regex": needle, "$options": "i"}},
                {"outlet": {"$regex": needle, "$options": "i"}},
                {"recipient": {"$regex": needle, "$options": "i"}},
                {"sales": {"$regex": needle, "$options": "i"}},
                {"source_po_ids": {"$regex": needle, "$options": "i"}},
            ]
        })
    if and_clauses:
        query["$and"] = and_clauses

    light_proj = {
        "_id": 0,
        "id": 1,
        "po_id": 1,
        "delivery_date": 1,
        "created_at": 1,
        "status": 1,
    }
    light_rows = await db.sales_deliveries.find(query, light_proj).sort(
        DELIVERY_LIST_SORT
    ).to_list(DELIVERY_LIST_HARD_MAX + 1)
    if len(light_rows) > DELIVERY_LIST_HARD_MAX:
        raise HTTPException(
            409,
            f"Hasil daftar pengiriman melebihi batas aman ({DELIVERY_LIST_HARD_MAX}). "
            "Perhalus pencarian/filter.",
        )

    po_ids = list({d["po_id"] for d in light_rows if d.get("po_id")})
    po_statuses = await _po_delivery_statuses(db, po_ids)

    date_preset = str(delivery_date_preset or "all").strip().lower() or "all"
    filter_norm = (status or "").strip().upper()
    counts = {"all": 0, "SEBAGIAN": 0, "SELESAI": 0}
    eligible_ids: List[str] = []

    for row in light_rows:
        if date_preset not in ("", "all"):
            if not match_shipping_date(row.get("delivery_date"), date_preset):
                continue
        derived = _normalize_delivery_list_status(
            po_statuses.get(row.get("po_id")) or row.get("status")
        )
        counts["all"] += 1
        counts[derived] += 1
        if filter_norm and filter_norm not in ("ALL", "SEMUA", "SEMUA STATUS"):
            if derived != filter_norm:
                continue
        row_id = str(row.get("id") or "").strip()
        if row_id:
            eligible_ids.append(row_id)

    total = len(eligible_ids)
    total_pages = max(1, (total + size - 1) // size) if total else 1
    if page_num > total_pages:
        page_num = total_pages
    start = (page_num - 1) * size
    page_ids = eligible_ids[start:start + size]

    page_rows: List[dict] = []
    if page_ids:
        found = await db.sales_deliveries.find(
            {"id": {"$in": page_ids}},
            {"_id": 0},
        ).to_list(len(page_ids))
        by_id = {str(d.get("id")): d for d in found}
        for rid in page_ids:
            doc = by_id.get(rid)
            if not doc:
                continue
            doc["status"] = _normalize_delivery_list_status(
                po_statuses.get(doc.get("po_id")) or doc.get("status")
            )
            page_rows.append(doc)
        from services.po_identity_fields import attach_po_identity_fields
        await attach_po_identity_fields(db, page_rows, po_id_key="po_id")

    return {
        "deliveries": page_rows,
        # Page length (aligned with Faktur/Pesanan list contract).
        "count": len(page_rows),
        "total": total,
        "page": page_num,
        "page_size": size,
        "total_pages": total_pages,
        # Same q/date/created_by context; not limited to the current page.
        "counts": counts,
    }


async def list_sales_delivery_filter_options(db, branch_query=None) -> Dict[str, Any]:
    """Global created_by options for SJ header filter (deduped by created_by)."""
    rows = await db.sales_deliveries.find(
        {"$and": [branch_query or {}, {"is_cancelled": {"$ne": True}, "created_by": {"$exists": True, "$nin": [None, ""]}}]},
        {"_id": 0, "created_by": 1, "created_by_name": 1},
    ).to_list(5000)
    by_key: Dict[str, str] = {}
    for row in rows:
        key = str(row.get("created_by") or "").strip()
        if not key or key in by_key:
            continue
        label = str(row.get("created_by_name") or "").strip() or key
        by_key[key] = label
    options = [
        {"value": key, "label": by_key[key]}
        for key in sorted(by_key.keys(), key=lambda k: by_key[k].lower())
    ]
    return {"created_by_options": options}


async def get_sales_delivery_detail(db, delivery_id: str) -> Dict[str, Any]:
    """Get single sales delivery with its PO snapshot and all related shipments.

    Provisional pending SJ are fail-closed (not returned as official detail).
    """
    did = str(delivery_id or "").strip()
    delivery = await require_operational_sales_delivery(db, did, attempt_reconcile=True)

    # Read-time legacy normalization — do not rewrite stored docs.
    normalized_alloc = normalize_delivery_source_allocations(delivery)
    delivery["source_allocations"] = normalized_alloc
    delivery["source_po_ids"] = delivery_source_po_ids(delivery)
    delivery["is_multi_source"] = is_multi_source_delivery(delivery)

    po_id = delivery.get("po_id")
    po_summary = await get_po_delivery_summary(db, po_id)
    delivery["status"] = po_summary.get("delivery_status") or "SELESAI"

    source_summaries = []
    orders_by_id: Dict[str, dict] = {}
    for src_id in delivery["source_po_ids"]:
        try:
            src_summary = await get_po_delivery_summary(db, src_id)
            source_summaries.append({
                "po_id": src_id,
                "po_number": src_summary.get("po_number"),
                "customer": src_summary.get("customer"),
                "total_ordered": src_summary.get("total_ordered"),
                "total_shipped": src_summary.get("total_shipped"),
                "total_remaining": src_summary.get("total_remaining"),
                "delivery_status": src_summary.get("delivery_status"),
                "allocated_on_this_sj": sum(
                    int(a.get("allocated_qty") or 0)
                    for a in normalized_alloc
                    if str(a.get("source_po_id") or "") == str(src_id)
                ),
            })
        except HTTPException:
            continue
        order_doc = await db.orders.find_one({"id": src_id}, {"_id": 0})
        if order_doc:
            orders_by_id[src_id] = order_doc

    slice_fulfillment = assess_multi_source_slice_fulfillment(delivery, orders_by_id)

    # All deliveries for primary PO to display complete multi-shipment history
    all_deliveries = await db.sales_deliveries.find(
        active_deliveries_query_for_po(str(po_id)),
        {"_id": 0}
    ).sort("shipment_index", 1).to_list(100)

    oneshot = None
    try:
        from services.sales_delivery_fulfillment import assess_oneshot_capabilities
        oneshot = await assess_oneshot_capabilities(db, str(delivery.get("id") or did))
        # Prefer freshly computed slice from capability (same helper).
        if isinstance(oneshot.get("slice_fulfillment"), dict):
            slice_fulfillment = oneshot["slice_fulfillment"]
    except Exception:
        oneshot = None

    related_invoices = []
    relation_history = []
    try:
        from services.sales_document_relations import (
            related_invoices_for_sj,
            build_relation_history_events,
        )
        related_invoices = await related_invoices_for_sj(db, str(delivery.get("id") or did))
        primary_order = orders_by_id.get(str(po_id)) if po_id else None
        relation_history = build_relation_history_events(
            order=primary_order,
            delivery=delivery,
        )
    except Exception:
        related_invoices = []
        relation_history = []

    delivery["fulfillment_last_status"] = (
        (oneshot or {}).get("fulfillment_last_status")
        or delivery.get("fulfillment_last_status")
    )
    delivery["fulfillment_commit_pending"] = bool(
        (oneshot or {}).get("fulfillment_commit_pending")
        or delivery.get("fulfillment_commit_pending")
    )

    return {
        "delivery": delivery,
        "po_summary": po_summary,
        "source_po_summaries": source_summaries,
        "slice_fulfillment": slice_fulfillment,
        "oneshot_fulfillment": oneshot,
        "related_invoices": related_invoices,
        "relation_history": relation_history,
        "all_po_deliveries": all_deliveries,
    }


def _format_sj_pdf_phone_display(value: Any) -> str:
    """Normalize phone text for SJ PDF rendering only (not persisted)."""
    text = str(value or "").strip()
    if not text:
        return ""
    # Collapse unicode whitespace to ASCII space; drop other controls.
    text = re.sub(r"[\s\u00a0\u2000-\u200b\u202f\u205f\u3000]+", " ", text)
    text = "".join(ch for ch in text if ch.isprintable() or ch == " ")
    return text.strip()[:40]


def generate_sales_delivery_pdf_bytes(
    delivery: dict,
    po_summary: Optional[dict] = None,
    *,
    recipient_signature_png: Optional[bytes] = None,
    recipient_name: Optional[str] = None,
    recipient_signed_at: Optional[str] = None,
    driver_signature_png: Optional[bytes] = None,
    driver_name: Optional[str] = None,
    driver_signed_at: Optional[str] = None,
    design: Optional[dict] = None,
) -> bytes:
    """A4 portrait Surat Jalan. Renders the delivery document only.

    Optional signature PNGs stay on Kurir (driver) and Penerima (recipient).
    This is document rendering — it MUST NOT mutate packed/loading/delivery qty.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle
    from services.delivery_pod_signature import normalize_signature_image_for_pdf
    from services.pdf_date import format_pdf_date
    from services.pdf_layout import (
        build_pdf,
        content_width,
        esc,
        format_qty,
        logo_flowable,
        scale_widths,
        styles,
    )
    from services.print_context_resolver import build_delivery_print_context
    from services.print_design_settings import resolve_print_design
    from services.print_footer_render import (
        build_generic_invoice_footer_flowables,
        footer_elements_active,
    )
    from services.print_header_render import (
        build_generic_invoice_header_flowables,
        header_elements_active,
    )

    delivery = delivery or {}
    design = resolve_print_design(design)
    sj_design = design.get("surat_jalan") or {}
    header_elements = sj_design.get("header_elements") or []
    footer_elements = sj_design.get("footer_elements") or []
    use_generic_header = header_elements_active(header_elements)
    use_generic_footer = footer_elements_active(footer_elements)
    print_context = None
    if use_generic_header or use_generic_footer:
        print_context = build_delivery_print_context(delivery, design)
    _sig_tmp_paths: List[str] = []

    def _text(value) -> str:
        return str(value or "").strip()

    def _normalize_sig_file(raw_png: Optional[bytes]):
        if not raw_png:
            return None
        normalized = normalize_signature_image_for_pdf(raw_png)
        if not normalized:
            logger.warning("signature image normalize failed — PDF will omit image")
            return None
        import os
        import tempfile
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".png")
            with os.fdopen(fd, "wb") as fh:
                fh.write(normalized)
            try:
                from PIL import Image as PILImage
                with PILImage.open(tmp_path) as pil:
                    iw, ih = pil.size
            except Exception:
                iw, ih = 300, 300
            _sig_tmp_paths.append(tmp_path)
            return tmp_path, max(iw, 1), max(ih, 1)
        except Exception:
            logger.warning("signature Image embed failed", exc_info=True)
            if tmp_path:
                try:
                    import os as _os
                    _os.unlink(tmp_path)
                except Exception:
                    pass
            return None

    def _sig_image(tmp_path: str, iw: int, ih: int, max_w: float, max_h: float):
        scale = min(max_w / iw, max_h / ih)
        draw_w = max(iw * scale, 0.5 * mm * 10)
        draw_h = max(ih * scale, 0.5 * mm * 10)
        img = Image(tmp_path, width=draw_w, height=draw_h)
        img.hAlign = "CENTER"
        return img

    def _signed_date(raw_ts: Optional[str]) -> str:
        if not raw_ts:
            return ""
        return format_pdf_date(raw_ts, fallback="")

    sty = styles(design.get("accent_color"))
    width = content_width(A4)
    elements = []
    try:
        po_number = _text(delivery.get("po_number"))
        if use_generic_header:
            elements.extend(
                build_generic_invoice_header_flowables(
                    elements=header_elements,
                    context=print_context,
                    design=design,
                    sty=sty,
                    width=width,
                    document_title="SURAT JALAN",
                )
            )
            if po_number:
                elements.append(Paragraph(f"Nomor PO : {esc(po_number)}", sty["small"]))
            elements.append(Spacer(1, 2 * mm))
        else:
            logo = logo_flowable(design, width=36 * mm, height=12.2 * mm, company_style=sty["company"])
            address_html = "<br/>".join(esc(line) for line in (design.get("address_lines") or []))
            identity = Paragraph(
                f"<font size='12'><b>{esc(design.get('company_name') or '')}</b></font><br/>{address_html}",
                sty["address"],
            )
            brand = Table(
                [[logo, identity]],
                colWidths=[40 * mm, width - 40 * mm],
            )
            brand.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            elements.append(brand)
            elements.append(Spacer(1, 2 * mm))
            elements.append(Paragraph("SURAT JALAN", sty["title"]))
            elements.append(Spacer(1, 2 * mm))

            address = _text(delivery.get("address"))
            customer = Paragraph(
                f"Kepada<br/><b>{esc(_text(delivery.get('customer')) or '-')}</b><br/>{esc(address)}",
                sty["body"],
            )
            grid_width = width * 0.56
            left_width = width - grid_width
            customer_cell = Table([[customer]], colWidths=[left_width - 2 * mm])
            customer_cell.setStyle(TableStyle([
                ("LINEBELOW", (0, 0), (-1, -1), 0.7, colors.black),
                ("LEFTPADDING", (0, 0), (-1, -1), 1),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))

            def cell(label: str, value: str) -> Paragraph:
                shown = esc(value) if value else "&nbsp;"
                return Paragraph(f"{esc(label)}<br/><b>{shown}</b>", sty["small"])

            meta = [
                [
                    cell("Nama Penjual", _text(delivery.get("sales"))),
                    cell("Nomor", _text(delivery.get("sj_number"))),
                ],
                [
                    cell("Ekspedisi", _text(delivery.get("expedition"))),
                    cell("Pembuat Data", _text(delivery.get("created_by_name"))),
                ],
                [
                    cell("Tanggal Pengiriman", format_pdf_date(delivery.get("delivery_date"))),
                    cell("Zona Kirim", _text(delivery.get("delivery_zone"))),
                ],
            ]
            meta_table = Table(meta, colWidths=[grid_width / 2, grid_width / 2])
            meta_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.black),
                ("LINEAFTER", (0, 0), (0, -1), 0.6, colors.black),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.black, None, (2, 2)),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            party = Table(
                [[customer_cell, meta_table]],
                colWidths=[width - grid_width, grid_width],
            )
            party.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            elements.append(party)
            if po_number:
                elements.append(Spacer(1, 1 * mm))
                elements.append(Paragraph(f"Nomor PO : {esc(po_number)}", sty["small"]))
            elements.append(Spacer(1, 3 * mm))

        header = [
            Paragraph("<b>Kode Barang</b>", sty["header"]),
            Paragraph("<b>Nama Barang</b>", sty["header"]),
            Paragraph("<b>Kts.</b>", sty["header"]),
            Paragraph("<b>Satuan</b>", sty["header"]),
            Paragraph("<b>Outlet</b>", sty["header"]),
        ]
        rows = [header]
        total_qty = 0
        note_parts: List[str] = []
        header_note = _text(delivery.get("notes"))
        if header_note:
            note_parts.append(header_note)
        lines = [row for row in (delivery.get("line_items") or []) if isinstance(row, dict)]
        delivery_outlet = _text(delivery.get("outlet"))
        for line in lines:
            raw_qty = line.get("qty") if line.get("qty") is not None else line.get("quantity")
            try:
                qty = int(float(raw_qty or 0))
            except (TypeError, ValueError):
                qty = 0
            total_qty += qty
            line_note = _text(line.get("notes"))
            if line_note and line_note not in note_parts and line_note not in ("-",):
                note_parts.append(line_note)
            outlet = _text(line.get("outlet")) or delivery_outlet
            rows.append([
                Paragraph(esc(_text(line.get("item_code"))), sty["body"]),
                Paragraph(esc(_text(line.get("name") or line.get("item_name"))), sty["body"]),
                Paragraph(esc(format_qty(qty)), sty["right"]),
                Paragraph(esc(_text(line.get("unit")) or "pcs"), sty["right"]),
                Paragraph(esc(outlet), sty["body"]),
            ])
        if len(rows) == 1:
            rows.append([Paragraph("", sty["body"]) for _ in range(5)])
        items = Table(rows, colWidths=scale_widths([78, 210, 58, 52, 80], width), repeatRows=1)
        items.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ]))
        elements.append(items)
        elements.append(Spacer(1, 3 * mm))

        if delivery.get("total_qty") is not None and not lines:
            try:
                shown_total = int(float(delivery.get("total_qty")))
            except (TypeError, ValueError):
                shown_total = total_qty
        else:
            shown_total = total_qty
        item_count = len(lines)
        if not lines and delivery.get("item_count") is not None:
            try:
                item_count = int(delivery.get("item_count"))
            except (TypeError, ValueError):
                item_count = 0
        note = Paragraph(
            f"<b>Keterangan:</b><br/>{esc(chr(10).join(note_parts)) or '&nbsp;'}",
            sty["body"],
        )
        note_width = width - 62 * mm
        note_box = Table([[note]], colWidths=[note_width])
        note_box.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.7, colors.black),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        totals = [
            [
                Paragraph("<b>Total Kuantitas</b>", sty["body"]),
                Paragraph(f"<b>{esc(format_qty(shown_total))}</b>", sty["right_bold"]),
            ],
            [
                Paragraph("<b>Jumlah Barang</b>", sty["body"]),
                Paragraph(f"<b>{item_count}</b>", sty["right_bold"]),
            ],
        ]
        totals_table = Table(totals, colWidths=[34 * mm, 22 * mm])
        totals_table.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, -1), 0.6, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        lower = Table(
            [[note_box, totals_table]],
            colWidths=[note_width + 2 * mm, width - note_width - 2 * mm],
        )
        lower.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(lower)
        elements.append(Spacer(1, 6 * mm))

        # Presentation-only generic footer (optional). POD/driver evidence stays frozen below.
        if use_generic_footer:
            elements.extend(
                build_generic_invoice_footer_flowables(
                    elements=footer_elements,
                    context=print_context,
                    design=design,
                    sty=sty,
                    width=width,
                )
            )
            elements.append(Spacer(1, 4 * mm))

        recip_file = _normalize_sig_file(recipient_signature_png)
        driver_file = _normalize_sig_file(driver_signature_png)
        cap_w = 32 * mm
        cap_h = 18 * mm
        recipient_img = _sig_image(*recip_file, cap_w, cap_h) if recip_file else None
        driver_img = _sig_image(*driver_file, cap_w, cap_h) if driver_file else None
        display_recipient = _text(recipient_name or delivery.get("recipient"))
        display_driver = _text(driver_name or delivery.get("driver_name"))
        # Display-only normalization — does not mutate delivery/order storage.
        recipient_phone_text = _format_sj_pdf_phone_display(delivery.get("recipient_phone"))
        recipient_when = _signed_date(recipient_signed_at)
        driver_when = _signed_date(driver_signed_at)

        labels = list(sj_design.get("signature_labels") or ["Gudang", "Cek Barang", "Kurir", "Penerima"])
        date_label = str(sj_design.get("date_label") or "Tgl.")
        images = [None, None, driver_img, recipient_img]
        names = ["", "", display_driver, display_recipient]
        whens = ["", "", driver_when, recipient_when]
        slot = width / 4
        name_width = slot - 16 * mm
        label_row = []
        gap_row = []
        line_row = []
        for idx, (label, image, name, when) in enumerate(zip(labels, images, names, whens)):
            label_row.append(Paragraph(esc(label), sty["center"]))
            gap_row.append(image if image is not None else Spacer(1, 12 * mm))
            tgl = date_label + (f" {when}" if when else "")
            # Underline only the name band — phone sits below so LINEBELOW
            # cannot cut through telephone glyphs / space gaps.
            name_para = Paragraph(esc(name) if name else "&nbsp;", sty["small"])
            name_cell = Table([[name_para]], colWidths=[name_width])
            name_cell.setStyle(TableStyle([
                ("LINEBELOW", (0, 0), (-1, -1), 0.6, colors.black),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ]))
            stacked_rows = [[name_cell]]
            if idx == 3 and recipient_phone_text:
                stacked_rows.append([Paragraph(esc(recipient_phone_text), sty["small"])])
            stacked = Table(stacked_rows, colWidths=[name_width])
            stacked.setStyle(TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            line = Table(
                [[
                    Paragraph(esc(tgl), sty["small"]),
                    stacked,
                ]],
                colWidths=[14 * mm, name_width],
            )
            line.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            line_row.append(line)
        sig = Table([label_row, gap_row, line_row], colWidths=[slot, slot, slot, slot])
        sig.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, 1), "BOTTOM"),
            ("VALIGN", (0, 2), (-1, 2), "BOTTOM"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, 0), 1),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
            ("TOPPADDING", (0, 1), (-1, 1), 1),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 1),
        ]))
        elements.append(sig)
        _ = po_summary
        return build_pdf(elements, A4)
    finally:
        import os
        for tmp in _sig_tmp_paths:
            try:
                os.unlink(tmp)
            except Exception:
                pass

async def generate_signed_sj_pdf_for_order(
    db,
    order: dict,
    sig: dict,
    obj_storage,
    *,
    user: Optional[dict] = None,
    driver_sig: Optional[dict] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Render signed SJ PDF for exact delivery_note/shipment. Pure document write to storage.

    Returns (signed_storage_path, error_code). Does not mutate packed/loading/delivered qty
    or workflow stage. Preserves original SJ storage_path on the file entry.
    """
    from services.surat_jalan_files import find_file, find_generated_sj_file
    from services.delivery_pod_signature import (
        get_active_driver_signature,
        get_frozen_driver_signatures,
        resolve_pdf_driver_display_name,
        storage_key_for_signed_sj,
        now_iso as _now,
    )

    sj_file_id = str(sig.get("sj_file_id") or sig.get("delivery_note_id") or "").strip()
    shipment_id = str(sig.get("shipment_id") or "").strip()
    if not sj_file_id and shipment_id:
        gen = find_generated_sj_file(order, shipment_id)
        if gen:
            sj_file_id = str(gen.get("id") or "")
    entry = find_file(order, sj_file_id) if sj_file_id else None
    if not entry and shipment_id:
        entry = find_generated_sj_file(order, shipment_id)
        if entry:
            sj_file_id = str(entry.get("id") or "")

    if driver_sig is None:
        driver_sig = get_active_driver_signature(order)
        if not driver_sig:
            # Prefer frozen driver sig matching confirmation / shipment
            conf_id = str(sig.get("confirmation_id") or "")
            for ds in get_frozen_driver_signatures(order):
                if conf_id and str(ds.get("confirmation_id") or "") == conf_id:
                    driver_sig = ds
                    break
                if shipment_id and str(ds.get("shipment_id") or "") == shipment_id:
                    driver_sig = ds
                    break
            if not driver_sig:
                frozen = get_frozen_driver_signatures(order)
                driver_sig = frozen[-1] if frozen else None

    delivery = None
    if shipment_id:
        delivery = await db.sales_deliveries.find_one(
            {"id": shipment_id, "is_cancelled": {"$ne": True}},
            {"_id": 0},
        )
    if not delivery and sj_file_id:
        delivery = await db.sales_deliveries.find_one(
            {"generated_sj_file_id": sj_file_id, "is_cancelled": {"$ne": True}},
            {"_id": 0},
        )
    if not delivery:
        lines = (entry or {}).get("lines") or []
        snap_phone = None
        want_ids = {
            str(shipment_id or "").strip(),
            str((entry or {}).get("shipment_id") or "").strip(),
        }
        want_ids.discard("")
        for sh in order.get("shipments") or []:
            if not isinstance(sh, dict):
                continue
            if str(sh.get("id") or "").strip() in want_ids:
                snap_phone = sh.get("recipient_phone")
                break
        delivery = {
            "id": shipment_id or sj_file_id or str(uuid.uuid4()),
            "po_id": order.get("id"),
            "po_number": order.get("po_number"),
            "sj_number": (entry or {}).get("sj_number") or order.get("delivery_note_number"),
            "customer": order.get("customer") or order.get("customer_name"),
            "outlet": order.get("outlet"),
            "address": order.get("address") or order.get("delivery_address"),
            "recipient": sig.get("recipient_name") or order.get("recipient") or order.get("recipient_name"),
            "recipient_phone": snap_phone,
            "driver_name": resolve_pdf_driver_display_name(order, driver_sig=driver_sig, user=user),
            "sales": order.get("sales") or order.get("sales_name"),
            "delivery_zone": order.get("delivery_zone") or order.get("zone"),
            "expedition": order.get("expedition"),
            "delivery_date": order.get("delivery_note_created_at")
            or order.get("effective_delivery_date")
            or order.get("tanggal_pengiriman")
            or order.get("scheduled_delivery_date")
            or order.get("delivery_deadline_manual")
            or order.get("deadline"),
            "shipment_index": 1,
            "line_items": [
                {
                    "name": ln.get("name"),
                    "item_code": ln.get("item_code"),
                    "production_process": ln.get("production_process"),
                    "qty": ln.get("qty") if ln.get("qty") is not None else ln.get("quantity"),
                    "unit": ln.get("unit") or "pcs",
                    "notes": ln.get("notes"),
                    "color": ln.get("color"),
                    "line_idx": ln.get("line_idx"),
                    "line_id": ln.get("line_id"),
                }
                for ln in lines
            ] or [
                {
                    "name": li.get("name"),
                    "item_code": li.get("item_code") or li.get("accurate_item_id"),
                    "production_process": li.get("production_process"),
                    "qty": li.get("quantity"),
                    "unit": li.get("unit") or "pcs",
                    "color": li.get("color"),
                    "line_idx": idx,
                    "line_id": li.get("line_id") or li.get("id"),
                }
                for idx, li in enumerate(order.get("line_items") or [])
            ],
            "notes": order.get("delivery_note_notes"),
            "created_by_name": (user or {}).get("name") or order.get("delivery_note_created_by_name"),
        }
    else:
        delivery = dict(delivery)
        delivery["driver_name"] = resolve_pdf_driver_display_name(
            order, driver_sig=driver_sig, delivery=delivery, user=user
        )

    sig_path = str(sig.get("signature_storage_path") or "").strip()
    png_bytes = None
    if sig_path and obj_storage:
        try:
            png_bytes, _ct = obj_storage.get_object(sig_path)
        except Exception:
            png_bytes = None
    if not png_bytes:
        return None, "signature_bytes_missing"

    driver_png = None
    driver_path = ""
    if driver_sig:
        driver_path = str(
            driver_sig.get("driver_signature_storage_path")
            or driver_sig.get("signature_storage_path")
            or ""
        ).strip()
    if driver_path and obj_storage:
        try:
            driver_png, _ct = obj_storage.get_object(driver_path)
        except Exception:
            driver_png = None
    if not driver_png:
        return None, "driver_signature_bytes_missing"

    try:
        from services.print_design_settings import get_print_design_for_pdf
        design = await get_print_design_for_pdf(db, branch_id=(delivery or {}).get("branch_id"))
        pdf_bytes = generate_sales_delivery_pdf_bytes(
            delivery,
            None,
            recipient_signature_png=png_bytes,
            recipient_name=sig.get("recipient_name"),
            recipient_signed_at=sig.get("recipient_signed_at"),
            driver_signature_png=driver_png,
            driver_name=resolve_pdf_driver_display_name(
                order, driver_sig=driver_sig, delivery=delivery, user=user
            ),
            driver_signed_at=(driver_sig or {}).get("driver_signed_at"),
            design=design,
        )
    except Exception:
        logger.exception("signed SJ PDF generate failed")
        return None, "pdf_generate_failed"

    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        return None, "pdf_invalid"

    po_id = str(order.get("id") or "")
    signed_path = storage_key_for_signed_sj(po_id, sj_file_id or shipment_id or sig.get("id") or "sig")
    try:
        stored = obj_storage.put_object(signed_path, pdf_bytes, "application/pdf")
        signed_path = stored.get("path") or signed_path
    except Exception:
        logger.exception("signed SJ storage write failed")
        return None, "storage_failed"

    stamp = _now()
    if sj_file_id:
        await db.orders.update_one(
            {"id": po_id, "surat_jalan_files.id": sj_file_id},
            {"$set": {
                "surat_jalan_files.$.signed_storage_path": signed_path,
                "surat_jalan_files.$.signed_at": stamp,
                "surat_jalan_files.$.signed_recipient_name": sig.get("recipient_name"),
                "surat_jalan_files.$.signed_driver_name": resolve_pdf_driver_display_name(
                    order, driver_sig=driver_sig, delivery=delivery, user=user
                ),
                "surat_jalan_files.$.original_storage_path": (entry or {}).get("original_storage_path")
                    or (entry or {}).get("storage_path"),
            }},
        )
        await db.files.update_one(
            {"id": sj_file_id, "kind": "surat_jalan"},
            {"$set": {
                "signed_storage_path": signed_path,
                "signed_at": stamp,
                "original_storage_path": (entry or {}).get("original_storage_path")
                    or (entry or {}).get("storage_path"),
            }},
            upsert=False,
        )
    return signed_path, None
