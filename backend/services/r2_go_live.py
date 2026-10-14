"""R2 go-live safety: delivery cutover baseline, authority guards, readiness, cutover tools.

LOCAL / future go-live only. Does not auto-mutate production.
Physical opening APPLY is never implied by dry-run helpers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from services.purchasing_inventory_access import (
    HOLDING_TEAM_PACKAGE_GRANTS,
    HOLDING_TEAM_ROLE,
    PACKAGE_PERMISSIONS,
)
from permissions import derived_nav_permissions

SETTINGS_KEY = "r2_go_live"
CUTOVER_COLLECTION = "r2_go_live_settings"

LEGACY_MUTATION_BLOCKED_MSG = (
    "Mutasi stok legacy dinonaktifkan. Gunakan jalur R2 (Stock Saat Ini / Stock Opname R2)."
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_go_live_doc() -> Dict[str, Any]:
    return {
        "key": SETTINGS_KEY,
        "r2_authority_active": False,
        "delivery_cutover": {
            "initialized": False,
            "cutover_at": None,
            "baseline_basis": None,
            "baseline_by_master_item_id": {},
            "baseline_by_order_line": {},
            "order_count": 0,
            "line_count": 0,
        },
        "updated_at": None,
        "updated_by": None,
    }


async def get_go_live_settings(db) -> Dict[str, Any]:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    if not isinstance(doc, dict):
        return default_go_live_doc()
    base = default_go_live_doc()
    cut = dict(base["delivery_cutover"])
    raw_cut = doc.get("delivery_cutover") if isinstance(doc.get("delivery_cutover"), dict) else {}
    cut.update({k: raw_cut.get(k, cut[k]) for k in cut})
    cut["baseline_by_master_item_id"] = {
        str(k): float(v)
        for k, v in dict(raw_cut.get("baseline_by_master_item_id") or {}).items()
    }
    cut["baseline_by_order_line"] = {
        str(k): float(v)
        for k, v in dict(raw_cut.get("baseline_by_order_line") or {}).items()
    }
    return {
        "key": SETTINGS_KEY,
        "r2_authority_active": bool(doc.get("r2_authority_active")),
        "delivery_cutover": cut,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


async def is_r2_authority_active(db) -> bool:
    """True when R2 is the sole physical mutation authority."""
    settings = await get_go_live_settings(db)
    return bool(settings.get("r2_authority_active"))


async def require_legacy_stock_mutation_allowed(db) -> None:
    """Refuse legacy product_stock_state mutations after R2 authority activation."""
    if await is_r2_authority_active(db):
        raise HTTPException(409, LEGACY_MUTATION_BLOCKED_MSG)


R2_MUTATION_BLOCKED_MSG = (
    "Mutasi stok R2 belum diaktifkan. Aktifkan r2_authority_active sebelum menulis R2."
)


async def require_r2_authority_active(db) -> None:
    """Refuse R2 physical mutations until authority is explicitly activated."""
    if not await is_r2_authority_active(db):
        raise HTTPException(409, R2_MUTATION_BLOCKED_MSG)


def order_line_baseline_key(order_id: str, line_idx: int) -> str:
    return f"{order_id}:{int(line_idx)}"


async def get_delivery_cutover_baseline(db) -> Dict[str, Any]:
    settings = await get_go_live_settings(db)
    return settings["delivery_cutover"]


async def cutover_delivered_qty_for_item(db, master_item_id: str) -> float:
    cut = await get_delivery_cutover_baseline(db)
    if not cut.get("initialized"):
        return 0.0
    return float((cut.get("baseline_by_master_item_id") or {}).get(str(master_item_id), 0) or 0)


async def cutover_delivered_qty_for_order_line(db, order_id: str, line_idx: int) -> float:
    cut = await get_delivery_cutover_baseline(db)
    if not cut.get("initialized"):
        return 0.0
    key = order_line_baseline_key(order_id, line_idx)
    return float((cut.get("baseline_by_order_line") or {}).get(key, 0) or 0)


class DeliveryCutoverBlockedError(Exception):
    """Cutover cannot proceed: shipment attribution is ambiguous/unproven."""

    def __init__(self, message: str, *, ambiguous: Optional[List[dict]] = None):
        super().__init__(message)
        self.ambiguous = list(ambiguous or [])


_DELIVERY_IDENTITY_KEYS = (
    "id",
    "sales_delivery_id",
    "delivery_id",
    "sj_number",
    "number",
    "shipment_id",
)


def _delivery_identity_tokens(doc: Optional[dict]) -> List[str]:
    """All stable identity aliases for one shipment/SJ (order preserves primary).

    A single real delivery often appears as order.shipment.id=shipment-1 with
    sales_delivery_id=delivery-1 AND sales_deliveries.id=delivery-1. Dedup must
    match on any shared alias — not on qty or PO alone.
    """
    if not isinstance(doc, dict):
        return []
    out: List[str] = []
    seen: set = set()
    for key in _DELIVERY_IDENTITY_KEYS:
        token = str(doc.get(key) or "").strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _delivery_identity_token(doc: Optional[dict]) -> str:
    """Primary display/report identity (first non-empty alias)."""
    tokens = _delivery_identity_tokens(doc)
    return tokens[0] if tokens else ""


def _identity_already_counted(
    tokens: Iterable[str],
    *,
    seen_identities: set,
    exclude_identities: set,
) -> bool:
    for token in tokens:
        if token in seen_identities or token in exclude_identities:
            return True
    return False


def _mark_identities_seen(tokens: Iterable[str], seen_identities: set) -> None:
    for token in tokens:
        if token:
            seen_identities.add(token)


def _proven_shipped_qty_for_shipment(order: dict, shipment: dict) -> float:
    """Qty actually confirmed delivered for one SJ — never from sales_deliveries.status.

    create_sales_delivery sets sales_deliveries.status to SELESAI/SEBAGIAN when the
    SJ allocation fills the PO remaining qty. That is NOT customer delivery proof.
    Proof is delivery_confirmations and/or shipment delivery_status=Terkirim /
    delivered_at (with qty_delivered when present).
    """
    from quantity_rules import (
        delivery_batch_qty,
        shipment_identity_keys,
        shipment_is_active,
    )
    from services.physical_stock import _qty_num

    if not isinstance(shipment, dict) or not shipment_is_active(shipment):
        return 0.0
    keys = shipment_identity_keys(order, shipment)
    conf_total = 0.0
    for rec in order.get("delivery_confirmations") or []:
        if not isinstance(rec, dict):
            continue
        if rec.get("is_cancelled") or rec.get("cancelled") or rec.get("is_deleted"):
            continue
        if str(rec.get("shipment_id") or "").strip() not in keys:
            continue
        conf_total += float(_qty_num(rec.get("qty") if rec.get("qty") is not None else rec.get("quantity")))
    batch = float(delivery_batch_qty(shipment))
    if conf_total > 0:
        return min(conf_total, batch) if batch > 0 else conf_total
    if str(shipment.get("delivery_status") or "").strip() == "Terkirim" or shipment.get("delivered_at"):
        if shipment.get("qty_delivered") is not None and str(shipment.get("qty_delivered")).strip() != "":
            qd = float(_qty_num(shipment.get("qty_delivered")))
            if qd <= 0:
                return 0.0
            return min(qd, batch) if batch > 0 else qd
        return batch if batch > 0 else 0.0
    return 0.0


def _confirmation_line_qtys(
    order: dict,
    shipment: dict,
    *,
    order_line_count: int,
) -> Dict[int, float]:
    """Line-level qty from delivery_confirmations items when present."""
    from quantity_rules import shipment_identity_keys
    from services.physical_stock import _qty_num

    keys = shipment_identity_keys(order, shipment)
    out: Dict[int, float] = {}
    for rec in order.get("delivery_confirmations") or []:
        if not isinstance(rec, dict):
            continue
        if rec.get("is_cancelled") or rec.get("cancelled") or rec.get("is_deleted"):
            continue
        if str(rec.get("shipment_id") or "").strip() not in keys:
            continue
        items = [it for it in (rec.get("items") or []) if isinstance(it, dict)]
        if not items:
            continue
        for it in items:
            try:
                idx = int(it.get("line_idx"))
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= order_line_count:
                continue
            qty = float(_qty_num(it.get("qty") if it.get("qty") is not None else it.get("quantity")))
            if qty > 0:
                out[idx] = float(out.get(idx, 0.0)) + qty
    return out


def _scale_attributed_to_proven(
    attributed: Dict[int, float],
    proven: float,
) -> Dict[int, float]:
    if proven <= 0 or not attributed:
        return {}
    total = sum(attributed.values())
    if total <= 0:
        return {}
    if abs(total - proven) < 1e-9:
        return dict(attributed)
    factor = proven / total
    return {idx: float(qty) * factor for idx, qty in attributed.items()}


def _attribute_line_qtys(
    *,
    order_id: str,
    source: str,
    identity: str,
    line_rows: Iterable[dict],
    order_line_count: int,
) -> Tuple[Dict[int, float], List[dict]]:
    """Map shipment/SJ lines → order line_idx. Fail closed when attribution is unclear."""
    from services.physical_stock import _qty_num

    attributed: Dict[int, float] = {}
    ambiguous: List[dict] = []
    rows = [ln for ln in line_rows if isinstance(ln, dict)]
    if not rows:
        return attributed, ambiguous

    unresolved = []
    for ln in rows:
        qty = float(_qty_num(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
        if qty <= 0:
            continue
        raw_idx = ln.get("line_idx")
        if raw_idx is None or str(raw_idx).strip() == "":
            unresolved.append({"qty": qty, "line": ln})
            continue
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            unresolved.append({"qty": qty, "line": ln})
            continue
        if idx < 0 or idx >= order_line_count:
            ambiguous.append({
                "order_id": order_id,
                "source": source,
                "identity": identity,
                "reason": "line_idx_out_of_range",
                "line_idx": idx,
                "qty": qty,
            })
            continue
        attributed[idx] = float(attributed.get(idx, 0.0)) + qty

    if unresolved:
        if order_line_count == 1 and len(unresolved) >= 1:
            # Sole line on the PO: safe to attribute unmarked qty to line 0.
            for row in unresolved:
                attributed[0] = float(attributed.get(0, 0.0)) + float(row["qty"])
        else:
            for row in unresolved:
                ambiguous.append({
                    "order_id": order_id,
                    "source": source,
                    "identity": identity,
                    "reason": "missing_or_invalid_line_idx",
                    "qty": row["qty"],
                })
    return attributed, ambiguous


def _header_qty_for_sole_line(
    *,
    order_id: str,
    source: str,
    identity: str,
    header_qty: float,
    order_line_count: int,
    attributed: Dict[int, float],
) -> Tuple[Dict[int, float], List[dict]]:
    """Attribute proven header-only qty to line 0 when the PO has exactly one line."""
    if attributed or header_qty <= 0:
        return attributed, []
    if order_line_count == 1:
        return {0: float(header_qty)}, []
    return {}, [{
        "order_id": order_id,
        "source": source,
        "identity": identity,
        "reason": "proven_delivery_without_attributable_lines",
        "qty": float(header_qty),
    }]


def _build_delivery_baseline(
    orders: Iterable[dict],
    sales_deliveries: Optional[Iterable[dict]] = None,
    accurate_owners: Optional[Dict[str, str]] = None,
    exclude_identities: Optional[Iterable[str]] = None,
) -> Tuple[Dict[str, float], Dict[str, float], int, int, List[dict]]:
    """Snapshot PROVEN historically shipped qty as cutover delivery baseline.

    Operator completion alone never fills the baseline — production selesai ≠
    barang sudah dikirim. sales_deliveries.status SELESAI/SEBAGIAN is SJ
    allocation against the PO, NOT customer delivery proof, and never fills
    the baseline by itself.

    Only order.shipments with delivery_confirmations and/or delivery_status
    Terkirim contribute. Alias identity dedupe prevents double counting the
    same real shipment. Ambiguous line attribution fail-closes.
    """
    from services.physical_stock import _line_master_id, _order_cancelled, _qty_num

    owners = accurate_owners or {}
    excluded = {str(x).strip() for x in (exclude_identities or []) if str(x).strip()}
    by_item: Dict[str, float] = {}
    by_line: Dict[str, float] = {}
    ambiguous: List[dict] = []
    order_count = 0
    line_count = 0
    # sales_deliveries retained in signature for callers/preview; never used as
    # delivery proof (SELESAI/SEBAGIAN = allocation, not customer receipt).
    _ = sales_deliveries

    for order in orders:
        if _order_cancelled(order):
            continue
        order_id = str(order.get("id") or "").strip()
        if not order_id:
            continue
        lines = list(order.get("line_items") or [])
        if not lines:
            continue
        master_ids = [_line_master_id(line, owners) for line in lines]
        shipped_by_idx: Dict[int, float] = {}
        seen_identities: set = set()

        for sh in order.get("shipments") or []:
            if not isinstance(sh, dict):
                continue
            proven = _proven_shipped_qty_for_shipment(order, sh)
            if proven <= 0:
                continue
            tokens = _delivery_identity_tokens(sh)
            identity = tokens[0] if tokens else ""
            if tokens and _identity_already_counted(
                tokens, seen_identities=seen_identities, exclude_identities=excluded,
            ):
                continue
            _mark_identities_seen(tokens, seen_identities)

            attributed = _confirmation_line_qtys(
                order, sh, order_line_count=len(lines),
            )
            if attributed:
                attributed = _scale_attributed_to_proven(attributed, proven)
            else:
                rows = sh.get("lines") if isinstance(sh.get("lines"), list) else []
                attributed, issues = _attribute_line_qtys(
                    order_id=order_id,
                    source="order.shipment",
                    identity=identity or "(missing-id)",
                    line_rows=rows,
                    order_line_count=len(lines),
                )
                ambiguous.extend(issues)
                if issues:
                    continue
                if not attributed:
                    attributed, header_issues = _header_qty_for_sole_line(
                        order_id=order_id,
                        source="order.shipment",
                        identity=identity or "(missing-id)",
                        header_qty=proven,
                        order_line_count=len(lines),
                        attributed=attributed,
                    )
                    ambiguous.extend(header_issues)
                    if header_issues:
                        continue
                else:
                    attributed = _scale_attributed_to_proven(attributed, proven)
            if not attributed:
                ambiguous.append({
                    "order_id": order_id,
                    "source": "order.shipment",
                    "identity": identity or "(missing-id)",
                    "reason": "proven_delivery_without_attributable_lines",
                    "qty": proven,
                })
                continue
            for idx, qty in attributed.items():
                shipped_by_idx[idx] = float(shipped_by_idx.get(idx, 0.0)) + float(qty)

        touched = False
        for idx, shipped in shipped_by_idx.items():
            if idx < 0 or idx >= len(lines) or not master_ids[idx]:
                continue
            ordered = float(_qty_num(lines[idx].get("quantity")))
            # Never baseline above ordered qty; never invent from completion/SJ status.
            qty = min(max(float(shipped), 0.0), max(ordered, 0.0))
            if qty <= 0:
                continue
            key = order_line_baseline_key(order_id, idx)
            by_line[key] = qty
            mid = str(master_ids[idx])
            by_item[mid] = float(by_item.get(mid, 0.0)) + qty
            line_count += 1
            touched = True
        if touched:
            order_count += 1

    return by_item, by_line, order_count, line_count, ambiguous


async def _r2_booked_delivery_identities(db) -> set:
    """Identities already deducted via R2 confirmations — must not enter baseline."""
    from services.physical_stock import SALES_DELIVERY_CONFIRMATIONS

    rows = await db[SALES_DELIVERY_CONFIRMATIONS].find(
        {"reversed": {"$ne": True}},
        {"_id": 0, "id": 1, "sales_delivery_id": 1},
    ).to_list(100000)
    out: set = set()
    for row in rows:
        for key in ("id", "sales_delivery_id"):
            token = str(row.get(key) or "").strip()
            if token:
                out.add(token)
    return out


async def preview_delivery_cutover_baseline(db) -> Dict[str, Any]:
    """Read-only preview of PROVEN shipped cutover baseline (not production completed)."""
    from services.physical_stock import _unique_accurate_owners

    existing = await get_delivery_cutover_baseline(db)
    owners = await _unique_accurate_owners(db)
    orders = await db.orders.find({}, {"_id": 0}).to_list(50000)
    sales_deliveries = await db.sales_deliveries.find({}, {"_id": 0}).to_list(100000)
    exclude = await _r2_booked_delivery_identities(db)
    by_item, by_line, order_count, line_count, ambiguous = _build_delivery_baseline(
        orders, sales_deliveries, owners, exclude_identities=exclude,
    )
    return {
        "already_initialized": bool(existing.get("initialized")),
        "existing_cutover_at": existing.get("cutover_at"),
        "order_count": order_count,
        "line_count": line_count,
        "item_count": len(by_item),
        "baseline_qty_total": sum(by_item.values()),
        "baseline_by_master_item_id": by_item,
        "baseline_by_order_line": by_line,
        "baseline_basis": "proven_shipped",
        "ambiguous_count": len(ambiguous),
        "ambiguous": ambiguous[:100],
        "blocked": bool(ambiguous),
    }


async def initialize_delivery_cutover_baseline(
    db,
    *,
    actor: Optional[dict] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Persist proven-shipped cutover baseline. Idempotent unless force=True.

    Fail-closed when any shipment/SJ cannot be attributed safely — never treats
    completed production as delivered and never invents zeros for ambiguous rows.
    """
    current = await get_go_live_settings(db)
    cut = current["delivery_cutover"]
    if cut.get("initialized") and not force:
        return {
            "initialized": True,
            "replayed": True,
            "cutover_at": cut.get("cutover_at"),
            "order_count": cut.get("order_count") or 0,
            "line_count": cut.get("line_count") or 0,
            "item_count": len(cut.get("baseline_by_master_item_id") or {}),
            "baseline_qty_total": sum((cut.get("baseline_by_master_item_id") or {}).values()),
            "baseline_basis": cut.get("baseline_basis") or "proven_shipped",
        }

    if force:
        from services.physical_stock import SALES_DELIVERY_CONFIRMATIONS

        r2_booked = await db[SALES_DELIVERY_CONFIRMATIONS].count_documents(
            {"reversed": {"$ne": True}}
        )
        if r2_booked:
            raise DeliveryCutoverBlockedError(
                "Force re-init cutover ditolak: sudah ada konfirmasi pengiriman R2. "
                "Re-inisialisasi akan menduplikasi qty terkirim (baseline + ledger).",
                ambiguous=[{
                    "source": "r2_confirmation",
                    "reason": "force_reinit_after_r2_delivery",
                    "count": int(r2_booked),
                }],
            )

    preview = await preview_delivery_cutover_baseline(db)
    if preview.get("blocked"):
        raise DeliveryCutoverBlockedError(
            "Cutover baseline ditolak: ada pengiriman historis yang tidak dapat "
            "diatribusikan ke baris pesanan secara aman. "
            "Selesaikan atribusi SJ/line_idx sebelum inisialisasi.",
            ambiguous=preview.get("ambiguous") or [],
        )
    cutover_at = now_iso()
    actor_info = None
    if isinstance(actor, dict):
        actor_info = {
            "id": str(actor.get("id") or actor.get("_id") or ""),
            "email": actor.get("email") or "",
            "name": actor.get("name") or actor.get("email") or "user",
            "role": actor.get("role") or "",
        }
    delivery_cutover = {
        "initialized": True,
        "cutover_at": cutover_at,
        "baseline_basis": "proven_shipped",
        "baseline_by_master_item_id": preview["baseline_by_master_item_id"],
        "baseline_by_order_line": preview["baseline_by_order_line"],
        "order_count": preview["order_count"],
        "line_count": preview["line_count"],
    }
    saved = {
        "key": SETTINGS_KEY,
        "r2_authority_active": bool(current.get("r2_authority_active")),
        "delivery_cutover": delivery_cutover,
        "updated_at": cutover_at,
        "updated_by": actor_info,
    }
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    return {
        "initialized": True,
        "replayed": False,
        "cutover_at": cutover_at,
        "order_count": preview["order_count"],
        "line_count": preview["line_count"],
        "item_count": preview["item_count"],
        "baseline_qty_total": preview["baseline_qty_total"],
        "baseline_basis": "proven_shipped",
    }


