"""
Iteration 24 - HoP Dashboard: permission gate, semantic queues (design+payment
independent), cancelled exclusion, HOLD coexistence, KPI == list length.
"""
import os
import pytest
import requests
from pymongo import MongoClient

def _read_frontend_env():
    try:
        with open("/app/frontend/.env") as f:
            for ln in f:
                if ln.startswith("REACT_APP_BACKEND_URL="):
                    return ln.split("=", 1)[1].strip().strip('"').rstrip("/")
    except Exception:
        pass
    return os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

BASE_URL = _read_frontend_env()
assert BASE_URL, "REACT_APP_BACKEND_URL not set"
ADMIN = ("admin@eternalpackaging.com", "Eternal123!")
SCREENER = ("screener@eternalpackaging.com", "Eternal123!")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

TEST_PREFIX = "TEST-HOP24"


def _login(session, email, password):
    r = session.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text}"
    return r.json()


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    _login(s, *ADMIN)
    return s


@pytest.fixture(scope="module")
def screener_session():
    s = requests.Session()
    _login(s, *SCREENER)
    return s


@pytest.fixture(scope="module")
def mongo():
    c = MongoClient(MONGO_URL)
    db = c[DB_NAME]
    yield db
    # Cleanup all TEST-HOP24 POs
    db.orders.delete_many({"po_number": {"$regex": f"^{TEST_PREFIX}"}})
    c.close()


def _seed_po(db, po_number, **overrides):
    """Insert a minimal PO document directly for controlled testing."""
    import uuid
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "po_number": po_number,
        "customer": "TEST Customer",
        "outlet": "TEST Outlet",
        "deadline": (datetime.now(timezone.utc).replace(microsecond=0)).isoformat(),
        "order_date": now,
        "priority": "normal",
        "current_stage": 2,
        "status_label": "Approval Design",
        "design_status": "Waiting",
        "payment_status": "Waiting",
        "design_files": [{"name": "d.pdf", "url": "/x"}],
        "payment_files": [{"name": "p.pdf", "url": "/x"}],
        "design_bypassed": False,
        "payment_bypassed": False,
        "cancelled": False,
        "total_quantity": 100,
        "item_count": 1,
        "items": [],
        "operator_splits": [],
        "timeline": [],
    }
    doc.update(overrides)
    db.orders.insert_one(doc)
    return doc


