"""Iteration 19 retest — Phase B HIGH fix: role_permissions top-up migration for
head_production and head_operations (dashboard.production.view/export + po.cancel).

Auth is via httpOnly cookie set on POST /api/auth/login (no bearer token),
so tests use per-user requests.Session objects.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://print-ops-hub-12.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

CREDS = {
    "admin": ("admin@eternalpackaging.com", "Eternal123!"),
    "head_production": ("production@eternalpackaging.com", "Eternal123!"),
    "head_operations": ("operasional@eternalpackaging.com", "Eternal123!"),
    "designer": ("designer@eternalpackaging.com", "Eternal123!"),
}


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed {email}: {r.status_code} {r.text[:200]}"
    return s


@pytest.fixture(scope="module")
def sess():
    return {k: _login(*v) for k, v in CREDS.items()}


# ---------- FIX VERIFICATION: dashboard.production.view/export ----------

@pytest.mark.parametrize("role", ["head_production", "head_operations"])
def test_auth_me_has_dashboard_production_perms(sess, role):
    r = sess[role].get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200
    perms = r.json().get("permissions", [])
    assert "dashboard.production.view" in perms, f"{role} missing dashboard.production.view; got {perms}"
    assert "dashboard.production.export" in perms, f"{role} missing dashboard.production.export; got {perms}"


@pytest.mark.parametrize("role", ["head_production", "head_operations", "admin"])
def test_dashboard_production_200(sess, role):
    r = sess[role].get(f"{API}/dashboard/production", timeout=20)
    assert r.status_code == 200, f"{role}: {r.status_code} {r.text[:200]}"
    body = r.json()
    assert isinstance(body, (dict, list))


@pytest.mark.parametrize("role", ["head_production", "head_operations", "admin"])
def test_export_production_excel_200(sess, role):
    r = sess[role].post(f"{API}/dashboard/production/export", json={}, timeout=45)
    assert r.status_code == 200, f"{role}: {r.status_code} {r.text[:200]}"
    ct = r.headers.get("content-type", "")
    assert "spreadsheetml" in ct or "xlsx" in ct or "octet-stream" in ct, f"unexpected content-type: {ct}"
    assert len(r.content) > 500, "xlsx content too small"
    assert r.content[:2] == b"PK", "not a zip/xlsx magic"


# ---------- NO REGRESSION: designer must remain 403 ----------

def test_designer_dashboard_production_403(sess):
    r = sess["designer"].get(f"{API}/dashboard/production", timeout=15)
    assert r.status_code == 403, f"designer expected 403 got {r.status_code}"


def test_designer_export_production_403(sess):
    r = sess["designer"].post(f"{API}/dashboard/production/export", json={}, timeout=15)
    assert r.status_code == 403


def test_designer_auth_me_has_no_dashboard_production(sess):
    r = sess["designer"].get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200
    perms = r.json().get("permissions", [])
    assert "dashboard.production.view" not in perms
    assert "dashboard.production.export" not in perms


# ---------- head_operations po.cancel top-up ----------

def test_head_operations_has_po_cancel_perm(sess):
    r = sess["head_operations"].get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200
    perms = r.json().get("permissions", [])
    assert "po.cancel" in perms, f"head_operations missing po.cancel; got {perms}"


def test_head_operations_can_cancel_po(sess):
    """Pick a non-critical, non-cancelled PO and cancel it as head_operations.
    Success criteria: NOT 403 (top-up applied). 200 is best; 400/409 acceptable as business-rule."""
    admin = sess["admin"]
    ops = sess["head_operations"]

    r = admin.get(f"{API}/orders", timeout=20)
    assert r.status_code == 200, r.text[:200]
    orders = r.json()
    if isinstance(orders, dict):
        orders = orders.get("items") or orders.get("data") or orders.get("orders") or []
    assert isinstance(orders, list) and orders, "no orders returned"

    target = None
    for o in orders:
        status = (o.get("status") or o.get("status_label") or "").lower()
        if any(x in status for x in ("cancel", "complete", "selesai", "batal")):
            continue
        target = o
        if "TEST" in str(o.get("po_number", "")).upper():
            break

    if not target:
        pytest.skip("No cancellable PO found")

    po_id = target.get("id") or target.get("_id") or target.get("po_id")
    assert po_id, f"No id field on order keys={list(target.keys())}"

    r = ops.post(
        f"{API}/orders/{po_id}/cancel",
        json={"reason": "iteration19 retest — head_operations po.cancel top-up verification"},
        timeout=25,
    )
    print(f"[po.cancel] po_id={po_id} po_number={target.get('po_number')} "
          f"status_before={target.get('status')} -> resp={r.status_code} body={r.text[:200]}")
    assert r.status_code != 403, f"head_operations got 403 on po.cancel — top-up NOT applied. body={r.text[:300]}"
    assert r.status_code in (200, 201, 400, 409, 422), f"unexpected: {r.status_code} {r.text[:200]}"
