"""
Iteration 28 — Loading (stage 13) → Terkirim (stage 14), aligned to production.

Canonical write: POST /orders/{id}/loading-confirm (surat_jalan_id + lines).
Deprecated POST /loading stays 400 and points to Konfirmasi Loading.
"""
import os
import uuid

import pytest
import requests
from pymongo import MongoClient

from services.delivery_loading_gate import MSG_DELIVERY_LOADING_INCOMPLETE

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

PASS = "Eternal123!"
ADMIN_EMAIL = "admin@eternalpackaging.com"
OPERATOR_EMAIL = "operator@eternalpackaging.com"
DRIVER_EMAIL = "driver@eternalpackaging.com"
DEPRECATED_LOADING_HINT = "Gunakan Konfirmasi Loading"
HIST_DELIVERED_AT = "2026-01-04T00:00:00+00:00"


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


def _sj_id(oid):
    return f"sj-{oid}"


def _assert_loading_incomplete(resp):
    assert resp.status_code == 400, resp.text
    body = resp.json()
    detail = body.get("detail", body)
    if isinstance(detail, dict):
        reasons = detail.get("prereq_reasons") or []
        blob = " ".join(str(x) for x in reasons) + " " + str(detail.get("message") or "")
    else:
        blob = str(detail)
    assert MSG_DELIVERY_LOADING_INCOMPLETE in blob, blob
    assert "Loading belum lengkap" not in blob or MSG_DELIVERY_LOADING_INCOMPLETE in blob
    assert "Checklist Loading" not in blob


def _confirm_loading(sess, oid, qty, line_idx=0, surat_jalan_id=None):
    sid = surat_jalan_id or _sj_id(oid)
    return sess.post(
        f"{BASE_URL}/api/orders/{oid}/loading-confirm",
        json={"surat_jalan_id": sid, "lines": [{"line_idx": line_idx, "qty": qty}]},
        timeout=15,
    )


