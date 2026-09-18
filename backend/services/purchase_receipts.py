"""Accurate Penerimaan Barang evidence → Pembelian Barang aggregate.

Pembelian Barang = SUM received_qty of valid receipts per accurate_item_id.
Never Purchase Order qty. Never name matching. Never unitPrice/amount.
Upsert by accurate_receipt_id (idempotent). Failed fetch does not truncate.
Manual (source=manual) documents coexist in the same collection; Accurate sync
must not overwrite or deactivate them. Manual receipts are excluded from
Pembelian Barang / current-stock aggregation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from services.accurate import AccurateClient
from services.current_stock import qty_num
from services.purchase_common import (
    PREFIX_GOODS_RECEIPT,
    actor_snapshot,
    cancellation_fields,
    stamp_create,
    get_named_lock,
    is_cancelled,
    next_dated_seq,
    now_iso as purchase_now_iso,
    parse_qty,
)

COLLECTION = "accurate_purchase_receipts"
SYNC_META_COLLECTION = "accurate_receipt_sync_meta"
SYNC_META_ID = "receive-item"
PURCHASE_SOURCE_ACCURATE = "ACCURATE"
PURCHASE_SOURCE_MANUAL = "MANUAL_FALLBACK"
RECEIPT_SOURCE_ACCURATE = "accurate"
RECEIPT_SOURCE_MANUAL = "manual"
MANUAL_RECEIPT_ID_PREFIX = "manual:"
DEFAULT_RECEIPT_CANCEL_REASON = "Pembatalan penerimaan"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_accurate_receipt_source(doc: Optional[dict]) -> bool:
    src = str((doc or {}).get("source") or RECEIPT_SOURCE_ACCURATE).strip().lower()
    return src == RECEIPT_SOURCE_ACCURATE


def receipt_doc_from_normalized(norm: Dict[str, Any], *, now: Optional[str] = None) -> Dict[str, Any]:
    ts = now or now_iso()
    accurate_actor = {"id": "accurate", "name": "Accurate", "role": "system", "email": ""}
    return {
        "accurate_receipt_id": str(norm["accurate_receipt_id"]),
        "receipt_number": norm.get("receipt_number") or "",
        "receipt_date": norm.get("receipt_date") or "",
        "approval_status": norm.get("approval_status") or "",
        "valid": bool(norm.get("valid")),
        "lines": list(norm.get("lines") or []),
        "source": RECEIPT_SOURCE_ACCURATE,
        "last_synced_at": ts,
        "created_at": ts,
        "updated_at": ts,
        "created_by": accurate_actor,
        "updated_by": accurate_actor,
    }


def aggregate_received_qty(receipts: Iterable[dict]) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Sum valid Accurate receipt line quantity per accurate_item_id.

    Skip lines without ID. Skip manual receipts so local documents do not
    change Pembelian Barang / Stock Saat Ini.
    """
    bucket: Dict[str, float] = {}
    aliases: Dict[str, str] = {}
    for rec in receipts:
        if rec.get("valid") is False:
            continue
        if not is_accurate_receipt_source(rec):
            continue
        for li in (rec.get("lines") or []):
            aid = str(li.get("accurate_item_id") or "").strip()
            if not aid:
                continue
            key = f"a:{aid}"
            aliases[key] = key
            bucket[key] = qty_num(bucket.get(key, 0)) + qty_num(li.get("received_qty"))
    return bucket, aliases


def purchase_qty_for_item(item: dict, bucket: Dict[str, float], aliases: Dict[str, str]) -> float:
    aid = str(item.get("accurate_item_id") or "").strip()
    if aid:
        key = aliases.get(f"a:{aid}", f"a:{aid}")
        return qty_num(bucket.get(key, 0))
    return 0


