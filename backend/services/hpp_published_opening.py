"""Published Customer Pricing -> one-time inventory HPP opening.

Preview is pure read. Confirmation re-reads the same published pointers,
product snapshots, and Stock V2 states under the cutover database claim before
delegating to the existing moving-average cutover engine.

Customer Pricing remains immutable: this module never writes its collections.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.customer_pricing import (
    GROUPS as PRICING_GROUPS,
    HPP_MANUAL,
    MODE_HPP_FORMULA,
    STATUS_PUBLISHED,
    VERSIONS as PRICING_VERSIONS,
)
from services.hpp_cutover import confirm_cutover, get_active_cutover
from services.hpp_valuation import METHOD_AVG, RECOG_GR, get_hpp_settings
from services.inventory_accounting import ITEM_TYPE_SERVICE
from services.product_stock_state import STATUS_INITIALIZED
from services.purchase_common import money_round, qty_round

SOURCE = "PUBLISHED_CUSTOMER_PRICING_OPENING"

STATUS_READY = "SIAP"
STATUS_MISSING_PUBLISHED = "BELUM ADA HPP PUBLISHED"
STATUS_STOCK_PROBLEM = "STOCK BERMASALAH"
STATUS_INVALID_HPP = "DATA HPP TIDAK VALID"


def _decimal(value: Any) -> Optional[Decimal]:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _preview_fingerprint(payload: dict) -> str:
    canonical = {
        "active_version": payload.get("active_version"),
        "active_cutover_id": payload.get("active_cutover_id"),
        "method": payload.get("method"),
        "recognition_point": payload.get("recognition_point"),
        "rows": [
            {
                "master_item_id": row.get("master_item_id"),
                "accurate_item_id": row.get("accurate_item_id"),
                "stock_fisik": row.get("stock_fisik"),
                "base_uom": row.get("base_uom"),
                "published_pricing_group_id": row.get("published_pricing_group_id"),
                "published_pricing_version_id": row.get("published_pricing_version_id"),
                "published_version_number": row.get("published_version_number"),
                "published_revision": row.get("published_revision"),
                "published_hpp": row.get("published_hpp"),
                "status": row.get("status"),
                "blocking": row.get("blocking"),
            }
            for row in payload.get("rows") or []
        ],
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


async def _published_candidates(db) -> Dict[str, List[dict]]:
    """Resolve only each enabled group's current published pointer."""
    groups = await db[PRICING_GROUPS].find(
        {
            "enabled": {"$ne": False},
            "current_published_version_id": {"$type": "string"},
        },
        {
            "_id": 0,
            "id": 1,
            "group_name": 1,
            "current_published_version_id": 1,
        },
    ).to_list(5000)
    version_ids = [
        str(group.get("current_published_version_id") or "")
        for group in groups
        if group.get("current_published_version_id")
    ]
    versions = await db[PRICING_VERSIONS].find(
        {
            "id": {"$in": version_ids},
            "status": STATUS_PUBLISHED,
        },
        {"_id": 0},
    ).to_list(len(version_ids) or 1)
    versions_by_id = {str(version.get("id")): version for version in versions}
    candidates: Dict[str, List[dict]] = {}
    for group in groups:
        version_id = str(group.get("current_published_version_id") or "")
        version = versions_by_id.get(version_id)
        if not version:
            continue
        for product in version.get("products") or []:
            aid = str(product.get("accurate_item_id") or "").strip()
            if not aid:
                continue
            effective_hpp = None
            source_valid = (
                version.get("pricing_mode") == MODE_HPP_FORMULA
                and version.get("hpp_source") == HPP_MANUAL
            )
            if source_valid:
                parsed = _decimal(product.get("hpp_snapshot"))
                if parsed is not None and parsed > 0:
                    effective_hpp = money_round(parsed)
            candidates.setdefault(aid, []).append({
                "pricing_group_id": group.get("id"),
                "group_name": group.get("group_name"),
                "version_id": version.get("id"),
                "version_number": version.get("version_number"),
                "revision": version.get("revision") or 1,
                "pricing_mode": version.get("pricing_mode"),
                "hpp_source": version.get("hpp_source"),
                "published_at": version.get("published_at") or version.get("effective_at"),
                "published_master_item_id": product.get("master_item_id"),
                "published_hpp": effective_hpp,
                "source_valid": source_valid,
            })
    return candidates


