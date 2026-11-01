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
    public_order_summary,
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
        assert set(payload.keys()) <= {
            "order_number", "customer_name", "workflow", "delivery_proofs", "delivery_history",
            "delivery_reschedule", "documents", "shipments_evidence",
            "summary", "design_approval",
        }
        assert payload["order_number"] == "PO/2026/TEST-A"
        assert payload["customer_name"] == "PT Contoh"
        assert isinstance(payload["workflow"], list)
        assert payload["workflow"]
        for row in payload["workflow"]:
            assert set(row.keys()) <= {"stage", "status", "label"}
            assert "stage" in row and "status" in row
            assert row["status"] in ALLOWED_STATUSES

    def test_estimasi_uses_resolver_not_legacy_deadline(self):
        order = _print_po(
            deadline="2026-01-01",
            estimated_delivery="2026-01-02",
            design_approved_at="2026-09-14T10:00:00+07:00",
            payment_approved_at="2026-09-15T10:00:00+07:00",
            design_status="Approved",
            payment_status="Approved",
        )
        summary = public_order_summary(order, lead_time_days=3, holidays=[])
        assert "deadline" not in summary
        assert "estimated_delivery" not in summary
        est = summary["estimasi"]
        assert est["final"] is True
        assert est["source"] == "automatic"
        assert est["date"] == "2026-09-18"
        assert is_public_payload_safe(public_payload(order, lead_time_days=3, holidays=[]))

    def test_estimasi_non_final_when_gates_incomplete(self):
        summary = public_order_summary(
            _print_po(deadline="2026-12-31", design_approved_at="2026-09-14T10:00:00+07:00"),
            lead_time_days=3,
            holidays=[],
        )
        assert summary["estimasi"]["final"] is False
        assert summary["estimasi"]["display_state"] == "menunggu_approval"
        assert "date" not in summary["estimasi"]

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
        assert "qty_" not in blob.lower()
        assert "qty_packed" not in blob.lower()
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
            assert {"id", "url"} <= set(p.keys())
            assert set(p.keys()) <= {"id", "url", "shipment_id"}
            assert p["url"].startswith("/api/public/tracking/")
            assert "/proofs/" in p["url"]
        assert is_public_payload_safe(payload)

    def test_design_approval_omitted_when_no_link(self):
        payload = public_payload(_print_po(), token="tok-public-aaaaaaaaaaaaaaaa")
        assert "design_approval" not in payload
        assert is_public_payload_safe(payload)

    def test_design_approval_cta_uses_existing_path_without_raw_token(self):
        payload = public_payload(_print_po(
            tracking_token="tok-public-aaaaaaaaaaaaaaaa",
            customer_approval={
                "approval_id": "appr-1",
                "status": "active",
                "design_version": "df-1",
            },
            design_files=[{
                "id": "df-1",
                "name": "art.png",
                "content_type": "image/png",
                "storage_path": "internal/secret.png",
            }],
        ), token="tok-public-aaaaaaaaaaaaaaaa")
        assert payload["design_approval"]["status"] == "active"
        assert payload["design_approval"]["path"] == "/customer/design-approval/tok-public-aaaaaaaaaaaaaaaa"
        assert payload["design_approval"]["preview_url"].endswith("/design")
        blob = json.dumps(payload)
        assert "appr-1" not in blob
        assert "token_hash" not in blob
        assert "internal/secret" not in blob
        assert is_public_payload_safe(payload)

    def test_design_approval_completed_has_no_cta_path(self):
        payload = public_payload(_print_po(
            customer_approval={"approval_id": "appr-1", "status": "approved"},
        ), token="tok-public-aaaaaaaaaaaaaaaa")
        assert payload["design_approval"]["status"] == "approved"
        assert "path" not in payload["design_approval"]
        assert is_public_payload_safe(payload)

    def test_summary_keeps_same_name_lines_distinct(self):
        payload = public_payload(_print_po(
            line_items=[
                {"name": "Kaos", "quantity": 100, "unit": "pcs", "line_id": "L1"},
                {"name": "Kaos", "quantity": 50, "unit": "pcs", "line_id": "L2"},
            ],
            order_date="2026-01-02",
            deadline="2026-01-20",
            outlet="Yogyakarta",
        ))
        items = payload["summary"]["items"]
        assert len(items) == 2
        assert items[0]["quantity"] == 100
        assert items[1]["quantity"] == 50
        blob = json.dumps(payload)
        assert "L1" not in blob
        assert "line_id" not in blob
        assert payload["summary"]["outlet"] == "Yogyakarta"
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
        assert _stage(payload, "Payment")["status"] == "in_progress"
        assert _stage(payload, "Payment")["label"] == "Belum dikerjakan"
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
        # Operator stays waiting until helper prep evidence exists (evidence-based, not stage-index)
        assert _stage(payload, "Operator")["status"] == "waiting"

    def test_d_bypass_maps_to_completed_matching_alur(self):
        payload = public_payload(_print_po(design_bypassed=True, payment_bypassed=True))
        assert _stage(payload, "Design")["status"] == "completed"
        assert _stage(payload, "Payment")["status"] == "completed"
        assert _stage(payload, "Design")["label"] == "BYPASS"
        assert _stage(payload, "Payment")["label"] == "BYPASS"

    def test_live_tracking_is_exactly_15_alur_stages(self):
        from services.status_po import STAGES
        payload = public_payload(_print_po())
        names = [r["stage"] for r in payload["workflow"]]
        assert names == list(STAGES)
        assert len(names) == 15
        assert names[0] == "Order Masuk"
        assert names[12] == "Surat Jalan"
        assert names[13] == "Loading"
        assert names[14] == "Pengiriman"

    def test_print_stages_kept_for_non_print(self):
        payload = public_payload(_print_po(
            line_items=[{"name": "Box", "quantity": 10, "production_process": "non_print"}],
        ))
        names = [r["stage"] for r in payload["workflow"]]
        assert names[6] == "Screener"
        assert "Cetak" in names
        assert "Konfirmasi Sample" in names
        assert "Loading" in names
        assert len(names) == 15


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
        assert _stage(payload, "Pengiriman")["status"] == "in_progress"
        assert _stage(payload, "Pengiriman")["status"] != "completed"

    def test_f_full_delivery_terkirim_completed(self):
        payload = public_payload(_print_po(
            packing_info={"qty_packed": 8000},
            qty_delivered=8000,
            delivery_status="Terkirim",
            current_stage=14,
            total_quantity=8000,
        ))
        assert _stage(payload, "Pengiriman")["status"] == "completed"


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
        order = {
            "po_number": "PO-1",
            "tracking_token": "abc",
            "tracking_token_created_at": "x",
            "tracking_public_code": "alias",
            "tracking_public_code_created_at": "y",
        }
        strip_tracking_secret(order)
        assert "tracking_token" not in order
        assert "tracking_token_created_at" not in order
        assert "tracking_public_code" not in order
        assert "tracking_public_code_created_at" not in order
        assert order["po_number"] == "PO-1"


