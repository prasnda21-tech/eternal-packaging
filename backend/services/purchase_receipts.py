"""Accurate Penerimaan Barang evidence → Pembelian Barang aggregate.

Pembelian Barang = SUM received_qty of valid receipts per accurate_item_id.
Never Purchase Order qty. Never name matching. Never unitPrice/amount.
Upsert by accurate_receipt_id (idempotent). Failed fetch does not truncate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.accurate import AccurateClient
from services.current_stock import qty_num

COLLECTION = "accurate_purchase_receipts"
SYNC_META_COLLECTION = "accurate_receipt_sync_meta"
SYNC_META_ID = "receive-item"
PURCHASE_SOURCE_ACCURATE = "ACCURATE"
PURCHASE_SOURCE_MANUAL = "MANUAL_FALLBACK"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def receipt_doc_from_normalized(norm: Dict[str, Any], *, now: Optional[str] = None) -> Dict[str, Any]:
    ts = now or now_iso()
    return {
        "accurate_receipt_id": str(norm["accurate_receipt_id"]),
        "receipt_number": norm.get("receipt_number") or "",
        "receipt_date": norm.get("receipt_date") or "",
        "approval_status": norm.get("approval_status") or "",
        "valid": bool(norm.get("valid")),
        "lines": list(norm.get("lines") or []),
        "source": "accurate",
        "last_synced_at": ts,
        "updated_at": ts,
    }


def aggregate_received_qty(receipts: Iterable[dict]) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Sum valid receipt line quantity per accurate_item_id. Skip lines without ID."""
    bucket: Dict[str, float] = {}
    aliases: Dict[str, str] = {}
    for rec in receipts:
        if rec.get("valid") is False:
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
        by_id[rid] = merged
        if same:
            unchanged += 1
        else:
            updated += 1
    deactivated = 0
    if complete:
        for rid, prev in list(by_id.items()):
            if rid not in incoming_ids and prev.get("valid") is not False:
                prev = dict(prev)
                prev["valid"] = False
                prev["updated_at"] = ts
                prev["last_synced_at"] = ts
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
            "accurate_line_id": li.get("accurate_line_id") or "",
            "accurate_item_id": aid,
            "master_item_id": li.get("master_item_id") or master.get("id"),
            "item_code": master.get("item_code") or "",
            "item_name": master.get("item_name") or "",
            "unit": li.get("unit") or master.get("unit") or "",
            "received_qty": qty_num(li.get("received_qty")),
        })
    status = rec.get("approval_status") or ""
    if not status:
        status = "VALID" if rec.get("valid") is not False else "INVALID"
    return {
        "accurate_receipt_id": rec.get("accurate_receipt_id"),
        "receipt_number": rec.get("receipt_number") or "",
        "receipt_date": rec.get("receipt_date") or "",
        "status": status,
        "valid": rec.get("valid") is not False,
        "source": rec.get("source") or "accurate",
        "line_count": len(lines),
        "total_qty": sum(li["received_qty"] for li in lines),
        "last_synced_at": rec.get("last_synced_at"),
        "created_at": rec.get("created_at"),
        "updated_at": rec.get("updated_at"),
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
