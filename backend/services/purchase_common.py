"""Shared purchasing helpers: numbering, qty/money, identity, actor snapshot.

Business numbers follow the sales dated-sequence convention PREFIX-YYYYMMDD-NNN.
Purchase prefixes are distinct from sales PO/INV/PAY/SJ so collections never collide
by convention even though uniqueness is per-collection.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

# Dated sequence (same shape as sales PO-YYYYMMDD-NNN).
PREFIX_REQUEST = "PR"
PREFIX_PURCHASE_ORDER = "PB"
PREFIX_GOODS_RECEIPT = "GR"
PREFIX_PURCHASE_INVOICE = "FPB"
PREFIX_PURCHASE_PAYMENT = "PPB"

_locks: Dict[str, Dict[asyncio.AbstractEventLoop, asyncio.Lock]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_ymd() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def get_named_lock(name: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    bucket = _locks.setdefault(name, {})
    if loop not in bucket:
        bucket[loop] = asyncio.Lock()
    return bucket[loop]


def actor_snapshot(user: Optional[dict]) -> dict:
    user = user or {}
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "name": str(user.get("name") or user.get("email") or ""),
        "role": str(user.get("role") or ""),
    }


def parse_qty(raw: Any, field_name: str = "Kuantitas") -> float:
    if raw is None or raw == "":
        raise HTTPException(400, f"{field_name} wajib diisi.")
    try:
        d = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field_name} '{raw}' tidak valid. Format harus angka.")
    if d <= Decimal("0"):
        raise HTTPException(400, f"{field_name} harus lebih besar dari 0.")
    return float(d)


def parse_qty_allow_zero(raw: Any, field_name: str = "Kuantitas") -> float:
    if raw is None or raw == "":
        return 0.0
    try:
        d = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field_name} '{raw}' tidak valid. Format harus angka.")
    if d < Decimal("0"):
        raise HTTPException(400, f"{field_name} tidak boleh negatif.")
    return float(d)


def parse_money(raw: Any, field_name: str = "Harga") -> float:
    if raw is None or raw == "":
        return 0.0
    try:
        d = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field_name} '{raw}' tidak valid. Format harus angka.")
    if d < Decimal("0"):
        raise HTTPException(400, f"{field_name} tidak boleh negatif.")
    return float(d)


def parse_positive_money(raw: Any, field_name: str = "Jumlah") -> float:
    if raw is None or raw == "":
        raise HTTPException(400, f"{field_name} wajib diisi.")
    try:
        d = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field_name} '{raw}' tidak valid. Format harus angka.")
    if d <= Decimal("0"):
        raise HTTPException(400, f"{field_name} harus lebih besar dari 0.")
    return float(d)


def money_round(value: Any) -> float:
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01")))


def qty_round(value: Any) -> float:
    d = Decimal(str(value or 0))
    return float(d)


def qty_le(a: Any, b: Any) -> bool:
    return Decimal(str(a or 0)) <= Decimal(str(b or 0)) + Decimal("0.0000001")


def remaining_qty(ordered: Any, cumulative: Any) -> float:
    d = Decimal(str(ordered or 0)) - Decimal(str(cumulative or 0))
    if d < 0:
        return 0.0
    return float(d)


def line_subtotal(qty: Any, unit_price: Any) -> float:
    return money_round(Decimal(str(qty or 0)) * Decimal(str(unit_price or 0)))


def item_identity(line: dict) -> str:
    """Product identity for costing. Never uses display name as a relation key."""
    aid = str(line.get("accurate_item_id") or "").strip()
    if aid:
        return f"a:{aid}"
    mid = str(line.get("master_item_id") or "").strip()
    if mid:
        return f"m:{mid}"
    po_id = str(line.get("purchase_order_id") or "").strip()
    line_id = str(line.get("line_id") or "").strip()
    if po_id and line_id:
        return f"line:{po_id}:{line_id}"
    return f"line:{line_id or 'unknown'}"


def dated_sequence_number(prefix: str, seq: int, day: Optional[str] = None) -> str:
    return f"{prefix}-{day or today_ymd()}-{int(seq):03d}"


def derive_child_number(parent_number: str, child_prefix: str, index: int) -> str:
    """INV/PAY-style: replace alphabetic prefix, suffix -2/-3 for later docs."""
    pn = (parent_number or "").strip()
    if not pn:
        base = f"{child_prefix}-{today_ymd()}-001"
    else:
        m = re.match(r"^[A-Za-z]+([/.\-\s_]?)(.*)$", pn)
        if m:
            base = f"{child_prefix}{m.group(1)}{m.group(2)}"
        else:
            base = f"{child_prefix}-{pn}"
    if index <= 1:
        return base
    return f"{base}-{index}"


async def next_dated_seq(db, collection_name: str, field: str, prefix: str) -> str:
    day = today_ymd()
    stem = f"{prefix}-{day}-"
    regex = f"^{re.escape(stem)}\\d+$"
    cursor = db[collection_name].find({field: {"$regex": regex}}, {field: 1, "_id": 0})
    existing = await cursor.to_list(2000)
    max_seq = 0
    for doc in existing:
        suffix = str(doc.get(field) or "")[len(stem):]
        try:
            seq = int(suffix)
            if seq > max_seq:
                max_seq = seq
        except ValueError:
            continue
    return dated_sequence_number(prefix, max_seq + 1, day)


async def insert_with_unique_number(
    db,
    collection_name: str,
    field: str,
    doc: dict,
    allocate,
) -> dict:
    """allocate() -> str. Retries on DuplicateKey for the business-number field."""
    coll = db[collection_name]
    last_err = None
    for _ in range(25):
        doc[field] = await allocate()
        try:
            await coll.insert_one(doc)
            doc.pop("_id", None)
            return doc
        except DuplicateKeyError as exc:
            last_err = exc
            msg = str(exc)
            if "idempotency_key" in msg:
                if doc.get("idempotency_key"):
                    existing = await coll.find_one({"idempotency_key": doc["idempotency_key"]}, {"_id": 0})
                    if existing:
                        return existing
                raise HTTPException(409, "Dokumen duplikat terdeteksi.")
            if field in msg or "E11000" in msg:
                continue
            raise HTTPException(409, "Dokumen duplikat terdeteksi.")
    raise HTTPException(409, f"Gagal mengalokasikan nomor unik ({field}). {last_err}")
