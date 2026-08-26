"""Customer PO tracking — token, public payload, evidence-based workflow."""
import json
import sys
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.customer_tracking import (
    ALLOWED_STATUSES,
    PUBLIC_NOT_FOUND,
    is_public_payload_safe,
    mask_token,
    new_tracking_token,
    public_payload,
    strip_tracking_secret,
)


def _print_po(**extra):
    order = {
        "id": "ord-a",
        "po_number": "PO/2026/TEST-A",
        "customer": extra.pop("customer", "PT Contoh"),
        "current_stage": 0,
        "design_files": [],
        "payment_files": [],
        "design_status": "Waiting",
        "payment_status": "Waiting",
        "design_bypassed": False,
        "payment_bypassed": False,
        "operator_splits": [],
        "screener_info": None,
        "helper_prep": None,
        "sample_files": [],
        "sample_approved": False,
        "line_items": [{"name": "Kaos", "quantity": 8000, "production_process": "print"}],
        "total_quantity": 8000,
        "requires_printing": True,
        **extra,
    }
    return order


def _stage(payload, name):
    return next(r for r in payload["workflow"] if r["stage"] == name)


# ---------- A / C: payload shape + no sensitive fields ----------

class TestPublicPayload:
    def test_a_valid_order_only_order_number_and_workflow(self):
        payload = public_payload(_print_po(
            design_files=[{"name": "d.png", "uploaded_at": "2026-01-01T00:00:00Z"}],
            design_status="Approved",
            payment_files=[{"name": "p.png"}],
            payment_status="Approved",
        ))
        assert {"order_number", "customer_name", "workflow", "delivery_proofs", "delivery_history"} <= set(payload.keys())
        assert set(payload.keys()) <= {"order_number", "customer_name", "workflow", "delivery_proofs", "delivery_history", "delivery_reschedule"}
        assert payload["order_number"] == "PO/2026/TEST-A"
        assert payload["customer_name"] == "PT Contoh"
        assert isinstance(payload["workflow"], list)
        assert payload["workflow"]
        for row in payload["workflow"]:
            assert set(row.keys()) == {"stage", "status"}
            assert row["status"] in ALLOWED_STATUSES

    def test_reschedule_snippet_is_date_only(self):
        payload = public_payload(_print_po(
            scheduled_delivery_date="2026-08-31",
            delivery_reschedules=[{
                "status": "scheduled",
                "new_date": "2026-08-31",
                "reason": "INTERNAL SECRET",
                "requested_by_id": "u-1",
            }],
        ))
        assert payload["delivery_reschedule"] == {"scheduled_date": "2026-08-31"}
        blob = json.dumps(payload)
        assert "INTERNAL" not in blob
        assert "u-1" not in blob
        assert is_public_payload_safe(payload)

    def test_c_no_sensitive_fields(self):
        dirty = _print_po(
            employee_id="E1",
            assigned_user="alice@example.com",
            qty_packed=10,
            price=99000,
            unit_price=12,
            stock=5,
            accurate_id="ACC-1",
            audit_log=[{"a": 1}],
            tracking_token="secret-token-value",
            notes="internal note",
            operator_splits=[{"operator_id": "u1", "qty_completed": 1}],
        )
        payload = public_payload(dirty)
        blob = json.dumps(payload)
        assert "employee" not in blob.lower()
        assert "alice@" not in blob
        assert "qty" not in blob.lower()
        assert "price" not in blob.lower()
        assert "stock" not in blob.lower()
        assert "accurate" not in blob.lower()
        assert "audit" not in blob.lower()
        assert "secret-token" not in blob
        assert "internal note" not in blob
        assert "u1" not in blob
        assert "ord-a" not in blob
        assert is_public_payload_safe(payload)

    def test_customer_and_po_and_max_five_proofs(self):
        proofs = [
            {"id": f"p{i}", "storage_path": f"eternal-packaging/delivery-proof/secret/{i}.jpg",
             "uploaded_at": f"2026-08-2{i}T10:00:00+07:00"}
            for i in range(7)
        ]
        payload = public_payload(_print_po(
            customer="PT Abadi",
            tracking_token="tok-public-aaaaaaaaaaaaaaaa",
            delivery_proofs=proofs,
        ), token="tok-public-aaaaaaaaaaaaaaaa")
        assert payload["customer_name"] == "PT Abadi"
        assert payload["order_number"] == "PO/2026/TEST-A"
        assert len(payload["delivery_proofs"]) == 5
        blob = json.dumps(payload)
        assert "storage_path" not in blob
        assert "eternal-packaging" not in blob
        assert "secret/" not in blob
        for p in payload["delivery_proofs"]:
            assert set(p.keys()) == {"id", "url"}
            assert p["url"].startswith("/api/public/tracking/")
            assert "/proofs/" in p["url"]
        assert is_public_payload_safe(payload)


# ---------- B: invalid token contract ----------