def upsert_receipts(existing: List[dict], incoming: List[dict], *, complete: bool, now: Optional[str] = None) -> Dict[str, Any]:
    """Pure upsert for tests. If complete, receipts missing from incoming become invalid."""
    ts = now or now_iso()
    by_id = {str(r.get("accurate_receipt_id")): dict(r) for r in existing if r.get("accurate_receipt_id")}
    created = updated = unchanged = 0
    incoming_ids = set()
    for norm in incoming:
        rid = str(norm.get("accurate_receipt_id") or "")
        if not rid:
            continue
        incoming_ids.add(rid)
        doc = receipt_doc_from_normalized(norm, now=ts)
        prev = by_id.get(rid)
        if prev is not None and not is_accurate_receipt_source(prev):
            continue
        if prev is None:
            doc["created_at"] = ts
            by_id[rid] = doc
            created += 1
            continue
        same = (
            prev.get("receipt_number") == doc["receipt_number"]
            and prev.get("receipt_date") == doc["receipt_date"]
            and prev.get("approval_status") == doc["approval_status"]
            and prev.get("valid") == doc["valid"]
            and prev.get("lines") == doc["lines"]
        )
        merged = dict(prev)
        merged.update(doc)
        merged["created_at"] = prev.get("created_at") or ts
        merged["created_by"] = prev.get("created_by") or doc.get("created_by")
        merged["updated_by"] = doc.get("updated_by") or prev.get("updated_by")
        by_id[rid] = merged
        if same:
            unchanged += 1
        else:
            updated += 1
    deactivated = 0
    if complete:
        for rid, prev in list(by_id.items()):
            if not is_accurate_receipt_source(prev):
                continue
            if rid not in incoming_ids and prev.get("valid") is not False:
                prev = dict(prev)
                prev["valid"] = False
                prev["updated_at"] = ts
                prev["last_synced_at"] = ts
                prev["updated_by"] = {"id": "accurate", "name": "Accurate", "role": "system", "email": ""}
                by_id[rid] = prev
                deactivated += 1
    return {
        "docs": list(by_id.values()),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "deactivated": deactivated,
    }


async def load_receipts(db) -> List[dict]:
    return await db[COLLECTION].find({}, {"_id": 0}).to_list(10000)


async def load_sync_meta(db) -> Optional[dict]:
    return await db[SYNC_META_COLLECTION].find_one({"id": SYNC_META_ID}, {"_id": 0})


def last_success_at(meta: Optional[dict]) -> Optional[str]:
    return (meta or {}).get("last_success_at")


def purchase_source_from_meta(meta: Optional[dict]) -> str:
    if last_success_at(meta):
        return PURCHASE_SOURCE_ACCURATE
    return PURCHASE_SOURCE_MANUAL


