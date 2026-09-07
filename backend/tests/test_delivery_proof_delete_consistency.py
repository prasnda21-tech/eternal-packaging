"""Hapus Bukti Kirim — exact proof removal, no full-document persist, StageFlow source."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import qty_packed, valid_delivery_proofs
from services.workflow_stage_status import resolve_stage_chip


def _fn_block(src: str, name: str) -> str:
    marker = f"async def {name}("
    start = src.index(marker)
    nxt = src.find("\nasync def ", start + len(marker))
    nxt_class = src.find("\nclass ", start + len(marker))
    ends = [i for i in (nxt, nxt_class) if i != -1]
    end = min(ends) if ends else len(src)
    return src[start:end]


def test_delete_endpoint_writes_only_proof_and_timeline():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    block = _fn_block(src, "delete_delivery_proof")
    assert 'persist_keys=("delivery_proofs", "timeline")' in block
    assert "obj_storage.get_object" not in block
    assert "list_objects" not in block
    assert "current_stage =" not in block
    persist = _fn_block(src, "_persist_order")
    assert "persist_keys" in persist
    assert 'allowed = set(persist_keys) | {"updated_at"}' in persist


def test_delete_exact_proof_leaves_unrelated_and_quantities():
    order = {
        "id": "po-proof",
        "packing_info": {"qty_packed": 40, "lines": [{"line_idx": 0, "packed_qty": 40}]},
        "loading_confirmations": [{"id": "c1", "surat_jalan_id": "sj1", "lines": [{"line_idx": 0, "qty": 40}]}],
        "delivery_proofs": [
            {"id": "p-keep", "original_filename": "a.jpg"},
            {"id": "p-gone", "original_filename": "b.jpg"},
        ],
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "terkirim_confirmed": False,
        "current_stage": 13,
        "eligible_delivery_qty": 40,
    }
    packed_before = qty_packed(order)
    loading_before = list(order["loading_confirmations"])
    proofs = order["delivery_proofs"]
    target_id = "p-gone"
    order["delivery_proofs"] = [p for p in proofs if p["id"] != target_id]
    remaining = valid_delivery_proofs(order)
    assert [p["id"] for p in remaining] == ["p-keep"]
    assert qty_packed(order) == packed_before
    assert order["loading_confirmations"] == loading_before
    chip = resolve_stage_chip(14, order, 13)
    assert chip["state"] != "done"


def test_pengiriman_chip_follows_persisted_delivery_not_proof_count():
    waiting = {
        "delivery_proofs": [{"id": "p1"}, {"id": "p2"}],
        "terkirim_confirmed": False,
        "delivery_status": "Belum Terkirim",
        "qty_delivered": 0,
        "eligible_delivery_qty": 100,
        "current_stage": 13,
    }
    assert resolve_stage_chip(14, waiting, 13)["state"] != "done"
    waiting["delivery_proofs"] = []
    after = resolve_stage_chip(14, waiting, 13)
    assert after["state"] != "done"
    confirmed = {
        "delivery_proofs": [],
        "terkirim_confirmed": True,
        "delivery_status": "Terkirim",
        "qty_delivered": 100,
        "current_stage": 14,
        "line_items": [{"name": "A", "quantity": 100, "production_process": "print"}],
        "total_quantity": 100,
        "packing_info": {"qty_packed": 100, "lines": [{"line_idx": 0, "packed_qty": 100}]},
    }
    assert resolve_stage_chip(14, confirmed, 14)["state"] == "done"
