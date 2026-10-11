"""Phase 3 — read-only Sales Invoice / Receipt lifecycle capability.

Reports whether auto create/post, edit revision, and business cancel are
available to the authenticated user. Fail closed. No writes. No secrets.
Does not replace mutation authorization on write endpoints.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# Public operation keys (stable contract for FE).
OP_AUTO_CREATE_POST = "auto_create_post"
OP_EDIT_REVISION = "edit_revision"
OP_BUSINESS_CANCEL = "business_cancel"
OP_LEGACY_MANUAL = "legacy_manual_actions_required"

# Index contracts (name + key + unique + partial). Names are checked first;
# equivalent unique key+partial also accepted where modules already do so.


def _index_key_fields(meta: dict) -> List[str]:
    keys = (meta or {}).get("key")
    if keys is None:
        return []
    if isinstance(keys, dict):
        return [str(k) for k in keys.keys()]
    try:
        items = list(keys)
    except TypeError:
        return []
    out: List[str] = []
    for item in items:
        if isinstance(item, (list, tuple)) and item:
            out.append(str(item[0]))
        elif isinstance(item, dict):
            out.extend(str(k) for k in item.keys())
        else:
            return []
    return out


def _partial_matches(actual: Any, expected: Optional[dict]) -> bool:
    if expected is None:
        return True
    if not isinstance(actual, dict):
        return False
    for key, want in expected.items():
        if actual.get(key) != want:
            return False
    return True


async def _collection_index_ready(
    db,
    collection: str,
    *,
    name: str,
    key_fields: Sequence[str],
    unique: bool = True,
    partial: Optional[dict] = None,
    accept_equivalent_key: bool = True,
) -> bool:
    """True only when a compatible unique index exists. Read-only."""
    try:
        info = await db[collection].index_information()
    except Exception:
        return False
    if not isinstance(info, dict):
        return False
    expected = list(key_fields)

    def _ok(meta: dict) -> bool:
        if unique and not meta.get("unique"):
            return False
        if _index_key_fields(meta) != expected:
            return False
        return _partial_matches(meta.get("partialFilterExpression"), partial)

    named = info.get(name)
    if isinstance(named, dict) and _ok(named):
        return True
    if not accept_equivalent_key:
        return False
    for meta in info.values():
        if isinstance(meta, dict) and _ok(meta):
            return True
    return False


async def primary_journal_index_ready(db) -> bool:
    from services.financial_journal_foundation import (
        REQUIRED_PRIMARY_INDEX_NAME,
        REQUIRED_PRIMARY_INDEX_PARTIAL,
    )

    return await _collection_index_ready(
        db,
        "journal_entries",
        name=REQUIRED_PRIMARY_INDEX_NAME,
        key_fields=("source_type", "source_id"),
        unique=True,
        partial=dict(REQUIRED_PRIMARY_INDEX_PARTIAL),
        accept_equivalent_key=False,
    )


async def invoice_edit_intent_index_ready(db) -> bool:
    return await _collection_index_ready(
        db,
        "sales_invoice_edit_revisions",
        name="uniq_sales_invoice_edit_revision_key",
        key_fields=("invoice_id", "idempotency_key"),
        unique=True,
        partial=None,
        accept_equivalent_key=True,
    )


async def receipt_edit_intent_index_ready(db) -> bool:
    from services.sales_receipt_edit_revision import REQUIRED_INTENT_INDEX_NAME

    return await _collection_index_ready(
        db,
        "sales_receipt_edit_revisions",
        name=REQUIRED_INTENT_INDEX_NAME,
        key_fields=("receipt_id", "idempotency_key"),
        unique=True,
        partial=None,
        accept_equivalent_key=True,
    )


async def business_cancel_intent_index_ready(db) -> bool:
    from services.sales_business_cancel import sales_business_cancel_index_ready

    try:
        return bool(await sales_business_cancel_index_ready(db))
    except Exception:
        return False


async def invoice_auto_indexes_ready(db) -> bool:
    from services.sales_invoice_auto_lifecycle import (
        intent_idempotency_index_ready,
        sales_invoice_idempotency_index_ready,
    )

    try:
        return bool(
            await sales_invoice_idempotency_index_ready(db)
            and await intent_idempotency_index_ready(db)
        )
    except Exception:
        return False


async def receipt_auto_indexes_ready(db) -> bool:
    from services.sales_receipt_auto_lifecycle import (
        intent_idempotency_index_ready,
        sales_receipt_idempotency_index_ready,
    )

    try:
        return bool(
            await sales_receipt_idempotency_index_ready(db)
            and await intent_idempotency_index_ready(db)
        )
    except Exception:
        return False


# Must match server.create_sales_invoice_endpoint /
# recover_auto_sales_invoice_accounting_endpoint (_require_any_perm).
INVOICE_CREATE_ENDPOINT_PERMS: Tuple[str, ...] = (
    "sales.invoice.create",
    "sales_order.create",
    "po.edit",
)


async def _role_defaults_from_db(db) -> Dict[str, List[str]]:
    """Same source as server._role_defaults_override — role_permissions docs."""
    try:
        docs = await db.role_permissions.find(
            {}, {"_id": 0, "role": 1, "permissions": 1}
        ).to_list(50)
    except Exception:
        return {}
    out: Dict[str, List[str]] = {}
    for doc in docs or []:
        role = str((doc or {}).get("role") or "").strip()
        if not role:
            continue
        perms = (doc or {}).get("permissions")
        if isinstance(perms, list):
            out[role] = list(perms)
    return out


def _user_has_perm(
    user: Optional[dict],
    perm: str,
    *,
    opt_in: bool = False,
    role_defaults_override: Optional[Dict[str, List[str]]] = None,
) -> bool:
    from permissions import OPT_IN_ONLY_PERMISSIONS, effective_permissions, opt_in_permission_granted

    role = str((user or {}).get("role") or "")
    override = (user or {}).get("permissions_override") or {}
    if opt_in or perm in OPT_IN_ONLY_PERMISSIONS:
        return bool(opt_in_permission_granted(role, override, perm))
    if role == "admin":
        return True
    perms = effective_permissions(role, override, role_defaults_override)
    return perm in perms


def _user_has_any_perm(
    user: Optional[dict],
    perms: Sequence[str],
    *,
    opt_in: bool = False,
    role_defaults_override: Optional[Dict[str, List[str]]] = None,
) -> bool:
    """True when any listed perm is granted — mirrors server._require_any_perm."""
    for perm in perms:
        if _user_has_perm(
            user,
            perm,
            opt_in=opt_in,
            role_defaults_override=role_defaults_override,
        ):
            return True
    return False


def _doc_pending_or_failed(doc: Optional[dict]) -> Tuple[bool, Optional[str]]:
    """Document lifecycle state — separate from system capability readiness."""
    if not doc:
        return False, None
    auto = str(doc.get("auto_accounting_status") or "").strip().lower()
    if auto in {"pending", "failed", "gagal"}:
        return True, auto
    if doc.get("revision_incomplete") is True or doc.get("edit_revision_incomplete") is True:
        return True, "revision_incomplete"
    progress = str(doc.get("business_cancel_progress") or "").strip().lower()
    if progress and progress not in {"", "complete", "completed", "failed"}:
        return True, f"cancel_{progress}"
    if progress == "failed":
        return True, "cancel_failed"
    return False, None


async def _invoice_auto_ready(db) -> Tuple[bool, List[str]]:
    from services.sales_invoice_auto_lifecycle import is_auto_sales_invoice_lifecycle_enabled

    reasons: List[str] = []
    try:
        if not await is_auto_sales_invoice_lifecycle_enabled(db):
            reasons.append("auto_flag_off")
        if not await invoice_auto_indexes_ready(db):
            reasons.append("auto_indexes_not_ready")
    except Exception:
        reasons.append("readiness_unavailable")
    return (len(reasons) == 0), reasons


async def _receipt_auto_ready(db) -> Tuple[bool, List[str]]:
    from services.sales_receipt_auto_lifecycle import is_auto_sales_receipt_lifecycle_enabled

    reasons: List[str] = []
    try:
        if not await is_auto_sales_receipt_lifecycle_enabled(db):
            reasons.append("auto_flag_off")
        if not await receipt_auto_indexes_ready(db):
            reasons.append("auto_indexes_not_ready")
    except Exception:
        reasons.append("readiness_unavailable")
    return (len(reasons) == 0), reasons


async def _invoice_edit_ready(db) -> Tuple[bool, List[str]]:
    from services.financial_journal_foundation import (
        is_financial_journal_revision_enabled,
        revision_source_allowlist,
    )
    from services.sales_invoice_edit_revision import is_sales_invoice_edit_revision_enabled
    from services.sales_invoice_posting import SOURCE_TYPE

    reasons: List[str] = []
    try:
        if not await is_sales_invoice_edit_revision_enabled(db):
            reasons.append("edit_revision_flag_off")
        if not await is_financial_journal_revision_enabled(db):
            reasons.append("global_flag_off")
        if SOURCE_TYPE not in await revision_source_allowlist(db):
            reasons.append("not_allowlisted")
        if not await primary_journal_index_ready(db):
            reasons.append("primary_index_not_ready")
        if not await invoice_edit_intent_index_ready(db):
            reasons.append("intent_index_not_ready")
    except Exception:
        reasons.append("readiness_unavailable")
    return (len(reasons) == 0), reasons


async def _receipt_edit_ready(db) -> Tuple[bool, List[str]]:
    from services.sales_receipt_edit_revision import _activation_blockers

    try:
        blockers = await _activation_blockers(db)
    except Exception:
        return False, ["readiness_unavailable"]
    return (len(blockers) == 0), list(blockers or [])


async def _cancel_ready(db, *, kind: str) -> Tuple[bool, List[str]]:
    from services.sales_business_cancel import (
        is_sales_invoice_business_cancel_enabled,
        is_sales_receipt_business_cancel_enabled,
    )

    reasons: List[str] = []
    try:
        enabled = (
            await is_sales_invoice_business_cancel_enabled(db)
            if kind == "invoice"
            else await is_sales_receipt_business_cancel_enabled(db)
        )
        if not enabled:
            reasons.append("cancel_flag_off")
        if not await business_cancel_intent_index_ready(db):
            reasons.append("cancel_index_not_ready")
    except Exception:
        reasons.append("readiness_unavailable")
    return (len(reasons) == 0), reasons


def _public_op(*, available: bool, reasons: List[str], document_state: Optional[str] = None) -> Dict[str, Any]:
    """Capability slice — no flag names that leak allowlist internals as values."""
    out: Dict[str, Any] = {"available": bool(available)}
    # Stable coarse reasons only (no index specs / tokens).
    safe = []
    for r in reasons or []:
        code = str(r or "").strip()
        if not code:
            continue
        if any(x in code.lower() for x in ("token", "secret", "password", "mongo")):
            continue
        safe.append(code[:64])
    if safe and not available:
        out["reasons"] = safe[:8]
    if document_state:
        out["document_state"] = document_state
    return out


async def build_invoice_lifecycle_capability(
    db,
    user: Optional[dict],
    document: Optional[dict] = None,
) -> Dict[str, Any]:
    """Read-only invoice lifecycle capability for the authenticated user."""
    pending, doc_state = _doc_pending_or_failed(document)
    auto_sys, auto_reasons = await _invoice_auto_ready(db)
    edit_sys, edit_reasons = await _invoice_edit_ready(db)
    cancel_sys, cancel_reasons = await _cancel_ready(db, kind="invoice")

    # Create/auto-post must match endpoint any-of auth + role_permissions docs.
    role_defaults = await _role_defaults_from_db(db)
    can_create = _user_has_any_perm(
        user,
        INVOICE_CREATE_ENDPOINT_PERMS,
        role_defaults_override=role_defaults,
    )
    can_edit = _user_has_perm(user, "sales.invoice.edit")
    can_cancel = _user_has_perm(user, "sales.invoice.cancel", opt_in=True)

    auto_available = auto_sys and can_create
    if not can_create:
        auto_reasons = list(auto_reasons) + ["permission_missing"]
    edit_available = edit_sys and can_edit
    if not can_edit:
        edit_reasons = list(edit_reasons) + ["permission_missing"]
    cancel_available = cancel_sys and can_cancel
    if not can_cancel:
        cancel_reasons = list(cancel_reasons) + ["permission_missing"]

    # Legacy Finalisasi/Post/Reverse still required when auto create/post is not ready.
    legacy_required = not auto_available

    return {
        "document_type": "sales_invoice",
        OP_AUTO_CREATE_POST: _public_op(
            available=auto_available,
            reasons=auto_reasons,
            document_state=doc_state if pending else None,
        ),
        OP_EDIT_REVISION: _public_op(available=edit_available, reasons=edit_reasons),
        OP_BUSINESS_CANCEL: _public_op(available=cancel_available, reasons=cancel_reasons),
        OP_LEGACY_MANUAL: legacy_required,
        "document_pending_or_failed": pending,
    }


async def build_receipt_lifecycle_capability(
    db,
    user: Optional[dict],
    document: Optional[dict] = None,
) -> Dict[str, Any]:
    """Read-only receipt lifecycle capability for the authenticated user."""
    pending, doc_state = _doc_pending_or_failed(document)
    auto_sys, auto_reasons = await _receipt_auto_ready(db)
    edit_sys, edit_reasons = await _receipt_edit_ready(db)
    cancel_sys, cancel_reasons = await _cancel_ready(db, kind="receipt")

    can_create = _user_has_perm(user, "sales.receipt.create")
    can_edit = _user_has_perm(user, "sales.receipt.edit", opt_in=True)
    can_cancel = _user_has_perm(user, "sales.receipt.cancel", opt_in=True)

    auto_available = auto_sys and can_create
    if not can_create:
        auto_reasons = list(auto_reasons) + ["permission_missing"]
    edit_available = edit_sys and can_edit
    if not can_edit:
        edit_reasons = list(edit_reasons) + ["permission_missing"]
    cancel_available = cancel_sys and can_cancel
    if not can_cancel:
        cancel_reasons = list(cancel_reasons) + ["permission_missing"]

    legacy_required = not auto_available

    return {
        "document_type": "sales_receipt",
        OP_AUTO_CREATE_POST: _public_op(
            available=auto_available,
            reasons=auto_reasons,
            document_state=doc_state if pending else None,
        ),
        OP_EDIT_REVISION: _public_op(available=edit_available, reasons=edit_reasons),
        OP_BUSINESS_CANCEL: _public_op(available=cancel_available, reasons=cancel_reasons),
        OP_LEGACY_MANUAL: legacy_required,
        "document_pending_or_failed": pending,
    }


async def index_readiness_snapshot(db) -> Dict[str, bool]:
    """Coarse read-only index readiness map (booleans only — no specs)."""
    return {
        "primary_journal": await primary_journal_index_ready(db),
        "invoice_auto": await invoice_auto_indexes_ready(db),
        "receipt_auto": await receipt_auto_indexes_ready(db),
        "invoice_edit_intent": await invoice_edit_intent_index_ready(db),
        "receipt_edit_intent": await receipt_edit_intent_index_ready(db),
        "business_cancel_intent": await business_cancel_intent_index_ready(db),
    }
