"""Temporary Accurate Sales Order JSON-path audit helpers.

CLI / tests only. Do not import from production API routes or the frontend.
Never logs tokens, Authorization, secrets, refresh tokens, or API signatures.
Never returns a full Accurate payload.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence, Tuple

SECRET_KEY_PARTS = (
    "token",
    "secret",
    "authorization",
    "password",
    "signature",
    "bearer",
    "session",
)

INTERESTING_KEY_FRAGMENTS = (
    "branch",
    "warehouse",
    "outlet",
    "zona",
    "zone",
    "shipvia",
    "classif",
    "charfield",
    "character",
    "color",
    "warna",
    "location",
    "store",
    "cabang",
    "gudang",
    "detailnotes",
    "detailname",
    "memo",
    "notes",
    "category",
    "group",
    "custom",
    "shipment",
    "itemno",
    "itemname",
)

DEFAULT_TARGET_VALUES = (
    "JS",
    "JT",
    "HITAM",
    "PUTIH",
    "MERAH",
    "OUTLET",
)


def is_secret_key(key: Any) -> bool:
    low = str(key or "").lower()
    return any(part in low for part in SECRET_KEY_PARTS)


def _scalar_text(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (bool, dict, list)):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value).strip()
    return text or None


def walk_scalar_paths(obj: Any, prefix: str = "", depth: int = 0) -> List[Tuple[str, str]]:
    """Recursively collect (json_path, scalar_text). Secret keys are skipped."""
    out: List[Tuple[str, str]] = []
    if depth > 12:
        return out
    if isinstance(obj, dict):
        for key, val in obj.items():
            path = f"{prefix}{key}" if prefix else str(key)
            if is_secret_key(key):
                continue
            if isinstance(val, dict):
                out.extend(walk_scalar_paths(val, path + ".", depth + 1))
            elif isinstance(val, list):
                for i, item in enumerate(val):
                    out.extend(walk_scalar_paths(item, f"{path}[{i}].", depth + 1))
            else:
                text = _scalar_text(val)
                if text is not None:
                    out.append((path.rstrip("."), text))
        return out
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            out.extend(walk_scalar_paths(item, f"{prefix}[{i}].", depth + 1))
        return out
    text = _scalar_text(obj)
    if text is not None:
        out.append((prefix.rstrip(".") or ".", text))
    return out


def unique_key_names(obj: Any, depth: int = 0) -> List[str]:
    """Sorted unique JSON key names. No values. Secret keys omitted."""
    names: set[str] = set()
    if depth > 12:
        return []
    if isinstance(obj, dict):
        for key, val in obj.items():
            if is_secret_key(key):
                continue
            names.add(str(key))
            names.update(unique_key_names(val, depth + 1))
    elif isinstance(obj, list):
        for item in obj[:20]:
            names.update(unique_key_names(item, depth + 1))
    return sorted(names)


def filter_target_hits(
    scalars: Sequence[Tuple[str, str]],
    targets: Iterable[str] = DEFAULT_TARGET_VALUES,
) -> List[Tuple[str, str, str]]:
    """Return (path, value, matched_target) when value contains a target literal."""
    needles = [(t, t.strip().lower()) for t in targets if str(t).strip()]
    hits: List[Tuple[str, str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for path, value in scalars:
        hay = value.lower()
        for label, needle in needles:
            if not needle:
                continue
            if hay == needle or needle in hay:
                key = (path, value)
                if key in seen:
                    continue
                seen.add(key)
                hits.append((path, value, label))
    return hits


def interesting_scalars(scalars: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Scalars whose path looks outlet/zone/color/branch/warehouse related."""
    out: List[Tuple[str, str]] = []
    for path, value in scalars:
        low = path.lower()
        if any(frag in low for frag in INTERESTING_KEY_FRAGMENTS):
            out.append((path, value))
    return out


def format_hits(hits: Sequence[Tuple[str, str, str]]) -> str:
    if not hits:
        return "(no target literal found)"
    lines = []
    for path, value, target in hits:
        shown = value if len(value) <= 180 else value[:177] + "..."
        lines.append(f"{path} = {shown!r}  [matched {target}]")
    return "\n".join(lines)
