"""Customer delivery / outlet locations for route optimization.

Separate records so one customer can have multiple outlets.
Confirmed route plans keep their own lat/lng/place_id snapshot and are not
rewritten when the master outlet location is later edited.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.delivery_stops import (
    LOCATION_NEED,
    LOCATION_OK,
    MISSING_LOCATION_REASON,
    parse_location,
)

COLLECTION = "delivery_outlets"

HISTORICAL_ROUTE_STATUSES = frozenset({"terjadwal", "berjalan", "selesai", "dibatalkan"})


def now_iso(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def customer_key(name: Any) -> str:
    text = str(name or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def outlet_key(name: Any) -> str:
    text = customer_key(name)
    return text or "utama"


def _safe_customer_id(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    return text or None


def public_outlet(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    loc = parse_location(doc)
    return {
        "id": doc.get("id"),
        "customer_id": doc.get("customer_id"),
        "customer_name": doc.get("customer_name") or "",
        "customer_key": doc.get("customer_key") or "",
        "name": doc.get("name") or "",
        "outlet_key": doc.get("outlet_key") or "",
        "formatted_address": loc.get("formatted_address") or doc.get("formatted_address"),
        "google_place_id": loc.get("place_id") or doc.get("google_place_id") or doc.get("place_id"),
        "place_id": loc.get("place_id") or doc.get("google_place_id") or doc.get("place_id"),
        "lat": loc.get("lat") if loc.get("lat") is not None else doc.get("lat"),
        "lng": loc.get("lng") if loc.get("lng") is not None else doc.get("lng"),
        "location_status": loc.get("location_status"),
        "updated_at": doc.get("updated_at"),
    }


def location_payload_from_outlet(doc: dict) -> dict:
    pub = public_outlet(doc) or {}
    return {
        "outlet_id": pub.get("id"),
        "outlet_name": pub.get("name"),
        "lat": pub.get("lat"),
        "lng": pub.get("lng"),
        "place_id": pub.get("place_id"),
        "google_place_id": pub.get("place_id"),
        "formatted_address": pub.get("formatted_address"),
    }


def index_outlets(outlets: Iterable[dict]) -> dict:
    by_id: Dict[str, dict] = {}
    by_pair: Dict[Tuple[str, str], dict] = {}
    by_customer: Dict[str, List[dict]] = {}
    for raw in outlets or []:
        if not isinstance(raw, dict):
            continue
        oid = str(raw.get("id") or "").strip()
        ck = str(raw.get("customer_key") or customer_key(raw.get("customer_name"))).strip()
        ok = str(raw.get("outlet_key") or outlet_key(raw.get("name"))).strip()
        if oid:
            by_id[oid] = raw
        if ck:
            by_pair[(ck, ok)] = raw
            by_customer.setdefault(ck, []).append(raw)
    return {"by_id": by_id, "by_pair": by_pair, "by_customer": by_customer}


def resolve_outlet_for_order(order: dict, index: Optional[dict]) -> Optional[dict]:
    """Pick the outlet record for this PO without inventing a location."""
    if not index:
        return None
    oid = str(order.get("delivery_outlet_id") or "").strip()
    if oid and oid in index["by_id"]:
        return index["by_id"][oid]
    ck = customer_key(order.get("customer_name") or order.get("customer") or "")
    if not ck:
        return None
    ok = outlet_key(order.get("outlet"))
    if (ck, ok) in index["by_pair"]:
        return index["by_pair"][(ck, ok)]
    matches = index["by_customer"].get(ck) or []
    if len(matches) == 1:
        return matches[0]
    return None


def resolved_location(order: dict, index: Optional[dict] = None) -> dict:
    """Outlet record wins when valid; otherwise legacy order.delivery_location."""
    outlet = resolve_outlet_for_order(order, index)
    if outlet:
        loc = parse_location(outlet)
        if loc.get("location_status") == LOCATION_OK:
            loc["outlet_id"] = outlet.get("id")
            loc["outlet_name"] = outlet.get("name") or order.get("outlet")
            if not loc.get("formatted_address"):
                loc["formatted_address"] = (order.get("address") or "").strip() or None
            return loc
        # Invalid master still exposes the id so the UI can edit it.
        loc["outlet_id"] = outlet.get("id")
        loc["outlet_name"] = outlet.get("name") or order.get("outlet")
        fallback = parse_location(order.get("delivery_location"))
        if fallback.get("location_status") == LOCATION_OK:
            fallback["outlet_id"] = outlet.get("id")
            fallback["outlet_name"] = loc.get("outlet_name")
            return fallback
        if not loc.get("formatted_address"):
            loc["formatted_address"] = (order.get("address") or "").strip() or None
        return loc
    loc = parse_location(order.get("delivery_location"))
    loc["outlet_id"] = order.get("delivery_outlet_id")
    loc["outlet_name"] = order.get("outlet")
    if not loc.get("formatted_address"):
        loc["formatted_address"] = (order.get("address") or "").strip() or None
    return loc


def build_outlet_doc(
    *,
    existing: Optional[dict] = None,
    customer_name: str,
    customer_id: Any = None,
    name: str,
    location: dict,
    user: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> dict:
    loc = parse_location(location)
    if loc.get("location_status") != LOCATION_OK:
        raise ValueError("Lokasi wajib punya latitude dan longitude yang valid.")
    ts = now_iso(now)
    actor = ""
    if user:
        actor = str(user.get("user_id") or user.get("id") or user.get("email") or "").strip()
    label = str(name or "").strip() or str(customer_name or "").strip() or "Utama"
    ck = customer_key(customer_name)
    ok = outlet_key(label)
    doc = dict(existing or {})
    if not doc.get("id"):
        doc["id"] = str(uuid.uuid4())
        doc["created_at"] = ts
        doc["created_by_id"] = actor
    doc.update({
        "customer_id": _safe_customer_id(customer_id) or doc.get("customer_id"),
        "customer_name": str(customer_name or "").strip(),
        "customer_key": ck,
        "name": label,
        "outlet_key": ok,
        "formatted_address": loc.get("formatted_address"),
        "google_place_id": loc.get("place_id"),
        "place_id": loc.get("place_id"),
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "updated_at": ts,
        "updated_by_id": actor,
    })
    return doc


def order_set_from_outlet(outlet: dict) -> dict:
    loc = location_payload_from_outlet(outlet)
    return {
        "delivery_outlet_id": loc.get("outlet_id"),
        "delivery_location": loc,
    }


def reuse_query_for_outlet(outlet: dict) -> dict:
    """Orders that should inherit this outlet on the next schedule."""
    ck = outlet.get("customer_key") or ""
    ok = outlet.get("outlet_key") or ""
    oid = outlet.get("id")
    clauses = [{"delivery_outlet_id": oid}]
    if ck:
        name = outlet.get("customer_name") or ""
        clauses.append({
            "$and": [
                {"$or": [
                    {"customer_key": ck},
                    {"customer": name},
                    {"customer_name": name},
                ]},
                {"$or": [
                    {"delivery_outlet_id": {"$in": [None, ""]}},
                    {"delivery_outlet_id": {"$exists": False}},
                    {"delivery_outlet_id": oid},
                ]},
                {"$or": [
                    {"outlet_key": ok},
                    {"outlet": outlet.get("name")},
                    {"outlet": {"$in": [None, ""]}},
                ]} if ok == "utama" else {
                    "$or": [
                        {"outlet_key": ok},
                        {"outlet": outlet.get("name")},
                    ]
                },
            ]
        })
    return {"$or": clauses}


def apply_location_to_stops(stops: List[dict], *, order_id: Optional[str], outlet: dict) -> int:
    """Update unconfirmed stop snapshots that belong to this order/outlet."""
    loc = location_payload_from_outlet(outlet)
    loc_status = LOCATION_OK if loc.get("lat") is not None and loc.get("lng") is not None else LOCATION_NEED
    changed = 0
    oid = str(order_id or "").strip()
    outlet_id = str(outlet.get("id") or "")
    for st in stops or []:
        if st.get("destination_snapshot"):
            continue
        if oid and str(st.get("order_id") or "") != oid and str(st.get("outlet_id") or "") != outlet_id:
            continue
        if not oid and str(st.get("outlet_id") or "") != outlet_id:
            continue
        st["lat"] = loc.get("lat")
        st["lng"] = loc.get("lng")
        st["place_id"] = loc.get("place_id")
        st["google_place_id"] = loc.get("place_id")
        if loc.get("formatted_address"):
            st["address"] = loc.get("formatted_address")
        st["outlet_id"] = loc.get("outlet_id")
        st["outlet_name"] = loc.get("outlet_name")
        st["location_status"] = loc_status
        st["location_label"] = "Lokasi Siap" if loc_status == LOCATION_OK else "Belum Ada Lokasi"
        if loc_status == LOCATION_OK and st.get("unassigned_reason") in (
            MISSING_LOCATION_REASON, "Lokasi belum lengkap", "PERLU LENGKAPI LOKASI",
        ):
            st["unassigned_reason"] = None
        changed += 1
    return changed


def plan_is_historical(plan: Optional[dict]) -> bool:
    if not plan:
        return False
    return str(plan.get("status") or "") in HISTORICAL_ROUTE_STATUSES