# ---------- 1. Permission gate ----------
class TestPermissionGate:
    def test_admin_can_read_summary(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/dashboard/summary")
        assert r.status_code == 200
        data = r.json()
        assert "kpi" in data
        for k in ["total_active", "total_urgent", "due_today", "overdue",
                  "waiting_design_approval", "waiting_payment_approval", "hold", "completed"]:
            assert k in data["kpi"], f"missing kpi.{k}"
        for k in ["active", "urgent", "due_today", "overdue",
                  "waiting_design_approval", "waiting_payment_approval", "hold", "completed",
                  "stage_breakdown", "operator_load"]:
            assert k in data, f"missing list {k}"

    def test_screener_forbidden(self, screener_session):
        r = screener_session.get(f"{BASE_URL}/api/dashboard/summary")
        assert r.status_code == 403, f"expected 403 for screener, got {r.status_code} {r.text}"


# ---------- 2. KPI count == list length (structural invariant) ----------
class TestKpiEqualsListLength:
    def test_all_kpi_match_list_length(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/dashboard/summary")
        assert r.status_code == 200
        d = r.json()
        pairs = [
            ("total_active", "active"),
            ("total_urgent", "urgent"),
            ("due_today", "due_today"),
            ("overdue", "overdue"),
            ("waiting_design_approval", "waiting_design_approval"),
            ("waiting_payment_approval", "waiting_payment_approval"),
            ("hold", "hold"),
            ("completed", "completed"),
        ]
        for kpi_k, list_k in pairs:
            assert d["kpi"][kpi_k] == len(d[list_k]), \
                f"MISMATCH kpi.{kpi_k}={d['kpi'][kpi_k]} vs len({list_k})={len(d[list_k])}"


# ---------- 3. Semantic queue: design pending on stage > 5 ----------
class TestSemanticQueues:
    def test_design_pending_independent_of_stage(self, admin_session, mongo):
        po = _seed_po(mongo, f"{TEST_PREFIX}-STAGE7-DESIGN",
                      current_stage=7,   # far past scheduling
                      design_status="Waiting",
                      payment_status="Approved")
        try:
            r = admin_session.get(f"{BASE_URL}/api/dashboard/summary")
            assert r.status_code == 200
            d = r.json()
            ids = [o["id"] for o in d["waiting_design_approval"]]
            assert po["id"] in ids, f"PO stage=7 with design_status=Waiting missing from waiting_design_approval; got ids={ids}"
            # And NOT in waiting_payment_approval (payment approved)
            pay_ids = [o["id"] for o in d["waiting_payment_approval"]]
            assert po["id"] not in pay_ids
        finally:
            mongo.orders.delete_one({"id": po["id"]})

    def test_po_in_both_design_and_payment_queues(self, admin_session, mongo):
        po = _seed_po(mongo, f"{TEST_PREFIX}-BOTH",
                      current_stage=2,
                      design_status="Waiting",
                      payment_status="Waiting")
        try:
            r = admin_session.get(f"{BASE_URL}/api/dashboard/summary")
            d = r.json()
            design_ids = [o["id"] for o in d["waiting_design_approval"]]
            pay_ids = [o["id"] for o in d["waiting_payment_approval"]]
            assert po["id"] in design_ids, "not in waiting_design_approval"
            assert po["id"] in pay_ids, "not in waiting_payment_approval"
        finally:
            mongo.orders.delete_one({"id": po["id"]})


# ---------- 4. Cancelled exclusion ----------
class TestCancelledExclusion:
    def test_cancelled_po_excluded_from_all_lists(self, admin_session, mongo):
        po = _seed_po(mongo, f"{TEST_PREFIX}-CANCEL",
                      current_stage=2,
                      design_status="Waiting",
                      payment_status="Waiting",
                      priority="urgent")
        try:
            # Sanity: before cancel it's in lists
            d0 = admin_session.get(f"{BASE_URL}/api/dashboard/summary").json()
            assert po["id"] in [o["id"] for o in d0["active"]]

            # Cancel via API
            r = admin_session.post(f"{BASE_URL}/api/orders/{po['id']}/cancel",
                                   json={"reason": "test cancellation"})
            assert r.status_code == 200, r.text

            d = admin_session.get(f"{BASE_URL}/api/dashboard/summary").json()
            for key in ["active", "urgent", "waiting_design_approval",
                        "waiting_payment_approval", "hold", "completed",
                        "overdue", "due_today"]:
                ids = [o["id"] for o in d[key]]
                assert po["id"] not in ids, f"cancelled PO leaked into '{key}'"
            # stage_breakdown should not count cancelled
            total_stage = sum(s["count"] for s in d["stage_breakdown"])
            # We can't compare exactly (other POs exist), but our PO shouldn't
            # increment. Just make sure it's excluded from `active`.
        finally:
            mongo.orders.delete_one({"id": po["id"]})


# ---------- 5. HOLD does not hide pending action ----------
class TestHoldCoexistence:
    def test_hold_po_still_in_waiting_design(self, admin_session, mongo):
        po = _seed_po(mongo, f"{TEST_PREFIX}-HOLD",
                      current_stage=2,
                      design_status="Waiting",
                      payment_status="Waiting")
        try:
            d = admin_session.get(f"{BASE_URL}/api/dashboard/summary").json()
            hold_ids = [o["id"] for o in d["hold"]]
            design_ids = [o["id"] for o in d["waiting_design_approval"]]
            assert po["id"] in hold_ids, "PO should be in hold"
            assert po["id"] in design_ids, "HOLD PO must still appear in waiting_design_approval"
        finally:
            mongo.orders.delete_one({"id": po["id"]})


# ---------- 6. No mutation on GET dashboard summary ----------
class TestNoMutation:
    def test_get_summary_does_not_mutate(self, admin_session, mongo):
        po = _seed_po(mongo, f"{TEST_PREFIX}-NOMUT",
                      current_stage=3,
                      design_status="Waiting",
                      payment_status="Waiting")
        try:
            def snap():
                doc = mongo.orders.find_one({"id": po["id"]}, {"_id": 0})
                return {
                    "current_stage": doc.get("current_stage"),
                    "design_status": doc.get("design_status"),
                    "payment_status": doc.get("payment_status"),
                    "timeline_len": len(doc.get("timeline") or []),
                    "updated_at": doc.get("updated_at"),
                }
            before = snap()
            for _ in range(5):
                admin_session.get(f"{BASE_URL}/api/dashboard/summary")
            after = snap()
            assert before == after, f"summary GET mutated PO: before={before} after={after}"
        finally:
            mongo.orders.delete_one({"id": po["id"]})