def _mk_order(db, current_stage=13, with_sj=True, loading_complete=False,
              packing=True, delivered=False, qty=100, extra_shipments=None,
              loading_qty=None):
    """Synthetic PO with live SJ file + shipment identity for loading-confirm."""
    oid = f"TEST-T28-{uuid.uuid4().hex[:8]}"
    sid = _sj_id(oid)
    sj_number = f"SJ/{oid}"
    packed = qty if packing else 0
    doc = {
        "id": oid,
        "customer_name": f"TEST_T28 {oid}",
        "customer": f"TEST_T28 {oid}",
        "po_number": oid,
        "product_name": "Kardus Test T28",
        "quantity": qty,
        "total_quantity": qty,
        "price_per_unit": 1000,
        "total_price": qty * 1000,
        "current_stage": current_stage,
        "priority": "normal",
        "design_status": "Approved",
        "design_approved_by": ADMIN_EMAIL,
        "design_approved_at": "2026-01-01T00:00:00+00:00",
        "design_files": [{"name": "d.png", "url": "http://x"}],
        "payment_status": "Approved",
        "payment_approved_by": ADMIN_EMAIL,
        "payment_approved_at": "2026-01-01T00:00:00+00:00",
        "payment_proofs": [{"name": "p.png", "url": "http://x"}],
        "sample_files": [{"name": "s.png", "url": "http://x"}],
        "sample_approved": True,
        "sample_approved_by": ADMIN_EMAIL,
        "sample_approved_at": "2026-01-01T00:00:00+00:00",
        "line_items": [{
            "name": "Kardus Test T28", "quantity": qty, "unit": "pcs",
            "production_process": "print",
        }],
        "qty_screened": qty,
        "qty_prepared": qty,
        "qty_printed": qty,
        "qty_packed": packed,
        "packing_status": "Sudah" if packing else "Belum",
        "packing_info": {
            "qty_packed": packed,
            "lines": [{"line_idx": 0, "packed_qty": packed}] if packing else [],
        },
        "operator_splits": [
            {"id": str(uuid.uuid4()), "operator_email": OPERATOR_EMAIL,
             "operator_name": "Operator T28", "qty": qty, "qty_completed": qty,
             "line_idx": 0, "quantity": qty,
             "stage": 11, "updated_at": "2026-01-01T00:00:00+00:00"}
        ],
        "loading_confirmations": [],
        "loading_info": {"lines": []},
        "delivery_proofs": [],
        "timeline": [],
        "cancelled": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    if with_sj:
        ships = [{
            "id": sid,
            "number": sj_number,
            "source": "manual",
            "created_at": "2026-01-02T00:00:00+00:00",
            "created_by": ADMIN_EMAIL,
            "lines": [{"line_idx": 0, "qty": qty, "name": "Kardus Test T28"}],
            "total_qty": qty,
        }]
        if extra_shipments:
            ships.extend(extra_shipments)
        doc["delivery_note_number"] = sj_number
        doc["delivery_note_created_by"] = ADMIN_EMAIL
        doc["delivery_note_created_by_name"] = "Admin"
        doc["delivery_note_created_at"] = "2026-01-02T00:00:00+00:00"
        doc["surat_jalan_created"] = True
        doc["shipments"] = ships
        doc["surat_jalan_files"] = [{
            "id": sh["id"],
            "shipment_id": sh["id"],
            "sj_number": sh["number"],
            "number": sh["number"],
            "name": f"{sh['number']}.pdf",
            "lines": sh.get("lines") or [],
        } for sh in ships]
    load_qty = loading_qty if loading_qty is not None else (qty if loading_complete else None)
    if load_qty:
        doc["loading_confirmations"] = [{
            "id": f"lc-{oid}",
            "surat_jalan_id": sid,
            "shipment_id": sid,
            "sj_number": sj_number,
            "lines": [{"line_idx": 0, "qty": load_qty, "name": "Kardus Test T28"}],
        }]
    if loading_complete:
        # Historical checklist leftover — not used as Loading evidence by the gate.
        doc["loading_checklist"] = {
            "vehicle_plate": "B 1 TEST",
            "driver_name": "Sopir T28",
            "items_loaded": True,
            "seal_ok": True,
            "notes": "seed leftover checklist",
            "updated_by": ADMIN_EMAIL,
            "updated_at": "2026-01-03T00:00:00+00:00",
        }
    if delivered:
        doc["delivered_at"] = HIST_DELIVERED_AT
        doc["delivered_by"] = ADMIN_EMAIL
        doc["delivered_by_name"] = "Admin"
        doc["delivery_status"] = "Terkirim"
        doc["qty_delivered"] = qty
        doc["current_stage"] = 14
        doc["terkirim_confirmed"] = True
        for sh in doc.get("shipments") or []:
            sh["delivery_status"] = "Terkirim"
            sh["delivered_at"] = HIST_DELIVERED_AT
    db.orders.insert_one(doc)
    return oid


@pytest.fixture()
def cleanup(db):
    ids = []
    yield ids
    if ids:
        db.orders.delete_many({"id": {"$in": ids}})
    db.orders.delete_many({"id": {"$regex": "^TEST-T28-"}})


def test_1_deprecated_loading_stays_400_and_canonical_confirm_is_perm_based(
    db, admin_tok, op_tok, cleanup,
):
    oid = _mk_order(db, current_stage=13, with_sj=True)
    cleanup.append(oid)
    payload = {"vehicle_plate": "B 1", "driver_name": "S", "items_loaded": False, "seal_ok": False}
    r_op = op_tok.post(f"{BASE_URL}/api/orders/{oid}/loading", json=payload)
    assert r_op.status_code == 403, r_op.text
    assert "loading.update" in r_op.text

    r_ad = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/loading", json=payload)
    assert r_ad.status_code == 400, r_ad.text
    assert DEPRECATED_LOADING_HINT in r_ad.text

    confirm_payload = {"surat_jalan_id": _sj_id(oid), "lines": [{"line_idx": 0, "qty": 10}]}
    r_op_c = op_tok.post(f"{BASE_URL}/api/orders/{oid}/loading-confirm", json=confirm_payload)
    assert r_op_c.status_code == 403, r_op_c.text
    assert "loading.update" in r_op_c.text

    r_ad_c = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/loading-confirm", json=confirm_payload)
    assert r_ad_c.status_code == 200, r_ad_c.text
    order = r_ad_c.json()["order"]
    assert order["current_stage"] == 13
    assert order.get("delivery_status") in (None, "Belum", "Belum Terkirim", "")


def test_2_loading_confirm_partial_no_auto_advance(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True)
    cleanup.append(oid)
    r = _confirm_loading(admin_tok, oid, 40)
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["current_stage"] == 13
    assert order.get("loading_completed") is False
    assert order.get("loading_qty_status") == "partial"
    assert order.get("terkirim_confirmed") is False
    assert not order.get("delivered_at")


def test_3_loading_confirm_complete_actor_and_timeline(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True)
    cleanup.append(oid)
    r = _confirm_loading(admin_tok, oid, 100)
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order.get("loading_completed") is True
    assert order.get("loading_qty_status") == "completed"
    recs = order.get("loading_confirmations") or []
    assert recs, recs
    assert recs[-1].get("confirmed_by") == ADMIN_EMAIL
    assert recs[-1].get("confirmed_at")
    tl = order.get("timeline", [])
    stage13 = [t for t in tl if t.get("stage") == 13 and "Konfirmasi Loading" in (t.get("note") or "")]
    assert stage13, f"missing loading-confirm timeline entry: {tl}"
    assert order["current_stage"] == 13
    assert order.get("terkirim_confirmed") is False


def test_4_deprecated_loading_always_400_even_without_sj(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=11, with_sj=False)
    cleanup.append(oid)
    payload = {"vehicle_plate": "X", "items_loaded": True, "seal_ok": True}
    r = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/loading", json=payload)
    assert r.status_code == 400
    assert DEPRECATED_LOADING_HINT in r.text

    r_c = admin_tok.post(
        f"{BASE_URL}/api/orders/{oid}/loading-confirm",
        json={"surat_jalan_id": "missing", "lines": [{"line_idx": 0, "qty": 1}]},
        timeout=15,
    )
    assert r_c.status_code == 400, r_c.text
    assert "Surat Jalan" in r_c.text


def test_5_actor_sj_to_loading_timeline(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=12, with_sj=True)
    cleanup.append(oid)
    r = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/advance", json={"note": ""})
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["current_stage"] == 13
    tl = order.get("timeline", [])
    match = [t for t in tl if t.get("stage") == 12 and "Surat Jalan selesai" in (t.get("note") or "")]
    assert match, f"missing advance-12 timeline entry: {tl}"
    auto13 = [t for t in tl if t.get("stage") == 13 and "Loading selesai" in (t.get("note") or "")]
    assert not auto13, f"unexpected auto Loading-selesai entry: {auto13}"


def test_6_advance_terkirim_gate(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True, loading_complete=False)
    cleanup.append(oid)
    r_block = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/advance", json={"note": ""})
    _assert_loading_incomplete(r_block)

    r_ok = _confirm_loading(admin_tok, oid, 100)
    assert r_ok.status_code == 200, r_ok.text
    r_adv = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/advance", json={"note": ""})
    assert r_adv.status_code == 200, r_adv.text
    assert r_adv.json()["order"]["current_stage"] == 14


def test_7_delivered_gate_same_validator(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True, loading_complete=False)
    cleanup.append(oid)
    r_block = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    _assert_loading_incomplete(r_block)

    r_load = _confirm_loading(admin_tok, oid, 100)
    assert r_load.status_code == 200, r_load.text
    r_ok = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    assert r_ok.status_code == 200, r_ok.text
    o = r_ok.json()["order"]
    assert o["current_stage"] == 14
    assert o["delivery_status"] == "Terkirim"
    assert o["delivered_by"] == ADMIN_EMAIL
    assert o["delivered_at"]
    assert o["terkirim_confirmed"] is True


def test_8_delivered_perm_operator_403(db, admin_tok, op_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True, loading_complete=True)
    cleanup.append(oid)
    r_op = op_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    assert r_op.status_code == 403
    assert "delivery.update" in r_op.text


def test_9_driver_needs_delivery_proofs(db, driver_tok, cleanup):
    if not driver_tok:
        pytest.skip("driver account not available")
    oid = _mk_order(db, current_stage=13, with_sj=True, loading_complete=True)
    cleanup.append(oid)
    r = driver_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    assert r.status_code in (400, 403), r.text
    if r.status_code == 400:
        low = r.text.lower()
        assert (
            "foto bukti" in low
            or "delivery_proofs" in low
            or "bukti kirim" in low
            or "tanda tangan" in low
        ), r.text


def test_10_terkirim_confirmed_derived(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True, loading_complete=True)
    cleanup.append(oid)
    r = admin_tok.get(f"{BASE_URL}/api/orders/{oid}")
    assert r.status_code == 200
    o = r.json()
    if "order" in o:
        o = o["order"]
    assert o.get("terkirim_confirmed") is False
    assert not o.get("delivered_at")

    r2 = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    assert r2.status_code == 200, r2.text
    r3 = admin_tok.get(f"{BASE_URL}/api/orders/{oid}")
    o3 = r3.json()
    if "order" in o3:
        o3 = o3["order"]
    assert o3["terkirim_confirmed"] is True
    assert o3["delivered_at"]
    assert o3["delivered_by"] == ADMIN_EMAIL


def test_11_existing_delivered_intact(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=14, with_sj=True, loading_complete=True, delivered=True)
    cleanup.append(oid)
    r = admin_tok.get(f"{BASE_URL}/api/orders/{oid}")
    o = r.json()
    if "order" in o:
        o = o["order"]
    assert o["current_stage"] == 14
    assert o["terkirim_confirmed"] is True
    assert o["loading_completed"] is True
    assert o["delivered_at"] == HIST_DELIVERED_AT

    r2 = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    assert r2.status_code == 200, r2.text
    after = r2.json()["order"]
    assert after["delivered_at"] == HIST_DELIVERED_AT
    assert after["qty_delivered"] == 100
    assert after["delivery_status"] == "Terkirim"


def test_11b_historical_delivered_without_loading_does_not_rewrite(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=14, with_sj=True, loading_complete=False, delivered=True)
    cleanup.append(oid)
    r = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered")
    assert r.status_code == 200, r.text
    o = r.json()["order"]
    assert o["delivered_at"] == HIST_DELIVERED_AT
    assert o["qty_delivered"] == 100
    assert o["delivery_status"] == "Terkirim"
    assert o["current_stage"] == 14
    assert not (o.get("loading_confirmations") or [])
    assert not o.get("loading_qty_status") in ("partial", "completed") or not (o.get("loading_confirmations") or [])


def test_12_regression_advance_12_needs_sj(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=12, with_sj=False)
    cleanup.append(oid)
    r = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/advance", json={"note": ""})
    assert r.status_code == 400
    detail = r.json().get("detail")
    assert isinstance(detail, dict)
    assert any("Surat Jalan" in x for x in detail.get("prereq_reasons", []))


def test_13_dashboard_15_stages_no_driver(admin_tok):
    r = admin_tok.get(f"{BASE_URL}/api/dashboard/summary")
    assert r.status_code == 200
    data = r.json()
    sb = data.get("stage_breakdown") or []
    assert len(sb) == 15, f"expected 15 stages, got {len(sb)}"
    labels = [s.get("label") for s in sb]
    assert "Driver" not in labels, labels
    assert "Pengiriman" in labels


def test_14_partial_loading_caps_delivered_qty(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True)
    cleanup.append(oid)
    r_load = _confirm_loading(admin_tok, oid, 40)
    assert r_load.status_code == 200, r_load.text
    r_over = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered", params={"qty_delivered": 100})
    _assert_loading_incomplete(r_over)
    r_ok = admin_tok.post(f"{BASE_URL}/api/orders/{oid}/delivered", params={"qty_delivered": 40})
    assert r_ok.status_code == 200, r_ok.text
    o = r_ok.json()["order"]
    assert o["qty_delivered"] == 40
    assert o["delivery_status"] == "Sebagian Terkirim"
    assert o.get("terkirim_confirmed") is False


def test_15_loading_sj_a_cannot_cover_sj_b(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True, qty=100)
    cleanup.append(oid)
    sja = _sj_id(oid)
    sjb = f"sjb-{oid}"
    db.orders.update_one({"id": oid}, {"$set": {
        "shipments": [
            {
                "id": sja, "number": f"SJ-A/{oid}", "source": "manual",
                "lines": [{"line_idx": 0, "qty": 40, "name": "Kardus Test T28"}],
                "total_qty": 40,
            },
            {
                "id": sjb, "number": f"SJ-B/{oid}", "source": "manual",
                "lines": [{"line_idx": 0, "qty": 60, "name": "Kardus Test T28"}],
                "total_qty": 60,
            },
        ],
        "surat_jalan_files": [
            {
                "id": sja, "shipment_id": sja, "sj_number": f"SJ-A/{oid}",
                "number": f"SJ-A/{oid}", "name": "SJ-A.pdf",
                "lines": [{"line_idx": 0, "qty": 40}],
            },
            {
                "id": sjb, "shipment_id": sjb, "sj_number": f"SJ-B/{oid}",
                "number": f"SJ-B/{oid}", "name": "SJ-B.pdf",
                "lines": [{"line_idx": 0, "qty": 60}],
            },
        ],
        "loading_confirmations": [{
            "id": f"lc-{oid}",
            "surat_jalan_id": sja,
            "shipment_id": sja,
            "sj_number": f"SJ-A/{oid}",
            "lines": [{"line_idx": 0, "qty": 40, "name": "Kardus Test T28"}],
        }],
        "delivery_proofs": [
            {"id": f"p1-{oid}", "shipment_id": sjb, "storage_path": "p1.jpg"},
            {"id": f"p2-{oid}", "shipment_id": sjb, "storage_path": "p2.jpg"},
        ],
        "delivery_recipient_signature": {
            "id": f"sig-{oid}", "shipment_id": sjb, "recipient_name": "Budi Penerima",
            "signature_storage_path": "sig.png", "signature_file_id": f"sf-{oid}",
            "frozen": False,
        },
        "delivery_driver_signature": {
            "id": f"dsig-{oid}", "shipment_id": sjb,
            "driver_signature_storage_path": "dsig.png",
            "signature_file_id": f"dsf-{oid}", "frozen": False,
        },
    }})
    r = admin_tok.post(
        f"{BASE_URL}/api/orders/{oid}/confirm-delivered",
        json={"shipment_id": sjb, "recipient_name": "Budi Penerima"},
        timeout=15,
    )
    assert r.status_code == 400, r.text
    assert MSG_DELIVERY_LOADING_INCOMPLETE in r.text or "Qty Loading" in r.text, r.text
    after = db.orders.find_one({"id": oid}, {"_id": 0})
    assert int(after.get("qty_delivered") or 0) == 0


def test_16_confirm_delivered_without_loading_rejected(db, admin_tok, cleanup):
    oid = _mk_order(db, current_stage=13, with_sj=True)
    cleanup.append(oid)
    sid = _sj_id(oid)
    db.orders.update_one({"id": oid}, {"$set": {
        "delivery_proofs": [
            {"id": f"p1-{oid}", "shipment_id": sid, "storage_path": "p1.jpg"},
            {"id": f"p2-{oid}", "shipment_id": sid, "storage_path": "p2.jpg"},
        ],
        "delivery_recipient_signature": {
            "id": f"sig-{oid}", "shipment_id": sid, "recipient_name": "Budi Penerima",
            "signature_storage_path": "sig.png", "signature_file_id": f"sf-{oid}",
            "frozen": False,
        },
        "delivery_driver_signature": {
            "id": f"dsig-{oid}", "shipment_id": sid,
            "driver_signature_storage_path": "dsig.png",
            "signature_file_id": f"dsf-{oid}", "frozen": False,
        },
    }})
    r = admin_tok.post(
        f"{BASE_URL}/api/orders/{oid}/confirm-delivered",
        json={"shipment_id": sid, "recipient_name": "Budi Penerima"},
        timeout=15,
    )
    assert r.status_code == 400, r.text
    assert MSG_DELIVERY_LOADING_INCOMPLETE in r.text or "Loading" in r.text
    if "foto" not in r.text.lower():
        assert MSG_DELIVERY_LOADING_INCOMPLETE in r.text or "Qty Loading" in r.text
