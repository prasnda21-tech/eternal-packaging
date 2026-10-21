"""CRM Daily Report dropdown settings (reasons / complaint / issue categories)."""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

SETTINGS_KEY = "crm_daily_report_options"
PERM_VIEW = "settings.crm_daily_report.view"
PERM_MANAGE = "settings.crm_daily_report.manage"

OTHER_OPTION_ID = "__other__"

CATEGORY_KEYS = ("no_order_reasons", "complaint_categories", "issue_categories")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_options(label: str = "Lainnya") -> List[dict]:
    return [{
        "id": OTHER_OPTION_ID,
        "label": label,
        "active": True,
        "sort_order": 9999,
        "is_other": True,
    }]


def default_settings() -> dict:
    return {
        "key": SETTINGS_KEY,
        "no_order_reasons": _default_options("Lainnya"),
        "complaint_categories": _default_options("Lainnya"),
        "issue_categories": _default_options("Lainnya"),
        "doc_version": 1,
        "updated_at": None,
        "updated_by": None,
        "updated_by_name": None,
    }


def _sanitize_option(raw: Any, *, index: int) -> dict:
    if not isinstance(raw, dict):
        raise HTTPException(400, f"Opsi[{index}] tidak valid")
    oid = str(raw.get("id") or "").strip()
    label = str(raw.get("label") or "").strip()
    if not oid:
        oid = str(uuid.uuid4())
    if not label:
        raise HTTPException(400, f"Opsi[{index}] membutuhkan label")
    is_other = bool(raw.get("is_other")) or oid == OTHER_OPTION_ID
    if is_other:
        oid = OTHER_OPTION_ID
    try:
        sort_order = int(raw.get("sort_order") if raw.get("sort_order") is not None else index)
    except (TypeError, ValueError):
        raise HTTPException(400, f"Opsi[{index}] sort_order tidak valid")
    return {
        "id": oid,
        "label": label,
        "active": bool(raw.get("active", True)),
        "sort_order": sort_order,
        "is_other": is_other,
    }


def _sanitize_category(raw: Any, *, key: str) -> List[dict]:
    if raw is None:
        return copy.deepcopy(default_settings()[key])
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, f"{key} tidak boleh kosong")
    out: List[dict] = []
    seen = set()
    other_count = 0
    for i, row in enumerate(raw):
        opt = _sanitize_option(row, index=i)
        if opt["id"] in seen:
            raise HTTPException(400, f"{key}: id duplikat {opt['id']}")
        seen.add(opt["id"])
        if opt["is_other"]:
            other_count += 1
        out.append(opt)
    if other_count != 1:
        raise HTTPException(400, f"{key} wajib memiliki tepat satu opsi Lainnya ({OTHER_OPTION_ID})")
    out.sort(key=lambda o: (int(o.get("sort_order") or 0), o.get("label") or ""))
    return out


def merge_settings(raw: Optional[dict]) -> dict:
    base = default_settings()
    if not raw:
        return base
    merged = copy.deepcopy(base)
    for key in CATEGORY_KEYS:
        if key in raw and raw[key] is not None:
            merged[key] = _sanitize_category(raw[key], key=key)
    merged["doc_version"] = int(raw.get("doc_version") or 1)
    merged["updated_at"] = raw.get("updated_at")
    merged["updated_by"] = raw.get("updated_by")
    merged["updated_by_name"] = raw.get("updated_by_name")
    return merged


def public_settings(doc: Optional[dict]) -> dict:
    return merge_settings(doc)


def active_options(settings: dict, key: str) -> List[dict]:
    merged = merge_settings(settings)
    return [o for o in merged.get(key) or [] if o.get("active")]


def option_by_id(settings: dict, key: str, option_id: str) -> Optional[dict]:
    oid = str(option_id or "").strip()
    for opt in merge_settings(settings).get(key) or []:
        if opt.get("id") == oid:
            return opt
    return None


async def load_settings(db) -> dict:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return merge_settings(doc)


async def save_settings(db, payload: dict, actor: dict) -> dict:
    existing = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    expected = payload.get("doc_version")
    if expected is None:
        raise HTTPException(400, "doc_version wajib diisi")
    try:
        expected_i = int(expected)
    except (TypeError, ValueError):
        raise HTTPException(400, "doc_version tidak valid")
    current_version = int((existing or {}).get("doc_version") or 1)
    if existing and current_version != expected_i:
        raise HTTPException(409, "Pengaturan sudah diubah pengelola lain. Muat ulang lalu simpan lagi.")

    sanitized = {
        "key": SETTINGS_KEY,
        "no_order_reasons": _sanitize_category(
            payload.get("no_order_reasons", (existing or {}).get("no_order_reasons")),
            key="no_order_reasons",
        ),
        "complaint_categories": _sanitize_category(
            payload.get("complaint_categories", (existing or {}).get("complaint_categories")),
            key="complaint_categories",
        ),
        "issue_categories": _sanitize_category(
            payload.get("issue_categories", (existing or {}).get("issue_categories")),
            key="issue_categories",
        ),
        "doc_version": current_version + 1,
        "updated_at": now_iso(),
        "updated_by": (actor or {}).get("email"),
        "updated_by_name": (actor or {}).get("name"),
    }
    # Soft-delete protection: never drop ids that existed before (mark inactive instead)
    if existing:
        for key in CATEGORY_KEYS:
            prev_ids = {str(o.get("id")) for o in (existing.get(key) or []) if isinstance(o, dict)}
            next_ids = {str(o.get("id")) for o in sanitized[key]}
            missing = prev_ids - next_ids
            if missing:
                raise HTTPException(
                    400,
                    f"{key}: opsi yang sudah ada tidak boleh dihapus permanen ({', '.join(sorted(missing))}). "
                    "Nonaktifkan saja.",
                )

    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": sanitized}, upsert=True)
    await db.audit_logs.insert_one({
        "id": str(uuid.uuid4()),
        "action": "settings.crm_daily_report.update",
        "actor_email": (actor or {}).get("email"),
        "actor_name": (actor or {}).get("name"),
        "actor_role": (actor or {}).get("role"),
        "before": public_settings(existing) if existing else None,
        "after": public_settings(sanitized),
        "note": "update CRM daily report options",
        "at": now_iso(),
    })
    return public_settings(sanitized)
