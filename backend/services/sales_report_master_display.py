"""Optional Master Sales display metadata for reports (read-only).

Financial attribution remains Accurate label → sales_user_mapping → users.id.
Master fields are informational only: never change grouping, totals, or buckets.

Match rules:
- Exact Accurate label ↔ sales_master.sales_name (strip only; no fuzzy).
- Optional explicit sales_master.user_id ↔ attributed sales_user_id.
- Conflicting masters → ambiguous (null id/name).
- Missing link → unmatched.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from services.sales_user_mapping import exact_sales_label

MATCH_MATCHED = "matched"
MATCH_UNMATCHED = "unmatched"
MATCH_AMBIGUOUS = "ambiguous"

_MASTER_PROJ = {"_id": 0, "id": 1, "sales_name": 1, "user_id": 1, "active": 1}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _split_labels(raw: Any) -> List[str]:
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        text = _text(raw)
        if not text:
            return []
        values = [part.strip() for part in text.split(",")]
    out: List[str] = []
    seen: Set[str] = set()
    for part in values:
        label = exact_sales_label(part)
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def resolve_master_display(
    *,
    labels: Iterable[Any],
    sales_user_id: Any = None,
    by_exact_name: Mapping[str, Mapping],
    by_user_id: Mapping[str, Sequence[Mapping]],
) -> Dict[str, Optional[str]]:
    """Return sales_master_id / sales_master_name / sales_master_match_status."""
    ids: Set[str] = set()
    names: Dict[str, str] = {}

    for label in _split_labels(labels):
        master = by_exact_name.get(label)
        if not master:
            continue
        mid = _text(master.get("id"))
        if not mid:
            continue
        ids.add(mid)
        names[mid] = _text(master.get("sales_name")) or label

    uid = _text(sales_user_id)
    if uid:
        linked = list(by_user_id.get(uid) or [])
        if len(linked) > 1:
            return {
                "sales_master_id": None,
                "sales_master_name": None,
                "sales_master_match_status": MATCH_AMBIGUOUS,
            }
        if len(linked) == 1:
            mid = _text(linked[0].get("id"))
            if mid:
                ids.add(mid)
                names[mid] = _text(linked[0].get("sales_name")) or names.get(mid) or ""

    if not ids:
        return {
            "sales_master_id": None,
            "sales_master_name": None,
            "sales_master_match_status": MATCH_UNMATCHED,
        }
    if len(ids) > 1:
        return {
            "sales_master_id": None,
            "sales_master_name": None,
            "sales_master_match_status": MATCH_AMBIGUOUS,
        }
    mid = next(iter(ids))
    return {
        "sales_master_id": mid,
        "sales_master_name": names.get(mid) or None,
        "sales_master_match_status": MATCH_MATCHED,
    }


async def load_master_display_indexes(
    db,
    *,
    labels: Iterable[Any],
    user_ids: Iterable[Any],
) -> Tuple[Dict[str, dict], Dict[str, List[dict]]]:
    """Batch-load Master Sales docs for exact names and explicit user links."""
    clean_labels = sorted({exact_sales_label(x) for x in labels if exact_sales_label(x)})
    clean_uids = sorted({_text(x) for x in user_ids if _text(x)})
    clauses: List[dict] = []
    if clean_labels:
        clauses.append({"sales_name": {"$in": clean_labels}})
    if clean_uids:
        clauses.append({"user_id": {"$in": clean_uids}})
    by_name: Dict[str, dict] = {}
    by_user: Dict[str, List[dict]] = {}
    if not clauses:
        return by_name, by_user

    cursor = db.sales_master.find({"$or": clauses}, _MASTER_PROJ)
    async for doc in cursor:
        if not isinstance(doc, dict):
            continue
        mid = _text(doc.get("id"))
        if not mid:
            continue
        name = exact_sales_label(doc.get("sales_name"))
        pub = {
            "id": mid,
            "sales_name": _text(doc.get("sales_name")),
            "user_id": _text(doc.get("user_id")) or None,
            "active": bool(doc.get("active", True)),
        }
        if name and name not in by_name:
            by_name[name] = pub
        uid = pub.get("user_id")
        if uid:
            by_user.setdefault(uid, []).append(pub)
    return by_name, by_user


async def attach_master_display_to_sales_rows(db, rows: Sequence[dict]) -> List[dict]:
    """Enrich penjualan-per-sales rows with optional Master fields (in place copy)."""
    label_pool: List[str] = []
    user_pool: List[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        label_pool.extend(_split_labels(row.get("label_mapping") or row.get("labels")))
        uid = _text(row.get("sales_user_id"))
        if uid:
            user_pool.append(uid)
    by_name, by_user = await load_master_display_indexes(
        db, labels=label_pool, user_ids=user_pool
    )
    out: List[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        enriched = dict(row)
        meta = resolve_master_display(
            labels=row.get("label_mapping") or row.get("labels") or [],
            sales_user_id=row.get("sales_user_id"),
            by_exact_name=by_name,
            by_user_id=by_user,
        )
        enriched.update(meta)
        out.append(enriched)
    return out


async def attach_master_display_to_dashboard_groups(
    db,
    groups: Sequence[dict],
) -> List[dict]:
    """Enrich dashboard sales groups; omzet/invoice_count/key unchanged."""
    label_pool: List[str] = []
    user_pool: List[str] = []
    for row in groups or []:
        if not isinstance(row, dict):
            continue
        label_pool.append(row.get("source_label") or "")
        key = _text(row.get("key"))
        if key.startswith("user:"):
            user_pool.append(key.split(":", 1)[1])
    by_name, by_user = await load_master_display_indexes(
        db, labels=label_pool, user_ids=user_pool
    )
    out: List[dict] = []
    for row in groups or []:
        if not isinstance(row, dict):
            continue
        enriched = dict(row)
        key = _text(row.get("key"))
        uid = key.split(":", 1)[1] if key.startswith("user:") else None
        meta = resolve_master_display(
            labels=[row.get("source_label") or ""],
            sales_user_id=uid,
            by_exact_name=by_name,
            by_user_id=by_user,
        )
        enriched.update(meta)
        out.append(enriched)
    return out