async def sync_purchase_receipts(db, client: AccurateClient) -> Dict[str, Any]:
    """Fetch then upsert. Incomplete fetch does not write or wipe evidence."""
    existing = await load_receipts(db)
    try:
        payload = await client.fetch_all_receive_items()
    except Exception as e:
        return {
            "ok": False,
            "preserved": True,
            "error": type(e).__name__,
            "receipts_found": 0,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "failed": 1,
            "total_lines": sum(len(r.get("lines") or []) for r in existing),
            "last_synced_at": (await load_sync_meta(db) or {}).get("last_success_at"),
            "purchase_source": purchase_source_from_meta(await load_sync_meta(db)),
            "mode": getattr(client, "auth_mode", "mock"),
        }

    receipts = payload.get("receipts") or []
    failed = payload.get("failed") or []
    complete = bool(payload.get("complete")) and not failed
    if not receipts and failed:
        return {
            "ok": False,
            "preserved": True,
            "error": "detail_failed",
            "receipts_found": 0,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "failed": len(failed),
            "total_lines": sum(len(r.get("lines") or []) for r in existing),
            "last_synced_at": (await load_sync_meta(db) or {}).get("last_success_at"),
            "purchase_source": purchase_source_from_meta(await load_sync_meta(db)),
            "mode": payload.get("mode"),
        }

    now = now_iso()
    id_map: Dict[str, str] = {}
    try:
        masters = await db.master_items.find({}, {"_id": 0, "id": 1, "accurate_item_id": 1}).to_list(10000)
        for m in masters or []:
            aid = str(m.get("accurate_item_id") or "").strip()
            if aid:
                id_map[aid] = m.get("id")
    except Exception:
        id_map = {}
    for rec in receipts:
        for line in rec.get("lines") or []:
            aid = str(line.get("accurate_item_id") or "").strip()
            if aid and id_map.get(aid):
                line["master_item_id"] = id_map[aid]
    result = upsert_receipts(existing, receipts, complete=complete, now=now)
    for doc in result["docs"]:
        if not is_accurate_receipt_source(doc):
            continue
        rid = doc["accurate_receipt_id"]
        to_set = {k: v for k, v in doc.items() if k != "created_at"}
        await db[COLLECTION].update_one(
            {"accurate_receipt_id": rid},
            {"$set": to_set, "$setOnInsert": {"created_at": doc.get("created_at") or now}},
            upsert=True,
        )
    total_lines = sum(len(d.get("lines") or []) for d in result["docs"] if d.get("valid") is not False)
    ok = complete
    persisted = bool(receipts)
    prev_meta = await load_sync_meta(db) or {}
    meta = {
        "id": SYNC_META_ID,
        "last_attempt_at": now,
        "last_success_at": now if persisted else prev_meta.get("last_success_at"),
        "last_ok": ok,
        "mode": payload.get("mode"),
        "summary": {
            "receipts_found": len(receipts),
            "created": result["created"],
            "updated": result["updated"],
            "unchanged": result["unchanged"],
            "failed": len(failed),
            "total_lines": total_lines,
        },
    }
    await db[SYNC_META_COLLECTION].update_one({"id": SYNC_META_ID}, {"$set": meta}, upsert=True)
    return {
        "ok": ok,
        "preserved": True,
        "receipts_found": len(receipts),
        "created": result["created"],
        "updated": result["updated"],
        "unchanged": result["unchanged"],
        "failed": len(failed),
        "failed_ids": [f.get("id") for f in failed],
        "total_lines": total_lines,
        "last_synced_at": meta.get("last_success_at"),
        "purchase_source": purchase_source_from_meta(meta),
        "mode": payload.get("mode"),
        "complete": complete,
        "detail_required": payload.get("detail_required"),
    }


def public_receipt(rec: dict, masters_by_aid: Optional[Dict[str, dict]] = None) -> dict:
    """Transaction evidence for Pembelian UI. Display names from Master Barang/Jasa."""
    masters_by_aid = masters_by_aid or {}
    lines = []
    for li in rec.get("lines") or []:
        aid = str(li.get("accurate_item_id") or "").strip()
        master = masters_by_aid.get(aid) or {}
        lines.append({
            "accurate_line_id": li.get("accurate_line_id") or li.get("line_id") or "",
            "accurate_item_id": aid,
            "master_item_id": li.get("master_item_id") or master.get("id") or "",
            "purchase_order_line_id": li.get("purchase_order_line_id") or "",
            "item_code": master.get("item_code") or li.get("item_code") or "",
            "item_name": master.get("item_name") or li.get("item_name") or "",
            "unit": li.get("unit") or master.get("unit") or "",
            "line_notes": li.get("notes") or li.get("line_notes") or "",
            "received_qty": qty_num(li.get("received_qty")),
        })
    status = rec.get("approval_status") or ""
    if not is_accurate_receipt_source(rec):
        status = "CANCELLED" if is_cancelled(rec) else "ACTIVE"
    elif not status:
        status = "VALID" if rec.get("valid") is not False else "INVALID"
    source = rec.get("source") or RECEIPT_SOURCE_ACCURATE
    last_synced = rec.get("last_synced_at") if is_accurate_receipt_source(rec) else None
    return {
        "accurate_receipt_id": rec.get("accurate_receipt_id"),
        "receipt_number": rec.get("receipt_number") or "",
        "receipt_date": rec.get("receipt_date") or "",
        "status": status,
        "valid": rec.get("valid") is not False,
        "source": source,
        "supplier": rec.get("supplier") or "",
        "notes": rec.get("notes") or "",
        "purchase_order_id": rec.get("purchase_order_id") or "",
        "po_number": rec.get("po_number") or "",
        "goods_receipt_id": rec.get("goods_receipt_id") or "",
        "line_count": len(lines),
        "total_qty": sum(li["received_qty"] for li in lines),
        "last_synced_at": last_synced,
        "created_at": rec.get("created_at"),
        "updated_at": rec.get("updated_at"),
        "created_by": rec.get("created_by"),
        "updated_by": rec.get("updated_by"),
        "cancelled": bool(rec.get("cancelled")),
        "cancelled_at": rec.get("cancelled_at"),
        "cancelled_by": rec.get("cancelled_by"),
        "cancel_reason": rec.get("cancel_reason") or "",
        "reversal_movement_id": rec.get("reversal_movement_id"),
        "reversal_movement_ids": rec.get("reversal_movement_ids") or [],
        "lines": lines,
    }


