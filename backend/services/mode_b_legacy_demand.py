"""Mode B legacy demand + branch-scoped historical delivery baseline.

Owner policy (explicit settings — never invent):
- Historical orders may attribute demand to one configured org branch (e.g. YGY)
  when they carry listed Accurate digit branch ids (e.g. "50") or, when enabled,
  a missing branch_id/cabang_id — but ONLY before historical_cutoff_at.
- Orders at/after the cutoff must match the org UUID exactly (fail-closed).
- Never rewrite orders.branch_id. Never treat Accurate "50" as a UUID.
- Delivery baseline is proven-shipped only; never sales_deliveries.status alone.
- Baseline is branch-scoped and must not double-count ledger SALES_DELIVERY.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

SETTINGS_KEY = "mode_b_legacy_demand"


class ModeBLegacyDemandError(Exception):
    """Configuration or cutover blocked."""

    def __init__(self, message: str, *, details: Optional[dict] = None):
        super().__init__(message)
        self.details = details or {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_settings() -> Dict[str, Any]:
    return {
        "key": SETTINGS_KEY,
        "enabled": False,
        "org_branch_id": "",
        "historical_cutoff_at": None,
        "accurate_branch_ids": [],
        "include_null_branch": False,
        "delivery_cutover": {
            "initialized": False,
            "cutover_at": None,
            "branch_id": None,
            "baseline_basis": None,
            "baseline_by_master_item_id": {},
            "baseline_by_order_line": {},
            "order_count": 0,
            "line_count": 0,
            "unresolved_count": 0,
            "unresolved": [],
            "provenance": None,
        },
        "updated_at": None,
        "updated_by": None,
    }


def _as_str_list(raw: Any) -> List[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[str] = []
    seen = set()
    for item in raw:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def sanitize_settings(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = default_settings()
    if not isinstance(doc, dict):
        return base
    cut_base = dict(base["delivery_cutover"])
    raw_cut = doc.get("delivery_cutover") if isinstance(doc.get("delivery_cutover"), dict) else {}
    cut = {**cut_base, **{k: raw_cut.get(k, cut_base[k]) for k in cut_base}}
    cut["baseline_by_master_item_id"] = {
        str(k): float(v)
        for k, v in dict(raw_cut.get("baseline_by_master_item_id") or {}).items()
    }
    cut["baseline_by_order_line"] = {
        str(k): float(v)
        for k, v in dict(raw_cut.get("baseline_by_order_line") or {}).items()
    }
    cut["unresolved"] = list(raw_cut.get("unresolved") or [])[:100]
    cut["unresolved_count"] = int(raw_cut.get("unresolved_count") or len(cut["unresolved"]) or 0)
    cut["initialized"] = bool(raw_cut.get("initialized"))
    return {
        "key": SETTINGS_KEY,
        "enabled": bool(doc.get("enabled")) if doc.get("enabled") is not None else False,
        "org_branch_id": str(doc.get("org_branch_id") or "").strip(),
        "historical_cutoff_at": (
            str(doc.get("historical_cutoff_at")).strip()
            if doc.get("historical_cutoff_at") not in (None, "")
            else None
        ),
        "accurate_branch_ids": _as_str_list(doc.get("accurate_branch_ids")),
        "include_null_branch": bool(doc.get("include_null_branch")),
        "delivery_cutover": cut,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


async def get_mode_b_legacy_demand_settings(db) -> Dict[str, Any]:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return sanitize_settings(doc)


def _actor_info(user: Optional[dict]) -> Optional[dict]:
    if not isinstance(user, dict):
        return None
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "email": user.get("email") or "",
        "name": user.get("name") or user.get("email") or "user",
        "role": user.get("role") or "",
    }


async def configure_mode_b_legacy_demand(
    db,
    *,
    enabled: bool,
    org_branch_id: str,
    historical_cutoff_at: str,
    accurate_branch_ids: Optional[Iterable[str]] = None,
    include_null_branch: bool = False,
    user: Optional[dict] = None,
) -> Dict[str, Any]:
    """Upsert policy. Does not initialize delivery baseline and never rewrites orders."""
    org = str(org_branch_id or "").strip()
    cutoff = str(historical_cutoff_at or "").strip()
    if enabled:
        if not org:
            raise ModeBLegacyDemandError(
                "org_branch_id wajib diisi ketika Mode B legacy demand diaktifkan."
            )
        if not cutoff or parse_iso_datetime(cutoff) is None:
            raise ModeBLegacyDemandError(
                "historical_cutoff_at ISO wajib dan harus dapat di-parse "
                "(batas historis eksplisit — fail-closed tanpa cutoff)."
            )
    current = await get_mode_b_legacy_demand_settings(db)
    stamp = now_iso()
    saved = {
        "key": SETTINGS_KEY,
        "enabled": bool(enabled),
        "org_branch_id": org if enabled else (org or current.get("org_branch_id") or ""),
        "historical_cutoff_at": cutoff if enabled else (
            cutoff or current.get("historical_cutoff_at")
        ),
        "accurate_branch_ids": _as_str_list(accurate_branch_ids)
        if accurate_branch_ids is not None
        else list(current.get("accurate_branch_ids") or []),
        "include_null_branch": bool(include_null_branch),
        "delivery_cutover": current.get("delivery_cutover") or default_settings()["delivery_cutover"],
        "updated_at": stamp,
        "updated_by": _actor_info(user),
    }
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    return sanitize_settings(saved)


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def order_branch_raw(order: Optional[dict]) -> str:
    if not isinstance(order, dict):
        return ""
    return str(order.get("branch_id") or order.get("cabang_id") or "").strip()


def order_is_historical(order: dict, cutoff_at: Optional[str]) -> bool:
    """Fail-closed: missing/unparseable created_at or cutoff → not historical."""
    cutoff = parse_iso_datetime(cutoff_at)
    if cutoff is None:
        return False
    created = parse_iso_datetime(order.get("created_at") or order.get("imported_at"))
    if created is None:
        return False
    return created < cutoff


def order_attributed_to_branch(
    order: Optional[dict],
    target_branch_id: Optional[str],
    settings: Optional[dict],
) -> bool:
    """Whether this order contributes demand for target org branch.

    Exact UUID/cabang match always counts. Legacy Accurate digit / null-branch
    attribution applies only when settings.enabled, target == org_branch_id,
    order is historical vs cutoff, and policy lists allow the raw id.
    """
    if not isinstance(order, dict):
        return False
    target = str(target_branch_id or "").strip()
    if not target:
        return False
    raw = order_branch_raw(order)
    if raw == target:
        return True
    cfg = sanitize_settings(settings if isinstance(settings, dict) else None)
    if not cfg.get("enabled"):
        return False
    if str(cfg.get("org_branch_id") or "").strip() != target:
        return False
    if not order_is_historical(order, cfg.get("historical_cutoff_at")):
        return False
    accurate_ids = {str(x) for x in (cfg.get("accurate_branch_ids") or [])}
    if raw and raw.isdigit() and raw in accurate_ids:
        return True
    if not raw and cfg.get("include_null_branch"):
        return True
    return False


def filter_orders_for_branch(
    orders: Iterable[dict],
    branch_id: Optional[str],
    settings: Optional[dict] = None,
) -> List[dict]:
    wanted = str(branch_id or "").strip()
    if not wanted:
        return []
    return [
        order for order in orders
        if order_attributed_to_branch(order, wanted, settings)
    ]


def mongo_order_branch_clauses(
    branch_id: Optional[str],
    settings: Optional[dict] = None,
) -> List[dict]:
    """Broad fetch clauses; Python filter remains authoritative."""
    wanted = str(branch_id or "").strip()
    if not wanted:
        return [{"_id": {"$exists": False}}]
    clauses: List[dict] = [
        {"branch_id": wanted},
        {"cabang_id": wanted},
    ]
    cfg = sanitize_settings(settings if isinstance(settings, dict) else None)
    if (
        cfg.get("enabled")
        and str(cfg.get("org_branch_id") or "").strip() == wanted
        and cfg.get("historical_cutoff_at")
    ):
        for aid in cfg.get("accurate_branch_ids") or []:
            if str(aid).strip().isdigit():
                clauses.append({"branch_id": str(aid).strip()})
        if cfg.get("include_null_branch"):
            clauses.append({
                "$and": [
                    {"$or": [
                        {"branch_id": {"$exists": False}},
                        {"branch_id": None},
                        {"branch_id": ""},
                    ]},
                    {"$or": [
                        {"cabang_id": {"$exists": False}},
                        {"cabang_id": None},
                        {"cabang_id": ""},
                    ]},
                ]
            })
    return clauses


def legacy_policy_active_for_branch(
    settings: Optional[dict],
    branch_id: Optional[str],
) -> bool:
    cfg = sanitize_settings(settings if isinstance(settings, dict) else None)
    wanted = str(branch_id or "").strip()
    return bool(
        cfg.get("enabled")
        and wanted
        and str(cfg.get("org_branch_id") or "").strip() == wanted
        and cfg.get("historical_cutoff_at")
        and parse_iso_datetime(cfg.get("historical_cutoff_at")) is not None
    )


def delivery_baseline_for_branch(
    settings: Optional[dict],
    branch_id: Optional[str],
) -> Dict[str, float]:
    """Item-level proven-shipped baseline when initialized for this branch."""
    cfg = sanitize_settings(settings if isinstance(settings, dict) else None)
    cut = cfg.get("delivery_cutover") or {}
    wanted = str(branch_id or "").strip()
    if not cut.get("initialized"):
        return {}
    if str(cut.get("branch_id") or "").strip() != wanted:
        return {}
    return {
        str(k): float(v)
        for k, v in dict(cut.get("baseline_by_master_item_id") or {}).items()
    }


def delivery_baseline_line_qty(
    settings: Optional[dict],
    *,
    branch_id: Optional[str],
    order_id: str,
    line_idx: int,
) -> float:
    cfg = sanitize_settings(settings if isinstance(settings, dict) else None)
    cut = cfg.get("delivery_cutover") or {}
    wanted = str(branch_id or "").strip()
    if not cut.get("initialized"):
        return 0.0
    if str(cut.get("branch_id") or "").strip() != wanted:
        return 0.0
    from services.r2_go_live import order_line_baseline_key

    key = order_line_baseline_key(order_id, line_idx)
    return float((cut.get("baseline_by_order_line") or {}).get(key, 0) or 0)


def historical_delivery_baseline_ready(
    settings: Optional[dict],
    branch_id: Optional[str],
) -> bool:
    """True when legacy policy is off for branch, or baseline is initialized."""
    if not legacy_policy_active_for_branch(settings, branch_id):
        return True
    cfg = sanitize_settings(settings if isinstance(settings, dict) else None)
    cut = cfg.get("delivery_cutover") or {}
    return bool(
        cut.get("initialized")
        and str(cut.get("branch_id") or "").strip() == str(branch_id or "").strip()
    )


async def preview_mode_b_delivery_cutover(
    db,
    *,
    branch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Read-only proven-shipped baseline for attributed historical (+ UUID) orders."""
    from services.physical_stock import _unique_accurate_owners
    from services.r2_go_live import (
        _build_delivery_baseline,
        _r2_booked_delivery_identities,
    )

    settings = await get_mode_b_legacy_demand_settings(db)
    wanted = str(branch_id or settings.get("org_branch_id") or "").strip()
    if not wanted:
        raise ModeBLegacyDemandError("branch_id / org_branch_id wajib untuk preview cutover.")
    if not legacy_policy_active_for_branch(settings, wanted):
        raise ModeBLegacyDemandError(
            "Mode B legacy demand belum dikonfigurasi untuk cabang ini "
            "(enabled + org_branch_id + historical_cutoff_at)."
        )

    owners = await _unique_accurate_owners(db)
    clauses = mongo_order_branch_clauses(wanted, settings)
    orders_raw = await db.orders.find({"$or": clauses}, {"_id": 0}).to_list(50000)
    orders = filter_orders_for_branch(orders_raw, wanted, settings)
    sales_deliveries = await db.sales_deliveries.find({}, {"_id": 0}).to_list(100000)
    exclude = await _r2_booked_delivery_identities(db)
    # Also exclude standalone Mode B ledger-posted confirmation identities.
    from services.physical_stock import SALES_DELIVERY_CONFIRMATIONS

    conf_rows = await db[SALES_DELIVERY_CONFIRMATIONS].find(
        {"reversed": {"$ne": True}},
        {"_id": 0, "id": 1, "sales_delivery_id": 1},
    ).to_list(100000)
    for row in conf_rows:
        for key in ("id", "sales_delivery_id"):
            token = str(row.get(key) or "").strip()
            if token:
                exclude.add(token)

    by_item, by_line, order_count, line_count, ambiguous = _build_delivery_baseline(
        orders, sales_deliveries, owners, exclude_identities=exclude,
    )
    existing = settings.get("delivery_cutover") or {}
    return {
        "branch_id": wanted,
        "already_initialized": bool(existing.get("initialized"))
        and str(existing.get("branch_id") or "") == wanted,
        "existing_cutover_at": existing.get("cutover_at"),
        "order_count": order_count,
        "line_count": line_count,
        "item_count": len(by_item),
        "baseline_qty_total": sum(by_item.values()),
        "baseline_by_master_item_id": by_item,
        "baseline_by_order_line": by_line,
        "baseline_basis": "proven_shipped",
        "unresolved_count": len(ambiguous),
        "unresolved": ambiguous[:100],
        "blocked": bool(ambiguous),
        "attributed_order_count": len(orders),
        "legacy_policy": {
            "enabled": settings.get("enabled"),
            "org_branch_id": settings.get("org_branch_id"),
            "historical_cutoff_at": settings.get("historical_cutoff_at"),
            "accurate_branch_ids": settings.get("accurate_branch_ids"),
            "include_null_branch": settings.get("include_null_branch"),
        },
    }


