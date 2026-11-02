"""Durable explicit mapping: Accurate sales label → users.id.

Authorization never uses the display name. Exact stripped-label lookup only.
No fuzzy match, no nickname, no case-fold, no auto-backfill.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

SETTINGS_KEY = "sales_user_mapping"
PERM_VIEW = "settings.sales_user_mapping.view"
PERM_MANAGE = "settings.sales_user_mapping.manage"
SALES_PWA_ROLE = "sales_eternal"
SALES_PWA_PERM = "sales.pwa.view"
SURFACE_LOCK_DETAIL = "Role sales_eternal hanya dapat memakai PWA Sales"

STATUS_MATCHED = "matched"
STATUS_MATCHED_MASTER = "matched_master"
STATUS_IDENTITY_CONFLICT = "identity_conflict"
STATUS_UNMATCHED = "unmatched"
STATUS_EMPTY = "empty"
ADMIN_STATUS_MAPPED = "terpetakan"
ADMIN_STATUS_NEEDS_VERIFICATION = "perlu verifikasi"

# Distinguishable ownership resolution source on new PO writes only.
SOURCE_MAPPING = "mapping"
SOURCE_MASTER_ALIAS = "master_alias"
SOURCE_CONFLICT = "identity_conflict"
SOURCE_NONE = None

_MATCHED_STATUSES = frozenset({STATUS_MATCHED, STATUS_MATCHED_MASTER})

SALES_ETERNAL_ALLOWED_PREFIXES = (
    "/api/auth",
    "/api/system",
    "/api/sales-pwa",
)

# Lookup keys only — never a production users.id. users.id is discovered at apply/audit time.
CANONICAL_SALES_PROFILES = (
    {
        "key": "ayub",
        "accurate_label": "Sholahudin Al - Ayubi",
        "email": "ayub@eternal.com",
        "employee_id": "EMP-0049",
        "role": SALES_PWA_ROLE,
    },
    {
        "key": "euis",
        "accurate_label": "Euis Risnawati",
        "email": "euis@eternal.com",
        "employee_id": "EMP-0052",
        "role": SALES_PWA_ROLE,
    },
)
MAPPING_TARGET_ROLES = frozenset({SALES_PWA_ROLE})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def exact_sales_label(value: Any) -> str:
    """Canonical mapping key: strip only. Does not fold case or collapse spaces."""
    return str(value or "").strip()


def session_user_id(user: Optional[dict]) -> str:
    """Immutable users.id from authenticated session. Never from request body."""
    user = user or {}
    return str(user.get("user_id") or user.get("id") or user.get("sub") or "").strip()


def is_sales_pwa_role(user: Optional[dict]) -> bool:
    return str((user or {}).get("role") or "").strip() == SALES_PWA_ROLE


def sales_owner_match(user: Optional[dict]) -> dict:
    """Mongo match for PO ownership. Empty dict if role is not sales_eternal."""
    if not is_sales_pwa_role(user):
        return {}
    uid = session_user_id(user)
    if not uid:
        return {"sales_user_id": {"$in": []}}
    return {"sales_user_id": uid}


def resolve_from_map(mapping: Dict[str, str], sales_label: Any) -> Tuple[Optional[str], str]:
    """Exact-key resolve. mapping keys must already be exact_sales_label()."""
    label = exact_sales_label(sales_label)
    if not label:
        return None, STATUS_EMPTY
    uid = str(mapping.get(label) or "").strip()
    if not uid:
        return None, STATUS_UNMATCHED
    return uid, STATUS_MATCHED


def mapping_dict_from_entries(entries: Optional[List[dict]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in entries or []:
        if not isinstance(row, dict):
            continue
        label = exact_sales_label(row.get("label"))
        uid = str(row.get("user_id") or "").strip()
        if label and uid:
            out[label] = uid
    return out


def _users_as_list(users: Any) -> List[dict]:
    if isinstance(users, dict):
        return [u for u in users.values() if isinstance(u, dict)]
    return [u for u in (users or []) if isinstance(u, dict)]


def discover_canonical_user(profile: dict, users: Any) -> Tuple[Optional[dict], List[str]]:
    """Fail-closed identity: exact email AND exact employee_id AND role, one users.id."""
    errors: List[str] = []
    key = str(profile.get("key") or "sales")
    email = exact_sales_label(profile.get("email"))
    emp = exact_sales_label(profile.get("employee_id"))
    role = exact_sales_label(profile.get("role") or SALES_PWA_ROLE)
    rows = _users_as_list(users)
    by_email = [u for u in rows if exact_sales_label(u.get("email")) == email]
    by_emp = [u for u in rows if exact_sales_label(u.get("employee_id")) == emp]
    if not email:
        errors.append(f"{key}: email lookup kosong")
    if not emp:
        errors.append(f"{key}: employee_id lookup kosong")
    if len(by_email) != 1:
        errors.append(f"{key}: email {email!r} cocok {len(by_email)} user (wajib tepat 1)")
    if len(by_emp) != 1:
        errors.append(f"{key}: employee_id {emp!r} cocok {len(by_emp)} user (wajib tepat 1)")
    if errors:
        return None, errors
    email_user = by_email[0]
    emp_user = by_emp[0]
    email_id = str(email_user.get("id") or "").strip()
    emp_id = str(emp_user.get("id") or "").strip()
    if not email_id or email_id != emp_id:
        errors.append(f"{key}: email dan employee_id menuju users.id berbeda")
        return None, errors
    found_role = exact_sales_label(email_user.get("role"))
    if found_role != role:
        errors.append(f"{key}: role {found_role!r} != {role!r}")
    status = exact_sales_label(email_user.get("status") or "active") or "active"
    if status != "active":
        errors.append(f"{key}: status={status}, wajib active")
    if errors:
        return None, errors
    return email_user, []


def discover_canonical_users(users: Any) -> Tuple[Dict[str, dict], List[str]]:
    """Resolve Ayub and Euis. Ambiguous or missing either person fails closed."""
    found: Dict[str, dict] = {}
    errors: List[str] = []
    for profile in CANONICAL_SALES_PROFILES:
        user, errs = discover_canonical_user(profile, users)
        errors.extend(errs)
        if user:
            found[str(profile["key"])] = {
                "profile": profile,
                "user": user,
                "user_id": str(user.get("id") or "").strip(),
                "accurate_label": exact_sales_label(profile.get("accurate_label")),
            }
    ids = [row["user_id"] for row in found.values() if row.get("user_id")]
    if len(ids) != len(set(ids)):
        errors.append("Ayub dan Euis resolve ke users.id yang sama")
    if "ayub" not in found:
        errors.append("Ayub tidak ditemukan secara deterministik")
    if "euis" not in found:
        errors.append("Euis tidak ditemukan secara deterministik")
    if errors:
        return {}, errors
    return found, []


def apply_sales_user_id(doc: dict, user_id: Optional[str]) -> None:
    """Always write the field so a previous owner cannot linger on unmatched labels."""
    doc["sales_user_id"] = user_id or None


def sales_needs_verification(status: Optional[str], sales_user_id: Any = None) -> bool:
    st = str(status or "").strip()
    if st in _MATCHED_STATUSES:
        return False
    if st == STATUS_IDENTITY_CONFLICT:
        return True
    if st:
        return st not in _MATCHED_STATUSES
    return not str(sales_user_id or "").strip()


def sales_ownership_admin_label(status: Optional[str], sales_user_id: Any = None) -> str:
    if str(status or "").strip() == STATUS_IDENTITY_CONFLICT:
        return "konflik identitas"
    if sales_needs_verification(status, sales_user_id):
        return ADMIN_STATUS_NEEDS_VERIFICATION
    return ADMIN_STATUS_MAPPED


def attach_sales_ownership_admin_fields(order: dict) -> None:
    """Derived admin-facing ownership status. Does not authorize PWA access."""
    status = order.get("sales_ownership_status")
    uid = order.get("sales_user_id")
    order["sales_needs_verification"] = sales_needs_verification(status, uid)
    order["sales_ownership_admin_label"] = sales_ownership_admin_label(status, uid)


def public_mapping(doc: Optional[dict]) -> dict:
    doc = doc or {}
    entries = []
    for row in doc.get("entries") or []:
        if not isinstance(row, dict):
            continue
        label = exact_sales_label(row.get("label"))
        uid = str(row.get("user_id") or "").strip()
        if not label:
            continue
        entries.append({
            "label": label,
            "user_id": uid or None,
        })
    return {
        "key": SETTINGS_KEY,
        "entries": entries,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "updated_by_name": doc.get("updated_by_name"),
        "updated_by_role": doc.get("updated_by_role"),
    }


async def load_mapping_doc(db) -> dict:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return doc or {"key": SETTINGS_KEY, "entries": []}


async def load_mapping_dict(db) -> Dict[str, str]:
    doc = await load_mapping_doc(db)
    return mapping_dict_from_entries(doc.get("entries"))


async def resolve_sales_user_id(db, sales_label: Any) -> Tuple[Optional[str], str]:
    mapping = await load_mapping_dict(db)
    return resolve_from_map(mapping, sales_label)


async def _eligible_sales_eternal_user_id(db, user_id: Any) -> Optional[str]:
    """Return users.id only when active sales_eternal. Else None."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    row = await db.users.find_one(
        {"id": uid},
        {"_id": 0, "id": 1, "role": 1, "status": 1},
    )
    if not row:
        return None
    if exact_sales_label(row.get("role")) != SALES_PWA_ROLE:
        return None
    status = exact_sales_label(row.get("status") or "active") or "active"
    if status != "active":
        return None
    return str(row.get("id") or "").strip() or None


