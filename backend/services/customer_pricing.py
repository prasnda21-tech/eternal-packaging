"""Customer Pricing groups with versioned multi-product price configuration.

Customer Pricing only reads authoritative HPP data. It never recalculates HPP
and never mutates master items, purchasing, inventory, or stock.
"""
from __future__ import annotations

import copy
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.hpp_valuation import (
    METHOD_AVG,
    METHOD_FIFO,
    get_hpp_settings,
    query_cost_boundary,
)
from services.purchase_common import actor_snapshot, get_named_lock, money_round, now_iso

GROUPS = "customer_pricing_groups"
VERSIONS = "customer_pricing_versions"
LEGACY_PRODUCTS = "customer_pricing_products"
SETTINGS_KEY = "customer_pricing"

MAX_CUSTOMER_PRICING_QTY = 10_000_000
STATUS_DRAFT = "DRAFT"
STATUS_PUBLISHED = "PUBLISHED"
MODE_HPP_FORMULA = "HPP_FORMULA"
MODE_MANUAL_TIER = "MANUAL_TIER_PRICE"
HPP_AUTO = "AUTO_HPP"
HPP_MANUAL = "MANUAL_HPP"
PROFIT_FIXED = "HPP_PLUS_FIXED"
PROFIT_PERCENT = "HPP_PLUS_PERCENT"
HPP_OK = "OK"
HPP_MISSING = "MISSING"
HPP_AMBIGUOUS = "UNRESOLVED_AMBIGUOUS_FIFO"

MSG_FIFO_AMBIGUOUS = (
    "HPP FIFO memiliki beberapa layer dengan nilai berbeda sehingga belum dapat "
    "digunakan sebagai HPP otomatis tunggal. Gunakan HPP Manual atau Harga Tier "
    "Manual, atau selesaikan kebijakan HPP FIFO terlebih dahulu."
)
MSG_HPP_MISSING = "HPP otomatis belum tersedia."

PERM_VIEW = "settings.customer_pricing.view"
PERM_MANAGE = "settings.customer_pricing.manage"
PERM_PUBLISH = "settings.customer_pricing.publish"


def _new_id() -> str:
    return str(uuid.uuid4())


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _clean_group_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise HTTPException(400, "Nama kelompok wajib diisi.")
    if len(name) > 120:
        raise HTTPException(400, "Nama kelompok maksimal 120 karakter.")
    return name


def _parse_positive_int_qty(raw: Any, field: str) -> int:
    if raw is None or raw == "":
        raise HTTPException(400, f"{field} wajib diisi.")
    try:
        if isinstance(raw, bool):
            raise ValueError
        if isinstance(raw, float) and not raw.is_integer():
            raise ValueError
        value = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field} harus bilangan bulat positif.")
    if value < 1:
        raise HTTPException(400, f"{field} harus >= 1.")
    if value > MAX_CUSTOMER_PRICING_QTY:
        raise HTTPException(400, f"{field} melebihi batas {MAX_CUSTOMER_PRICING_QTY:,}.")
    return value


def _parse_positive_money(raw: Any, field: str) -> float:
    try:
        if raw is None or raw == "" or isinstance(raw, bool):
            raise ValueError
        value = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field} tidak valid.")
    if value <= 0:
        raise HTTPException(400, f"{field} harus lebih besar dari 0.")
    return money_round(value)


def _parse_positive_percent(raw: Any, field: str) -> float:
    try:
        if raw is None or raw == "" or isinstance(raw, bool):
            raise ValueError
        value = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field} tidak valid.")
    if value <= 0:
        raise HTTPException(400, f"{field} harus lebih besar dari 0.")
    return float(value.quantize(Decimal("0.01")))


def compute_unit_price_fixed(hpp: Any, profit: Any) -> float:
    return money_round(_dec(hpp) + _dec(profit))


def compute_unit_price_percent(hpp: Any, percent: Any) -> float:
    return money_round(_dec(hpp) * (Decimal("1") + _dec(percent) / Decimal("100")))


def default_global_settings() -> dict:
    return {
        "key": SETTINGS_KEY,
        "admin_phone": "",
        "default_quotation_validity_days": None,
        "updated_at": None,
        "updated_by": None,
    }


def public_global_settings(doc: Optional[dict]) -> dict:
    if not doc:
        return {
            "admin_phone": "",
            "default_quotation_validity_days": None,
            "updated_at": None,
        }
    days = doc.get("default_quotation_validity_days")
    try:
        days = int(days) if days not in (None, "") else None
    except (TypeError, ValueError):
        days = None
    return {
        "admin_phone": str(doc.get("admin_phone") or ""),
        "default_quotation_validity_days": days,
        "updated_at": doc.get("updated_at"),
    }


async def get_global_settings(db) -> dict:
    return public_global_settings(
        await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    )


