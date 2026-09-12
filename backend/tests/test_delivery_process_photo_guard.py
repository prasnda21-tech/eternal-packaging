"""Early Pengiriman create + Loading-based photo guard + SJ/remain reconcile."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    REASON_LOADING_INCOMPLETE,
    REASON_SJ_NOT_READY,
    apply_delivery_from_proof_evidence,
    delivery_batch_photo_block_reason,
    delivery_batch_photo_readiness,
    qty_loaded,
    qty_packed,
    qty_shipped,
    reconcile_sj_quantities_to_loaded_delivery,
    validate_delivery_qty,
)


def _order(
    *,
    po_qty=10000,
    packed=0,
    loaded=0,
    sj_qty=None,
    shipment_id="s1",
    delivered=0,
    multi_line=None,
):
    if multi_line is not None:
        lines = multi_line
        order = {
            "id": "po1",
            "line_items": [
                {
                    "name": ln["name"],
                    "quantity": ln["po_qty"],
                    "line_id": ln.get("line_id") or f"L{i}",
                    "production_process": ln.get("production_process", "print"),
                }
                for i, ln in enumerate(lines)
            ],
            "total_quantity": sum(ln["po_qty"] for ln in lines),
            "packing_info": {
                "qty_packed": sum(ln.get("packed", 0) for ln in lines),
                "lines": [{"line_idx": i, "packed_qty": ln.get("packed", 0)} for i, ln in enumerate(lines)],
            },
            "shipments": [{
                "id": shipment_id,
                "number": "SJ-1",
                "sales_delivery_id": shipment_id,
                "lines": [{"line_idx": i, "qty": ln["sj"]} for i, ln in enumerate(lines)],
                "total_qty": sum(ln["sj"] for ln in lines),
            }],
            "surat_jalan_files": [{
                "id": shipment_id,
                "shipment_id": shipment_id,
                "sj_number": "SJ-1",
                "storage_path": "eternal-packaging/sj/old.pdf",
                "lines": [{"line_idx": i, "qty": ln["sj"]} for i, ln in enumerate(lines)],
                "total_qty": sum(ln["sj"] for ln in lines),
            }],
            "loading_confirmations": [{
                "id": "c1",
                "surat_jalan_id": shipment_id,
                "lines": [{"line_idx": i, "qty": ln.get("loaded", 0)} for i, ln in enumerate(lines)],
            }] if any(ln.get("loaded", 0) for ln in lines) else [],
            "qty_delivered": delivered,
            "delivery_proofs": [],
        }
        return order

    batch = sj_qty if sj_qty is not None else 0
    order = {
        "id": "po1",
        "line_items": [{"name": "Cup", "quantity": po_qty, "line_id": "L0", "production_process": "print"}],
        "total_quantity": po_qty,
        "packing_info": {
            "qty_packed": packed,
            "lines": [{"line_idx": 0, "packed_qty": packed}],
        },
        "shipments": [],
        "surat_jalan_files": [],
        "loading_confirmations": [],
        "qty_delivered": delivered,
        "delivery_proofs": [],
    }
    if batch > 0:
        order["shipments"] = [{
            "id": shipment_id,
            "number": "SJ-1",
            "sales_delivery_id": shipment_id,
            "lines": [{"line_idx": 0, "qty": batch}],
            "total_qty": batch,
        }]
        order["surat_jalan_files"] = [{
            "id": shipment_id,
            "shipment_id": shipment_id,
            "sj_number": "SJ-1",
            "storage_path": "eternal-packaging/sj/old.pdf",
            "lines": [{"line_idx": 0, "qty": batch}],
            "total_qty": batch,
        }]
    if loaded > 0 and batch > 0:
        order["loading_confirmations"] = [{
            "id": "c1",
            "surat_jalan_id": shipment_id,
            "lines": [{"line_idx": 0, "qty": loaded}],
        }]
    return order


def test_photo_case_a_sj3000_packed0_loaded0_blocked():
    o = _order(packed=0, loaded=0, sj_qty=3000)
    r = delivery_batch_photo_readiness(o)
    assert r["allowed"] is False
    assert r["reason_code"] == REASON_LOADING_INCOMPLETE


def test_photo_case_b_sj3000_packed3000_loaded0_blocked():
    o = _order(packed=3000, loaded=0, sj_qty=3000)
    r = delivery_batch_photo_readiness(o)
    assert r["allowed"] is False
    assert r["reason_code"] == REASON_LOADING_INCOMPLETE


def test_photo_case_c_partial_loading_blocked_until_sj_complete():
    """Delivery readiness requires full SJ line Loading — partial no longer unlocks photos."""
    o = _order(packed=3000, loaded=2000, sj_qty=3000)
    r = delivery_batch_photo_readiness(o)
    assert r["allowed"] is False
    assert r["reason_code"] == REASON_LOADING_INCOMPLETE
    assert r["loaded"] == 2000


def test_photo_case_d_full_loading_allows_even_if_packed_diag_lower():
    """Packing aggregate must not re-block Delivery after Loading completed for SJ."""
    o = _order(packed=1500, loaded=3000, sj_qty=3000)
    r = delivery_batch_photo_readiness(o)
    assert r["allowed"] is True
    assert r["delivery_ready"] is True


def test_photo_case_e_full_batch_loaded_allowed():
    o = _order(po_qty=10000, packed=4000, loaded=4000, sj_qty=4000)
    r = delivery_batch_photo_readiness(o)
    assert r["allowed"] is True
    assert r["batch_qty"] == 4000


def test_confirm_sj3000_partial_loaded2000_blocked_until_sj_complete():
    """Confirm Delivery requires full SJ-line Loading — same gate as photo upload."""
    o = _order(packed=3000, loaded=2000, sj_qty=3000, delivered=0)
    assert validate_delivery_qty(2000, o) is None  # qty ceiling alone still ok
    assert apply_delivery_from_proof_evidence(o) is None  # readiness gate blocks
    full = _order(packed=3000, loaded=3000, sj_qty=3000, delivered=0)
    ev = apply_delivery_from_proof_evidence(full)
    assert ev is not None
    assert ev["qty_delivered"] == 3000
    full["qty_delivered"] = ev["qty_delivered"]
    result = reconcile_sj_quantities_to_loaded_delivery(full)
    assert result["returned_qty"] == 0
    assert qty_shipped(full) == 3000
    assert qty_packed(full) == 3000
    assert qty_loaded(full) == 3000


def test_confirm_delivered2500_with_loaded2000_rejected():
    o = _order(packed=3000, loaded=2000, sj_qty=3000)
    err = validate_delivery_qty(2500, o)
    assert err is not None
    assert "Loading" in err or "loading" in err.lower() or "2500" in err


def test_confirm_packed1500_loaded2000_delivered2000_rejected_by_physical():
    o = _order(packed=1500, loaded=2000, sj_qty=3000)
    err = validate_delivery_qty(2000, o)
    assert err is not None
    assert "1500" in err or "fisik" in err.lower() or "eligible" in err.lower()


def test_confirm_loaded3000_delivered2000_accepted_loading_unchanged():
    o = _order(packed=3000, loaded=3000, sj_qty=3000, delivered=0)
    assert validate_delivery_qty(2000, o) is None
    o["qty_delivered"] = 2000
    loaded_before = qty_loaded(o)
    result = reconcile_sj_quantities_to_loaded_delivery(o)
    assert result["returned_qty"] == 1000
    assert qty_shipped(o) == 2000
    assert qty_loaded(o) == loaded_before == 3000
    assert qty_packed(o) == 3000


def test_reconcile_sj_3000_delivered_2000_idempotent():
    o = _order(po_qty=10000, packed=3000, loaded=2000, sj_qty=3000, delivered=2000)
    pdf_path = o["surat_jalan_files"][0]["storage_path"]
    result = reconcile_sj_quantities_to_loaded_delivery(o)
    assert result["returned_qty"] == 1000
    assert o["surat_jalan_files"][0]["storage_path"] == pdf_path
    result2 = reconcile_sj_quantities_to_loaded_delivery(o)
    assert result2["returned_qty"] == 0
    assert qty_shipped(o) == 2000


def test_reconcile_does_not_increase_sj_above_allocation():
    o = _order(po_qty=10000, packed=5000, loaded=4000, sj_qty=3000, delivered=3000)
    result = reconcile_sj_quantities_to_loaded_delivery(o)
    assert result["returned_qty"] == 0
    assert o["shipments"][0]["lines"][0]["qty"] == 3000


def test_reconcile_multi_line_exact_identity():
    o = _order(multi_line=[
        {"name": "Cup A", "po_qty": 5000, "packed": 1500, "sj": 1500, "loaded": 1500, "line_id": "A"},
        {"name": "Cup A", "po_qty": 5000, "packed": 1500, "sj": 1500, "loaded": 500, "line_id": "B"},
    ], delivered=2000)
    result = reconcile_sj_quantities_to_loaded_delivery(o)
    assert result["returned_qty"] == 1000
    assert o["shipments"][0]["lines"][0]["qty"] == 1500
    assert o["shipments"][0]["lines"][1]["qty"] == 500
    assert o["line_items"][0]["line_id"] == "A"
    assert o["line_items"][1]["line_id"] == "B"


def test_reconcile_skips_shipment_without_loading():
    o = _order(po_qty=10000, packed=4000, loaded=0, sj_qty=3000, delivered=0)
    o["shipments"].append({
        "id": "s2",
        "number": "SJ-2",
        "lines": [{"line_idx": 0, "qty": 1000}],
        "total_qty": 1000,
    })
    result = reconcile_sj_quantities_to_loaded_delivery(o)
    assert result["returned_qty"] == 0
    assert o["shipments"][0]["lines"][0]["qty"] == 3000
    assert o["shipments"][1]["lines"][0]["qty"] == 1000


def test_create_sales_delivery_source_no_longer_has_packing_gate():
    src = Path(__file__).resolve().parents[1] / "services" / "sales_deliveries.py"
    text = src.read_text(encoding="utf-8")
    assert "melebihi kuantitas packing yang siap dibuatkan Surat Jalan" not in text
    assert "Packing/Loading are NOT prerequisites" in text


def test_early_sales_delivery_does_not_inflate_eligible_without_loading():
    from quantity_rules import eligible_delivery_qty, loading_status_semantic

    o = _order(po_qty=10000, packed=0, loaded=0, sj_qty=4000)
    assert o["shipments"][0].get("sales_delivery_id")
    assert eligible_delivery_qty(o) == 0
    assert loading_status_semantic(o) != "completed"

    o["packing_info"] = {"qty_packed": 4000, "lines": [{"line_idx": 0, "packed_qty": 4000}]}
    o["loading_confirmations"] = [{
        "id": "c1",
        "surat_jalan_id": "s1",
        "lines": [{"line_idx": 0, "qty": 4000}],
    }]
    assert eligible_delivery_qty(o) == 4000
    assert delivery_batch_photo_readiness(o)["allowed"] is True


def test_historical_shipment_without_sales_delivery_id_still_eligible():
    from quantity_rules import eligible_delivery_qty

    o = {
        "line_items": [{"name": "Cup", "quantity": 1000}],
        "total_quantity": 1000,
        "packing_info": {"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        "shipments": [{"id": "sj-1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
        "loading_confirmations": [],
        "qty_delivered": 0,
    }
    assert eligible_delivery_qty(o) == 1000


def test_upload_delivery_proof_enforces_batch_guard():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "_qr_delivery_batch_photo_block_reason" in src
    assert "upload_delivery_proof" in src


def test_confirm_delivered_calls_sj_reconcile():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "_qr_reconcile_sj_quantities_to_loaded_delivery" in src
    block = src[src.index("async def confirm_delivered") : src.index("async def confirm_delivered") + 3500]
    assert "_qr_reconcile_sj_quantities_to_loaded_delivery" in block


def test_no_sj_returns_sj_not_ready_code():
    o = _order(packed=0, loaded=0, sj_qty=None)
    r = delivery_batch_photo_readiness(o)
    assert r["reason_code"] == REASON_SJ_NOT_READY
    assert delivery_batch_photo_block_reason(o)
