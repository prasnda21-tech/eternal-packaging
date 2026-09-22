"""Live Tracking documents + recipient electronic signature / POD gates."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    can_confirm_delivery,
    confirm_delivery_block_reason,
    qty_packed,
    qty_loaded_for_line,
)
from services.customer_tracking import (
    is_public_payload_safe,
    public_payload,
)
from services.delivery_pod_signature import (
    apply_recipient_name_to_active_signature,
    build_driver_signature_record,
    build_signature_record,
    clear_active_signature,
    decode_data_url_png,
    find_signature_by_id,
    freeze_active_signature,
    has_valid_active_signature,
    normalize_recipient_name,
    public_signature_view,
    signature_confirm_block_reason,
    validate_recipient_name,
    validate_signature_bytes,
)
from services.sales_deliveries import generate_sales_delivery_pdf_bytes


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _visible_sig_png(stroke=(0, 0, 0), points=None):
    from PIL import Image as PILImage, ImageDraw
    im = PILImage.new("RGBA", (300, 100), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    pts = points or [(20, 70), (80, 30), (140, 75), (200, 25), (280, 60)]
    d.line(pts, fill=stroke + (255,), width=4)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _with_active_driver_sig(order):
    order["delivery_driver_signature"] = {
        "id": "dsig-a",
        "driver_signature_file_id": "dsig-a",
        "driver_name": "Budi Driver",
        "driver_signature_storage_path": "eternal-packaging/delivery-driver-signature/PO/dsig-a.png",
        "driver_signed_at": "2026-09-01T10:06:00+00:00",
        "driver_signed_by_user_id": "u-driver",
        "frozen": False,
        "shipment_id": "ship-a",
        "sj_file_id": "sj-a1",
    }
    return order


def _base_order(**extra):
    order = {
        "id": "ord-track-sig",
        "po_number": "PREVIEW-TRACKING-SIGNATURE-A",
        "customer": "PT Tracking A",
        "design_status": "Approved",
        "design_files": [
            {
                "id": "des-a1",
                "name": "design-a.png",
                "content_type": "image/png",
                "data_url": "data:image/png;base64,aaa",
            }
        ],
        "surat_jalan_files": [
            {
                "id": "sj-a1",
                "name": "SJ-A1.pdf",
                "sj_number": "SJ/A1",
                "shipment_id": "ship-a",
                "storage_path": "eternal-packaging/surat-jalan/ord-track-sig/sj-a1.pdf",
                "signed_storage_path": "eternal-packaging/surat-jalan/ord-track-sig/sj-a1-signed.pdf",
                "content_type": "application/pdf",
                "source": "SYSTEM_GENERATED",
            }
        ],
        "shipments": [{"id": "ship-a", "number": "SJ/A1", "lines": [{"line_idx": 0, "qty": 100}]}],
        "delivery_proofs": [
            {"id": "photo-a1", "shipment_id": "ship-a", "uploaded_at": "2026-09-01T10:00:00+00:00"},
            {"id": "photo-a2", "shipment_id": "ship-a", "uploaded_at": "2026-09-01T10:01:00+00:00"},
        ],
        "delivery_recipient_signature": {
            "id": "sig-a",
            "signature_file_id": "sig-a",
            "recipient_name": "Andi Penerima",
            "signature_storage_path": "eternal-packaging/delivery-signature/PO/sig-a.png",
            "recipient_signed_at": "2026-09-01T10:05:00+00:00",
            "frozen": True,
            "shipment_id": "ship-a",
            "sj_file_id": "sj-a1",
            "signed_sj_storage_path": "eternal-packaging/surat-jalan/ord-track-sig/sj-a1-signed.pdf",
        },
        "delivery_driver_signature": {
            "id": "dsig-a",
            "driver_signature_file_id": "dsig-a",
            "driver_name": "Budi Driver",
            "driver_signature_storage_path": "eternal-packaging/delivery-driver-signature/PO/dsig-a.png",
            "driver_signed_at": "2026-09-01T10:06:00+00:00",
            "driver_signed_by_user_id": "u-driver",
            "frozen": True,
            "shipment_id": "ship-a",
            "sj_file_id": "sj-a1",
        },
        "delivery_driver_signatures": [],
        "loading_checklist": {"driver_name": "Budi Driver"},
        "delivery_confirmations": [{
            "id": "conf-a",
            "shipment_id": "ship-a",
            "proof_ids": ["photo-a1", "photo-a2"],
            "confirmed_at": "2026-09-01T10:10:00+00:00",
            "qty": 100,
            "driver_name": "Budi Driver",
            "recipient_signature_id": "sig-a",
            "driver_signature_id": "dsig-a",
        }],
        "delivery_status": "Terkirim",
        "qty_delivered": 100,
        "packing_info": {"qty_packed": 100, "lines": [{"line_idx": 0, "packed_qty": 100}]},
        "loading_info": {"lines": [{"line_idx": 0, "loaded_qty": 100}]},
        "line_items": [{"name": "Karton", "quantity": 100, "production_process": "print"}],
        "total_quantity": 100,
        "current_stage": 14,
        "tracking_token": "token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    }
    order.update(extra)
    return order


def test_approved_design_visible_in_tracking_payload():
    payload = public_payload(_base_order(), "token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert is_public_payload_safe(payload)
    docs = payload["documents"]["design_approved"]
    assert len(docs) == 1
    assert docs[0]["id"] == "des-a1"
    assert "/public/tracking/" in docs[0]["preview_url"]
    assert "storage_path" not in str(docs[0])


def test_unapproved_design_not_exposed():
    o = _base_order(design_status="Waiting")
    payload = public_payload(o, "token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert payload["documents"]["design_approved"] == []


def test_tracking_payload_has_exact_sj():
    payload = public_payload(_base_order(), "token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    sj = payload["documents"]["surat_jalan"]
    assert len(sj) == 1
    assert sj[0]["id"] == "sj-a1"
    assert sj[0]["has_signed"] is True
    assert "object_key" not in str(payload)


def test_unsigned_sj_stays_listed_without_signature_status():
    order = _base_order()
    order["surat_jalan_files"][0].pop("signed_storage_path")
    before = [dict(f) for f in order["surat_jalan_files"]]
    payload = public_payload(order, "token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert order["surat_jalan_files"] == before
    sj = payload["documents"]["surat_jalan"]
    assert len(sj) == 1
    assert sj[0]["id"] == "sj-a1"
    assert sj[0]["sj_number"] == "SJ/A1"
    assert sj[0]["has_signed"] is False
    assert "signed_at" not in sj[0]
    assert "recipient_name" not in sj[0]


def test_signed_sj_appears_once_with_same_identity_and_sibling_unchanged():
    order = _base_order()
    order["surat_jalan_files"] = [
        {
            **order["surat_jalan_files"][0],
            "signed_at": "2026-09-01T10:07:00+00:00",
            "signed_recipient_name": "Andi Penerima",
        },
        {
            "id": "sj-b",
            "name": "SJ-B.pdf",
            "sj_number": "SJ/B",
            "shipment_id": "ship-b",
            "storage_path": "eternal-packaging/surat-jalan/ord-track-sig/sj-b.pdf",
            "content_type": "application/pdf",
        },
    ]
    before_ids = [(f["id"], f["sj_number"]) for f in order["surat_jalan_files"]]
    payload = public_payload(order, "token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert [(f["id"], f["sj_number"]) for f in order["surat_jalan_files"]] == before_ids
    assert is_public_payload_safe(payload)
    sj = payload["documents"]["surat_jalan"]
    assert [row["id"] for row in sj] == ["sj-a1", "sj-b"]
    assert sj[0]["sj_number"] == "SJ/A1"
    assert sj[0]["shipment_id"] == "ship-a"
    assert sj[0]["has_signed"] is True
    assert sj[0]["signed_at"] == "2026-09-01T10:07:00+00:00"
    assert sj[0]["recipient_name"] == "Andi Penerima"
    assert sj[1]["sj_number"] == "SJ/B"
    assert sj[1]["has_signed"] is False
    assert "signed_at" not in sj[1]
    assert "sj-other-order" not in str(payload)


def test_tracking_a_evidence_isolated_from_b_ids():
    payload = public_payload(_base_order(), "token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    blob = str(payload)
    assert "des-b" not in blob
    assert "sj-b" not in blob
    assert "photo-b" not in blob


def test_shipment_evidence_shows_two_photos_and_signature():
    payload = public_payload(_base_order(), "token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    ev = payload["shipments_evidence"]
    assert len(ev) == 1
    assert len(ev[0]["photos"]) == 2
    assert ev[0]["recipient"]["recipient_name"] == "Andi Penerima"
    assert ev[0]["recipient"]["url"].endswith("/signatures/sig-a")
    assert ev[0]["driver"]["driver_name"] == "Budi Driver"
    assert ev[0]["driver"]["url"].endswith("/driver-signatures/dsig-a")


def test_empty_signature_rejected():
    media, err = validate_signature_bytes(b"")
    assert media is None and err
    media, err = validate_signature_bytes(b"not-an-image")
    assert media is None and err


def test_png_signature_accepted():
    # pad tiny png to pass MIN_SIGNATURE_BYTES
    body = TINY_PNG + (b"\x00" * 80)
    media, err = validate_signature_bytes(body, "image/png")
    assert err is None
    assert media == "image/png"


def test_recipient_name_validation():
    assert validate_recipient_name("")
    assert validate_recipient_name("A")
    assert validate_recipient_name("Budi") is None
    assert normalize_recipient_name("  Budi  ") == "Budi"


def test_save_signature_does_not_mark_delivered():
    o = _base_order(
        delivery_status="Belum Terkirim",
        qty_delivered=0,
        delivery_confirmations=[],
    )
    o["delivery_recipient_signature"] = build_signature_record(
        recipient_name="Budi",
        signature_file_id="sig-new",
        storage_path="eternal-packaging/delivery-signature/x/sig-new.png",
        content_type="image/png",
        size=100,
        user={"email": "driver@test", "name": "Driver", "id": "u1"},
        shipment_id="ship-a",
        sj_file_id="sj-a1",
        signed_at="2026-09-01T12:00:00+00:00",
    )
    assert o["delivery_status"] == "Belum Terkirim"
    assert o["qty_delivered"] == 0
    assert has_valid_active_signature(o)


def test_confirm_rejects_one_photo():
    o = _base_order(delivery_proofs=[{"id": "p1"}], delivery_status="Belum Terkirim", qty_delivered=0)
    o["delivery_recipient_signature"] = {
        "id": "sig", "recipient_name": "X", "signature_storage_path": "p",
        "signature_file_id": "sig", "frozen": False, "recipient_signed_at": "t",
    }
    _with_active_driver_sig(o)
    assert can_confirm_delivery(o) is False
    assert confirm_delivery_block_reason(o)


def test_confirm_rejects_two_photos_no_signature():
    o = _base_order(
        delivery_status="Belum Terkirim",
        qty_delivered=0,
        delivery_confirmations=[],
    )
    o.pop("delivery_recipient_signature", None)
    o.pop("delivery_driver_signature", None)
    assert can_confirm_delivery(o) is True  # button appearance
    assert confirm_delivery_block_reason(o)
    assert "tanda tangan" in confirm_delivery_block_reason(o).lower()


def test_confirm_rejects_empty_recipient_name():
    o = _base_order(delivery_status="Belum Terkirim", qty_delivered=0, delivery_confirmations=[])
    o["delivery_recipient_signature"] = {
        "id": "sig", "recipient_name": "", "signature_storage_path": "p",
        "signature_file_id": "sig", "frozen": False, "recipient_signed_at": "t",
    }
    _with_active_driver_sig(o)
    assert confirm_delivery_block_reason(o)


def test_apply_recipient_name_binds_live_name_then_confirm_passes():
    o = _base_order(delivery_status="Belum Terkirim", qty_delivered=0, delivery_confirmations=[])
    o["delivery_recipient_signature"] = {
        "id": "sig", "recipient_name": "", "signature_storage_path": "p",
        "signature_file_id": "sig", "frozen": False, "recipient_signed_at": "t",
    }
    _with_active_driver_sig(o)
    assert "nama" in (confirm_delivery_block_reason(o) or "").lower()
    assert apply_recipient_name_to_active_signature(o, "   ") == "Nama penerima wajib diisi sebelum Konfirmasi Terkirim."
    assert o["delivery_recipient_signature"]["recipient_name"] == ""
    assert apply_recipient_name_to_active_signature(o, None) == "Nama penerima wajib diisi sebelum Konfirmasi Terkirim."
    assert apply_recipient_name_to_active_signature(o, "A") == "Nama penerima terlalu pendek."
    err = apply_recipient_name_to_active_signature(o, "  me time  ")
    assert err is None
    assert o["delivery_recipient_signature"]["recipient_name"] == "me time"
    assert confirm_delivery_block_reason(o) is None


def test_apply_recipient_name_does_not_create_a_signature():
    o = _base_order(delivery_status="Belum Terkirim", qty_delivered=0, delivery_confirmations=[])
    o.pop("delivery_recipient_signature", None)
    _with_active_driver_sig(o)
    assert apply_recipient_name_to_active_signature(o, "Budi Penerima") is None
    assert not o.get("delivery_recipient_signature")
    reason = confirm_delivery_block_reason(o) or ""
    assert "tanda tangan" in reason.lower()
    assert "nama penerima wajib" not in reason.lower()


def test_legacy_signature_without_recipient_name_key_accepts_live_name():
    o = _base_order(delivery_status="Belum Terkirim", qty_delivered=0, delivery_confirmations=[])
    o["delivery_recipient_signature"] = {
        "id": "sig", "signature_storage_path": "p",
        "signature_file_id": "sig", "frozen": False, "recipient_signed_at": "t",
    }
    _with_active_driver_sig(o)
    assert "recipient_name" not in o["delivery_recipient_signature"]
    assert "nama" in (confirm_delivery_block_reason(o) or "").lower()
    assert apply_recipient_name_to_active_signature(o, "  me time  ") is None
    assert o["delivery_recipient_signature"]["recipient_name"] == "me time"
    assert confirm_delivery_block_reason(o) is None


def test_confirm_rejects_missing_driver_signature():
    o = _base_order(delivery_status="Belum Terkirim", qty_delivered=0, delivery_confirmations=[])
    o["delivery_recipient_signature"] = {
        "id": "sig", "recipient_name": "Budi", "signature_storage_path": "p",
        "signature_file_id": "sig", "frozen": False, "recipient_signed_at": "t",
    }
    o.pop("delivery_driver_signature", None)
    reason = confirm_delivery_block_reason(o) or ""
    assert "driver" in reason.lower()


def test_confirm_succeeds_when_photos_name_signature_present():
    o = _base_order(delivery_status="Belum Terkirim", qty_delivered=0, delivery_confirmations=[])
    o["delivery_recipient_signature"] = {
        "id": "sig", "recipient_name": "Budi", "signature_storage_path": "p",
        "signature_file_id": "sig", "frozen": False, "recipient_signed_at": "t",
    }
    _with_active_driver_sig(o)
    assert confirm_delivery_block_reason(o) is None


def test_save_driver_signature_alone_does_not_mark_delivered():
    o = _base_order(
        delivery_status="Belum Terkirim",
        qty_delivered=0,
        delivery_confirmations=[],
    )
    o["delivery_driver_signature"] = build_driver_signature_record(
        signature_file_id="dsig-new",
        storage_path="eternal-packaging/delivery-driver-signature/x/dsig-new.png",
        content_type="image/png",
        size=100,
        user={"email": "driver@test", "name": "Budi Driver", "id": "u1"},
        order=o,
        shipment_id="ship-a",
        sj_file_id="sj-a1",
        signed_at="2026-09-01T12:00:00+00:00",
        driver_name="Budi Driver",
    )
    assert o["delivery_status"] == "Belum Terkirim"
    assert o["qty_delivered"] == 0


def test_admin_actor_cannot_overwrite_authoritative_driver_name():
    from services.delivery_pod_signature import (
        authoritative_driver_name,
        resolve_pdf_driver_display_name,
    )
    order = {
        "loading_checklist": {"driver_name": "Budi Driver Assigned"},
        "delivery_confirmations": [],
        "delivery_driver_signature": {
            "driver_name": "Local Preview Admin",  # tainted / wrong prior label
        },
    }
    admin = {"id": "admin-1", "name": "Local Preview Admin", "email": "admin@eternalpackaging.com"}
    assert authoritative_driver_name(order, admin) == "Budi Driver Assigned"
    # Explicit client-supplied wrong name must not win over loading assignment
    rec = build_driver_signature_record(
        signature_file_id="dsig-admin",
        storage_path="p.png",
        content_type="image/png",
        size=10,
        user=admin,
        order=order,
        shipment_id="ship-a",
        sj_file_id="sj-a1",
        driver_name="Local Preview Admin",
    )
    assert rec["driver_name"] == "Budi Driver Assigned"
    assert rec["driver_signed_by_user_id"] == "admin-1"
    assert rec["driver_signed_by_name"] == "Local Preview Admin"
    assert resolve_pdf_driver_display_name(order, driver_sig=rec, user=admin) == "Budi Driver Assigned"
    # PDF still uses assignment even if sig label was wrong
    assert (
        resolve_pdf_driver_display_name(
            order,
            driver_sig={"driver_name": "Hacker Name"},
            user=admin,
        )
        == "Budi Driver Assigned"
    )


def test_freeze_signature_and_clear_guard():
    o = {"delivery_recipient_signature": {
        "id": "sig", "recipient_name": "Budi", "signature_storage_path": "p",
        "signature_file_id": "sig", "frozen": False,
    }}
    freeze_active_signature(o, confirmation_id="c1")
    assert o["delivery_recipient_signature"]["frozen"] is True
    assert clear_active_signature(o) is False


def test_signed_pdf_includes_signature_and_does_not_change_qty():
    delivery = {
        "po_number": "PREVIEW-TRACKING-SIGNATURE-A",
        "sj_number": "SJ/A1",
        "shipment_index": 1,
        "customer": "PT Tracking A",
        "recipient": "Andi",
        "line_items": [{"name": "Karton", "qty": 100, "unit": "pcs", "production_process": "print"}],
        "created_by_name": "Sales",
    }
    order = _base_order()
    packed_before = order["packing_info"]
    loading_before = order["loading_info"]
    stage_before = order["current_stage"]
    recip = _visible_sig_png((0, 0, 0))
    driver = _visible_sig_png((20, 40, 160), [(15, 50), (80, 80), (150, 20), (220, 85), (285, 40)])
    unsigned = generate_sales_delivery_pdf_bytes(delivery, None)
    pdf = generate_sales_delivery_pdf_bytes(
        delivery,
        None,
        recipient_signature_png=recip,
        recipient_name="Andi Penerima",
        recipient_signed_at="2026-09-01T10:05:00+00:00",
        driver_signature_png=driver,
        driver_name="Budi Driver",
        driver_signed_at="2026-09-01T10:06:00+00:00",
    )
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > len(unsigned) + 800
    assert pdf.count(b"/Image") >= unsigned.count(b"/Image") + 2
    assert order["packing_info"] == packed_before
    assert order["loading_info"] == loading_before
    assert order["current_stage"] == stage_before
    assert qty_packed(order) == 100
    assert qty_loaded_for_line(order, 0) == 100


def test_partial_shipments_keep_proof_isolated():
    o = _base_order(
        surat_jalan_files=[
            {"id": "sj-a", "shipment_id": "ship-a", "name": "A", "storage_path": "a.pdf", "sj_number": "A"},
            {"id": "sj-b", "shipment_id": "ship-b", "name": "B", "storage_path": "b.pdf", "sj_number": "B"},
        ],
        delivery_proofs=[
            {"id": "pa1", "shipment_id": "ship-a"},
            {"id": "pa2", "shipment_id": "ship-a"},
            {"id": "pb1", "shipment_id": "ship-b"},
            {"id": "pb2", "shipment_id": "ship-b"},
        ],
        delivery_confirmations=[
            {"id": "ca", "shipment_id": "ship-a", "proof_ids": ["pa1", "pa2"], "confirmed_at": "t1"},
            {"id": "cb", "shipment_id": "ship-b", "proof_ids": ["pb1", "pb2"], "confirmed_at": "t2"},
        ],
        delivery_recipient_signatures=[
            {
                "id": "sa", "frozen": True, "shipment_id": "ship-a", "sj_file_id": "sj-a",
                "recipient_name": "A", "recipient_signed_at": "t1",
                "signature_storage_path": "sa.png", "confirmation_id": "ca",
            },
            {
                "id": "sb", "frozen": True, "shipment_id": "ship-b", "sj_file_id": "sj-b",
                "recipient_name": "B", "recipient_signed_at": "t2",
                "signature_storage_path": "sb.png", "confirmation_id": "cb",
            },
        ],
    )
    o.pop("delivery_recipient_signature", None)
    payload = public_payload(o, "token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    ev = payload["shipments_evidence"]
    assert len(ev) == 2
    assert {p["id"] for p in ev[0]["photos"]} == {"pa1", "pa2"}
    assert {p["id"] for p in ev[1]["photos"]} == {"pb1", "pb2"}
    assert ev[0]["recipient"]["recipient_name"] == "A"
    assert ev[1]["recipient"]["recipient_name"] == "B"


def test_legacy_delivered_without_signature_still_readable():
    o = _base_order()
    o.pop("delivery_recipient_signature", None)
    o["delivery_recipient_signatures"] = []
    o["delivery_confirmations"] = [{
        "id": "legacy", "shipment_id": "ship-a",
        "proof_ids": ["photo-a1", "photo-a2"], "confirmed_at": "2025-01-01T00:00:00Z",
    }]
    payload = public_payload(o, "token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert payload["order_number"]
    assert is_public_payload_safe(payload)
    # No fabricated signature
    for ev in payload["shipments_evidence"]:
        assert "recipient" not in ev or not ev.get("recipient")


def test_public_signature_view_has_no_storage_path():
    view = public_signature_view({
        "id": "sig-a",
        "recipient_name": "Andi",
        "recipient_signed_at": "t",
        "signature_storage_path": "SECRET/path.png",
        "shipment_id": "ship-a",
        "sj_file_id": "sj-a1",
        "signed_sj_storage_path": "SECRET/signed.pdf",
    }, "tok")
    assert "storage_path" not in view
    assert "SECRET" not in str(view)
    assert view["url"].startswith("/api/public/tracking/")


def test_find_signature_by_id():
    o = _base_order()
    assert find_signature_by_id(o, "sig-a")["recipient_name"] == "Andi Penerima"
    assert find_signature_by_id(o, "other") is None


def test_decode_data_url():
    import base64
    b64 = base64.b64encode(TINY_PNG + b"\x00" * 80).decode()
    body, media = decode_data_url_png(f"data:image/png;base64,{b64}")
    assert body and media == "image/png"
    body, err = decode_data_url_png("not-data")
    assert body is None and err


def test_server_signature_endpoint_uses_delivery_update():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert 'delivery-recipient-signature' in src
    assert "upload_delivery_recipient_signature" in src
    idx = src.index("async def upload_delivery_recipient_signature")
    block = src[idx:idx + 800]
    assert 'delivery.update' in block
