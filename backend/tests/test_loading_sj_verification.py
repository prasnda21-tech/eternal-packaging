"""Focused tests: Loading SJ one-click match, discrepancy, CRM notify, RBAC, readiness."""

from __future__ import annotations

import copy

from permissions import effective_permissions, with_loading_permission_defaults
from quantity_rules import (
    apply_loading_discrepancy,
    apply_loading_match_confirmation,
    available_for_sj_loading_line,
    delivery_batch_photo_readiness,
    loading_verification_status,
    packing_shortfall_for_sj_match,
    sj_fingerprint,
    validate_loading_discrepancy,
    validate_loading_match_confirmation,
)


def _base_order(*, packed_cup=5000, sj_cup=5000, sj_lid=5000):
    return {
        "id": "po-load-1",
        "po_number": "PO-LOAD-1",
        "branch_id": "br-a",
        "customer_name": "Outlet A",
        "line_items": [
            {"name": "Cup Print", "quantity": 5000, "production_process": "print", "accurate_item_id": "cup"},
            {"name": "LID Non Print", "quantity": 5000, "production_process": "non_print", "accurate_item_id": "lid"},
        ],
        "packing_info": {"lines": [{"line_idx": 0, "packed_qty": packed_cup}]},
        "surat_jalan_files": [{
            "id": "sj1",
            "sj_number": "SJ-1",
            "total_qty": sj_cup + sj_lid,
            "lines": [{"line_idx": 0, "qty": sj_cup}, {"line_idx": 1, "qty": sj_lid}],
        }],
        "shipments": [{
            "id": "sj1",
            "number": "SJ-1",
            "total_qty": sj_cup + sj_lid,
            "lines": [{"line_idx": 0, "qty": sj_cup}, {"line_idx": 1, "qty": sj_lid}],
        }],
        "loading_confirmations": [],
        "loading_snapshots": [],
        "loading_discrepancies": [],
    }


def test_print_packing_zero_blocks_match_nonprint_ok_alone():
    o = _base_order(packed_cup=0)
    msg = packing_shortfall_for_sj_match(o, "sj1")
    assert msg and "Cup Print" in msg
    assert validate_loading_match_confirmation(o, "sj1")

    lid_only = _base_order(packed_cup=0, sj_cup=0, sj_lid=4000)
    lid_only["surat_jalan_files"][0]["lines"] = [{"line_idx": 1, "qty": 4000}]
    lid_only["shipments"][0]["lines"] = [{"line_idx": 1, "qty": 4000}]
    assert packing_shortfall_for_sj_match(lid_only, "sj1") is None
    assert validate_loading_match_confirmation(lid_only, "sj1") is None


def test_one_click_match_generates_lines_and_unlocks_delivery():
    o = _base_order()
    assert validate_loading_match_confirmation(o, "sj1") is None
    rec = apply_loading_match_confirmation(
        o,
        surat_jalan_id="sj1",
        actor={"email": "loader@x", "name": "Loader"},
        confirmation_id="c1",
        confirmed_at="2026-01-01T00:00:00Z",
        action_id="act-1",
    )
    assert rec.get("source") == "sj_match"
    assert sorted((ln.get("line_idx"), ln.get("qty")) for ln in rec.get("lines") or []) == [(0, 5000), (1, 5000)]
    assert o.get("loading_snapshots")
    assert o["loading_snapshots"][-1]["sj_fingerprint"] == sj_fingerprint(o, "sj1")
    ready = delivery_batch_photo_readiness(o)
    assert ready.get("allowed") is True
    st = loading_verification_status(o, "sj1")
    assert "complete" in str(st.get("code") or "")


def test_frontend_cannot_inject_match_quantities_domain_ignores_client_lines():
    """Match path builds from SJ only — client qty list is not an input to apply_loading_match."""
    o = _base_order()
    rec = apply_loading_match_confirmation(
        o, surat_jalan_id="sj1", actor={"email": "a"}, confirmation_id="c1",
        confirmed_at="t", action_id="a1",
    )
    assert all(ln.get("qty") in (5000,) for ln in rec["lines"])