def _stock_snapshot(master: dict, state: Optional[dict]) -> dict:
    if not state:
        return {
            "valid": False,
            "stock_fisik": None,
            "base_uom": master.get("unit") or "",
            "reason": "Stock V2 belum diinisialisasi.",
        }
    raw = _decimal(state.get("total_qty"))
    if (
        state.get("status") != STATUS_INITIALIZED
        or state.get("initialized") is not True
        or state.get("pending_event")
        or raw is None
        or raw < 0
    ):
        return {
            "valid": False,
            "stock_fisik": float(raw) if raw is not None else None,
            "base_uom": state.get("base_uom") or master.get("unit") or "",
            "reason": (
                "Stock Fisik negatif."
                if raw is not None and raw < 0
                else "Stock V2 tidak siap direkonsiliasi."
            ),
        }
    return {
        "valid": True,
        "stock_fisik": qty_round(raw),
        "base_uom": state.get("base_uom") or master.get("unit") or "",
        "reason": None,
    }


async def build_preview(db) -> dict:
    settings = await get_hpp_settings(db)
    active_cutover = await get_active_cutover(db)
    already_active = bool(settings.get("active_version") or active_cutover)
    masters = await db.master_items.find(
        {
            "is_active": {"$ne": False},
            "item_type": {"$ne": ITEM_TYPE_SERVICE},
        },
        {
            "_id": 0,
            "id": 1,
            "accurate_item_id": 1,
            "item_code": 1,
            "item_name": 1,
            "unit": 1,
        },
    ).sort([("item_code", 1), ("item_name", 1)]).to_list(20000)
    states = await db.product_stock_state.find({}, {"_id": 0}).to_list(20000)
    states_by_mid = {
        str(state.get("master_item_id") or ""): state
        for state in states
        if state.get("master_item_id")
    }
    published = await _published_candidates(db)
    rows: List[dict] = []
    for master in masters:
        mid = str(master.get("id") or "").strip()
        aid = str(master.get("accurate_item_id") or "").strip()
        stock = _stock_snapshot(master, states_by_mid.get(mid))
        options = published.get(aid) or []
        candidate = options[0] if len(options) == 1 else None
        published_hpp = candidate.get("published_hpp") if candidate else None
        status = STATUS_READY
        message = None
        blocking = False
        if not stock["valid"]:
            status = STATUS_STOCK_PROBLEM
            message = stock["reason"]
            blocking = True
        elif len(options) > 1:
            status = STATUS_INVALID_HPP
            message = "Produk memiliki lebih dari satu versi Published efektif."
            blocking = True
        elif not candidate:
            status = STATUS_MISSING_PUBLISHED
            message = "Belum ada HPP dari versi Published."
            blocking = bool((stock["stock_fisik"] or 0) > 0)
        elif (
            not candidate.get("source_valid")
            or candidate.get("published_master_item_id") not in (None, "", mid)
            or published_hpp is None
        ):
            status = STATUS_INVALID_HPP
            message = (
                "Published AUTO_HPP tidak dapat menjadi sumber opening."
                if candidate.get("hpp_source") != HPP_MANUAL
                else "Data HPP Published tidak valid atau mapping produk berubah."
            )
            blocking = True
            published_hpp = None
        opening_value = None
        if status == STATUS_READY:
            opening_value = money_round(
                Decimal(str(stock["stock_fisik"])) * Decimal(str(published_hpp))
            )
        rows.append({
            "master_item_id": mid,
            "accurate_item_id": aid,
            "item_code": master.get("item_code") or "",
            "item_name": master.get("item_name") or master.get("item_code") or aid or mid,
            "unit": stock["base_uom"],
            "stock_fisik": stock["stock_fisik"],
            "published_hpp": published_hpp,
            "opening_hpp": published_hpp if status == STATUS_READY else None,
            "opening_value": opening_value,
            "status": status,
            "message": message,
            "blocking": blocking,
            "published_pricing_group_id": candidate.get("pricing_group_id") if candidate else None,
            "published_group_name": candidate.get("group_name") if candidate else None,
            "published_pricing_version_id": candidate.get("version_id") if candidate else None,
            "published_version_number": candidate.get("version_number") if candidate else None,
            "published_revision": candidate.get("revision") if candidate else None,
            "published_at": candidate.get("published_at") if candidate else None,
        })
    ready = [row for row in rows if row["status"] == STATUS_READY]
    missing = [row for row in rows if row["status"] == STATUS_MISSING_PUBLISHED]
    problems = [
        row for row in rows
        if row["status"] in {STATUS_STOCK_PROBLEM, STATUS_INVALID_HPP}
    ]
    blocking = [row for row in rows if row["blocking"]]
    payload = {
        "source": SOURCE,
        "already_active": already_active,
        "active_version": settings.get("active_version"),
        "active_cutover_id": (active_cutover or {}).get("cutover_id"),
        "method": settings.get("method"),
        "recognition_point": settings.get("recognition_point"),
        "summary": {
            "total_items": len(rows),
            "ready_items": len(ready),
            "missing_published_items": len(missing),
            "problem_items": len(problems),
            "blocking_items": len(blocking),
            "total_opening_value": money_round(sum(
                Decimal(str(row.get("opening_value") or 0))
                for row in ready
            )),
        },
        "rows": rows,
        "can_confirm": (
            not already_active
            and bool(ready)
            and not blocking
        ),
        "blocking_message": (
            "HPP Persediaan sudah diaktifkan."
            if already_active
            else (
                "Masih ada barang dengan Stock Fisik yang belum memiliki HPP Published. "
                "Lengkapi dan Publish Harga Customer terlebih dahulu."
                if blocking
                else None
            )
        ),
    }
    payload["preview_token"] = _preview_fingerprint(payload)
    return payload


