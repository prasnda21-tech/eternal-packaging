"""Administrasi Karyawan — eligibility, snapshots, sequential approval, numbering, PDF."""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest
import httpx
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS, with_nav_defaults
from server import app, current_user
from services.employee_admin import (
    PERM_APPROVE,
    PERM_DOCUMENT,
    PERM_SETTINGS,
    PERM_SUBMIT,
    PERM_VIEW_ALL,
    PERM_VIEW_OWN,
    REQUESTS_COLLECTION,
    TYPES_COLLECTION,
    is_eligible,
    normalize_eligibility,
    normalize_fields,
    render_template_text,
    validate_values,
)

NEW_PERMS = (
    PERM_VIEW_OWN, PERM_SUBMIT, PERM_APPROVE, PERM_VIEW_ALL, PERM_SETTINGS, PERM_DOCUMENT,
    "nav.employee_administration",
    "nav.employee_administration.mine",
    "nav.employee_administration.create",
    "nav.employee_administration.approve",
    "nav.employee_administration.archive",
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def indexes(bind_motor):
    db = server.db
    await db[TYPES_COLLECTION].create_index("id", unique=True)
    await db[TYPES_COLLECTION].create_index("code", unique=True)
    await db[REQUESTS_COLLECTION].create_index("id", unique=True)
    try:
        await db[REQUESTS_COLLECTION].create_index("request_number", unique=True)
    except Exception:
        pass


def _user(role, **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    if "permissions_override" in kw:
        override = kw["permissions_override"]
    elif role == "admin":
        override = {}
    else:
        override = {
            PERM_VIEW_OWN: True,
            PERM_SUBMIT: True,
            PERM_DOCUMENT: True,
        }
        if role in ("head_production", "head_operations"):
            override[PERM_APPROVE] = True
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or role.title(),
        "role": role,
        "employee_id": kw.get("employee_id") or f"EMP-{uid[-4:]}",
        "status": "active",
        "user_id": uid,
        "permissions_override": override,
    }


async def _upsert_user(doc):
    await server.db.users.delete_many({"email": doc["email"]})
    await server.db.users.insert_one(dict(doc))
    return doc


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _type_payload(**kw):
    suffix = uuid.uuid4().hex[:6].upper()
    return {
        "name": kw.get("name") or f"Kasbon {suffix}",
        "code": kw.get("code") or f"KAS{suffix}"[:12],
        "description": "tes",
        "category": "Keuangan",
        "active": True,
        "generates_pdf": kw.get("generates_pdf", True),
        "eligibility": kw.get("eligibility") or {"mode": "any", "all_employees": True, "user_ids": [], "roles": [], "divisions": []},
        "form_fields": kw.get("form_fields") or [
            {"key": "nominal", "label": "Nominal", "type": "currency", "required": True, "order": 1, "active": True, "minimum": 1},
            {"key": "alasan", "label": "Alasan", "type": "textarea", "required": True, "order": 2, "active": True},
        ],
        "approval_steps": kw.get("approval_steps") or [
            {"label": "Level 1", "roles": ["head_production"], "user_ids": [], "divisions": []},
            {"label": "Level 2", "roles": ["admin"], "user_ids": [], "divisions": []},
        ],
        "document_template": {
            "title": "Surat Kasbon",
            "header_text": "ETERNAL PACKAGING",
            "footer_text": "Disetujui secara elektronik",
            "sections": [{"heading": "Isi", "body": "{{submitted_fields}}"}],
        },
    }


# ---------- unit: catalog / eligibility / form ----------

@pytest.mark.anyio
async def test_permission_catalog_recognizes_all_new_keys():
    keys = {k for _m, k, _l in PERMISSION_CATALOG}
    for key in NEW_PERMS:
        assert key in keys, key
        assert key in ALL_PERMISSIONS, key
    labels = {k: lbl for k, lbl, _p in __import__("permissions", fromlist=["NAV_MENU_ITEMS"]).NAV_MENU_ITEMS}
    assert labels["nav.employee_administration"] == "Administrasi Karyawan"
    assert labels["nav.employee_administration.mine"] == "Pengajuan Saya"
    assert labels["nav.employee_administration.create"] == "Buat Pengajuan"
    assert labels["nav.employee_administration.approve"] == "Persetujuan"
    assert labels["nav.employee_administration.archive"] == "Arsip Dokumen"


@pytest.mark.anyio
async def test_arbitrary_unknown_permission_still_rejected():
    assert "employee_admin.hack" not in ALL_PERMISSIONS
    assert "not.a.real.permission" not in ALL_PERMISSIONS


@pytest.mark.anyio
async def test_existing_roles_do_not_auto_gain_employee_admin():
    for role in ("helper", "operator", "screener", "designer", "driver", "crm", "head_production", "head_operations"):
        defaults = set(ROLE_DEFAULT_PERMISSIONS[role])
        derived = with_nav_defaults(defaults)
        for key in NEW_PERMS:
            assert key not in defaults, f"{role} {key}"
            assert key not in derived, f"{role} {key}"
    admin = set(ROLE_DEFAULT_PERMISSIONS["admin"])
    for key in NEW_PERMS:
        assert key in admin


@pytest.mark.anyio
async def test_eligibility_user_role_division_and_or():
    emp = {"id": "u1", "role": "operator", "status": "active"}
    assert is_eligible(emp, normalize_eligibility({"all_employees": True}))
    assert is_eligible(emp, normalize_eligibility({"user_ids": ["u1"]}))
    assert not is_eligible(emp, normalize_eligibility({"user_ids": ["u2"]}))
    assert is_eligible(emp, normalize_eligibility({"roles": ["operator"]}))
    assert not is_eligible(emp, normalize_eligibility({"roles": ["helper"]}))
    assert is_eligible(emp, normalize_eligibility({"divisions": ["operator"]}))
    assert is_eligible(emp, normalize_eligibility({"mode": "any", "roles": ["helper"], "divisions": ["operator"]}))
    assert not is_eligible(emp, normalize_eligibility({"mode": "all", "roles": ["helper"], "divisions": ["operator"]}))
    assert is_eligible(emp, normalize_eligibility({"mode": "all", "roles": ["operator"], "divisions": ["operator"]}))


@pytest.mark.anyio
async def test_dynamic_required_field_validation():
    fields = normalize_fields([
        {"key": "nominal", "label": "Nominal", "type": "currency", "required": True, "minimum": 10, "maximum": 100},
        {"key": "alasan", "label": "Alasan", "type": "textarea", "required": True},
    ])
    ok = validate_values(fields, {"nominal": 20, "alasan": "butuh"})
    assert ok["nominal"] == 20
    with pytest.raises(HTTPException) as missing:
        validate_values(fields, {"nominal": 20})
    assert missing.value.status_code == 400
    with pytest.raises(HTTPException) as low:
        validate_values(fields, {"nominal": 5, "alasan": "x"})
    assert low.value.status_code == 400
    with pytest.raises(HTTPException) as extra:
        validate_values(fields, {"nominal": 20, "alasan": "x", "hack": "1"})
    assert extra.value.status_code == 400


@pytest.mark.anyio
async def test_safe_templating_no_code_exec():
    out = render_template_text("Halo {{employee.name}} {{missing}}", {"employee.name": "Budi"})
    assert out == "Halo Budi "
    assert "{{" not in out
    from services.employee_admin import generate_pdf_bytes
    pdf = generate_pdf_bytes({
        "request_number": "PKY-TEST-001",
        "type_name": "Kasbon",
        "submitted_at": "2026-09-01T00:00:00",
        "employee": {"name": "Budi", "employee_id": "EMP-1", "role": "operator", "email": "b@x"},
        "form_snapshot": {"fields": [{"key": "alasan", "label": "Alasan", "type": "text", "active": True}]},
        "values": {"alasan": "tes"},
        "approval_history": [{"action": "approved", "level": 1, "actor": {"name": "Admin"}, "timestamp": "2026-09-01T01:00:00", "note": "ok"}],
        "document": {"document_number": "SKY-1", "template_snapshot": {"title": "Surat", "header_text": "ETERNAL PACKAGING", "footer_text": "Disetujui secara elektronik", "sections": [{"heading": "Isi", "body": "{{submitted_fields}}"}]}},
    })
    assert pdf[:4] == b"%PDF"


# ---------- HTTP / mongo ----------

@pytest.mark.anyio
async def test_eligible_submit_and_non_eligible_403():
    admin = await _upsert_user(_user("admin", id="ea-admin-1", email="ea-admin-1@example.com"))
    op = await _upsert_user(_user("operator", id="ea-op-1", email="ea-op-1@example.com"))
    helper = await _upsert_user(_user("helper", id="ea-help-1", email="ea-help-1@example.com"))
    async with await client_for(admin) as c:
        try:
            typ = (await c.post("/api/employee-admin/types", json=await _type_payload(
                eligibility={"mode": "any", "all_employees": False, "user_ids": [op["id"]], "roles": [], "divisions": []},
                approval_steps=[{"label": "L1", "user_ids": [admin["id"]], "roles": [], "divisions": []}],
            ))).json()["type"]
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(op) as c:
        try:
            res = await c.post("/api/employee-admin/requests", json={
                "type_id": typ["id"],
                "values": {"nominal": 50000, "alasan": "kasbon tes"},
            })
            assert res.status_code == 200, res.text
            req = res.json()["request"]
            assert req["request_number"].startswith("PKY-")
            assert req["form_snapshot"]["fields"]
            assert req["approval_snapshot"]["steps"]
            assert any(e["action"] == "submitted" for e in req["audit_events"])
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(helper) as c:
        try:
            res = await c.post("/api/employee-admin/requests", json={
                "type_id": typ["id"],
                "values": {"nominal": 50000, "alasan": "tidak boleh"},
            })
            assert res.status_code == 403
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_eligibility_by_role_and_division():
    admin = await _upsert_user(_user("admin", id="ea-admin-2", email="ea-admin-2@example.com"))
    op = await _upsert_user(_user("operator", id="ea-op-2", email="ea-op-2@example.com"))
    helper = await _upsert_user(_user("helper", id="ea-help-2", email="ea-help-2@example.com"))
    async with await client_for(admin) as c:
        try:
            by_role = (await c.post("/api/employee-admin/types", json=await _type_payload(
                eligibility={"mode": "any", "all_employees": False, "roles": ["operator"], "user_ids": [], "divisions": []},
                approval_steps=[{"label": "L1", "roles": ["admin"], "user_ids": [], "divisions": []}],
            ))).json()["type"]
            by_div = (await c.post("/api/employee-admin/types", json=await _type_payload(
                eligibility={"mode": "any", "all_employees": False, "roles": [], "user_ids": [], "divisions": ["helper"]},
                approval_steps=[{"label": "L1", "roles": ["admin"], "user_ids": [], "divisions": []}],
            ))).json()["type"]
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(op) as c:
        try:
            ok = await c.post("/api/employee-admin/requests", json={"type_id": by_role["id"], "values": {"nominal": 11, "alasan": "ok"}})
            no = await c.post("/api/employee-admin/requests", json={"type_id": by_div["id"], "values": {"nominal": 11, "alasan": "no"}})
            assert ok.status_code == 200, ok.text
            assert no.status_code == 403
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(helper) as c:
        try:
            ok = await c.post("/api/employee-admin/requests", json={"type_id": by_div["id"], "values": {"nominal": 11, "alasan": "ok"}})
            no = await c.post("/api/employee-admin/requests", json={"type_id": by_role["id"], "values": {"nominal": 11, "alasan": "no"}})
            assert ok.status_code == 200, ok.text
            assert no.status_code == 403
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_snapshots_sequential_approval_reject_and_pdf():
    admin = await _upsert_user(_user("admin", id="ea-admin-3", email="ea-admin-3@example.com"))
    hop = await _upsert_user(_user("head_production", id="ea-hop-3", email="ea-hop-3@example.com"))
    op = await _upsert_user(_user("operator", id="ea-op-3", email="ea-op-3@example.com"))
    other = await _upsert_user(_user("helper", id="ea-help-3", email="ea-help-3@example.com"))
    async with await client_for(admin) as c:
        try:
            typ = (await c.post("/api/employee-admin/types", json=await _type_payload(
                eligibility={"mode": "any", "all_employees": True, "user_ids": [], "roles": [], "divisions": []},
            ))).json()["type"]
            old_version = typ["config_version"]
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(op) as c:
        try:
            res = await c.post("/api/employee-admin/requests", json={"type_id": typ["id"], "values": {"nominal": 25000, "alasan": "awal"}})
            assert res.status_code == 200, res.text
            req = res.json()["request"]
            rid = req["id"]
            assert req["status"] == "DIAJUKAN"
            assert req["form_snapshot"]["config_version"] == old_version
            assert len(req["approval_snapshot"]["steps"]) == 2
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(admin) as c:
        try:
            await c.put(f"/api/employee-admin/types/{typ['id']}", json={
                "form_fields": [{"key": "nominal", "label": "Nominal Baru", "type": "currency", "required": True, "active": True}],
                "approval_steps": [{"label": "Hanya Admin", "roles": ["admin"], "user_ids": [], "divisions": []}],
            })
            historical = (await c.get(f"/api/employee-admin/requests/{rid}")).json()["request"]
            assert historical["form_snapshot"]["fields"][1]["key"] == "alasan"
            assert historical["approval_snapshot"]["steps"][1]["label"] == "Level 2"
            assert historical["values"]["alasan"] == "awal"
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(admin) as c:
        try:
            skip = await c.post(f"/api/employee-admin/requests/{rid}/decide", json={"action": "approve", "note": "terlalu cepat"})
            assert skip.status_code == 403
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(other) as c:
        try:
            unauth = await c.post(f"/api/employee-admin/requests/{rid}/decide", json={"action": "approve"})
            assert unauth.status_code == 403
            peek = await c.get(f"/api/employee-admin/requests/{rid}")
            assert peek.status_code == 403
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(hop) as c:
        try:
            first, dup = await asyncio.gather(
                c.post(f"/api/employee-admin/requests/{rid}/decide", json={"action": "approve", "note": "ok L1"}),
                c.post(f"/api/employee-admin/requests/{rid}/decide", json={"action": "approve"}),
            )
            codes = sorted([first.status_code, dup.status_code])
            assert 200 in (first.status_code, dup.status_code)
            assert codes[1] in (403, 409)
            winner = first if first.status_code == 200 else dup
            assert winner.json()["request"]["status"] == "MENUNGGU_PERSETUJUAN"
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(admin) as c:
        try:
            final = await c.post(f"/api/employee-admin/requests/{rid}/decide", json={"action": "approve", "note": "ok L2"})
            assert final.status_code == 200, final.text
            body = final.json()["request"]
            assert body["status"] == "DISETUJUI"
            assert body["document"]["generated"] is True
            assert str(body["document"]["document_number"]).startswith("SKY-")
            assert any(e["action"] == "approved" for e in body["audit_events"])
            assert any(e["action"] == "pdf_generated" for e in body["audit_events"])
            pdf1 = await c.get(f"/api/employee-admin/requests/{rid}/document")
            assert pdf1.status_code == 200
            assert pdf1.headers["content-type"].startswith("application/pdf")
            assert pdf1.content[:4] == b"%PDF"
            assert b"ReportLab" in pdf1.content or len(pdf1.content) > 200
            pdf2 = await c.get(f"/api/employee-admin/requests/{rid}/document")
            assert pdf2.status_code == 200
            latest = (await c.get(f"/api/employee-admin/requests/{rid}")).json()["request"]
            assert latest["document"]["file_id"] == body["document"]["file_id"]
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_rejected_flow_stops():
    admin = await _upsert_user(_user("admin", id="ea-admin-4", email="ea-admin-4@example.com"))
    hop = await _upsert_user(_user("head_production", id="ea-hop-4", email="ea-hop-4@example.com"))
    op = await _upsert_user(_user("operator", id="ea-op-4", email="ea-op-4@example.com"))
    async with await client_for(admin) as c:
        try:
            typ = (await c.post("/api/employee-admin/types", json=await _type_payload())).json()["type"]
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(op) as c:
        try:
            rid = (await c.post("/api/employee-admin/requests", json={"type_id": typ["id"], "values": {"nominal": 12, "alasan": "tolak"}})).json()["request"]["id"]
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(hop) as c:
        try:
            res = await c.post(f"/api/employee-admin/requests/{rid}/decide", json={"action": "reject", "note": "tidak setuju"})
            assert res.status_code == 200, res.text
            assert res.json()["request"]["status"] == "DITOLAK"
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(admin) as c:
        try:
            later = await c.post(f"/api/employee-admin/requests/{rid}/decide", json={"action": "approve"})
            assert later.status_code == 409
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_unique_and_concurrent_numbering():
    admin = await _upsert_user(_user("admin", id="ea-admin-5", email="ea-admin-5@example.com"))
    async with await client_for(admin) as c:
        try:
            typ = (await c.post("/api/employee-admin/types", json=await _type_payload(
                approval_steps=[{"label": "L1", "roles": ["admin"], "user_ids": [], "divisions": []}],
            ))).json()["type"]
            payload = {"type_id": typ["id"], "values": {"nominal": 15, "alasan": "num"}}
            r1, r2 = await asyncio.gather(
                c.post("/api/employee-admin/requests", json=payload),
                c.post("/api/employee-admin/requests", json=dict(payload)),
            )
            assert r1.status_code == 200 and r2.status_code == 200
            n1 = r1.json()["request"]["request_number"]
            n2 = r2.json()["request"]["request_number"]
            assert n1 != n2
            assert n1.startswith("PKY-") and n2.startswith("PKY-")
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_approver_inbox_scoped_and_owner_isolation():
    admin = await _upsert_user(_user("admin", id="ea-admin-6", email="ea-admin-6@example.com"))
    hop = await _upsert_user(_user("head_production", id="ea-hop-6", email="ea-hop-6@example.com"))
    op = await _upsert_user(_user("operator", id="ea-op-6", email="ea-op-6@example.com"))
    other = await _upsert_user(_user("operator", id="ea-op-6b", email="ea-op-6b@example.com"))
    async with await client_for(admin) as c:
        try:
            typ = (await c.post("/api/employee-admin/types", json=await _type_payload())).json()["type"]
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(op) as c:
        try:
            rid = (await c.post("/api/employee-admin/requests", json={"type_id": typ["id"], "values": {"nominal": 18, "alasan": "milikku"}})).json()["request"]["id"]
            mine = (await c.get("/api/employee-admin/requests", params={"scope": "mine"})).json()["requests"]
            assert any(r["id"] == rid for r in mine)
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(other) as c:
        try:
            mine = (await c.get("/api/employee-admin/requests", params={"scope": "mine"})).json()["requests"]
            assert all(r["id"] != rid for r in mine)
            assert (await c.get(f"/api/employee-admin/requests/{rid}")).status_code == 403
        finally:
            app.dependency_overrides.pop(current_user, None)
    async with await client_for(hop) as c:
        try:
            inbox = (await c.get("/api/employee-admin/requests", params={"scope": "approve"})).json()["requests"]
            assert any(r["id"] == rid for r in inbox)
        finally:
            app.dependency_overrides.pop(current_user, None)