async def set_r2_authority_active(
    db,
    *,
    active: bool,
    actor: Optional[dict] = None,
) -> Dict[str, Any]:
    """Flip R2 as sole physical mutation authority (local/go-live tooling)."""
    current = await get_go_live_settings(db)
    actor_info = None
    if isinstance(actor, dict):
        actor_info = {
            "id": str(actor.get("id") or ""),
            "email": actor.get("email") or "",
            "name": actor.get("name") or actor.get("email") or "user",
            "role": actor.get("role") or "",
        }
    saved = {
        "key": SETTINGS_KEY,
        "r2_authority_active": bool(active),
        "delivery_cutover": current["delivery_cutover"],
        "updated_at": now_iso(),
        "updated_by": actor_info,
    }
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    return await get_go_live_settings(db)


# ---------------------------------------------------------------------------
# HOLDING_TEAM permission top-up (idempotent; apply only when requested)
# ---------------------------------------------------------------------------

def required_holding_team_package_permissions() -> List[str]:
    """Exact recovered package keys Holding must hold after go-live."""
    grants = set(HOLDING_TEAM_PACKAGE_GRANTS) | set(PACKAGE_PERMISSIONS)
    # Ensure nav keys for advances / returns / branches even if catalog drifts.
    grants |= {
        "nav.purchasing.advances",
        "nav.purchasing.returns",
        "nav.purchasing.hpp",
        "nav.inventory.branches_warehouses",
        "nav.inventory.current_stock",
        "nav.inventory.inventory_value",
        "org.branches.view",
        "org.branches.manage",
        "org.warehouses.view",
        "org.warehouses.manage",
        "purchase_hpp.view",
        "inventory_valuation.view",
        "inventory_valuation.export",
        "inventory_valuation.manage_opening",
        "master_items.manage",
    }
    grants |= derived_nav_permissions(grants)
    return sorted(grants)


