"""Dashboard widget: Barang Stok Minimum.

Uses the same sellable ATP helper as Stock Saat Ini.
KPI counts item×warehouse combinations — never page-1 only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.stock_minimum import evaluate_minimum_status, list_minimums
from services.stock_sellable import STATUS_INCOMPLETE


async def build_min_stock_snapshot(
    db,
    *,
    branch_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> dict:
    """Evaluate all inventory×warehouse rows in scope (via SSI catalog)."""
    from services.physical_stock import list_physical_stock_catalog

    safe_page = max(1, int(page or 1))
    safe_limit = max(1, min(int(limit or 50), 200))

    # Pull a wide page of masters; expand warehouse_balances for grain.
    # For KPI we iterate pages until exhausted (cap for safety).
    violations: List[dict] = []
    unset: List[dict] = []
    incomplete: List[dict] = []
    ok_count = 0
    evaluated = 0
    unevaluable = 0
    scanned_masters = 0

    catalog_page = 1
    catalog_limit = 200
    max_pages = 50
    while catalog_page <= max_pages:
        catalog = await list_physical_stock_catalog(
            db,
            q=None,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            page=catalog_page,
            limit=catalog_limit,
        )
        items = list(catalog.get("items") or [])
        total_masters = int(catalog.get("total") or 0)
        scanned_masters += len(items)
        if not items:
            break

        master_ids = [str(r.get("master_item_id") or "") for r in items if r.get("master_item_id")]
        mins = await list_minimums(
            db,
            branch_id=branch_id or catalog.get("effective_branch_id"),
            warehouse_id=warehouse_id or None,
            master_item_ids=master_ids,
        )
        min_map = {
            (
                str(m.get("master_item_id") or ""),
                str(m.get("branch_id") or ""),
                str(m.get("warehouse_id") or ""),
            ): m
            for m in mins
            if m
        }
        eff_branch = str(
            branch_id
            or catalog.get("effective_branch_id")
            or ""
        ).strip()

        for row in items:
            mid = str(row.get("master_item_id") or "").strip()
            if not mid:
                continue
            wh_rows = list(row.get("warehouse_balances") or [])
            if warehouse_id and not wh_rows:
                # Single-warehouse filter already applied to row physical qty.
                wh_rows = [{
                    "warehouse_id": str(row.get("warehouse_id") or warehouse_id),
                    "warehouse_code": row.get("warehouse_code") or "",
                    "warehouse_name": row.get("warehouse_name") or "",
                    "physical_qty": row.get("stock") if row.get("stock") is not None else row.get("physical_qty"),
                    "stock_dapat_dijual": row.get("stock_dapat_dijual"),
                    "stock_dapat_dijual_status": row.get("qty_siap_jual_status"),
                    "balance_present": True,
                }]
            if not wh_rows:
                unevaluable += 1
                incomplete.append(_detail_row(
                    row, None, None,
                    status_info={
                        "status": "SOURCE_INCOMPLETE",
                        "label": "Sumber belum lengkap",
                        "is_violation": False,
                        "evaluable": False,
                    },
                    branch_id=eff_branch,
                ))
                continue

            for wb in wh_rows:
                wid = str(wb.get("warehouse_id") or "").strip()
                if warehouse_id and wid and wid != str(warehouse_id):
                    continue
                if not wid:
                    continue
                min_doc = min_map.get((mid, eff_branch, wid))
                configured = bool(min_doc and min_doc.get("configured"))
                minimum_qty = min_doc.get("minimum_qty") if min_doc else None
                atp = wb.get("stock_dapat_dijual")
                atp_status = wb.get("stock_dapat_dijual_status") or row.get("qty_siap_jual_status")
                if row.get("allocation_incomplete") and atp_status not in (STATUS_INCOMPLETE, "INCOMPLETE"):
                    atp_status = STATUS_INCOMPLETE
                status_info = evaluate_minimum_status(
                    stock_dapat_dijual=atp,
                    sellable_status=atp_status,
                    minimum_qty=minimum_qty,
                    minimum_configured=configured,
                )
                detail = _detail_row(
                    row, wb, min_doc, status_info=status_info, branch_id=eff_branch,
                )
                if status_info["is_violation"]:
                    violations.append(detail)
                    evaluated += 1
                elif status_info["status"] == "MINIMUM_UNSET":
                    unset.append(detail)
                    unevaluable += 1
                elif status_info["status"] == "SOURCE_INCOMPLETE":
                    incomplete.append(detail)
                    unevaluable += 1
                else:
                    ok_count += 1
                    evaluated += 1

        if scanned_masters >= total_masters:
            break
        catalog_page += 1

    # Sort violations: most negative / lowest ATP first.
    def _atp_key(item):
        v = item.get("stock_dapat_dijual")
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    violations.sort(key=_atp_key)
    start = (safe_page - 1) * safe_limit
    page_rows = violations[start:start + safe_limit]

    truncated = scanned_masters >= catalog_limit * max_pages
    status = "complete"
    detail = None
    if truncated:
        status = "incomplete"
        detail = "Pemindaian mencapai batas aman. Jumlah pelanggaran adalah hasil yang terverifikasi."
    elif not violations and unevaluable > 0:
        status = "incomplete"
        detail = (
            "Belum ada pelanggaran terverifikasi, tetapi sebagian kombinasi "
            "barang–gudang belum bisa dinilai (minimum belum diatur atau sumber belum lengkap)."
        )
    elif not violations:
        status = "empty"
        detail = "Tidak ada kombinasi barang–gudang di/bawah minimum pada cakupan ini."

    return {
        "status": status,
        "detail": detail,
        "grain": "item_warehouse",
        "grain_label": "kombinasi barang–gudang",
        "violation_count": len(violations),
        "ok_count": ok_count,
        "unset_count": len(unset),
        "incomplete_count": len(incomplete),
        "evaluated_count": evaluated,
        "unevaluable_count": unevaluable,
        "total": len(violations),
        "page": safe_page,
        "limit": safe_limit,
        "page_count": (len(violations) + safe_limit - 1) // safe_limit if violations else 0,
        "items": page_rows,
        "truncated": truncated,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "period": False,
    }


def _detail_row(row, wb, min_doc, *, status_info, branch_id: str) -> dict:
    wb = wb or {}
    return {
        "master_item_id": row.get("master_item_id"),
        "item_code": row.get("item_code"),
        "item_name": row.get("item_name") or row.get("produk") or "",
        "branch_id": branch_id,
        "warehouse_id": wb.get("warehouse_id") or row.get("warehouse_id"),
        "warehouse_code": wb.get("warehouse_code") or row.get("warehouse_code") or "",
        "warehouse_name": wb.get("warehouse_name") or row.get("warehouse_name") or "",
        "stock": wb.get("physical_qty") if wb.get("physical_qty") is not None else row.get("stock"),
        "stock_dapat_dijual": wb.get("stock_dapat_dijual", row.get("stock_dapat_dijual")),
        "qty_polos": wb.get("qty_polos", row.get("qty_polos")),
        "qty_polos_note": wb.get("qty_polos_note") or (
            None if row.get("qty_polos") is not None else "Belum tersedia per gudang"
        ),
        "sisa_belum_cetak": wb.get("sisa_belum_cetak", row.get("sisa_belum_cetak")),
        "sisa_belum_cetak_note": wb.get("sisa_belum_cetak_note") or (
            None if row.get("sisa_belum_cetak") is not None else "Belum tersedia per gudang"
        ),
        "minimum_qty": (min_doc or {}).get("minimum_qty"),
        "minimum_configured": bool((min_doc or {}).get("configured")),
        "minimum_label": (
            (min_doc or {}).get("minimum_qty")
            if (min_doc or {}).get("configured")
            else "Minimum belum diatur"
        ),
        "unit": row.get("base_unit") or "PCS",
        "status": status_info.get("status"),
        "status_label": status_info.get("label"),
        "is_violation": bool(status_info.get("is_violation")),
        "evaluable": bool(status_info.get("evaluable")),
        "allocation_incomplete": bool(row.get("allocation_incomplete")),
    }