async def initialize_mode_b_delivery_cutover(
    db,
    *,
    branch_id: Optional[str] = None,
    actor: Optional[dict] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Persist branch-scoped proven-shipped baseline. Lab/Owner use only — no prod call here."""
    settings = await get_mode_b_legacy_demand_settings(db)
    wanted = str(branch_id or settings.get("org_branch_id") or "").strip()
    cut = settings.get("delivery_cutover") or {}
    if cut.get("initialized") and str(cut.get("branch_id") or "") == wanted and not force:
        return {
            "initialized": True,
            "replayed": True,
            "branch_id": wanted,
            "cutover_at": cut.get("cutover_at"),
            "order_count": cut.get("order_count") or 0,
            "line_count": cut.get("line_count") or 0,
            "item_count": len(cut.get("baseline_by_master_item_id") or {}),
            "baseline_qty_total": sum((cut.get("baseline_by_master_item_id") or {}).values()),
            "baseline_basis": cut.get("baseline_basis") or "proven_shipped",
            "unresolved_count": cut.get("unresolved_count") or 0,
        }

    if force:
        from services.physical_stock import LEDGER_COLLECTION, MOVEMENT_SALES_DELIVERY

        booked = await db[LEDGER_COLLECTION].count_documents({
            "movement_type": MOVEMENT_SALES_DELIVERY,
            "branch_id": wanted,
        })
        if booked:
            raise ModeBLegacyDemandError(
                "Force re-init cutover ditolak: sudah ada SALES_DELIVERY ledger "
                "pada cabang ini (baseline + ledger akan double-count).",
                details={"branch_id": wanted, "sales_delivery_ledger_count": int(booked)},
            )

    preview = await preview_mode_b_delivery_cutover(db, branch_id=wanted)
    if preview.get("blocked"):
        raise ModeBLegacyDemandError(
            "Cutover baseline Mode B ditolak: ada pengiriman historis yang tidak "
            "dapat diatribusikan ke baris pesanan secara aman.",
            details={"unresolved": preview.get("unresolved") or []},
        )

    stamp = now_iso()
    delivery_cutover = {
        "initialized": True,
        "cutover_at": stamp,
        "branch_id": wanted,
        "baseline_basis": "proven_shipped",
        "baseline_by_master_item_id": preview["baseline_by_master_item_id"],
        "baseline_by_order_line": preview["baseline_by_order_line"],
        "order_count": preview["order_count"],
        "line_count": preview["line_count"],
        "unresolved_count": 0,
        "unresolved": [],
        "provenance": {
            "source": "mode_b_legacy_demand.initialize_mode_b_delivery_cutover",
            "attributed_order_count": preview.get("attributed_order_count"),
            "historical_cutoff_at": settings.get("historical_cutoff_at"),
            "accurate_branch_ids": list(settings.get("accurate_branch_ids") or []),
            "include_null_branch": bool(settings.get("include_null_branch")),
            "basis": "proven_shipped_confirmations_or_shipment_terkirim",
            "excludes": "sales_deliveries.status_alone; ledger_booked_identities",
        },
    }
    saved = {
        "key": SETTINGS_KEY,
        "enabled": bool(settings.get("enabled")),
        "org_branch_id": settings.get("org_branch_id"),
        "historical_cutoff_at": settings.get("historical_cutoff_at"),
        "accurate_branch_ids": list(settings.get("accurate_branch_ids") or []),
        "include_null_branch": bool(settings.get("include_null_branch")),
        "delivery_cutover": delivery_cutover,
        "updated_at": stamp,
        "updated_by": _actor_info(actor),
    }
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    return {
        "initialized": True,
        "replayed": False,
        "branch_id": wanted,
        "cutover_at": stamp,
        "order_count": preview["order_count"],
        "line_count": preview["line_count"],
        "item_count": preview["item_count"],
        "baseline_qty_total": preview["baseline_qty_total"],
        "baseline_basis": "proven_shipped",
        "unresolved_count": 0,
    }