def filter_public_receipts(rows: List[dict], q: str = "") -> List[dict]:
    needle = (q or "").strip().lower()
    if not needle:
        return list(rows)
    out = []
    for r in rows:
        blob = " ".join([
            str(r.get("receipt_number") or ""),
            str(r.get("accurate_receipt_id") or ""),
            str(r.get("status") or ""),
            str(r.get("supplier") or ""),
            str(r.get("notes") or ""),
            str(r.get("po_number") or ""),
            str(r.get("source") or ""),
        ]).lower()
        if needle in blob:
            out.append(r)
            continue
        if any(
            needle in str(li.get("item_code") or "").lower()
            or needle in str(li.get("item_name") or "").lower()
            or needle in str(li.get("accurate_item_id") or "")
            for li in (r.get("lines") or [])
        ):
            out.append(r)
    return out


def page_public_receipts(rows: List[dict], *, page: int = 1, page_size: int = 50) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "items": rows[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": max(1, (total + page_size - 1) // page_size) if total else 1,
    }


def _normalize_manual_lines(raw_lines: Any) -> List[dict]:
    if not isinstance(raw_lines, list) or not raw_lines:
        raise HTTPException(400, "Minimal satu baris penerimaan wajib diisi.")
    lines = []
    for idx, raw in enumerate(raw_lines):
        if not isinstance(raw, dict):
            raise HTTPException(400, f"Baris penerimaan ke-{idx + 1} tidak valid.")
        master_item_id = str(raw.get("master_item_id") or "").strip()
        if not master_item_id:
            raise HTTPException(
                400,
                f"Baris ke-{idx + 1}: pilih item dari Master Item. Nama bebas tidak didukung.",
            )
        item_name = str(raw.get("item_name") or raw.get("name") or "").strip()
        accurate_item_id = str(raw.get("accurate_item_id") or "").strip()
        if not item_name:
            raise HTTPException(400, f"Nama item baris ke-{idx + 1} wajib diisi.")
        qty = parse_qty(raw.get("received_qty", raw.get("quantity")), f"Qty diterima '{item_name}'")
        line_id = str(raw.get("line_id") or raw.get("accurate_line_id") or "").strip() or str(uuid.uuid4())
        lines.append({
            "line_idx": idx,
            "line_id": line_id,
            "accurate_line_id": line_id,
            "purchase_order_line_id": str(raw.get("purchase_order_line_id") or "").strip(),
            "master_item_id": master_item_id,
            "accurate_item_id": accurate_item_id,
            "item_code": str(raw.get("item_code") or "").strip(),
            "item_name": item_name,
            "unit": str(raw.get("unit") or "pcs").strip() or "pcs",
            "received_qty": qty,
            "notes": str(raw.get("notes") or raw.get("line_notes") or "").strip(),
        })
    return lines