class TestInvalidToken:
    def test_b_generic_404_message(self):
        assert PUBLIC_NOT_FOUND == "Tidak ditemukan"
        exc = HTTPException(404, PUBLIC_NOT_FOUND)
        assert exc.status_code == 404
        assert "PO" not in str(exc.detail)
        assert "tidak ditemukan" in str(exc.detail).lower()

    def test_mask_does_not_echo_full_token(self):
        token = new_tracking_token()
        masked = mask_token(token)
        assert token not in masked
        assert "…" in masked or masked == "****"

    def test_access_log_redacts_token(self):
        from services.customer_tracking import redact_tracking_in_text
        token = new_tracking_token()
        line = f'GET /api/public/tracking/{token} HTTP/1.1'
        red = redact_tracking_in_text(line)
        assert token not in red
        assert "/api/public/tracking/" in red


# ---------- D: partial workflow from evidence, not stage index ----------

class TestPartialWorkflow:
    def test_d_design_done_does_not_complete_payment_or_helper(self):
        payload = public_payload(_print_po(
            current_stage=11,
            design_files=[{"name": "d.png", "uploaded_at": "2026-01-01T00:00:00Z"}],
            design_status="Approved",
        ))
        assert _stage(payload, "Design")["status"] == "completed"
        assert _stage(payload, "Approval Design")["status"] == "completed"
        assert _stage(payload, "Payment")["status"] == "waiting"
        assert _stage(payload, "Persiapan Helper")["status"] == "waiting"
        assert _stage(payload, "Screener")["status"] == "waiting"

    def test_d_helper_in_progress_operator_waiting(self):
        payload = public_payload(_print_po(
            design_files=[{"name": "d.png", "uploaded_at": "2026-01-01T00:00:00Z"}],
            design_status="Approved",
            payment_files=[{"name": "p.png"}],
            payment_status="Approved",
            operator_splits=[{"operator_id": "op", "qty": 100}],
            screener_info={"screen_qty": 8},
            helper_prep=None,
        ))
        assert _stage(payload, "Penjadwalan")["status"] == "completed"
        assert _stage(payload, "Screener")["status"] == "completed"
        assert _stage(payload, "Persiapan Helper")["status"] == "in_progress"
        assert _stage(payload, "Operator")["status"] == "in_progress"

    def test_d_bypass_maps_to_completed_not_debug_label(self):
        payload = public_payload(_print_po(design_bypassed=True, payment_bypassed=True))
        assert _stage(payload, "Design")["status"] == "completed"
        assert _stage(payload, "Payment")["status"] == "completed"
        blob = json.dumps(payload)
        assert "BYPASS" not in blob
        assert "bypass" not in blob.lower()

    def test_print_stages_hidden_for_non_print(self):
        payload = public_payload(_print_po(
            line_items=[{"name": "Box", "quantity": 10, "production_process": "non_print"}],
        ))
        names = [r["stage"] for r in payload["workflow"]]
        assert "Screener" not in names
        assert "Cetak" not in names
        assert "Konfirmasi Sample" not in names
        assert "Packing" in names
        assert "Terkirim" in names


# ---------- E / F: partial vs full delivery ----------

class TestDelivery:
    def test_e_partial_delivery_terkirim_not_completed(self):
        payload = public_payload(_print_po(
            design_files=[{"name": "d.png", "uploaded_at": "2026-01-01T00:00:00Z"}],
            design_status="Approved",
            payment_status="Approved",
            payment_bypassed=True,
            packing_info={"qty_packed": 8000},
            qty_delivered=4000,
            delivery_status="Sebagian Terkirim",
            current_stage=13,
            total_quantity=8000,
        ))
        assert _stage(payload, "Terkirim")["status"] == "in_progress"
        assert _stage(payload, "Terkirim")["status"] != "completed"

    def test_f_full_delivery_terkirim_completed(self):
        payload = public_payload(_print_po(
            packing_info={"qty_packed": 8000},
            qty_delivered=8000,
            delivery_status="Terkirim",
            current_stage=14,
            total_quantity=8000,
        ))
        assert _stage(payload, "Terkirim")["status"] == "completed"


# ---------- G: token uniqueness ----------

class TestToken:
    def test_g_two_tokens_differ_and_are_opaque(self):
        a = new_tracking_token()
        b = new_tracking_token()
        assert a != b
        assert a != "PO/2026/TEST-A"
        assert b != "ord-a"
        assert len(a) >= 32
        assert len(b) >= 32
        assert not a.isdigit()

    def test_strip_tracking_secret_from_internal_order(self):
        order = {"po_number": "PO-1", "tracking_token": "abc", "tracking_token_created_at": "x"}
        strip_tracking_secret(order)
        assert "tracking_token" not in order
        assert "tracking_token_created_at" not in order
        assert order["po_number"] == "PO-1"


def test_workflow_never_uses_generic_stage_index_alone():
    src = Path(__file__).resolve().parents[1] / "services" / "customer_tracking.py"
    text = src.read_text(encoding="utf-8")
    assert "idx < current_stage" not in text
