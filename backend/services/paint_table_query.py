"""Shared sort/filter helpers for Sentralisasi Cat GET list endpoints.

Filter/sort are applied BEFORE pagination. Scope/RBAC must already be in the dataset.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def parse_sort(sort_by: Optional[str], sort_dir: Optional[str], allowed: Sequence[str], default: str) -> Tuple[str, int]:
    field = str(sort_by or "").strip()
    if field not in allowed:
        field = default
    direction = str(sort_dir or "desc").strip().lower()
    mongo_dir = 1 if direction in {"asc", "1", "ascending"} else -1
    return field, mongo_dir


def parse_header_filters(raw: Optional[Sequence[str]], allowed: Sequence[str]) -> List[Dict[str, str]]:
    """Parse hf entries: field:op:value (value may contain ':')."""
    out: List[Dict[str, str]] = []
    allowed_set = set(allowed)
    for item in raw or []:
        text = str(item or "").strip()
        if not text:
            continue
        parts = text.split(":", 2)
        if len(parts) < 3:
            continue
        field, op, value = parts[0].strip(), parts[1].strip().lower(), parts[2]
        if field not in allowed_set or not op:
            continue
        out.append({"field": field, "op": op, "value": value})
    return out


def _as_number(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def row_matches_filters(row: Dict[str, Any], filters: Sequence[Dict[str, str]]) -> bool:
    for f in filters:
        field, op, raw = f["field"], f["op"], f["value"]
        cell = row.get(field)
        if op in {"contains", "text"}:
            if raw.lower() not in str(cell or "").lower():
                return False
        elif op in {"eq", "enum"}:
            if str(cell or "") != str(raw):
                return False
        elif op in {"gte", "lte", "gt", "lt", "eq_num"}:
            left = _as_number(cell)
            right = _as_number(raw)
            if left is None or right is None:
                return False
            if op == "gte" and not (left >= right):
                return False
            if op == "lte" and not (left <= right):
                return False
            if op == "gt" and not (left > right):
                return False
            if op == "lt" and not (left < right):
                return False
            if op == "eq_num" and not (left == right):
                return False
        elif op == "date_gte":
            if str(cell or "") < str(raw):
                return False
        elif op == "date_lte":
            # allow date-only compare against ISO timestamps
            cell_s = str(cell or "")
            limit = str(raw)
            if "T" not in limit:
                limit = limit + "T23:59:59.999999"
            if cell_s > limit:
                return False
        else:
            return False
    return True


def apply_filters(rows: Iterable[Dict[str, Any]], filters: Sequence[Dict[str, str]]) -> List[Dict[str, Any]]:
    if not filters:
        return list(rows)
    return [r for r in rows if row_matches_filters(r, filters)]


def sort_rows(
    rows: List[Dict[str, Any]],
    *,
    sort_by: str,
    sort_dir: int,
    tie_break: str = "transaction_id",
) -> List[Dict[str, Any]]:
    reverse = sort_dir < 0

    def key(row: Dict[str, Any]):
        val = row.get(sort_by)
        num = _as_number(val)
        if num is not None and not isinstance(val, bool):
            primary: Any = (0, num)
        else:
            primary = (1, str(val or "").lower())
        tie = str(row.get(tie_break) or row.get("paint_id") or row.get("employee_user_id") or "")
        return (primary, tie)

    return sorted(rows, key=key, reverse=reverse)


def mongo_text_filter(field: str, value: str) -> Dict[str, Any]:
    return {field: {"$regex": str(value), "$options": "i"}}


def mongo_filters(filters: Sequence[Dict[str, str]], *, numeric_fields: Sequence[str]) -> Dict[str, Any]:
    """Convert header filters into a Mongo $and clause (plus caller scope)."""
    clauses: List[Dict[str, Any]] = []
    numeric = set(numeric_fields)
    for f in filters:
        field, op, raw = f["field"], f["op"], f["value"]
        if op in {"contains", "text"}:
            clauses.append(mongo_text_filter(field, raw))
        elif op in {"eq", "enum"}:
            clauses.append({field: raw})
        elif op in {"gte", "lte", "gt", "lt", "eq_num"} and field in numeric:
            num = _as_number(raw)
            if num is None:
                continue
            if op == "eq_num":
                clauses.append({field: num})
            else:
                clauses.append({field: {f"${op}": num}})
        elif op == "date_gte":
            clauses.append({field: {"$gte": raw}})
        elif op == "date_lte":
            limit = raw if "T" in raw else raw + "T23:59:59.999999"
            clauses.append({field: {"$lte": limit}})
    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}