async def preview_holding_team_permission_top_up(db) -> Dict[str, Any]:
    required = required_holding_team_package_permissions()
    override = await db.role_permissions.find_one({"role": HOLDING_TEAM_ROLE}, {"_id": 0})
    existing = list((override or {}).get("permissions") or [])
    missing = [p for p in required if p not in existing]
    return {
        "role": HOLDING_TEAM_ROLE,
        "override_exists": bool(override),
        "existing_count": len(existing),
        "required_count": len(required),
        "missing": missing,
        "ready": not missing and bool(override),
        # Without override, builtins apply — still report missing vs builtins for clarity.
        "builtin_only_ok": not override,
    }


async def apply_holding_team_permission_top_up(
    db,
    *,
    apply: bool = False,
    actor: Optional[dict] = None,
) -> Dict[str, Any]:
    """Idempotent Holding package top-up. apply=False is dry-run only."""
    preview = await preview_holding_team_permission_top_up(db)
    if not apply:
        return {**preview, "applied": False, "dry_run": True}
    required = required_holding_team_package_permissions()
    override = await db.role_permissions.find_one({"role": HOLDING_TEAM_ROLE}, {"_id": 0})
    if not override:
        # Create override from required set so future Role UI edits start complete.
        await db.role_permissions.update_one(
            {"role": HOLDING_TEAM_ROLE},
            {
                "$set": {
                    "role": HOLDING_TEAM_ROLE,
                    "permissions": required,
                    "updated_at": now_iso(),
                    "updated_by": "system.r2_go_live.holding_team_top_up",
                }
            },
            upsert=True,
        )
        return {
            **preview,
            "applied": True,
            "dry_run": False,
            "created_override": True,
            "added": required,
            "missing": [],
            "ready": True,
        }
    missing = preview["missing"]
    if missing:
        await db.role_permissions.update_one(
            {"role": HOLDING_TEAM_ROLE},
            {
                "$addToSet": {"permissions": {"$each": missing}},
                "$set": {
                    "updated_at": now_iso(),
                    "updated_by": "system.r2_go_live.holding_team_top_up",
                },
            },
        )
    after = await preview_holding_team_permission_top_up(db)
    return {
        **after,
        "applied": True,
        "dry_run": False,
        "created_override": False,
        "added": missing,
    }


