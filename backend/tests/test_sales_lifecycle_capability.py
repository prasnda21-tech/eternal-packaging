"""Phase 3 — read-only lifecycle capability. Synthetic local data only."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_lifecycle_capability_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from services.financial_journal_foundation import ALLOWLIST_KEY, FLAG_KEY as JOURNAL_FLAG
from services.journal_core import ensure_indexes as ensure_journal
from services.sales_business_cancel import (
    INVOICE_FLAG_KEY as INV_CANCEL_FLAG,
    RECEIPT_FLAG_KEY as RCPT_CANCEL_FLAG,
    prepare_cancel_indexes_for_tests,
)
from services.sales_invoice_auto_lifecycle import (
    FLAG_KEY as INV_AUTO_FLAG,
    ensure_auto_intent_indexes,
)
from services.sales_invoice_edit_revision import (
    FLAG_KEY as INV_EDIT_FLAG,
    prepare_invoice_edit_indexes_for_tests,
)
from services.sales_lifecycle_capability import (
    OP_AUTO_CREATE_POST,
    OP_BUSINESS_CANCEL,
    OP_EDIT_REVISION,
    OP_LEGACY_MANUAL,
    build_invoice_lifecycle_capability,
    build_receipt_lifecycle_capability,
    index_readiness_snapshot,
)
from services.sales_receipt_auto_lifecycle import (
    AUTO_FLAG_KEY as RCPT_AUTO_FLAG,
    ensure_auto_receipt_intent_indexes,
)
from services.sales_receipt_edit_revision import (
    FLAG_KEY as RCPT_EDIT_FLAG,
    REQUIRED_INTENT_INDEX_NAME,
)

ADMIN = {"id": "admin-cap", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}
EDITOR = {
    "id": "editor-cap",
    "email": "editor@example.com",
    "name": "Editor",
    "role": "sales",
    "permissions_override": {
        "sales.invoice.create": True,
        "sales.invoice.edit": True,
        "sales.invoice.cancel": True,
        "sales.receipt.create": True,
        "sales.receipt.edit": True,
        "sales.receipt.cancel": True,
    },
}
NO_PERM = {"id": "noperm", "email": "x@example.com", "name": "X", "role": "sales", "permissions_override": {}}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def indexes(bind_motor):
    db = server.db
    await ensure_journal(db)
    await prepare_invoice_edit_indexes_for_tests(db)
    await prepare_cancel_indexes_for_tests(db)
    await db.sales_receipt_edit_revisions.create_index(
        [("receipt_id", 1), ("idempotency_key", 1)],
        unique=True,
        name=REQUIRED_INTENT_INDEX_NAME,
    )
    await db.sales_invoices.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="idempotency_key_partial_unique",
    )
    await db.sales_receipts.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="idempotency_key_partial_unique",
    )
    await ensure_auto_intent_indexes(db)
    await ensure_auto_receipt_intent_indexes(db)
    await db.app_settings.delete_many({})
    yield
    for name in (
        "app_settings", "journal_entries", "sales_invoice_edit_revisions",
        "sales_receipt_edit_revisions", "sales_business_cancellations",
        "sales_invoice_auto_create_intents", "sales_receipt_auto_create_intents",
        "sales_invoices", "sales_receipts",
    ):
        await db[name].delete_many({})


async def _enable_all(db):
    for key in (
        JOURNAL_FLAG, INV_AUTO_FLAG, RCPT_AUTO_FLAG, INV_EDIT_FLAG, RCPT_EDIT_FLAG,
        INV_CANCEL_FLAG, RCPT_CANCEL_FLAG,
    ):
        await db.app_settings.update_one({"key": key}, {"$set": {"key": key, "enabled": True}}, upsert=True)
    await db.app_settings.update_one(
        {"key": ALLOWLIST_KEY},
        {"$set": {"key": ALLOWLIST_KEY, "source_types": ["sales_invoice", "sales_receipt"]}},
        upsert=True,
    )


@pytest.mark.anyio
async def test_capability_flag_off_and_empty_allowlist(indexes):
    db = server.db
    cap = await build_invoice_lifecycle_capability(db, EDITOR, None)
    assert cap[OP_AUTO_CREATE_POST]["available"] is False
    assert cap[OP_EDIT_REVISION]["available"] is False
    assert cap[OP_BUSINESS_CANCEL]["available"] is False
    assert cap[OP_LEGACY_MANUAL] is True
    assert "auto_flag_off" in (cap[OP_AUTO_CREATE_POST].get("reasons") or [])
    # No secrets / allowlist dump
    blob = str(cap)
    assert "token" not in blob.lower()
    assert "uniq_primary" not in blob


@pytest.mark.anyio
async def test_capability_ready_when_prerequisites_met(indexes):
    db = server.db
    await _enable_all(db)
    snap = await index_readiness_snapshot(db)
    assert snap["primary_journal"] is True
    assert snap["invoice_edit_intent"] is True
    assert snap["receipt_edit_intent"] is True
    assert snap["business_cancel_intent"] is True
    assert snap["invoice_auto"] is True
    assert snap["receipt_auto"] is True

    inv = await build_invoice_lifecycle_capability(db, EDITOR, None)
    assert inv[OP_AUTO_CREATE_POST]["available"] is True
    assert inv[OP_EDIT_REVISION]["available"] is True
    assert inv[OP_BUSINESS_CANCEL]["available"] is True
    assert inv[OP_LEGACY_MANUAL] is False

    rcpt = await build_receipt_lifecycle_capability(db, EDITOR, None)
    assert rcpt[OP_AUTO_CREATE_POST]["available"] is True
    assert rcpt[OP_EDIT_REVISION]["available"] is True
    assert rcpt[OP_BUSINESS_CANCEL]["available"] is True
    assert rcpt[OP_LEGACY_MANUAL] is False


@pytest.mark.anyio
async def test_capability_missing_intent_index_and_permission(indexes):
    db = server.db
    await _enable_all(db)
    await db.sales_invoice_edit_revisions.drop_indexes()
    inv = await build_invoice_lifecycle_capability(db, EDITOR, None)
    assert inv[OP_EDIT_REVISION]["available"] is False
    assert "intent_index_not_ready" in (inv[OP_EDIT_REVISION].get("reasons") or [])
    await prepare_invoice_edit_indexes_for_tests(db)

    denied = await build_receipt_lifecycle_capability(db, NO_PERM, None)
    assert denied[OP_EDIT_REVISION]["available"] is False
    assert "permission_missing" in (denied[OP_EDIT_REVISION].get("reasons") or [])
    assert denied[OP_BUSINESS_CANCEL]["available"] is False
    # Admin does not get opt-in receipt edit/cancel without grant
    admin_rcpt = await build_receipt_lifecycle_capability(db, ADMIN, None)
    assert admin_rcpt[OP_EDIT_REVISION]["available"] is False
    assert admin_rcpt[OP_BUSINESS_CANCEL]["available"] is False


@pytest.mark.anyio
async def test_document_pending_distinct_from_system_capability(indexes):
    db = server.db
    await _enable_all(db)
    doc = {
        "id": str(uuid.uuid4()),
        "auto_accounting_status": "pending",
        "financial_complete": False,
    }
    cap = await build_invoice_lifecycle_capability(db, EDITOR, doc)
    assert cap[OP_AUTO_CREATE_POST]["available"] is True
    assert cap["document_pending_or_failed"] is True
    assert cap[OP_AUTO_CREATE_POST].get("document_state") == "pending"
    assert cap[OP_LEGACY_MANUAL] is False


DIREKTUR = {
    "id": "dir-cap",
    "email": "direktur@example.com",
    "name": "Direktur",
    "role": "direktur_utama",
    "permissions_override": {},
}


@pytest.mark.anyio
async def test_receipt_cancel_progress_none_is_not_document_pending(indexes):
    """Healthy receipts attach business_cancel_progress='none' — must stay eligible."""
    db = server.db
    await _enable_all(db)
    await db.role_permissions.update_one(
        {"role": "direktur_utama"},
        {
            "$set": {
                "role": "direktur_utama",
                "permissions": [
                    "sales.receipt.create",
                    "sales.receipt.edit",
                    "sales.receipt.cancel",
                ],
            }
        },
        upsert=True,
    )
    idle = {
        "id": str(uuid.uuid4()),
        "financial_status": "active",
        "business_cancel_progress": "none",
        "auto_accounting_status": "complete",
    }
    cap = await build_receipt_lifecycle_capability(db, DIREKTUR, idle)
    assert cap["document_pending_or_failed"] is False
    assert cap[OP_EDIT_REVISION]["available"] is True
    assert cap[OP_BUSINESS_CANCEL]["available"] is True

    pending_cancel = {**idle, "business_cancel_progress": "pending"}
    blocked = await build_receipt_lifecycle_capability(db, DIREKTUR, pending_cancel)
    assert blocked["document_pending_or_failed"] is True


@pytest.mark.anyio
async def test_invoice_auto_create_matches_endpoint_any_of_for_direktur(indexes):
    """Capability auto_create_post must mirror create endpoint any-of perms.

    Endpoint: sales.invoice.create | sales_order.create | po.edit via
    role_permissions (same as server.user_permissions). Builtin Direktur
    ROLE_DEFAULT lacks these keys — production grants live in role_permissions.
    """
    db = server.db
    await _enable_all(db)

    # No role_permissions → Direktur cannot create (fail closed; no new grants).
    await db.role_permissions.delete_many({"role": "direktur_utama"})
    denied = await build_invoice_lifecycle_capability(db, DIREKTUR, None)
    assert denied[OP_AUTO_CREATE_POST]["available"] is False
    assert "permission_missing" in (denied[OP_AUTO_CREATE_POST].get("reasons") or [])

    # Endpoint alternative: sales_order.create on role_permissions only.
    await db.role_permissions.update_one(
        {"role": "direktur_utama"},
        {"$set": {"role": "direktur_utama", "permissions": ["sales_order.create"]}},
        upsert=True,
    )
    via_so = await build_invoice_lifecycle_capability(db, DIREKTUR, None)
    assert via_so[OP_AUTO_CREATE_POST]["available"] is True
    assert "permission_missing" not in (via_so[OP_AUTO_CREATE_POST].get("reasons") or [])

    # Endpoint alternative: po.edit
    await db.role_permissions.update_one(
        {"role": "direktur_utama"},
        {"$set": {"permissions": ["po.edit"]}},
    )
    via_po = await build_invoice_lifecycle_capability(db, DIREKTUR, None)
    assert via_po[OP_AUTO_CREATE_POST]["available"] is True

    # Explicit sales.invoice.create
    await db.role_permissions.update_one(
        {"role": "direktur_utama"},
        {"$set": {"permissions": ["sales.invoice.create"]}},
    )
    via_inv = await build_invoice_lifecycle_capability(db, DIREKTUR, None)
    assert via_inv[OP_AUTO_CREATE_POST]["available"] is True


@pytest.mark.anyio
async def test_invoice_auto_create_denied_without_endpoint_perms(indexes):
    db = server.db
    await _enable_all(db)
    await db.role_permissions.delete_many({"role": {"$in": ["sales", "helper"]}})

    # Role with unrelated override — still denied.
    no_access = {
        "id": "no-create",
        "email": "helper@example.com",
        "name": "Helper",
        "role": "helper",
        "permissions_override": {"dashboard.owner.view": True},
    }
    cap = await build_invoice_lifecycle_capability(db, no_access, None)
    assert cap[OP_AUTO_CREATE_POST]["available"] is False
    assert "permission_missing" in (cap[OP_AUTO_CREATE_POST].get("reasons") or [])

    # Explicit False on create must not unlock via other keys on same user.
    blocked = {
        "id": "blocked-create",
        "email": "crm@example.com",
        "name": "CRM",
        "role": "crm",
        "permissions_override": {
            "sales.invoice.create": False,
            "sales_order.create": False,
            "po.edit": False,
        },
    }
    await db.role_permissions.update_one(
        {"role": "crm"},
        {"$set": {"role": "crm", "permissions": ["sales.invoice.create", "po.edit"]}},
        upsert=True,
    )
    blocked_cap = await build_invoice_lifecycle_capability(db, blocked, None)
    assert blocked_cap[OP_AUTO_CREATE_POST]["available"] is False
    assert "permission_missing" in (blocked_cap[OP_AUTO_CREATE_POST].get("reasons") or [])


@pytest.mark.anyio
async def test_receipt_opt_in_via_role_permissions_for_direktur(indexes):
    """Receipt edit/cancel capability must honor Hak Akses role_permissions.

    Regression: opt_in_permission_granted previously ignored role_defaults, so
    Direktur with sales.receipt.edit/cancel only on role_permissions got
    permission_missing while cancel write path and /auth/me already granted.
    """
    db = server.db
    await _enable_all(db)
    await db.role_permissions.delete_many({"role": "direktur_utama"})

    # No grant → denied (OPT_IN_ONLY; no admin/role wildcard).
    denied = await build_receipt_lifecycle_capability(db, DIREKTUR, None)
    assert denied[OP_EDIT_REVISION]["available"] is False
    assert "permission_missing" in (denied[OP_EDIT_REVISION].get("reasons") or [])
    assert denied[OP_BUSINESS_CANCEL]["available"] is False
    assert "permission_missing" in (denied[OP_BUSINESS_CANCEL].get("reasons") or [])

    # Role & Hak Akses grant via role_permissions only (empty user override).
    await db.role_permissions.update_one(
        {"role": "direktur_utama"},
        {
            "$set": {
                "role": "direktur_utama",
                "permissions": ["sales.receipt.edit", "sales.receipt.cancel"],
            }
        },
        upsert=True,
    )
    granted = await build_receipt_lifecycle_capability(db, DIREKTUR, None)
    assert granted[OP_EDIT_REVISION]["available"] is True
    assert "permission_missing" not in (granted[OP_EDIT_REVISION].get("reasons") or [])
    assert granted[OP_BUSINESS_CANCEL]["available"] is True
    assert "permission_missing" not in (granted[OP_BUSINESS_CANCEL].get("reasons") or [])


@pytest.mark.anyio
async def test_receipt_opt_in_override_false_denies_despite_role_permissions(indexes):
    db = server.db
    await _enable_all(db)
    await db.role_permissions.update_one(
        {"role": "direktur_utama"},
        {
            "$set": {
                "role": "direktur_utama",
                "permissions": ["sales.receipt.edit", "sales.receipt.cancel"],
            }
        },
        upsert=True,
    )
    blocked = {
        **DIREKTUR,
        "id": "dir-blocked",
        "permissions_override": {
            "sales.receipt.edit": False,
            "sales.receipt.cancel": False,
        },
    }
    cap = await build_receipt_lifecycle_capability(db, blocked, None)
    assert cap[OP_EDIT_REVISION]["available"] is False
    assert "permission_missing" in (cap[OP_EDIT_REVISION].get("reasons") or [])
    assert cap[OP_BUSINESS_CANCEL]["available"] is False
    assert "permission_missing" in (cap[OP_BUSINESS_CANCEL].get("reasons") or [])
