"""PUR-LOC-01 LOC-A: purchasing branch resolution (YGY-first) and legacy set helpers.

Mode default is ygy_first without writing app_settings. Multi mode is reserved for
later; field/API contract stays the same.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from services.branch_access import (
    BRANCHES_COLLECTION,
    DEFAULT_BRANCH_CODE,
    WAREHOUSES_COLLECTION,
    authorize_stock_location,
    require_branch_access,
)
from services.purchase_common import now_iso

MODE_YGY_FIRST = "ygy_first"
MODE_MULTI = "multi"
SETTINGS_KEY = "purchasing_branch_mode"

YGY_MISSING_MSG = (
    "Cabang operasional Yogyakarta (YGY) belum dikonfigurasi di master cabang."
)
YGY_INACTIVE_MSG = "Cabang operasional Yogyakarta (YGY) tidak aktif."
NON_YGY_IN_TRANSITION_MSG = (
    "Masa transisi: Pesanan Pembelian baru hanya boleh memakai Cabang Yogyakarta (YGY)."
)
LEGACY_BRANCH_REQUIRED_MSG = "Lengkapi Cabang PO terlebih dahulu."
CROSS_PO_WAREHOUSE_MSG = "Gudang tidak berada di Cabang Pembelian PO."
BRANCH_ALREADY_SET_MSG = "Cabang Pembelian sudah diisi dan tidak dapat diubah lewat endpoint ini."
GR_BRANCH_CONFLICT_MSG = (
    "Tidak dapat menetapkan cabang: terdapat penerimaan dengan lokasi di luar cabang tujuan."
)
PO_BRANCH_CHANGED_MSG = "Cabang Pembelian PO berubah; ulangi penerimaan."
BRANCH_OUTCOME_UNKNOWN_MSG = (
    "Penetapan Cabang Pembelian belum dapat dipastikan. "
    "Periksa status dokumen sebelum mengulang."
)


async def get_purchasing_branch_mode(db) -> str:
    """Read optional setting; default ygy_first. Never mutates settings."""
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0, "mode": 1, "enabled": 1})
    raw = ""
    if isinstance(doc, dict):
        raw = str(doc.get("mode") or "").strip().lower()
        if not raw and doc.get("enabled") is False:
            raw = MODE_MULTI
    if raw in {MODE_YGY_FIRST, MODE_MULTI}:
        return raw
    return MODE_YGY_FIRST


async def resolve_active_ygy_branch(db) -> dict:
    """Resolve YGY from master by code (then Yogyakarta name). Fail closed; never auto-create."""
    doc = await db[BRANCHES_COLLECTION].find_one(
        {"code": DEFAULT_BRANCH_CODE},
        {"_id": 0},
    )
    if not doc:
        doc = await db[BRANCHES_COLLECTION].find_one(
            {"name": {"$regex": "yogyakarta", "$options": "i"}},
            {"_id": 0},
        )
    if not doc or not str(doc.get("id") or "").strip():
        raise HTTPException(409, YGY_MISSING_MSG)
    if doc.get("active") is False:
        raise HTTPException(409, YGY_INACTIVE_MSG)
    return doc


def branch_snapshot(branch: dict) -> Dict[str, str]:
    return {
        "branch_id": str(branch.get("id") or "").strip(),
        "branch_code": str(branch.get("code") or "").strip(),
        "branch_name": str(branch.get("name") or "").strip(),
    }


async def resolve_create_purchase_order_branch(
    db,
    payload: Dict[str, Any],
    user: dict,
) -> Dict[str, str]:
    """Server-authoritative branch for new PO. YGY-first rejects non-YGY client picks."""
    mode = await get_purchasing_branch_mode(db)
    client_branch = str(payload.get("branch_id") or "").strip()

    if mode == MODE_YGY_FIRST:
        ygy = await resolve_active_ygy_branch(db)
        snap = branch_snapshot(ygy)
        await require_branch_access(db, user, snap["branch_id"])
        if client_branch and client_branch != snap["branch_id"]:
            raise HTTPException(400, NON_YGY_IN_TRANSITION_MSG)
        return snap

    # multi (future): require explicit branch_id
    if not client_branch:
        raise HTTPException(400, "Cabang Pembelian (branch_id) wajib diisi.")
    branch = await db[BRANCHES_COLLECTION].find_one({"id": client_branch}, {"_id": 0})
    if not branch or not str(branch.get("id") or "").strip():
        raise HTTPException(400, "Cabang Pembelian tidak ditemukan.")
    if branch.get("active") is False:
        raise HTTPException(400, "Cabang Pembelian tidak aktif.")
    await require_branch_access(db, user, client_branch)
    return branch_snapshot(branch)


async def load_active_branch(db, branch_id: str) -> dict:
    bid = str(branch_id or "").strip()
    if not bid:
        raise HTTPException(400, "branch_id wajib diisi.")
    branch = await db[BRANCHES_COLLECTION].find_one({"id": bid}, {"_id": 0})
    if not branch:
        raise HTTPException(404, "Cabang tidak ditemukan.")
    if branch.get("active") is False:
        raise HTTPException(400, "Cabang tidak aktif.")
    return branch


async def assert_po_receipts_match_branch(
    db, po_id: str, branch_id: str, *, session=None
) -> None:
    """Reject legacy branch set when any active GR location is outside target branch."""
    bid = str(branch_id or "").strip()
    rows = await db.purchase_goods_receipts.find(
        {
            "purchase_order_id": str(po_id),
            "cancelled": {"$ne": True},
        },
        {"_id": 0, "id": 1, "branch_id": 1, "warehouse_id": 1, "receipt_number": 1},
        session=session,
    ).to_list(500)
    for row in rows:
        gr_branch = str(row.get("branch_id") or "").strip()
        wid = str(row.get("warehouse_id") or "").strip()
        if wid:
            wh = await db[WAREHOUSES_COLLECTION].find_one(
                {"id": wid}, {"_id": 0, "branch_id": 1}, session=session,
            )
            wh_branch = str((wh or {}).get("branch_id") or "").strip()
            if wh_branch and wh_branch != bid:
                raise HTTPException(400, GR_BRANCH_CONFLICT_MSG)
            if gr_branch and gr_branch != bid:
                raise HTTPException(400, GR_BRANCH_CONFLICT_MSG)
        elif gr_branch and gr_branch != bid:
            raise HTTPException(400, GR_BRANCH_CONFLICT_MSG)


async def require_po_branch_stable_for_receipt(
    db,
    po_id: str,
    expected_branch_id: str,
    *,
    session=None,
) -> None:
    """CAS: PO.branch_id must still equal the branch authorized for this GR.

    Touches the PO document so concurrent branch-set transactions conflict
    at the database (multi-worker safe; not an asyncio lock).
    """
    expected = str(expected_branch_id or "").strip()
    if not expected:
        raise HTTPException(400, LEGACY_BRANCH_REQUIRED_MSG)
    result = await db.purchase_orders.update_one(
        {
            "id": str(po_id),
            "branch_id": expected,
            "cancelled": {"$ne": True},
        },
        {"$set": {"branch_receipt_guard_at": now_iso()}},
        session=session,
    )
    if result.matched_count == 1:
        return
    po = await db.purchase_orders.find_one(
        {"id": str(po_id)},
        {"_id": 0, "branch_id": 1, "cancelled": 1},
        session=session,
    )
    if not po or po.get("cancelled") is True:
        raise HTTPException(400, "Pesanan sudah dibatalkan; penerimaan tidak dapat dibuat.")
    if not str(po.get("branch_id") or "").strip():
        raise HTTPException(400, LEGACY_BRANCH_REQUIRED_MSG)
    raise HTTPException(409, PO_BRANCH_CHANGED_MSG)


async def authorize_gr_warehouse_for_po(
    db,
    user: dict,
    *,
    po_branch_id: str,
    warehouse_id: Optional[str],
) -> Optional[str]:
    """Authorize GR warehouse against PO.branch_id before any stock write.

    Returns the warehouse_id to use (may pin a resolved default).
    """
    po_branch = str(po_branch_id or "").strip()
    if not po_branch:
        raise HTTPException(400, LEGACY_BRANCH_REQUIRED_MSG)

    requested = str(warehouse_id or "").strip() or None
    if requested:
        wh = await db[WAREHOUSES_COLLECTION].find_one({"id": requested}, {"_id": 0})
        if not wh:
            raise HTTPException(404, "Gudang tidak ditemukan")
        if wh.get("active") is False:
            raise HTTPException(400, "Gudang tidak aktif.")
        await authorize_stock_location(
            db,
            user,
            warehouse_id=requested,
            branch_id=po_branch,
            require_warehouse=True,
        )
        return requested

    # Omitted warehouse: resolve candidate then enforce PO branch membership.
    from services.physical_stock import (
        PhysicalStockValidationError,
        resolve_purchase_receipt_warehouse,
    )

    try:
        resolved_branch, resolved_wh = await resolve_purchase_receipt_warehouse(
            db, None, warehouse_id=None,
        )
    except PhysicalStockValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

    if str(resolved_branch or "").strip() != po_branch:
        raise HTTPException(400, CROSS_PO_WAREHOUSE_MSG)
    await authorize_stock_location(
        db,
        user,
        warehouse_id=str(resolved_wh),
        branch_id=po_branch,
        require_warehouse=True,
    )
    return str(resolved_wh)
