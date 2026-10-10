"""Phase 2 — five-tab Sales list classification (backend authoritative).

Tab order (UI): SEMUA | AKTIF | SELESAI | PENDING | BATAL
API scopes: all | active | completed | pending | cancelled

Precedence: CANCELLED → PENDING → COMPLETED → ACTIVE.
Classification is computed from current evidence; no stored tab field.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

TAB_ALL = "all"
TAB_ACTIVE = "active"
TAB_COMPLETED = "completed"
TAB_PENDING = "pending"
TAB_CANCELLED = "cancelled"

FIVE_TAB_SCOPES = frozenset({TAB_ALL, TAB_ACTIVE, TAB_COMPLETED, TAB_PENDING, TAB_CANCELLED})
PRIMARY_TABS = (TAB_ACTIVE, TAB_COMPLETED, TAB_PENDING, TAB_CANCELLED)

DEFAULT_PAGE_SIZE = 50
PAGE_SIZE_MAX = 100
# Visible ceiling for streamed classify-then-page lists (not a silent truncate).
LIST_HARD_MAX = 50000
# Deterministic list order for five-tab pagination (Status PO uses its own sort).
LIST_SORT = [("created_at", -1), ("id", -1)]
LIST_SORT_DICT = {"created_at": -1, "id": -1}

AUTO_PENDING_STATUSES = frozenset({
    "pending",
    "posting",
    "failed",
    "retry_pending",
})


def normalize_list_tab(raw: Optional[str], *, default: str = TAB_ACTIVE) -> str:
    key = str(raw or "").strip().lower()
    if key in ("", "semua"):
        return TAB_ALL if raw is not None and str(raw).strip() else default
    if key in ("aktif",):
        return TAB_ACTIVE
    if key in ("selesai", "completed", "done"):
        return TAB_COMPLETED
    if key in ("pending",):
        return TAB_PENDING
    if key in ("batal", "cancelled", "canceled", "dibatalkan"):
        return TAB_CANCELLED
    if key in FIVE_TAB_SCOPES:
        return key
    raise ValueError(f"Tab list tidak valid: {raw}")


def empty_tab_counts() -> Dict[str, int]:
    return {
        TAB_ALL: 0,
        TAB_ACTIVE: 0,
        TAB_COMPLETED: 0,
        TAB_PENDING: 0,
        TAB_CANCELLED: 0,
    }


def bump_tab_count(counts: Dict[str, int], tab: str) -> None:
    if tab in PRIMARY_TABS:
        counts[tab] = int(counts.get(tab) or 0) + 1
        counts[TAB_ALL] = int(counts.get(TAB_ALL) or 0) + 1


def paginate_rows(rows: List[dict], *, page: int, page_size: int) -> Tuple[List[dict], int, int]:
    """Return (page_rows, page_num, size) with stable input order preserved."""
    try:
        page_num = int(page or 1)
        size = int(page_size or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        page_num, size = 1, DEFAULT_PAGE_SIZE
    if page_num < 1:
        page_num = 1
    if size < 1:
        size = DEFAULT_PAGE_SIZE
    if size > PAGE_SIZE_MAX:
        size = PAGE_SIZE_MAX
    start = (page_num - 1) * size
    return rows[start:start + size], page_num, size


def parse_page_params(page: Any, page_size: Any) -> Tuple[int, int]:
    try:
        page_num = int(page if page is not None else 1)
    except (TypeError, ValueError):
        raise ValueError("Halaman tidak valid.")
    try:
        size = int(page_size if page_size is not None else DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        raise ValueError("Ukuran halaman tidak valid.")
    if page_num < 1 or size < 1 or size > PAGE_SIZE_MAX:
        raise ValueError("Halaman atau ukuran halaman tidak valid.")
    return page_num, size


# ---------------------------------------------------------------------------
# Pesanan Penjualan
# ---------------------------------------------------------------------------

def sales_order_awaits_approval(order: Optional[dict]) -> bool:
    """True only for real payment/design *approval* waits (files present).

    Not every HOLD: missing design/payment files are operational waits, not Pending.
    """
    if not order or order.get("cancelled"):
        return False
    has_design = bool(order.get("design_files")) or bool(order.get("_has_design_files"))
    has_payment = bool(order.get("payment_files")) or bool(order.get("_has_payment_files"))
    design_approved = (
        str(order.get("design_status") or "").strip() == "Approved"
        or bool(order.get("design_bypassed"))
    )
    payment_approved = (
        str(order.get("payment_status") or "").strip() == "Approved"
        or bool(order.get("payment_bypassed"))
    )
    if has_design and not design_approved:
        return True
    if has_payment and not payment_approved:
        return True
    # hold_reasons already computed by workflow snapshot
    reasons = order.get("hold_reasons") or []
    for reason in reasons:
        text = str(reason or "").lower()
        if "menunggu approval design" in text or "menunggu approval payment" in text:
            return True
    return False


def classify_sales_order_list_tab(order: Optional[dict]) -> str:
    if not order:
        return TAB_PENDING
    if order.get("cancelled"):
        return TAB_CANCELLED
    try:
        stage = int(order.get("current_stage") or 0)
    except (TypeError, ValueError):
        stage = 0
    if stage >= 14:
        return TAB_COMPLETED
    if sales_order_awaits_approval(order):
        return TAB_PENDING
    return TAB_ACTIVE


# ---------------------------------------------------------------------------
# Faktur Penjualan
# ---------------------------------------------------------------------------

def _auto_status_pending(doc: Optional[dict]) -> bool:
    status = str((doc or {}).get("auto_accounting_status") or "").strip().lower()
    return status in AUTO_PENDING_STATUSES


def _outstanding_amount(doc: Optional[dict]) -> float:
    try:
        return round(float((doc or {}).get("outstanding_amount") or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def classify_sales_invoice_list_tab(
    invoice: Optional[dict],
    *,
    revision_incomplete: bool = False,
) -> str:
    """Exclusive primary tab for one invoice after live fields are attached."""
    from services.sales_business_cancel import (
        CANCEL_PROGRESS_FAILED,
        CANCEL_PROGRESS_PENDING,
        LIST_CLASS_CANCELLED,
    )
    from services.sales_invoice_posting import (
        ACCOUNTING_NOT_POSTED,
        ACCOUNTING_POSTED,
        ACCOUNTING_REVERSED,
    )
    from services.sales_invoices import FINANCIAL_CANCELLED, FINANCIAL_DRAFT, effective_financial_status

    if not invoice:
        return TAB_PENDING

    progress = str(invoice.get("business_cancel_progress") or "").strip().lower()
    list_class = str(invoice.get("list_class") or "").strip().lower()
    financial = effective_financial_status(invoice)
    accounting = str(invoice.get("accounting_status") or ACCOUNTING_NOT_POSTED).strip().lower()
    posted = bool(invoice.get("accounting_posted")) or accounting == ACCOUNTING_POSTED

    # Incomplete cancel stays PENDING even when financial_status already cancelled.
    if progress in {CANCEL_PROGRESS_PENDING, CANCEL_PROGRESS_FAILED}:
        return TAB_PENDING
    if list_class == LIST_CLASS_CANCELLED or financial == FINANCIAL_CANCELLED:
        return TAB_CANCELLED

    if revision_incomplete or bool(invoice.get("revision_incomplete")):
        return TAB_PENDING
    if bool(invoice.get("accounting_claim_active")):
        return TAB_PENDING
    if _auto_status_pending(invoice):
        return TAB_PENDING
    if financial == FINANCIAL_DRAFT:
        return TAB_PENDING
    if accounting == ACCOUNTING_REVERSED:
        return TAB_PENDING
    if not posted or accounting != ACCOUNTING_POSTED:
        return TAB_PENDING
    if not invoice.get("accounting_journal_id"):
        # Posted without journal id is ambiguous → Pending.
        return TAB_PENDING

    outstanding = _outstanding_amount(invoice)
    if outstanding <= 0:
        return TAB_COMPLETED
    return TAB_ACTIVE


# ---------------------------------------------------------------------------
# Penerimaan Penjualan
# ---------------------------------------------------------------------------

def receipt_settlement_is_effective(receipt: Optional[dict]) -> bool:
    """Mirrors receipt_counts_toward_outstanding settlement semantics (non-cancelled)."""
    from services.sales_receipt_posting import SETTLEMENT_EFFECTIVE_FIELD, receipt_is_cancelled

    if not receipt or receipt_is_cancelled(receipt):
        return False
    if SETTLEMENT_EFFECTIVE_FIELD in receipt and receipt.get(SETTLEMENT_EFFECTIVE_FIELD) is False:
        return False
    return True


def classify_sales_receipt_list_tab(
    receipt: Optional[dict],
    *,
    invoice_outstanding: Optional[float] = None,
) -> str:
    from services.sales_business_cancel import (
        CANCEL_PROGRESS_FAILED,
        CANCEL_PROGRESS_PENDING,
        LIST_CLASS_CANCELLED,
    )
    from services.sales_invoice_posting import (
        ACCOUNTING_NOT_POSTED,
        ACCOUNTING_POSTED,
        ACCOUNTING_REVERSED,
    )
    from services.sales_receipt_posting import (
        SETTLEMENT_EFFECTIVE_FIELD,
        receipt_is_cancelled,
    )

    if not receipt:
        return TAB_PENDING

    progress = str(receipt.get("business_cancel_progress") or "").strip().lower()
    list_class = str(receipt.get("list_class") or "").strip().lower()
    if progress in {CANCEL_PROGRESS_PENDING, CANCEL_PROGRESS_FAILED}:
        return TAB_PENDING
    if list_class == LIST_CLASS_CANCELLED or receipt_is_cancelled(receipt):
        return TAB_CANCELLED

    if bool(receipt.get("accounting_claim_active")):
        return TAB_PENDING
    if _auto_status_pending(receipt):
        return TAB_PENDING

    accounting = str(receipt.get("accounting_status") or ACCOUNTING_NOT_POSTED).strip().lower()
    posted = bool(receipt.get("accounting_posted")) or accounting == ACCOUNTING_POSTED
    if accounting == ACCOUNTING_REVERSED:
        return TAB_PENDING
    if not posted or accounting == ACCOUNTING_NOT_POSTED:
        return TAB_PENDING
    if SETTLEMENT_EFFECTIVE_FIELD in receipt and receipt.get(SETTLEMENT_EFFECTIVE_FIELD) is False:
        return TAB_PENDING
    if not receipt_settlement_is_effective(receipt):
        return TAB_PENDING

    if invoice_outstanding is None:
        try:
            invoice_outstanding = float(receipt.get("invoice_outstanding_amount"))
        except (TypeError, ValueError):
            invoice_outstanding = None
    if invoice_outstanding is None:
        # Linked invoice outstanding unknown → fail closed to Pending.
        return TAB_PENDING
    try:
        outstanding = round(float(invoice_outstanding), 2)
    except (TypeError, ValueError):
        return TAB_PENDING
    if outstanding <= 0:
        return TAB_COMPLETED
    return TAB_ACTIVE


def filter_by_list_tab(rows: Iterable[dict], list_tab: str) -> List[dict]:
    tab = normalize_list_tab(list_tab, default=TAB_ALL)
    out: List[dict] = []
    for row in rows:
        primary = str(row.get("list_tab") or "")
        if tab == TAB_ALL or primary == tab:
            out.append(row)
    return out


def legacy_invoice_scope_includes(list_tab: str, scope: str) -> bool:
    """Map five-tab primary membership onto legacy active/cancelled/all scopes."""
    scope_key = str(scope or TAB_ACTIVE).strip().lower()
    if scope_key == TAB_ALL:
        return True
    if scope_key == TAB_CANCELLED:
        return list_tab == TAB_CANCELLED
    if scope_key == TAB_ACTIVE:
        # Legacy Aktif = everything that is not verified Batal.
        return list_tab != TAB_CANCELLED
    # New exclusive scopes
    if scope_key in {TAB_COMPLETED, TAB_PENDING}:
        return list_tab == scope_key
    return list_tab == scope_key