async def update_global_settings(db, payload: dict, user: dict) -> dict:
    phone = str((payload or {}).get("admin_phone") or "").strip()
    if len(phone) > 40:
        raise HTTPException(400, "Nomor admin terlalu panjang.")
    if phone and not re.fullmatch(r"[0-9+\-\s()]+", phone):
        raise HTTPException(400, "Nomor admin hanya boleh berisi angka dan tanda telepon.")
    days_raw = (payload or {}).get("default_quotation_validity_days")
    days = None
    if days_raw not in (None, ""):
        try:
            days = int(days_raw)
        except (TypeError, ValueError):
            raise HTTPException(400, "Masa berlaku quotation harus bilangan bulat positif.")
        if days < 1 or days > 3650:
            raise HTTPException(400, "Masa berlaku quotation harus antara 1–3650 hari.")
    saved = {
        "key": SETTINGS_KEY,
        "admin_phone": phone,
        "default_quotation_validity_days": days,
        "updated_at": now_iso(),
        "updated_by": actor_snapshot(user),
    }
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    return public_global_settings(saved)


async def resolve_auto_hpp(db, accurate_item_id: str) -> dict:
    """Resolve one exact product independently from authoritative active HPP data."""
    aid = str(accurate_item_id or "").strip()
    identity = f"a:{aid}"
    settings = await get_hpp_settings(db)
    method = settings.get("method") or METHOD_FIFO
    active_version = settings.get("active_version")
    base = {
        "hpp_source": HPP_AUTO,
        "hpp_method": method,
        "hpp_snapshot": None,
        "manual_hpp": None,
        "hpp_active_version_id": active_version,
        "hpp_resolved_at": now_iso(),
        "hpp_status": HPP_MISSING,
        "hpp_message": MSG_HPP_MISSING,
        "item_identity": identity,
    }
    if not active_version:
        return base
    boundary = await query_cost_boundary(db, item_identity_key=identity)
    acquisitions = list(boundary.get("acquisitions") or [])
    if not acquisitions:
        return base
    if method == METHOD_AVG:
        ordered = sorted(
            acquisitions,
            key=lambda row: (
                str(row.get("recognized_at") or ""),
                str(row.get("source_id") or ""),
                str(row.get("source_line_id") or ""),
            ),
        )
        unit = money_round(ordered[-1].get("unit_hpp") or 0)
        if _dec(unit) <= 0:
            return base
        return {
            **base,
            "hpp_snapshot": unit,
            "hpp_status": HPP_OK,
            "hpp_message": None,
            "hpp_method": METHOD_AVG,
        }
    applicable = []
    for acquisition in acquisitions:
        if _dec(acquisition.get("remaining_qty")) <= 0:
            continue
        unit = money_round(acquisition.get("unit_hpp") or 0)
        if _dec(unit) > 0:
            applicable.append(unit)
    if not applicable:
        return base
    distinct = {money_round(unit) for unit in applicable}
    if len(distinct) > 1:
        return {
            **base,
            "hpp_status": HPP_AMBIGUOUS,
            "hpp_message": MSG_FIFO_AMBIGUOUS,
            "hpp_method": METHOD_FIFO,
        }
    return {
        **base,
        "hpp_snapshot": next(iter(distinct)),
        "hpp_status": HPP_OK,
        "hpp_message": None,
        "hpp_method": METHOD_FIFO,
    }


def _master_snapshot(master: dict) -> dict:
    return {
        "accurate_item_id": str(master.get("accurate_item_id") or ""),
        "master_item_id": master.get("id"),
        "display_name": master.get("item_name") or master.get("item_code") or str(master.get("accurate_item_id")),
        "item_code": master.get("item_code"),
        "unit": master.get("unit"),
        "item_type": master.get("item_type"),
        "is_active": master.get("is_active") is not False,
    }


async def _masters_for_ids(db, accurate_item_ids: List[str]) -> Dict[str, dict]:
    ids = [str(value or "").strip() for value in accurate_item_ids]
    if any(not value for value in ids):
        raise HTTPException(400, "Setiap produk harus memiliki accurate_item_id.")
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "Produk yang sama tidak boleh dipilih dua kali.")
    rows = await db.master_items.find(
        {"accurate_item_id": {"$in": ids}}, {"_id": 0}
    ).to_list(len(ids))
    by_id = {str(row.get("accurate_item_id")): row for row in rows}
    missing = [value for value in ids if value not in by_id]
    if missing:
        raise HTTPException(400, f"Produk master tidak ditemukan: {', '.join(missing)}")
    return by_id


