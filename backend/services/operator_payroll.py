"""Operator payroll from actual confirmed Operator output (Analitik Output).

Rates are per operator (operator_user_id / operator_email), with optional
operator+product overrides. Does not invent rates. Historical rates: settings
history as-of production date, plus optional rate_per_pcs snapshot on
progress_entries.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from operator_analytics import (
    line_item_name,
    parse_iso_datetime_date,
    safe_int,
    wib_today,
)
from output_reports import iter_operator_export_rows

SETTINGS_KEY = "operator_wage_rates"

PAYROLL_EXPORT_COLUMNS = [
    ("tanggal", "Tanggal"),
    ("operator", "Nama Operator"),
    ("item_code", "Kode Item"),
    ("item_name", "Nama Produk"),
    ("qty_completed", "Output"),
    ("rate_per_pcs", "Rate / pcs"),
    ("total_upah", "Total Upah"),
]

RATE_HISTORY_STRATEGY = (
    "Payroll uses the effective rate as-of the production date from wage settings "
    "history (per operator_user_id, with operator+product override). "
    "Confirmed Operator progress entries also snapshot rate_per_pcs. "
    "Changing today's rate does not rewrite earlier payroll rows."
)


def parse_operator_ids(
    operator_id: Optional[str] = None,
    operator_ids: Optional[Any] = None,
) -> List[str]:
    """Stable operator ids (user id or email). Empty list = all operators."""
    raw: List[str] = []
    if operator_ids is not None:
        if isinstance(operator_ids, (list, tuple, set)):
            parts = list(operator_ids)
        else:
            parts = str(operator_ids).split(",")
        raw.extend(parts)
    if operator_id:
        raw.append(operator_id)
    seen = set()
    out: List[str] = []
    for item in raw:
        val = str(item or "").strip()
        if not val or val in ("all", "*"):
            continue
        if val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def item_identity(li: Optional[dict]) -> Dict[str, str]:
    li = li or {}
    return {
        "accurate_item_id": str(li.get("accurate_item_id") or "").strip(),
        "master_item_id": str(li.get("master_item_id") or li.get("id") or "").strip(),
        "item_code": str(li.get("item_code") or "").strip(),
        "item_name": str(li.get("name") or li.get("item_name") or "").strip(),
    }


def identity_key(ident: dict) -> str:
    aid = str(ident.get("accurate_item_id") or "").strip()
    if aid:
        return f"accurate:{aid}"
    mid = str(ident.get("master_item_id") or "").strip()
    if mid:
        return f"master:{mid}"
    return ""


def operator_identity(row: Optional[dict]) -> Dict[str, str]:
    row = row or {}
    return {
        "operator_user_id": str(
            row.get("operator_user_id") or row.get("user_id") or ""
        ).strip(),
        "operator_email": str(
            row.get("operator_email")
            or row.get("operator_id")
            or row.get("email")
            or row.get("employee_id")
            or ""
        ).strip(),
        "operator_name": str(row.get("operator_name") or row.get("name") or row.get("operator") or "").strip(),
    }


def _as_date(raw) -> Optional[date]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    text = str(raw).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return parse_iso_datetime_date(text)


def _num_rate(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


def _norm_operator_row(row: dict) -> Optional[dict]:
    ident = operator_identity(row)
    if not ident["operator_user_id"] and not ident["operator_email"]:
        return None
    return {
        **ident,
        "default_rate_per_pcs": _num_rate(row.get("default_rate_per_pcs") if "default_rate_per_pcs" in row else row.get("rate_per_pcs")),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


def _norm_override_row(row: dict) -> Optional[dict]:
    ident = item_identity(row)
    if not identity_key(ident):
        return None
    op = operator_identity(row)
    return {
        **ident,
        "operator_user_id": op["operator_user_id"],
        "operator_email": op["operator_email"],
        "rate_per_pcs": _num_rate(row.get("rate_per_pcs")),
        "active": row.get("active") is not False,
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


def normalize_wage_settings(doc: Optional[dict]) -> dict:
    doc = doc or {}
    operators = []
    raw_ops = doc.get("operators") or []
    if isinstance(raw_ops, dict):
        raw_ops = [
            {**(v if isinstance(v, dict) else {"default_rate_per_pcs": v}), "operator_user_id": k}
            for k, v in raw_ops.items()
        ]
    for row in raw_ops:
        if not isinstance(row, dict):
            continue
        norm = _norm_operator_row(row)
        if norm:
            operators.append(norm)
    overrides = []
    raw = doc.get("overrides") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    for row in raw:
        if not isinstance(row, dict):
            continue
        norm = _norm_override_row(row)
        if norm:
            overrides.append(norm)
    history = [h for h in (doc.get("history") or []) if isinstance(h, dict)]
    return {
        "key": SETTINGS_KEY,
        "default_rate_per_pcs": _num_rate(doc.get("default_rate_per_pcs")),
        "operators": operators,
        "overrides": overrides,
        "history": history,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


def _ids_equal(a: Optional[str], b: Optional[str]) -> bool:
    x, y = (a or "").strip(), (b or "").strip()
    if not x or not y:
        return False
    return x == y or x.lower() == y.lower()


def _operator_matches(stored: dict, ident: dict) -> bool:
    uid = (ident.get("operator_user_id") or "").strip()
    email = (ident.get("operator_email") or "").strip()
    su = (stored.get("operator_user_id") or "").strip()
    se = (stored.get("operator_email") or "").strip()
    if uid and su and _ids_equal(uid, su):
        return True
    if email and se and _ids_equal(email, se):
        return True
    if uid and se and _ids_equal(uid, se):
        return True
    if email and su and _ids_equal(email, su):
        return True
    return False


def find_operator_rate(operators: Sequence[dict], ident: Optional[dict]) -> Optional[dict]:
    ident = ident or {}
    for row in operators or []:
        if _operator_matches(row, ident):
            return row
    return None


def _override_matches_operator(row: dict, ident: dict) -> bool:
    scoped = bool((row.get("operator_user_id") or "").strip() or (row.get("operator_email") or "").strip())
    if not scoped:
        return True
    return _operator_matches(row, ident)


def _rate_from_state(
    ident: dict,
    default_rate: Optional[float],
    overrides: Sequence[dict],
    operator_ident: Optional[dict] = None,
    operators: Optional[Sequence[dict]] = None,
) -> Tuple[Optional[float], str]:
    """Return (rate, source). Never invents a number."""
    op_ident = operator_identity(operator_ident)
    ops = list(operators or [])
    op_row = find_operator_rate(ops, op_ident) if ops else None
    op_default = _num_rate(op_row.get("default_rate_per_pcs")) if op_row else None
    key = identity_key(ident)
    scoped_override = None
    legacy_override = None
    if key:
        for row in overrides or []:
            if not row.get("active", True):
                continue
            if identity_key(row) != key:
                continue
            r = _num_rate(row.get("rate_per_pcs"))
            if r is None:
                continue
            scoped = bool((row.get("operator_user_id") or "").strip() or (row.get("operator_email") or "").strip())
            if scoped and _operator_matches(row, op_ident):
                scoped_override = r
                break
            if not scoped:
                legacy_override = r
        if scoped_override is not None:
            return scoped_override, "product_override"
        if legacy_override is not None and not ops:
            return legacy_override, "product_override"
        if scoped_override is None and legacy_override is not None and op_row is None and not ops:
            return legacy_override, "product_override"
    if op_default is not None:
        return op_default, "operator_default"
    if not ops:
        if legacy_override is not None:
            return legacy_override, "product_override"
        if default_rate is not None:
            return default_rate, "default"
    return None, "unset"


def _snap_sort_key(snap: dict):
    d = _as_date(snap.get("effective_from") or snap.get("at")) or date.min
    return d


def effective_rate_as_of(
    ident: dict,
    as_of: Optional[date],
    settings: dict,
    operator: Optional[dict] = None,
) -> Optional[float]:
    """Rate as-of production date. A later rate edit does not rewrite earlier days."""
    rate, _source = effective_rate_detail_as_of(ident, as_of, settings, operator)
    return rate


def effective_rate_detail_as_of(
    ident: dict,
    as_of: Optional[date],
    settings: dict,
    operator: Optional[dict] = None,
) -> Tuple[Optional[float], str]:
    settings = normalize_wage_settings(settings)
    snaps = sorted(settings.get("history") or [], key=_snap_sort_key)
    chosen = None
    for snap in snaps:
        start = _as_date(snap.get("effective_from") or snap.get("at"))
        if as_of and start and start <= as_of:
            chosen = snap
    if chosen is None and snaps:
        chosen = snaps[0]
    state = chosen if chosen else settings
    return _rate_from_state(
        ident,
        _num_rate(state.get("default_rate_per_pcs")),
        state.get("overrides") or [],
        operator,
        state.get("operators") or [],
    )


def _state_tuple(default_rate, operators, overrides):
    ops = tuple(
        (
            (o.get("operator_user_id") or ""),
            (o.get("operator_email") or ""),
            o.get("default_rate_per_pcs"),
        )
        for o in (operators or [])
    )
    rows = tuple(
        (
            identity_key(o),
            o.get("operator_user_id") or "",
            o.get("operator_email") or "",
            o.get("rate_per_pcs"),
            o.get("active", True),
        )
        for o in (overrides or [])
    )
    return (default_rate, ops, rows)


def snapshot_on_save(previous: Optional[dict], incoming: dict, user: dict, now_iso: str, today: Optional[date] = None) -> dict:
    """Append history when rates change so later edits do not rewrite old payroll."""
    prev = normalize_wage_settings(previous)
    nxt = normalize_wage_settings({
        **prev,
        **(incoming or {}),
        "history": prev.get("history") or [],
    })
    day = today or wib_today()
    new_snap = {
        "effective_from": day.isoformat(),
        "at": now_iso,
        "by": (user or {}).get("email"),
        "default_rate_per_pcs": nxt.get("default_rate_per_pcs"),
        "operators": [
            {
                "operator_user_id": o.get("operator_user_id"),
                "operator_email": o.get("operator_email"),
                "operator_name": o.get("operator_name"),
                "default_rate_per_pcs": o.get("default_rate_per_pcs"),
            }
            for o in (nxt.get("operators") or [])
        ],
        "overrides": [
            {
                **o,
                "rate_per_pcs": o.get("rate_per_pcs"),
                "active": o.get("active", True),
            }
            for o in (nxt.get("overrides") or [])
        ],
    }
    history = list(prev.get("history") or [])
    last = history[-1] if history else None
    last_state = _state_tuple(
        last.get("default_rate_per_pcs") if last else None,
        last.get("operators") if last else None,
        last.get("overrides") if last else None,
    ) if last else None
    new_state = _state_tuple(
        new_snap.get("default_rate_per_pcs"),
        new_snap.get("operators"),
        new_snap.get("overrides"),
    )
    if last_state != new_state:
        history.append(new_snap)
    nxt["history"] = history
    nxt["updated_at"] = now_iso
    nxt["updated_by"] = (user or {}).get("email")
    return nxt


def merge_operators_for_settings(users: Sequence[dict], settings: Optional[dict]) -> List[dict]:
    """Display table: all active operators + stored rates. Identity is user id, not name."""
    settings = normalize_wage_settings(settings)
    stored = list(settings.get("operators") or [])
    out: List[dict] = []
    seen = set()
    for u in users or []:
        ident = {
            "operator_user_id": str(u.get("id") or u.get("operator_user_id") or "").strip(),
            "operator_email": str(u.get("email") or "").strip(),
            "operator_name": str(u.get("name") or u.get("email") or "").strip(),
        }
        if not ident["operator_user_id"] and not ident["operator_email"]:
            continue
        row = find_operator_rate(stored, ident) or {}
        key = ident["operator_user_id"] or ident["operator_email"]
        seen.add(key)
        if ident["operator_email"]:
            seen.add(ident["operator_email"])
        out.append({
            "operator_user_id": ident["operator_user_id"] or row.get("operator_user_id") or "",
            "operator_email": ident["operator_email"] or row.get("operator_email") or "",
            "operator_name": ident["operator_name"] or row.get("operator_name") or "",
            "default_rate_per_pcs": row.get("default_rate_per_pcs"),
            "updated_at": row.get("updated_at") or settings.get("updated_at"),
            "updated_by": row.get("updated_by") or settings.get("updated_by"),
        })
    for row in stored:
        key = (row.get("operator_user_id") or row.get("operator_email") or "").strip()
        if key and key not in seen:
            out.append(row)
            seen.add(key)
    out.sort(key=lambda r: (r.get("operator_name") or r.get("operator_email") or "").lower())
    return out


def _line_for(order: dict, line_idx) -> dict:
    lis = order.get("line_items") or []
    try:
        idx = int(line_idx)
    except (TypeError, ValueError):
        return {}
    if 0 <= idx < len(lis):
        return lis[idx]
    return {}


def _split_for_row(order: dict, row: dict) -> dict:
    email = (row.get("operator_id") or row.get("operator") or "").strip()
    uid = (row.get("operator_user_id") or "").strip()
    line_idx = row.get("line_id")
    for split in order.get("operator_splits") or []:
        if split.get("line_idx") != line_idx and str(split.get("line_idx")) != str(line_idx):
            continue
        if uid and _ids_equal(str(split.get("operator_user_id") or ""), uid):
            return split
        if email and _ids_equal(str(split.get("operator_email") or ""), email):
            return split
    return {}


def _snapshot_rate_from_entries(order: dict, row: dict) -> Optional[float]:
    """If progress_entries on that day have rate_per_pcs, use qty-weighted average."""
    day = _as_date(row.get("tanggal") or row.get("date"))
    if not day:
        return None
    split = _split_for_row(order, row)
    splits = [split] if split else (order.get("operator_splits") or [])
    email = (row.get("operator_id") or row.get("operator") or "").strip()
    line_idx = row.get("line_id")
    weighted = 0.0
    qty_n = 0
    for s in splits:
        if split:
            pass
        else:
            if (s.get("operator_email") or "").strip() != email:
                continue
            if s.get("line_idx") != line_idx and str(s.get("line_idx")) != str(line_idx):
                continue
        for e in s.get("progress_entries") or []:
            if e.get("cancelled") or e.get("replaced"):
                continue
            ed = _as_date(e.get("created_at") or e.get("confirmed_at"))
            if ed != day:
                continue
            rate = _num_rate(e.get("rate_per_pcs"))
            q = safe_int(e.get("qty"))
            if rate is None or q <= 0:
                continue
            weighted += rate * q
            qty_n += q
    if qty_n <= 0:
        return None
    return weighted / qty_n


def compute_operator_payroll(
    orders: Iterable[dict],
    settings: Optional[dict],
    *,
    date_from: date,
    date_to: date,
    operator_id: Optional[str] = None,
    operator_ids: Optional[Sequence[str]] = None,
    item: Optional[str] = None,
) -> dict:
    settings = normalize_wage_settings(settings)
    ids = parse_operator_ids(operator_id, operator_ids)
    order_list = [o for o in orders if not o.get("cancelled")]
    by_id = {o.get("id") or o.get("po_number"): o for o in order_list}
    raw_rows = iter_operator_export_rows(
        order_list,
        date_from=date_from,
        date_to=date_to,
        operator_id=ids[0] if len(ids) == 1 else None,
        operator_ids=ids or None,
        item=item,
    )
    rows: List[dict] = []
    for r in raw_rows:
        order = by_id.get(r.get("order_id")) or {}
        ident = item_identity(_line_for(order, r.get("line_id")))
        if not ident.get("item_name"):
            ident["item_name"] = r.get("item_name") or r.get("cup_item") or line_item_name(order, r.get("line_id"))
        split = _split_for_row(order, r)
        op_ident = operator_identity({
            **r,
            "operator_user_id": r.get("operator_user_id") or split.get("operator_user_id"),
            "operator_email": split.get("operator_email") or r.get("operator_id") or r.get("employee_id"),
            "operator_name": split.get("operator_name") or r.get("operator"),
        })
        as_of = _as_date(r.get("tanggal") or r.get("date"))
        snap_rate = _snapshot_rate_from_entries(order, r)
        if snap_rate is not None:
            rate, source = snap_rate, "entry_snapshot"
        else:
            rate, source = effective_rate_detail_as_of(ident, as_of, settings, op_ident)
        qty = max(0, safe_int(r.get("qty_completed") if r.get("qty_completed") is not None else r.get("qty")))
        rate_n = float(rate) if rate is not None else None
        total = (qty * rate_n) if rate_n is not None else 0.0
        rows.append({
            **r,
            "operator_user_id": op_ident.get("operator_user_id") or "",
            "item_code": ident.get("item_code") or "",
            "item_name": ident.get("item_name") or r.get("item_name") or "",
            "accurate_item_id": ident.get("accurate_item_id") or "",
            "master_item_id": ident.get("master_item_id") or "",
            "qty_completed": qty,
            "rate_per_pcs": rate_n if rate_n is not None else "",
            "total_upah": total,
            "rate_source": source,
            "rate_unset": rate_n is None,
        })
    people = []
    acc: Dict[str, dict] = {}
    for r in rows:
        eid = r.get("operator_user_id") or r.get("operator_id") or r.get("employee_id") or ""
        rec = acc.setdefault(eid, {
            "id": eid,
            "operator_user_id": r.get("operator_user_id") or "",
            "email": r.get("operator_id") or eid,
            "name": r.get("operator") or eid,
            "qty": 0,
            "total_upah": 0.0,
        })
        if r.get("operator"):
            rec["name"] = r["operator"]
        rec["qty"] += r["qty_completed"]
        rec["total_upah"] += r["total_upah"] if isinstance(r["total_upah"], (int, float)) else 0
    people = sorted(acc.values(), key=lambda x: (x.get("name") or x.get("email") or ""))
    return {
        "rows": rows,
        "people": people,
        "kpis": {
            "output": sum(r["qty_completed"] for r in rows),
            "total_upah": sum(r["total_upah"] if isinstance(r["total_upah"], (int, float)) else 0 for r in rows),
            "operator_count": len(people),
        },
        "settings": {
            "default_rate_per_pcs": settings.get("default_rate_per_pcs"),
            "operators": settings.get("operators") or [],
        },
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "rate_history_strategy": RATE_HISTORY_STRATEGY,
        "source": "iter_operator_export_rows (operator_splits.qty_completed / timeline deltas)",
        "read_only": True,
        "filter": {"operator_ids": ids},
    }


def operators_and_items(orders: Iterable[dict]) -> Tuple[List[dict], List[dict]]:
    ops: Dict[str, dict] = {}
    items: Dict[str, dict] = {}
    for order in orders:
        if order.get("cancelled"):
            continue
        for s in order.get("operator_splits") or []:
            email = (s.get("operator_email") or "").strip()
            uid = (s.get("operator_user_id") or "").strip()
            key = uid or email
            if not key:
                continue
            rec = ops.setdefault(key, {
                "id": key,
                "operator_user_id": uid,
                "email": email,
                "name": s.get("operator_name") or email or uid,
            })
            if s.get("operator_name"):
                rec["name"] = s["operator_name"]
            if uid:
                rec["operator_user_id"] = uid
            if email:
                rec["email"] = email
        for li in order.get("line_items") or []:
            ident = item_identity(li)
            key = identity_key(ident)
            if key:
                items[key] = ident
    return (
        sorted(ops.values(), key=lambda x: (x.get("name") or x.get("email") or x.get("id") or "")),
        list(items.values()),
    )