def test_workflow_uses_shared_stage_rows():
    src = Path(__file__).resolve().parents[1] / "services" / "customer_tracking.py"
    text = src.read_text(encoding="utf-8")
    assert "workflow_stage_rows" in text
    assert "idx < current_stage" not in text
    get_block = text.split("async def public_tracking")[1].split("async def ")[0]
    assert "update_one" not in get_block
    assert "insert_one" not in get_block
    assert "generate" not in get_block.lower()


def test_public_alias_is_128_bits_and_not_a_token_slice():
    import base64
    from services.customer_tracking import ALIAS_BYTES, ALIAS_LEN, new_tracking_alias

    token = new_tracking_token()
    alias = new_tracking_alias()
    assert ALIAS_BYTES * 8 == 128
    assert len(alias) == ALIAS_LEN == 22
    assert "=" not in alias
    assert alias != token[:ALIAS_LEN]
    raw = base64.urlsafe_b64decode(alias + "==")
    assert len(raw) == ALIAS_BYTES
    assert set(alias) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_public_url_labels_customer_po_and_keeps_the_access_code():
    from services.customer_tracking import TOKEN_BYTES
    from services.tracking_public_url import (
        customer_slug,
        po_path_slug,
        resolve_tracking_po,
        tracking_public_path,
    )

    assert TOKEN_BYTES * 8 >= 128
    assert customer_slug("Frutavor") == "frutavor"
    assert customer_slug("PT. Maju Jaya") == "pt-maju-jaya"
    assert customer_slug("Café Niño & Co.") == "café-niño-co"
    assert customer_slug("客户 甲") == "客户-甲"
    assert customer_slug("  ---  ") == "pelanggan"
    assert po_path_slug("PO-2026-10-02-000012") == "PO-2026-10-02-000012"
    assert po_path_slug("PO/2026/10/02/000012") == "PO-2026-10-02-000012"
    assert "02/10/2026" not in po_path_slug("PO/2026/10/02/000012")

    code = new_tracking_token()
    order = {
        "customer": "Frutavor",
        "po_number": "PO-2026-10-02-000012",
        "sj_number": "SJ/2026/99",
    }
    path, rule = tracking_public_path(order, code)
    assert rule == "single"
    assert path == f"/t/frutavor/PO-2026-10-02-000012/{code}"
    # Label segments only — never scan the random access-code token for "SJ".
    label_path = path.rsplit("/", 1)[0]
    assert "SJ" not in label_path
    assert code not in customer_slug("Frutavor")
    assert "frutavor" not in code.lower()
    assert "PO-2026" not in code

    slash = {"customer": "Frutavor", "po_number": "PO/2026/10/02/000012"}
    slash_path, slash_rule = tracking_public_path(slash, code)
    assert slash_rule == "single"
    assert slash_path == f"/t/frutavor/PO-2026-10-02-000012/{code}"

    missing_path, missing_rule = tracking_public_path(
        {"customer": "Frutavor", "po_number": "", "sj_number": "SJ/1"},
        code,
    )
    assert missing_rule == "missing"
    assert missing_path == f"/t/frutavor/tanpa-po/{code}"

    many_path, many_rule = tracking_public_path({
        "customer": "Frutavor",
        "po_number": "PO-SATU",
        "line_items": [{"name": "Cup", "po_number": "PO-DUA"}],
    }, code)
    assert many_rule == "multiple"
    assert resolve_tracking_po({"po_number": "PO-SATU", "po_numbers": ["PO-SATU"]}) == ("PO-SATU", "single")
    assert many_path == f"/t/frutavor/beberapa-po/{code}"
    assert "PO-SATU" not in many_path
    assert "PO-DUA" not in many_path