# ---------------------------------------------------------------------------
# Read-only readiness checker
# ---------------------------------------------------------------------------

async def _mongo_transaction_ready(db) -> Tuple[bool, Dict[str, Any]]:
    from services.physical_stock import _client

    client = _client(db)
    try:
        hello = await client.admin.command("hello")
    except Exception as exc:  # noqa: BLE001 — readiness must never raise
        return False, {"error": str(exc)}
    info = {
        "set_name": hello.get("setName"),
        "is_writable_primary": bool(hello.get("isWritablePrimary")),
        "logical_session_timeout_minutes": hello.get("logicalSessionTimeoutMinutes"),
    }
    ok = bool(hello.get("setName")) and bool(hello.get("isWritablePrimary"))
    return ok, info


async def check_r2_production_readiness(db) -> Dict[str, Any]:
    """Read-only go-live readiness. Never mutates."""
    from services.org_locations import warehouse_is_operational
    from services.physical_stock import (
        BALANCE_COLLECTION,
        BRANCHES_COLLECTION,
        LEDGER_COLLECTION,
        WAREHOUSES_COLLECTION,
        resolve_operational_branch,
    )

    txn_ok, txn_info = await _mongo_transaction_ready(db)
    branches = await db[BRANCHES_COLLECTION].find(
        {}, {"_id": 0, "id": 1, "code": 1, "name": 1, "active": 1}
    ).to_list(500)
    operational = resolve_operational_branch(branches)
    ygy_ready = bool(operational and operational.get("id"))
    warehouses = []
    operational_wh = []
    if ygy_ready:
        warehouses = await db[WAREHOUSES_COLLECTION].find(
            {"branch_id": operational["id"]},
            {"_id": 0},
        ).to_list(500)
        operational_wh = [
            w for w in warehouses
            if w.get("active") is not False and warehouse_is_operational(w)
        ]
    wh_ready = len(operational_wh) >= 1

    r2_balance_count = await db[BALANCE_COLLECTION].count_documents({})
    r2_movement_count = await db[LEDGER_COLLECTION].count_documents({})
    legacy_nonzero = await db.product_stock_state.count_documents(
        {"total_qty": {"$gt": 0}}
    )

    holding = await preview_holding_team_permission_top_up(db)
    # Builtin-only (no override) is ready for code defaults; override must include package.
    holding_ready = bool(holding.get("ready") or holding.get("builtin_only_ok"))

    cut = await get_delivery_cutover_baseline(db)
    cutover_ready = bool(cut.get("initialized"))

    blockers = []
    if not txn_ok:
        blockers.append("MONGO_TRANSACTION_READY")
    if not ygy_ready:
        blockers.append("YGY_BRANCH_READY")
    if not wh_ready:
        blockers.append("OPERATIONAL_WAREHOUSE_READY")
    if not cutover_ready:
        blockers.append("CUTOVER_BASELINE_READY")
    if not holding_ready:
        blockers.append("HOLDING_TEAM_PERMISSION_READY")

    return {
        "MONGO_TRANSACTION_READY": txn_ok,
        "YGY_BRANCH_READY": ygy_ready,
        "OPERATIONAL_WAREHOUSE_READY": wh_ready,
        "R2_BALANCE_COUNT": int(r2_balance_count),
        "R2_MOVEMENT_COUNT": int(r2_movement_count),
        "LEGACY_STOCK_NONZERO_COUNT": int(legacy_nonzero),
        "HOLDING_TEAM_PERMISSION_READY": holding_ready,
        "CUTOVER_BASELINE_READY": cutover_ready,
        "mongo": txn_info,
        "yogyakarta": {
            "id": (operational or {}).get("id"),
            "code": (operational or {}).get("code"),
            "name": (operational or {}).get("name"),
        },
        "operational_warehouses": [
            {"id": w.get("id"), "code": w.get("code"), "name": w.get("name")}
            for w in operational_wh
        ],
        "holding_team": {
            "override_exists": holding.get("override_exists"),
            "missing": holding.get("missing") or [],
            "builtin_only_ok": holding.get("builtin_only_ok"),
        },
        "delivery_cutover": {
            "initialized": cut.get("initialized"),
            "cutover_at": cut.get("cutover_at"),
            "item_count": len(cut.get("baseline_by_master_item_id") or {}),
        },
        "blockers": blockers,
        "READY": len(blockers) == 0,
        "STATUS": "READY" if not blockers else "BLOCKED",
    }


