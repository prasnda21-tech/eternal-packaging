"""Division work-panel presentation settings (additive app_settings).

Business rules (append-only, confirmation, RBAC, validation) are not configurable.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

SETTINGS_KEY = "division_work_panel"

ALLOWED_SUBMIT_PERMS = {
    "screener": ("screen.update",),
    "helper": ("helper.prepare",),
    "operator": ("operator.update",),
    "packing": ("packing.update",),
    "surat_jalan": ("delivery_note.manage", "delivery_note.view"),
    "loading": ("loading.update",),
    "terkirim": ("delivery.update",),
}

ALLOWED_TARGET_SOURCES = {
    "screener": ("screener_qty",),
    "helper": ("helper_prepared",),
    "operator": ("print_line_qty", "print_qty_target", "split_allocation"),
    "packing": ("packing_cap",),
    "surat_jalan": ("packed_qty",),
    "loading": ("sj_qty",),
    "terkirim": ("delivered_qty",),
}

_DEFAULTS: List[dict] = [
    {
        "id": "screener",
        "label": "Screener",
        "active": True,
        "implemented": True,
        "submit_perm": "screen.update",
        "input_label": "Qty Screener",
        "target_source": "screener_qty",
        "input_kind": "qty",
        "unit": "item",
        "helper_text": "",
        "sort_order": 10,
    },
    {
        "id": "helper",
        "label": "Helper",
        "active": True,
        "implemented": True,
        "submit_perm": "helper.prepare",
        "input_label": "Qty Prepared",
        "target_source": "helper_prepared",
        "input_kind": "qty",
        "unit": "item",
        "helper_text": "",
        "sort_order": 20,
    },
    {
        "id": "operator",
        "label": "Operator",
        "active": True,
        "implemented": True,
        "submit_perm": "operator.update",
        "input_label": "Qty Cetak",
        "target_source": "print_line_qty",
        "input_kind": "qty",
        "unit": "item",
        "helper_text": "",
        "sort_order": 30,
    },
    {
        "id": "packing",
        "label": "Packing",
        "active": True,
        "implemented": True,
        "submit_perm": "packing.update",
        "input_label": "Qty Packing",
        "target_source": "packing_cap",
        "input_kind": "qty",
        "unit": "item",
        "helper_text": "",
        "sort_order": 40,
    },
    {
        "id": "surat_jalan",
        "label": "Surat Jalan",
        "active": True,
        "implemented": True,
        "submit_perm": "delivery_note.manage",
        "input_label": "Surat Jalan",
        "target_source": "packed_qty",
        "input_kind": "evidence",
        "unit": "item",
        "helper_text": "",
        "sort_order": 50,
    },
    {
        "id": "loading",
        "label": "Loading",
        "active": True,
        "implemented": True,
        "submit_perm": "loading.update",
        "input_label": "Qty Loading",
        "target_source": "sj_qty",
        "input_kind": "qty",
        "unit": "item",
        "helper_text": "",
        "sort_order": 60,
    },
    {
        "id": "terkirim",
        "label": "Terkirim",
        "active": True,
        "implemented": True,
        "submit_perm": "delivery.update",
        "input_label": "Bukti Pengiriman",
        "target_source": "delivered_qty",
        "input_kind": "evidence",
        "unit": "item",
        "helper_text": "",
        "sort_order": 70,
    },
]


def default_divisions() -> List[dict]:
    return [_locked(_clone(d)) for d in _DEFAULTS]


def _clone(d: dict) -> dict:
    return dict(d)


def _locked(d: dict) -> dict:
    out = dict(d)
    out["confirm_required"] = True
    out["entry_mode"] = "append_only"
    out["input_kind"] = d.get("input_kind") or "qty"
    out["allowed_submit_perms"] = list(ALLOWED_SUBMIT_PERMS.get(d["id"], ()))
    out["allowed_target_sources"] = list(ALLOWED_TARGET_SOURCES.get(d["id"], ()))
    return out


def _by_id(rows: List[dict]) -> Dict[str, dict]:
    return {str(r.get("id")): r for r in rows if r and r.get("id")}


def merge_settings(stored: Optional[dict]) -> dict:
    """Safe defaults when no record exists. Presentation fields only."""
    stored_rows = []
    if isinstance(stored, dict):
        stored_rows = list(stored.get("divisions") or [])
    stored_map = _by_id(stored_rows)
    merged = []
    for base in _DEFAULTS:
        patch = stored_map.get(base["id"]) or {}
        row = _clone(base)
        explicit_impl = patch.get("implemented")
        newly_implemented = bool(base["implemented"]) and explicit_impl is False
        if "active" in patch and not newly_implemented:
            row["active"] = bool(patch.get("active"))
        if isinstance(patch.get("input_label"), str) and patch["input_label"].strip():
            row["input_label"] = patch["input_label"].strip()[:80]
        if isinstance(patch.get("helper_text"), str):
            row["helper_text"] = patch["helper_text"].strip()[:240]
        if isinstance(patch.get("unit"), str) and patch["unit"].strip():
            row["unit"] = patch["unit"].strip()[:24]
        try:
            so = int(patch.get("sort_order"))
            row["sort_order"] = so
        except (TypeError, ValueError):
            pass
        allowed_perm = ALLOWED_SUBMIT_PERMS.get(base["id"], ())
        perm = patch.get("submit_perm")
        if perm in allowed_perm:
            row["submit_perm"] = perm
        allowed_src = ALLOWED_TARGET_SOURCES.get(base["id"], ())
        src = patch.get("target_source")
        if src in allowed_src:
            row["target_source"] = src
        # invariants
        row["implemented"] = bool(base["implemented"])
        row["input_kind"] = base.get("input_kind") or "qty"
        merged.append(_locked(row))
    merged.sort(key=lambda r: (r.get("sort_order") or 0, r.get("id")))
    return {"key": SETTINGS_KEY, "divisions": merged}


def sanitize_put(payload: Any) -> dict:
    divisions = []
    if isinstance(payload, dict):
        divisions = list(payload.get("divisions") or [])
    return merge_settings({"divisions": divisions})