async def resolve_master_alias_user_id(db, sales_label: Any) -> Tuple[Optional[str], Optional[str]]:
    """Exact alias → one active Master → eligible Eternal user.

    Returns (user_id, sales_master_id). Never fuzzy. Never invents users.
    """
    label = exact_sales_label(sales_label)
    if not label:
        return None, None
    from services import sales_master_aliases as aliases

    mid, st = await aliases.resolve_master_id_for_label(db, label)
    if st != "matched" or not mid:
        return None, None
    master = await db.sales_master.find_one({"id": mid}, {"_id": 0, "id": 1, "active": 1, "user_id": 1})
    if not master:
        return None, None
    if master.get("active") is False:
        return None, mid
    linked = await _eligible_sales_eternal_user_id(db, master.get("user_id"))
    if not linked:
        return None, mid
    return linked, mid


async def resolve_new_po_ownership(
    db,
    sales_label: Any,
    *,
    mapping: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], str, Optional[str]]:
    """Ownership for NEW PO writes only.

    Priority:
    1) exact sales_user_mapping
    2) if mapping empty/unmatched → verified alias → active Master.user_id (sales_eternal)
    Conflict when both resolve to different users → no assignment.

    Returns (user_id, status, source).
    """
    if mapping is None:
        mapping = await load_mapping_dict(db)
    map_uid, map_status = resolve_from_map(mapping, sales_label)
    master_uid, _master_id = await resolve_master_alias_user_id(db, sales_label)

    if map_status == STATUS_EMPTY:
        return None, STATUS_EMPTY, SOURCE_NONE

    if map_uid and master_uid and map_uid != master_uid:
        return None, STATUS_IDENTITY_CONFLICT, SOURCE_CONFLICT

    if map_uid:
        return map_uid, STATUS_MATCHED, SOURCE_MAPPING

    if master_uid:
        return master_uid, STATUS_MATCHED_MASTER, SOURCE_MASTER_ALIAS

    return None, STATUS_UNMATCHED if map_status == STATUS_UNMATCHED else map_status, SOURCE_NONE


