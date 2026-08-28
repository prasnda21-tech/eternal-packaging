"""Internal verified mapping: Accurate createdBy id → Pembuat PO name.

Stored in existing `app_settings` (key=accurate_creator_mappings).
No Accurate user-by-id API. No hardcoded id→name in the parser.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

SETTINGS_KEY = "accurate_creator_mappings"
CREATOR_MAP_TTL_S = 600.0  # 10 minutes
CREATOR_SCAN_LIMITS = (100, 250, 500)
DEFAULT_CREATOR_SCAN_LIMIT = 100
CREATOR_SCAN_LIST_PAGE_SIZE = 100
CREATOR_SCAN_SAMPLE_POS = 3

_Loader = Callable[[], Awaitable[Optional[Dict[str, Any]]]]
_mapping_loader: Optional[_Loader] = None
_map_cache: Optional[Dict[str, Dict[str, Any]]] = None
_map_cache_at = 0.0
_loader_calls = 0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_mapping_loader(loader: Optional[_Loader]) -> None:
    global _mapping_loader
    _mapping_loader = loader


def clear_creator_map_cache() -> None:
    """Tests and admin PUT. Drops the in-memory mapping table."""
    global _map_cache, _map_cache_at
    _map_cache = None
    _map_cache_at = 0.0


def reset_loader_call_count() -> None:
    global _loader_calls
    _loader_calls = 0


def loader_call_count() -> int:
    return _loader_calls


def normalize_created_by_id(value: Any) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        value = value.get("accurate_created_by_id") or value.get("id")
        if value is None or isinstance(value, bool):
            return None
    if isinstance(value, float):
        if value != value or value != int(value):
            return None
        value = int(value)
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    text = str(value).strip()
    if not text or not text.isdigit():
        return None
    return text


def usable_display_name(value: Any) -> Optional[str]:
    """Never treat a numeric Accurate id as a display name."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None
    text = str(value).strip()
    if not text or text.isdigit():
        return None
    return text