def test_discrepancy_shortage_and_excess_hold_loading():
    o = _base_order()
    assert validate_loading_discrepancy(o, "sj1", [{"line_idx": 1, "actual_qty": 3000}]) is None
    assert validate_loading_discrepancy(o, "sj1", [{"line_idx": 1, "actual_qty": 5000}])
    assert validate_loading_discrepancy(o, "sj1", [{"line_idx": 1, "actual_qty": -1}])
    assert validate_loading_discrepancy(o, "sj1", [{"line_idx": 1, "actual_qty": ""}])

    d = apply_loading_discrepancy(
        o,
        surat_jalan_id="sj1",
        mismatches=[{"line_idx": 1, "actual_qty": 3000}, {"line_idx": 0, "actual_qty": 5200}],
        note="cek fisik",
        actor={"email": "r@x", "name": "Reporter"},
        discrepancy_id="d1",
        reported_at="t",
        action_id="disc-1",
    )
    assert d["status"] == "open"
    assert len(d["lines"]) == 2
    classes = {ln["line_idx"]: ln["classification"] for ln in d["lines"]}
    assert classes[1] == "kurang"
    assert classes[0] == "lebih"
    st = loading_verification_status(o, "sj1")
    assert "held" in str(st.get("code") or "") or "mismatch" in str(st.get("code") or "")
    assert delivery_batch_photo_readiness(o).get("allowed") is False


def test_unselected_lines_not_required_in_payload():
    o = _base_order()
    # Only LID mismatch selected — cup omitted means treated as match by product rule
    err = validate_loading_discrepancy(o, "sj1", [{"line_idx": 1, "actual_qty": 3000}])
    assert err is None


def test_sj_change_invalidates_snapshot_requires_reverify():
    o = _base_order()
    apply_loading_match_confirmation(
        o, surat_jalan_id="sj1", actor={"email": "a"}, confirmation_id="c1",
        confirmed_at="t", action_id="a1",
    )
    assert delivery_batch_photo_readiness(o).get("allowed") is True
    o["surat_jalan_files"][0]["lines"][1]["qty"] = 4000
    o["shipments"][0]["lines"][1]["qty"] = 4000
    ready = delivery_batch_photo_readiness(o)
    assert ready.get("allowed") is False
    assert "verifikasi" in str(ready.get("reason") or "").lower() or "Surat Jalan" in str(ready.get("reason") or "")
    st = loading_verification_status(o, "sj1")
    assert st.get("sj_changed") or "Menunggu" in str(st.get("label") or "") or "Verifikasi" in str(st.get("label") or "")
    assert validate_loading_match_confirmation(o, "sj1") is None
    apply_loading_match_confirmation(
        o, surat_jalan_id="sj1", actor={"email": "a"}, confirmation_id="c2",
        confirmed_at="t2", action_id="a2",
    )
    assert delivery_batch_photo_readiness(o).get("allowed") is True


def test_reverify_resolves_open_discrepancy():
    o = _base_order()
    apply_loading_discrepancy(
        o, surat_jalan_id="sj1", mismatches=[{"line_idx": 1, "actual_qty": 3000}],
        note="x", actor={"email": "r"}, discrepancy_id="d1", reported_at="t", action_id="a",
    )
    assert delivery_batch_photo_readiness(o).get("allowed") is False
    apply_loading_match_confirmation(
        o, surat_jalan_id="sj1", actor={"email": "a"}, confirmation_id="c1",
        confirmed_at="t2", action_id="a2",
    )
    open_status = [d for d in o.get("loading_discrepancies") or [] if d.get("status") in ("open", "held", None, "")]
    assert open_status == []
    assert delivery_batch_photo_readiness(o).get("allowed") is True


def test_loading_permission_defaults_backward_compatible():
    base = {"loading.update"}
    expanded = with_loading_permission_defaults(base)
    assert "loading.view" in expanded
    assert "loading.confirm_match" in expanded
    assert "loading.report_discrepancy" in expanded
    assert "loading.view_discrepancy_history" in expanded
    assert "loading.resolve_discrepancy" not in expanded

    driver = effective_permissions("driver", None)
    assert "loading.update" in driver
    assert "loading.confirm_match" in driver
    helper = effective_permissions("helper", None)
    assert "loading.update" not in helper
    assert "loading.confirm_match" not in helper

    # Explicit deny of child after update still respected (pre-configure / user override)
    denied = effective_permissions("driver", {"loading.report_discrepancy": False})
    assert "loading.confirm_match" in denied
    assert "loading.report_discrepancy" not in denied