async def attach_sales_ownership(
    db,
    doc: dict,
    mapping: Optional[Dict[str, str]] = None,
    *,
    allow_master_fallback: bool = False,
) -> Tuple[Optional[str], str]:
    """Attach sales_user_id from mapping (default) or mapping+Master fallback.

    allow_master_fallback=True is for NEW imported/synced inserts only.
    Never use to rewrite historical ownership in place.
    """
    if allow_master_fallback:
        uid, status, source = await resolve_new_po_ownership(
            db, doc.get("sales"), mapping=mapping,
        )
        apply_sales_user_id(doc, uid)
        doc["sales_ownership_status"] = status
        doc["sales_ownership_source"] = source
        attach_sales_ownership_admin_fields(doc)
        return uid, status

    if mapping is None:
        uid, status = await resolve_sales_user_id(db, doc.get("sales"))
    else:
        uid, status = resolve_from_map(mapping, doc.get("sales"))
    apply_sales_user_id(doc, uid)
    doc["sales_ownership_status"] = status
    doc["sales_ownership_source"] = SOURCE_MAPPING if uid else SOURCE_NONE
    attach_sales_ownership_admin_fields(doc)
    return uid, status


def sales_eternal_path_allowed(path: str) -> bool:
    raw = str(path or "").split("?", 1)[0]
    for prefix in SALES_ETERNAL_ALLOWED_PREFIXES:
        if raw == prefix or raw.startswith(prefix + "/"):
            return True
    return False


