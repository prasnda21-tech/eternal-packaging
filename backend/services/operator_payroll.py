"""Operator payroll from actual confirmed Operator output (Analitik Output).

Rates are per operator (operator_user_id / operator_email), with optional
operator+product rules. Does not invent rates. Historical rates: settings
history as-of production date, plus optional rate_per_pcs snapshot on
progress_entries.

Formula:
  BASE WAGE        = confirmed qty × effective base rate
  PRODUCT ADDITION = confirmed qty for special products × additional rate
  BONUS            = configured bonus rule (per operator, selected period)
  TOTAL            = BASE + PRODUCT ADDITION + BONUS

Product rules:
  rate_type=override (legacy)  → absolute replacement rate (preserved)
  rate_type=addition (new)     → added on top of base wage
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

BONUS_ONCE = "once_per_period"
BONUS_BLOCK = "per_target_block"
BONUS_MODES = (BONUS_ONCE, BONUS_BLOCK)

RATE_OVERRIDE = "override"
RATE_ADDITION = "addition"

PAYROLL_EXPORT_COLUMNS = [
    ("tanggal", "Tanggal"),
    ("operator", "Nama Operator"),
    ("item_code", "Kode Item"),
    ("item_name", "Nama Produk"),
    ("qty_completed", "Output"),
    ("rate_per_pcs", "Rate / pcs"),
    ("base_wage", "Upah Dasar"),
    ("product_addition", "Tambahan Produk"),
    ("total_upah", "Total Baris"),
]

PAYROLL_REKAP_COLUMNS = [
    ("name", "Nama Operator"),
    ("qty", "Total Output"),
    ("base_wage", "Upah Dasar"),
    ("product_addition", "Tambahan Produk"),
    ("bonus", "Bonus"),
    ("total_upah", "Total Upah"),
]

RATE_HISTORY_STRATEGY = (
    "Payroll uses the effective rate as-of the production date from wage settings "
    "history (per operator_user_id). Product override (legacy absolute rate) is "
    "preserved; new product rules are additions on top of base. Bonus is per "
    "operator for the selected period. Confirmed Operator progress entries also "
    "snapshot rate_per_pcs. Changing today's rate does not rewrite earlier payroll rows."
)

PAYROLL_PRECEDENCE = (
    "Base: operator-specific base rate, else existing global default_rate_per_pcs, "
    "else RATE BELUM DISETEL. "
    "Product: operator+product override if present (absolute, legacy), else unscoped "
    "legacy override, else operator+product addition, else global product addition. "
    "Override and addition are never combined on the same row."
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


def _num_positive(v) -> Optional[float]:
    n = _num_rate(v)
    if n is None or n <= 0:
        return None
    return n


def _rate_type(raw) -> str:
    val = str(raw or RATE_OVERRIDE).strip().lower()
    if val in (RATE_ADDITION, "add", "tambahan"):
        return RATE_ADDITION
    return RATE_OVERRIDE


def _bonus_mode(raw) -> str:
    val = str(raw or BONUS_ONCE).strip().lower()
    if val in (BONUS_BLOCK, "block", "kelipatan"):
        return BONUS_BLOCK
    return BONUS_ONCE


def _norm_operator_row(row: dict) -> Optional[dict]:
    ident = operator_identity(row)
    if not ident["operator_user_id"] and not ident["operator_email"]:
        return None
    ef = _as_date(row.get("effective_from"))
    return {
        **ident,
        "default_rate_per_pcs": _num_rate(row.get("default_rate_per_pcs") if "default_rate_per_pcs" in row else row.get("rate_per_pcs")),
        "effective_from": ef.isoformat() if ef else (str(row.get("effective_from") or "").strip() or None),
        "active": row.get("active") is not False,
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


def _norm_override_row(row: dict) -> Optional[dict]:
    ident = item_identity(row)
    if not identity_key(ident):
        return None
    op = operator_identity(row)
    ef = _as_date(row.get("effective_from"))
    return {
        **ident,
        "operator_user_id": op["operator_user_id"],
        "operator_email": op["operator_email"],
        "rate_per_pcs": _num_rate(row.get("rate_per_pcs")),
        "rate_type": _rate_type(row.get("rate_type")),
        "active": row.get("active") is not False,
        "effective_from": ef.isoformat() if ef else (str(row.get("effective_from") or "").strip() or None),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


def _norm_addition_row(row: dict) -> Optional[dict]:
    ident = item_identity(row)
    if not identity_key(ident):
        return None
    op = operator_identity(row)
    rate = _num_rate(row.get("additional_rate_per_pcs") if "additional_rate_per_pcs" in row else row.get("rate_per_pcs"))
    ef = _as_date(row.get("effective_from"))
    return {
        **ident,
        "operator_user_id": op["operator_user_id"],
        "operator_email": op["operator_email"],
        "additional_rate_per_pcs": rate,
        "rate_type": RATE_ADDITION,
        "active": row.get("active") is not False,
        "effective_from": ef.isoformat() if ef else (str(row.get("effective_from") or "").strip() or None),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


def _norm_bonus(raw) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    target = _num_positive(raw.get("target_qty") if "target_qty" in raw else raw.get("target"))
    amount = _num_rate(raw.get("amount") if "amount" in raw else raw.get("bonus_amount"))
    if target is None and amount is None and not raw.get("mode") and not raw.get("effective_from"):
        if raw.get("active") is not False and not raw:
            return None
    ef = _as_date(raw.get("effective_from"))
    active = raw.get("active")
    if target is None and amount is None and not raw.get("mode"):
        if active is None:
            return None
    return {
        "target_qty": target,
        "amount": amount,
        "mode": _bonus_mode(raw.get("mode")),
        "effective_from": ef.isoformat() if ef else (str(raw.get("effective_from") or "").strip() or None),
        "active": active is not False,
        "updated_at": raw.get("updated_at"),
        "updated_by": raw.get("updated_by"),
    }


def _norm_version_list(raw, norm_fn) -> List[dict]:
    out = []
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        norm = norm_fn(row)
        if not norm:
            continue
        if row.get("at"):
            norm["at"] = row.get("at")
        if row.get("by"):
            norm["by"] = row.get("by")
        out.append(norm)
    return out


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
    additions = []
    raw_add = doc.get("product_additions") or []
    if isinstance(raw_add, dict):
        raw_add = list(raw_add.values())
    for row in raw_add:
        if not isinstance(row, dict):
            continue
        # Never silently convert historical override records.
        if _rate_type(row.get("rate_type")) == RATE_OVERRIDE and "additional_rate_per_pcs" not in row:
            continue
        norm = _norm_addition_row(row)
        if norm:
            additions.append(norm)
    history = [h for h in (doc.get("history") or []) if isinstance(h, dict)]
    return {
        "key": SETTINGS_KEY,
        "default_rate_per_pcs": _num_rate(doc.get("default_rate_per_pcs")),
        "operators": operators,
        "overrides": overrides,
        "product_additions": additions,
        "bonus": _norm_bonus(doc.get("bonus")),
        "operator_rate_versions": _norm_version_list(doc.get("operator_rate_versions"), _norm_operator_row),
        "bonus_versions": _norm_version_list(doc.get("bonus_versions"), _norm_bonus),
        "product_addition_versions": _norm_version_list(doc.get("product_addition_versions"), _norm_addition_row),
        "history": history,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "precedence": PAYROLL_PRECEDENCE,
    }


def validate_wage_settings(doc: Optional[dict]) -> Optional[str]:
    """Backend-authoritative validation. None = ok."""
    data = doc or {}
    for row in data.get("operators") or []:
        if not isinstance(row, dict):
            continue
        raw = row.get("default_rate_per_pcs") if "default_rate_per_pcs" in row else row.get("rate_per_pcs")
        if raw is None or raw == "":
            pass
        else:
            try:
                n = float(raw)
            except (TypeError, ValueError):
                return "Upah dasar / pcs tidak valid"
            if n < 0:
                return "Upah dasar / pcs tidak boleh negatif"
        ef = row.get("effective_from")
        if ef not in (None, "") and _as_date(ef) is None:
            return "Tanggal berlaku upah dasar tidak valid"
        ident = operator_identity(row)
        if not ident["operator_user_id"] and not ident["operator_email"]:
            return "Identitas Operator wajib (operator_user_id)"
    bonus = data.get("bonus")
    if isinstance(bonus, dict) and any(
        bonus.get(k) not in (None, "") for k in ("target_qty", "target", "amount", "bonus_amount", "mode", "effective_from")
    ):
        t_raw = bonus.get("target_qty") if "target_qty" in bonus else bonus.get("target")
        a_raw = bonus.get("amount") if "amount" in bonus else bonus.get("bonus_amount")
        if t_raw not in (None, ""):
            try:
                t = float(t_raw)
            except (TypeError, ValueError):
                return "Target produksi bonus tidak valid"
            if t <= 0:
                return "Target produksi bonus harus lebih dari 0"
        if a_raw not in (None, ""):
            try:
                a = float(a_raw)
            except (TypeError, ValueError):
                return "Nilai bonus tidak valid"
            if a < 0:
                return "Nilai bonus tidak boleh negatif"
        mode = bonus.get("mode")
        if mode not in (None, "", BONUS_ONCE, BONUS_BLOCK, "block", "kelipatan"):
            return "Mode bonus tidak valid"
        if bonus.get("effective_from") not in (None, "") and _as_date(bonus.get("effective_from")) is None:
            return "Tanggal berlaku bonus tidak valid"
        if (t_raw not in (None, "") and a_raw in (None, "")) or (a_raw not in (None, "") and t_raw in (None, "")):
            return "Target produksi dan nilai bonus harus diisi bersama"
    for row in data.get("product_additions") or []:
        if not isinstance(row, dict):
            continue
        raw = row.get("additional_rate_per_pcs") if "additional_rate_per_pcs" in row else row.get("rate_per_pcs")
        if raw not in (None, ""):
            try:
                n = float(raw)
            except (TypeError, ValueError):
                return "Tambahan upah produk tidak valid"
            if n < 0:
                return "Tambahan upah produk tidak boleh negatif"
        if not identity_key(item_identity(row)):
            return "Produk khusus wajib memakai ID produk yang stabil"
        if row.get("effective_from") not in (None, "") and _as_date(row.get("effective_from")) is None:
            return "Tanggal berlaku tambahan produk tidak valid"
    for row in data.get("overrides") or []:
        if not isinstance(row, dict):
            continue
        raw = row.get("rate_per_pcs")
        if raw not in (None, ""):
            try:
                n = float(raw)
            except (TypeError, ValueError):
                return "Rate override produk tidak valid"
            if n < 0:
                return "Rate override produk tidak boleh negatif"
    return None


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


def _pick_latest_version(versions: Sequence[dict], as_of: Optional[date], match_fn) -> Optional[dict]:
    chosen = None
    chosen_start = None
    chosen_at = ""
    for row in versions or []:
        if not match_fn(row):
            continue
        start = _as_date(row.get("effective_from") or row.get("at"))
        if start is None:
            continue
        if as_of and start > as_of:
            continue
        at = str(row.get("at") or "")
        if (
            chosen is None
            or (chosen_start is not None and start > chosen_start)
            or (start == chosen_start and at >= chosen_at)
        ):
            chosen = row
            chosen_start = start
            chosen_at = at
    return chosen


def _operator_rate_from_versions(settings: dict, ident: dict, as_of: Optional[date]) -> Optional[float]:
    versions = settings.get("operator_rate_versions") or []
    if not versions:
        return None
    row = _pick_latest_version(versions, as_of, lambda v: _operator_matches(v, ident) and v.get("active") is not False)
    if not row:
        return None
    return _num_rate(row.get("default_rate_per_pcs"))


def _additions_from_state(state: dict) -> List[dict]:
    return list(state.get("product_additions") or [])


def _find_product_rule(
    rows: Sequence[dict],
    ident: dict,
    op_ident: dict,
    *,
    addition: bool,
) -> Optional[dict]:
    key = identity_key(ident)
    if not key:
        return None
    scoped = None
    unscoped = None
    for row in rows or []:
        if row.get("active") is False:
            continue
        if identity_key(row) != key:
            continue
        if addition:
            rate = _num_rate(row.get("additional_rate_per_pcs") if "additional_rate_per_pcs" in row else row.get("rate_per_pcs"))
        else:
            if _rate_type(row.get("rate_type")) == RATE_ADDITION:
                continue
            rate = _num_rate(row.get("rate_per_pcs"))
        if rate is None:
            continue
        is_scoped = bool((row.get("operator_user_id") or "").strip() or (row.get("operator_email") or "").strip())
        if is_scoped and _operator_matches(row, op_ident):
            scoped = row
            break
        if not is_scoped:
            unscoped = row
    return scoped or unscoped


def _rate_from_state(
    ident: dict,
    default_rate: Optional[float],
    overrides: Sequence[dict],
    operator_ident: Optional[dict] = None,
    operators: Optional[Sequence[dict]] = None,
) -> Tuple[Optional[float], str]:
    """Return (rate, source). Never invents a number. Legacy single-rate lookup."""
    parts = _parts_from_state(ident, default_rate, overrides, operator_ident, operators, additions=None)
    if parts["unit_base"] is not None:
        total = parts["unit_base"] + (parts["unit_addition"] or 0)
        return total, parts["source"]
    return None, "unset"


def _parts_from_state(
    ident: dict,
    default_rate: Optional[float],
    overrides: Sequence[dict],
    operator_ident: Optional[dict] = None,
    operators: Optional[Sequence[dict]] = None,
    additions: Optional[Sequence[dict]] = None,
    version_base: Optional[float] = None,
) -> Dict[str, Any]:
    op_ident = operator_identity(operator_ident)
    ops = list(operators or [])
    op_row = find_operator_rate(ops, op_ident) if ops else None
    op_default = _num_rate(op_row.get("default_rate_per_pcs")) if op_row else None
    if version_base is not None:
        op_default = version_base

    override_row = _find_product_rule(overrides or [], ident, op_ident, addition=False)
    addition_row = _find_product_rule(additions or [], ident, op_ident, addition=True)

    base = op_default
    base_source = "operator_default" if op_default is not None else None
    if base is None and default_rate is not None:
        base = default_rate
        base_source = "default"

    if override_row is not None:
        rate = _num_rate(override_row.get("rate_per_pcs"))
        scoped = bool((override_row.get("operator_user_id") or "").strip() or (override_row.get("operator_email") or "").strip())
        return {
            "unit_base": rate,
            "unit_addition": 0.0,
            "rate_unset": rate is None,
            "source": "product_override",
            "base_source": "product_override",
            "addition_source": None,
            "override_scoped": scoped,
        }

    add_rate = _num_rate(
        addition_row.get("additional_rate_per_pcs") if addition_row else None
    ) if addition_row else None

    if base is None:
        return {
            "unit_base": None,
            "unit_addition": 0.0,
            "rate_unset": True,
            "source": "unset",
            "base_source": "unset",
            "addition_source": None,
        }

    return {
        "unit_base": base,
        "unit_addition": add_rate if add_rate is not None else 0.0,
        "rate_unset": False,
        "source": "product_addition" if add_rate else base_source,
        "base_source": base_source,
        "addition_source": "product_addition" if add_rate is not None else None,
    }


def _snap_sort_key(snap: dict):
    d = _as_date(snap.get("effective_from") or snap.get("at")) or date.min
    at = str(snap.get("at") or "")
    return (d, at)


def _history_state_as_of(settings: dict, as_of: Optional[date]) -> dict:
    settings = normalize_wage_settings(settings)
    snaps = sorted(settings.get("history") or [], key=_snap_sort_key)
    chosen = None
    for snap in snaps:
        start = _as_date(snap.get("effective_from") or snap.get("at"))
        if as_of and start and start <= as_of:
            chosen = snap
        elif not as_of:
            chosen = snap
    if chosen is None and snaps:
        # No snapshot is on/before as_of — do not use a later snap.
        if as_of:
            return {
                "default_rate_per_pcs": None,
                "operators": [],
                "overrides": [],
                "product_additions": [],
                "bonus": None,
            }
        chosen = snaps[0]
    return chosen if chosen else settings


def effective_rate_as_of(
    ident: dict,
    as_of: Optional[date],
    settings: dict,
    operator: Optional[dict] = None,
) -> Optional[float]:
    """Unit wage as-of production date (override, or base + addition)."""
    rate, _source = effective_rate_detail_as_of(ident, as_of, settings, operator)
    return rate


def effective_rate_detail_as_of(
    ident: dict,
    as_of: Optional[date],
    settings: dict,
    operator: Optional[dict] = None,
) -> Tuple[Optional[float], str]:
    parts = effective_wage_parts_as_of(ident, as_of, settings, operator)
    if parts["rate_unset"] or parts["unit_base"] is None:
        return None, "unset"
    total = float(parts["unit_base"]) + float(parts["unit_addition"] or 0)
    return total, parts["source"]


def _has_operator_versions(settings: dict, ident: dict) -> bool:
    return any(_operator_matches(v, ident) for v in (settings.get("operator_rate_versions") or []))


def effective_wage_parts_as_of(
    ident: dict,
    as_of: Optional[date],
    settings: dict,
    operator: Optional[dict] = None,
) -> Dict[str, Any]:
    settings = normalize_wage_settings(settings)
    state = _history_state_as_of(settings, as_of)
    op_ident = operator_identity(operator)
    has_op_ver = _has_operator_versions(settings, op_ident)
    version_base = _operator_rate_from_versions(settings, op_ident, as_of) if has_op_ver else None
    add_versions = settings.get("product_addition_versions") or []
    key = identity_key(ident)
    has_add_ver = any(identity_key(v) == key for v in add_versions) if key else False
    if has_add_ver:
        picked_scoped = _pick_latest_version(
            add_versions, as_of,
            lambda v: identity_key(v) == key and v.get("active") is not False
            and bool((v.get("operator_user_id") or "").strip() or (v.get("operator_email") or "").strip())
            and _operator_matches(v, op_ident),
        )
        picked_global = _pick_latest_version(
            add_versions, as_of,
            lambda v: identity_key(v) == key and v.get("active") is not False
            and not ((v.get("operator_user_id") or "").strip() or (v.get("operator_email") or "").strip()),
        )
        state_additions = [r for r in (picked_scoped, picked_global) if r]
    else:
        state_additions = list(state.get("product_additions") or [])
    operators = list(state.get("operators") or [])
    if has_op_ver:
        operators = [o for o in operators if not _operator_matches(o, op_ident)]
        if version_base is not None:
            operators = [
                {**op_ident, "default_rate_per_pcs": version_base, "active": True},
                *operators,
            ]
    default_rate = _num_rate(state.get("default_rate_per_pcs"))
    if default_rate is None and not (settings.get("history") or []):
        default_rate = _num_rate(settings.get("default_rate_per_pcs"))
    return _parts_from_state(
        ident,
        default_rate,
        state.get("overrides") or [],
        operator,
        operators,
        additions=state_additions,
        version_base=version_base if has_op_ver else None,
    )


def effective_bonus_as_of(as_of: Optional[date], settings: dict) -> Optional[dict]:
    settings = normalize_wage_settings(settings)
    versions = settings.get("bonus_versions") or []
    if versions:
        return _pick_latest_version(versions, as_of, lambda v: v.get("active") is not False)
    state = _history_state_as_of(settings, as_of)
    bonus = _norm_bonus(state.get("bonus")) if isinstance(state.get("bonus"), dict) else settings.get("bonus")
    return bonus


def compute_bonus(output_qty: Any, bonus: Optional[dict]) -> float:
    """Bonus from one operator's confirmed output in the selected period."""
    cfg = _norm_bonus(bonus) if isinstance(bonus, dict) else bonus
    if not cfg or cfg.get("active") is False:
        return 0.0
    target = _num_positive(cfg.get("target_qty"))
    amount = _num_rate(cfg.get("amount"))
    if target is None or amount is None:
        return 0.0
    qty = float(output_qty or 0)
    if qty < 0:
        qty = 0.0
    mode = _bonus_mode(cfg.get("mode"))
    if mode == BONUS_BLOCK:
        blocks = int(qty // target)
        return float(blocks) * float(amount)
    if qty + 1e-9 < target:
        return 0.0
    return float(amount)


def _state_tuple(default_rate, operators, overrides, bonus=None, additions=None):
    ops = tuple(
        (
            (o.get("operator_user_id") or ""),
            (o.get("operator_email") or ""),
            o.get("default_rate_per_pcs"),
            o.get("effective_from"),
        )
        for o in (operators or [])
    )
    rows = tuple(
        (
            identity_key(o),
            o.get("operator_user_id") or "",
            o.get("operator_email") or "",
            o.get("rate_per_pcs"),
            o.get("rate_type") or RATE_OVERRIDE,
            o.get("active", True),
        )
        for o in (overrides or [])
    )
    adds = tuple(
        (
            identity_key(o),
            o.get("operator_user_id") or "",
            o.get("operator_email") or "",
            o.get("additional_rate_per_pcs"),
            o.get("active", True),
            o.get("effective_from"),
        )
        for o in (additions or [])
    )
    b = bonus or {}
    bonus_t = (
        b.get("target_qty"),
        b.get("amount"),
        b.get("mode"),
        b.get("effective_from"),
        b.get("active", True),
    ) if b else None
    return (default_rate, ops, rows, bonus_t, adds)


def _append_version(versions: List[dict], new_row: Optional[dict], match_fn, equal_keys: Sequence[str]) -> List[dict]:
    if not new_row:
        return versions
    last = None
    for v in reversed(versions):
        if match_fn(v):
            last = v
            break
    if last:
        same = True
        for k in equal_keys:
            lv, nv = last.get(k), new_row.get(k)
            if k == "effective_from":
                if _as_date(lv) != _as_date(nv):
                    same = False
                    break
            elif lv != nv:
                same = False
                break
        if same:
            return versions
    versions.append(dict(new_row))
    return versions


def snapshot_on_save(previous: Optional[dict], incoming: dict, user: dict, now_iso: str, today: Optional[date] = None) -> dict:
    """Append history when rates change so later edits do not rewrite old payroll."""
    prev = normalize_wage_settings(previous)
    nxt = normalize_wage_settings({
        **prev,
        **(incoming or {}),
        "history": prev.get("history") or [],
        "operator_rate_versions": prev.get("operator_rate_versions") or [],
        "bonus_versions": prev.get("bonus_versions") or [],
        "product_addition_versions": prev.get("product_addition_versions") or [],
    })
    day = today or wib_today()
    by = (user or {}).get("email")

    op_versions = list(prev.get("operator_rate_versions") or [])
    for o in (nxt.get("operators") or []):
        ef = _as_date(o.get("effective_from")) or day
        row = {
            **o,
            "effective_from": ef.isoformat(),
            "at": now_iso,
            "by": by,
        }
        ident = {"operator_user_id": o.get("operator_user_id"), "operator_email": o.get("operator_email")}
        op_versions = _append_version(
            op_versions, row,
            lambda v, ident=ident: _operator_matches(v, ident),
            ("default_rate_per_pcs", "effective_from", "active"),
        )

    bonus_versions = list(prev.get("bonus_versions") or [])
    if nxt.get("bonus"):
        b = dict(nxt["bonus"])
        ef = _as_date(b.get("effective_from")) or day
        b["effective_from"] = ef.isoformat()
        b["at"] = now_iso
        b["by"] = by
        bonus_versions = _append_version(
            bonus_versions, b,
            lambda v: True,
            ("target_qty", "amount", "mode", "effective_from", "active"),
        )

    add_versions = list(prev.get("product_addition_versions") or [])
    for a in (nxt.get("product_additions") or []):
        ef = _as_date(a.get("effective_from")) or day
        row = {**a, "effective_from": ef.isoformat(), "at": now_iso, "by": by}
        key = identity_key(a)
        ident = {"operator_user_id": a.get("operator_user_id"), "operator_email": a.get("operator_email")}
        add_versions = _append_version(
            add_versions, row,
            lambda v, key=key, ident=ident: identity_key(v) == key and _operator_matches(v, ident) if (ident.get("operator_user_id") or ident.get("operator_email")) else identity_key(v) == key and not ((v.get("operator_user_id") or "").strip() or (v.get("operator_email") or "").strip()),
            ("additional_rate_per_pcs", "effective_from", "active"),
        )

    new_snap = {
        "effective_from": day.isoformat(),
        "at": now_iso,
        "by": by,
        "default_rate_per_pcs": nxt.get("default_rate_per_pcs"),
        "operators": [
            {
                "operator_user_id": o.get("operator_user_id"),
                "operator_email": o.get("operator_email"),
                "operator_name": o.get("operator_name"),
                "default_rate_per_pcs": o.get("default_rate_per_pcs"),
                "effective_from": o.get("effective_from"),
            }
            for o in (nxt.get("operators") or [])
        ],
        "overrides": [
            {
                **o,
                "rate_per_pcs": o.get("rate_per_pcs"),
                "rate_type": o.get("rate_type") or RATE_OVERRIDE,
                "active": o.get("active", True),
            }
            for o in (nxt.get("overrides") or [])
        ],
        "product_additions": [
            {
                **o,
                "additional_rate_per_pcs": o.get("additional_rate_per_pcs"),
                "active": o.get("active", True),
            }
            for o in (nxt.get("product_additions") or [])
        ],
        "bonus": nxt.get("bonus"),
    }
    history = list(prev.get("history") or [])
    last = history[-1] if history else None
    last_state = _state_tuple(
        last.get("default_rate_per_pcs") if last else None,
        last.get("operators") if last else None,
        last.get("overrides") if last else None,
        last.get("bonus") if last else None,
        last.get("product_additions") if last else None,
    ) if last else None
    new_state = _state_tuple(
        new_snap.get("default_rate_per_pcs"),
        new_snap.get("operators"),
        new_snap.get("overrides"),
        new_snap.get("bonus"),
        new_snap.get("product_additions"),
    )
    if last_state != new_state:
        history.append(new_snap)
    nxt["history"] = history
    nxt["operator_rate_versions"] = op_versions
    nxt["bonus_versions"] = bonus_versions
    nxt["product_addition_versions"] = add_versions
    nxt["updated_at"] = now_iso
    nxt["updated_by"] = by
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
        rate = row.get("default_rate_per_pcs")
        out.append({
            "operator_user_id": ident["operator_user_id"] or row.get("operator_user_id") or "",
            "operator_email": ident["operator_email"] or row.get("operator_email") or "",
            "operator_name": ident["operator_name"] or row.get("operator_name") or "",
            "default_rate_per_pcs": rate,
            "effective_from": row.get("effective_from"),
            "status": "Aktif" if rate is not None else "Belum Disetel",
            "updated_at": row.get("updated_at") or settings.get("updated_at"),
            "updated_by": row.get("updated_by") or settings.get("updated_by"),
        })
    for row in stored:
        key = (row.get("operator_user_id") or row.get("operator_email") or "").strip()
        if key and key not in seen:
            rate = row.get("default_rate_per_pcs")
            out.append({
                **row,
                "status": "Aktif" if rate is not None else "Belum Disetel",
            })
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
            unit_base, unit_add, source, unset = snap_rate, 0.0, "entry_snapshot", False
        else:
            parts = effective_wage_parts_as_of(ident, as_of, settings, op_ident)
            unit_base = parts["unit_base"]
            unit_add = float(parts["unit_addition"] or 0)
            source = parts["source"]
            unset = bool(parts["rate_unset"])
        qty = max(0, safe_int(r.get("qty_completed") if r.get("qty_completed") is not None else r.get("qty")))
        base_n = float(unit_base) if unit_base is not None else None
        add_n = float(unit_add or 0) if not unset else 0.0
        base_wage = (qty * base_n) if base_n is not None else 0.0
        product_addition = (qty * add_n) if base_n is not None else 0.0
        total = base_wage + product_addition
        rows.append({
            **r,
            "operator_user_id": op_ident.get("operator_user_id") or "",
            "item_code": ident.get("item_code") or "",
            "item_name": ident.get("item_name") or r.get("item_name") or "",
            "accurate_item_id": ident.get("accurate_item_id") or "",
            "master_item_id": ident.get("master_item_id") or "",
            "qty_completed": qty,
            "rate_per_pcs": (base_n + add_n) if base_n is not None else "",
            "base_rate_per_pcs": base_n if base_n is not None else "",
            "addition_per_pcs": add_n if base_n is not None else "",
            "base_wage": base_wage,
            "product_addition": product_addition,
            "total_upah": total,
            "rate_source": source,
            "rate_unset": unset,
        })
    acc: Dict[str, dict] = {}
    for r in rows:
        eid = r.get("operator_user_id") or r.get("operator_id") or r.get("employee_id") or ""
        rec = acc.setdefault(eid, {
            "id": eid,
            "operator_user_id": r.get("operator_user_id") or "",
            "email": r.get("operator_id") or eid,
            "name": r.get("operator") or eid,
            "qty": 0,
            "base_wage": 0.0,
            "product_addition": 0.0,
            "bonus": 0.0,
            "total_upah": 0.0,
            "rate_unset": False,
        })
        if r.get("operator"):
            rec["name"] = r["operator"]
        rec["qty"] += r["qty_completed"]
        rec["base_wage"] += r["base_wage"] if isinstance(r["base_wage"], (int, float)) else 0
        rec["product_addition"] += r["product_addition"] if isinstance(r["product_addition"], (int, float)) else 0
        if r.get("rate_unset"):
            rec["rate_unset"] = True
    bonus_cfg = effective_bonus_as_of(date_to, settings)
    for rec in acc.values():
        rec["bonus"] = compute_bonus(rec["qty"], bonus_cfg)
        rec["total_upah"] = rec["base_wage"] + rec["product_addition"] + rec["bonus"]
    people = sorted(acc.values(), key=lambda x: (x.get("name") or x.get("email") or ""))
    missing = [p["name"] or p["email"] or p["id"] for p in people if p.get("rate_unset")]
    return {
        "rows": rows,
        "people": people,
        "kpis": {
            "output": sum(r["qty_completed"] for r in rows),
            "base_wage": sum(p["base_wage"] for p in people),
            "product_addition": sum(p["product_addition"] for p in people),
            "bonus": sum(p["bonus"] for p in people),
            "total_upah": sum(p["total_upah"] for p in people),
            "operator_count": len(people),
        },
        "settings": {
            "default_rate_per_pcs": settings.get("default_rate_per_pcs"),
            "operators": settings.get("operators") or [],
            "bonus": settings.get("bonus"),
        },
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "rate_history_strategy": RATE_HISTORY_STRATEGY,
        "precedence": PAYROLL_PRECEDENCE,
        "source": "iter_operator_export_rows (operator_splits.qty_completed / timeline deltas)",
        "read_only": True,
        "filter": {"operator_ids": ids},
        "missing_base_rate": missing,
        "missing_base_rate_message": "Upah Operator belum disetel" if missing else None,
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
                rec["id"] = uid
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