def normalize_shared_tiers(
    raw_tiers: Any, *, pricing_mode: str, profit_mode: Optional[str]
) -> Tuple[List[dict], List[str], List[str], List[str]]:
    structural: List[str] = []
    publish_errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(raw_tiers, list) or not raw_tiers:
        return [], ["Minimal satu tier wajib diisi."], publish_errors, warnings
    if len(raw_tiers) > 50:
        return [], ["Terlalu banyak tier (maksimal 50)."], publish_errors, warnings
    normalized: List[dict] = []
    for index, raw in enumerate(raw_tiers):
        if not isinstance(raw, dict):
            structural.append(f"Tier #{index + 1} tidak valid.")
            continue
        try:
            minimum = _parse_positive_int_qty(raw.get("min_qty"), f"Tier #{index + 1} min_qty")
            raw_max = raw.get("max_qty")
            is_open = raw_max is None or raw_max == "" or str(raw_max).lower() in {
                "null", "none", "open", "inf", "∞",
            }
            if is_open:
                maximum = None
                if index != len(raw_tiers) - 1:
                    structural.append(f"Tier #{index + 1}: hanya tier terakhir yang boleh tanpa batas.")
                    continue
            else:
                maximum = _parse_positive_int_qty(raw_max, f"Tier #{index + 1} max_qty")
                if maximum < minimum:
                    structural.append(f"Tier #{index + 1}: max_qty harus >= min_qty.")
                    continue
                if index == len(raw_tiers) - 1:
                    publish_errors.append("Tier terakhir harus tanpa batas (max_qty kosong).")
            row = {"min_qty": minimum, "max_qty": maximum}
            if pricing_mode == MODE_HPP_FORMULA:
                raw_profit = raw.get("profit_value")
                if raw_profit in (None, ""):
                    row["profit_value"] = None
                    publish_errors.append(f"Tier #{index + 1}: nilai keuntungan wajib diisi sebelum Publish.")
                elif profit_mode == PROFIT_FIXED:
                    row["profit_value"] = _parse_positive_money(raw_profit, f"Tier #{index + 1} nilai keuntungan")
                elif profit_mode == PROFIT_PERCENT:
                    row["profit_value"] = _parse_positive_percent(raw_profit, f"Tier #{index + 1} persentase")
                else:
                    structural.append("Metode keuntungan tidak valid.")
                    continue
            normalized.append(row)
        except HTTPException as exc:
            structural.append(str(exc.detail))
    if structural:
        return normalized, structural, publish_errors, warnings
    seen = set()
    for tier in normalized:
        key = (tier["min_qty"], tier["max_qty"])
        if key in seen:
            structural.append(f"Tier duplikat untuk rentang {tier['min_qty']}–{tier['max_qty']}.")
        seen.add(key)
    if structural:
        return normalized, structural, publish_errors, warnings
    if normalized != sorted(normalized, key=lambda tier: tier["min_qty"]):
        structural.append("Tier harus diurutkan menaik berdasarkan min_qty.")
        return normalized, structural, publish_errors, warnings
    for index in range(1, len(normalized)):
        previous = normalized[index - 1]
        current = normalized[index]
        if previous["max_qty"] is None:
            structural.append("Hanya tier terakhir yang boleh tanpa batas.")
            break
        expected = previous["max_qty"] + 1
        if current["min_qty"] <= previous["max_qty"]:
            structural.append(
                f"Tier overlap: min_qty {current['min_qty']} bentrok dengan tier sebelumnya."
            )
        elif current["min_qty"] != expected:
            publish_errors.append(
                f"Tier tidak kontigu: setelah {previous['max_qty']} harus dimulai {expected}, "
                f"bukan {current['min_qty']}."
            )
    return normalized, structural, publish_errors, warnings


def _normalize_price_matrix(
    raw: Any, tier_count: int, product_name: str
) -> Tuple[List[Optional[float]], List[str], List[str]]:
    structural: List[str] = []
    publish_errors: List[str] = []
    if raw is None:
        values: List[Any] = []
    elif not isinstance(raw, list):
        return [], [f"{product_name}: harga tier harus berupa daftar."], publish_errors
    else:
        values = raw
    if len(values) > tier_count:
        structural.append(f"{product_name}: jumlah harga tier melebihi jumlah tier.")
    normalized: List[Optional[float]] = []
    for index in range(tier_count):
        value = values[index] if index < len(values) else None
        if value in (None, ""):
            normalized.append(None)
            publish_errors.append(
                f"{product_name}: harga tier #{index + 1} wajib diisi sebelum Publish."
            )
            continue
        try:
            normalized.append(_parse_positive_money(value, f"{product_name} harga tier #{index + 1}"))
        except HTTPException as exc:
            structural.append(str(exc.detail))
            normalized.append(None)
    return normalized, structural, publish_errors


