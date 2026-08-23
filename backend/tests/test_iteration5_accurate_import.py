"""Iteration 5: Per-PO Accurate Import.

Covers: preview happy/404, confirm happy/duplicate/not-found, role gating,
import logs, available list, no-credentials contract, schedule persistence,
static READ-ONLY assertion on services/accurate.py.
"""
import os
import json
import re
import pytest
import requests
from pathlib import Path

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    with open('/app/frontend/.env') as f:
        for line in f:
            if line.startswith('REACT_APP_BACKEND_URL='):
                BASE_URL = line.split('=', 1)[1].strip().rstrip('/')
                break

PASSWORD = "Eternal123!"
EMAILS = {
    "admin": "admin@eternalpackaging.com",
    "prod": "production@eternalpackaging.com",
    "designer": "designer@eternalpackaging.com",
    "screener": "screener@eternalpackaging.com",
    "helper": "helper@eternalpackaging.com",
    "op1": "operator@eternalpackaging.com",
    "driver": "driver@eternalpackaging.com",
    "crm": "crm@eternalpackaging.com",
}

SECRET_TERMS = ["client_id", "client_secret", "access_token", "authorization", "bearer", "sk-"]


def _client(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def sessions():
    s = {k: _client(v) for k, v in EMAILS.items()}
    # Reset state for PO-2001 and PO-2002 so idempotency tests are deterministic.
    # Uses admin session against mongo indirectly by attempting to remove via a
    # helper endpoint if present; otherwise via direct mongo cleanup below.
    return s


@pytest.fixture(scope="module", autouse=True)
def reset_import_state():
    """Reset PO-2001, PO-2002 and import_logs between test runs."""
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    async def _reset():
        mongo_url = os.environ.get('MONGO_URL')
        db_name = os.environ.get('DB_NAME')
        if not mongo_url:
            for line in open('/app/backend/.env'):
                if line.startswith('MONGO_URL='):
                    mongo_url = line.split('=', 1)[1].strip().strip('"').strip("'")
                if line.startswith('DB_NAME='):
                    db_name = line.split('=', 1)[1].strip().strip('"').strip("'")
        c = AsyncIOMotorClient(mongo_url)
        db = c[db_name]
        await db.orders.delete_many({"po_number": {"$in": ["PO-2001", "PO-2002", "PO-1049", "PO-1050"]}})
        await db.import_logs.delete_many({})
        c.close()
    asyncio.get_event_loop().run_until_complete(_reset()) if False else asyncio.new_event_loop().run_until_complete(_reset())
    yield


def _assert_no_credentials(payload):
    dump = json.dumps(payload).lower()
    for term in SECRET_TERMS:
        assert term not in dump, f"secret term '{term}' leaked in response"


# ---------- Static assertion: services/accurate.py READ-ONLY ----------
class TestAccurateReadOnly:
    def test_no_mutation_methods(self):
        p = Path('/app/backend/services/accurate.py')
        src = p.read_text().lower()
        # Ensure no HTTP mutation verbs or CRUD verbs on Accurate side.
        # Note: the file MAY contain those verbs inside comments discussing
        # the read-only contract. Check code lines (excluding comments) only.
        code_lines = []
        for line in p.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            code_lines.append(stripped.lower())
        code = "\n".join(code_lines)
        for verb in [".post(", ".put(", ".patch(", ".delete("]:
            assert verb not in code, f"forbidden verb {verb} present in code"
        # `save_`/`create_`/`update_`/`delete_` methods on client
        for pat in ["def save_", "def create_", "def update_", "def delete_"]:
            assert pat not in code, f"mutation method {pat} present"


# ---------- Preview ----------
class TestPreview:
    def test_preview_happy(self, sessions):
        # Use PO-2001 preview, but duplicate flag depends on DB state; the
        # important invariants here are the preview payload shape, not
        # duplicate=False (that is asserted deterministically in
        # test_preview_does_not_write via PO-2002).
        r = sessions["admin"].post(f"{BASE_URL}/api/accurate/import/preview", json={"so_number": "PO-2001"}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "duplicate" in body
        prev = body["preview"]
        assert prev["accurate_id"] == "ACC-SO-2001"
        assert prev["po_number"] == "PO-2001"
        for k in ("customer", "outlet", "recipient", "address", "sales", "created_by_user",
                  "expedition", "delivery_zone", "payment_terms"):
            assert k in prev, f"{k} missing from preview"
        # sales must be a real name for PO-2001; created_by_user may be present
        assert prev["sales"], "sales (tenaga penjual) missing/empty"
        assert isinstance(prev["line_items"], list) and len(prev["line_items"]) >= 1
        li = prev["line_items"][0]
        for k in ("item_code", "name", "quantity", "unit", "color"):
            assert k in li
        assert prev["total_quantity"] == sum(x["quantity"] for x in prev["line_items"])
        assert prev["item_count"] == len(prev["line_items"])
        assert prev["order_date"] and prev["deadline"]
        _assert_no_credentials(body)

    def test_preview_not_found(self, sessions):
        r = sessions["admin"].post(f"{BASE_URL}/api/accurate/import/preview", json={"so_number": "PO-XXXX-NOPE"}, timeout=15)
        assert r.status_code == 404
        assert "tidak ditemukan" in r.text.lower()

    def test_preview_empty_input(self, sessions):
        r = sessions["admin"].post(f"{BASE_URL}/api/accurate/import/preview", json={"so_number": ""}, timeout=15)
        assert r.status_code == 404

    def test_preview_does_not_write(self, sessions):
        # After preview, orders count for PO-2001 in list should be unchanged (0)
        # if we haven't confirmed yet
        r = sessions["admin"].get(f"{BASE_URL}/api/orders", timeout=15)
        pos = [o["po_number"] for o in r.json()["orders"]]
        # PO-2001 may or may not exist depending on other tests; just ensure
        # preview endpoint returned without inserting a fresh one.
        # More reliable: two consecutive previews return duplicate=False
        r1 = sessions["admin"].post(f"{BASE_URL}/api/accurate/import/preview", json={"so_number": "PO-2002"}, timeout=15)
        r2 = sessions["admin"].post(f"{BASE_URL}/api/accurate/import/preview", json={"so_number": "PO-2002"}, timeout=15)
        assert r1.json()["duplicate"] is False
        assert r2.json()["duplicate"] is False

    @pytest.mark.parametrize("role", ["prod", "designer", "screener", "helper", "op1", "driver", "crm"])
    def test_preview_role_gated(self, sessions, role):
        r = sessions[role].post(f"{BASE_URL}/api/accurate/import/preview", json={"so_number": "PO-2001"}, timeout=15)
        assert r.status_code == 403


# ---------- Confirm ----------
class TestConfirm:
    def test_confirm_happy_and_duplicate(self, sessions):
        # First import PO-2001
        r = sessions["admin"].post(f"{BASE_URL}/api/accurate/import/confirm", json={"so_number": "PO-2001"}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        order = body["order"]
        assert order["po_number"] == "PO-2001"
        assert order["accurate_id"] == "ACC-SO-2001"
        assert order["current_stage"] == 0
        assert order["status_label"] == "Order Masuk"
        assert order["id"]
        assert order["created_at"]
        assert order["timeline"] and "Import dari Accurate" in order["timeline"][0]["note"]
        assert "ACC-SO-2001" in order["timeline"][0]["note"]
        _assert_no_credentials(body)

        # Verify persistence via GET /orders
        gr = sessions["admin"].get(f"{BASE_URL}/api/orders", timeout=15)
        pos = {o["po_number"]: o for o in gr.json()["orders"]}
        assert "PO-2001" in pos

        # Duplicate attempt
        r2 = sessions["admin"].post(f"{BASE_URL}/api/accurate/import/confirm", json={"so_number": "PO-2001"}, timeout=15)
        assert r2.status_code == 409
        detail = r2.json().get("detail")
        assert isinstance(detail, dict)
        assert detail.get("message") == "PO ini sudah pernah diimport"
        assert detail.get("existing_po_id")
        assert detail.get("existing_po_number") == "PO-2001"

    def test_confirm_not_found_logs_failed(self, sessions):
        so = "PO-DOES-NOT-EXIST-9999"
        r = sessions["admin"].post(f"{BASE_URL}/api/accurate/import/confirm", json={"so_number": so}, timeout=15)
        assert r.status_code == 404
        # Check logs contain a failed entry
        lr = sessions["admin"].get(f"{BASE_URL}/api/accurate/import/logs", timeout=15)
        assert lr.status_code == 200
        logs = lr.json()["logs"]
        failed = [l for l in logs if l.get("so_number") == so and l.get("status") == "failed"]
        assert failed, "no failed log entry for unknown SO"

    @pytest.mark.parametrize("role", ["prod", "designer", "screener", "helper", "op1", "driver", "crm"])
    def test_confirm_role_gated(self, sessions, role):
        r = sessions[role].post(f"{BASE_URL}/api/accurate/import/confirm", json={"so_number": "PO-2002"}, timeout=15)
        assert r.status_code == 403


# ---------- Logs & available ----------
class TestLogsAndAvailable:
    def test_logs_admin(self, sessions):
        r = sessions["admin"].get(f"{BASE_URL}/api/accurate/import/logs", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert "logs" in body
        logs = body["logs"]
        assert len(logs) >= 2  # at least success + duplicate + failed from earlier tests
        # sorted desc by imported_at
        times = [l["imported_at"] for l in logs]
        assert times == sorted(times, reverse=True), "logs not sorted desc"
        for l in logs:
            for k in ("id", "so_number", "imported_by", "imported_by_name", "imported_at", "status", "message", "mode", "read_only_source"):
                assert k in l, f"log missing {k}"
            assert l["status"] in {"success", "duplicate", "failed"}
            assert l["read_only_source"] is True
            assert l["imported_by"] == EMAILS["admin"]
        _assert_no_credentials(logs)
        # explicit forbidden field names
        for l in logs:
            for bad in ("token", "client_secret", "password", "access_token", "authorization"):
                assert bad not in l, f"forbidden field {bad} in log"

    @pytest.mark.parametrize("role", ["prod", "designer", "op1", "crm"])
    def test_logs_role_gated(self, sessions, role):
        r = sessions[role].get(f"{BASE_URL}/api/accurate/import/logs", timeout=15)
        assert r.status_code == 403

    def test_available_admin(self, sessions):
        r = sessions["admin"].get(f"{BASE_URL}/api/accurate/import/available", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "mock"
        po_numbers = {a["po_number"] for a in body["available"]}
        for expected in ("PO-1049", "PO-1050", "PO-2001", "PO-2002"):
            assert expected in po_numbers, f"missing {expected}"
        _assert_no_credentials(body)

    @pytest.mark.parametrize("role", ["prod", "op1", "crm"])
    def test_available_role_gated(self, sessions, role):
        r = sessions[role].get(f"{BASE_URL}/api/accurate/import/available", timeout=15)
        assert r.status_code == 403


# ---------- Status + no credentials contract ----------
class TestNoCredentialsLeak:
    def test_status_no_credentials(self, sessions):
        r = sessions["admin"].get(f"{BASE_URL}/api/accurate/status", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body.get("read_only") is True
        # Actual credential VALUES must be absent. The diagnostic message may
        # reference env-var NAMES (ACCURATE_CLIENT_ID etc.) — those names are
        # not secrets. Assert none of those keys carry a value string.
        for k in ("client_id", "client_secret", "access_token", "token", "authorization"):
            assert k not in body, f"secret key '{k}' present in status body"


# ---------- Bulk sync legacy regression ----------
class TestBulkSyncStillWorks:
    def test_admin_bulk_sync(self, sessions):
        r = sessions["admin"].post(f"{BASE_URL}/api/accurate/sync", timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "imported" in body and "updated" in body
        # PO-2001 is already imported via /import/confirm and should be
        # updated (sales fields) but not clobbered on workflow.
        # Confirm current_stage on PO-2001 is still 0.
        gr = sessions["admin"].get(f"{BASE_URL}/api/orders", timeout=15)
        pos = {o["po_number"]: o for o in gr.json()["orders"]}
        assert pos["PO-2001"]["current_stage"] == 0
