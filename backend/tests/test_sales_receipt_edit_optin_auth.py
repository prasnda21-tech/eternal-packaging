"""Receipt edit opt-in auth must match require_cancel_permission / Hak Akses."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_receipt_edit_optin_auth_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from services.sales_receipt_edit_revision import _require_edit_permission


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def clean_roles(bind_motor):
    db = server.db
    await db.role_permissions.delete_many({})
    yield db
    await db.role_permissions.delete_many({})


DIREKTUR = {
    "id": "dir-edit-auth",
    "email": "direktur-edit@example.com",
    "name": "Direktur",
    "role": "direktur_utama",
    "permissions_override": {},
}


@pytest.mark.anyio
async def test_require_edit_permission_role_permissions_grant(clean_roles):
    db = clean_roles
    with pytest.raises(HTTPException) as denied:
        await _require_edit_permission(db, DIREKTUR)
    assert denied.value.status_code == 403

    await db.role_permissions.insert_one(
        {"role": "direktur_utama", "permissions": ["sales.receipt.edit"]}
    )
    await _require_edit_permission(db, DIREKTUR)  # no raise


@pytest.mark.anyio
async def test_require_edit_permission_override_false_denies(clean_roles):
    db = clean_roles
    await db.role_permissions.insert_one(
        {"role": "direktur_utama", "permissions": ["sales.receipt.edit"]}
    )
    blocked = {
        **DIREKTUR,
        "permissions_override": {"sales.receipt.edit": False},
    }
    with pytest.raises(HTTPException) as exc:
        await _require_edit_permission(db, blocked)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_require_edit_permission_override_true_grants_without_role_doc(clean_roles):
    db = clean_roles
    granted = {
        **DIREKTUR,
        "permissions_override": {"sales.receipt.edit": True},
    }
    await _require_edit_permission(db, granted)  # no raise
