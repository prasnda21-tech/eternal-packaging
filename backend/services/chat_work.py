"""Canonical PO/work references for chat rooms and messages."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from permissions import ALL_PERMISSIONS, effective_permissions
from services.division_tasks import DIVISION_DEFS, build_division_tasks
from services.purchase_common import now_iso
from services.screen_size import line_screen_size


def _uid(user: dict) -> str:
    return str((user or {}).get("user_id") or (user or {}).get("id") or "").strip()


async def _role_defaults_override(db) -> dict:
    docs = await db.role_permissions.find({}, {"_id": 0, "role": 1, "permissions": 1}).to_list(100)
    return {d["role"]: d.get("permissions", []) for d in docs if d.get("role")}


async def _perms(db, user: dict) -> set:
    """Effective permissions including persisted role_permissions overrides."""
    if (user or {}).get("role") == "admin":
        return set(ALL_PERMISSIONS)
    if (user or {}).get("email"):
        u = await db.users.find_one({"email": user["email"]}, {"is_super_admin": 1, "_id": 0})
        if u and u.get("is_super_admin"):
            return set(ALL_PERMISSIONS)
    role_override = await _role_defaults_override(db)
    return effective_permissions(
        (user or {}).get("role"),
        (user or {}).get("permissions_override") or {},
        role_override,
    )


def _is_admin(user: dict) -> bool:
    return (user or {}).get("role") == "admin"


async def _can_view_po(db, user: dict) -> bool:
    return _is_admin(user) or "po.view" in await _perms(db, user)


def _task_visible_to_user(order: dict, user: dict, perms: set, task_id: Optional[str] = None) -> Optional[dict]:
    for division, cfg in DIVISION_DEFS.items():
        if cfg.get("submit_perm") not in perms:
            continue
        mine_only = not (_is_admin(user) or "schedule.edit" in perms or "dashboard.production.view" in perms)
        for task in build_division_tasks([order], division, user, mine_only=mine_only):
            if not task_id or task.get("task_id") == task_id:
                return task
    return None


def _line_by_idx(order: dict, line_idx) -> tuple[Optional[int], Optional[dict]]:
    if line_idx is None or str(line_idx).strip() == "":
        return None, None
    try:
        idx = int(line_idx)
    except (TypeError, ValueError):
        raise HTTPException(400, "line_idx tidak valid")
    lines = order.get("line_items") or []
    if idx < 0 or idx >= len(lines):
        raise HTTPException(404, "Line PO tidak ditemukan")
    return idx, lines[idx]


def _line_idx_from_id(order: dict, line_id) -> Optional[int]:
    if not line_id:
        return None
    want = str(line_id)
    for idx, line in enumerate(order.get("line_items") or []):
        if str(line.get("id") or line.get("line_id") or "") == want:
            return idx
    raise HTTPException(404, "Line PO tidak ditemukan")


def work_key(order_id, line_id=None, line_idx=None, task_id=None) -> str:
    oid = str(order_id or "").strip()
    if not oid:
        raise ValueError("order_id is required")
    if task_id:
        return f"task:{oid}:{str(task_id).strip()}"
    if line_id:
        return f"line:{oid}:id:{str(line_id).strip()}"
    if line_idx is not None and str(line_idx).strip() != "":
        return f"line:{oid}:idx:{int(line_idx)}"
    return f"po:{oid}"


def _order_summary(d: dict) -> dict:
    return {
        "order_id": d.get("id"),
        "po_number": d.get("po_number"),
        "customer_name": d.get("customer_name") or d.get("customer") or d.get("outlet"),
        "deadline": d.get("deadline"),
        "urgent": d.get("priority") == "urgent",
        "priority": d.get("priority") or "normal",
        "buffer_stock": bool(d.get("buffer_stock")),
    }


async def resolve_work_snapshot(db, user, *, order_id, line_id=None, line_idx=None, task_id=None) -> dict:
    order = await db.orders.find_one({"id": str(order_id)}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    perms = await _perms(db, user)
    task = _task_visible_to_user(order, user, perms, task_id) if task_id else None
    if task_id and not task:
        raise HTTPException(404, "Task pekerjaan tidak ditemukan")
    if not (_is_admin(user) or "po.view" in perms) and not task:
        raise HTTPException(403, "Anda tidak punya permission untuk melihat PO ini")
    if line_idx is None and line_id:
        line_idx = _line_idx_from_id(order, line_id)
    idx, line = _line_by_idx(order, line_idx)
    if idx is None and task and task.get("line_idx") is not None:
        idx, line = _line_by_idx(order, task.get("line_idx"))
    stage_label = None
    if task:
        stage_label = task.get("required_input") or task.get("division")
    else:
        stages = [
            "Order Masuk", "Design", "Approval Design", "Payment", "Approval Payment", "Penjadwalan",
            "Screener", "Persiapan Helper", "Operator", "Konfirmasi Sample", "Cetak", "Packing",
            "Surat Jalan", "Loading", "Pengiriman",
        ]
        try:
            stage_label = stages[min(max(int(order.get("current_stage", 0)), 0), len(stages) - 1)]
        except Exception:
            stage_label = order.get("status_label")
    assignee = None
    if task:
        assignee = task.get("assigned_user_name") or task.get("assigned_user_email") or ", ".join(task.get("operator_names") or [])
    qty = (line or {}).get("quantity") if line else order.get("total_quantity")
    return {
        "order_id": order.get("id"),
        "po_number": order.get("po_number"),
        "customer_name": order.get("customer_name") or order.get("customer") or order.get("outlet"),
        "line_id": (line or {}).get("id") or (line or {}).get("line_id") or line_id,
        "line_idx": idx,
        "item_name": (line or {}).get("name") or (line or {}).get("item_name"),
        "qty": qty,
        "task_id": task_id,
        "stage_label": stage_label,
        "division": task.get("division") if task else None,
        "assignee": assignee,
        "work_date": (task or {}).get("work_date") or order.get("work_date"),
        "delivery_deadline": order.get("delivery_deadline_effective") or order.get("delivery_deadline_manual") or order.get("deadline"),
        "delivery_deadline_status": order.get("delivery_deadline_status"),
        "h_minus_1": order.get("delivery_deadline_status") == "h1",
        "urgent": order.get("priority") == "urgent",
        "priority": order.get("priority") or "normal",
        "buffer_stock": bool(order.get("buffer_stock")),
        "screen_size": line_screen_size(order, idx) if idx is not None else None,
        "captured_at": now_iso(),
    }


async def search_work_orders(db, user, q, limit=20) -> list[dict]:
    """Bounded PO/work discovery for Tag Pekerjaan.

    Users with po.view (including DB role overrides) get recent/matching POs.
    Users without po.view but with visible division tasks get only those orders.
    """
    text = str(q or "").strip()
    lim = max(1, min(int(limit or 20), 50))
    perms = await _perms(db, user)
    can_po = _is_admin(user) or "po.view" in perms

    if can_po:
        query = {"cancelled": {"$ne": True}}
        if text:
            query["$or"] = [
                {"po_number": {"$regex": text, "$options": "i"}},
                {"customer": {"$regex": text, "$options": "i"}},
                {"customer_name": {"$regex": text, "$options": "i"}},
                {"outlet": {"$regex": text, "$options": "i"}},
            ]
        docs = await (
            db.orders.find(
                query,
                {
                    "_id": 0,
                    "id": 1,
                    "po_number": 1,
                    "customer": 1,
                    "customer_name": 1,
                    "outlet": 1,
                    "deadline": 1,
                    "priority": 1,
                },
            )
            .sort("created_at", -1)
            .limit(lim)
            .to_list(lim)
        )
        return [_order_summary(d) for d in docs]

    # Task-scoped discovery: scan a bounded recent window, keep only visible work.
    scan_lim = min(120, max(lim * 4, 40))
    query = {"cancelled": {"$ne": True}}
    if text:
        query["$or"] = [
            {"po_number": {"$regex": text, "$options": "i"}},
            {"customer": {"$regex": text, "$options": "i"}},
            {"customer_name": {"$regex": text, "$options": "i"}},
            {"outlet": {"$regex": text, "$options": "i"}},
        ]
    docs = await (
        db.orders.find(
            query,
            {
                "_id": 0,
                "id": 1,
                "po_number": 1,
                "customer": 1,
                "customer_name": 1,
                "outlet": 1,
                "deadline": 1,
                "priority": 1,
                "line_items": 1,
                "operator_splits": 1,
                "current_stage": 1,
                "status_label": 1,
                "work_date": 1,
            },
        )
        .sort("created_at", -1)
        .limit(scan_lim)
        .to_list(scan_lim)
    )
    out = []
    for d in docs:
        if _task_visible_to_user(d, user, perms):
            out.append(_order_summary(d))
            if len(out) >= lim:
                break
    return out


async def list_order_lines(db, user, order_id) -> list[dict]:
    await resolve_work_snapshot(db, user, order_id=order_id)
    order = await db.orders.find_one({"id": str(order_id)}, {"_id": 0, "line_items": 1})
    out = []
    for idx, line in enumerate(order.get("line_items") or []):
        out.append({
            "line_idx": idx,
            "line_id": line.get("id") or line.get("line_id"),
            "name": line.get("name") or line.get("item_name"),
            "qty": line.get("quantity"),
            "screen_size": line.get("screen_size"),
        })
    return out


async def list_order_tasks(db, user, order_id, line_idx=None) -> list[dict]:
    order = await db.orders.find_one({"id": str(order_id)}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    perms = await _perms(db, user)
    can_po = _is_admin(user) or "po.view" in perms
    if not can_po and not _task_visible_to_user(order, user, perms):
        raise HTTPException(403, "Anda tidak punya permission untuk melihat PO ini")
    out = []
    for division, cfg in DIVISION_DEFS.items():
        if cfg.get("submit_perm") not in perms and not _is_admin(user):
            continue
        mine_only = not (_is_admin(user) or "schedule.edit" in perms or "dashboard.production.view" in perms)
        out.extend(build_division_tasks([order], division, user, mine_only=mine_only))
    if line_idx is not None and str(line_idx).strip() != "":
        wanted = int(line_idx)
        out = [t for t in out if t.get("line_idx") == wanted]
    return out
