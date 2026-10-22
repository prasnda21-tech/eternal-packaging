"""Sales Receipt (Penerimaan Penjualan) Service & Workflow Payment Integration.

Manages backend-authoritative sales receipts linked to exact Sales Invoices (Faktur Penjualan),
enforces overpayment protection, computes cumulative invoice payment totals, derives
DP / LUNAS status, and performs automated Payment Stage & Approval Payment resolution
in the production workflow.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from workflow_gates import sync_workflow_stage, design_ok, payment_ok
from services.sales_receipt_payment_proof import (
    parse_proof_payload,
    proof_from_receipt,
    public_proof,
    soft_delete_proof_for_receipt,
    store_payment_proof,
)
from services.sales_receipt_posting import (
    CLAIM_FIELD,
    DELETE_CLAIM_FIELD,
    DELETE_IN_PROGRESS_DELETE_MESSAGE,
    POSTED_DELETE_MESSAGE,
    POSTING_IN_PROGRESS_DELETE_MESSAGE,
    REVERSED_DELETE_MESSAGE,
    _claim_absent_clause,
    acquire_delete_claim,
    attach_receipt_accounting_fields,
    delete_claim_absent_or_stale_clause,
    delete_claim_is_active,
    find_sales_receipt_source_journal,
    posting_claim_is_active,
    receipt_counts_toward_outstanding,
    receipt_is_cancelled,
    release_delete_claims,
    resolve_cash_bank_for_create,
)

_receipt_locks: Dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _get_receipt_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _receipt_locks:
        _receipt_locks[loop] = asyncio.Lock()
    return _receipt_locks[loop]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_base_receipt_number(po_number: str) -> str:
    """Derives base receipt number from PO number while preserving main sequence."""
    pn = (po_number or "").strip()
    if not pn:
        return "PAY/" + datetime.now(timezone.utc).strftime("%d/%m/%y/001")

    # 1. Match PO/04/09/26/001 or SO/04/09/26/001 -> PAY/04/09/26/001
    # 2. Match PO/2026/08/30/000123 or SO/2026/08/30/000123 -> PAY/2026/08/30/000123
    # 3. Match PO-20260830-001 or SO-20260830-001 -> PAY-20260830-001
    # 4. Match PO.2026.08.001 -> PAY.2026.08.001
    # 5. Match PO 12345 -> PAY 12345
    # 6. Match PO12345 -> PAY12345
    m = re.match(r'^(?:PO|SO)([\/\.\-\s_]?)(.*)$', pn, re.IGNORECASE)
    if m:
        sep = m.group(1)
        rest = m.group(2)
        return f"PAY{sep}{rest}"

    # Other alphabetic prefixes with separator
    m2 = re.match(r'^[A-Za-z]+([\/\.\-\s_])(.*)$', pn)
    if m2:
        sep = m2.group(1)
        rest = m2.group(2)
        return f"PAY{sep}{rest}"

    return f"PAY-{pn}"


def derive_receipt_number(po_number: str, receipt_index: int) -> str:
    """Computes backend-authoritative receipt number for a specific receipt index.

    Index 1: base PAY (e.g. PAY/04/09/26/001 or PAY-20260830-001)
    Index 2: base PAY + "_2" (e.g. PAY/04/09/26/001_2)
    Index 3: base PAY + "_3" (e.g. PAY/04/09/26/001_3)
    """
    base = derive_base_receipt_number(po_number)
    if receipt_index <= 1:
        return base
    return f"{base}_{receipt_index}"


def _parse_non_negative_amount(raw: Any, field_name: str = "Jumlah pembayaran") -> float:
    if raw is None or raw == "":
        raise HTTPException(400, f"{field_name} wajib diisi.")
    try:
        d = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field_name} '{raw}' tidak valid. Format harus angka.")

    if d <= Decimal("0"):
        raise HTTPException(400, f"{field_name} harus lebih besar dari 0.")

    return float(d)


_RECEIPT_SCOPES = frozenset({"active", "cancelled", "all", "completed", "pending"})
_RECEIPT_CANCELLED_STATUS_VALUES = ["cancelled", "Cancelled", "CANCELLED", "reversed", "Reversed"]
_RECEIPT_PAGE_SIZE_MAX = 100
_RECEIPT_LIST_BATCH = 250


def _receipt_scope_clause(
    scope: str, *, incomplete_cancel_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Tab Mongo prefilter with incomplete-cancel holdouts kept on Aktif."""
    hold_ids = [str(i) for i in (incomplete_cancel_ids or []) if i]
    cancelled = {
        "$or": [
            {"financial_status": {"$in": _RECEIPT_CANCELLED_STATUS_VALUES}},
            {"cancelled": True},
        ]
    }
    if scope == "cancelled":
        if hold_ids:
            return {"$and": [cancelled, {"id": {"$nin": hold_ids}}]}
        return cancelled
    if scope == "all":
        return {}
    active = {
        "$and": [
            {"financial_status": {"$nin": _RECEIPT_CANCELLED_STATUS_VALUES}},
            {"cancelled": {"$ne": True}},
        ]
    }
    if hold_ids:
        return {"$or": [active, {"id": {"$in": hold_ids}}]}
    return active


