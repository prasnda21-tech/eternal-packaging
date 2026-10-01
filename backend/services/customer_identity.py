"""Read-time customer identity for Prediksi Order and Data Pelanggan.

Does not write orders, history, or sales_customers.
Outlet / branch / Name|Outlet composites are never a customer key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


def normalize_customer_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    return " ".join(text.split())


def display_customer_name(record: Optional[dict], *, kind: str = "order") -> str:
    if not record:
        return ""
    if kind == "master":
        return str(record.get("name") or record.get("customer") or record.get("customer_name") or "").strip()
    return str(record.get("customer") or record.get("customer_name") or record.get("name") or "").strip()


def is_legacy_composite_customer_id(value: Any, *, customer: str = "", outlet: str = "") -> bool:
    """True for official histori auto-IDs such as 'KEDAI TUMAN|Yogyakarta'."""
    text = str(value or "").strip()
    if "|" not in text:
        return False
    left, sep, right = text.partition("|")
    if not sep or not left.strip() or not right.strip():
        return False
    if "|" in right:
        return True
    cname = normalize_customer_name(customer)
    oname = normalize_customer_name(outlet)
    if cname and oname and normalize_customer_name(left) == cname and normalize_customer_name(right) == oname:
        return True
    return True


def extract_stable_customer_id(record: Optional[dict], *, kind: str = "order") -> Optional[str]:
    if not record:
        return None
    if kind == "master":
        text = str(record.get("id") or "").strip()
        return text or None
    text = str(record.get("customer_id") or "").strip()
    if not text:
        return None
    if is_legacy_composite_customer_id(
        text,
        customer=display_customer_name(record),
        outlet=str(record.get("outlet") or ""),
    ):
        return None
    return text


def collect_outlet_names(records: Sequence[dict]) -> List[str]:
    seen: Dict[str, str] = {}
    for rec in records or []:
        raw = str((rec or {}).get("outlet") or "").strip()
        if not raw:
            continue
        key = normalize_customer_name(raw)
        if key and key not in seen:
            seen[key] = raw
    return [seen[k] for k in sorted(seen)]


def format_outlet_label(records: Sequence[dict]) -> str:
    return ", ".join(collect_outlet_names(records))


@dataclass
class CustomerIdentityContext:
    name_to_stable_ids: Dict[str, Set[str]] = field(default_factory=dict)
    id_to_display: Dict[str, str] = field(default_factory=dict)
    master_ids: Set[str] = field(default_factory=set)


@dataclass
class ResolvedCustomer:
    key: str
    display_name: str
    stable_id: Optional[str]
    normalized_name: str
    ambiguous: bool
    kind: str


def _register_stable(context: CustomerIdentityContext, stable_id: str, display: str, normalized: str) -> None:
    if display:
        context.id_to_display.setdefault(stable_id, display)
    if normalized:
        context.name_to_stable_ids.setdefault(normalized, set()).add(stable_id)


def build_identity_context(
    masters: Optional[Sequence[dict]] = None,
    orders: Optional[Sequence[dict]] = None,
    history: Optional[Sequence[dict]] = None,
) -> CustomerIdentityContext:
    context = CustomerIdentityContext()
    for master in masters or []:
        sid = extract_stable_customer_id(master, kind="master")
        if not sid:
            continue
        context.master_ids.add(sid)
        display = display_customer_name(master, kind="master")
        _register_stable(context, sid, display, normalize_customer_name(display))
    for order in list(orders or []) + list(history or []):
        display = display_customer_name(order)
        sid = extract_stable_customer_id(order)
        if sid:
            _register_stable(context, sid, display, normalize_customer_name(display))
    return context


def resolve_customer_identity(
    record: Optional[dict],
    context: Optional[CustomerIdentityContext] = None,
    *,
    kind: str = "order",
    allow_name_fallback: bool = True,
) -> ResolvedCustomer:
    display = display_customer_name(record, kind=kind)
    normalized = normalize_customer_name(display)
    stable = extract_stable_customer_id(record, kind=kind)

    if not display and not stable:
        return ResolvedCustomer(
            key="", display_name="", stable_id=None, normalized_name="",
            ambiguous=False, kind="none",
        )

    if stable:
        preferred = ""
        if context:
            preferred = context.id_to_display.get(stable) or ""
        return ResolvedCustomer(
            key=f"id:{stable}",
            display_name=preferred or display,
            stable_id=stable,
            normalized_name=normalized,
            ambiguous=False,
            kind="stable_id",
        )

    if not normalized:
        return ResolvedCustomer(
            key="", display_name="", stable_id=None, normalized_name="",
            ambiguous=False, kind="none",
        )

    known = set((context.name_to_stable_ids.get(normalized) if context else None) or ())
    # Office reports may attach a nameless histori row to the one stable id
    # that shares its name. PWA prediction passes allow_name_fallback=False so
    # a name is never promoted to customer_id.
    if len(known) == 1 and allow_name_fallback:
        sid = next(iter(known))
        preferred = context.id_to_display.get(sid) if context else ""
        return ResolvedCustomer(
            key=f"id:{sid}",
            display_name=preferred or display,
            stable_id=sid,
            normalized_name=normalized,
            ambiguous=False,
            kind="name_fallback",
        )
    if len(known) >= 2:
        return ResolvedCustomer(
            key=f"ambiguous:{normalized}",
            display_name=display,
            stable_id=None,
            normalized_name=normalized,
            ambiguous=True,
            kind="ambiguous_name",
        )
    return ResolvedCustomer(
        key=f"name:{normalized}",
        display_name=display,
        stable_id=None,
        normalized_name=normalized,
        ambiguous=False,
        kind="name",
    )


def customer_identity_key(
    record: Optional[dict],
    context: Optional[CustomerIdentityContext] = None,
    *,
    kind: str = "order",
) -> str:
    return resolve_customer_identity(record, context, kind=kind).key