def parse_verified_map(doc: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """id → {display_name, verified, updated_at}. Unverified rows omitted."""
    out: Dict[str, Dict[str, Any]] = {}
    rows = []
    if isinstance(doc, dict):
        raw = doc.get("mappings")
        if isinstance(raw, list):
            rows = raw
        elif isinstance(raw, dict):
            rows = [
                {"accurate_created_by_id": k, **(v if isinstance(v, dict) else {"display_name": v})}
                for k, v in raw.items()
            ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("verified") is False:
            continue
        cid = normalize_created_by_id(row.get("accurate_created_by_id") or row.get("id"))
        name = usable_display_name(row.get("display_name") or row.get("name"))
        if not cid or not name:
            continue
        out[cid] = {
            "accurate_created_by_id": cid,
            "display_name": name,
            "verified": True,
            "updated_at": row.get("updated_at"),
        }
    return out


def normalize_scan_limit(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CREATOR_SCAN_LIMIT
    if n in CREATOR_SCAN_LIMITS:
        return n
    return DEFAULT_CREATOR_SCAN_LIMIT


def upsert_verified_mapping(
    doc: Optional[Dict[str, Any]],
    created_by_id: Any,
    display_name: Any,
    *,
    updated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge one verified row. Never duplicates createdBy ids. Scan must not call this."""
    stamp = updated_at or now_iso()
    cid = normalize_created_by_id(created_by_id)
    name = usable_display_name(display_name)
    if not cid:
        raise ValueError("createdBy ID tidak valid")
    if not name:
        raise ValueError("Nama pembuat PO wajib diisi")
    current = sanitize_settings(doc if isinstance(doc, dict) else {}, updated_at=stamp)
    mappings = list(current["mappings"])
    for row in mappings:
        if row["accurate_created_by_id"] == cid:
            row["display_name"] = name
            row["verified"] = True
            row["updated_at"] = stamp
            return {"key": SETTINGS_KEY, "mappings": mappings, "updated_at": stamp}
    mappings.append({
        "accurate_created_by_id": cid,
        "display_name": name,
        "verified": True,
        "updated_at": stamp,
    })
    return {"key": SETTINGS_KEY, "mappings": mappings, "updated_at": stamp}


def aggregate_creator_scan(
    rows: List[Dict[str, Any]],
    mappings: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    scanned: int = 0,
    failed: int = 0,
    limit: int = DEFAULT_CREATOR_SCAN_LIMIT,
    list_requests: int = 0,
    detail_requests: int = 0,
) -> Dict[str, Any]:
    """Deduplicate createdBy from scanned SOs. Does not mark unknown ids verified."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cid = normalize_created_by_id(row.get("accurate_created_by_id"))
        if not cid:
            continue
        bucket = buckets.get(cid)
        if bucket is None:
            bucket = {"count": 0, "samples": [], "seen": set(), "last_so_date": None}
            buckets[cid] = bucket
        bucket["count"] += 1
        number = str(row.get("po_number") or "").strip()
        if number and number not in bucket["seen"] and len(bucket["samples"]) < CREATOR_SCAN_SAMPLE_POS:
            bucket["samples"].append(number)
            bucket["seen"].add(number)
        incoming = str(row.get("trans_date") or "").strip() or None
        if incoming and (not bucket["last_so_date"] or incoming > bucket["last_so_date"]):
            bucket["last_so_date"] = incoming

    maps = mappings or {}
    unknown_out: List[Dict[str, Any]] = []
    verified_out: List[Dict[str, Any]] = []
    for cid, bucket in buckets.items():
        entry = maps.get(cid) if isinstance(maps.get(cid), dict) else None
        name = usable_display_name((entry or {}).get("display_name")) if entry else None
        is_verified = bool(entry and entry.get("verified") is not False and name)
        item = {
            "accurate_created_by_id": cid,
            "so_count": bucket["count"],
            "sample_po_numbers": list(bucket["samples"]),
            "last_so_date": bucket["last_so_date"],
            "display_name": name if is_verified else None,
            "verified": is_verified,
            "status": "verified" if is_verified else "unknown",
        }
        (verified_out if is_verified else unknown_out).append(item)

    seen_ids = set(buckets)
    for cid, entry in maps.items():
        if cid in seen_ids or not isinstance(entry, dict):
            continue
        name = usable_display_name(entry.get("display_name"))
        if not name or entry.get("verified") is False:
            continue
        verified_out.append({
            "accurate_created_by_id": cid,
            "so_count": 0,
            "sample_po_numbers": [],
            "last_so_date": None,
            "display_name": name,
            "verified": True,
            "status": "verified",
        })

    def _sort_key(item: Dict[str, Any]) -> tuple:
        return (-int(item.get("so_count") or 0), str(item.get("last_so_date") or ""), str(item["accurate_created_by_id"]))

    unknown_out.sort(key=_sort_key)
    verified_out.sort(key=_sort_key)
    return {
        "limit": limit,
        "scanned": scanned,
        "failed": failed,
        "list_requests": list_requests,
        "detail_requests": detail_requests,
        "unknown": unknown_out,
        "verified": verified_out,
        "wrote_mappings": False,
    }


def sanitize_settings(payload: Any, *, updated_at: Optional[str] = None) -> Dict[str, Any]:
    stamp = updated_at or now_iso()
    rows_in: List[Any] = []
    if isinstance(payload, dict):
        raw = payload.get("mappings")
        if isinstance(raw, list):
            rows_in = raw
    seen = set()
    mappings: List[Dict[str, Any]] = []
    for row in rows_in:
        if not isinstance(row, dict):
            continue
        cid = normalize_created_by_id(row.get("accurate_created_by_id") or row.get("id"))
        name = usable_display_name(row.get("display_name") or row.get("name"))
        if not cid or not name or cid in seen:
            continue
        seen.add(cid)
        mappings.append({
            "accurate_created_by_id": cid,
            "display_name": name,
            "verified": bool(row.get("verified", True)),
            "updated_at": row.get("updated_at") or stamp,
        })
    return {"key": SETTINGS_KEY, "mappings": mappings, "updated_at": stamp}


def resolve_accurate_creator(
    created_by: Any,
    *,
    live_name: Any = None,
    mappings: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[str]:
    """Priority: live Accurate name, then verified internal mapping, else None."""
    name = usable_display_name(live_name)
    if name:
        return name
    cid = normalize_created_by_id(created_by)
    if not cid:
        return None
    entry = (mappings or {}).get(cid)
    if not entry or entry.get("verified") is False:
        return None
    return usable_display_name(entry.get("display_name"))


def apply_creator_to_so(so: Dict[str, Any], mappings: Optional[Dict[str, Dict[str, Any]]]) -> None:
    """Fill created_by_user from verified map when Accurate sent only an id."""
    if so.get("created_by_user"):
        so["created_by_user"] = usable_display_name(so.get("created_by_user"))
        if so.get("created_by_user"):
            return
    cid = so.get("_created_by_id")
    so["created_by_user"] = resolve_accurate_creator(
        cid,
        live_name=so.get("created_by_user"),
        mappings=mappings,
    )


async def load_verified_creator_map(*, force: bool = False) -> Dict[str, Dict[str, Any]]:
    """One settings-document load, cached ~10 minutes. Shared across a batch."""
    global _map_cache, _map_cache_at, _loader_calls
    now = time.monotonic()
    if not force and _map_cache is not None and (now - _map_cache_at) < CREATOR_MAP_TTL_S:
        return _map_cache
    doc: Optional[Dict[str, Any]] = None
    if _mapping_loader is not None:
        _loader_calls += 1
        try:
            doc = await _mapping_loader()
        except Exception:
            doc = None
    parsed = parse_verified_map(doc if isinstance(doc, dict) else None)
    _map_cache = parsed
    _map_cache_at = now
    return parsed
