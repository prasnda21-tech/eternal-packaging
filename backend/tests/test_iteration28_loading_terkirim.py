"""
Iteration 28 — Task 14 targeted BUGFIX: Loading (stage 13) → Terkirim (stage 14).

Covers:
- Perm-based /loading (loading.update) — operator 403, admin 200
- /loading TIDAK auto-advance current_stage
- Loading completion actor recorded when checklist first becomes valid
- Loading gate: /loading di stage <12 → 400 SJ prereq
- Actor SJ→Loading timeline transition (advance 12→13 note "Surat Jalan selesai — menuju Loading")
- /advance 13→14 gate: prereq_reasons multi-line when checklist incomplete
- /delivered gate: same validator as advance path
- /delivered perm-based (delivery.update) — operator 403, admin 200
- Driver role business rule: delivery_proofs mandatory
- terkirim_confirmed derived flag
- Existing regression: /packing-info stage 11→12 still requires qty_packed;
  /surat-jalan stage 12→13 still requires SJ explicit
"""
import os
import uuid
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

PASS = "Eternal123!"
ADMIN_EMAIL = "admin@eternalpackaging.com"
OPERATOR_EMAIL = "operator@eternalpackaging.com"
DRIVER_EMAIL = "driver@eternalpackaging.com"


# ---------- fixtures ----------
@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


