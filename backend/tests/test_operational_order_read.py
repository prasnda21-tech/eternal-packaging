"""view=operational reads slim file bytes without changing the response contract."""
from __future__ import annotations

import base64
import copy
import json
import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("LOCAL_STORAGE_DIR", "/tmp/pwa-stage3-open-storage")
os.environ.setdefault("DB_NAME", "pwa_stage3_order_read")

from services.delivery_schedule import attach_schedule_snapshot  # noqa: E402
from services.operational_order_view import (  # noqa: E402
    operational_order_pipeline,
    operational_public_order,
)

pytestmark = pytest.mark.anyio
DB_NAME = "pwa_stage3_order_read"
PDF_BYTES = b"%PDF-1.4\nstage3"
PDF_URL = "data:application/pdf;base64," + base64.b64encode(PDF_BYTES).decode("ascii")
LARGE = "data:application/pdf;base64," + ("Z" * 40000)


def _order():
    return {
        "id": "po-open",
        "po_number": "PO-OPEN",
        "customer": "Cust",
        "customer_name": "Cust",
        "cancelled": False,
        "current_stage": 11,
        "delivery_status": "Belum Terkirim",
        "work_date": "2026-10-04",
        "deadline": "2026-10-10",
        "design_approved_at": "2026-10-01T00:00:00+00:00",
        "payment_approved_at": "2026-10-01T00:00:00+00:00",
        "line_items": [{"id": "L0", "name": "Cup", "quantity": 10, "production_process": "print"}],
        "operator_splits": [{
            "id": "s1",
            "line_idx": 0,
            "operator_email": "op@measure.local",
            "quantity": 10,
            "qty_completed": 4,
            "progress_entries": [{"qty": 4, "photo": LARGE, "data_url": LARGE}],
        }],
        "screener_info": {"screen_qty": 10},
        "helper_prep": {"qty_prepared": 10},
        "packing_info": {"qty_packed": 4, "lines": [{"line_idx": 0, "packed_qty": 4}]},
        "shipments": [{
            "id": "sh-1",
            "number": "SJ-1",
            "delivery_date": "2026-10-08",
            "lines": [{"line_idx": 0, "name": "Cup", "qty": 10}],
        }],
        "design_files": [
            {"id": "design-store", "name": "stored.pdf", "storage_path": "eternal-packaging/design/stored.pdf", "uploaded_at": "2026-10-01T00:00:00+00:00", "data_url": LARGE},
            {"id": "design-id", "name": "by-id.pdf", "content_type": "application/pdf", "uploaded_at": "2026-10-01T00:00:00+00:00", "data_url": PDF_URL},
            {"name": "legacy.pdf", "content_type": "application/pdf", "data_url": LARGE},
        ],
        "sample_files": [{"name": "sample.png", "data_url": LARGE}],
        "payment_files": [{"id": "pay-1", "name": "pay.pdf", "uploaded_at": "2026-10-01T00:00:00+00:00", "data_url": LARGE}],
        "surat_jalan_files": [
            {"id": "sj-store", "name": "sj-store.pdf", "storage_path": "eternal-packaging/sj/store.pdf", "shipment_id": "sh-1", "uploaded_at": "2026-10-03T00:00:00+00:00", "data_url": LARGE, "lines": [{"line_idx": 0, "qty": 10}]},
            {"name": "sj-legacy.pdf", "data_url": LARGE},
        ],
        "delivery_proofs": [
            {"id": "proof-store", "storage_path": "eternal-packaging/proof/p.png", "uploaded_at": "2026-10-03T00:00:00+00:00", "data_url": LARGE},
            {"id": "proof-id-only", "uploaded_at": "2026-10-03T00:00:00+00:00", "data_url": LARGE},
        ],
        "delivery_recipient_signature": {
            "id": "sig-r",
            "recipient_name": "Penerima",
            "signature_storage_path": "eternal-packaging/sig/r.png",
            "recipient_signed_at": "2026-10-04T00:00:00+00:00",
            "shipment_id": "sh-1",
        },
        "delivery_driver_signature": {
            "id": "sig-d",
            "signature_storage_path": "eternal-packaging/sig/d.png",
            "driver_signature_storage_path": "eternal-packaging/sig/d.png",
            "driver_signed_at": "2026-10-04T00:00:00+00:00",
            "shipment_id": "sh-1",
        },
    }


