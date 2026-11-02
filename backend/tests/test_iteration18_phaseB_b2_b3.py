"""Iteration 18: Phase B — B1 regression + B2 (Dashboard Production + Excel Export) + B3 sanity.

Tests:
  - B1 regression: custom role assignable via POST/PATCH /admin/users; perm-first on cancel/delete
  - B2: /dashboard/production endpoint (schema, gating, filters)
  - B2: /dashboard/production/export Excel (headers/sheets/no DB write/audit/gating)
  - Req 5 light regression (list, get, cancel, delete, advance guard)
"""
import io
import os
import uuid
import zipfile

import pytest
import requests
from openpyxl import load_workbook

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://print-ops-hub-12.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE}/api"
PW = "Eternal123!"

ADMIN = "admin@eternalpackaging.com"
HEAD_PROD = "production@eternalpackaging.com"
HEAD_OPS = "operasional@eternalpackaging.com"
DESIGNER = "designer@eternalpackaging.com"


def _login(email: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": PW}, timeout=20)
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def head_prod():
    return _login(HEAD_PROD)


@pytest.fixture(scope="module")
def head_ops():
    return _login(HEAD_OPS)


@pytest.fixture(scope="module")
def designer():
    return _login(DESIGNER)


# ---------------- B1 REGRESSION (fixes from iteration 17) ----------------
class TestB1Regression:
    def test_custom_role_can_be_used_in_create_and_update_user(self, admin):
        # Cleanup any leftover
        admin.delete(f"{API}/admin/roles/qa_test_role")
        # Create custom role
        r = admin.post(
            f"{API}/admin/roles",
            json={
                "role": "qa_test_role",
                "label": "QA Test Role",
                "description": "iter18",
                "permissions": ["po.view"],
            },
        )
        assert r.status_code == 200, r.text

        # Create user with custom role (should be 200, previously was 400)
        email = f"TEST_qa_customrole_{uuid.uuid4().hex[:6]}@example.com"
        create = admin.post(
            f"{API}/admin/users",
            json={
                "email": email,
                "name": "TEST Custom Role User",
                "role": "qa_test_role",
                "employee_id": f"TEST-{uuid.uuid4().hex[:5]}",
                "password": "Eternal123!",
            },
        )
        assert create.status_code == 200, f"create-user with custom role expected 200: {create.status_code} {create.text}"
        user_id = create.json().get("user", {}).get("id") or create.json().get("id")
        assert user_id, f"no user id in response: {create.text}"

        # PUT with custom role (still qa_test_role) — should stay 200
        patch = admin.put(
            f"{API}/admin/users/{user_id}",
            json={"role": "qa_test_role", "name": "TEST Renamed"},
        )
        assert patch.status_code == 200, f"put-user with custom role expected 200: {patch.status_code} {patch.text}"

        # Cleanup: deactivate user, drop role after reassigning to designer builtin
        admin.put(f"{API}/admin/users/{user_id}", json={"role": "designer"})
        admin.post(f"{API}/admin/users/{user_id}/deactivate")
        # Role no longer in use → delete
        drop = admin.delete(f"{API}/admin/roles/qa_test_role")
        assert drop.status_code in (200, 400), drop.text  # 400 acceptable if user_count still >0

    def test_designer_cancel_returns_403_not_404(self, admin, designer):
        # Grab a real order id
        r = admin.get(f"{API}/orders")
        arr = r.json() if isinstance(r.json(), list) else r.json().get("orders", [])
        if not arr:
            pytest.skip("no orders in system")
        oid = arr[0]["id"]
        # Designer lacks po.cancel → must be 403 (not 404 even if we use bogus id)
        r1 = designer.post(f"{API}/orders/{oid}/cancel", json={"reason": "x"})
        assert r1.status_code == 403, f"expected 403, got {r1.status_code}: {r1.text}"

        # Bogus id should also be 403 (perm check runs before existence)
        r2 = designer.post(f"{API}/orders/does-not-exist/cancel", json={"reason": "x"})
        assert r2.status_code == 403, f"bogus id: expected 403, got {r2.status_code}: {r2.text}"

    def test_designer_delete_returns_403(self, admin, designer):
        r = admin.get(f"{API}/orders")
        arr = r.json() if isinstance(r.json(), list) else r.json().get("orders", [])
        if not arr:
            pytest.skip("no orders")
        oid = arr[0]["id"]
        r1 = designer.delete(f"{API}/orders/{oid}", json={"reason": "x", "confirm": "HAPUS"})
        assert r1.status_code == 403, f"expected 403, got {r1.status_code}: {r1.text}"
        r2 = designer.delete(f"{API}/orders/nope", json={"reason": "x", "confirm": "HAPUS"})
        assert r2.status_code == 403, f"bogus id: expected 403, got {r2.status_code}"


# ---------------- B2 DASHBOARD PRODUCTION ----------------
EXPECTED_ROW_FIELDS = {
    "id", "po_number", "customer", "outlet", "deadline", "urgent",
    "total_quantity", "operators", "design_status", "design_bypassed",
    "payment_status", "payment_bypassed", "hold", "hold_reasons",
    "screen_qty", "helper_required", "helper_prepared", "helper_shortage",
    "helper_shortage_status", "packing_status", "delivery_note_number",
    "loading_ok", "vehicle_plate", "driver_name", "delivery_status",
    "delivery_proof_count", "current_stage", "status_final", "cancelled",
}


class TestB2Dashboard:
    def test_dashboard_admin_ok_schema(self, admin):
        r = admin.get(f"{API}/dashboard/production")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "rows" in data and "total" in data
        assert data["total"] == len(data["rows"])
        if data["rows"]:
            row = data["rows"][0]
            missing = EXPECTED_ROW_FIELDS - set(row.keys())
            assert not missing, f"missing row fields: {missing}"
            # operators must be a list; if any splits, each must contain expected keys
            assert isinstance(row["operators"], list)
            for op in row["operators"]:
                for k in ("operator_name", "quantity", "qty_completed", "stage_label"):
                    assert k in op, f"operator missing {k}: {op}"

    def test_dashboard_head_production_ok(self, head_prod):
        r = head_prod.get(f"{API}/dashboard/production")
        assert r.status_code == 200, r.text

    def test_dashboard_head_operations_ok(self, head_ops):
        r = head_ops.get(f"{API}/dashboard/production")
        assert r.status_code == 200, r.text

    def test_dashboard_designer_403(self, designer):
        r = designer.get(f"{API}/dashboard/production")
        assert r.status_code == 403, f"designer must be 403 (no dashboard.production.view). got {r.status_code}"

    def test_filter_include_cancelled(self, admin):
        base = admin.get(f"{API}/dashboard/production").json()["total"]
        withc = admin.get(
            f"{API}/dashboard/production", params={"include_cancelled": "true"}
        ).json()["total"]
        assert withc >= base, f"include_cancelled=true count ({withc}) < default ({base})"

    def test_filter_exclude_completed(self, admin):
        base = admin.get(f"{API}/dashboard/production").json()["total"]
        excl = admin.get(
            f"{API}/dashboard/production", params={"include_completed": "false"}
        ).json()["total"]
        assert excl <= base, f"include_completed=false count ({excl}) > default ({base})"


# ---------------- B2 EXPORT EXCEL ----------------
EXPECTED_HEADERS = [
    "Tanggal Pengiriman", "Prioritas", "No PO", "Customer", "Outlet", "Sales",
    "Total Qty", "Operator", "Item Cetak", "Qty Operator", "Qty Selesai Operator",
    "Approval Design", "Design Bypass", "Approval Payment", "Payment Bypass",
    "HOLD", "Alasan HOLD",
    "Qty Screen", "Catatan Screener",
    "Qty Dibutuhkan (Helper)", "Qty Disiapkan (Helper)", "Qty Kurang (Helper)", "Status Barang",
    "Stage Sekarang", "Packing", "No Surat Jalan", "Loading OK", "Kendaraan", "Driver",
    "Status Kirim", "Jumlah Bukti Kirim", "Status Akhir", "Alasan Cancel",
]


class TestB2Export:
    def test_export_all_ok(self, admin):
        # Snapshot of active orders count BEFORE export
        before = admin.get(f"{API}/orders").json()
        before_count = len(before) if isinstance(before, list) else len(before.get("orders", []))

        r = admin.post(
            f"{API}/dashboard/production/export", json={"scope": "all"}
        )
        assert r.status_code == 200, r.text
        ct = r.headers.get("Content-Type", "")
        assert "openxmlformats" in ct, f"unexpected content-type: {ct}"
        assert len(r.content) > 100
        # Load workbook
        wb = load_workbook(io.BytesIO(r.content))
        assert "Jadwal Produksi" in wb.sheetnames
        assert "Ringkasan" in wb.sheetnames
        ws = wb["Jadwal Produksi"]
        header_row = [c.value for c in ws[1]]
        assert len(header_row) == 33, f"expected 33 columns, got {len(header_row)}: {header_row}"
        assert header_row == EXPECTED_HEADERS, f"header mismatch: {header_row}"

        # DB unchanged
        after = admin.get(f"{API}/orders").json()
        after_count = len(after) if isinstance(after, list) else len(after.get("orders", []))
        assert after_count == before_count, f"orders count changed: {before_count} → {after_count}"

    def test_export_filtered_by_ids(self, admin):
        # Pick 2 ids from dashboard
        dash = admin.get(f"{API}/dashboard/production").json()
        rows = dash["rows"]
        if len(rows) < 2:
            pytest.skip("need >=2 orders for filtered export")
        ids = [rows[0]["id"], rows[1]["id"]]

        r = admin.post(
            f"{API}/dashboard/production/export",
            json={"scope": "filtered", "ids": ids},
        )
        assert r.status_code == 200, r.text
        wb = load_workbook(io.BytesIO(r.content))
        ws = wb["Jadwal Produksi"]

        # Collect PO numbers appearing in the sheet (column C = "No PO")
        po_nums = set()
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[2]:  # No PO col
                po_nums.add(row[2])
        expected_po = {rows[0]["po_number"], rows[1]["po_number"]}
        assert po_nums == expected_po, f"expected {expected_po}, got {po_nums}"

        # Total Qty (col G, idx 6) should only appear once per PO (either in the only row
        # OR only in the first row when operator_splits exist)
        totals_per_po = {p: 0 for p in expected_po}
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[2] and row[6] is not None and row[6] != "":
                totals_per_po[row[2]] += 1
        for po, c in totals_per_po.items():
            assert c <= 1, f"Total Qty duplicated for {po}: {c} rows"

    def test_export_designer_forbidden(self, designer):
        # Designer has neither view nor export perm — must be 403
        r = designer.post(f"{API}/dashboard/production/export", json={"scope": "all"})
        assert r.status_code == 403, f"designer expected 403, got {r.status_code}: {r.text}"

    def test_audit_log_has_export_entry(self, admin):
        # Trigger a fresh export to ensure at least one entry
        admin.post(f"{API}/dashboard/production/export", json={"scope": "all"})
        r = admin.get(f"{API}/admin/audit-logs", params={"action": "dashboard.production.export"})
        assert r.status_code == 200, r.text
        body = r.json()
        entries = body.get("logs") or body.get("entries") or []
        if isinstance(entries, dict):
            entries = entries.get("items", [])
        # Find any entry with our action + admin actor
        found = [
            e for e in entries
            if isinstance(e, dict) and e.get("action") == "dashboard.production.export"
            and (e.get("actor_email") == ADMIN or e.get("actor", {}).get("email") == ADMIN)
        ]
        assert found, f"no audit entry for dashboard.production.export by admin: sample={entries[:3]}"


# ---------------- REGRESSION Phase A + Req 5 ----------------
class TestPhaseARegression:
    def test_list_orders_aktif(self, admin):
        r = admin.get(f"{API}/orders", params={"status": "aktif"})
        assert r.status_code == 200

    def test_get_order_detail(self, admin):
        r = admin.get(f"{API}/orders")
        arr = r.json() if isinstance(r.json(), list) else r.json().get("orders", [])
        if not arr:
            pytest.skip("no orders")
        # Find an active (non-cancelled) one
        active = next((o for o in arr if not o.get("cancelled")), arr[0])
        r2 = admin.get(f"{API}/orders/{active['id']}")
        assert r2.status_code == 200

    def test_list_roles(self, admin):
        r = admin.get(f"{API}/admin/roles")
        assert r.status_code == 200
        assert isinstance(r.json().get("roles"), list)
        assert len(r.json()["roles"]) >= 9

    def test_advance_on_cancelled_returns_409(self, admin):
        # Find or create a cancelled PO
        r = admin.get(f"{API}/orders", params={"include_cancelled": "true"})
        arr = r.json() if isinstance(r.json(), list) else r.json().get("orders", [])
        cancelled = next((o for o in arr if o.get("cancelled")), None)
        if not cancelled:
            pytest.skip("no cancelled PO to test advance guard")
        r2 = admin.post(f"{API}/orders/{cancelled['id']}/advance", json={})
        assert r2.status_code in (409, 400), f"expected 409/400 on cancelled advance, got {r2.status_code}: {r2.text}"
