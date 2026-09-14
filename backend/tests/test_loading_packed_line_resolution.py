"""Packing → selected SJ → Loading availability uses exact PO line identity.

Models the production symptom on PO/2026/09/08/000006 + SJ/2026/09/08/000006:
Packing shows 1000 on 14 oz Inject Datar Frosted Klir, SJ total is 2000,
already loaded is 0 — Loading must see 1000 available for that exact line,
not 0. Same-name lines stay independent. packed=0 still blocks Loading.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    available_for_sj_loading_line,
    build_sj_loading_lines,
    loaded_qty_for_sj_line,
    loading_verification_status,
    match_po_line_idx,
    packed_qty_for_line,
    packing_line_breakdown,
    packing_shortfall_for_sj_match,
    qty_packed,
    sj_qty_for_line,
    validate_loading_match_confirmation,
)

FROSTED = "14 oz Inject Datar Frosted Klir"
SJ_ID = "sj-2026-09-08-000006"
SJ_NUMBER = "SJ/2026/09/08/000006"


def _po(
    *,
    packed_a=1000,
    packed_b=0,
    sj_a=1000,
    sj_b=1000,
    loaded_a=0,
    loaded_b=0,
    line_a_name=FROSTED,
    line_b_name="Other Cup",
    line_b_process="non_print",
    sj_lines=None,
    packing_lines=None,
    extra_shipments=None,
):
    packing = packing_lines
    if packing is None:
        packing = [
            {"line_idx": 0, "line_id": "LA", "packed_qty": packed_a},
            {"line_idx": 1, "line_id": "LB", "packed_qty": packed_b},
        ]
    if sj_lines is None:
        sj_lines = [
            {"line_idx": 0, "line_id": "LA", "qty": sj_a, "name": line_a_name},
            {"line_idx": 1, "line_id": "LB", "qty": sj_b, "name": line_b_name},
        ]
    confs = []
    if loaded_a or loaded_b:
        confs.append({
            "id": "c-sel",
            "surat_jalan_id": SJ_ID,
            "lines": [
                {"line_idx": 0, "qty": loaded_a},
                {"line_idx": 1, "qty": loaded_b},
            ],
        })
    shipments = [{
        "id": SJ_ID,
        "number": SJ_NUMBER,
        "sales_delivery_id": SJ_ID,
        "total_qty": sum(
            max(0, int(ln.get("qty") or ln.get("quantity") or 0))
            for ln in sj_lines
            if isinstance(ln, dict)
        ),
        "lines": [dict(ln) for ln in sj_lines],
    }]
    if extra_shipments:
        shipments.extend(extra_shipments)
    return {
        "id": "po-2026-09-08-000006",
        "po_number": "PO/2026/09/08/000006",
        "line_items": [
            {
                "name": line_a_name,
                "quantity": 1000,
                "production_process": "print",
                "line_id": "LA",
                "accurate_item_id": "frosted",
            },
            {
                "name": line_b_name,
                "quantity": 1000,
                "production_process": line_b_process,
                "line_id": "LB",
                "accurate_item_id": "other",
            },
        ],
        "packing_info": {
            "qty_packed": sum(max(0, int(x.get("packed_qty") or 0)) for x in packing),
            "lines": packing,
        },
        "surat_jalan_files": [{
            "id": SJ_ID,
            "shipment_id": SJ_ID,
            "sj_number": SJ_NUMBER,
            "name": f"Surat Jalan {SJ_NUMBER}.pdf",
            "total_qty": sum(
                max(0, int(ln.get("qty") or ln.get("quantity") or 0))
                for ln in sj_lines
                if isinstance(ln, dict)
            ),
            "lines": [dict(ln) for ln in sj_lines],
        }],
        "shipments": shipments,
        "loading_confirmations": confs,
        "loading_snapshots": [],
        "loading_discrepancies": [],
    }


def test_case1_exact_line_packed_1000_sj_requires_1000_available_1000():
    o = _po()
    assert packed_qty_for_line(o, 0) == 1000
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 1000
    assert packing_shortfall_for_sj_match(o, SJ_ID) is None
    assert validate_loading_match_confirmation(o, SJ_ID) is None
    st = loading_verification_status(o, SJ_ID)
    assert st.get("code") != "packing_insufficient"
    assert "tersedia 0" not in str(st.get("reason") or "")


def test_case2_exact_line_packed_0_loading_rejected():
    o = _po(packed_a=0, packed_b=0)
    o["packing_info"] = {"qty_packed": 0, "lines": [{"line_idx": 0, "line_id": "LA", "packed_qty": 0}]}
    assert packed_qty_for_line(o, 0) == 0
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 0
    msg = packing_shortfall_for_sj_match(o, SJ_ID)
    assert msg and FROSTED in msg
    assert "tersedia 0" in msg
    assert validate_loading_match_confirmation(o, SJ_ID)


def test_case3_same_name_lines_do_not_share_packing():
    o = _po(line_b_name=FROSTED, line_b_process="print", packed_a=1000, packed_b=0)
    assert packed_qty_for_line(o, 0) == 1000
    assert packed_qty_for_line(o, 1) == 0
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 1000
    assert available_for_sj_loading_line(o, SJ_ID, 1) == 0
    msg = packing_shortfall_for_sj_match(o, SJ_ID)
    assert msg and FROSTED in msg
    assert "tersedia 0" in msg
    # Packing on LA must not satisfy LB even though names match.
    assert match_po_line_idx(o, {"line_id": "LB", "line_idx": 1}) == (1, None)
    assert packed_qty_for_line(o, match_po_line_idx(o, {"line_id": "LB"})[0]) == 0


def test_case4_sj_total_2000_availability_is_per_exact_line():
    o = _po(line_b_process="print")
    built = build_sj_loading_lines(o, SJ_ID)
    assert sum(ln["qty"] for ln in built) == 2000
    by = {ln["line_idx"]: ln["qty"] for ln in built}
    assert by[0] == 1000
    assert by[1] == 1000
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 1000
    assert available_for_sj_loading_line(o, SJ_ID, 1) == 0
    assert packing_shortfall_for_sj_match(o, SJ_ID)


def test_case5_already_loaded_subtracted_once():
    o = _po(loaded_a=400)
    assert loaded_qty_for_sj_line(o, SJ_ID, 0) == 400
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 600
    assert packing_shortfall_for_sj_match(o, SJ_ID) is None
    assert validate_loading_match_confirmation(o, SJ_ID) is None


def test_case6_selected_sj_uses_own_shipment_lines():
    later = {
        "id": "sj-later",
        "number": "SJ/2026/09/08/000007",
        "sales_delivery_id": "sj-later",
        "total_qty": 500,
        "lines": [{"line_idx": 0, "line_id": "LA", "qty": 500}],
    }
    later_file = {
        "id": "sj-later",
        "shipment_id": "sj-later",
        "sj_number": "SJ/2026/09/08/000007",
        "total_qty": 500,
        "lines": [{"line_idx": 0, "line_id": "LA", "qty": 500}],
    }
    o = _po(extra_shipments=[later])
    o["surat_jalan_files"].append(later_file)
    from quantity_rules import sj_qty_for_line
    # Later batch must not rewrite the selected SJ composition (still 1000+1000).
    assert sj_qty_for_line(o, SJ_ID, 0) == 1000
    assert sj_qty_for_line(o, SJ_ID, 1) == 1000
    assert sj_qty_for_line(o, "sj-later", 0) == 500
    earlier = {ln["line_idx"]: ln["qty"] for ln in build_sj_loading_lines(o, SJ_ID)}
    later_lines = {ln["line_idx"]: ln["qty"] for ln in build_sj_loading_lines(o, "sj-later")}
    assert earlier[0] == 1000
    assert earlier[1] == 1000
    assert later_lines == {0: 500}
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 1000
    assert available_for_sj_loading_line(o, "sj-later", 0) == 500


def test_case7_sj_pdf_existence_does_not_increase_packed():
    o = _po(packed_a=1000)
    packed_before = qty_packed(o)
    assert packed_before == 1000
    # Shipment / file already present — packed stays the physical packing value.
    assert packed_qty_for_line(o, 0) == 1000
    assert o["surat_jalan_files"][0]["sj_number"] == SJ_NUMBER
    assert qty_packed(o) == packed_before


def test_sj_line_id_without_line_idx_resolves_packed_line():
    """Production-shaped gap: SJ keeps line_id but drops line_idx."""
    o = _po(sj_lines=[
        {"line_id": "LA", "qty": 1000, "name": FROSTED},
        {"line_id": "LB", "qty": 1000, "name": "Other Cup"},
    ])
    assert match_po_line_idx(o, {"line_id": "LA", "qty": 1000}) == (0, None)
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 1000
    built = build_sj_loading_lines(o, SJ_ID)
    by = {ln["line_idx"]: ln["qty"] for ln in built}
    assert by[0] == 1000
    assert by[1] == 1000
    # Line B is non-print — match may proceed; line A must not report tersedia 0.
    assert packing_shortfall_for_sj_match(o, SJ_ID) is None
    assert validate_loading_match_confirmation(o, SJ_ID) is None


def test_stale_line_idx_prefers_stable_line_id():
    """line_id is authoritative when it disagrees with a stale/1-based line_idx."""
    o = _po(line_b_process="print", sj_lines=[
        {"line_idx": 1, "line_id": "LA", "qty": 1000, "name": FROSTED},
        {"line_idx": 0, "line_id": "LB", "qty": 1000, "name": "Other Cup"},
    ])
    assert match_po_line_idx(o, {"line_idx": 1, "line_id": "LA"}) == (0, None)
    assert match_po_line_idx(o, {"line_idx": 0, "line_id": "LB"}) == (1, None)
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 1000
    assert available_for_sj_loading_line(o, SJ_ID, 1) == 0
    built = {ln["line_idx"]: ln["qty"] for ln in build_sj_loading_lines(o, SJ_ID)}
    assert built[0] == 1000
    assert built[1] == 1000
    msg = packing_shortfall_for_sj_match(o, SJ_ID)
    assert msg and "Other Cup" in msg
    assert "tersedia 0" in msg


def test_packing_row_keyed_by_line_id_still_counts():
    o = _po(packing_lines=[{"line_id": "LA", "packed_qty": 1000}])
    rows = packing_line_breakdown(o)
    frosted = next(r for r in rows if r["line_idx"] == 0)
    assert frosted["packed"] == 1000
    assert packed_qty_for_line(o, 0) == 1000
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 1000
    assert packing_shortfall_for_sj_match(o, SJ_ID) is None


def test_name_is_never_used_as_identity():
    o = _po(line_b_name=FROSTED, packed_a=1000, packed_b=0, sj_lines=[
        {"name": FROSTED, "qty": 1000},
        {"name": FROSTED, "qty": 1000},
    ])
    # Name-only SJ lines must not silently attach to the packed line.
    assert match_po_line_idx(o, {"name": FROSTED, "qty": 1000})[0] is None
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 0
    built = build_sj_loading_lines(o, SJ_ID)
    assert built == []


def _assert_ready(o, sid=SJ_ID, line=0, avail=1000):
    assert packed_qty_for_line(o, line) == avail
    assert available_for_sj_loading_line(o, sid, line) == avail
    assert packing_shortfall_for_sj_match(o, sid) is None
    assert validate_loading_match_confirmation(o, sid) is None
    st = loading_verification_status(o, sid)
    assert st.get("code") != "packing_insufficient"
    assert "tersedia 0" not in str(st.get("reason") or "")


def test_current_format_line_id_and_line_idx_ready():
    _assert_ready(_po())


def test_legacy_packing_line_id_only_ready():
    _assert_ready(_po(packing_lines=[{"line_id": "LA", "packed_qty": 1000}]))


def test_legacy_packing_line_idx_only_ready():
    _assert_ready(_po(packing_lines=[{"line_idx": 0, "packed_qty": 1000}]))


def test_legacy_packing_stale_line_idx_line_id_wins():
    """Packing row line_id is canonical; stale/1-based line_idx must not steal qty."""
    o = _po(packing_lines=[{"line_idx": 1, "line_id": "LA", "packed_qty": 1000}])
    assert match_po_line_idx(o, {"line_idx": 1, "line_id": "LA"}) == (0, None)
    assert packed_qty_for_line(o, 0) == 1000
    assert packed_qty_for_line(o, 1) == 0
    _assert_ready(o)


def test_sj_missing_line_idx_does_not_collapse_to_line_0():
    o = _po(sj_lines=[
        {"line_id": "LB", "qty": 1000, "name": "Other Cup"},
        {"line_id": "LA", "qty": 1000, "name": FROSTED},
    ])
    assert match_po_line_idx(o, {"line_id": "LB", "qty": 1000}) == (1, None)
    assert sj_qty_for_line(o, SJ_ID, 0) == 1000
    assert sj_qty_for_line(o, SJ_ID, 1) == 1000
    built = {ln["line_idx"]: ln["qty"] for ln in build_sj_loading_lines(o, SJ_ID)}
    assert built[0] == 1000
    assert built[1] == 1000


def test_ambiguous_legacy_identity_blocks_safely():
    o = _po(sj_lines=[{"accurate_item_id": "same", "qty": 1000}])
    o["line_items"][0]["accurate_item_id"] = "same"
    o["line_items"][1]["accurate_item_id"] = "same"
    o["line_items"][1]["production_process"] = "print"
    assert match_po_line_idx(o, {"accurate_item_id": "same", "qty": 1000})[0] is None
    assert build_sj_loading_lines(o, SJ_ID) == []
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 0
    assert validate_loading_match_confirmation(o, SJ_ID)


def test_production_symptom_file_stale_idx_uses_selected_shipment_identity():
    """Legacy file.lines keep raw/stale line_idx; shipment keeps unique line_id.

    Before this fix: Loading summary (shipment + line_id) saw 1000 while
    shortfall (file + raw line_idx) saw 0 on the same-name unpacked line.
    After: both paths use the selected shipment identity.
    """
    o = _po(
        line_b_name=FROSTED,
        line_b_process="print",
        packed_a=1000,
        packed_b=0,
        sj_lines=[
            {"line_idx": 0, "line_id": "LA", "qty": 1000, "name": FROSTED},
        ],
    )
    o["surat_jalan_files"][0]["lines"] = [{"line_idx": 1, "qty": 1000, "name": FROSTED}]
    o["surat_jalan_files"][0]["total_qty"] = 1000
    o["shipments"][0]["total_qty"] = 1000
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 1000
    assert available_for_sj_loading_line(o, SJ_ID, 1) == 0
    assert packing_shortfall_for_sj_match(o, SJ_ID) is None
    assert validate_loading_match_confirmation(o, SJ_ID) is None
    st = loading_verification_status(o, SJ_ID)
    assert st.get("code") != "packing_insufficient"


def test_summary_and_shortfall_agree_on_selected_sj():
    o = _po()
    avail = available_for_sj_loading_line(o, SJ_ID, 0)
    short = packing_shortfall_for_sj_match(o, SJ_ID)
    assert avail == 1000
    assert short is None
    later = {
        "id": "sj-later",
        "number": "SJ/2026/09/08/000007",
        "sales_delivery_id": "sj-later",
        "total_qty": 500,
        "lines": [{"line_idx": 0, "line_id": "LA", "qty": 500}],
    }
    o["shipments"].append(later)
    o["surat_jalan_files"].append({
        "id": "sj-later",
        "shipment_id": "sj-later",
        "sj_number": "SJ/2026/09/08/000007",
        "total_qty": 500,
        "lines": [{"line_idx": 0, "line_id": "LA", "qty": 500}],
    })
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 1000
    assert available_for_sj_loading_line(o, "sj-later", 0) == 500
    assert packing_shortfall_for_sj_match(o, SJ_ID) is None
    later_status = loading_verification_status(o, "sj-later")
    assert later_status.get("surat_jalan_id") == "sj-later"


def _two_sj_selected_a_latest_b():
    """Selected SJ A packed 1000; later/default-latest SJ B packed 0."""
    return {
        "id": "po-2026-09-08-000006",
        "po_number": "PO/2026/09/08/000006",
        "line_items": [
            {
                "name": FROSTED,
                "quantity": 1000,
                "production_process": "print",
                "line_id": "LA",
                "accurate_item_id": "frosted",
            },
            {
                "name": FROSTED,
                "quantity": 1000,
                "production_process": "print",
                "line_id": "LB",
                "accurate_item_id": "other",
            },
        ],
        "packing_info": {
            "qty_packed": 1000,
            "lines": [
                {"line_idx": 0, "line_id": "LA", "packed_qty": 1000},
                {"line_idx": 1, "line_id": "LB", "packed_qty": 0},
            ],
        },
        "surat_jalan_files": [
            {
                "id": "file-a",
                "shipment_id": "ship-a",
                "sj_number": "SJ/2026/09/08/000006",
                "total_qty": 1000,
                "lines": [{"line_idx": 1, "qty": 1000, "name": FROSTED}],
            },
            {
                "id": "file-b",
                "shipment_id": "ship-b",
                "sj_number": "SJ/2026/09/08/000007",
                "total_qty": 1000,
                "lines": [{"line_idx": 1, "qty": 1000, "name": FROSTED}],
            },
        ],
        "shipments": [
            {
                "id": "ship-a",
                "number": "SJ/2026/09/08/000006",
                "sales_delivery_id": "ship-a",
                "file_id": "file-a",
                "total_qty": 1000,
                "lines": [{"line_idx": 0, "line_id": "LA", "qty": 1000, "name": FROSTED}],
            },
            {
                "id": "ship-b",
                "number": "SJ/2026/09/08/000007",
                "sales_delivery_id": "ship-b",
                "file_id": "file-b",
                "total_qty": 1000,
                "lines": [{"line_idx": 1, "line_id": "LB", "qty": 1000, "name": FROSTED}],
            },
        ],
        "loading_confirmations": [],
        "loading_snapshots": [],
        "loading_discrepancies": [],
    }


def test_generic_enrich_without_id_keeps_latest_shipment_semantics():
    """GET/dashboard generic enrich stays latest-batch; selected A is explicit."""
    o = _two_sj_selected_a_latest_b()
    enrich = loading_verification_status(o)
    assert enrich.get("surat_jalan_id") == "ship-b"
    assert enrich.get("code") == "packing_insufficient"
    selected = loading_verification_status(o, "file-a")
    assert selected.get("surat_jalan_id") == "file-a"
    assert selected.get("code") != "packing_insufficient"
    assert packing_shortfall_for_sj_match(o, "file-a") is None
    assert validate_loading_match_confirmation(o, "file-a") is None
    latest = loading_verification_status(o, "file-b")
    assert latest.get("code") == "packing_insufficient"
    assert "tersedia 0" in str(latest.get("reason") or "")
    assert packing_shortfall_for_sj_match(o, "file-b")
    assert validate_loading_match_confirmation(o, "file-b")


def test_selected_a_packed_latest_b_zero_allows_a():
    o = _two_sj_selected_a_latest_b()
    assert available_for_sj_loading_line(o, "file-a", 0) == 1000
    assert packing_shortfall_for_sj_match(o, "file-a") is None
    assert validate_loading_match_confirmation(o, "file-a") is None


def test_selected_a_zero_latest_b_packed_still_blocks_a():
    o = _two_sj_selected_a_latest_b()
    o["packing_info"]["lines"] = [
        {"line_idx": 0, "line_id": "LA", "packed_qty": 0},
        {"line_idx": 1, "line_id": "LB", "packed_qty": 1000},
    ]
    o["shipments"][1]["lines"] = [{"line_idx": 1, "line_id": "LB", "qty": 1000, "name": FROSTED}]
    assert packing_shortfall_for_sj_match(o, "file-a")
    assert validate_loading_match_confirmation(o, "file-a")
    assert packing_shortfall_for_sj_match(o, "file-b") is None


def test_loaded_400_of_packed_1000_leaves_remaining_600():
    o = _po(sj_a=1000, sj_b=0, loaded_a=400)
    o["line_items"] = [o["line_items"][0]]
    o["surat_jalan_files"][0]["lines"] = [{"line_idx": 0, "line_id": "LA", "qty": 1000}]
    o["shipments"][0]["lines"] = [{"line_idx": 0, "line_id": "LA", "qty": 1000}]
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 600
    assert packing_shortfall_for_sj_match(o, SJ_ID) is None


def test_legacy_loading_info_still_blocks_physical_remaining():
    o = _po(sj_a=1000, sj_b=0)
    o["line_items"] = [o["line_items"][0]]
    o["packing_info"] = {"qty_packed": 1000, "lines": [{"line_idx": 0, "line_id": "LA", "packed_qty": 1000}]}
    o["surat_jalan_files"][0]["lines"] = [{"line_idx": 0, "line_id": "LA", "qty": 1000}]
    o["shipments"][0]["lines"] = [{"line_idx": 0, "line_id": "LA", "qty": 1000}]
    o["loading_info"] = {"lines": [{"line_idx": 0, "loaded_qty": 1000}]}
    assert packed_qty_for_line(o, 0) == 1000
    assert available_for_sj_loading_line(o, SJ_ID, 0) == 0
    msg = packing_shortfall_for_sj_match(o, SJ_ID)
    assert msg and "tersedia 0" in msg
