"""Read-only Daftar Pengajuan Urgent for Dashboard Produksi.

Source: urgent_requests. Does not mutate approval workflow.
Branch scope matches Production Dashboard order access.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException

from services import urgent_requests as urg

PERM_MONITOR = "dashboard.production.urgent_requests.view"
STATUS_ACTIVE = "active"
STATUS_ALL = "all"
VALID_STATUSES = frozenset({
    urg.STATUS_PENDING,
    urg.STATUS_APPROVING,
    urg.STATUS_APPROVED,
    urg.STATUS_REJECTED,
    urg.STATUS_CANCELLED,
})
ACTIVE_STATUSES = (urg.STATUS_PENDING, urg.STATUS_APPROVING)
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 50
MAX_SEARCH_LEN = 80

_ORDER_PROJECT = {
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "customer_name": 1,
    "branch_id": 1,
    "operational_branch_id": 1,
    "branch_name": 1,
    "cabang": 1,
    "outlet": 1,
    "sales_user_id": 1,
}


def parse_status_filter(raw: Optional[str]) -> Optional[List[str]]:
    """Return status list for $in, or None for all statuses."""
    text = str(raw or "").strip().lower()
    if not text or text == STATUS_ACTIVE:
        return list(ACTIVE_STATUSES)
    if text == STATUS_ALL:
        return None
    if text in VALID_STATUSES:
        return [text]
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return list(ACTIVE_STATUSES)
    bad = [p for p in parts if p not in VALID_STATUSES]
    if bad:
        raise HTTPException(400, {"code": "STATUS_INVALID", "detail": "Filter status tidak valid"})
    return parts


def parse_page(page: int, page_size: int) -> Tuple[int, int]:
    page_n = max(1, int(page or 1))
    size = int(page_size or DEFAULT_PAGE_SIZE)
    if size < 1 or size > MAX_PAGE_SIZE:
        raise HTTPException(400, {"code": "PAGE_SIZE_INVALID", "detail": f"page_size harus 1–{MAX_PAGE_SIZE}"})
    return page_n, size


def normalize_search(raw: Optional[str]) -> str:
    text = str(raw or "").strip()
    if len(text) > MAX_SEARCH_LEN:
        raise HTTPException(400, {"code": "SEARCH_TOO_LONG", "detail": f"Pencarian maksimal {MAX_SEARCH_LEN} karakter"})
    return text


def _customer_name(order: Optional[dict]) -> str:
    if not order:
        return ""
    return str(order.get("customer") or order.get("customer_name") or "").strip()


def _branch_id(order: Optional[dict]) -> str:
    if not order:
        return ""
    return str(order.get("operational_branch_id") or order.get("branch_id") or "").strip()


def _branch_name(order: Optional[dict], names: Dict[str, str]) -> str:
    if not order:
        return ""
    bid = _branch_id(order)
    if bid and names.get(bid):
        return names[bid]
    for key in ("branch_name", "cabang", "outlet"):
        value = str(order.get(key) or "").strip()
        if value:
            return value
    return bid


def _scope_on_order(scope: dict) -> dict:
    """Rewrite top-level order scope fragments onto the joined ``order`` document."""
    if not scope:
        return {}

    def rewrite(node: Any) -> Any:
        if isinstance(node, dict):
            out: Dict[str, Any] = {}
            for key, value in node.items():
                if key.startswith("$"):
                    out[key] = rewrite(value)
                elif key in ("operational_branch_id", "branch_id", "sales_user_id", "sales_owner_id"):
                    out[f"order.{key}"] = rewrite(value)
                else:
                    out[key] = rewrite(value)
            return out
        if isinstance(node, list):
            return [rewrite(item) for item in node]
        return node

    return rewrite(scope)


def _public_history(entries: Optional[Sequence[dict]]) -> List[dict]:
    out: List[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        out.append({
            "action": entry.get("action"),
            "at": entry.get("at"),
            "by_name": entry.get("by_name"),
            "note": entry.get("note") or "",
        })
    return out


def serialize_row(doc: dict, *, branch_names: Dict[str, str]) -> dict:
    order = doc.get("order") if isinstance(doc.get("order"), dict) else None
    return {
        "id": doc.get("id"),
        "order_id": doc.get("order_id") or (order or {}).get("id"),
        "po_number": doc.get("po_number") or (order or {}).get("po_number") or "",
        "customer_name": _customer_name(order),
        "branch_id": _branch_id(order),
        "branch_name": _branch_name(order, branch_names),
        "requested_by_name": doc.get("requested_by_name") or "",
        "reason": doc.get("reason") or "",
        "notes": doc.get("notes") or "",
        "target_completion_date": doc.get("target_completion_date") or "",
        "status": str(doc.get("status") or "").lower(),
        "requested_at": doc.get("requested_at") or doc.get("created_at") or "",
        "decided_at": doc.get("decided_at") or "",
        "decided_by_name": doc.get("decided_by_name") or "",
        "decision_notes": doc.get("decision_notes") or "",
        "history": _public_history(doc.get("history")),
    }


async def list_dashboard_urgent_requests(
    db,
    user: dict,
    *,
    status: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    can_open_po: bool = False,
    now_iso: Optional[str] = None,
) -> dict:
    from services.production_dashboard_http import order_access_scope

    statuses = parse_status_filter(status)
    page_n, size = parse_page(page, page_size)
    query_text = normalize_search(q)
    scope = await order_access_scope(db, user)

    match_req: Dict[str, Any] = {}
    if statuses is not None:
        match_req["status"] = {"$in": statuses}

    pipeline: List[dict] = [{"$match": match_req}] if match_req else [{"$match": {}}]
    pipeline.extend([
        {
            "$lookup": {
                "from": "orders",
                "localField": "order_id",
                "foreignField": "id",
                "as": "order",
                "pipeline": [{"$project": _ORDER_PROJECT}],
            }
        },
        {"$addFields": {"order": {"$arrayElemAt": ["$order", 0]}}},
        # Branch isolation: drop rows without a readable in-scope order.
        {"$match": {"order": {"$type": "object"}}},
    ])

    order_scope = _scope_on_order(scope)
    if order_scope:
        pipeline.append({"$match": order_scope})

    if query_text:
        rx = {"$regex": re.escape(query_text), "$options": "i"}
        pipeline.append({
            "$match": {
                "$or": [
                    {"po_number": rx},
                    {"order.po_number": rx},
                    {"order.customer": rx},
                    {"order.customer_name": rx},
                ]
            }
        })

    pipeline.extend([
        {"$sort": {"created_at": -1, "id": -1}},
        {
            "$facet": {
                "total": [{"$count": "n"}],
                "items": [
                    {"$skip": (page_n - 1) * size},
                    {"$limit": size},
                ],
            }
        },
    ])

    rows = await db[urg.COLLECTION].aggregate(pipeline).to_list(1)
    facet = rows[0] if rows else {"total": [], "items": []}
    total = int(((facet.get("total") or [{}])[0] or {}).get("n") or 0)
    docs = list(facet.get("items") or [])

    branch_ids = {_branch_id(doc.get("order")) for doc in docs if isinstance(doc.get("order"), dict)}
    branch_ids.discard("")
    names: Dict[str, str] = {}
    if branch_ids:
        branch_docs = await db.branches.find(
            {"id": {"$in": list(branch_ids)}},
            {"_id": 0, "id": 1, "name": 1},
        ).to_list(len(branch_ids) + 10)
        names = {
            str(doc.get("id")): str(doc.get("name") or doc.get("id"))
            for doc in branch_docs
            if doc.get("id")
        }

    items = [serialize_row(doc, branch_names=names) for doc in docs]
    if statuses is None:
        filter_status = STATUS_ALL
    elif set(statuses) == set(ACTIVE_STATUSES):
        filter_status = STATUS_ACTIVE
    elif len(statuses) == 1:
        filter_status = statuses[0]
    else:
        filter_status = ",".join(statuses)
    return {
        "status": "complete" if total or items else "empty",
        "items": items,
        "page": page_n,
        "page_size": size,
        "total": total,
        "filter": {"status": filter_status, "q": query_text},
        "can_open_po": bool(can_open_po),
        "generated_at": now_iso or urg.now_iso(),
    }