# ---------------------------------------------------------------------------
# Opening cutover tool (dry-run mandatory; apply separate and refused here by default)
# ---------------------------------------------------------------------------

class OpeningCutoverError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


async def dry_run_legacy_to_r2_opening(
    db,
    *,
    warehouse_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Map nonzero product_stock_state → R2 OPENING_BALANCE plan. No writes."""
    from services.org_locations import warehouse_is_operational
    from services.physical_stock import (
        BALANCE_COLLECTION,
        BRANCHES_COLLECTION,
        LEDGER_COLLECTION,
        MASTER_ITEMS_COLLECTION,
        MOVEMENT_OPENING_BALANCE,
        WAREHOUSES_COLLECTION,
        _client,
        resolve_operational_branch,
        require_transaction_support,
    )

    txn_ok, txn_info = await _mongo_transaction_ready(db)
    if not txn_ok:
        raise OpeningCutoverError(
            "MongoDB transactions unavailable; refuse opening cutover.",
            status_code=409,
        )
    # Also exercise the same preflight R2 uses
    try:
        await require_transaction_support(_client(db))
    except Exception as exc:  # noqa: BLE001
        raise OpeningCutoverError(str(exc), status_code=409) from exc

    branches = await db[BRANCHES_COLLECTION].find({}, {"_id": 0}).to_list(500)
    operational = resolve_operational_branch(branches)
    if not operational:
        raise OpeningCutoverError("Cabang operasional Yogyakarta tidak ditemukan", status_code=409)

    warehouses = await db[WAREHOUSES_COLLECTION].find(
        {"branch_id": operational["id"], "active": {"$ne": False}},
        {"_id": 0},
    ).to_list(500)
    warehouses = [w for w in warehouses if warehouse_is_operational(w)]
    if not warehouses:
        raise OpeningCutoverError("Gudang operasional Yogyakarta tidak ditemukan", status_code=409)

    selected = None
    requested = str(warehouse_id or "").strip()
    if requested:
        selected = next((w for w in warehouses if str(w.get("id")) == requested), None)
        if not selected:
            raise OpeningCutoverError("Gudang yang dipilih bukan gudang operasional YGY", status_code=400)
    elif len(warehouses) == 1:
        selected = warehouses[0]
    else:
        raise OpeningCutoverError(
            "Pilih warehouse_id operasional YGY (lebih dari satu gudang aktif).",
            status_code=400,
        )

    legacy_rows = await db.product_stock_state.find(
        {"total_qty": {"$gt": 0}},
        {"_id": 0},
    ).to_list(20000)

    plans = []
    skipped = []
    qty_total = 0.0
    for row in legacy_rows:
        mid = str(row.get("master_item_id") or row.get("id") or "").strip()
        if not mid:
            skipped.append({"reason": "missing_master_item_id", "row": row.get("id")})
            continue
        master = await db[MASTER_ITEMS_COLLECTION].find_one({"id": mid}, {"_id": 0})
        if not master:
            skipped.append({"reason": "master_not_found", "master_item_id": mid})
            continue
        qty = float(row.get("total_qty") or 0)
        if qty <= 0:
            continue
        unit = (
            str(row.get("base_uom") or master.get("unit") or master.get("base_uom") or "PCS")
            .strip()
            or "PCS"
        )
        existing_bal = await db[BALANCE_COLLECTION].find_one(
            {
                "master_item_id": mid,
                "branch_id": operational["id"],
                "warehouse_id": selected["id"],
            },
            {"_id": 0, "physical_qty": 1},
        )
        existing_open = await db[LEDGER_COLLECTION].find_one(
            {
                "master_item_id": mid,
                "branch_id": operational["id"],
                "warehouse_id": selected["id"],
                "movement_type": MOVEMENT_OPENING_BALANCE,
            },
            {"_id": 0, "id": 1},
        )
        if existing_bal or existing_open:
            skipped.append({
                "reason": "r2_already_initialized",
                "master_item_id": mid,
                "physical_qty": (existing_bal or {}).get("physical_qty"),
            })
            continue
        plans.append({
            "master_item_id": mid,
            "item_code": master.get("item_code") or "",
            "item_name": master.get("item_name") or master.get("name") or "",
            "branch_id": operational["id"],
            "warehouse_id": selected["id"],
            "base_unit": unit,
            "qty": qty,
            "legacy_total_qty": qty,
            "movement_type": MOVEMENT_OPENING_BALANCE,
            "hpp_note": "PENDING_VALUATION — physical opening only; no HPP invented",
        })
        qty_total += qty

    return {
        "dry_run": True,
        "apply": False,
        "mongo": txn_info,
        "branch": {
            "id": operational["id"],
            "code": operational.get("code"),
            "name": operational.get("name"),
        },
        "warehouse": {
            "id": selected["id"],
            "code": selected.get("code"),
            "name": selected.get("name"),
        },
        "legacy_nonzero_count": len(legacy_rows),
        "planned_count": len(plans),
        "skipped_count": len(skipped),
        "planned_qty_total": qty_total,
        "plans": plans,
        "skipped": skipped,
        "legacy_source_preserved": True,
        "message": "Dry-run only. Call apply explicitly in a later authorized go-live step.",
    }


async def apply_legacy_to_r2_opening(*_args, **_kwargs):
    """APPLY is intentionally not implemented in this safety pass."""
    raise OpeningCutoverError(
        "APPLY opening cutover is disabled in this task. Dry-run only.",
        status_code=409,
    )
