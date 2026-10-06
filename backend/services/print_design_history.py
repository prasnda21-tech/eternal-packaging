"""Design Cetakan mutation history from existing audit_logs. Read-only DTO."""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

logger = logging.getLogger(__name__)

SCOPE_ALL = "all"
SCOPE_GLOBAL = "global"
SCOPE_BRANCH = "branch"
SCOPES = frozenset({SCOPE_ALL, SCOPE_GLOBAL, SCOPE_BRANCH})

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50

ACTION_UPDATE = "settings.design_cetakan.update"
ACTION_ASSET_UPLOAD = "settings.design_cetakan.asset_upload"
ACTION_ASSET_REMOVE = "settings.design_cetakan.asset_remove"
ACTION_BRANCH_UPDATE = "settings.design_cetakan.branch_update"
ACTION_BRANCH_RESET = "settings.design_cetakan.branch_reset"
ACTION_BRANCH_ASSET_UPLOAD = "settings.design_cetakan.branch_asset_upload"
ACTION_BRANCH_ASSET_REMOVE = "settings.design_cetakan.branch_asset_remove"

GLOBAL_ACTIONS = (ACTION_UPDATE, ACTION_ASSET_UPLOAD, ACTION_ASSET_REMOVE)
BRANCH_ACTIONS = (
    ACTION_BRANCH_UPDATE,
    ACTION_BRANCH_RESET,
    ACTION_BRANCH_ASSET_UPLOAD,
    ACTION_BRANCH_ASSET_REMOVE,
)
HISTORY_ACTIONS = GLOBAL_ACTIONS + BRANCH_ACTIONS

OPERATION_LABELS = {
    ACTION_UPDATE: "Pengaturan Global diperbarui",
    ACTION_BRANCH_UPDATE: "Pengaturan Cabang diperbarui",
    ACTION_BRANCH_RESET: "Cabang dikembalikan ke Global",
}

SLOT_NOUN = {
    "company_logo": "Logo perusahaan",
    "prepared_signature_image": "Tanda tangan Prepared",
    "approved_signature_image": "Tanda tangan Approved",
}

FIELD_LABELS = {
    "company_name": "Nama perusahaan",
    "address_lines": "Alamat",
    "accent_color": "Warna aksen",
    "bank_accounts": "Rekening bank",
    "invoice.bank_accounts": "Rekening bank",
    "prepared_label": "Label Prepared",
    "prepared_footer": "Footer Prepared",
    "approved_label": "Label Approved",
    "approved_footer": "Footer Approved",
    "company_logo": "Logo perusahaan",
    "prepared_signature_image": "Tanda tangan Prepared",
    "approved_signature_image": "Tanda tangan Approved",
}

OVERRIDE_FIELDS = (
    "company_name",
    "address_lines",
    "company_logo",
    "bank_accounts",
    "prepared_label",
    "prepared_footer",
    "approved_label",
    "approved_footer",
    "prepared_signature_image",
    "approved_signature_image",
)

REDACT_KEYS = frozenset({
    "password",
    "password_hash",
    "access_token",
    "token",
    "client_secret",
    "storage_path",
    "file_id",
    "provider",
    "provider_url",
    "object_key",
    "bucket",
    "binary",
    "bytes",
    "base64",
})


def _clip_scope(raw: Any) -> str:
    scope = str(raw or SCOPE_ALL).strip().lower() or SCOPE_ALL
    if scope not in SCOPES:
        raise HTTPException(400, "scope tidak valid")
    return scope


def _clip_page(raw: Any) -> int:
    try:
        page = int(raw)
    except (TypeError, ValueError):
        page = DEFAULT_PAGE
    return max(page, DEFAULT_PAGE)


def _clip_page_size(raw: Any) -> int:
    try:
        size = int(raw)
    except (TypeError, ValueError):
        size = DEFAULT_PAGE_SIZE
    if size < 1:
        size = DEFAULT_PAGE_SIZE
    return min(size, MAX_PAGE_SIZE)