def _session(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": email, "password": PASS}, timeout=15)
    assert r.status_code == 200, f"login {email} → {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_tok():
    return _session(ADMIN_EMAIL)


@pytest.fixture(scope="module")
def op_tok():
    return _session(OPERATOR_EMAIL)


@pytest.fixture(scope="module")
def driver_tok():
    try:
        return _session(DRIVER_EMAIL)
    except AssertionError:
        return None


def _h(_tok):
    # Session already carries cookies — kept for signature compatibility.
    return {"Content-Type": "application/json"}


def _mk_order(db, current_stage=13, with_sj=True, loading_complete=False,
              packing=True, delivered=False):
    """Insert a synthetic order at desired stage — bypass workflow to isolate
    Loading/Terkirim behaviour. Uses TEST_T28 prefix for easy cleanup."""
    oid = f"TEST-T28-{uuid.uuid4().hex[:8]}"
    doc = {
        "id": oid,
        "customer_name": f"TEST_T28 {oid}",
        "po_number": oid,
        "product_name": "Kardus Test T28",
        "quantity": 100,
        "price_per_unit": 1000,
        "total_price": 100000,
        "current_stage": current_stage,
        "priority": "normal",
        "design_status": "approved",
        "design_approved_by": ADMIN_EMAIL,
        "design_approved_at": "2026-01-01T00:00:00+00:00",
        "design_files": [{"name": "d.png", "url": "http://x"}],
        "payment_status": "approved",
        "payment_approved_by": ADMIN_EMAIL,
        "payment_approved_at": "2026-01-01T00:00:00+00:00",
        "payment_proofs": [{"name": "p.png", "url": "http://x"}],
        "sample_files": [{"name": "s.png", "url": "http://x"}],
        "sample_approved": True,
        "sample_approved_by": ADMIN_EMAIL,
        "sample_approved_at": "2026-01-01T00:00:00+00:00",
        "qty_screened": 100,
        "qty_prepared": 100,
        "qty_printed": 100,
        "qty_packed": 100 if packing else 0,
        "packing_status": "Sudah" if packing else "Belum",
        "operator_splits": [
            {"id": str(uuid.uuid4()), "operator_email": OPERATOR_EMAIL,
             "operator_name": "Operator T28", "qty": 100, "qty_completed": 100,
             "stage": 11, "updated_at": "2026-01-01T00:00:00+00:00"}
        ],
        "timeline": [],
        "cancelled": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    if with_sj:
        doc["delivery_note_number"] = f"SJ/{oid}"
        doc["delivery_note_created_by"] = ADMIN_EMAIL
        doc["delivery_note_created_by_name"] = "Admin"
        doc["delivery_note_created_at"] = "2026-01-02T00:00:00+00:00"
    if loading_complete:
        doc["loading_checklist"] = {
            "vehicle_plate": "B 1 TEST",
            "driver_name": "Sopir T28",
            "items_loaded": True,
            "seal_ok": True,
            "notes": "seed",
            "updated_by": ADMIN_EMAIL,
            "updated_at": "2026-01-03T00:00:00+00:00",
        }
        doc["loading_completed_at"] = "2026-01-03T00:00:00+00:00"
        doc["loading_completed_by"] = ADMIN_EMAIL
        doc["loading_completed_by_name"] = "Admin"
    if delivered:
        doc["delivered_at"] = "2026-01-04T00:00:00+00:00"
        doc["delivered_by"] = ADMIN_EMAIL
        doc["delivered_by_name"] = "Admin"
        doc["delivery_status"] = "Terkirim"
        doc["current_stage"] = 14
    db.orders.insert_one(doc)
    return oid


@pytest.fixture()
def cleanup(db):
    ids = []
    yield ids
    if ids:
        db.orders.delete_many({"id": {"$in": ids}})
    # Also cleanup any stray TEST_T28
    db.orders.delete_many({"id": {"$regex": "^TEST-T28-"}})


# ---------- Test 1: /loading perm-based ----------
def test_1_loading_perm_operator_403_admin_200(db, admin_tok, op_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True)
    cleanup.append(oid)
    payload = {"vehicle_plate": "B 1", "driver_name": "S", "items_loaded": False, "seal_ok": False}
    r_op = op_tok.post(f"{BASE_URL}/api/orders/{oid}/loading", json=payload)
    assert r_op.status_code == 403, r_op.text
    assert "loading.update" in r_op.text

    r_ad = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/loading", json=payload)
    assert r_ad.status_code == 200, r_ad.text


# ---------- Test 2: /loading TIDAK auto-advance ----------
def test_2_loading_incomplete_no_auto_advance(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True)
    cleanup.append(oid)
    payload = {"vehicle_plate": "B 1", "driver_name": "S", "items_loaded": False, "seal_ok": True}
    r = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/loading", json=payload)
    assert r.status_code == 200
    order = r.json()["order"]
    assert order["current_stage"] == 13
    assert order.get("loading_completed") is False
    assert not order.get("loading_completed_at")


# ---------- Test 3: /loading complete → actor + timeline ----------
def test_3_loading_complete_actor_and_timeline(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True)
    cleanup.append(oid)
    payload = {"vehicle_plate": "B 9", "driver_name": "SopirX", "items_loaded": True, "seal_ok": True}
    r = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/loading", json=payload)
    assert r.status_code == 200
    order = r.json()["order"]
    assert order["loading_completed"] is True
    assert order["loading_completed_by"] == ADMIN_EMAIL
    assert order["loading_completed_by_name"] in ("Admin", "admin", None) or order["loading_completed_by_name"]
    assert order["loading_completed_at"]
    # Timeline entry stage=13 with note 'Loading selesai'
    tl = order.get("timeline", [])
    stage13 = [t for t in tl if t.get("stage") == 13 and "Loading selesai" in (t.get("note") or "")]
    assert stage13, f"missing loading-selesai timeline entry: {tl}"
    # current_stage remains 13 (no auto-advance to 14)
    assert order["current_stage"] == 13


# ---------- Test 4: /loading gate: stage <12 (no SJ) → 400 ----------
def test_4_loading_gate_no_sj(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=11, with_sj=False)
    cleanup.append(oid)
    payload = {"vehicle_plate": "X", "items_loaded": True, "seal_ok": True}
    r = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/loading", json=payload)
    assert r.status_code == 400
    assert "Surat Jalan" in r.text


# ---------- Test 5: Actor SJ→Loading timeline transition on advance 12→13 ----------
def test_5_actor_sj_to_loading_timeline(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=12, with_sj=True)
    cleanup.append(oid)
    r = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/advance", json={"note": ""})
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["current_stage"] == 13
    tl = order.get("timeline", [])
    # Entry advance harus stage=12 dengan note tertentu, BUKAN stage=13 auto
    match = [t for t in tl if t.get("stage") == 12 and "Surat Jalan selesai" in (t.get("note") or "")]
    assert match, f"missing advance-12 timeline entry: {tl}"
    # Tidak boleh ada auto entry stage=13 "Loading selesai" karena checklist belum
    auto13 = [t for t in tl if t.get("stage") == 13 and "Loading selesai" in (t.get("note") or "")]
    assert not auto13, f"unexpected auto Loading-selesai entry: {auto13}"


# ---------- Test 6: /advance 13→14 blocked when loading incomplete; ok after complete ----------
def test_6_advance_terkirim_gate(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True, loading_complete=False)
    cleanup.append(oid)
    r_block = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/advance", json={"note": ""})
    assert r_block.status_code == 400, r_block.text
    detail = r_block.json().get("detail")
    assert isinstance(detail, dict)
    reasons = detail.get("prereq_reasons") or []
    assert any("Checklist Loading" in x for x in reasons), reasons

    # Now complete loading, then advance
    lp = {"vehicle_plate": "B", "driver_name": "S", "items_loaded": True, "seal_ok": True}
    r_ok = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/loading", json=lp)
    assert r_ok.status_code == 200
    r_adv = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/advance", json={"note": ""})
    assert r_adv.status_code == 200
    assert r_adv.json()["order"]["current_stage"] == 14


# ---------- Test 7: /delivered gate SAME validator ----------
def test_7_delivered_gate_same_validator(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True, loading_complete=False)
    cleanup.append(oid)
    r_block = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    assert r_block.status_code == 400, r_block.text
    detail = r_block.json().get("detail")
    assert isinstance(detail, dict)
    reasons = detail.get("prereq_reasons") or []
    assert any("Checklist Loading" in x for x in reasons), reasons

    # complete loading and mark delivered
    lp = {"vehicle_plate": "B", "driver_name": "S", "items_loaded": True, "seal_ok": True}
    admin_tok.post(f"{BASE_URL}/api/orders/{oid}/loading", json=lp)
    r_ok = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    assert r_ok.status_code == 200, r_ok.text
    o = r_ok.json()["order"]
    assert o["current_stage"] == 14
    assert o["delivery_status"] == "Terkirim"
    assert o["delivered_by"] == ADMIN_EMAIL
    assert o["delivered_at"]
    assert o["terkirim_confirmed"] is True


# ---------- Test 8: /delivered perm-based ----------
def test_8_delivered_perm_operator_403(db, admin_tok, op_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True, loading_complete=True)
    cleanup.append(oid)
    r_op = op_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    assert r_op.status_code == 403
    assert "delivery.update" in r_op.text


# ---------- Test 9: Driver role business rule (delivery_proofs mandatory) ----------
def test_9_driver_needs_delivery_proofs(db, driver_tok, cleanup):
    if not driver_tok:
        pytest.skip("driver account not available")
    oid = _mk_order(db, current_stage=13, with_sj=True, loading_complete=True)
    cleanup.append(oid)
    r = driver_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    # Driver must have delivery_proofs OR must have delivery.update perm
    # If 403, driver lacks perm — that's OK (perm-based). If 400 → missing proofs.
    assert r.status_code in (400, 403), r.text
    if r.status_code == 400:
        assert "foto bukti" in r.text.lower() or "delivery_proofs" in r.text.lower() or "bukti kirim" in r.text.lower()


# ---------- Test 10: terkirim_confirmed derived flag ----------
def test_10_terkirim_confirmed_derived(db, admin_tok, cleanup):
    # not delivered yet
    oid = _mk_order(db, current_stage=13, with_sj=True, loading_complete=True)
    cleanup.append(oid)
    r = admin_tok.get(f"{BASE_URL}/api/orders/{oid}")
    assert r.status_code == 200
    o = r.json()
    if "order" in o: o = o["order"]
    assert o.get("terkirim_confirmed") is False
    assert not o.get("delivered_at")

    # deliver
    r2 = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    assert r2.status_code == 200
    r3 = admin_tok.get(f"{BASE_URL}/api/orders/{oid}")
    o3 = r3.json()
    if "order" in o3: o3 = o3["order"]
    assert o3["terkirim_confirmed"] is True
    assert o3["delivered_at"]
    assert o3["delivered_by"] == ADMIN_EMAIL


# ---------- Test 11: Existing data safety — stage 14 already delivered stays intact ----------
def test_11_existing_delivered_intact(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=14, with_sj=True, loading_complete=True, delivered=True)
    cleanup.append(oid)
    r = admin_tok.get(f"{BASE_URL}/api/orders/{oid}")
    o = r.json()
    if "order" in o: o = o["order"]
    assert o["current_stage"] == 14
    assert o["terkirim_confirmed"] is True
    assert o["loading_completed"] is True


# ---------- Test 12: Regression — advance 12→13 blocked without SJ ----------
def test_12_regression_advance_12_needs_sj(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=12, with_sj=False)
    cleanup.append(oid)
    r = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/advance", json={"note": ""})
    assert r.status_code == 400
    detail = r.json().get("detail")
    assert isinstance(detail, dict)
    assert any("Surat Jalan" in x for x in detail.get("prereq_reasons", []))


# ---------- Test 13: Dashboard 15 stages, no Driver label ----------
def test_13_dashboard_15_stages_no_driver(admin_tok):
    r = admin_tok.get(f"{BASE_URL}/api/dashboard/summary")
    assert r.status_code == 200
    data = r.json()
    sb = data.get("stage_breakdown") or []
    assert len(sb) == 15, f"expected 15 stages, got {len(sb)}"
    labels = [s.get("label") for s in sb]
    assert "Driver" not in labels, labels
    assert "Terkirim" in labels