async def list_sales_receipts(
    db,
    q: Optional[str] = None,
    invoice_id: Optional[str] = None,
    po_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List sales receipts with search and relation filters."""
    filters: Dict[str, Any] = {}
    if invoice_id:
        filters["invoice_id"] = str(invoice_id)
    if po_id:
        filters["po_id"] = str(po_id)
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"id": {"$regex": needle, "$options": "i"}},
            {"receipt_number": {"$regex": needle, "$options": "i"}},
            {"invoice_id": {"$regex": needle, "$options": "i"}},
            {"invoice_number": {"$regex": needle, "$options": "i"}},
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"customer": {"$regex": needle, "$options": "i"}},
        ]

    from services.sales_list_tabs import LIST_SORT

    cursor = db.sales_receipts.find(filters, {"_id": 0}).sort(LIST_SORT)
    receipts = await cursor.to_list(1000)
    public = await attach_receipt_accounting_fields(db, [_public_receipt(r) for r in receipts])
    from services.sales_business_cancel import attach_receipt_business_cancel_fields

    return await attach_receipt_business_cancel_fields(db, public)


async def _invoice_outstanding_by_ids(db, invoice_ids: List[str]) -> Dict[str, float]:
    """Batch live outstanding for linked invoices (receipts + credit)."""
    ids = [str(i) for i in invoice_ids if i]
    if not ids:
        return {}
    from services.sales_customer_credit import invoice_credit_applied_totals_by_invoice_ids
    from services.sales_invoices import apply_invoice_payment_fields, effective_financial_status

    invoices = await db.sales_invoices.find(
        {"id": {"$in": ids}},
        {"_id": 0, "id": 1, "grand_total": 1, "financial_status": 1},
    ).to_list(len(ids))
    by_id = {str(inv.get("id")): inv for inv in invoices if inv.get("id")}
    receipt_cap = max(5000, len(ids) * 50)
    receipts = await db.sales_receipts.find(
        {"invoice_id": {"$in": ids}}, {"_id": 0}
    ).to_list(receipt_cap)
    receipts_by_inv: Dict[str, List[dict]] = {}
    for receipt in receipts:
        receipts_by_inv.setdefault(str(receipt.get("invoice_id") or ""), []).append(receipt)
    credit_totals = await invoice_credit_applied_totals_by_invoice_ids(db, ids)
    out: Dict[str, float] = {}
    for inv_id in ids:
        inv = by_id.get(inv_id)
        if not inv:
            continue
        credit = credit_totals.get(inv_id, 0)
        apply_invoice_payment_fields(inv, receipts_by_inv.get(inv_id, []), float(credit))
        # Touch financial_status for cancelled zeroing path inside helper.
        effective_financial_status(inv)
        try:
            out[inv_id] = round(float(inv.get("outstanding_amount") or 0), 2)
        except (TypeError, ValueError):
            out[inv_id] = 0.0
    return out


async def query_sales_receipts(
    db,
    *,
    q: Optional[str] = None,
    invoice_id: Optional[str] = None,
    po_id: Optional[str] = None,
    scope: str = "active",
    list_tab: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """Paged receipt list with five-tab classification.

    Legacy ``scope=active`` remains non-Batal. Exclusive tabs via ``list_tab`` or
    ``scope`` in {completed, pending}.
    """
    from services.sales_list_tabs import (
        DEFAULT_PAGE_SIZE,
        LIST_HARD_MAX,
        TAB_ACTIVE,
        TAB_ALL,
        TAB_CANCELLED,
        TAB_COMPLETED,
        TAB_PENDING,
        bump_tab_count,
        classify_sales_receipt_list_tab,
        empty_tab_counts,
        legacy_invoice_scope_includes,
        normalize_list_tab,
        parse_page_params,
    )

    scope_key = str(scope or "active").strip().lower()
    if scope_key not in _RECEIPT_SCOPES:
        raise HTTPException(
            400,
            "Tab penerimaan tidak valid. Gunakan active, completed, pending, cancelled, atau all.",
        )
    exclusive_tab = None
    if list_tab is not None and str(list_tab).strip() != "":
        try:
            exclusive_tab = normalize_list_tab(list_tab, default=TAB_ACTIVE)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    elif scope_key in {TAB_COMPLETED, TAB_PENDING}:
        exclusive_tab = scope_key

    try:
        page_num, size = parse_page_params(page, page_size if page_size is not None else DEFAULT_PAGE_SIZE)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if size > _RECEIPT_PAGE_SIZE_MAX:
        raise HTTPException(400, "Halaman atau ukuran halaman tidak valid.")

    from services.sales_business_cancel import (
        DOC_RECEIPT,
        attach_receipt_business_cancel_fields,
        incomplete_or_failed_cancel_document_ids,
    )

    hold_ids = sorted(await incomplete_or_failed_cancel_document_ids(db, DOC_RECEIPT))
    mongo_scope = "all" if exclusive_tab or scope_key in {TAB_COMPLETED, TAB_PENDING, TAB_ALL} else (
        "cancelled" if scope_key == TAB_CANCELLED else "active"
    )

    clauses: List[Dict[str, Any]] = []
    scope_clause = _receipt_scope_clause(mongo_scope, incomplete_cancel_ids=hold_ids)
    if scope_clause:
        clauses.append(scope_clause)
    if invoice_id:
        clauses.append({"invoice_id": str(invoice_id)})
    if po_id:
        clauses.append({"po_id": str(po_id)})
    if q and str(q).strip():
        needle = str(q).strip()
        clauses.append({
            "$or": [
                {"id": {"$regex": needle, "$options": "i"}},
                {"receipt_number": {"$regex": needle, "$options": "i"}},
                {"invoice_id": {"$regex": needle, "$options": "i"}},
                {"invoice_number": {"$regex": needle, "$options": "i"}},
                {"po_number": {"$regex": needle, "$options": "i"}},
                {"customer": {"$regex": needle, "$options": "i"}},
            ]
        })

    from services.sales_list_tabs import LIST_SORT

    find_filter: Dict[str, Any] = {"$and": clauses} if clauses else {}
    cursor = db.sales_receipts.find(find_filter, {"_id": 0}).sort(LIST_SORT)

    tab_counts = empty_tab_counts()
    filtered: List[Dict[str, Any]] = []
    scanned = 0
    batch: List[Dict[str, Any]] = []

    async def _flush(batch_rows: List[Dict[str, Any]]) -> None:
        nonlocal scanned
        if not batch_rows:
            return
        scanned += len(batch_rows)
        if scanned > LIST_HARD_MAX:
            raise HTTPException(
                409,
                f"Hasil daftar penerimaan melebihi batas aman ({LIST_HARD_MAX}). Perhalus pencarian/filter.",
            )
        public = await attach_receipt_accounting_fields(
            db, [_public_receipt(r) for r in batch_rows]
        )
        public = await attach_receipt_business_cancel_fields(db, public)
        outstanding_by_inv = await _invoice_outstanding_by_ids(
            db, [str(r.get("invoice_id") or "") for r in public if r.get("invoice_id")]
        )
        for rec in public:
            inv_id = str(rec.get("invoice_id") or "")
            inv_outstanding = outstanding_by_inv.get(inv_id)
            rec["invoice_outstanding_amount"] = inv_outstanding
            primary = classify_sales_receipt_list_tab(
                rec, invoice_outstanding=inv_outstanding
            )
            rec["list_tab"] = primary
            bump_tab_count(tab_counts, primary)
            if exclusive_tab is not None:
                if exclusive_tab != TAB_ALL and primary != exclusive_tab:
                    continue
            elif not legacy_invoice_scope_includes(primary, scope_key):
                continue
            filtered.append(rec)

    async for doc in cursor:
        batch.append(doc)
        if len(batch) >= _RECEIPT_LIST_BATCH:
            await _flush(batch)
            batch = []
    await _flush(batch)

    start = (page_num - 1) * size
    page_rows = filtered[start:start + size]
    from services.po_identity_fields import attach_po_identity_fields
    await attach_po_identity_fields(db, page_rows, po_id_key="po_id")
    if exclusive_tab == TAB_CANCELLED or (exclusive_tab is None and scope_key == TAB_CANCELLED):
        tab_total = tab_counts[TAB_CANCELLED]
    elif exclusive_tab == TAB_COMPLETED:
        tab_total = tab_counts[TAB_COMPLETED]
    elif exclusive_tab == TAB_PENDING:
        tab_total = tab_counts[TAB_PENDING]
    elif exclusive_tab == TAB_ACTIVE:
        tab_total = tab_counts[TAB_ACTIVE]
    elif exclusive_tab == TAB_ALL or scope_key == TAB_ALL:
        tab_total = tab_counts[TAB_ALL]
    else:
        tab_total = tab_counts[TAB_ALL] - tab_counts[TAB_CANCELLED]
    legacy_active = tab_counts[TAB_ALL] - tab_counts[TAB_CANCELLED]
    return {
        "receipts": page_rows,
        "count": len(page_rows),
        "total": len(filtered),
        "tab_total": tab_total,
        "active_total": legacy_active,
        "completed_total": tab_counts[TAB_COMPLETED],
        "pending_total": tab_counts[TAB_PENDING],
        "cancelled_total": tab_counts[TAB_CANCELLED],
        "all_total": tab_counts[TAB_ALL],
        "five_tab_active_total": tab_counts[TAB_ACTIVE],
        "page": page_num,
        "page_size": size,
        "list_tab": exclusive_tab,
        "scope": scope_key,
    }


def _public_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """Strip any non-public proof fields from a receipt document."""
    if not isinstance(receipt, dict):
        return receipt
    out = dict(receipt)
    proof = proof_from_receipt(out)
    if proof:
        out["payment_proof"] = proof
        out["has_payment_proof"] = True
    else:
        out.pop("payment_proof", None)
        out["has_payment_proof"] = False
    return out


async def get_sales_receipt_detail(db, receipt_id: str) -> Dict[str, Any]:
    """Fetch detail of a single sales receipt."""
    r = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not r:
        r = await db.sales_receipts.find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
    if not r:
        raise HTTPException(404, f"Penerimaan Penjualan '{receipt_id}' tidak ditemukan.")
    attached = await attach_receipt_accounting_fields(db, [_public_receipt(r)])
    from services.sales_customer_credit import list_applications_for_receipt
    from services.sales_receipt_edit_revision import receipt_edit_capability

    attached[0]["customer_credit_history"] = await list_applications_for_receipt(db, attached[0]["id"])
    # Safe Edit capability — no secrets/allowlist dump; permission checked separately.
    attached[0]["edit_capability"] = await receipt_edit_capability(db, attached[0])
    return attached[0]


async def get_sales_receipt_detail_for_user(db, receipt_id: str, user: Optional[dict]) -> Dict[str, Any]:
    """Detail plus read-only lifecycle capability for the authenticated user."""
    receipt = await get_sales_receipt_detail(db, receipt_id)
    from services.sales_lifecycle_capability import build_receipt_lifecycle_capability

    receipt["lifecycle_capability"] = await build_receipt_lifecycle_capability(db, user, receipt)
    return receipt


async def create_sales_receipt(
    db,
    payload: Dict[str, Any],
    user: dict,
    *,
    claim_token: Optional[str] = None,
    defer_operational_settlement: bool = False,
    require_posted_invoice_journal: bool = False,
) -> Dict[str, Any]:
    """Creates a new Sales Receipt (Penerimaan Penjualan) backend-authoritatively.

    Guarantees:
    - Linked to exact invoice_id (preserving receipt -> invoice_id -> po_id relation)
    - Validates amount > 0 and amount <= outstanding_amount (overpayment protection)
    - Computes cumulative paid amount and derives payment state (DP / LUNAS)
    - Idempotency & double-submit protection
    - Automated workflow resolution for Payment & Approval Payment stage on PO:
      - If PO prerequisite stages (Design) are resolved, advances to Penjadwalan
      - If PO prerequisite stages (Design) are not yet resolved, records payment fact safely without skipping stages

    When ``defer_operational_settlement`` is True (auto-lifecycle path):
    - Persists receipt with settlement_effective=false
    - Does NOT mutate invoice paid/outstanding or PO Approval Payment
    - Caller must post journal then apply settlement via apply_operational_settlement_for_receipt
    """
    invoice_id = str(payload.get("invoice_id") or "").strip()
    if not invoice_id:
        raise HTTPException(400, "ID Faktur Penjualan (invoice_id) wajib diisi.")

    # Validate proof payload early so invalid files never create a receipt / payment effect.
    proof_input = parse_proof_payload(payload.get("payment_proof"))

    async with _get_receipt_lock():
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if idempotency_key:
            existing_by_key = await db.sales_receipts.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
            if existing_by_key:
                # Idempotent replay: if original request included proof and receipt still lacks it, attach once.
                if proof_input and not proof_from_receipt(existing_by_key):
                    try:
                        proof_meta = await store_payment_proof(
                            db,
                            receipt=existing_by_key,
                            user=user,
                            filename=proof_input["name"],
                            data_url=proof_input["data_url"],
                            uploaded_at=now_iso(),
                        )
                        await db.sales_receipts.update_one(
                            {"id": existing_by_key["id"]},
                            {"$set": {"payment_proof": proof_meta}},
                        )
                        existing_by_key["payment_proof"] = proof_meta
                        if receipt_counts_toward_outstanding(existing_by_key):
                            await _patch_order_payment_receipt_proof(db, existing_by_key, proof_meta)
                    except HTTPException:
                        raise
                attached = await attach_receipt_accounting_fields(db, [_public_receipt(existing_by_key)])
                return attached[0]

        # 1. Fetch exact invoice
        invoice = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not invoice:
            invoice = await db.sales_invoices.find_one({"invoice_number": invoice_id}, {"_id": 0})
        if not invoice:
            raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

        from services.sales_invoice_posting import (
            INVOICE_REVERSE_IN_PROGRESS_RECEIPT_CREATE_MESSAGE,
            RECEIPT_CREATE_CLAIM_CONFLICT_MESSAGE,
            acquire_receipt_create_claim,
            clear_receipt_create_claim,
            receipt_create_claim_is_active,
            reverse_claim_is_active,
        )
        from services.sales_invoices import FINANCIAL_CANCELLED, FINANCIAL_FINALIZED, effective_financial_status

        invoice_status = effective_financial_status(invoice)
        if invoice_status == FINANCIAL_CANCELLED:
            raise HTTPException(409, "Faktur yang dibatalkan tidak dapat menerima Penerimaan Penjualan.")
        if invoice_status != FINANCIAL_FINALIZED:
            raise HTTPException(409, "Hanya faktur finalized yang dapat menerima Penerimaan Penjualan.")
        if reverse_claim_is_active(invoice):
            raise HTTPException(409, INVOICE_REVERSE_IN_PROGRESS_RECEIPT_CREATE_MESSAGE)

        # Gate A / auto-lifecycle: require posted invoice journal (flag-driven unless forced).
        # Helper/import failure fails closed — never silently skip a required gate.
        gate_required = require_posted_invoice_journal
        try:
            from services.sales_receipt_auto_lifecycle import (
                is_auto_sales_receipt_lifecycle_enabled,
                is_receipt_create_requires_invoice_journal_posted,
            )

            if (
                await is_receipt_create_requires_invoice_journal_posted(db)
                or await is_auto_sales_receipt_lifecycle_enabled(db)
            ):
                gate_required = True
        except Exception as exc:
            raise HTTPException(
                409,
                "Dependency gate jurnal faktur untuk penerimaan tidak dapat diverifikasi.",
            ) from exc

        from services.financial_statement_common import MONEY_Q, money_float
        from services.sales_customer_credit import (
            IDENTITY_BLOCK_REASON,
            effective_currency,
            scope_from_invoice,
            split_receipt_amounts,
            stable_customer_for_invoice,
        )
        from services.sales_receipt_posting import receipt_allocated_amount

        amount_dec = Decimal(str(_parse_non_negative_amount(payload.get("amount"), "Jumlah penerimaan"))).quantize(MONEY_Q)
        amount = money_float(amount_dec)
        create_claim_token = str(claim_token or uuid.uuid4())
        claimed = await acquire_receipt_create_claim(db, invoice["id"], token=create_claim_token)
        if claimed is None:
            current = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or invoice
            if reverse_claim_is_active(current):
                raise HTTPException(409, INVOICE_REVERSE_IN_PROGRESS_RECEIPT_CREATE_MESSAGE)
            if effective_financial_status(current) == FINANCIAL_CANCELLED:
                raise HTTPException(409, "Faktur yang dibatalkan tidak dapat menerima Penerimaan Penjualan.")
            if effective_financial_status(current) != FINANCIAL_FINALIZED:
                raise HTTPException(409, "Hanya faktur finalized yang dapat menerima Penerimaan Penjualan.")
            if receipt_create_claim_is_active(current):
                raise HTTPException(409, RECEIPT_CREATE_CLAIM_CONFLICT_MESSAGE)
            raise HTTPException(409, RECEIPT_CREATE_CLAIM_CONFLICT_MESSAGE)

        invoice = claimed
        try:
            invoice_status = effective_financial_status(invoice)
            if invoice_status != FINANCIAL_FINALIZED:
                raise HTTPException(409, "Hanya faktur finalized yang dapat menerima Penerimaan Penjualan.")
            if reverse_claim_is_active(invoice):
                raise HTTPException(409, INVOICE_REVERSE_IN_PROGRESS_RECEIPT_CREATE_MESSAGE)

            # Revalidate invoice journal under the create claim (blocks concurrent reverse via mutual claim).
            if gate_required:
                try:
                    from services.sales_receipt_posting import assert_invoice_allows_receipt_journal

                    await assert_invoice_allows_receipt_journal(db, invoice["id"])
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        409,
                        "Pemeriksaan jurnal faktur untuk penerimaan tidak dapat dijalankan.",
                    ) from exc

            grand_total = float(invoice.get("grand_total", 0))
            cash_bank_id = str(payload.get("cash_bank_id") or "").strip()
            cash_snap = await resolve_cash_bank_for_create(db, cash_bank_id) if cash_bank_id else {}

            existing_receipts = await db.sales_receipts.find({"invoice_id": invoice["id"]}, {"_id": 0}).to_list(500)
            from services.sales_customer_credit import invoice_credit_applied_total

            paid_total_before = money_float(
                sum(
                    (receipt_allocated_amount(r) for r in existing_receipts if receipt_counts_toward_outstanding(r)),
                    Decimal("0.00"),
                ).quantize(MONEY_Q)
                + await invoice_credit_applied_total(db, invoice["id"])
            )
            outstanding_before = money_float(
                max(Decimal(str(grand_total)).quantize(MONEY_Q) - Decimal(str(paid_total_before)).quantize(MONEY_Q), Decimal("0.00"))
            )
            allocated_dec, credit_dec = split_receipt_amounts(
                amount_dec,
                Decimal(str(outstanding_before)).quantize(MONEY_Q),
                payload.get("allocated_amount", None) if "allocated_amount" in payload else None,
            )
            customer_id = await stable_customer_for_invoice(db, invoice)
            scope = await scope_from_invoice(db, invoice)
            if credit_dec > 0 and not customer_id:
                raise HTTPException(400, IDENTITY_BLOCK_REASON)
            allocated_amount = money_float(allocated_dec)
            customer_credit_amount = money_float(credit_dec)

            new_paid_total = money_float(
                (Decimal(str(paid_total_before)) + allocated_dec).quantize(MONEY_Q)
            )
            new_outstanding = money_float(
                max(Decimal(str(grand_total)).quantize(MONEY_Q) - Decimal(str(new_paid_total)).quantize(MONEY_Q), Decimal("0.00"))
            )
            payment_state = "LUNAS" if Decimal(str(new_paid_total)) >= Decimal(str(grand_total)).quantize(MONEY_Q) else "DP"

            receipt_id = str(uuid.uuid4())
            receipt_date = str(payload.get("receipt_date") or now_iso()[:10]).strip()

            existing_receipts_count = await db.sales_receipts.count_documents({"po_id": invoice["po_id"]})
            receipt_index = existing_receipts_count + 1
            candidate_receipt_number = derive_receipt_number(invoice["po_number"], receipt_index)
            while await db.sales_receipts.find_one({"receipt_number": candidate_receipt_number}, {"_id": 1}):
                receipt_index += 1
                candidate_receipt_number = derive_receipt_number(invoice["po_number"], receipt_index)
            receipt_number = candidate_receipt_number

            receipt_doc = {
                "id": receipt_id,
                "receipt_number": receipt_number,
                "receipt_index": receipt_index,
                "idempotency_key": idempotency_key or None,
                "invoice_id": invoice["id"],
                "invoice_number": invoice.get("invoice_number", invoice["id"]),
                "po_id": invoice["po_id"],
                "po_number": invoice["po_number"],
                "customer": invoice["customer"],
                "customer_id": customer_id,
                "branch_id": scope.get("branch_id") or None,
                "currency": effective_currency(scope.get("currency")),
                "receipt_date": receipt_date,
                "amount": amount,
                "allocated_amount": allocated_amount,
                "customer_credit_amount": customer_credit_amount,
                "customer_credit_applied": 0,
                "customer_credit_block_reason": None if customer_id or customer_credit_amount <= 0 else IDENTITY_BLOCK_REASON,
                "payment_state": payment_state,
                "notes": str(payload.get("notes") or "").strip(),
                "financial_status": "active",
                **cash_snap,
                "created_at": now_iso(),
                "created_by": {
                    "id": str(user.get("id") or user.get("_id") or ""),
                    "name": str(user.get("name") or user.get("email") or ""),
                    "role": str(user.get("role") or ""),
                },
            }
            if defer_operational_settlement:
                # Auto-lifecycle: not confirmed paid until journal + settlement CAS.
                receipt_doc["settlement_effective"] = False

            try:
                await db.sales_receipts.insert_one(receipt_doc)
            except DuplicateKeyError:
                if idempotency_key:
                    existing = await db.sales_receipts.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
                    if existing:
                        attached = await attach_receipt_accounting_fields(db, [_public_receipt(existing)])
                        return attached[0]
                raise HTTPException(409, "Penerimaan Penjualan duplikat terdeteksi.")

            receipt_doc.pop("_id", None)

            if not defer_operational_settlement:
                await db.sales_invoices.update_one(
                    {"id": invoice["id"]},
                    {
                        "$set": {
                            "paid_total": new_paid_total,
                            "outstanding_amount": new_outstanding,
                            "payment_status": payment_state,
                            "updated_at": now_iso(),
                        }
                    }
                )

                po_id = invoice.get("po_id")
                if po_id:
                    order = await db.orders.find_one({"id": str(po_id)})
                    if not order:
                        order = await db.orders.find_one({"po_number": str(po_id)})

                    if order and not order.get("cancelled"):
                        order.setdefault("payment_receipts", []).append({
                            "receipt_id": receipt_id,
                            "invoice_id": invoice["id"],
                            "amount": amount,
                            "allocated_amount": allocated_amount,
                            "customer_credit_amount": customer_credit_amount,
                            "payment_state": payment_state,
                            "receipt_number": receipt_number,
                            "receipt_date": receipt_date,
                            "at": now_iso(),
                        })
                        order["payment_fact"] = {
                            "receipt_id": receipt_id,
                            "invoice_id": invoice["id"],
                            "amount": amount,
                            "allocated_amount": allocated_amount,
                            "payment_state": payment_state,
                            "updated_at": now_iso(),
                        }

                        if order.get("payment_status") != "Approved":
                            order["payment_status"] = "Approved"
                            order["payment_approved_by"] = "system"
                            order["payment_approved_by_name"] = "Penerimaan Penjualan"
                            order["payment_approved_at"] = now_iso()
                            order.setdefault("payment_approval_history", []).append({
                                "action": "approve",
                                "by_email": "system",
                                "by_name": "Penerimaan Penjualan",
                                "at": now_iso(),
                                "note": f"Otomatis disetujui dari Penerimaan Penjualan #{receipt_id} ({payment_state})",
                                "receipt_id": receipt_id,
                                "payment_state": payment_state,
                                "amount_received": amount,
                            })

                            timeline_entry = {
                                "stage": 4,
                                "stage_label": "Approval Payment",
                                "at": now_iso(),
                                "by_email": "system",
                                "by_role": "system",
                                "by_name": "Penerimaan Penjualan",
                                "note": f"Approval Payment otomatis disetujui ({payment_state}) via Penerimaan #{receipt_id}",
                            }
                            order.setdefault("timeline", []).append(timeline_entry)
                            sync_workflow_stage(order)

                        await db.orders.replace_one({"id": order["id"]}, order)

            if proof_input:
                try:
                    proof_meta = await store_payment_proof(
                        db,
                        receipt=receipt_doc,
                        user=user,
                        filename=proof_input["name"],
                        data_url=proof_input["data_url"],
                        uploaded_at=now_iso(),
                    )
                except HTTPException:
                    await db.sales_receipts.delete_one({"id": receipt_id})
                    await _recalc_invoice_payment_from_receipts(db, invoice["id"])
                    await _recalc_order_payment_facts_after_receipt_delete(db, invoice.get("po_id"), [receipt_id])
                    raise
                await db.sales_receipts.update_one(
                    {"id": receipt_id},
                    {"$set": {"payment_proof": proof_meta}},
                )
                receipt_doc["payment_proof"] = proof_meta
                if receipt_counts_toward_outstanding(receipt_doc):
                    await _patch_order_payment_receipt_proof(db, receipt_doc, proof_meta)

            attached = await attach_receipt_accounting_fields(db, [_public_receipt(receipt_doc)])
            return attached[0]
        finally:
            await clear_receipt_create_claim(db, invoice["id"], token=create_claim_token)


def _approval_history_has_receipt(history: Any, receipt_id: str) -> bool:
    rid = str(receipt_id or "").strip()
    if not rid:
        return False
    for row in history or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("receipt_id") or "").strip() == rid:
            return True
    return False


def _timeline_has_receipt_approval(timeline: Any, receipt_id: str) -> bool:
    rid = str(receipt_id or "").strip()
    if not rid:
        return False
    marker = f"#{rid}"
    for row in timeline or []:
        if not isinstance(row, dict):
            continue
        note = str(row.get("note") or "")
        if marker in note and "Approval Payment" in str(row.get("stage_label") or note):
            return True
        if str(row.get("receipt_id") or "").strip() == rid:
            return True
    return False


async def sync_order_payment_projections(db, po_id: str) -> None:
    """Rebuild PO payment_receipts / payment_fact from settlement-effective receipts.

    Aggregate Approval Payment: one transition to Approved per PO.
    History/timeline appends are idempotent per receipt_id (no blind duplicate).
    """
    if not po_id:
        return
    order = await db.orders.find_one({"id": str(po_id)})
    if not order:
        order = await db.orders.find_one({"po_number": str(po_id)})
    if not order or order.get("cancelled"):
        return

    from services.sales_receipt_posting import receipt_allocated_amount

    effective = [
        r
        for r in await db.sales_receipts.find({"po_id": order["id"]}, {"_id": 0}).sort("created_at", 1).to_list(500)
        if receipt_counts_toward_outstanding(r)
    ]
    prior_by_id = {
        str(row.get("receipt_id") or ""): row
        for row in (order.get("payment_receipts") or [])
        if row.get("receipt_id")
    }
    rebuilt = []
    for rec in effective:
        rid = str(rec.get("id") or "")
        prior = prior_by_id.get(rid) or {}
        entry = {
            "receipt_id": rid,
            "invoice_id": rec.get("invoice_id"),
            "amount": rec.get("amount"),
            "allocated_amount": float(receipt_allocated_amount(rec)),
            "customer_credit_amount": rec.get("customer_credit_amount"),
            "payment_state": rec.get("payment_state"),
            "receipt_number": rec.get("receipt_number"),
            "receipt_date": rec.get("receipt_date"),
            "at": prior.get("at") or rec.get("created_at") or now_iso(),
        }
        if prior.get("payment_proof"):
            entry["payment_proof"] = prior.get("payment_proof")
        rebuilt.append(entry)

    # Always reconcile payment_receipts / payment_fact (idempotent rebuild).
    base_patch: Dict[str, Any] = {"payment_receipts": rebuilt}
    if effective:
        last = effective[-1]
        last_id = str(last.get("id") or "")
        base_patch["payment_fact"] = {
            "receipt_id": last.get("id"),
            "invoice_id": last.get("invoice_id"),
            "amount": last.get("amount"),
            "allocated_amount": float(receipt_allocated_amount(last)),
            "payment_state": last.get("payment_state"),
            "updated_at": now_iso(),
        }
        await db.orders.update_one({"id": order["id"]}, {"$set": base_patch})

        # Aggregate Approval Payment per PO (workflow semantics): one Waiting→Approved transition.
        # Concurrent recover workers: CAS on payment_status; losers skip history/timeline append.
        if order.get("payment_status") != "Approved":
            history = list(order.get("payment_approval_history") or [])
            timeline = list(order.get("timeline") or [])
            if last_id and not _approval_history_has_receipt(history, last_id):
                history = history + [{
                    "action": "approve",
                    "by_email": "system",
                    "by_name": "Penerimaan Penjualan",
                    "at": now_iso(),
                    "note": f"Otomatis disetujui dari Penerimaan Penjualan #{last_id} ({last.get('payment_state')})",
                    "receipt_id": last_id,
                    "payment_state": last.get("payment_state"),
                    "amount_received": last.get("amount"),
                }]
            if last_id and not _timeline_has_receipt_approval(timeline, last_id):
                timeline = timeline + [{
                    "stage": 4,
                    "stage_label": "Approval Payment",
                    "at": now_iso(),
                    "by_email": "system",
                    "by_role": "system",
                    "by_name": "Penerimaan Penjualan",
                    "receipt_id": last_id,
                    "note": f"Approval Payment otomatis disetujui ({last.get('payment_state')}) via Penerimaan #{last_id}",
                }]
            approve_set: Dict[str, Any] = {
                "payment_status": "Approved",
                "payment_approved_by": "system",
                "payment_approved_by_name": "Penerimaan Penjualan",
                "payment_approved_at": now_iso(),
                "payment_approval_history": history,
                "timeline": timeline,
            }
            merged = {**order, **base_patch, **approve_set}
            sync_workflow_stage(merged)
            approve_set["current_stage"] = merged.get("current_stage")
            approve_set["status_label"] = merged.get("status_label")
            if "events" in merged:
                approve_set["events"] = merged.get("events")
            await db.orders.update_one(
                {
                    "id": order["id"],
                    "$or": [
                        {"payment_status": {"$ne": "Approved"}},
                        {"payment_status": {"$exists": False}},
                        {"payment_status": None},
                        {"payment_status": ""},
                    ],
                },
                {"$set": approve_set},
            )
    else:
        base_patch["payment_fact"] = None
        cs = int(order.get("current_stage") or 0)
        if cs < 5 and order.get("payment_approved_by") == "system":
            base_patch["payment_status"] = "Waiting"
            base_patch["payment_approved_by"] = None
            base_patch["payment_approved_by_name"] = None
            base_patch["payment_approved_at"] = None
        await db.orders.update_one({"id": order["id"]}, {"$set": base_patch})


async def expected_invoice_payment_from_receipts(db, invoice_id: str) -> Optional[Dict[str, Any]]:
    """Authoritative paid/outstanding/payment_status from counting receipts (+ credit apps)."""
    if not invoice_id:
        return None
    invoice = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not invoice:
        return None
    from services.financial_statement_common import MONEY_Q, money_float
    from services.sales_customer_credit import invoice_credit_applied_total
    from services.sales_receipt_posting import receipt_allocated_amount

    remaining = await db.sales_receipts.find({"invoice_id": invoice_id}, {"_id": 0}).to_list(500)
    paid_dec = sum(
        (receipt_allocated_amount(r) for r in remaining if receipt_counts_toward_outstanding(r)),
        Decimal("0.00"),
    )
    paid_dec += await invoice_credit_applied_total(db, invoice_id)
    paid_dec = paid_dec.quantize(MONEY_Q)
    paid = money_float(paid_dec)
    grand = float(invoice.get("grand_total", 0))
    outstanding = money_float(max(Decimal(str(grand)).quantize(MONEY_Q) - paid_dec, Decimal("0.00")))
    if paid <= 0:
        p_status = "BELUM DIBAYAR"
    elif paid < grand:
        p_status = "DP"
    else:
        p_status = "LUNAS"
    return {
        "paid_total": paid,
        "outstanding_amount": outstanding,
        "payment_status": p_status,
        "invoice": invoice,
    }


def _money_eq(a: Any, b: Any) -> bool:
    try:
        return abs(Decimal(str(a if a is not None else 0)) - Decimal(str(b if b is not None else 0))) < Decimal("0.005")
    except Exception:
        return False


async def verify_settlement_projections(db, receipt_id: str) -> Dict[str, Any]:
    """Verify journal + settlement_effective + invoice/PO projections. No writes."""
    from services.sales_invoice_posting import ACCOUNTING_POSTED, accounting_status_from_journal
    from services.sales_receipt_posting import SETTLEMENT_EFFECTIVE_FIELD, find_sales_receipt_source_journal

    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        return {"ok": False, "reason": "receipt_missing"}
    if receipt_is_cancelled(rec):
        return {"ok": False, "reason": "cancelled", "receipt": rec}
    journal = await find_sales_receipt_source_journal(db, rec["id"])
    if accounting_status_from_journal(journal) != ACCOUNTING_POSTED:
        return {"ok": False, "reason": "journal_not_posted", "receipt": rec}
    if rec.get(SETTLEMENT_EFFECTIVE_FIELD) is not True:
        return {"ok": False, "reason": "settlement_not_effective", "receipt": rec}

    expected = await expected_invoice_payment_from_receipts(db, rec.get("invoice_id"))
    if not expected:
        return {"ok": False, "reason": "invoice_missing", "receipt": rec}
    inv = expected["invoice"]
    if not _money_eq(inv.get("paid_total"), expected["paid_total"]):
        return {"ok": False, "reason": "invoice_paid_stale", "receipt": rec, "expected": expected}
    if not _money_eq(inv.get("outstanding_amount"), expected["outstanding_amount"]):
        return {"ok": False, "reason": "invoice_outstanding_stale", "receipt": rec, "expected": expected}
    if str(inv.get("payment_status") or "") != expected["payment_status"]:
        return {"ok": False, "reason": "invoice_status_stale", "receipt": rec, "expected": expected}

    po_id = rec.get("po_id")
    if po_id:
        order = await db.orders.find_one({"id": str(po_id)}, {"_id": 0})
        if not order:
            order = await db.orders.find_one({"po_number": str(po_id)}, {"_id": 0})
        if order and not order.get("cancelled"):
            fact = order.get("payment_fact") or {}
            if str(fact.get("receipt_id") or "") != str(rec.get("id") or ""):
                # payment_fact may point at another effective receipt on same PO — ok if ours is in payment_receipts
                pr_ids = {
                    str(row.get("receipt_id") or "")
                    for row in (order.get("payment_receipts") or [])
                    if isinstance(row, dict)
                }
                if str(rec.get("id") or "") not in pr_ids:
                    return {"ok": False, "reason": "po_projection_stale", "receipt": rec}
            if order.get("payment_status") != "Approved":
                return {"ok": False, "reason": "po_not_approved", "receipt": rec}

    return {"ok": True, "receipt": rec, "journal": journal, "expected": expected}


async def apply_operational_settlement_for_receipt(db, receipt_id: str, user: Optional[dict] = None) -> dict:
    """Make settlement effective only after a posted receipt primary journal.

    CAS: settlement_effective false → true. Idempotent if already true.
    Recomputes invoice paid/outstanding from counting receipts, then syncs PO.
    Never settles cancelled receipts. Does not reverse journals.
    """
    from pymongo import ReturnDocument

    from services.sales_invoice_posting import ACCOUNTING_POSTED, accounting_status_from_journal
    from services.sales_receipt_posting import SETTLEMENT_EFFECTIVE_FIELD, find_sales_receipt_source_journal

    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        raise HTTPException(404, f"Penerimaan Penjualan '{receipt_id}' tidak ditemukan.")
    if receipt_is_cancelled(rec):
        raise HTTPException(
            409,
            {
                "error_code": "receipt_cancelled",
                "message": "Penerimaan yang dibatalkan tidak dapat diselesaikan settlement-nya.",
            },
        )

    journal = await find_sales_receipt_source_journal(db, rec["id"])
    if accounting_status_from_journal(journal) != ACCOUNTING_POSTED:
        raise HTTPException(409, "Settlement memerlukan jurnal penerimaan yang sudah diposting.")

    # Re-check cancel after journal read (cancel during recovery).
    fresh = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0}) or rec
    if receipt_is_cancelled(fresh):
        raise HTTPException(
            409,
            {
                "error_code": "receipt_cancelled",
                "message": "Penerimaan dibatalkan selama recovery; settlement tidak diterapkan.",
            },
        )

    now = now_iso()
    if fresh.get(SETTLEMENT_EFFECTIVE_FIELD) is True:
        await _recalc_invoice_payment_from_receipts(db, fresh.get("invoice_id"))
        await sync_order_payment_projections(db, fresh.get("po_id"))
        return await db.sales_receipts.find_one({"id": fresh["id"]}, {"_id": 0}) or fresh

    # Absent field (legacy) already counts — treat as settled for projection sync.
    if SETTLEMENT_EFFECTIVE_FIELD not in fresh:
        await _recalc_invoice_payment_from_receipts(db, fresh.get("invoice_id"))
        await sync_order_payment_projections(db, fresh.get("po_id"))
        return fresh

    from services.sales_business_cancel import (
        receipt_cancel_fence_absent_clause,
        receipt_cancel_fence_is_active,
    )

    claimed = await db.sales_receipts.find_one_and_update(
        {
            "id": fresh["id"],
            SETTLEMENT_EFFECTIVE_FIELD: False,
            "financial_status": {"$nin": ["cancelled", "reversed"]},
            "cancelled": {"$ne": True},
            "$and": [receipt_cancel_fence_absent_clause()],
        },
        {
            "$set": {
                SETTLEMENT_EFFECTIVE_FIELD: True,
                "settlement_effective_at": now,
                "settlement_effective_by": {
                    "id": str((user or {}).get("id") or (user or {}).get("_id") or ""),
                    "name": str((user or {}).get("name") or (user or {}).get("email") or ""),
                },
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        current = await db.sales_receipts.find_one({"id": fresh["id"]}, {"_id": 0}) or fresh
        if receipt_is_cancelled(current):
            raise HTTPException(
                409,
                {
                    "error_code": "receipt_cancelled",
                    "message": "Penerimaan dibatalkan selama recovery; settlement tidak diterapkan.",
                },
            )
        if receipt_cancel_fence_is_active(current):
            raise HTTPException(
                409,
                {
                    "error_code": "receipt_cancel_in_progress",
                    "message": "Pembatalan penerimaan belum selesai; settlement ditolak.",
                },
            )
        if current.get(SETTLEMENT_EFFECTIVE_FIELD) is True:
            await _recalc_invoice_payment_from_receipts(db, current.get("invoice_id"))
            await sync_order_payment_projections(db, current.get("po_id"))
            return current
        raise HTTPException(409, "Settlement penerimaan gagal karena perubahan bersamaan.")

    claimed.pop("_id", None)
    # Cancel race after CAS win: revert effectiveness if cancelled slipped through (should not match filter).
    if receipt_is_cancelled(claimed):
        await db.sales_receipts.update_one(
            {"id": claimed["id"], SETTLEMENT_EFFECTIVE_FIELD: True},
            {"$set": {SETTLEMENT_EFFECTIVE_FIELD: False, "updated_at": now_iso()}},
        )
        raise HTTPException(
            409,
            {
                "error_code": "receipt_cancelled",
                "message": "Penerimaan dibatalkan selama recovery; settlement tidak diterapkan.",
            },
        )

    await _recalc_invoice_payment_from_receipts(db, claimed.get("invoice_id"))
    await sync_order_payment_projections(db, claimed.get("po_id"))
    return claimed


async def _patch_order_payment_receipt_proof(db, receipt: dict, proof_meta: dict) -> None:
    """Attach public proof meta onto the matching payment_receipts entry. No payment_files mutation."""
    po_id = receipt.get("po_id")
    receipt_id = receipt.get("id")
    if not po_id or not receipt_id or not proof_meta:
        return
    order = await db.orders.find_one({"id": str(po_id)}, {"_id": 0, "payment_receipts": 1})
    if not order:
        return
    changed = False
    entries = list(order.get("payment_receipts") or [])
    for entry in entries:
        if entry.get("receipt_id") == receipt_id:
            entry["payment_proof"] = public_proof(proof_meta) or proof_meta
            entry["has_payment_proof"] = True
            changed = True
            break
    if changed:
        await db.orders.update_one({"id": str(po_id)}, {"$set": {"payment_receipts": entries}})


async def attach_payment_proof_to_receipt(db, receipt_id: str, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Attach proof to a receipt that has none. Idempotent if proof already exists. Immutable replace denied."""
    receipt = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not receipt:
        raise HTTPException(404, "Penerimaan Penjualan tidak ditemukan.")
    existing = proof_from_receipt(receipt)
    if existing and existing.get("id"):
        attached = await attach_receipt_accounting_fields(db, [_public_receipt(receipt)])
        return attached[0]
    proof_input = parse_proof_payload(payload.get("payment_proof") or payload)
    if not proof_input:
        raise HTTPException(400, "Bukti Payment wajib diunggah.")
    proof_meta = await store_payment_proof(
        db,
        receipt=receipt,
        user=user,
        filename=proof_input["name"],
        data_url=proof_input["data_url"],
        uploaded_at=now_iso(),
    )
    await db.sales_receipts.update_one({"id": receipt["id"]}, {"$set": {"payment_proof": proof_meta}})
    receipt["payment_proof"] = proof_meta
    await _patch_order_payment_receipt_proof(db, receipt, proof_meta)
    attached = await attach_receipt_accounting_fields(db, [_public_receipt(receipt)])
    return attached[0]


_PAY_DOWNSTREAM_MSG = (
    "Penerimaan tidak dapat dihapus karena alur produksi sudah melanjutkan tahap setelah Approval Payment."
)


async def _receipt_delete_blocked_by_workflow(db, rec: dict, remaining_after_delete: int) -> bool:
    """Last PAY on a PO cannot be removed once Penjadwalan (stage 5+) has started."""
    if remaining_after_delete > 0:
        return False
    po_id = rec.get("po_id")
    if not po_id and rec.get("invoice_id"):
        invoice = await db.sales_invoices.find_one({"id": rec.get("invoice_id")}, {"po_id": 1, "_id": 0})
        po_id = (invoice or {}).get("po_id")
    if not po_id:
        return False
    order = await db.orders.find_one({"id": po_id}, {"current_stage": 1, "_id": 0})
    if not order:
        return False
    return int(order.get("current_stage") or 0) >= 5


async def _recalc_invoice_payment_from_receipts(db, invoice_id: str) -> None:
    if not invoice_id:
        return
    invoice = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not invoice:
        return
    from services.financial_statement_common import MONEY_Q, money_float
    from services.sales_customer_credit import invoice_credit_applied_total
    from services.sales_receipt_posting import receipt_allocated_amount

    remaining = await db.sales_receipts.find({"invoice_id": invoice_id}, {"_id": 0}).to_list(500)
    paid_dec = sum(
        (receipt_allocated_amount(r) for r in remaining if receipt_counts_toward_outstanding(r)),
        Decimal("0.00"),
    )
    paid_dec += await invoice_credit_applied_total(db, invoice_id)
    paid_dec = paid_dec.quantize(MONEY_Q)
    paid = money_float(paid_dec)
    grand = float(invoice.get("grand_total", 0))
    outstanding = money_float(max(Decimal(str(grand)).quantize(MONEY_Q) - paid_dec, Decimal("0.00")))
    if paid <= 0:
        p_status = "BELUM DIBAYAR"
    elif paid < grand:
        p_status = "DP"
    else:
        p_status = "LUNAS"
    await db.sales_invoices.update_one(
        {"id": invoice_id},
        {"$set": {"paid_total": paid, "outstanding_amount": outstanding, "payment_status": p_status}},
    )


async def _recalc_order_payment_facts_after_receipt_delete(db, po_id: str, deleted_receipt_ids: list) -> None:
    """Recalculate payment_fact from remaining receipts. Never reverse stage history."""
    if not po_id:
        return
    order = await db.orders.find_one({"id": po_id})
    if not order:
        return
    deleted = set(deleted_receipt_ids or [])
    remaining_docs = [
        r
        for r in await db.sales_receipts.find({"po_id": po_id}, {"_id": 0}).sort("created_at", 1).to_list(500)
        if receipt_counts_toward_outstanding(r)
    ]
    kept_facts = [
        r for r in (order.get("payment_receipts") or [])
        if r.get("receipt_id") not in deleted
    ]
    patch: Dict[str, Any] = {"payment_receipts": kept_facts}
    if remaining_docs:
        last = remaining_docs[-1]
        patch["payment_fact"] = {
            "receipt_id": last.get("id"),
            "invoice_id": last.get("invoice_id"),
            "amount": last.get("amount"),
            "payment_state": last.get("payment_state"),
            "updated_at": now_iso(),
        }
    else:
        patch["payment_fact"] = None
        cs = int(order.get("current_stage") or 0)
        if cs < 5 and order.get("payment_approved_by") == "system":
            patch["payment_status"] = "Waiting"
            patch["payment_approved_by"] = None
            patch["payment_approved_by_name"] = None
            patch["payment_approved_at"] = None
    await db.orders.update_one({"id": po_id}, {"$set": patch})


async def delete_sales_receipt(db, receipt_id: str, user: dict) -> dict:
    """Delete a single sales receipt if safe (workflow not past Approval Payment)."""
    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "Penerimaan Penjualan tidak ditemukan.")

    invoice_id = rec.get("invoice_id")
    po_id = rec.get("po_id")
    if not po_id and invoice_id:
        invoice = await db.sales_invoices.find_one({"id": invoice_id}, {"po_id": 1, "_id": 0})
        po_id = (invoice or {}).get("po_id")

    remaining_after = 0
    if po_id:
        remaining_after = max(await db.sales_receipts.count_documents({"po_id": po_id}) - 1, 0)
    journal = await find_sales_receipt_source_journal(db, rec["id"])
    if journal:
        if str(journal.get("status") or "").strip().lower() == "reversed" or receipt_is_cancelled(rec):
            raise HTTPException(409, REVERSED_DELETE_MESSAGE)
        raise HTTPException(409, POSTED_DELETE_MESSAGE)
    if receipt_is_cancelled(rec):
        raise HTTPException(409, REVERSED_DELETE_MESSAGE)
    if posting_claim_is_active(rec):
        raise HTTPException(409, POSTING_IN_PROGRESS_DELETE_MESSAGE)
    if delete_claim_is_active(rec):
        raise HTTPException(409, DELETE_IN_PROGRESS_DELETE_MESSAGE)

    if await _receipt_delete_blocked_by_workflow(db, rec, remaining_after):
        raise HTTPException(400, _PAY_DOWNSTREAM_MSG)

    receipt_number = rec.get("receipt_number", rec["id"])
    deleted = await db.sales_receipts.find_one_and_delete(
        {
            "id": rec["id"],
            "$and": [
                _claim_absent_clause(CLAIM_FIELD),
                delete_claim_absent_or_stale_clause(),
            ],
        }
    )
    if not deleted:
        fresh = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0})
        if fresh and delete_claim_is_active(fresh):
            raise HTTPException(409, DELETE_IN_PROGRESS_DELETE_MESSAGE)
        if fresh and posting_claim_is_active(fresh):
            raise HTTPException(409, POSTING_IN_PROGRESS_DELETE_MESSAGE)
        journal = await find_sales_receipt_source_journal(db, rec["id"])
        if journal:
            raise HTTPException(409, POSTED_DELETE_MESSAGE)
        raise HTTPException(409, POSTING_IN_PROGRESS_DELETE_MESSAGE)
    await soft_delete_proof_for_receipt(
        db,
        rec["id"],
        deleted_by=str(user.get("email") or ""),
        reason="sales_receipt_deleted",
    )
    await _recalc_invoice_payment_from_receipts(db, invoice_id)
    await _recalc_order_payment_facts_after_receipt_delete(db, po_id, [rec["id"]])
    return {"deleted": True, "receipt_number": receipt_number, "id": rec["id"]}