def _lines_from_goods_receipt(gr: dict) -> List[dict]:
    lines = []
    for idx, li in enumerate(gr.get("lines") or []):
        line_id = str(li.get("line_id") or "").strip() or str(uuid.uuid4())
        lines.append({
            "line_idx": idx,
            "line_id": line_id,
            "accurate_line_id": line_id,
            "purchase_order_line_id": str(li.get("purchase_order_line_id") or "").strip(),
            "master_item_id": str(li.get("master_item_id") or "").strip(),
            "accurate_item_id": str(li.get("accurate_item_id") or "").strip(),
            "item_code": str(li.get("item_code") or "").strip(),
            "item_name": str(li.get("item_name") or "").strip(),
            "unit": str(li.get("unit") or "pcs").strip() or "pcs",
            "received_qty": qty_num(li.get("received_qty")),
            "notes": str(li.get("notes") or "").strip(),
        })
    if not lines:
        raise HTTPException(400, "Minimal satu baris penerimaan wajib diisi.")
    return lines


async def create_manual_purchase_receipt(db, payload: Dict[str, Any], user: dict) -> dict:
    """Create a local Penerimaan Barang document in accurate_purchase_receipts.

    Does not write journals or call HPP. Does not update Accurate.
    Standalone (no PO): when Stock V2 is enabled with stock_in_trigger=goods_receipt,
    appends idempotent PENERIMAAN movements and updates product_stock_state.
    With PO: reuses official create_purchase_goods_receipt (PO remaining + physical
    stock engine), then stores a list projection with source=manual.
    Manual receipts remain excluded from Accurate Pembelian Barang aggregation.
    """
    from services.physical_stock_engine import (
        handle_manual_receipt_created,
        precheck_manual_receipt_lines,
    )

    async with get_named_lock("purchase_receipt_manual"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                if not existing.get("goods_receipt_id") and not is_cancelled(existing):
                    await handle_manual_receipt_created(db, existing, user)
                return existing

        po_id = str(payload.get("purchase_order_id") or "").strip()
        goods_receipt = None
        if po_id:
            from services.purchase_goods_receipts import create_purchase_goods_receipt
            goods_receipt = await create_purchase_goods_receipt(db, payload, user)
            if goods_receipt and goods_receipt.get("id"):
                listed = await db[COLLECTION].find_one(
                    {"goods_receipt_id": goods_receipt["id"]}, {"_id": 0}
                )
                if listed:
                    return listed
            lines = _lines_from_goods_receipt(goods_receipt)
            supplier = str(goods_receipt.get("supplier") or payload.get("supplier") or "").strip()
            receipt_date = str(goods_receipt.get("receipt_date") or payload.get("receipt_date") or "").strip()[:10]
            notes = str(payload.get("notes") or goods_receipt.get("notes") or "").strip()
            po_number = goods_receipt.get("po_number") or ""
            receipt_number = goods_receipt.get("receipt_number") or ""
        else:
            lines = _normalize_manual_lines(payload.get("lines") or payload.get("line_items"))
            await precheck_manual_receipt_lines(db, lines)
            supplier = str(payload.get("supplier") or payload.get("supplier_name") or "").strip()
            if not supplier:
                raise HTTPException(400, "Supplier wajib diisi.")
            ts_date = purchase_now_iso()[:10]
            receipt_date = str(payload.get("receipt_date") or ts_date).strip()[:10]
            if not receipt_date:
                raise HTTPException(400, "Tanggal penerimaan wajib diisi.")
            notes = str(payload.get("notes") or "").strip()
            po_number = str(payload.get("po_number") or payload.get("reference") or "").strip()
            receipt_number = ""

        ts = now_iso()
        doc_id = str(uuid.uuid4())
        accurate_receipt_id = f"{MANUAL_RECEIPT_ID_PREFIX}{doc_id}"
        if goods_receipt and goods_receipt.get("id"):
            accurate_receipt_id = f"{MANUAL_RECEIPT_ID_PREFIX}{goods_receipt['id']}"

        collision = await db[COLLECTION].find_one({"accurate_receipt_id": accurate_receipt_id}, {"_id": 1})
        if collision:
            raise HTTPException(409, "Identitas penerimaan bentrok dengan dokumen yang sudah ada.")

        doc = {
            "accurate_receipt_id": accurate_receipt_id,
            "receipt_number": receipt_number,
            "receipt_date": receipt_date,
            "approval_status": "",
            "valid": True,
            "lines": lines,
            "source": RECEIPT_SOURCE_MANUAL,
            "supplier": supplier,
            "notes": notes,
            "purchase_order_id": po_id or (goods_receipt or {}).get("purchase_order_id") or "",
            "po_number": po_number,
            "goods_receipt_id": (goods_receipt or {}).get("id") or "",
            **stamp_create(user),
        }
        if idem:
            doc["idempotency_key"] = idem

        if not doc["receipt_number"]:
            doc["receipt_number"] = await next_dated_seq(db, COLLECTION, "receipt_number", PREFIX_GOODS_RECEIPT)

        await db[COLLECTION].insert_one(dict(doc))
        doc.pop("_id", None)
        if not doc.get("goods_receipt_id"):
            await handle_manual_receipt_created(db, doc, user)
        return doc


async def cancel_manual_purchase_receipt(
    db,
    receipt_id: str,
    user: dict,
    *,
    reason: str = "",
) -> dict:
    """Soft-cancel one ERP manual receipt; Accurate history is immutable."""
    rid = str(receipt_id or "").strip()
    why = str(reason or "").strip()
    async with get_named_lock("purchase_receipt_manual"):
        doc = await db[COLLECTION].find_one(
            {
                "$or": [
                    {"accurate_receipt_id": rid},
                    {"receipt_number": rid},
                ]
            },
            {"_id": 0},
        )
        if not doc:
            raise HTTPException(404, f"Penerimaan Barang '{rid}' tidak ditemukan.")
        if is_accurate_receipt_source(doc):
            raise HTTPException(409, "Riwayat Penerimaan Accurate tidak dapat dibatalkan dari ERP.")
        if is_cancelled(doc):
            return doc
        why = why or DEFAULT_RECEIPT_CANCEL_REASON

        linked_gr = None
        reversal_ids: List[str] = []
        goods_receipt_id = str(doc.get("goods_receipt_id") or "").strip()
        if goods_receipt_id:
            from services.purchase_goods_receipts import cancel_purchase_goods_receipt

            linked_gr = await cancel_purchase_goods_receipt(
                db,
                goods_receipt_id,
                user,
                reason=why,
            )
            reversal_ids = list(linked_gr.get("reversal_movement_ids") or [])
            if linked_gr.get("reversal_movement_id") and not reversal_ids:
                reversal_ids = [linked_gr["reversal_movement_id"]]
        else:
            from services.purchase_goods_receipts import apply_receipt_stock_reversals

            reversal_ids = await apply_receipt_stock_reversals(
                db,
                str(doc.get("accurate_receipt_id") or ""),
                receipt_number=doc.get("receipt_number") or "",
                supplier=doc.get("supplier") or "SUPPLIER",
                reason=why,
                user=user,
                order_id=doc.get("purchase_order_id") or None,
            )

        fields = cancellation_fields(user, why)
        fields.update({
            "status": "CANCELLED",
            "valid": False,
            "reversal_movement_id": (
                (linked_gr or {}).get("reversal_movement_id")
                or (reversal_ids[0] if reversal_ids else None)
            ),
            "reversal_movement_ids": (
                (linked_gr or {}).get("reversal_movement_ids")
                or reversal_ids
                or []
            ),
        })
        await db[COLLECTION].update_one(
            {
                "accurate_receipt_id": doc["accurate_receipt_id"],
                "cancelled": {"$ne": True},
                "source": RECEIPT_SOURCE_MANUAL,
            },
            {"$set": fields},
        )
        return await db[COLLECTION].find_one(
            {"accurate_receipt_id": doc["accurate_receipt_id"]},
            {"_id": 0},
        )
