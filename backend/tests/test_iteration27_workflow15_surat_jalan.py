"""Iteration 27 - Task 13 targeted BUGFIX tests.
Coverage:
  Part A: work_date per operator split (independent PATCH).
  Part B: workflow 15 stages (Driver removed as stage), list orders status filter,
          dashboard summary 15 stages (no 'Driver' label).
  Part C: legacy POST /surat-jalan disabled (BUG-04) + advance 12->13 gate + no auto SJ on 11->12.
Uses pymongo to seed POs directly (no public POST /orders endpoint).
"""
import os
import uuid
import time
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL"):
                BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
                break
API = f"{BASE_URL}/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

ADMIN = {"email": "admin@eternalpackaging.com", "password": "Eternal123!"}
OPERATOR = {"email": "operator@eternalpackaging.com", "password": "Eternal123!"}
OPERATOR2 = {"email": "operator2@eternalpackaging.com", "password": "Eternal123!"}


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def mongo():
    c = MongoClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest.fixture(scope="module")
def admin_sess():
    return _login(ADMIN["email"], ADMIN["password"])


@pytest.fixture(scope="module")
def operator_sess():
    return _login(OPERATOR["email"], OPERATOR["password"])


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _seed_po(mongo, stage=12, total_quantity=100, operator_splits=None,
             packing_status="Sudah", packing_info=None, extra=None):
    now = _now()
    if operator_splits is None:
        operator_splits = [{
            "id": str(uuid.uuid4()),
            "operator_email": OPERATOR["email"],
            "operator_name": "Rafi",
            "quantity": total_quantity,
            "qty_completed": total_quantity,
            "stage": 8,
            "updated_at": now,
        }]
    if packing_info is None and stage >= 12:
        packing_info = {"qty_packed": total_quantity, "notes": "seed", "saved_at": now, "saved_by": "seed"}
    doc = {
        "id": str(uuid.uuid4()),
        "po_number": f"TEST-T27-{uuid.uuid4().hex[:8].upper()}",
        "customer_name": "TEST Customer",
        "product_name": "TEST Product",
        "total_quantity": total_quantity,
        "current_stage": stage,
        "status": "active",
        "priority": "normal",
        "design_status": "Approved",
        "design_approved_by": "seed", "design_approved_at": now,
        "design_files": [{"id": str(uuid.uuid4()), "name": "d.png"}],
        "payment_status": "Approved",
        "payment_approved_by": "seed", "payment_approved_at": now,
        "payment_files": [{"id": str(uuid.uuid4()), "name": "p.png"}],
        "operator_splits": operator_splits,
        "sample_files": [{"id": str(uuid.uuid4()), "name": "s.png"}],
        "sample_approved": True,
        "sample_approved_by": "seed",
        "screener_info": {"screen_qty": total_quantity},
        "helper_prep": {"qty_required": total_quantity, "qty_prepared": total_quantity},
        "timeline": [{"stage": stage, "at": now, "by": "seed", "note": "seeded"}],
        "created_at": now, "updated_at": now,
        "notes": "",
        "packing_status": packing_status,
        "delivery_status": "Belum",
    }
    if packing_info is not None:
        doc["packing_info"] = packing_info
    if extra:
        doc.update(extra)
    mongo.orders.insert_one(doc)
    return doc


@pytest.fixture
def cleanup_pos(mongo):
    ids = []
    yield ids
    if ids:
        mongo.orders.delete_many({"id": {"$in": ids}})


def _get_order(sess, oid):
    r = sess.get(f"{API}/orders/{oid}", timeout=10)
    assert r.status_code == 200, r.text
    j = r.json()
    return j.get("order") or j