async def bulk_delete_sales_receipts(
    db,
    ids: list,
    user: dict,
    *,
    after_eligibility=None,
    after_reserve=None,
) -> dict:
    """Bulk delete receipts — ALL OR NOTHING, reserved before any physical delete."""
    unique_ids = list(dict.fromkeys(ids))
    receipts = await db.sales_receipts.find({"id": {"$in": unique_ids}}, {"_id": 0}).to_list(len(unique_ids) or 1)
    by_id = {r["id"]: r for r in receipts}

    blockers = []
    to_delete = []

    for rid in unique_ids:
        rec = by_id.get(rid)
        if not rec:
            blockers.append({"id": rid, "number": rid, "reason": "Penerimaan tidak ditemukan."})
            continue
        po_id = rec.get("po_id")
        if not po_id and rec.get("invoice_id"):
            invoice = await db.sales_invoices.find_one({"id": rec.get("invoice_id")}, {"po_id": 1, "_id": 0})
            po_id = (invoice or {}).get("po_id")
            rec["po_id"] = po_id
        remaining_after = 0
        if po_id:
            all_po_receipts = await db.sales_receipts.count_documents({"po_id": po_id})
            po_receipts_in_batch = sum(1 for rid2 in unique_ids if by_id.get(rid2, {}).get("po_id") == po_id)
            remaining_after = max(all_po_receipts - po_receipts_in_batch, 0)
        journal = await find_sales_receipt_source_journal(db, rec["id"])
        if journal:
            blockers.append({
                "id": rec["id"],
                "number": rec.get("receipt_number", rec["id"]),
                "reason": (
                    REVERSED_DELETE_MESSAGE
                    if str(journal.get("status") or "").strip().lower() == "reversed" or receipt_is_cancelled(rec)
                    else POSTED_DELETE_MESSAGE
                ),
            })
            continue
        if receipt_is_cancelled(rec):
            blockers.append({
                "id": rec["id"],
                "number": rec.get("receipt_number", rec["id"]),
                "reason": REVERSED_DELETE_MESSAGE,
            })
            continue
        if posting_claim_is_active(rec):
            blockers.append({
                "id": rec["id"],
                "number": rec.get("receipt_number", rec["id"]),
                "reason": POSTING_IN_PROGRESS_DELETE_MESSAGE,
            })
            continue
        if delete_claim_is_active(rec):
            blockers.append({
                "id": rec["id"],
                "number": rec.get("receipt_number", rec["id"]),
                "reason": DELETE_IN_PROGRESS_DELETE_MESSAGE,
            })
            continue
        if await _receipt_delete_blocked_by_workflow(db, rec, remaining_after):
            blockers.append({
                "id": rec["id"],
                "number": rec.get("receipt_number", rec["id"]),
                "reason": _PAY_DOWNSTREAM_MSG,
            })
            continue
        to_delete.append(rec)

    if blockers:
        raise HTTPException(400, {"message": "Tidak dapat menghapus. Beberapa penerimaan memiliki dependensi.", "blockers": blockers})

    if after_eligibility is not None:
        await after_eligibility(list(to_delete))

    if not to_delete:
        return {"deleted_count": 0, "deleted": []}

    token = str(uuid.uuid4())
    reserved: List[dict] = []
    try:
        for rec in to_delete:
            claimed = await acquire_delete_claim(db, rec["id"], token)
            if claimed is None:
                raise HTTPException(409, {
                    "message": "Tidak dapat menghapus. Beberapa penerimaan memiliki dependensi.",
                    "blockers": [{
                        "id": rec["id"],
                        "number": rec.get("receipt_number", rec["id"]),
                        "reason": POSTING_IN_PROGRESS_DELETE_MESSAGE,
                    }],
                })
            journal = await find_sales_receipt_source_journal(db, rec["id"])
            if journal:
                raise HTTPException(409, {
                    "message": "Tidak dapat menghapus. Beberapa penerimaan memiliki dependensi.",
                    "blockers": [{
                        "id": rec["id"],
                        "number": rec.get("receipt_number", rec["id"]),
                        "reason": POSTED_DELETE_MESSAGE,
                    }],
                })
            reserved.append(rec)

        if after_reserve is not None:
            await after_reserve(list(reserved), token)

        result = await db.sales_receipts.delete_many({DELETE_CLAIM_FIELD: token})
        if int(result.deleted_count or 0) != len(reserved):
            raise HTTPException(409, {
                "message": "Tidak dapat menghapus. Beberapa penerimaan memiliki dependensi.",
                "blockers": [{
                    "id": reserved[0]["id"] if reserved else "",
                    "number": reserved[0].get("receipt_number", "") if reserved else "",
                    "reason": POSTING_IN_PROGRESS_DELETE_MESSAGE,
                }],
            })
    except Exception:
        await release_delete_claims(db, token)
        raise

    for r in reserved:
        await soft_delete_proof_for_receipt(
            db,
            r["id"],
            deleted_by=str(user.get("email") or ""),
            reason="sales_receipt_bulk_deleted",
        )

    affected_invoice_ids = list(set(r.get("invoice_id") for r in reserved if r.get("invoice_id")))
    for inv_id in affected_invoice_ids:
        await _recalc_invoice_payment_from_receipts(db, inv_id)

    affected_po_ids = list(set(r.get("po_id") for r in reserved if r.get("po_id")))
    for po_id in affected_po_ids:
        po_deleted = [r["id"] for r in reserved if r.get("po_id") == po_id]
        await _recalc_order_payment_facts_after_receipt_delete(db, po_id, po_deleted)

    return {
        "deleted_count": len(reserved),
        "deleted": [{"id": r["id"], "receipt_number": r.get("receipt_number", "")} for r in reserved],
    }