def _enrich(server, order):
    server._apply_hold_snapshot(order)
    order["packing_prereq_reasons"] = server._prereq_reasons_for_packing(order)
    order["packing_ready"] = len(order["packing_prereq_reasons"]) == 0
    order["packing_status_semantic"] = server._packing_status_semantic(order)
    order["surat_jalan_created"] = server._surat_jalan_created(order)
    order["loading_prereq_reasons"] = server._prereq_reasons_for_loading(order)
    order["loading_ready"] = len(order["loading_prereq_reasons"]) == 0
    order["loading_completed"] = server._loading_completed(order)
    order["terkirim_prereq_reasons"] = server._prereq_reasons_for_terkirim(order)
    order["terkirim_ready"] = len(order["terkirim_prereq_reasons"]) == 0
    order["terkirim_confirmed"] = server._terkirim_confirmed(order)
    server._attach_quantity_snapshot(order)
    server._attach_deadline_snapshot(order, 3)
    attach_schedule_snapshot(order)
    server._strip_tracking_secret(order)
    return operational_public_order(order)


def _by_name(files):
    return {item.get("name") or item.get("id"): item for item in files or []}


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_operational_aggregate_drops_opened_bytes_and_keeps_response():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[DB_NAME]
    await client.drop_database(DB_NAME)
    server.db = db
    stored = _order()
    await db.orders.insert_one(copy.deepcopy(stored))
    await db.users.insert_one({
        "id": "u-drv", "email": "drv@measure.local", "role": "driver", "name": "Driver", "status": "active",
    })
    query = {"id": "po-open"}
    try:
        aggregate_docs = await db.orders.aggregate(operational_order_pipeline(query)).to_list(1)
        assert len(aggregate_docs) == 1
        aggregate = aggregate_docs[0]
        full = await db.orders.find_one(query, {"_id": 0})
        aggregate_bytes = len(json.dumps(aggregate, default=str).encode())
        full_bytes = len(json.dumps(full, default=str).encode())
        assert aggregate_bytes < full_bytes
        assert "ZZZZ" not in json.dumps({
            "design_files": aggregate["design_files"][:2],
            "payment_files": aggregate["payment_files"],
            "surat_jalan_files": [aggregate["surat_jalan_files"][0]],
            "progress": aggregate["operator_splits"][0]["progress_entries"],
        })
        design = _by_name(aggregate["design_files"])
        assert "data_url" not in design["stored.pdf"]
        assert design["stored.pdf"]["storage_path"] == "eternal-packaging/design/stored.pdf"
        assert "data_url" not in design["by-id.pdf"]
        assert design["by-id.pdf"]["id"] == "design-id"
        assert design["legacy.pdf"]["data_url"] == LARGE
        assert aggregate["sample_files"][0]["data_url"] == LARGE
        sj = _by_name(aggregate["surat_jalan_files"])
        assert "data_url" not in sj["sj-store.pdf"]
        assert sj["sj-store.pdf"]["storage_path"] == "eternal-packaging/sj/store.pdf"
        assert sj["sj-legacy.pdf"]["data_url"] == LARGE
        proofs = {item["id"]: item for item in aggregate["delivery_proofs"]}
        assert proofs["proof-store"]["data_url"] == LARGE
        assert proofs["proof-id-only"]["data_url"] == LARGE
        assert aggregate["delivery_recipient_signature"]["signature_storage_path"] == "eternal-packaging/sig/r.png"
        assert aggregate["delivery_driver_signature"]["signature_storage_path"] == "eternal-packaging/sig/d.png"
        assert "photo" not in aggregate["operator_splits"][0]["progress_entries"][0]
        assert full["design_files"][0]["data_url"] == LARGE
        assert full["operator_splits"][0]["progress_entries"][0]["photo"] == LARGE

        app = server.app
        app.dependency_overrides[server.current_user] = lambda: {
            "id": "u-drv", "user_id": "u-drv", "email": "drv@measure.local", "name": "Driver", "role": "driver",
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            operational = await http.get("/api/orders/po-open", params={"view": "operational"})
            plain = await http.get("/api/orders/po-open")
            opened = await http.get("/api/orders/po-open/attachments/design/design-id")
        assert operational.status_code == 200
        assert plain.status_code == 200
        assert opened.status_code == 200
        assert opened.content == PDF_BYTES
        expected = json.loads(json.dumps(_enrich(server, copy.deepcopy(full)), default=str))
        assert operational.json()["order"] == expected
        assert plain.json()["order"]["line_items"][0]["quantity"] == 10
        assert plain.json()["order"]["id"] == "po-open"
        op_design = _by_name(operational.json()["order"]["design_files"])
        assert op_design["legacy.pdf"]["data_url"] == LARGE
        assert "data_url" not in op_design["by-id.pdf"]
        assert operational.json()["order"]["delivery_recipient_signature"]["signature_storage_path"].endswith("r.png")
        sizes = {
            "mongo_full_json_bytes": full_bytes,
            "mongo_aggregate_json_bytes": aggregate_bytes,
            "http_operational_bytes": len(operational.content),
            "http_plain_bytes": len(plain.content),
        }
        out = Path("/opt/cursor/artifacts")
        out.mkdir(parents=True, exist_ok=True)
        (out / "pwa_stage3_order_read.json").write_text(json.dumps(sizes, indent=2), encoding="utf-8")
    finally:
        app = server.app
        app.dependency_overrides.pop(server.current_user, None)
        await client.drop_database(DB_NAME)
        client.close()


def _data_url(payload: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


def _decode_data_url(value: str) -> bytes:
    return base64.b64decode(value.split(",", 1)[1])


async def test_operational_open_returns_bytes_for_projected_files():
    """Open paths for file categories the earlier fixture did not fetch."""
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    from services import storage as obj_storage

    pay_bytes = b"%PDF-1.4\npayment-id\n"
    sample_meta_bytes = b"%PDF-1.4\nsample-id-meta\n"
    legacy_design_bytes = b"%PDF-1.4\nlegacy-design\n"
    legacy_pay_bytes = b"%PDF-1.4\nlegacy-payment\n"
    legacy_sample_bytes = b"%PDF-1.4\nlegacy-sample\n"
    legacy_sj_bytes = b"%PDF-1.4\nlegacy-sj\n"
    sj_meta_bytes = b"%PDF-1.4\nsj-id-meta\n"
    sj_store_bytes = b"%PDF-1.4\nsj-storage\n"
    sample_store_bytes = b"sample-storage-stage3"
    proof_bytes = b"proof-storage-stage3"
    proof_legacy_bytes = b"proof-legacy-stage3"
    sig_recipient_bytes = b"sig-recipient-stage3"
    sig_driver_bytes = b"sig-driver-stage3"
    uploaded = "2026-10-01T00:00:00+00:00"

    sample_path = obj_storage.put_object(
        "eternal-packaging/stage3-open/sample.bin", sample_store_bytes, "application/octet-stream",
    )["path"]
    sj_path = obj_storage.put_object(
        "eternal-packaging/stage3-open/sj.pdf", sj_store_bytes, "application/pdf",
    )["path"]
    proof_path = obj_storage.put_object(
        "eternal-packaging/stage3-open/proof.bin", proof_bytes, "application/octet-stream",
    )["path"]
    sig_recipient_path = obj_storage.put_object(
        "eternal-packaging/stage3-open/recipient.bin", sig_recipient_bytes, "application/octet-stream",
    )["path"]
    sig_driver_path = obj_storage.put_object(
        "eternal-packaging/stage3-open/driver.bin", sig_driver_bytes, "application/octet-stream",
    )["path"]
    stored_paths = [sample_path, sj_path, proof_path, sig_recipient_path, sig_driver_path]

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db_name = "pwa_stage3_open_paths"
    db = client[db_name]
    await client.drop_database(db_name)
    server.db = db
    stored = _order()
    stored["design_files"] = [{
        "name": "legacy.pdf",
        "content_type": "application/pdf",
        "data_url": _data_url(legacy_design_bytes, "application/pdf"),
    }]
    stored["payment_files"] = [
        {
            "id": "pay-1",
            "name": "pay.pdf",
            "content_type": "application/pdf",
            "uploaded_at": uploaded,
            "data_url": _data_url(pay_bytes, "application/pdf"),
        },
        {
            "name": "pay-legacy.pdf",
            "content_type": "application/pdf",
            "data_url": _data_url(legacy_pay_bytes, "application/pdf"),
        },
    ]
    stored["sample_files"] = [
        {
            "id": "sample-store",
            "name": "sample-store.bin",
            "storage_path": sample_path,
            "uploaded_at": uploaded,
            "data_url": LARGE,
        },
        {
            "id": "sample-meta",
            "name": "sample-meta.pdf",
            "content_type": "application/pdf",
            "uploaded_at": uploaded,
            "data_url": _data_url(sample_meta_bytes, "application/pdf"),
        },
        {
            "name": "sample-legacy.pdf",
            "content_type": "application/pdf",
            "data_url": _data_url(legacy_sample_bytes, "application/pdf"),
        },
    ]
    stored["surat_jalan_files"] = [
        {
            "id": "sj-meta",
            "name": "sj-meta.pdf",
            "content_type": "application/pdf",
            "uploaded_at": uploaded,
            "shipment_id": "sh-1",
            "data_url": _data_url(sj_meta_bytes, "application/pdf"),
            "lines": [{"line_idx": 0, "qty": 10}],
        },
        {
            "id": "sj-store",
            "name": "sj-store.pdf",
            "content_type": "application/pdf",
            "storage_path": sj_path,
            "uploaded_at": uploaded,
            "shipment_id": "sh-1",
            "data_url": LARGE,
            "lines": [{"line_idx": 0, "qty": 10}],
        },
        {
            "name": "sj-legacy.pdf",
            "content_type": "application/pdf",
            "data_url": _data_url(legacy_sj_bytes, "application/pdf"),
        },
    ]
    stored["delivery_proofs"] = [
        {
            "id": "proof-store",
            "storage_path": proof_path,
            "uploaded_at": uploaded,
            "shipment_id": "sh-1",
            "data_url": LARGE,
        },
        {
            "id": "proof-legacy",
            "uploaded_at": uploaded,
            "shipment_id": "sh-1",
            "data_url": _data_url(proof_legacy_bytes, "application/octet-stream"),
        },
    ]
    stored["delivery_recipient_signature"] = {
        "id": "sig-r",
        "recipient_name": "Penerima",
        "signature_storage_path": sig_recipient_path,
        "recipient_signed_at": uploaded,
        "shipment_id": "sh-1",
    }
    stored["delivery_driver_signature"] = {
        "id": "sig-d",
        "signature_storage_path": sig_driver_path,
        "driver_signature_storage_path": sig_driver_path,
        "driver_signed_at": uploaded,
        "shipment_id": "sh-1",
    }
    await db.orders.insert_one(copy.deepcopy(stored))
    for file_id, path, kind, content_type in (
        ("sample-store", sample_path, "sample", "application/octet-stream"),
        ("sj-store", sj_path, "surat_jalan", "application/pdf"),
        ("proof-store", proof_path, "delivery_proof", "application/octet-stream"),
        ("sig-r", sig_recipient_path, "signature", "application/octet-stream"),
        ("sig-d", sig_driver_path, "signature", "application/octet-stream"),
    ):
        await db.files.insert_one({
            "id": file_id,
            "storage_path": path,
            "content_type": content_type,
            "kind": kind,
            "order_id": "po-open",
            "is_deleted": False,
        })

    app = server.app
    app.dependency_overrides[server.current_user] = lambda: {
        "id": "admin-1", "user_id": "admin-1", "email": "admin@x.com", "name": "Admin", "role": "admin",
    }
    try:
        full = await db.orders.find_one({"id": "po-open"}, {"_id": 0})
        aggregate = (await db.orders.aggregate(operational_order_pipeline({"id": "po-open"})).to_list(1))[0]
        aggregate_payment = _by_name(aggregate["payment_files"])
        aggregate_sample = _by_name(aggregate["sample_files"])
        aggregate_sj = _by_name(aggregate["surat_jalan_files"])
        assert "data_url" not in aggregate_payment["pay.pdf"]
        assert aggregate_payment["pay-legacy.pdf"]["data_url"].endswith(
            base64.b64encode(legacy_pay_bytes).decode("ascii")
        )
        assert "data_url" not in aggregate_sample["sample-store.bin"]
        assert aggregate_sample["sample-store.bin"]["storage_path"] == sample_path
        assert _decode_data_url(aggregate_sample["sample-meta.pdf"]["data_url"]) == sample_meta_bytes
        assert _decode_data_url(aggregate_sample["sample-legacy.pdf"]["data_url"]) == legacy_sample_bytes
        assert "data_url" not in aggregate_sj["sj-meta.pdf"]
        assert "data_url" not in aggregate_sj["sj-store.pdf"]
        assert _decode_data_url(aggregate_sj["sj-legacy.pdf"]["data_url"]) == legacy_sj_bytes
        proofs = {item["id"]: item for item in aggregate["delivery_proofs"]}
        assert proofs["proof-store"]["data_url"] == LARGE
        assert _decode_data_url(proofs["proof-legacy"]["data_url"]) == proof_legacy_bytes

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            operational = await http.get("/api/orders/po-open", params={"view": "operational"})
            payment = await http.get("/api/orders/po-open/attachments/payment/pay-1")
            sample_open = await http.get(f"/api/files/{sample_path}")
            sample_kind = await http.get("/api/orders/po-open/attachments/sample/sample-meta")
            sj_meta = await http.get("/api/orders/po-open/surat-jalan/files/sj-meta")
            sj_store = await http.get("/api/orders/po-open/surat-jalan/files/sj-store")
            proof_open = await http.get(f"/api/files/{proof_path}")
            recipient_open = await http.get(f"/api/files/{sig_recipient_path}")
            driver_open = await http.get(f"/api/files/{sig_driver_path}")

        assert operational.status_code == 200, operational.text
        expected = json.loads(json.dumps(_enrich(server, copy.deepcopy(full)), default=str))
        assert operational.json()["order"] == expected

        assert payment.status_code == 200, payment.text
        assert payment.content == pay_bytes
        assert sample_open.status_code == 200, sample_open.text
        assert sample_open.content == sample_store_bytes
        assert sample_kind.status_code == 400
        assert sj_meta.status_code == 200, sj_meta.text
        assert sj_meta.content == sj_meta_bytes
        assert sj_store.status_code == 200, sj_store.text
        assert sj_store.content == sj_store_bytes
        assert proof_open.status_code == 200, proof_open.text
        assert proof_open.content == proof_bytes
        assert recipient_open.status_code == 200, recipient_open.text
        assert recipient_open.content == sig_recipient_bytes
        assert driver_open.status_code == 200, driver_open.text
        assert driver_open.content == sig_driver_bytes

        order = operational.json()["order"]
        payment_out = _by_name(order["payment_files"])
        sample_out = _by_name(order["sample_files"])
        sj_out = {item.get("name") or item.get("id"): item for item in order["surat_jalan_files"]}
        proof_out = {item["id"]: item for item in order["delivery_proofs"]}
        assert "data_url" not in payment_out["pay.pdf"]
        assert _decode_data_url(payment_out["pay-legacy.pdf"]["data_url"]) == legacy_pay_bytes
        assert "data_url" not in sample_out["sample-store.bin"]
        assert _decode_data_url(sample_out["sample-meta.pdf"]["data_url"]) == sample_meta_bytes
        assert _decode_data_url(sample_out["sample-legacy.pdf"]["data_url"]) == legacy_sample_bytes
        assert "data_url" not in sj_out["sj-meta.pdf"]
        assert "data_url" not in sj_out["sj-store.pdf"]
        assert "storage_path" not in sj_out["sj-store.pdf"]
        assert _decode_data_url(sj_out["sj-legacy.pdf"]["data_url"]) == legacy_sj_bytes
        assert "data_url" not in proof_out["proof-store"]
        assert proof_out["proof-store"]["storage_path"] == proof_path
        assert _decode_data_url(proof_out["proof-legacy"]["data_url"]) == proof_legacy_bytes
        assert _decode_data_url(order["design_files"][0]["data_url"]) == legacy_design_bytes
        assert order["delivery_recipient_signature"]["signature_storage_path"] == sig_recipient_path
        assert order["delivery_driver_signature"]["signature_storage_path"] == sig_driver_path
        assert "data_url" not in order["delivery_recipient_signature"]
        assert "data_url" not in order["delivery_driver_signature"]

        untouched = await db.orders.find_one({"id": "po-open"}, {"_id": 0})
        assert untouched["payment_files"][0]["data_url"] == _data_url(pay_bytes, "application/pdf")
        assert untouched["surat_jalan_files"][0]["data_url"] == _data_url(sj_meta_bytes, "application/pdf")
        assert untouched["sample_files"][0]["data_url"] == LARGE
        assert untouched["delivery_proofs"][0]["data_url"] == LARGE
    finally:
        app.dependency_overrides.pop(server.current_user, None)
        for path in stored_paths:
            try:
                obj_storage.delete_object(path)
            except Exception:
                pass
        await client.drop_database(db_name)
        client.close()