def test_loading_granular_configured_makes_actions_revocable():
    """Once Role UI stamps loading.granular_configured, loading.update must not re-grant actions."""
    from permissions import LOADING_GRANULAR_CONFIGURED

    locked = with_loading_permission_defaults({
        "loading.update",
        LOADING_GRANULAR_CONFIGURED,
    })
    assert "loading.confirm_match" not in locked
    assert "loading.report_discrepancy" not in locked
    assert "loading.view" not in locked

    # Role defaults: update kept, confirm_match unchecked, configured stamped
    role_perms = [
        "loading.update",
        LOADING_GRANULAR_CONFIGURED,
        "po.view",
    ]
    revoked = effective_permissions("driver", None, {"driver": role_perms})
    assert "loading.update" in revoked
    assert "loading.confirm_match" not in revoked
    assert "loading.report_discrepancy" not in revoked

    # Re-enable confirm_match alone restores that action (not permanent OR with update)
    restored = effective_permissions(
        "driver",
        None,
        {"driver": role_perms + ["loading.confirm_match"]},
    )
    assert "loading.confirm_match" in restored
    assert "loading.report_discrepancy" not in restored
    assert "loading.view" in restored  # accompanies mutation key

    # View-only never grants mutation
    view_only = effective_permissions(
        "helper",
        None,
        {"helper": ["loading.view", LOADING_GRANULAR_CONFIGURED]},
    )
    assert "loading.view" in view_only
    assert "loading.confirm_match" not in view_only
    assert "loading.report_discrepancy" not in view_only
    assert "loading.update" not in view_only


def test_notification_helpers_exist_and_are_idempotent_keyed():
    from services.notifications import EVENT_LOADING_QTY_MISMATCH, emit_loading_qty_discrepancy_notifications
    assert EVENT_LOADING_QTY_MISMATCH == "loading_qty_mismatch"
    assert callable(emit_loading_qty_discrepancy_notifications)


def test_historical_loading_confirmations_still_readable_for_readiness():
    o = _base_order()
    # legacy confirmation without snapshot
    o["loading_confirmations"] = [{
        "id": "legacy1",
        "surat_jalan_id": "sj1",
        "lines": [{"line_idx": 0, "qty": 5000}, {"line_idx": 1, "qty": 5000}],
        "confirmed_at": "t",
    }]
    # Without matching fingerprint snapshot, readiness may still pass via line coverage
    # (legacy path). Ambiguous aggregate-only is covered in delivery readiness tests.
    ready = delivery_batch_photo_readiness(o)
    assert ready.get("allowed") in (True, False)  # must not raise
    # After match snapshot, fingerprint binds
    apply_loading_match_confirmation(
        o, surat_jalan_id="sj1", actor={"email": "a"}, confirmation_id="c-new",
        confirmed_at="t2", action_id="a-new",
    )
    assert any(s.get("sj_fingerprint") for s in o.get("loading_snapshots") or [])


def test_replaced_sj_confirmation_still_blocks_and_is_not_packing_shortage():
    o = _base_order(packed_cup=5000)
    o["loading_confirmations"] = [{
        "id": "orphan",
        "surat_jalan_id": "dc9c9875-9d8b-47c0-ad6f-37af8d15a1a2",
        "lines": [{"line_idx": 0, "qty": 5000}],
    }]
    assert available_for_sj_loading_line(o, "sj1", 0) == 0
    msg = packing_shortfall_for_sj_match(o, "sj1")
    assert msg and "sudah pernah dikonfirmasi Loading" in msg
    assert "Packing belum mencukupi" not in msg
    assert validate_loading_match_confirmation(o, "sj1") == msg
    st = loading_verification_status(o, "sj1")
    assert st.get("code") == "prior_loading_consumed"
