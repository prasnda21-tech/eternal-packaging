"""Iteration 29: SJ / Loading / Terkirim evidence via POST /loading-confirm.

Upload bukti kirim does not mark delivery. Confirm/mark-delivered requires
canonical Loading for the selected Surat Jalan.
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

TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xd9"
)
DEPRECATED_LOADING_HINT = "Gunakan Konfirmasi Loading"


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return s


@pytest.fixture()
def cleanup(db):
    ids = []
    yield ids
    if ids:
        db.orders.delete_many({"id": {"$in": ids}})
    db.orders.delete_many({"id": {"$regex": "^TEST-T29-"}})


def _sid(oid):
    return f"shp-{oid}"


def _confirm_loading(admin, oid, lines, surat_jalan_id=None):
    return admin.post(
        f"{BASE_URL}/api/orders/{oid}/loading-confirm",
        json={"surat_jalan_id": surat_jalan_id or _sid(oid), "lines": lines},
        timeout=15,
    )


def _mk_partial_po(db, *, packed_a=4000, packed_b=None, qty_a=10000, qty_b=5000, extra_ships=None):
    oid = f"TEST-T29-{uuid.uuid4().hex[:8]}"
    sid = _sid(oid)
    lines = [
        {"name": "Item A", "quantity": qty_a, "unit": "pcs", "production_process": "print", "item_code": "A"},
    ]
    packing_lines = [{"line_idx": 0, "packed_qty": packed_a}]
    packed_total = packed_a
    ship_lines = [{"line_idx": 0, "qty": packed_a, "name": "Item A"}]
    if packed_b is not None:
        lines.append(
            {"name": "Item B", "quantity": qty_b, "unit": "pcs", "production_process": "print", "item_code": "B"}
        )
        packing_lines.append({"line_idx": 1, "packed_qty": packed_b})
        packed_total += packed_b
        ship_lines.append({"line_idx": 1, "qty": packed_b, "name": "Item B"})
    ships = extra_ships or [{
        "id": sid,
        "number": f"SJ-{oid}",
        "source": "manual",
        "created_at": "2026-01-02T00:00:00+00:00",
        "created_by": ADMIN_EMAIL,
        "lines": ship_lines,
        "total_qty": packed_total,
    }]
    doc = {
        "id": oid,
        "po_number": oid,
        "customer": "TEST T29",
        "line_items": lines,
        "total_quantity": qty_a + (qty_b if packed_b is not None else 0),
        "current_stage": 13,
        "cancelled": False,
        "priority": "normal",
        "design_status": "Approved",
        "payment_status": "Approved",
        "sample_approved": True,
        "packing_info": {"lines": packing_lines, "qty_packed": packed_total},
        "qty_packed": packed_total,
        "helper_prep": {"qty_prepared": packed_total},
        "operator_splits": [
            {"line_idx": i, "quantity": row["packed_qty"], "qty_completed": row["packed_qty"]}
            for i, row in enumerate(packing_lines)
        ],
        "delivery_note_number": ships[0]["number"],
        "delivery_note_created_by": ADMIN_EMAIL,
        "delivery_note_created_by_name": "Admin",
        "delivery_note_created_at": "2026-01-02T00:00:00+00:00",
        "surat_jalan_created": True,
        "shipments": ships,
        "surat_jalan_files": [{
            "id": sh["id"],
            "shipment_id": sh["id"],
            "sj_number": sh["number"],
            "number": sh["number"],
            "name": f"{sh['number']}.pdf",
            "lines": sh.get("lines") or [],
        } for sh in ships],
        "loading_info": {"lines": []},
        "loading_confirmations": [],
        "delivery_proofs": [],
        "timeline": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    db.orders.insert_one(doc)
    return oid


def _order(admin, oid):
    r = admin.get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    return body.get("order") or body


def test_deprecated_loading_stays_closed(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    r = admin.post(
        f"{BASE_URL}/api/orders/{oid}/loading",
        json={"lines": [{"line_idx": 0, "qty": 2500}]},
        timeout=15,
    )
    assert r.status_code == 400, r.text
    assert DEPRECATED_LOADING_HINT in r.text


def test_loading_save_auto_stage_partial(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    r = _confirm_loading(admin, oid, [{"line_idx": 0, "qty": 2500}])
    assert r.status_code == 200, r.text
    o = r.json()["order"]
    assert o["current_stage"] == 13
    assert o["loading_qty_status"] == "partial"
    assert o.get("loading_completed") is False
    assert o.get("delivery_status") in (None, "Belum", "Belum Terkirim", "")
    assert o.get("terkirim_confirmed") is False
    rows = o.get("shipment_lines") or []
    loaded = next(x for x in rows if int(x["line_idx"]) == 0)
    assert loaded["loaded"] == 2500
    assert loaded["available_loading"] == 1500


def test_second_loading_completes_eligible_not_po(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    first = _confirm_loading(admin, oid, [{"line_idx": 0, "qty": 2500}])
    assert first.status_code == 200, first.text
    r = _confirm_loading(admin, oid, [{"line_idx": 0, "qty": 1500}])
    assert r.status_code == 200, r.text
    o = r.json()["order"]
    loaded = next(x for x in o["shipment_lines"] if int(x["line_idx"]) == 0)
    assert loaded["loaded"] == 4000
    assert loaded["available_loading"] == 0
    assert o["loading_qty_status"] == "completed"
    assert o["current_stage"] == 13
    assert o.get("terkirim_confirmed") is False


def test_multi_item_overall_not_fully_loaded(db, admin, cleanup):
    oid = _mk_partial_po(db, packed_a=4000, packed_b=2000)
    cleanup.append(oid)
    first = _confirm_loading(admin, oid, [{"line_idx": 0, "qty": 4000}])
    assert first.status_code == 200, first.text
    r = _confirm_loading(admin, oid, [{"line_idx": 1, "qty": 500}])
    assert r.status_code == 200, r.text
    o = r.json()["order"]
    by = {int(x["line_idx"]): x for x in o["shipment_lines"]}
    assert by[0]["loaded"] == 4000
    assert by[1]["loaded"] == 500
    assert o["loading_qty_status"] == "partial"
    assert o.get("loading_completed") is False
    assert o["current_stage"] == 13


def test_proof_upload_does_not_mark_delivery(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    loaded = _confirm_loading(admin, oid, [{"line_idx": 0, "qty": 4000}])
    assert loaded.status_code == 200, loaded.text
    before = _order(admin, oid)
    r = admin.post(
        f"{BASE_URL}/api/orders/{oid}/delivery-proof",
        files={"file": ("proof.jpg", TINY_JPEG, "image/jpeg")},
        data={"note": "sj1", "shipment_id": _sid(oid)},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    assert "EMERGENT_LLM_KEY" not in (r.text or "")
    o = r.json()["order"]
    assert int(o.get("qty_delivered") or 0) == int(before.get("qty_delivered") or 0)
    assert o.get("delivery_status") in (None, "Belum", "Belum Terkirim", "", before.get("delivery_status"))
    assert o.get("terkirim_confirmed") is False
    assert o["current_stage"] == 13
    proof = r.json()["proof"]
    assert proof.get("shipment_id") == _sid(oid)
    fr = admin.get(f"{BASE_URL}/api/files/{proof['storage_path']}", timeout=15)
    assert fr.status_code == 200, fr.text
    assert len(fr.content) > 0


def test_failed_proof_does_not_mark_delivered(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    loaded = _confirm_loading(admin, oid, [{"line_idx": 0, "qty": 4000}])
    assert loaded.status_code == 200, loaded.text
    before = _order(admin, oid)
    r = admin.post(
        f"{BASE_URL}/api/orders/{oid}/delivery-proof",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
        timeout=15,
    )
    assert r.status_code == 400
    after = _order(admin, oid)
    assert after.get("qty_delivered") == before.get("qty_delivered")
    assert after.get("delivery_status") == before.get("delivery_status")
    assert (after.get("delivery_proofs") or []) == (before.get("delivery_proofs") or [])


def test_reload_keeps_loading_evidence(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    r = _confirm_loading(admin, oid, [{"line_idx": 0, "qty": 2500}])
    assert r.status_code == 200, r.text
    o = _order(admin, oid)
    assert o["loading_qty_status"] == "partial"
    assert o["current_stage"] == 13
    loaded = next(x for x in o["shipment_lines"] if int(x["line_idx"]) == 0)
    assert loaded["loaded"] == 2500


def test_photo_without_loading_rejected(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    r = admin.post(
        f"{BASE_URL}/api/orders/{oid}/delivery-proof",
        files={"file": ("proof.jpg", TINY_JPEG, "image/jpeg")},
        data={"note": "no-load", "shipment_id": _sid(oid)},
        timeout=15,
    )
    assert r.status_code == 400, r.text
    assert "Loading" in r.text


def test_confirm_delivered_photos_without_loading_rejected(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    sid = _sid(oid)
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
    r = admin.post(
        f"{BASE_URL}/api/orders/{oid}/confirm-delivered",
        json={"shipment_id": sid, "recipient_name": "Budi Penerima"},
        timeout=15,
    )
    assert r.status_code == 400, r.text
    assert MSG_DELIVERY_LOADING_INCOMPLETE in r.text or "Loading" in r.text


def test_partial_loading_allows_only_partial_delivery(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    r_load = _confirm_loading(admin, oid, [{"line_idx": 0, "qty": 2500}])
    assert r_load.status_code == 200, r_load.text
    r_over = admin.post(
        f"{BASE_URL}/api/orders/{oid}/delivered",
        params={"qty_delivered": 4000},
        timeout=15,
    )
    assert r_over.status_code == 400, r_over.text
    detail = r_over.json().get("detail")
    blob = detail if isinstance(detail, str) else " ".join(
        str(x) for x in ((detail or {}).get("prereq_reasons") or [detail])
    )
    assert MSG_DELIVERY_LOADING_INCOMPLETE in str(blob)
    r_ok = admin.post(
        f"{BASE_URL}/api/orders/{oid}/delivered",
        params={"qty_delivered": 2500},
        timeout=15,
    )
    assert r_ok.status_code == 200, r_ok.text
    o = r_ok.json()["order"]
    assert o["qty_delivered"] == 2500
    assert o["delivery_status"] == "Sebagian Terkirim"
    assert o.get("terkirim_confirmed") is False


def test_loading_sj_a_not_usable_for_sj_b(db, admin, cleanup):
    token = uuid.uuid4().hex[:8]
    sja, sjb = f"sja-{token}", f"sjb-{token}"
    ships = [
        {"id": sja, "number": f"SJ-A-{token}", "source": "manual",
         "lines": [{"line_idx": 0, "qty": 4000, "name": "Item A"}], "total_qty": 4000},
        {"id": sjb, "number": f"SJ-B-{token}", "source": "manual",
         "lines": [{"line_idx": 0, "qty": 4000, "name": "Item A"}], "total_qty": 4000},
    ]
    oid = _mk_partial_po(db, packed_a=8000, extra_ships=ships)
    cleanup.append(oid)
    r_load = _confirm_loading(admin, oid, [{"line_idx": 0, "qty": 4000}], surat_jalan_id=sja)
    assert r_load.status_code == 200, r_load.text
    db.orders.update_one({"id": oid}, {"$set": {
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
    r = admin.post(
        f"{BASE_URL}/api/orders/{oid}/confirm-delivered",
        json={"shipment_id": sjb, "recipient_name": "Budi Penerima"},
        timeout=15,
    )
    assert r.status_code == 400, r.text
    assert MSG_DELIVERY_LOADING_INCOMPLETE in r.text or "Qty Loading" in r.text or "Loading" in r.text
    after = _order(admin, oid)
    assert int(after.get("qty_delivered") or 0) == 0