def _digit_branch_id(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    bid = str(raw).strip()
    if not bid:
        return None
    if not bid.isdigit():
        raise HTTPException(400, "branch_id tidak valid")
    return bid


def _as_dict(raw: Any) -> Dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _actions_for_scope(scope: str) -> Tuple[str, ...]:
    if scope == SCOPE_GLOBAL:
        return GLOBAL_ACTIONS
    if scope == SCOPE_BRANCH:
        return BRANCH_ACTIONS
    return HISTORY_ACTIONS


def _event_scope(action: str) -> str:
    if action in BRANCH_ACTIONS:
        return SCOPE_BRANCH
    return SCOPE_GLOBAL


def _event_branch_id(doc: dict) -> Optional[str]:
    after = _as_dict(doc.get("after"))
    before = _as_dict(doc.get("before"))
    for src in (after, before):
        bid = str(src.get("branch_id") or "").strip()
        if bid.isdigit():
            return bid
    return None


def _actor_name(doc: dict) -> str:
    name = str(doc.get("actor_name") or "").strip()
    email = str(doc.get("actor_email") or "").strip()
    return name or email or "-"


def _actor_email(doc: dict) -> str:
    return str(doc.get("actor_email") or "").strip()


def _slot(doc: dict) -> str:
    after = _as_dict(doc.get("after"))
    return str(after.get("slot") or "").strip()


def operation_label(action: str, slot: str = "") -> str:
    if action in {ACTION_ASSET_UPLOAD, ACTION_BRANCH_ASSET_UPLOAD}:
        noun = SLOT_NOUN.get(slot)
        if noun:
            return f"{noun} diunggah"
    if action in {ACTION_ASSET_REMOVE, ACTION_BRANCH_ASSET_REMOVE}:
        noun = SLOT_NOUN.get(slot)
        if noun:
            return f"{noun} dihapus"
    return OPERATION_LABELS.get(action) or "Perubahan Design Cetakan"


def field_label(key: str) -> Optional[str]:
    return FIELD_LABELS.get(key)


def _override_value(override: dict, field: str) -> Any:
    src = _as_dict(override)
    inv = _as_dict(src.get("invoice"))
    if field == "bank_accounts":
        return inv.get("bank_accounts") if "bank_accounts" in inv else src.get("bank_accounts")
    if field in {"prepared_label", "prepared_footer", "approved_label", "approved_footer",
                 "prepared_signature_image", "approved_signature_image"}:
        return inv.get(field)
    return src.get(field)


def _override_present(override: dict, field: str) -> bool:
    src = _as_dict(override)
    inv = _as_dict(src.get("invoice"))
    if field == "bank_accounts":
        return "bank_accounts" in inv or "bank_accounts" in src
    if field in {"prepared_label", "prepared_footer", "approved_label", "approved_footer",
                 "prepared_signature_image", "approved_signature_image"}:
        return field in inv
    return field in src


def _changed_from_overrides(before_override: dict, after_override: dict) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for field in OVERRIDE_FIELDS:
        before_on = _override_present(before_override, field)
        after_on = _override_present(after_override, field)
        if not before_on and not after_on:
            continue
        if before_on != after_on or _override_value(before_override, field) != _override_value(after_override, field):
            label = field_label(field)
            if label:
                out.append({"key": field, "label": label})
    return out


def _reset_fields(before_override: dict) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for field in OVERRIDE_FIELDS:
        if _override_present(before_override, field):
            label = field_label(field)
            if label:
                out.append({"key": field, "label": label})
    return out


def _changed_fields(action: str, doc: dict) -> List[Dict[str, str]]:
    before = _as_dict(doc.get("before"))
    after = _as_dict(doc.get("after"))
    if action == ACTION_BRANCH_UPDATE:
        return _changed_from_overrides(_as_dict(before.get("override")), _as_dict(after.get("override")))
    if action == ACTION_BRANCH_RESET:
        return _reset_fields(_as_dict(before.get("override")))
    return []


def serialize_history_row(doc: dict, branch_names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """One sanitized history row. Never includes raw before/after."""
    src = _as_dict(doc)
    action = str(src.get("action") or "").strip()
    slot = _slot(src)
    scope = _event_scope(action)
    branch_id = _event_branch_id(src)
    names = branch_names or {}
    if scope == SCOPE_BRANCH:
        if branch_id:
            branch_name = names.get(branch_id) or f"Cabang {branch_id}"
        else:
            branch_name = "Cabang tidak diketahui"
    else:
        branch_name = "Global"
        branch_id = None
    changed = _changed_fields(action, src)
    label = operation_label(action, slot)
    row = {
        "id": str(src.get("id") or ""),
        "at": src.get("at"),
        "action": action,
        "operation_label": label,
        "scope": scope,
        "branch_id": branch_id,
        "branch_name": branch_name,
        "actor_name": _actor_name(src),
        "actor_email": _actor_email(src),
        "changed_fields": changed,
        "asset_slot": slot or None,
        "summary": label,
    }
    after = _as_dict(src.get("after"))
    for extra in ("source_scope", "source_branch_id", "target_branch_id"):
        if extra in REDACT_KEYS:
            continue
        value = after.get(extra)
        if value not in (None, ""):
            row[extra] = value
    return row


async def _lookup_names(ids: Iterable[str], name_lookup) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if name_lookup is None:
        return out
    for bid in ids:
        try:
            looked = await name_lookup(bid)
            label = str(looked or "").strip()
        except Exception:
            logger.warning("history branch name lookup failed id=%s", bid, exc_info=True)
            label = ""
        if label:
            out[bid] = label
    return out


def _mongo_query(actions: Tuple[str, ...], branch_id: Optional[str]) -> Dict[str, Any]:
    query: Dict[str, Any] = {"action": {"$in": list(actions)}}
    if branch_id:
        query["$or"] = [{"after.branch_id": branch_id}, {"before.branch_id": branch_id}]
    return query


async def list_print_design_history(
    db,
    *,
    scope: Any = SCOPE_ALL,
    branch_id: Any = None,
    page: Any = DEFAULT_PAGE,
    page_size: Any = DEFAULT_PAGE_SIZE,
    name_lookup=None,
) -> Dict[str, Any]:
    """Paged, sanitized Design Cetakan mutation history. Read-only."""
    chosen_scope = _clip_scope(scope)
    bid = _digit_branch_id(branch_id)
    page_n = _clip_page(page)
    size = _clip_page_size(page_size)
    actions = _actions_for_scope(chosen_scope)
    if bid and chosen_scope == SCOPE_GLOBAL:
        return {"items": [], "page": page_n, "page_size": size, "has_more": False}

    skip = (page_n - 1) * size
    query = _mongo_query(actions, bid)
    cursor = db.audit_logs.find(query, {"_id": 0})
    cursor = cursor.sort([("at", -1), ("id", -1)]).skip(skip).limit(size + 1)
    raw = await cursor.to_list(size + 1)
    has_more = len(raw) > size
    page_docs = raw[:size]

    unique_ids = []
    seen = set()
    for doc in page_docs:
        event_bid = _event_branch_id(doc)
        if event_bid and event_bid not in seen:
            seen.add(event_bid)
            unique_ids.append(event_bid)
    names = await _lookup_names(unique_ids, name_lookup)

    items = []
    for doc in page_docs:
        try:
            items.append(serialize_history_row(doc, names))
        except Exception:
            logger.warning("skip malformed design cetakan audit row id=%s", (doc or {}).get("id"), exc_info=True)
            items.append({
                "id": str((doc or {}).get("id") or ""),
                "at": (doc or {}).get("at"),
                "action": str((doc or {}).get("action") or ""),
                "operation_label": "Perubahan Design Cetakan",
                "scope": SCOPE_GLOBAL,
                "branch_id": None,
                "branch_name": "Global",
                "actor_name": "-",
                "actor_email": "",
                "changed_fields": [],
                "asset_slot": None,
                "summary": "Perubahan Design Cetakan",
            })
    return {"items": items, "page": page_n, "page_size": size, "has_more": has_more}
