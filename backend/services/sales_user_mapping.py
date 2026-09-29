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
STATUS_UNMATCHED = "unmatched"
STATUS_EMPTY = "empty"
ADMIN_STATUS_MAPPED = "terpetakan"
ADMIN_STATUS_NEEDS_VERIFICATION = "perlu verifikasi"

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
# Who may be the sales owner of a transaction. This is attribution only.
# It does not grant PWA, dashboard, PO, or invoice permissions.
SALES_RESPONSIBLE_ROLES = frozenset({
    SALES_PWA_ROLE,
    "holding_team",
    "direktur_utama",
})
MAPPING_TARGET_ROLES = SALES_RESPONSIBLE_ROLES


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


def account_is_active(user: Optional[dict]) -> bool:
    """Same active-account rule as the sales-order user list: status != inactive."""
    return str((user or {}).get("status") or "") != "inactive"


def responsible_role_allowed(user: Optional[dict]) -> bool:
    return exact_sales_label((user or {}).get("role")) in SALES_RESPONSIBLE_ROLES


def document_sales_label(doc: Optional[dict]) -> str:
    """Invoice/PO display label. sales, then sales_name. Not a person lookup."""
    for key in ("sales", "sales_name"):
        value = exact_sales_label((doc or {}).get(key))
        if value:
            return value
    return ""


def dashboard_sales_owner(invoice: Optional[dict], mapping: Dict[str, str]) -> Tuple[Optional[str], str]:
    """Stable owner for one invoice. Returns (users.id or None, reason).

    Snapshot id on the invoice wins. The Accurate-label map is only a fallback
    when the invoice has no id. A stored id is not dropped because the account
    is inactive or no longer a sales role. Conflicting stored ids, or an
    explicit conflict flag, stay unassigned. Current PO, creator, and poster
    are not read here.
    """
    invoice = invoice or {}
    if invoice.get("sales_attribution_conflict") is True:
        return None, "conflict"
    stored = exact_sales_label(invoice.get("sales_user_id"))
    alternate = exact_sales_label(invoice.get("sales_id"))
    if stored and alternate and stored != alternate:
        return None, "conflict"
    if stored or alternate:
        return stored or alternate, "snapshot"
    label = document_sales_label(invoice)
    mapped, status = resolve_from_map(mapping, label)
    if status == STATUS_MATCHED and mapped:
        return mapped, "mapping"
    if not label:
        return None, "empty"
    return None, "unmatched"


def snapshot_invoice_sales(order: Optional[dict], mapping: Dict[str, str], *, label: Any = None) -> dict:
    """Identity copied onto a new invoice from the PO at issue time.

    A single stored users.id on the PO is the snapshot. The label map is used
    only when that id is empty. Two different id fields on the PO are a
    conflict and are not resolved by the label or by the current map.
    A later change to the label map does not discard the stored id.
    """
    order = order or {}
    shown = exact_sales_label(label) if label is not None else document_sales_label(order)
    if label is None and not shown:
        shown = exact_sales_label(order.get("seller") or order.get("sales_rep"))
    stored = exact_sales_label(order.get("sales_user_id"))
    alternate = exact_sales_label(order.get("sales_id"))
    if stored and alternate and stored != alternate:
        return {
            "sales": shown,
            "sales_user_id": None,
            "sales_attribution_conflict": True,
        }
    chosen = stored or alternate
    if chosen:
        return {
            "sales": shown,
            "sales_user_id": chosen,
            "sales_attribution_conflict": False,
        }
    mapped, status = resolve_from_map(mapping, shown)
    return {
        "sales": shown,
        "sales_user_id": mapped if status == STATUS_MATCHED and mapped else None,
        "sales_attribution_conflict": False,
    }


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
    if st:
        return st != STATUS_MATCHED
    return not str(sales_user_id or "").strip()


def sales_ownership_admin_label(status: Optional[str], sales_user_id: Any = None) -> str:
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


async def attach_sales_ownership(
    db,
    doc: dict,
    mapping: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], str]:
    if mapping is None:
        uid, status = await resolve_sales_user_id(db, doc.get("sales"))
    else:
        uid, status = resolve_from_map(mapping, doc.get("sales"))
    apply_sales_user_id(doc, uid)
    doc["sales_ownership_status"] = status
    attach_sales_ownership_admin_fields(doc)
    return uid, status


async def assign_sales_responsible(
    db,
    doc: dict,
    *,
    label: Any,
    explicit_user_id: Any = None,
    explicit_sent: bool = False,
) -> Tuple[Optional[str], str]:
    """Store the sales label and, when an account was chosen, its users.id.

    A typed label alone still resolves only through the exact mapping.
    An explicit id is accepted only for an active sales, holding, or director
    account. That id is kept even when the label map currently points elsewhere.
    The chosen user's role and permissions are not modified.
    """
    doc["sales"] = exact_sales_label(label) or None
    uid = exact_sales_label(explicit_user_id)
    if not explicit_sent or not uid:
        return await attach_sales_ownership(db, doc)
    user = await db.users.find_one(
        {"id": uid},
        {"_id": 0, "id": 1, "role": 1, "status": 1, "name": 1},
    )
    if not user or exact_sales_label(user.get("id")) != uid:
        raise HTTPException(400, "Penanggung jawab penjualan tidak ditemukan")
    if not account_is_active(user) or not responsible_role_allowed(user):
        raise HTTPException(
            400,
            "Penanggung jawab penjualan harus akun aktif dengan role sales, holding, atau direktur",
        )
    apply_sales_user_id(doc, uid)
    doc["sales_ownership_status"] = STATUS_MATCHED
    attach_sales_ownership_admin_fields(doc)
    return uid, STATUS_MATCHED


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
    previous = await load_mapping_doc(db)
    previous_pairs = {
        (exact_sales_label(row.get("label")), str(row.get("user_id") or "").strip())
        for row in (previous or {}).get("entries") or []
        if isinstance(row, dict)
        and exact_sales_label(row.get("label"))
        and str(row.get("user_id") or "").strip()
    }
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
        bad_inactive = []
        for row in entries:
            pair = (row["label"], row["user_id"])
            # Only an unchanged (label, user_id) pair may stay after the
            # account is deactivated or its role changes. A new label for
            # that same user is a new assignment.
            if pair in previous_pairs:
                continue
            target = found_by_id[row["user_id"]]
            role = exact_sales_label(target.get("role"))
            if role not in MAPPING_TARGET_ROLES:
                bad_role.append(row["user_id"])
            elif not account_is_active(target):
                bad_inactive.append(row["user_id"])
        if bad_role:
            raise HTTPException(
                400,
                f"users.id bukan role sales, holding, atau direktur: {', '.join(bad_role)}",
            )
        if bad_inactive:
            raise HTTPException(
                400,
                f"users.id bukan akun aktif: {', '.join(bad_inactive)}",
            )
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