def _validate_descending_prices(
    prices: List[Optional[float]], product_name: str
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    if any(value is None for value in prices):
        return errors, warnings
    for index in range(1, len(prices)):
        previous = _dec(prices[index - 1])
        current = _dec(prices[index])
        if current > previous:
            errors.append(
                f"{product_name}: harga unit tier lebih besar tidak boleh lebih mahal "
                f"(tier {index}: Rp {prices[index - 1]} → tier {index + 1}: Rp {prices[index]})."
            )
        elif current == previous:
            warnings.append(
                f"{product_name}: tier {index + 1} memiliki harga sama dengan tier sebelumnya."
            )
    return errors, warnings


def _formula_prices(
    hpp: Optional[float], tiers: List[dict], profit_mode: str
) -> List[Optional[float]]:
    if hpp is None or _dec(hpp) <= 0:
        return [None for _ in tiers]
    prices: List[Optional[float]] = []
    for tier in tiers:
        profit = tier.get("profit_value")
        if profit is None:
            prices.append(None)
        elif profit_mode == PROFIT_PERCENT:
            prices.append(compute_unit_price_percent(hpp, profit))
        else:
            prices.append(compute_unit_price_fixed(hpp, profit))
    return prices


def public_version(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "pricing_group_id": doc.get("pricing_group_id"),
        "group_name": doc.get("group_name"),
        "version_number": doc.get("version_number"),
        "status": doc.get("status"),
        "pricing_mode": doc.get("pricing_mode"),
        "hpp_source": doc.get("hpp_source"),
        "profit_mode": doc.get("profit_mode"),
        "tiers": copy.deepcopy(doc.get("tiers") or []),
        "products": copy.deepcopy(doc.get("products") or []),
        "effective_at": doc.get("effective_at"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "created_by": doc.get("created_by"),
        "updated_by": doc.get("updated_by"),
        "published_at": doc.get("published_at"),
        "published_by": doc.get("published_by"),
        "revision": doc.get("revision") or 1,
    }


def public_group(doc: dict, summary_version: Optional[dict] = None) -> dict:
    output = {
        "id": doc.get("id"),
        "public_id": doc.get("public_id"),
        "group_name": doc.get("group_name"),
        "enabled": bool(doc.get("enabled", True)),
        "current_draft_version_id": doc.get("current_draft_version_id"),
        "current_published_version_id": doc.get("current_published_version_id"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "created_by": doc.get("created_by"),
        "updated_by": doc.get("updated_by"),
        "summary_version": public_version(summary_version) if summary_version else None,
    }
    if summary_version:
        if summary_version.get("id") == doc.get("current_draft_version_id"):
            output["current_draft_version_number"] = summary_version.get("version_number")
        if summary_version.get("id") == doc.get("current_published_version_id"):
            output["current_published_version_number"] = summary_version.get("version_number")
    return output


async def ensure_indexes(db) -> None:
    """Create final group indexes; remove obsolete version indexes only while empty."""
    if await db[LEGACY_PRODUCTS].count_documents({}) == 0:
        legacy_indexes = {
            item["name"] for item in await db[LEGACY_PRODUCTS].list_indexes().to_list(None)
        }
        for obsolete in ("id_1", "public_id_1", "accurate_item_id_1"):
            if obsolete in legacy_indexes:
                await db[LEGACY_PRODUCTS].drop_index(obsolete)
    if await db[VERSIONS].count_documents({}) == 0:
        existing = {item["name"] for item in await db[VERSIONS].list_indexes().to_list(None)}
        for obsolete in ("product_pricing_id_1_version_number_1", "uniq_one_draft_per_product"):
            if obsolete in existing:
                await db[VERSIONS].drop_index(obsolete)
    await db[GROUPS].create_index("id", unique=True)
    await db[GROUPS].create_index("public_id", unique=True)
    await db[GROUPS].create_index(
        "active_accurate_item_ids",
        unique=True,
        partialFilterExpression={"enabled": True},
        name="uniq_active_customer_pricing_product",
    )
    await db[VERSIONS].create_index("id", unique=True)
    await db[VERSIONS].create_index(
        [("pricing_group_id", 1), ("version_number", 1)],
        unique=True,
        name="uniq_customer_pricing_group_version",
    )
    await db[VERSIONS].create_index(
        [("pricing_group_id", 1), ("status", 1)],
        unique=True,
        partialFilterExpression={"status": STATUS_DRAFT},
        name="uniq_one_draft_per_pricing_group",
    )


async def _summary_for_group(db, group: dict) -> Optional[dict]:
    version_id = group.get("current_draft_version_id") or group.get("current_published_version_id")
    if not version_id:
        return None
    return await db[VERSIONS].find_one({"id": version_id}, {"_id": 0})


async def list_groups(db, *, q: str = "") -> List[dict]:
    rows = await db[GROUPS].find({}, {"_id": 0}).sort("updated_at", -1).to_list(500)
    qn = str(q or "").strip().lower()
    output = []
    for row in rows:
        summary = await _summary_for_group(db, row)
        if qn:
            names = " ".join(
                product.get("display_name") or "" for product in (summary or {}).get("products", [])
            )
            if qn not in f"{row.get('group_name', '')} {names}".lower():
                continue
        item = public_group(row, summary)
        if row.get("current_published_version_id") and not item.get("current_published_version_number"):
            published = await db[VERSIONS].find_one(
                {"id": row["current_published_version_id"]},
                {"_id": 0, "version_number": 1},
            )
            item["current_published_version_number"] = (published or {}).get("version_number")
        output.append(item)
    return output


async def get_group(db, group_id: str) -> dict:
    group = await db[GROUPS].find_one({"id": group_id}, {"_id": 0})
    if not group:
        raise HTTPException(404, "Kelompok harga tidak ditemukan.")
    item = public_group(group, await _summary_for_group(db, group))
    if group.get("current_published_version_id") and not item.get("current_published_version_number"):
        published = await db[VERSIONS].find_one(
            {"id": group["current_published_version_id"]},
            {"_id": 0, "version_number": 1},
        )
        item["current_published_version_number"] = (published or {}).get("version_number")
    return item


async def create_group(db, payload: dict, user: dict) -> dict:
    name = _clean_group_name((payload or {}).get("group_name"))
    now = now_iso()
    actor = actor_snapshot(user)
    doc = {
        "id": _new_id(),
        "public_id": _new_id(),
        "group_name": name,
        "enabled": True,
        "current_draft_version_id": None,
        "current_published_version_id": None,
        "active_accurate_item_ids": [],
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
        "updated_by": actor,
    }
    await db[GROUPS].insert_one(doc)
    return public_group(doc)


async def patch_group(db, group_id: str, payload: dict, user: dict) -> dict:
    group = await db[GROUPS].find_one({"id": group_id}, {"_id": 0})
    if not group:
        raise HTTPException(404, "Kelompok harga tidak ditemukan.")
    updates: Dict[str, Any] = {
        "updated_at": now_iso(),
        "updated_by": actor_snapshot(user),
    }
    if "enabled" in payload:
        updates["enabled"] = bool(payload.get("enabled"))
    try:
        await db[GROUPS].update_one({"id": group_id}, {"$set": updates})
    except DuplicateKeyError:
        raise HTTPException(409, "Produk kelompok ini sudah digunakan kelompok harga aktif lain.")
    return await get_group(db, group_id)


async def _membership_ids(db, version_id: Optional[str]) -> List[str]:
    if not version_id:
        return []
    version = await db[VERSIONS].find_one({"id": version_id}, {"_id": 0, "products": 1})
    return [product["accurate_item_id"] for product in (version or {}).get("products", [])]


async def _sync_active_membership(db, group_id: str) -> None:
    group = await db[GROUPS].find_one({"id": group_id}, {"_id": 0})
    if not group:
        return
    ids = set(await _membership_ids(db, group.get("current_published_version_id")))
    ids.update(await _membership_ids(db, group.get("current_draft_version_id")))
    try:
        await db[GROUPS].update_one(
            {"id": group_id}, {"$set": {"active_accurate_item_ids": sorted(ids)}}
        )
    except DuplicateKeyError:
        raise HTTPException(409, "Satu atau lebih produk sudah digunakan kelompok harga aktif lain.")


async def _assert_membership_available(db, group_id: str, accurate_item_ids: List[str]) -> None:
    if not accurate_item_ids:
        return
    conflict = await db[GROUPS].find_one(
        {
            "id": {"$ne": group_id},
            "enabled": True,
            "active_accurate_item_ids": {"$in": accurate_item_ids},
        },
        {"_id": 0, "group_name": 1, "active_accurate_item_ids": 1},
    )
    if conflict:
        duplicates = sorted(set(accurate_item_ids) & set(conflict.get("active_accurate_item_ids") or []))
        raise HTTPException(
            409,
            f"Produk {', '.join(duplicates)} sudah digunakan kelompok {conflict.get('group_name')}.",
        )


def _default_version(group: dict, version_number: int, clone: Optional[dict]) -> dict:
    if clone:
        return {
            "group_name": clone.get("group_name") or group.get("group_name"),
            "pricing_mode": clone.get("pricing_mode") or MODE_MANUAL_TIER,
            "hpp_source": clone.get("hpp_source"),
            "profit_mode": clone.get("profit_mode"),
            "tiers": copy.deepcopy(clone.get("tiers") or []),
            "products": copy.deepcopy(clone.get("products") or []),
        }
    return {
        "group_name": group.get("group_name"),
        "pricing_mode": MODE_MANUAL_TIER,
        "hpp_source": None,
        "profit_mode": None,
        "tiers": [],
        "products": [],
    }


async def create_or_get_draft(db, group_id: str, user: dict) -> dict:
    lock = get_named_lock(f"customer_pricing_group:{group_id}")
    async with lock:
        group = await db[GROUPS].find_one({"id": group_id}, {"_id": 0})
        if not group:
            raise HTTPException(404, "Kelompok harga tidak ditemukan.")
        if group.get("current_draft_version_id"):
            current = await db[VERSIONS].find_one(
                {"id": group["current_draft_version_id"], "status": STATUS_DRAFT}, {"_id": 0}
            )
            if current:
                return public_version(current)
        existing = await db[VERSIONS].find_one(
            {"pricing_group_id": group_id, "status": STATUS_DRAFT}, {"_id": 0}
        )
        if existing:
            await db[GROUPS].update_one(
                {"id": group_id}, {"$set": {"current_draft_version_id": existing["id"]}}
            )
            await _sync_active_membership(db, group_id)
            return public_version(existing)
        latest = await db[VERSIONS].find(
            {"pricing_group_id": group_id}, {"_id": 0, "version_number": 1}
        ).sort("version_number", -1).to_list(1)
        version_number = int(latest[0]["version_number"] if latest else 0) + 1
        published = None
        if group.get("current_published_version_id"):
            published = await db[VERSIONS].find_one(
                {"id": group["current_published_version_id"]}, {"_id": 0}
            )
        body = _default_version(group, version_number, published)
        now = now_iso()
        actor = actor_snapshot(user)
        doc = {
            "id": _new_id(),
            "pricing_group_id": group_id,
            **body,
            "version_number": version_number,
            "status": STATUS_DRAFT,
            "revision": 1,
            "effective_at": None,
            "published_at": None,
            "published_by": None,
            "created_at": now,
            "updated_at": now,
            "created_by": actor,
            "updated_by": actor,
        }
        await db[VERSIONS].insert_one(doc)
        await db[GROUPS].update_one(
            {"id": group_id},
            {"$set": {
                "current_draft_version_id": doc["id"],
                "group_name": doc["group_name"],
                "updated_at": now,
                "updated_by": actor,
            }},
        )
        await _sync_active_membership(db, group_id)
        return public_version(doc)


async def list_versions(db, group_id: str) -> List[dict]:
    if not await db[GROUPS].find_one({"id": group_id}, {"_id": 0, "id": 1}):
        raise HTTPException(404, "Kelompok harga tidak ditemukan.")
    rows = await db[VERSIONS].find(
        {"pricing_group_id": group_id}, {"_id": 0}
    ).sort("version_number", -1).to_list(200)
    return [public_version(row) for row in rows]


async def get_version(db, version_id: str) -> dict:
    row = await db[VERSIONS].find_one({"id": version_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Versi harga tidak ditemukan.")
    return public_version(row)


async def _build_version(
    db, current: dict, payload: dict
) -> Tuple[dict, List[str], List[str], List[str]]:
    pricing_mode = payload.get("pricing_mode", current.get("pricing_mode"))
    if pricing_mode not in {MODE_HPP_FORMULA, MODE_MANUAL_TIER}:
        raise HTTPException(400, "Mode harga tidak valid.")
    hpp_source = payload.get("hpp_source", current.get("hpp_source")) if pricing_mode == MODE_HPP_FORMULA else None
    profit_mode = payload.get("profit_mode", current.get("profit_mode")) if pricing_mode == MODE_HPP_FORMULA else None
    if pricing_mode == MODE_HPP_FORMULA and hpp_source not in {HPP_AUTO, HPP_MANUAL}:
        raise HTTPException(400, "Sumber HPP tidak valid.")
    if pricing_mode == MODE_HPP_FORMULA and profit_mode not in {PROFIT_FIXED, PROFIT_PERCENT}:
        raise HTTPException(400, "Metode keuntungan tidak valid.")
    group_name = _clean_group_name(payload.get("group_name", current.get("group_name")))
    tiers, structural, publish_errors, warnings = normalize_shared_tiers(
        payload.get("tiers", current.get("tiers")),
        pricing_mode=pricing_mode,
        profit_mode=profit_mode,
    )
    raw_products = payload.get("products", current.get("products") or [])
    if not isinstance(raw_products, list):
        raise HTTPException(400, "Produk harus berupa daftar.")
    aids = [
        str(item.get("accurate_item_id") or "").strip() if isinstance(item, dict) else ""
        for item in raw_products
    ]
    masters = await _masters_for_ids(db, aids)
    await _assert_membership_available(db, current["pricing_group_id"], aids)
    existing = {
        item.get("accurate_item_id"): item for item in (current.get("products") or [])
    }
    products = []
    for raw, aid in zip(raw_products, aids):
        snapshot = _master_snapshot(masters[aid])
        previous = existing.get(aid, {})
        product = {
            **snapshot,
            "manual_hpp": None,
            "hpp_method": None,
            "hpp_snapshot": None,
            "hpp_active_version_id": None,
            "hpp_resolved_at": None,
            "hpp_status": None,
            "hpp_message": None,
            "tier_prices": [],
        }
        if pricing_mode == MODE_HPP_FORMULA:
            if hpp_source == HPP_MANUAL:
                manual_raw = raw.get("manual_hpp")
                if manual_raw in (None, ""):
                    manual_hpp = None
                    publish_errors.append(
                        f"{snapshot['display_name']}: HPP Manual wajib diisi sebelum Publish."
                    )
                else:
                    try:
                        manual_hpp = _parse_positive_money(
                            manual_raw, f"{snapshot['display_name']} HPP Manual"
                        )
                    except HTTPException as exc:
                        structural.append(str(exc.detail))
                        manual_hpp = None
                product.update({
                    "manual_hpp": manual_hpp,
                    "hpp_snapshot": manual_hpp,
                    "hpp_status": HPP_OK if manual_hpp else HPP_MISSING,
                    "hpp_message": None if manual_hpp else "HPP Manual belum diisi.",
                    "hpp_resolved_at": now_iso() if manual_hpp else None,
                })
            else:
                if current.get("pricing_mode") == MODE_HPP_FORMULA and current.get("hpp_source") == HPP_AUTO:
                    for key in (
                        "hpp_method", "hpp_snapshot", "hpp_active_version_id",
                        "hpp_resolved_at", "hpp_status", "hpp_message",
                    ):
                        product[key] = previous.get(key)
                product["hpp_status"] = product.get("hpp_status") or HPP_MISSING
                product["hpp_message"] = product.get("hpp_message") or MSG_HPP_MISSING
                if product["hpp_status"] != HPP_OK or not product.get("hpp_snapshot"):
                    publish_errors.append(
                        f"{snapshot['display_name']}: {product.get('hpp_message') or MSG_HPP_MISSING}"
                    )
            product["tier_prices"] = _formula_prices(
                product.get("hpp_snapshot"), tiers, profit_mode
            )
        else:
            prices, price_structural, price_publish = _normalize_price_matrix(
                raw.get("tier_prices"), len(tiers), snapshot["display_name"]
            )
            product["tier_prices"] = prices
            structural.extend(price_structural)
            publish_errors.extend(price_publish)
        price_errors, price_warnings = _validate_descending_prices(
            product["tier_prices"], snapshot["display_name"]
        )
        publish_errors.extend(price_errors)
        warnings.extend(price_warnings)
        products.append(product)
    if not products:
        publish_errors.append("Minimal satu produk wajib dipilih sebelum Publish.")
    built = {
        **current,
        "group_name": group_name,
        "pricing_mode": pricing_mode,
        "hpp_source": hpp_source,
        "profit_mode": profit_mode,
        "tiers": tiers,
        "products": products,
    }
    return built, structural, list(dict.fromkeys(publish_errors)), list(dict.fromkeys(warnings))


async def update_draft(db, version_id: str, payload: dict, user: dict) -> dict:
    lock = get_named_lock(f"customer_pricing_version:{version_id}")
    async with lock:
        current = await db[VERSIONS].find_one({"id": version_id}, {"_id": 0})
        if not current:
            raise HTTPException(404, "Versi harga tidak ditemukan.")
        if current.get("status") != STATUS_DRAFT:
            raise HTTPException(409, "Versi Published tidak dapat diubah. Buat Draft baru.")
        try:
            revision = int(payload.get("revision"))
        except (TypeError, ValueError):
            raise HTTPException(409, "Revision wajib diisi.")
        if revision != int(current.get("revision") or 1):
            raise HTTPException(409, "Draft telah berubah. Muat ulang sebelum menyimpan.")
        built, structural, _, _ = await _build_version(db, current, payload)
        if structural:
            raise HTTPException(400, {"message": "Draft tidak valid.", "errors": structural})
        now = now_iso()
        actor = actor_snapshot(user)
        updates = {
            "group_name": built["group_name"],
            "pricing_mode": built["pricing_mode"],
            "hpp_source": built["hpp_source"],
            "profit_mode": built["profit_mode"],
            "tiers": built["tiers"],
            "products": built["products"],
            "revision": revision + 1,
            "updated_at": now,
            "updated_by": actor,
        }
        result = await db[VERSIONS].update_one(
            {"id": version_id, "status": STATUS_DRAFT, "revision": revision},
            {"$set": updates},
        )
        if result.modified_count != 1:
            raise HTTPException(409, "Draft telah berubah. Muat ulang sebelum menyimpan.")
        await db[GROUPS].update_one(
            {"id": current["pricing_group_id"]},
            {"$set": {
                "group_name": built["group_name"],
                "updated_at": now,
                "updated_by": actor,
            }},
        )
        await _sync_active_membership(db, current["pricing_group_id"])
        saved = await db[VERSIONS].find_one({"id": version_id}, {"_id": 0})
        return public_version(saved)


async def refresh_auto_hpp(db, version_id: str, user: dict) -> dict:
    lock = get_named_lock(f"customer_pricing_version:{version_id}")
    async with lock:
        current = await db[VERSIONS].find_one({"id": version_id}, {"_id": 0})
        if not current:
            raise HTTPException(404, "Versi harga tidak ditemukan.")
        if current.get("status") != STATUS_DRAFT:
            raise HTTPException(409, "Versi Published tidak dapat diubah.")
        if current.get("pricing_mode") != MODE_HPP_FORMULA or current.get("hpp_source") != HPP_AUTO:
            raise HTTPException(400, "Refresh HPP hanya untuk Formula HPP otomatis.")
        products = []
        for product in current.get("products") or []:
            resolved = await resolve_auto_hpp(db, product["accurate_item_id"])
            updated = {**product, **resolved}
            updated["tier_prices"] = _formula_prices(
                updated.get("hpp_snapshot"),
                current.get("tiers") or [],
                current.get("profit_mode"),
            )
            products.append(updated)
        now = now_iso()
        revision = int(current.get("revision") or 1)
        await db[VERSIONS].update_one(
            {"id": version_id, "status": STATUS_DRAFT, "revision": revision},
            {"$set": {
                "products": products,
                "revision": revision + 1,
                "updated_at": now,
                "updated_by": actor_snapshot(user),
            }},
        )
        saved = await db[VERSIONS].find_one({"id": version_id}, {"_id": 0})
        return public_version(saved)


async def preview_version(db, version_id: str, payload: Optional[dict] = None) -> dict:
    current = await db[VERSIONS].find_one({"id": version_id}, {"_id": 0})
    if not current:
        raise HTTPException(404, "Versi harga tidak ditemukan.")
    if payload:
        built, structural, publish_errors, warnings = await _build_version(db, current, payload)
    else:
        built, structural, publish_errors, warnings = await _build_version(
            db, current, public_version(current)
        )
    return {
        "valid": not structural and not publish_errors,
        "errors": structural + publish_errors,
        "warnings": warnings,
        "group_name": built["group_name"],
        "pricing_mode": built["pricing_mode"],
        "hpp_source": built["hpp_source"],
        "profit_mode": built["profit_mode"],
        "tiers": copy.deepcopy(built["tiers"]),
        "products": copy.deepcopy(built["products"]),
    }


async def publish_version(db, version_id: str, payload: dict, user: dict) -> dict:
    current = await db[VERSIONS].find_one({"id": version_id}, {"_id": 0})
    if not current:
        raise HTTPException(404, "Versi harga tidak ditemukan.")
    group_id = current["pricing_group_id"]
    lock = get_named_lock(f"customer_pricing_group:{group_id}")
    async with lock:
        current = await db[VERSIONS].find_one({"id": version_id}, {"_id": 0})
        if current.get("status") != STATUS_DRAFT:
            raise HTTPException(409, "Hanya Draft yang dapat di-Publish.")
        try:
            revision = int(payload.get("revision"))
        except (TypeError, ValueError):
            raise HTTPException(409, "Revision wajib diisi.")
        if revision != int(current.get("revision") or 1):
            raise HTTPException(409, "Draft telah berubah. Muat ulang sebelum Publish.")
        built, structural, publish_errors, warnings = await _build_version(
            db, current, public_version(current)
        )
        errors = structural + publish_errors
        inactive = [
            product["display_name"] for product in built["products"] if not product.get("is_active")
        ]
        if inactive:
            errors.append(f"Produk master nonaktif: {', '.join(inactive)}")
        if errors:
            raise HTTPException(
                400,
                {"message": "Publish diblokir karena konfigurasi belum valid.", "errors": errors},
            )
        now = now_iso()
        actor = actor_snapshot(user)
        updates = {
            "group_name": built["group_name"],
            "pricing_mode": built["pricing_mode"],
            "hpp_source": built["hpp_source"],
            "profit_mode": built["profit_mode"],
            "tiers": built["tiers"],
            "products": built["products"],
            "status": STATUS_PUBLISHED,
            "effective_at": now,
            "published_at": now,
            "published_by": actor,
            "updated_at": now,
            "updated_by": actor,
            "revision": revision + 1,
        }
        result = await db[VERSIONS].update_one(
            {"id": version_id, "status": STATUS_DRAFT, "revision": revision},
            {"$set": updates},
        )
        if result.modified_count != 1:
            raise HTTPException(409, "Draft telah berubah. Muat ulang sebelum Publish.")
        await db[GROUPS].update_one(
            {"id": group_id},
            {"$set": {
                "group_name": built["group_name"],
                "current_published_version_id": version_id,
                "current_draft_version_id": None,
                "updated_at": now,
                "updated_by": actor,
            }},
        )
        await _sync_active_membership(db, group_id)
        published = await db[VERSIONS].find_one({"id": version_id}, {"_id": 0})
        group = await db[GROUPS].find_one({"id": group_id}, {"_id": 0})
        return {
            "group": public_group(group, published),
            "version": public_version(published),
            "warnings": warnings,
        }