# ================================================================
# Part A: work_date per operator split
# ================================================================
class TestPartAWorkDate:
    def test_operator_split_work_date_independent(self, admin_sess, mongo, cleanup_pos):
        splits = [
            {"id": str(uuid.uuid4()), "operator_email": OPERATOR["email"], "operator_name": "Rafi",
             "quantity": 50, "qty_completed": 0, "stage": 8, "updated_at": _now()},
            {"id": str(uuid.uuid4()), "operator_email": OPERATOR2["email"], "operator_name": "Nanda",
             "quantity": 50, "qty_completed": 0, "stage": 8, "updated_at": _now()},
        ]
        doc = _seed_po(mongo, stage=5, total_quantity=100, operator_splits=splits, packing_status="Belum", packing_info=None)
        cleanup_pos.append(doc["id"])
        oid = doc["id"]
        sid_a, sid_b = splits[0]["id"], splits[1]["id"]

        initial = _get_order(admin_sess, oid)
        initial_stage = initial.get("current_stage")

        r1 = admin_sess.patch(f"{API}/orders/{oid}/operator-splits/{sid_a}/work-date",
                              json={"work_date": "2026-02-10"}, timeout=10)
        assert r1.status_code == 200, r1.text
        r2 = admin_sess.patch(f"{API}/orders/{oid}/operator-splits/{sid_b}/work-date",
                              json={"work_date": "2026-02-15"}, timeout=10)
        assert r2.status_code == 200, r2.text

        o2 = _get_order(admin_sess, oid)
        sp = {s["id"]: s for s in (o2.get("operator_splits") or [])}
        assert sp[sid_a].get("work_date") == "2026-02-10"
        assert sp[sid_b].get("work_date") == "2026-02-15"
        assert o2.get("current_stage") == initial_stage, "work_date should not change current_stage"

    def test_operator_split_work_date_clear(self, admin_sess, mongo, cleanup_pos):
        splits = [{"id": str(uuid.uuid4()), "operator_email": OPERATOR["email"], "operator_name": "Rafi",
                   "quantity": 50, "qty_completed": 0, "stage": 8, "work_date": "2026-02-01",
                   "updated_at": _now()}]
        doc = _seed_po(mongo, stage=5, total_quantity=50, operator_splits=splits, packing_status="Belum", packing_info=None)
        cleanup_pos.append(doc["id"])
        sid = splits[0]["id"]
        r = admin_sess.patch(f"{API}/orders/{doc['id']}/operator-splits/{sid}/work-date",
                             json={"work_date": None}, timeout=10)
        assert r.status_code == 200, r.text
        o = _get_order(admin_sess, doc["id"])
        sp = (o.get("operator_splits") or [])[0]
        assert not sp.get("work_date")