def generate_sales_receipt_pdf_bytes(receipt: dict) -> bytes:
    """Generate official A4 PDF for Sales Receipt (Penerimaan Penjualan)."""
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from services.pdf_date import format_pdf_date

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    styles = getSampleStyleSheet()

    company_title_style = ParagraphStyle(
        'CompanyTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=17,
        textColor=colors.HexColor('#0f172a'),
    )

    doc_title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        alignment=2,
        textColor=colors.HexColor('#059669'), # Green for receipt
    )

    meta_label_style = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#334155'),
    )

    meta_val_style = ParagraphStyle(
        'MetaVal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#0f172a'),
    )

    tbl_cell_bold = ParagraphStyle(
        'TblCellBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        alignment=2, # Right align for numbers
        textColor=colors.HexColor('#0f172a'),
    )

    from html import escape

    elements = []

    header_data = [
        [
            Paragraph("<b>ETERNAL PACKAGING</b>", company_title_style),
            Paragraph("<b>PENERIMAAN PENJUALAN</b><br/><font size='9' color='#10b981'><b>" + escape(str(receipt.get('receipt_number') or receipt.get('id') or '')) + "</b></font>", doc_title_style),
        ]
    ]
    header_tbl = Table(header_data, colWidths=[9.5 * cm, 8.5 * cm])
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(header_tbl)
    elements.append(Spacer(1, 0.2 * cm))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#34d399'), spaceAfter=8, spaceBefore=0))

    meta_data = [
        [
            Paragraph("<b>Nomor Faktur</b>", meta_label_style),
            Paragraph(f": {escape(str(receipt.get('invoice_number') or '-'))}", meta_val_style),
            Paragraph("<b>Customer</b>", meta_label_style),
            Paragraph(f": {escape(str(receipt.get('customer') or '-'))}", meta_val_style),
        ],
        [
            Paragraph("<b>Nomor PO</b>", meta_label_style),
            Paragraph(f": {escape(str(receipt.get('po_number') or '-'))}", meta_val_style),
            Paragraph("<b>Tgl Penerimaan</b>", meta_label_style),
            Paragraph(f": {format_pdf_date(receipt.get('receipt_date'))}", meta_val_style),
        ],
        [
            Paragraph("<b>Tipe Bayar</b>", meta_label_style),
            Paragraph(f": {escape(str(receipt.get('payment_state') or '-'))}", meta_val_style),
            Paragraph("<b>Catatan</b>", meta_label_style),
            Paragraph(f": {escape(str(receipt.get('notes') or '-'))}", meta_val_style),
        ],
    ]
    meta_tbl = Table(meta_data, colWidths=[3.0 * cm, 6.0 * cm, 3.0 * cm, 6.0 * cm])
    meta_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(meta_tbl)
    elements.append(Spacer(1, 0.8 * cm))

    def format_money(val):
        try:
            f_val = float(val)
            if f_val.is_integer():
                return "Rp " + f"{int(f_val):,}".replace(",", ".")
            else:
                s = f"{f_val:,.2f}"
                return "Rp " + s.replace(",", "X").replace(".", ",").replace("X", ".")
        except:
            return "Rp 0"

    amount = receipt.get("amount", 0)

    amount_data = [
        [
            Paragraph("<font size='10'><b>TOTAL DITERIMA</b></font>", meta_label_style),
            Paragraph(f"<font size='12'>{format_money(amount)}</font>", tbl_cell_bold),
        ]
    ]

    amount_tbl = Table(amount_data, colWidths=[9.0 * cm, 9.0 * cm])
    amount_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#ecfdf5')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#6ee7b7')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(amount_tbl)

    doc.build(elements)
    return buffer.getvalue()