def enforce_sales_eternal_surface(request: Any, user: Optional[dict] = None) -> None:
    """sales_eternal may only use PWA + auth/health. Generic 403, no data leak."""
    if not is_sales_pwa_role(user):
        return
    path = ""
    url = getattr(request, "url", None)
    if url is not None:
        path = str(getattr(url, "path", "") or "")
    elif isinstance(request, str):
        path = request
    if sales_eternal_path_allowed(path):
        return
    raise HTTPException(403, SURFACE_LOCK_DETAIL)


def reject_sales_pwa_bypass(user: Optional[dict]) -> None:
    """Operational endpoints are not an authorization path for PWA Sales."""
    if is_sales_pwa_role(user):
        raise HTTPException(403, SURFACE_LOCK_DETAIL)


async def ensure_owned_po_id(db, user: Optional[dict], po_id: Any) -> None:
    from services.operational_branch import require_document_branch
    doc = await db.orders.find_one({"id": str(po_id or "")}, {"_id": 0, "id": 1, "branch_id": 1, "operational_branch_id": 1})
    if not doc:
        raise HTTPException(404, "PO tidak ditemukan")
    await require_document_branch(db, user, doc)
    if not is_sales_pwa_role(user):
        return
    oid = str(po_id or "").strip()
    if not oid:
        raise HTTPException(404, "PO tidak ditemukan")
    found = await db.orders.find_one({"id": oid, **sales_owner_match(user)}, {"_id": 0, "id": 1})
    if not found:
        raise HTTPException(404, "PO tidak ditemukan")


def _sanitize_entries(raw_entries: Any) -> List[dict]:
    if raw_entries is None:
        return []
    if not isinstance(raw_entries, list):
        raise HTTPException(400, "entries harus berupa daftar")
    seen_labels = set()
    out: List[dict] = []
    for i, row in enumerate(raw_entries):
        if not isinstance(row, dict):
            raise HTTPException(400, f"entries[{i}] tidak valid")
        label = exact_sales_label(row.get("label"))
        uid = str(row.get("user_id") or "").strip()
        if not label:
            raise HTTPException(400, f"entries[{i}].label wajib diisi")
        if label in seen_labels:
            raise HTTPException(400, f"Label Accurate duplikat: {label}")
        if not uid:
            raise HTTPException(400, f"entries[{i}].user_id wajib diisi")
        seen_labels.add(label)
        out.append({"label": label, "user_id": uid})
    return out


async def save_mapping(db, payload: dict, user: dict) -> dict:
    entries = _sanitize_entries(payload.get("entries"))
    user_ids = sorted({e["user_id"] for e in entries})
    if user_ids:
        found = await db.users.find(
            {"id": {"$in": user_ids}},
            {"_id": 0, "id": 1, "role": 1, "email": 1, "status": 1},
        ).to_list(len(user_ids))
        found_by_id = {str(u.get("id") or "").strip(): u for u in found}
        missing = [uid for uid in user_ids if uid not in found_by_id]
        if missing:
            raise HTTPException(400, f"users.id tidak ditemukan: {', '.join(missing)}")
        bad_role = []
        for uid in user_ids:
            role = exact_sales_label(found_by_id[uid].get("role"))
            if role not in MAPPING_TARGET_ROLES:
                bad_role.append(uid)
        if bad_role:
            raise HTTPException(
                400,
                f"users.id bukan role {SALES_PWA_ROLE}: {', '.join(bad_role)}",
            )
    previous = await load_mapping_doc(db)
    actor = {
        "id": session_user_id(user),
        "email": user.get("email"),
        "name": user.get("name"),
        "role": user.get("role"),
    }
    doc = {
        "key": SETTINGS_KEY,
        "entries": entries,
        "updated_at": now_iso(),
        "updated_by": actor["email"] or actor["id"],
        "updated_by_name": actor["name"],
        "updated_by_role": actor["role"],
    }
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": doc}, upsert=True)
    await db.audit_logs.insert_one({
        "action": "sales_user_mapping.update",
        "actor_email": actor.get("email"),
        "actor_role": actor.get("role"),
        "actor_name": actor.get("name"),
        "target_email": SETTINGS_KEY,
        "before": {"entries": (previous or {}).get("entries") or []},
        "after": {"entries": entries},
        "note": "Explicit Accurate sales label → users.id mapping",
        "at": now_iso(),
    })
    return public_mapping(doc)