async def confirm_published_opening(
    db,
    payload: dict,
    user: Optional[dict] = None,
) -> dict:
    request_id = str((payload or {}).get("client_request_id") or "").strip()
    preview_token = str((payload or {}).get("preview_token") or "").strip()
    if not request_id:
        raise HTTPException(400, "client_request_id wajib diisi.")
    if not preview_token:
        raise HTTPException(400, "preview_token wajib diisi.")
    existing = await db.hpp_valuation_cutovers.find_one(
        {"client_request_id": request_id},
        {"_id": 0},
    )
    if existing and existing.get("status") == "ACTIVE":
        return {
            "cutover": existing,
            "settings": await get_hpp_settings(db),
            "idempotent": True,
        }
    initial = await build_preview(db)
    if initial["already_active"]:
        raise HTTPException(409, {
            "code": "HPP_ALREADY_ACTIVE",
            "message": "HPP Persediaan sudah diaktifkan.",
        })
    if initial["preview_token"] != preview_token:
        raise HTTPException(409, {
            "code": "STALE_PREVIEW",
            "message": "Data Stock Fisik atau Harga Customer Published berubah. Muat ulang preview.",
        })
    if not initial["can_confirm"]:
        raise HTTPException(409, {
            "code": "OPENING_BLOCKED",
            "message": initial.get("blocking_message") or "HPP awal belum dapat dikonfirmasi.",
        })
    ready = [row for row in initial["rows"] if row["status"] == STATUS_READY]

    async def validate_after_claim():
        current = await build_preview(db)
        if current["preview_token"] != preview_token:
            raise HTTPException(409, {
                "code": "STALE_PREVIEW",
                "message": "Data Stock Fisik atau Harga Customer Published berubah. Muat ulang preview.",
            })

    result = await confirm_cutover(
        db,
        {
            "client_request_id": request_id,
            "expected_method": initial["method"],
            "expected_recognition_point": initial["recognition_point"],
            "expected_active_version": initial["active_version"],
            "acknowledge_settings_change": True,
            "source": SOURCE,
            "lines": [
                {
                    "master_item_id": row["master_item_id"],
                    "accurate_item_id": row["accurate_item_id"],
                    "item_code": row["item_code"],
                    "item_name": row["item_name"],
                    "opening_unit_cost": row["opening_hpp"],
                    "base_uom": row["unit"],
                    "expected_stock_fisik": row["stock_fisik"],
                    "provenance_source": SOURCE,
                    "published_pricing_group_id": row["published_pricing_group_id"],
                    "published_pricing_version_id": row["published_pricing_version_id"],
                    "published_version_number": row["published_version_number"],
                    "published_revision": row["published_revision"],
                    "published_hpp_snapshot": row["published_hpp"],
                }
                for row in ready
            ],
        },
        user,
        source_validator=validate_after_claim,
    )
    return {
        **result,
        "source": SOURCE,
        "idempotent": bool(result.get("idempotent")),
    }
