"""Historical SSI relation backfill — explicit identity only.

DRY-RUN is the default contract for callers. This module never invents
Add On→induk or SJ→SO links from name, qty, or item similarity.

Safe writes (apply mode only, when caller opts in):
- line_items.parent_line_id / parent_line_idx dual-store sync
- shipments.lines / delivery_confirmations.items line_id + line_idx stamps

Never mutates: physical stock, ledger, SO qty, delivered qty, operator_splits,
item_type, production_process, or Saldo Awal / Pembelian.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from quantity_rules import (
    is_print_line,
    is_process_only_service_line,
    match_po_line_idx,
)
from services.order_line_addon import (
    build_line_id_index,
    line_cancelled,
)

STATUS_VERIFIED_CANDIDATE = "VERIFIED_CANDIDATE"
STATUS_ALREADY_OK = "ALREADY_OK"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_MISSING = "MISSING"
STATUS_CONFLICT = "CONFLICT"
STATUS_SKIP = "SKIP"

KIND_ADDON_PARENT = "addon_parent"
KIND_SJ_LINE = "sj_line"
KIND_CONFIRMATION_ITEM = "confirmation_item"


def _text(raw: Any) -> str:
    return str(raw or "").strip()


def _has_parent_id(line: dict) -> bool:
    return bool(_text(line.get("parent_line_id")))


def _has_parent_idx(line: dict) -> bool:
    raw = line.get("parent_line_idx")
    return raw is not None and str(raw).strip() != ""


def _resolve_parent_by_id_only(line: dict, lines: Sequence[dict]) -> Optional[int]:
    parent_id = _text(line.get("parent_line_id"))
    if not parent_id:
        return None
    ids = build_line_id_index(lines)
    if parent_id in ids:
        return ids[parent_id]
    return None


def _resolve_parent_by_idx_only(line: dict, lines: Sequence[dict]) -> Optional[int]:
    if not _has_parent_idx(line):
        return None
    try:
        idx = int(line.get("parent_line_idx"))
    except (TypeError, ValueError):
        return None
    if idx < 0 or idx >= len(lines):
        return None
    if not isinstance(lines[idx], dict):
        return None
    return idx


def _parent_type_ok(parent_row: dict) -> bool:
    return _text(parent_row.get("item_type")).upper() != "SERVICE"


def classify_addon_parent_line(
    line: dict,
    lines: Sequence[dict],
    *,
    line_idx: int,
) -> Dict[str, Any]:
    """Classify one process Add On line for parent dual-key backfill.

    Explicit signals only: parent_line_id and/or parent_line_idx.
    Never uses name or quantity similarity.
    """
    base = {
        "kind": KIND_ADDON_PARENT,
        "line_idx": int(line_idx),
        "line_id": _text(line.get("line_id") or line.get("id")) or None,
        "master_item_id": _text(line.get("master_item_id")) or None,
        "name": _text(line.get("name")) or None,
    }
    if not isinstance(line, dict) or line_cancelled(line):
        return {**base, "status": STATUS_SKIP, "reason": "cancelled_or_invalid"}
    if not is_process_only_service_line(line):
        return {
            **base,
            "status": STATUS_SKIP,
            "reason": "not_process_only_service",
        }
    if not is_print_line(line):
        return {**base, "status": STATUS_SKIP, "reason": "non_print_service"}

    by_id = _resolve_parent_by_id_only(line, lines)
    by_idx = _resolve_parent_by_idx_only(line, lines)
    has_id = _has_parent_id(line)
    has_idx = _has_parent_idx(line)

    if has_id and has_idx and by_id is not None and by_idx is not None and by_id != by_idx:
        return {
            **base,
            "status": STATUS_CONFLICT,
            "reason": "parent_line_id_and_idx_disagree",
            "parent_by_id": by_id,
            "parent_by_idx": by_idx,
        }

    if has_id and by_id is None and not has_idx:
        return {
            **base,
            "status": STATUS_CONFLICT,
            "reason": "stale_parent_line_id",
            "parent_line_id": _text(line.get("parent_line_id")),
        }

    if has_id and by_id is None and has_idx and by_idx is None:
        return {
            **base,
            "status": STATUS_CONFLICT,
            "reason": "stale_parent_id_and_idx",
            "parent_line_id": _text(line.get("parent_line_id")),
            "parent_line_idx": line.get("parent_line_idx"),
        }

    if has_id and by_id is None and has_idx and by_idx is not None:
        # Explicit idx is valid; stale id must not be overwritten silently.
        return {
            **base,
            "status": STATUS_CONFLICT,
            "reason": "stale_parent_line_id_with_valid_idx",
            "parent_line_id": _text(line.get("parent_line_id")),
            "parent_by_idx": by_idx,
        }

    resolved = by_id if by_id is not None else by_idx
    if resolved is None:
        if has_id or has_idx:
            return {
                **base,
                "status": STATUS_CONFLICT,
                "reason": "parent_pointer_unresolved",
            }
        return {
            **base,
            "status": STATUS_MISSING,
            "reason": "no_explicit_parent_identity",
            "owner_hold": True,
        }

    parent_row = lines[resolved]
    if not _parent_type_ok(parent_row):
        return {
            **base,
            "status": STATUS_CONFLICT,
            "reason": "parent_is_service",
            "parent_idx": resolved,
        }

    parent_lid = _text(parent_row.get("line_id") or parent_row.get("id"))
    want_idx = int(resolved)
    want_id = parent_lid or None
    cur_idx = int(line["parent_line_idx"]) if _has_parent_idx(line) else None
    cur_id = _text(line.get("parent_line_id")) or None

    if cur_idx == want_idx and (not want_id or cur_id == want_id):
        return {
            **base,
            "status": STATUS_ALREADY_OK,
            "reason": "dual_parent_keys_consistent",
            "parent_idx": want_idx,
            "parent_line_id": want_id,
        }

    # Valid explicit relation present — safe dual-store sync candidate.
    # Does not invent; only mirrors the already-resolved pointer.
    return {
        **base,
        "status": STATUS_VERIFIED_CANDIDATE,
        "reason": "sync_dual_parent_keys_from_explicit_resolve",
        "parent_idx": want_idx,
        "parent_line_id": want_id,
        "before": {"parent_line_id": cur_id, "parent_line_idx": cur_idx},
        "after": {"parent_line_id": want_id, "parent_line_idx": want_idx},
        "op": {
            "field": "line_items",
            "line_idx": int(line_idx),
            "set": {
                "parent_line_idx": want_idx,
                **({"parent_line_id": want_id} if want_id else {}),
            },
        },
    }


def _order_line_id(order: dict, idx: int) -> Optional[str]:
    lis = order.get("line_items") or []
    if not (0 <= idx < len(lis)) or not isinstance(lis[idx], dict):
        return None
    return _text(lis[idx].get("line_id") or lis[idx].get("id")) or None


def classify_delivery_line_identity(
    order: dict,
    row: dict,
    *,
    kind: str,
    path: str,
) -> Dict[str, Any]:
    """Classify one SJ / confirmation line for identity stamp.

    Uses match_po_line_idx (line_id → accurate_item_id → line_idx). Never name/qty.
    """
    base = {
        "kind": kind,
        "path": path,
        "row_line_idx": row.get("line_idx"),
        "row_line_id": _text(row.get("line_id")) or None,
        "row_accurate_item_id": _text(row.get("accurate_item_id")) or None,
        "qty": row.get("qty") if row.get("qty") is not None else row.get("quantity"),
    }
    if not isinstance(row, dict):
        return {**base, "status": STATUS_SKIP, "reason": "invalid_row"}

    qty_raw = row.get("qty") if row.get("qty") is not None else row.get("quantity")
    try:
        qty_n = float(qty_raw) if qty_raw is not None and str(qty_raw).strip() != "" else 0.0
    except (TypeError, ValueError):
        qty_n = 0.0
    if qty_n <= 0:
        return {**base, "status": STATUS_SKIP, "reason": "non_positive_qty"}

    idx, err = match_po_line_idx(order, row)
    if err and idx is None:
        err_l = (err or "").lower()
        if "ambigu" in err_l:
            return {**base, "status": STATUS_AMBIGUOUS, "reason": err}
        if "wajib" in err_l or "tidak valid" in err_l:
            # Missing all explicit keys on multi-line, or bad pointer.
            lis = order.get("line_items") or []
            if len(lis) > 1 and not (
                _text(row.get("line_id"))
                or _text(row.get("accurate_item_id"))
                or (row.get("line_idx") is not None and str(row.get("line_idx")).strip() != "")
            ):
                return {
                    **base,
                    "status": STATUS_MISSING,
                    "reason": "no_explicit_sj_line_identity",
                    "owner_hold": True,
                }
            return {**base, "status": STATUS_CONFLICT, "reason": err}
        return {**base, "status": STATUS_CONFLICT, "reason": err}

    if idx is None:
        return {**base, "status": STATUS_MISSING, "reason": "unresolved_line", "owner_hold": True}

    want_id = _order_line_id(order, idx)
    cur_idx = None
    if row.get("line_idx") is not None and str(row.get("line_idx")).strip() != "":
        try:
            cur_idx = int(row.get("line_idx"))
        except (TypeError, ValueError):
            cur_idx = None
    cur_id = _text(row.get("line_id")) or None

    # Disagreeing explicit line_id vs resolved idx when both present and line_id
    # failed uniqueness — match_po_line_idx already preferred unique line_id.
    if cur_id and want_id and cur_id != want_id and cur_idx is not None and cur_idx == idx:
        # line_id on row differs from PO line at same idx → conflict
        return {
            **base,
            "status": STATUS_CONFLICT,
            "reason": "row_line_id_disagrees_with_po_line_at_idx",
            "resolved_idx": idx,
            "po_line_id": want_id,
        }

    if cur_idx == idx and (not want_id or cur_id == want_id):
        return {
            **base,
            "status": STATUS_ALREADY_OK,
            "reason": "sj_line_identity_consistent",
            "resolved_idx": idx,
            "line_id": want_id,
        }

    set_fields: Dict[str, Any] = {"line_idx": int(idx)}
    if want_id:
        set_fields["line_id"] = want_id

    return {
        **base,
        "status": STATUS_VERIFIED_CANDIDATE,
        "reason": "stamp_line_identity_from_explicit_match",
        "resolved_idx": idx,
        "before": {"line_idx": cur_idx, "line_id": cur_id},
        "after": {"line_idx": int(idx), "line_id": want_id},
        "op": {
            "field": kind,
            "path": path,
            "set": set_fields,
        },
    }


def plan_order_relation_backfill(order: dict) -> Dict[str, Any]:
    """Plan relation fixes for one order. Pure — does not mutate ``order``."""
    if not isinstance(order, dict):
        return {
            "order_id": None,
            "findings": [],
            "counts": _empty_counts(),
            "ops": [],
        }
    lines = list(order.get("line_items") or [])
    findings: List[dict] = []

    for i, li in enumerate(lines):
        if isinstance(li, dict):
            findings.append(classify_addon_parent_line(li, lines, line_idx=i))

    for s_i, sh in enumerate(order.get("shipments") or []):
        if not isinstance(sh, dict):
            continue
        sh_id = _text(sh.get("id")) or f"ship#{s_i}"
        for l_i, ln in enumerate(sh.get("lines") or []):
            if not isinstance(ln, dict):
                continue
            findings.append(
                classify_delivery_line_identity(
                    order,
                    ln,
                    kind=KIND_SJ_LINE,
                    path=f"shipments[{s_i}:{sh_id}].lines[{l_i}]",
                )
            )

    for c_i, conf in enumerate(order.get("delivery_confirmations") or []):
        if not isinstance(conf, dict):
            continue
        conf_id = _text(conf.get("id") or conf.get("action_id")) or f"conf#{c_i}"
        for i_i, it in enumerate(conf.get("items") or []):
            if not isinstance(it, dict):
                continue
            findings.append(
                classify_delivery_line_identity(
                    order,
                    it,
                    kind=KIND_CONFIRMATION_ITEM,
                    path=f"delivery_confirmations[{c_i}:{conf_id}].items[{i_i}]",
                )
            )

    counts = _empty_counts()
    ops: List[dict] = []
    for f in findings:
        st = f.get("status")
        if st in counts:
            counts[st] += 1
        if st == STATUS_VERIFIED_CANDIDATE and f.get("op"):
            ops.append(f["op"])

    return {
        "order_id": order.get("id"),
        "po_number": order.get("po_number"),
        "findings": findings,
        "counts": counts,
        "ops": ops,
    }


def _empty_counts() -> Dict[str, int]:
    return {
        STATUS_VERIFIED_CANDIDATE: 0,
        STATUS_ALREADY_OK: 0,
        STATUS_AMBIGUOUS: 0,
        STATUS_MISSING: 0,
        STATUS_CONFLICT: 0,
        STATUS_SKIP: 0,
    }


def apply_order_relation_ops(order: dict, ops: Sequence[dict]) -> dict:
    """Apply planned ops in memory. Mutates a deep copy; returns the copy.

    Idempotent: re-applying the same plan yields the same dual keys / stamps.
    Does not touch qty / stock / ledger / operator_splits.
    """
    out = copy.deepcopy(order)
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        field = op.get("field")
        sets = op.get("set") or {}
        if field == "line_items":
            idx = int(op.get("line_idx"))
            lis = out.setdefault("line_items", [])
            if 0 <= idx < len(lis) and isinstance(lis[idx], dict):
                for k, v in sets.items():
                    if k in ("parent_line_id", "parent_line_idx"):
                        lis[idx][k] = v
            continue
        if field in (KIND_SJ_LINE, KIND_CONFIRMATION_ITEM):
            _apply_path_set(out, str(op.get("path") or ""), sets)
    return out


def _apply_path_set(order: dict, path: str, sets: dict) -> None:
    """Apply identity stamps using the path encoding from the planner."""
    # paths: shipments[i:id].lines[j]  OR  delivery_confirmations[i:id].items[j]
    if path.startswith("shipments["):
        try:
            rest = path[len("shipments[") :]
            head, after = rest.split("].lines[", 1)
            s_i = int(head.split(":", 1)[0])
            l_i = int(after.rstrip("]"))
        except (ValueError, IndexError):
            return
        ships = order.get("shipments") or []
        if not (0 <= s_i < len(ships) and isinstance(ships[s_i], dict)):
            return
        lines = ships[s_i].setdefault("lines", [])
        if not (0 <= l_i < len(lines) and isinstance(lines[l_i], dict)):
            return
        for k, v in sets.items():
            if k in ("line_id", "line_idx"):
                lines[l_i][k] = v
        return
    if path.startswith("delivery_confirmations["):
        try:
            rest = path[len("delivery_confirmations[") :]
            head, after = rest.split("].items[", 1)
            c_i = int(head.split(":", 1)[0])
            i_i = int(after.rstrip("]"))
        except (ValueError, IndexError):
            return
        confs = order.get("delivery_confirmations") or []
        if not (0 <= c_i < len(confs) and isinstance(confs[c_i], dict)):
            return
        items = confs[c_i].setdefault("items", [])
        if not (0 <= i_i < len(items) and isinstance(items[i_i], dict)):
            return
        for k, v in sets.items():
            if k in ("line_id", "line_idx"):
                items[i_i][k] = v


def plan_orders_relation_backfill(orders: Iterable[dict]) -> Dict[str, Any]:
    plans = []
    totals = _empty_counts()
    for order in orders or []:
        plan = plan_order_relation_backfill(order)
        plans.append(plan)
        for k, v in (plan.get("counts") or {}).items():
            totals[k] = totals.get(k, 0) + int(v)
    return {
        "order_count": len(plans),
        "counts": totals,
        "plans": plans,
        "dry_run_default": True,
        "inference_policy": {
            "name_similarity": False,
            "qty_similarity": False,
            "item_similarity": False,
            "allowed_signals": [
                "parent_line_id",
                "parent_line_idx",
                "line_id",
                "accurate_item_id",
                "line_idx",
            ],
        },
    }


def assert_no_stock_side_effects(before: dict, after: dict) -> None:
    """Raise AssertionError if apply touched frozen business quantities."""
    frozen_order_keys = (
        "qty_delivered",
        "delivery_status",
        "operator_splits",
        "packing_info",
        "loading_checklist",
    )
    for key in frozen_order_keys:
        if before.get(key) != after.get(key):
            raise AssertionError(f"forbidden mutation of {key}")
    before_lines = before.get("line_items") or []
    after_lines = after.get("line_items") or []
    if len(before_lines) != len(after_lines):
        raise AssertionError("line_items length changed")
    for i, (b, a) in enumerate(zip(before_lines, after_lines)):
        if not isinstance(b, dict) or not isinstance(a, dict):
            continue
        for k in ("quantity", "item_type", "production_process", "master_item_id", "name"):
            if b.get(k) != a.get(k):
                raise AssertionError(f"line[{i}].{k} mutated")
    # Shipment / confirmation qtys must stay identical; only identity keys may change.
    for key in ("shipments", "delivery_confirmations"):
        bl = before.get(key) or []
        al = after.get(key) or []
        if len(bl) != len(al):
            raise AssertionError(f"{key} length changed")
        for i, (b, a) in enumerate(zip(bl, al)):
            if not isinstance(b, dict) or not isinstance(a, dict):
                continue
            if key == "shipments":
                if b.get("qty_delivered") != a.get("qty_delivered"):
                    raise AssertionError("shipment qty_delivered mutated")
                b_lines = b.get("lines") or []
                a_lines = a.get("lines") or []
                if len(b_lines) != len(a_lines):
                    raise AssertionError("shipment lines length changed")
                for j, (bln, aln) in enumerate(zip(b_lines, a_lines)):
                    bq = bln.get("qty") if bln.get("qty") is not None else bln.get("quantity")
                    aq = aln.get("qty") if aln.get("qty") is not None else aln.get("quantity")
                    if bq != aq:
                        raise AssertionError(f"shipment line[{j}] qty mutated")
            else:
                if b.get("qty") != a.get("qty"):
                    raise AssertionError("confirmation qty mutated")
                b_items = b.get("items") or []
                a_items = a.get("items") or []
                if len(b_items) != len(a_items):
                    raise AssertionError("confirmation items length changed")
                for j, (bi, ai) in enumerate(zip(b_items, a_items)):
                    if bi.get("qty") != ai.get("qty"):
                        raise AssertionError(f"confirmation item[{j}] qty mutated")


def snapshot_ssi_columns_for_order(order: dict, *, stock_book: float = 0.0) -> Dict[str, Any]:
    """Compute five-column SSI snapshot for one order (formula unchanged)."""
    from services.physical_stock import aggregate_operational_orders
    from services.stock_saat_ini_excel import (
        aggregate_proven_print_delivered_qty,
        compute_excel_columns,
        resolve_qty_tercetak,
    )

    process_ids = set()
    for li in order.get("line_items") or []:
        if isinstance(li, dict) and is_process_only_service_line(li):
            mid = _text(li.get("master_item_id"))
            if mid:
                process_ids.add(mid)

    progress = aggregate_operational_orders(
        [order],
        exclude_master_ids=process_ids or None,
    )
    delivered, prov = aggregate_proven_print_delivered_qty(
        [order],
        process_master_ids=process_ids or None,
    )
    rows = {}
    for mid, prow in progress.items():
        tercetak = resolve_qty_tercetak(prow)
        print_proven = mid in delivered and prov.get(mid) != "unproven"
        cetak_qty = delivered.get(mid) if print_proven else None
        if prov.get(mid) == "unproven":
            print_proven = False
            cetak_qty = None
        ordered = float(prow.get("ordered_qty") or 0)
        so_del = float(cetak_qty or 0) if print_proven else 0.0
        row = compute_excel_columns(
            saldo_awal=stock_book,
            delivered_total=so_del,
            po_ordered=ordered,
            so_delivered=so_del,
            qty_tercetak=tercetak.get("qty"),
            cetak_delivered_proven=cetak_qty if print_proven else None,
            print_delivery_proven=print_proven,
            stock_book=stock_book,
            qty_tercetak_status=tercetak.get("status"),
            qty_tercetak_reason=tercetak.get("reason"),
        )
        rows[mid] = {
            "qty_tercetak": row.get("qty_tercetak"),
            "qty_tercetak_status": row.get("qty_tercetak_status"),
            "qty_tercetak_reason": row.get("qty_tercetak_reason"),
            **{k: row.get(k) for k in (
                "stock",
                "stock_dapat_dijual",
                "cetak_sudah_dikirim",
                "cetak_sudah_dikirim_status",
                "cetak_sudah_dikirim_reason",
                "tercetak_belum_kirim",
                "tercetak_belum_kirim_status",
                "qty_polos",
                "qty_polos_status",
            )},
        }
    return rows
