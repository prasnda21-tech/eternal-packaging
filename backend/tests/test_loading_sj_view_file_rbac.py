"""Loading ↔ Surat Jalan file-view RBAC invariant.

Operate Loading requires loading.update.
Opening the generated SJ PDF requires delivery_note.view_file (separate from manage).

Code defaults already grant view_file to every Loading-capable built-in role.
Persisted role_permissions overrides may still omit view_file — that is an
admin/Roles-UI fix, not a silent DB mutation.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ROLE_DEFAULT_PERMISSIONS, effective_permissions
from server import app, current_user
from services.surat_jalan_files import PERM_VIEW_FILE, SOURCE_SYSTEM_GENERATED


LOADING_UPDATE = "loading.update"
LOADING_DELETE = "loading.delete"
SJ_MANAGE = "delivery_note.manage"
SJ_VIEW = PERM_VIEW_FILE  # delivery_note.view_file


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
def isolated_storage(monkeypatch):
    store = {}

    def put_object(path, data, content_type):
        store[path] = {"data": data, "content_type": content_type, "size": len(data)}
        return {"path": path, "size": len(data)}

    def get_object(path):
        rec = store.get(path)
        if not rec:
            raise FileNotFoundError(path)
        return rec["data"], rec["content_type"]

    monkeypatch.setattr("services.sales_deliveries.obj_storage.put_object", put_object)
    monkeypatch.setattr("services.sales_deliveries.obj_storage.get_object", get_object)
    monkeypatch.setattr("services.storage.put_object", put_object)
    monkeypatch.setattr("services.storage.get_object", get_object)
    monkeypatch.setattr("server.obj_storage.put_object", put_object)
    monkeypatch.setattr("server.obj_storage.get_object", get_object)
    return store


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def loading_capable_default_roles():
    return sorted(
        role
        for role, perms in ROLE_DEFAULT_PERMISSIONS.items()
        if LOADING_UPDATE in perms
    )


def test_loading_route_and_confirm_require_loading_update():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert 'await _require_perm(user, "loading.update")' in src
    assert '("/orders/{order_id}/loading-confirm"' in src or '"/orders/{order_id}/loading-confirm"' in src
    front_app = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.js").read_text(encoding="utf-8")
    assert "loading.update" in front_app
    assert "/tasks/loading" in front_app
    panel = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "PODetail.jsx").read_text(encoding="utf-8")
    assert 'hasPerm("loading.update")' in panel
    assert "canManage={canLoadingUpdate}" in panel
    view_btn = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "SuratJalanViewButton.jsx").read_text(encoding="utf-8")
    assert "SJ_VIEW_FILE_PERM" in view_btn or "delivery_note.view_file" in view_btn


def test_default_loading_capable_roles_all_have_view_file():
    capable = loading_capable_default_roles()
    assert "driver" in capable
    assert "admin" in capable
    assert "head_production" in capable
    assert "head_operations" in capable
    assert "helper" not in capable
    assert "crm" not in capable
    for role in capable:
        perms = set(ROLE_DEFAULT_PERMISSIONS[role])
        assert SJ_VIEW in perms, f"{role} can Loading but lacks {SJ_VIEW} in code defaults"
    # Defaults: no Loading-capable role is missing view_file
    gaps = [r for r in capable if SJ_VIEW not in ROLE_DEFAULT_PERMISSIONS[r]]
    assert gaps == []


def test_view_file_and_manage_remain_independent_in_defaults():
    driver = set(ROLE_DEFAULT_PERMISSIONS["driver"])
    crm = set(ROLE_DEFAULT_PERMISSIONS["crm"])
    helper = set(ROLE_DEFAULT_PERMISSIONS["helper"])
    assert LOADING_UPDATE in driver and SJ_VIEW in driver and SJ_MANAGE not in driver
    assert SJ_MANAGE in crm and SJ_VIEW not in crm and LOADING_UPDATE not in crm
    assert LOADING_UPDATE not in helper and SJ_VIEW not in helper and SJ_MANAGE not in helper


def test_manage_does_not_imply_view_file():
    perms = effective_permissions("crm", None)
    assert SJ_MANAGE in perms
    assert SJ_VIEW not in perms
    # Explicit override: manage without view_file
    only_manage = effective_permissions("helper", {SJ_MANAGE: True, SJ_VIEW: False})
    assert SJ_MANAGE in only_manage
    assert SJ_VIEW not in only_manage


def test_view_file_does_not_imply_manage_or_loading():
    only_view = effective_permissions("helper", {SJ_VIEW: True, SJ_MANAGE: False, LOADING_UPDATE: False})
    assert SJ_VIEW in only_view
    assert SJ_MANAGE not in only_view
    assert LOADING_UPDATE not in only_view


def test_persisted_override_shape_can_omit_view_file_without_code_mutation():
    """Documents the production risk: role_permissions replace defaults entirely.

    A Loading-capable override without view_file stays broken until Roles UI tops it up.
    Code must NOT silently rewrite that document in this hardening pass.
    """
    stale_driver = {
        "driver": [
            "po.view",
            "delivery_note.view",
            "delivery_note.manage",
            "loading.update",
            "delivery.update",
            "work_panel.view",
        ]
    }
    eff = effective_permissions("driver", None, stale_driver)
    assert LOADING_UPDATE in eff
    assert SJ_VIEW not in eff
    assert SJ_MANAGE in eff
    # Restoring defaults (no override) recovers view_file
    restored = effective_permissions("driver", None, None)
    assert LOADING_UPDATE in restored and SJ_VIEW in restored and SJ_MANAGE not in restored


@pytest.mark.anyio
async def test_loading_authorized_role_can_view_generated_sj_pdf(isolated_storage):
    po_id = str(uuid.uuid4())
    po_num = f"PO-LOADVIEW-{uuid.uuid4().hex[:6].upper()}"
    await server.db.orders.insert_one({
        "id": po_id,
        "po_number": po_num,
        "customer": "PT Load View",
        "customer_name": "PT Load View",
        "outlet": "Pusat",
        "recipient": "PIC",
        "address": "Jl Load View",
        "delivery_zone": "Z1",
        "sales": "S",
        "expedition": "Armada",
        "order_date": "2026-09-04",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "current_stage": 11,
        "cancelled": False,
        "line_items": [{"name": "Cup", "quantity": 500, "unit": "pcs"}],
        "total_quantity": 500,
        "packing_info": {"qty_packed": 500, "lines": [{"line_idx": 0, "packed_qty": 500}]},
        "shipments": [],
        "surat_jalan_files": [],
        "loading_confirmations": [],
    })

    def admin():
        return {"email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}

    def driver_like():
        # Effective set matching code-default driver (Loading + view_file, no manage)
        return {
            "email": "driver.view@eternalpackaging.com",
            "name": "Driver Viewer",
            "role": "driver",
            "permissions_override": {},
        }

    def no_view_file():
        return {
            "email": "noview@eternalpackaging.com",
            "name": "No View",
            "role": "helper",
            "permissions_override": {
                LOADING_UPDATE: True,
                SJ_VIEW: False,
                SJ_MANAGE: False,
            },
        }

    app.dependency_overrides[current_user] = admin
    try:
        async with _client() as client:
            created = await client.post("/api/sales-deliveries", json={
                "po_id": po_id,
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert created.status_code == 200, created.text
            file_id = created.json()["delivery"]["id"]
            order = await server.db.orders.find_one({"id": po_id}, {"_id": 0})
            gen = (order.get("surat_jalan_files") or [])[0]
            assert gen.get("source") == SOURCE_SYSTEM_GENERATED
            assert gen.get("id") == file_id

        # Driver default effective perms include view_file
        driver_perms = effective_permissions("driver", None)
        assert LOADING_UPDATE in driver_perms and SJ_VIEW in driver_perms

        app.dependency_overrides[current_user] = driver_like
        # Patch user_permissions path: current_user alone is not enough if role
        # overrides are read from DB. Force permissions via override on a helper
        # role that mirrors Loading+view_file without manage.
        def loading_viewer():
            return {
                "email": "loading.viewer@eternalpackaging.com",
                "name": "Loading Viewer",
                "role": "helper",
                "permissions_override": {
                    LOADING_UPDATE: True,
                    SJ_VIEW: True,
                    SJ_MANAGE: False,
                    "po.view": True,
                },
            }

        app.dependency_overrides[current_user] = loading_viewer
        async with _client() as client:
            ok = await client.get(f"/api/orders/{po_id}/surat-jalan/files/{file_id}")
            assert ok.status_code == 200
            assert ok.content.startswith(b"%PDF")
            # view_file must not unlock upload
            denied_upload = await client.post(f"/api/orders/{po_id}/attachment", json={
                "kind": "surat_jalan",
                "name": "x.pdf",
                "data_url": "data:application/pdf;base64,JVBERi0=",
            })
            assert denied_upload.status_code == 403

        app.dependency_overrides[current_user] = no_view_file
        async with _client() as client:
            denied = await client.get(f"/api/orders/{po_id}/surat-jalan/files/{file_id}")
            assert denied.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


def test_frontend_loading_view_uses_separate_file_perm():
    loading = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "LoadingPanel.jsx").read_text(encoding="utf-8")
    assert "SuratJalanViewButton" in loading
    assert "Lihat Surat Jalan" in loading
    surat = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "suratJalan.js").read_text(encoding="utf-8")
    assert 'SJ_VIEW_FILE_PERM = "delivery_note.view_file"' in surat
    assert 'SJ_MANAGE_PERM = "delivery_note.manage"' in surat
