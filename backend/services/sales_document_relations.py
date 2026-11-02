"""Read-only document relation enrichment for Flexible Document Relation (Stage 4).

Builds compact PO ↔ SJ ↔ Faktur relation payloads from canonical collections.
Does not mutate stock, billing, or create events.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.sales_delivery_allocations import (
    ALLOCATION_COMMIT_PENDING_FIELD,
    assess_multi_source_slice_fulfillment,
    delivery_source_po_ids,
    is_multi_source_delivery,
    normalize_delivery_source_allocations,
)
from services.sales_invoice_allocations import BILLING_COMMIT_PENDING_FIELD


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _po_qty_on_delivery(delivery: dict, po_id: str) -> int:
    pid = _norm(po_id)
    total = 0
    for alloc in normalize_delivery_source_allocations(delivery):
        if _norm(alloc.get("source_po_id")) == pid:
            total += _as_int(alloc.get("allocated_qty"))
    return total


def _invoice_qty_for_po(invoice: dict, po_id: str) -> int:
    pid = _norm(po_id)
    total = 0
    for line in invoice.get("lines") or []:
        if not isinstance(line, dict):
            continue
        line_po = _norm(line.get("source_po_id") or invoice.get("po_id"))
        if line_po == pid:
            total += _as_int(line.get("quantity"))
    if total == 0 and _norm(invoice.get("po_id")) == pid:
        total = _as_int(invoice.get("total_qty"))
    return total


async def related_deliveries_for_po(db, po_id: str, *, limit: int = 50) -> List[dict]:
    """Active (non-provisional) SJ that reference this PO — capped, newest first."""
    pid = _norm(po_id)
    if not pid:
        return []
    cursor = (
        db.sales_deliveries.find(
            {
                "$or": [
                    {"po_id": pid},
                    {"source_po_ids": pid},
                    {"line_items.source_po_id": pid},
                ],
                ALLOCATION_COMMIT_PENDING_FIELD: {"$ne": True},
            },
            {
                "_id": 0,
                "id": 1,
                "sj_number": 1,
                "po_id": 1,
                "po_number": 1,
                "source_po_ids": 1,
                "is_multi_source": 1,
                "is_cancelled": 1,
                "status": 1,
                "total_qty": 1,
                "source_allocations": 1,
                "line_items": 1,
                "created_at": 1,
                "fulfillment_last_status": 1,
                "fulfillment_commit_pending": 1,
            },
        )
        .sort("created_at", -1)
        .limit(max(1, min(int(limit), 100)))
    )
    rows = await cursor.to_list(limit)
    out: List[dict] = []
    for d in rows:
        if not isinstance(d, dict):
            continue
        cancelled = d.get("is_cancelled") is True or _norm(d.get("status")).lower() in {
            "cancelled", "dibatalkan", "deleted", "batal",
        }
        out.append({
            "id": _norm(d.get("id")),
            "sj_number": _norm(d.get("sj_number") or d.get("id")),
            "is_multi_source": bool(d.get("is_multi_source")) or is_multi_source_delivery(d),
            "source_po_count": len(delivery_source_po_ids(d)),
            "is_cancelled": cancelled,
            "status": "cancelled" if cancelled else _norm(d.get("status") or "active"),
            "po_qty_on_sj": _po_qty_on_delivery(d, pid),
            "total_qty": _as_int(d.get("total_qty")),
            "fulfillment_last_status": _norm(d.get("fulfillment_last_status")),
            "fulfillment_commit_pending": d.get("fulfillment_commit_pending") is True,
            "created_at": d.get("created_at"),
        })
    return out


async def related_invoices_for_po(db, po_id: str, *, limit: int = 50) -> List[dict]:
    pid = _norm(po_id)
    if not pid:
        return []
    cursor = (
        db.sales_invoices.find(
            {
                "$or": [
                    {"po_id": pid},
                    {"source_po_ids": pid},
                    {"lines.source_po_id": pid},
                ],
                BILLING_COMMIT_PENDING_FIELD: {"$ne": True},
            },
            {
                "_id": 0,
                "id": 1,
                "invoice_number": 1,
                "po_id": 1,
                "source_mode": 1,
                "source_po_ids": 1,
                "source_sj_ids": 1,
                "is_multi_source": 1,
                "financial_status": 1,
                "payment_status": 1,
                "total_qty": 1,
                "grand_total": 1,
                "lines": 1,
                "created_at": 1,
                "finalized_at": 1,
                "cancelled_at": 1,
            },
        )
        .sort("created_at", -1)
        .limit(max(1, min(int(limit), 100)))
    )
    rows = await cursor.to_list(limit)
    out: List[dict] = []
    for inv in rows:
        if not isinstance(inv, dict):
            continue
        mode = _norm(inv.get("source_mode")).lower()
        if mode not in ("po", "sj"):
            mode = "sj" if (inv.get("source_sj_ids") or []) else "po"
        out.append({
            "id": _norm(inv.get("id")),
            "invoice_number": _norm(inv.get("invoice_number") or inv.get("id")),
            "source_mode": mode,
            "is_multi_source": bool(inv.get("is_multi_source"))
            or len(inv.get("source_po_ids") or []) > 1
            or len(inv.get("source_sj_ids") or []) > 1,
            "financial_status": _norm(inv.get("financial_status") or "draft").lower() or "draft",
            "payment_status": _norm(inv.get("payment_status") or "BELUM DIBAYAR").upper() or "BELUM DIBAYAR",
            "po_qty_on_invoice": _invoice_qty_for_po(inv, pid),
            "total_qty": _as_int(inv.get("total_qty")),
            "grand_total": inv.get("grand_total"),
            "created_at": inv.get("created_at"),
            "finalized_at": inv.get("finalized_at"),
            "cancelled_at": inv.get("cancelled_at"),
        })
    return out


async def related_invoices_for_sj(db, sales_delivery_id: str, *, limit: int = 50) -> List[dict]:
    sid = _norm(sales_delivery_id)
    if not sid:
        return []
    cursor = (
        db.sales_invoices.find(
            {
                "$or": [
                    {"source_sj_ids": sid},
                    {"lines.source_sj_id": sid},
                ],
                BILLING_COMMIT_PENDING_FIELD: {"$ne": True},
            },
            {
                "_id": 0,
                "id": 1,
                "invoice_number": 1,
                "source_mode": 1,
                "financial_status": 1,
                "payment_status": 1,
                "total_qty": 1,
                "grand_total": 1,
                "created_at": 1,
            },
        )
        .sort("created_at", -1)
        .limit(max(1, min(int(limit), 100)))
    )
    rows = await cursor.to_list(limit)
    out = []
    for inv in rows:
        if not isinstance(inv, dict):
            continue
        out.append({
            "id": _norm(inv.get("id")),
            "invoice_number": _norm(inv.get("invoice_number") or inv.get("id")),
            "source_mode": _norm(inv.get("source_mode") or "sj").lower() or "sj",
            "financial_status": _norm(inv.get("financial_status") or "draft").lower() or "draft",
            "payment_status": _norm(inv.get("payment_status") or "BELUM DIBAYAR").upper(),
            "total_qty": _as_int(inv.get("total_qty")),
            "grand_total": inv.get("grand_total"),
            "created_at": inv.get("created_at"),
        })
    return out


async def build_po_document_relations(db, order: dict) -> dict:
    """Compact relation + qty strip for PO detail (read-only)."""
    if not isinstance(order, dict):
        return {"sales_deliveries": [], "invoices": [], "qty_summary": {}}
    po_id = _norm(order.get("id"))
    deliveries = await related_deliveries_for_po(db, po_id)
    invoices = await related_invoices_for_po(db, po_id)

    # Enrich active SJ rows with slice loading/POD for this PO only (capped).
    active_ids = [d["id"] for d in deliveries if d.get("id") and not d.get("is_cancelled")][:20]
    slice_by_sj: Dict[str, dict] = {}
    if active_ids:
        docs = await db.sales_deliveries.find(
            {"id": {"$in": active_ids}},
            {"_id": 0, "id": 1, "sj_number": 1, "source_allocations": 1, "line_items": 1, "source_po_ids": 1, "po_id": 1},
        ).to_list(len(active_ids))
        order_cache = {po_id: order}
        for doc in docs:
            sid = _norm(doc.get("id"))
            # Only need this PO's order for slice of interest; assess still needs all sources for AND.
            source_ids = delivery_source_po_ids(doc)
            orders_by_id = dict(order_cache)
            for oid in source_ids:
                if oid not in orders_by_id:
                    o = await db.orders.find_one(
                        {"id": oid},
                        {
                            "_id": 0,
                            "id": 1,
                            "shipments": 1,
                            "loading_confirmations": 1,
                            "delivery_confirmations": 1,
                            "qty_delivered": 1,
                        },
                    )
                    if o:
                        orders_by_id[oid] = o
            slice_info = assess_multi_source_slice_fulfillment(doc, orders_by_id)
            mine = next(
                (s for s in (slice_info.get("sources") or []) if _norm(s.get("source_po_id")) == po_id),
                None,
            )
            slice_by_sj[sid] = {
                "loading_complete": bool((mine or {}).get("loading_complete")),
                "delivered": bool((mine or {}).get("delivered")),
                "loading_qty": _as_int((mine or {}).get("loading_qty")),
                "delivered_qty": _as_int((mine or {}).get("delivered_qty")),
                "slice_qty": _as_int((mine or {}).get("slice_qty")),
                "sj_loading_complete": bool(slice_info.get("loading_complete")),
                "sj_pod_complete": bool(slice_info.get("pod_complete")),
                "sj_complete": bool(slice_info.get("complete")),
            }

    for row in deliveries:
        extra = slice_by_sj.get(row["id"]) or {}
        row.update(extra)

    ordered = 0
    for li in order.get("line_items") or []:
        if isinstance(li, dict):
            ordered += _as_int(li.get("quantity") if li.get("quantity") is not None else li.get("qty"))

    sd = order.get("sales_delivery_summary") or {}
    shipped = _as_int(sd.get("total_shipped"))
    remaining_delivery = _as_int(sd.get("total_remaining"), default=max(0, ordered - shipped))

    loaded = 0
    for conf in order.get("loading_confirmations") or []:
        if not isinstance(conf, dict):
            continue
        if conf.get("cancelled") or conf.get("is_cancelled"):
            continue
        for ln in conf.get("lines") or []:
            if isinstance(ln, dict):
                loaded += _as_int(ln.get("qty"))

    delivered = _as_int(order.get("qty_delivered"))
    invoiced = sum(
        r["po_qty_on_invoice"]
        for r in invoices
        if r.get("financial_status") != "cancelled"
    )
    inv_summary = order.get("invoiceable_summary") or {}
    remaining_invoiceable = inv_summary.get("remaining_invoice_qty")
    if remaining_invoiceable is None:
        remaining_invoiceable = max(0, ordered - invoiced)

    return {
        "sales_deliveries": deliveries,
        "invoices": invoices,
        "qty_summary": {
            "ordered": ordered,
            "shipped": shipped,
            "loaded": loaded,
            "delivered": delivered,
            "invoiced": invoiced,
            "remaining_delivery": remaining_delivery,
            "remaining_invoiceable": _as_int(remaining_invoiceable),
        },
    }


def build_relation_history_events(
    *,
    order: Optional[dict] = None,
    delivery: Optional[dict] = None,
    invoice: Optional[dict] = None,
) -> List[dict]:
    """Assemble read-only events from stored timestamps only — never invent times."""
    events: List[dict] = []

    if isinstance(order, dict):
        for t in order.get("timeline") or []:
            if not isinstance(t, dict):
                continue
            ts = t.get("at") or t.get("timestamp") or t.get("created_at")
            if not ts:
                continue
            events.append({
                "at": ts,
                "kind": "timeline",
                "label": _norm(t.get("note") or f"Stage {t.get('stage')}"),
                "actor": _norm((t.get("by") or {}).get("name") if isinstance(t.get("by"), dict) else t.get("by")),
                "source": "order.timeline",
            })
        for conf in order.get("loading_confirmations") or []:
            if not isinstance(conf, dict):
                continue
            ts = conf.get("confirmed_at") or conf.get("created_at")
            if not ts:
                continue
            events.append({
                "at": ts,
                "kind": "loading",
                "label": f"Loading confirmed ({_norm(conf.get('surat_jalan_name') or conf.get('surat_jalan_id') or 'SJ')})",
                "actor": _norm((conf.get("confirmed_by") or {}).get("name") if isinstance(conf.get("confirmed_by"), dict) else conf.get("confirmed_by")),
                "source": "order.loading_confirmations",
            })
        for conf in order.get("delivery_confirmations") or []:
            if not isinstance(conf, dict):
                continue
            if conf.get("is_cancelled") or conf.get("cancelled"):
                continue
            ts = conf.get("confirmed_at") or conf.get("created_at")
            if not ts:
                continue
            events.append({
                "at": ts,
                "kind": "pod",
                "label": f"POD confirmed (qty +{_as_int(conf.get('qty'))})",
                "actor": _norm(conf.get("confirmed_by_id") or conf.get("confirmed_by")),
                "source": "order.delivery_confirmations",
            })

    if isinstance(delivery, dict):
        if delivery.get("created_at"):
            events.append({
                "at": delivery.get("created_at"),
                "kind": "sj_created",
                "label": f"SJ {_norm(delivery.get('sj_number') or delivery.get('id'))} dibuat",
                "actor": _norm(delivery.get("created_by")),
                "source": "sales_deliveries",
            })
        if delivery.get("cancelled_at") or (delivery.get("is_cancelled") and delivery.get("updated_at")):
            events.append({
                "at": delivery.get("cancelled_at") or delivery.get("updated_at"),
                "kind": "sj_cancelled",
                "label": f"SJ {_norm(delivery.get('sj_number') or delivery.get('id'))} dibatalkan",
                "actor": "",
                "source": "sales_deliveries",
            })

    if isinstance(invoice, dict):
        if invoice.get("created_at"):
            events.append({
                "at": invoice.get("created_at"),
                "kind": "invoice_created",
                "label": f"Faktur {_norm(invoice.get('invoice_number') or invoice.get('id'))} dibuat (draft)",
                "actor": _norm((invoice.get("created_by") or {}).get("name") if isinstance(invoice.get("created_by"), dict) else ""),
                "source": "sales_invoices",
            })
        if invoice.get("finalized_at"):
            events.append({
                "at": invoice.get("finalized_at"),
                "kind": "invoice_finalized",
                "label": f"Faktur {_norm(invoice.get('invoice_number'))} difinalisasi",
                "actor": _norm((invoice.get("finalized_by") or {}).get("name") if isinstance(invoice.get("finalized_by"), dict) else ""),
                "source": "sales_invoices",
            })
        if invoice.get("cancelled_at"):
            events.append({
                "at": invoice.get("cancelled_at"),
                "kind": "invoice_cancelled",
                "label": f"Faktur {_norm(invoice.get('invoice_number'))} dibatalkan",
                "actor": _norm((invoice.get("cancelled_by") or {}).get("name") if isinstance(invoice.get("cancelled_by"), dict) else ""),
                "source": "sales_invoices",
            })

    events.sort(key=lambda e: str(e.get("at") or ""))
    return events