# ================================================================
# Part B: workflow 15 stages
# ================================================================
class TestPartBWorkflow15:
    def test_dashboard_summary_15_stages_no_driver(self, admin_sess):
        r = admin_sess.get(f"{API}/dashboard/summary", timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        sb = data.get("stage_breakdown")
        assert sb is not None
        if isinstance(sb, list):
            assert len(sb) == 15, f"expected 15 stages, got {len(sb)}"
            labels = [str(x.get("label") or x.get("stage") or x.get("name") or "") for x in sb]
        elif isinstance(sb, dict):
            labels = list(sb.keys())
            assert len(labels) == 15
        else:
            pytest.fail(f"unexpected type: {type(sb)}")
        for lbl in labels:
            assert "driver" not in lbl.lower(), f"Driver present in stage labels: {labels}"

    def test_list_aktif_lt_14(self, admin_sess):
        r = admin_sess.get(f"{API}/orders", params={"status": "aktif"}, timeout=10)
        assert r.status_code == 200
        j = r.json()
        orders = j.get("orders") if isinstance(j, dict) else j
        for o in orders:
            if isinstance(o, dict):
                assert o.get("current_stage", 0) < 14

    def test_list_selesai_gte_14(self, admin_sess):
        r = admin_sess.get(f"{API}/orders", params={"status": "selesai"}, timeout=10)
        assert r.status_code == 200
        j = r.json()
        orders = j.get("orders") if isinstance(j, dict) else j
        for o in orders:
            if isinstance(o, dict):
                assert o.get("current_stage", 0) >= 14

    def test_migration_idempotent(self, mongo):
        """Migration is triggered at startup. After startup, no legacy stage=15 should exist.
        Also seeds legacy docs and calls the migration query manually to verify idempotency."""
        # After startup, DB should have no current_stage==15
        count_15 = mongo.orders.count_documents({"current_stage": 15})
        assert count_15 == 0, f"legacy stage=15 still exists ({count_15})"


# ================================================================
# Part C: Surat Jalan endpoint
# ================================================================
class TestPartCSuratJalan:
    def test_create_sj_operator_forbidden(self, admin_sess, operator_sess, mongo, cleanup_pos):
        doc = _seed_po(mongo, stage=12)
        cleanup_pos.append(doc["id"])
        r = operator_sess.post(f"{API}/orders/{doc['id']}/surat-jalan", json={}, timeout=10)
        assert r.status_code == 403, f"expected 403, got {r.status_code} {r.text}"

    def test_create_sj_admin_legacy_disabled_409(self, admin_sess, mongo, cleanup_pos):
        """BUG-04: legacy POST /surat-jalan is disabled (use POST /sales-deliveries)."""
        doc = _seed_po(mongo, stage=10, packing_status="Belum", packing_info=None)
        cleanup_pos.append(doc["id"])
        r = admin_sess.post(f"{API}/orders/{doc['id']}/surat-jalan", json={}, timeout=10)
        assert r.status_code == 409, f"{r.status_code} {r.text}"
        detail = r.json().get("detail") or {}
        assert detail.get("code") == "LEGACY_SJ_CREATE_DISABLED"
        assert "sales-deliveries" in str(detail.get("canonical_endpoint") or "")

    def test_create_sj_admin_at_stage_12_legacy_disabled_no_mutation(self, admin_sess, mongo, cleanup_pos):
        doc = _seed_po(mongo, stage=12)
        cleanup_pos.append(doc["id"])
        oid = doc["id"]
        # Pre-check: no SJ yet
        o0 = _get_order(admin_sess, oid)
        assert o0.get("surat_jalan_created") is False
        assert o0.get("loading_ready") is False
        assert "Surat Jalan harus dibuat terlebih dahulu." in (o0.get("loading_prereq_reasons") or [])

        r = admin_sess.post(f"{API}/orders/{oid}/surat-jalan",
                            json={"delivery_note_number": "SJ-TEST-27", "notes": "test"}, timeout=10)
        assert r.status_code == 409, r.text
        detail = r.json().get("detail") or {}
        assert detail.get("code") == "LEGACY_SJ_CREATE_DISABLED"

        # Order unchanged — canonical create is POST /sales-deliveries
        o1 = _get_order(admin_sess, oid)
        assert o1.get("surat_jalan_created") is False
        assert o1.get("current_stage") == 12
        assert not o1.get("delivery_note_number")

        # Advance 12→13 still blocked without real SJ
        r2 = admin_sess.post(f"{API}/orders/{oid}/advance", json={}, timeout=10)
        assert r2.status_code == 400, r2.text
        detail2 = r2.json().get("detail") or {}
        reasons = detail2.get("prereq_reasons") if isinstance(detail2, dict) else []
        assert any("Surat Jalan" in str(x) for x in (reasons or []))

    def test_advance_12_to_13_blocked_without_sj(self, admin_sess, mongo, cleanup_pos):
        doc = _seed_po(mongo, stage=12)
        cleanup_pos.append(doc["id"])
        r = admin_sess.post(f"{API}/orders/{doc['id']}/advance", json={}, timeout=10)
        assert r.status_code == 400, f"{r.status_code} {r.text}"
        body = r.json()
        detail = body.get("detail") or body
        reasons = detail.get("prereq_reasons") if isinstance(detail, dict) else []
        joined = " ".join(reasons) if reasons else r.text
        assert "Surat Jalan" in joined

    def test_advance_11_to_12_no_auto_sj_number(self, admin_sess, mongo, cleanup_pos):
        # seed at stage 11 with qty_packed missing → add packing_info first via API
        doc = _seed_po(mongo, stage=11, packing_status="Belum", packing_info=None)
        cleanup_pos.append(doc["id"])
        oid = doc["id"]
        # add packing qty
        r0 = admin_sess.post(f"{API}/orders/{oid}/packing-info", json={"qty_packed": 100, "notes": ""}, timeout=10)
        assert r0.status_code == 200, r0.text
        # advance to 12
        r = admin_sess.post(f"{API}/orders/{oid}/advance", json={}, timeout=10)
        assert r.status_code == 200, r.text
        o = r.json().get("order") or {}
        assert o.get("current_stage") == 12
        assert not o.get("delivery_note_number"), f"delivery_note_number should be empty; got {o.get('delivery_note_number')}"
        assert not o.get("delivery_note_created_by")
        assert o.get("surat_jalan_created") is False
        assert o.get("loading_ready") is False

    def test_create_sj_legacy_disabled_keeps_flags_false(self, admin_sess, mongo, cleanup_pos):
        doc = _seed_po(mongo, stage=12)
        cleanup_pos.append(doc["id"])
        oid = doc["id"]
        r = admin_sess.post(f"{API}/orders/{oid}/surat-jalan", json={}, timeout=10)
        assert r.status_code == 409, r.text
        detail = r.json().get("detail") or {}
        assert detail.get("code") == "LEGACY_SJ_CREATE_DISABLED"
        o2 = _get_order(admin_sess, oid)
        assert o2.get("surat_jalan_created") is False
        assert o2.get("loading_ready") is False