def test_page_url_log_redacts_the_access_code():
    from services.customer_tracking import redact_tracking_in_text
    code = new_tracking_token()
    line = f"GET /t/frutavor/PO-2026-10-02-000012/{code} HTTP/1.1"
    red = redact_tracking_in_text(line)
    assert code not in red
    assert "/t/frutavor/PO-2026-10-02-000012/" in red


def test_share_endpoint_reuses_token_and_get_does_not_create_one():
    src = Path(__file__).resolve().parents[1] / "services" / "customer_tracking.py"
    text = src.read_text(encoding="utf-8")
    share = text.split("async def tracking_link")[1]
    assert "ensure_tracking_token" in share
    assert "ensure_tracking_public_code" in share
    assert "tracking_public_path" in share
    assert "legacy_path" in share
    getter = text.split("async def public_tracking")[1].split("async def ")[0]
    assert "ensure_tracking_token" not in getter
    assert "ensure_tracking_public_code" not in getter
    assert "update_one" not in getter


def test_approval_lookup_reuses_tracking_token_without_generate():
    src = Path(__file__).resolve().parents[1] / "customer_approval.py"
    text = src.read_text(encoding="utf-8")
    assert 'find_one({"tracking_token": token}' in text or "tracking_token\": token" in text
    assert "tracking_public_code" in text
    load = text.split("async def _load_by_token")[1].split("async def ")[0]
    assert "token_urlsafe" not in load
    assert "insert_one" not in load
    assert "update_one" not in load
