"""Public Live Tracking URL labels.

The access code is the existing tracking token. Customer and PO segments are
labels only: they are not looked up, and a different label does not select a
different order. Existing tokens are reused. This module does not write.
"""
from __future__ import annotations

import unicodedata
from typing import List
from urllib.parse import quote

CUSTOMER_SLUG_FALLBACK = "pelanggan"
PO_SLUG_MISSING = "tanpa-po"
PO_SLUG_MULTIPLE = "beberapa-po"
_SEGMENT_SAFE = "-._~"


def customer_slug(name: object) -> str:
    """Lowercase label. Spaces and punctuation become single hyphens.

    Letters and numbers are kept, including non-ASCII letters. Combining
    marks are folded with NFKC before casing. An empty result uses a fixed
    fallback so the path still has a customer segment.
    """
    text = unicodedata.normalize("NFKC", str(name or "")).casefold().strip()
    parts: List[str] = []
    buf: List[str] = []

    def flush() -> None:
        if buf:
            parts.append("".join(buf))
            buf.clear()

    for ch in text:
        if unicodedata.category(ch)[0] in ("L", "N"):
            buf.append(ch)
        else:
            flush()
    flush()
    return "-".join(parts) or CUSTOMER_SLUG_FALLBACK


def po_path_slug(po_number: object) -> str:
    """Keep the PO identity. Only slash becomes a hyphen so it is one segment.

    The calendar date inside a PO number is not reformatted, and the value is
    not replaced with a Surat Jalan number.
    """
    text = str(po_number or "").strip()
    return text.replace("\\", "-").replace("/", "-")


def _add_po(found: List[str], seen: set, value: object) -> None:
    if isinstance(value, (list, tuple)):
        for item in value:
            _add_po(found, seen, item)
        return
    text = str(value or "").strip()
    if not text or text in seen:
        return
    seen.add(text)
    found.append(text)


def distinct_po_numbers(order: dict) -> List[str]:
    """PO numbers that belong to this order. Surat Jalan numbers are ignored."""
    found: List[str] = []
    seen: set = set()
    if not isinstance(order, dict):
        return found
    _add_po(found, seen, order.get("po_number"))
    _add_po(found, seen, order.get("po_numbers"))
    for line in order.get("line_items") or []:
        if isinstance(line, dict):
            _add_po(found, seen, line.get("po_number"))
    return found


def resolve_tracking_po(order: dict) -> tuple:
    """Return (slug, rule).

    single: one PO number represents the order.
    missing: the order has no PO. The slug is a label, not an invented number.
    multiple: more than one distinct PO is stored on the order. None of them
    is chosen for the URL.
    """
    numbers = distinct_po_numbers(order)
    if not numbers:
        return PO_SLUG_MISSING, "missing"
    if len(numbers) > 1:
        return PO_SLUG_MULTIPLE, "multiple"
    slug = po_path_slug(numbers[0])
    if not slug:
        return PO_SLUG_MISSING, "missing"
    return slug, "single"


def encode_segment(value: object) -> str:
    return quote(str(value or "").strip(), safe=_SEGMENT_SAFE)


def tracking_public_path(order: dict, code: str) -> tuple:
    """Return (path, po_label). Path is /t/{customer}/{po}/{code}."""
    customer = customer_slug(
        (order or {}).get("customer") or (order or {}).get("customer_name") or ""
    )
    po_slug, rule = resolve_tracking_po(order or {})
    path = f"/t/{encode_segment(customer)}/{encode_segment(po_slug)}/{encode_segment(code)}"
    return path, rule


def tracking_legacy_path(code: str) -> str:
    return f"/tracking/{encode_segment(code)}"
