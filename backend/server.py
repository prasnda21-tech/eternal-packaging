from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, UploadFile, File, Form, Header, Query, Body
from fastapi.responses import Response as FastAPIResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os, logging, uuid, base64, re, secrets, hashlib, copy, inspect, asyncio
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Any, Dict, Tuple, Union
from datetime import date, datetime, timezone, timedelta
import bcrypt
import jwt
from pymongo.errors import DuplicateKeyError, OperationFailure
from services.accurate import (
    AccurateClient,
    AccurateErrorCategory,
    AccurateRequestError,
    ACCURATE_DELIVERY_ENDPOINT_AVAILABLE,
    ACCURATE_DELIVERY_ENDPOINT_REASON,
    coerce_accurate_quantity,
    fetch_delivery_notes,
    get_last_list_error,
    get_mock_delivery_note,
    log_master_items_sync_failure,
    new_correlation_id,
    sanitize_accurate_error,
    so_preview_dates,
    sync_sales_orders,
)
from services.import_parser import parse_import_file, ImportParseError
from services.pdf_parser import parse_pdf_po, PDFParseError
from services import storage as obj_storage
from services.master_items import (
    COLLECTION as MASTER_ITEMS_COLLECTION,
    MasterItemUnresolvedError,
    SyncInProgressError,
    list_master_items,
    list_master_items_picker,
    resolve_po_line_items,
    sync_master_items,
)
from services.shipment_status import (
    SHIPMENT_STATUS_PROJECTION,
    shipment_status_row,
)
from services.delivery_schedule import (
    RESCHEDULE_PROJECTION,
    SCHEDULE_PROJECTION,
    RescheduleError,
    apply_reschedule as _apply_delivery_reschedule,
    apply_set_date as _apply_delivery_set_date,
    attach_schedule_snapshot,
    reschedule_rows as _delivery_reschedule_rows,
    schedule_rows as _delivery_schedule_rows,
)
from services.workflow_stage_status import role_status_from_order as _role_status_from_order
from services.delivery_history import (
    append_confirmation as _append_delivery_confirmation,
    attach_delivery_history,
)
from services.operator_schedule import (
    apply_operator_assignments as _apply_operator_assignments,
    cancel_split_remaining as _cancel_split_remaining,
    pending_splits_for_user as _pending_splits_for_user,
)
from services.screen_size import line_screen_size as _line_screen_size
from services.operator_payroll import (
    PAYROLL_EXPORT_COLUMNS,
    PAYROLL_REKAP_COLUMNS,
    SETTINGS_KEY as OPERATOR_WAGE_SETTINGS_KEY,
    compute_operator_payroll,
    item_identity as _wage_item_identity,
    operators_and_items as _payroll_filter_options,
    snapshot_on_save as _wage_snapshot_on_save,
    effective_rate_as_of as _wage_effective_rate,
    normalize_wage_settings,
    parse_operator_ids as _parse_operator_ids,
    merge_operators_for_settings as _merge_wage_operators,
    validate_wage_settings as _validate_wage_settings,
)
from services.payment_files import (
    MAX_PAYMENT_PROOFS,
    payment_proof_reject_reason,
)
from services.surat_jalan_files import (
    PERM_VIEW_FILE as _SJ_VIEW_FILE,
    PERM_DELETE_FILE as _SJ_DELETE_FILE,
    SOURCE_SYSTEM_GENERATED as _SJ_SOURCE_GENERATED,
    content_disposition as _sj_content_disposition,
    decode_data_url as _sj_decode_data_url,
    find_file as _sj_find_file,
    history_entries as _sj_history_entries,
    is_generated_sj_file as _sj_is_generated,
    remove_file_from_order as _sj_remove_file,
    sniffer_content_type as _sj_sniff_ct,
    strip_sj_bytes_from_order as _strip_sj_bytes,
)
from services.inline_attachments import (
    ALLOWED_KINDS as _INLINE_ATTACHMENT_KINDS,
    UnsafeInlineMime as _UnsafeInlineMime,
    content_disposition as _inline_attachment_disposition,
    decode_inline_attachment as _decode_inline_attachment,
    field_for_kind as _inline_attachment_field,
    find_inline_attachment as _find_inline_attachment,
    strip_inline_attachment_bytes as _strip_inline_attachment_bytes,
)
from services.status_po import build_status_po
from services.order_prediction import (
    ORDER_PREDICTION_PROJECTION,
    PERM_VIEW as ORDER_PREDICTION_PERM,
    build_order_prediction,
    build_order_prediction_detail,
    can_view_order_prediction,
)
from services.sales_order_repeat import (
    build_repeat_source_dto,
    find_latest_valid_repeat_source,
    find_repeat_source_by_order_id,
    stamp_canonical_customer,
)
from services.workflow_entries import append_qty_entry, find_by_action_id, seed_legacy, sum_entry_qty
from services.customer_tracking import strip_tracking_secret as _strip_tracking_secret
from services import delivery_route_api as _delivery_route_api
from services.work_panel_settings import SETTINGS_KEY as WORK_PANEL_SETTINGS_KEY
from services import work_panel_settings as work_panel_cfg
from services import accurate_creator as accurate_creator_cfg
from services.dashboard_today import (
    WORK_PROJECTION as TODAY_WORK_PROJECTION,
    build_shipments_today,
    build_work_today,
    due_today_orders,
    operational_iso,
    parse_operational_date_key,
    partition_orders_by_delivery,
    resolve_sj_operational_date,
    shipments_today_mongo_query,
    wib_today,
    work_today_mongo_query,
)
from services.current_stock import (
    COLLECTION as STOCK_MANUAL_COLLECTION,
    ORDER_PROJECTION as STOCK_ORDER_PROJECTION,
    PURCHASE_SOURCE as STOCK_PURCHASE_SOURCE,
    build_stock_rows,
    filter_sort_page as filter_stock_page,
    parse_manual_qty,
    public_manual as public_stock_manual,
)
from services.current_stock_import import (
    PERM_IMPORT as STOCK_IMPORT_PERM,
    build_stock_import_preview,
    validate_apply_rows as validate_stock_import_apply,
)
from services.stock_engine_v2 import (
    STOCK_INSUFFICIENT_MSG,
    apply_stock_opname,
    available_for_item,
    build_current_stock_rows,
    ensure_v2_physical_settings,
    filter_sort_page_v2,
    validate_sales_order_availability,
)
from services.stock_opname_import import (
    apply_opname_import_batch,
    build_opname_import_preview,
    build_opname_template_xlsx,
    filter_active_stock_rows,
    revalidate_opname_apply_rows,
)
from services.order_prediction_import import (
    COLLECTION as ORDER_PREDICTION_HISTORY_COLLECTION,
    PERM_IMPORT as ORDER_PREDICTION_IMPORT_PERM,
    build_history_import_preview,
    validate_history_apply_rows,
)
from services.purchase_receipts import (
    COLLECTION as RECEIPT_COLLECTION,
    PURCHASE_SOURCE_ACCURATE as STOCK_PURCHASE_ACCURATE,
    SYNC_META_COLLECTION as RECEIPT_SYNC_META_COLLECTION,
    cancel_manual_purchase_receipt,
    create_manual_purchase_receipt,
    filter_public_receipts,
    last_success_at as receipt_last_success_at,
    load_receipts,
    load_sync_meta,
    page_public_receipts,
    public_receipt,
    purchase_source_from_meta,
    sync_purchase_receipts,
)
from services.purchase_http import register_purchase_routes
from services.product_requests_http import register_product_request_routes
from services.spk_http import register_spk_routes
from services.customer_pricing_http import register_customer_pricing_routes
from services.sales_accounting_settings_http import register_sales_accounting_settings_routes
from services.sales_user_mapping_http import register_sales_user_mapping_routes
from services.sales_pwa_http import register_sales_pwa_routes
from services.sales_user_mapping import (
    attach_sales_ownership,
    attach_sales_ownership_admin_fields,
    ensure_owned_po_id,
    enforce_sales_eternal_surface,
    is_sales_pwa_role,
    reject_sales_pwa_bypass,
    sales_owner_match,
    session_user_id,
    SURFACE_LOCK_DETAIL,
)
from services.payroll_accounting_http import register_payroll_accounting_routes
from services.customer_pricing_public_http import register_public_customer_pricing_routes
from services.employee_admin_http import register_employee_admin_routes
from services.hr_payroll_http import register_hr_payroll_routes
from services.chart_of_accounts_http import register_chart_of_accounts_routes
from services.cash_bank_http import register_cash_bank_routes
from services.chat_http import register_chat_routes
from services.chat_indexes import ensure_indexes as ensure_chat_indexes
from services.chat_membership import bootstrap_system_groups as bootstrap_chat_system_groups, on_user_changed as on_chat_user_changed
from services.general_journal_http import register_general_journal_routes
from services.opening_balance_http import register_opening_balance_routes
from services.accounting_periods_http import register_accounting_periods_routes
from services.general_ledger_http import register_general_ledger_routes
from services.inventory_issue_http import register_inventory_issue_routes
from services.accounts_receivable_http import register_accounts_receivable_routes
from services.accounts_payable_http import register_accounts_payable_routes
from services.purchase_accounting_settings_http import register_purchase_accounting_settings_routes
from services.trial_balance_http import register_trial_balance_routes
from services.profit_loss_http import register_profit_loss_routes
from services.financial_dashboard_http import register_financial_dashboard_routes
from services.sales_dashboard_http import register_sales_dashboard_routes
from services.balance_sheet_http import register_balance_sheet_routes
from services.inventory_reconciliation_http import register_inventory_reconciliation_routes
from services.chart_of_accounts import COLLECTION as LEDGER_ACCOUNTS_COLLECTION
from services.sales_activity.http import register_sales_activity_routes
from services.independent_route.http import register_independent_route_routes
from permissions import (
    PERMISSION_CATALOG, ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS,
    ROLE_LABELS, STAGE_PERMISSION, effective_permissions,
    NAV_MENU_ITEMS, NAV_CONFIGURED, with_nav_defaults,
)
from services.division_tasks import (
    DIVISION_DEFS as _DIVISION_TASK_DEFS,
    TASK_PROJECTION as _DIVISION_TASK_PROJECTION,
    build_division_tasks as _build_division_tasks,
    enrich_tasks_current_stock as _enrich_tasks_current_stock,
    can_view_all_division as _can_view_all_division,
    po_needs_loading_task as _po_needs_loading_task,
    resolve_division as _resolve_division,
)
from operator_analytics import (
    build_activity_analytics_pipeline,
    build_design_analytics_pipeline,
    build_driver_analytics_pipeline,
    build_operator_analytics_pipeline,
    compute_operator_detail,
    compute_operator_kpis,
    compute_packing_report,
    compute_preparation_report,
    resolve_period,
    series_grouping,
    wib_today,
)
from output_reports import (
    EXPORT_COLUMNS,
    compute_designer_report,
    compute_driver_report,
    compute_output_series,
    compute_screener_report,
    export_filename,
    iter_design_export_rows,
    iter_driver_export_rows,
    iter_helper_export_rows,
    iter_operator_export_rows,
    iter_screener_export_rows,
    normalize_daily_targets,
    summarize_people,
    _export_na,
)
from quantity_rules import (
    SHIPMENT_SOURCE_ACCURATE as _QR_SRC_ACCURATE,
    SHIPMENT_SOURCE_MANUAL as _QR_SRC_MANUAL,
    apply_shipment_record as _qr_apply_shipment_record,
    available_for_packing as _qr_available_for_packing,
    helper_prepared_for_line as _qr_helper_prepared_for_line,
    helper_prepared_print_qty as _qr_helper_prepared_print_qty,
    helper_over_prepared_qty as _qr_helper_over_prepared_qty,
    helper_target_for_line as _qr_helper_target_for_line,
    helper_target_qty as _qr_helper_target_qty,
    is_fully_delivered as _qr_is_fully_delivered,
    is_print_line as _qr_is_print_line,
    apply_delivery_from_proof_evidence as _qr_apply_delivery_from_proof_evidence,
    confirm_delivery_block_reason as _qr_confirm_delivery_block_reason,
    delivery_batch_identity as _qr_delivery_batch_identity,
    delivery_batch_photo_block_reason as _qr_delivery_batch_photo_block_reason,
    mark_shipment_delivered as _qr_mark_shipment_delivered,
    resolve_delivery_shipment as _qr_resolve_delivery_shipment,
    shipment_is_delivered as _qr_shipment_is_delivered,
    reconcile_sj_quantities_to_loaded_delivery as _qr_reconcile_sj_quantities_to_loaded_delivery,
    valid_delivery_proof_count as _qr_valid_delivery_proof_count,
    delivery_proofs_for_upload_limit as _qr_delivery_proofs_for_upload_limit,
    MAX_PUBLIC_DELIVERY_PROOFS as _QR_MAX_PUBLIC_DELIVERY_PROOFS,
    apply_loading_increments as _qr_apply_loading_increments,
    apply_loading_confirmation as _qr_apply_loading_confirmation,
    validate_loading_match_confirmation as _qr_validate_loading_match_confirmation,
    apply_loading_match_confirmation as _qr_apply_loading_match_confirmation,
    validate_loading_discrepancy as _qr_validate_loading_discrepancy,
    apply_loading_discrepancy as _qr_apply_loading_discrepancy,
    loading_verification_status as _qr_loading_verification_status,
    build_sj_loading_lines as _qr_build_sj_loading_lines,
    cancel_loading_confirmation as _qr_cancel_loading_confirmation,
    effective_loaded_qty as _qr_effective_loaded_qty,
    eligible_delivery_qty as _qr_eligible_delivery_qty,
    loading_status_semantic as _qr_loading_status_semantic,
    merge_loading_line_updates as _qr_merge_loading_line_updates,
    non_print_qty_total as _qr_non_print_qty_total,
    operator_assigned_qty as _qr_operator_assigned_qty,
    operator_assignment_cap as _qr_operator_assignment_cap,
    packing_line_breakdown as _qr_packing_line_breakdown,
    packing_required_total as _qr_packing_required_total,
    packing_status_semantic as _qr_packing_status_semantic,
    apply_confirmed_packing_progress as _qr_apply_confirmed_packing_progress,
    print_available_for_packing as _qr_print_available_for_packing,
    print_completed_qty as _qr_print_completed_qty,
    print_line_indices as _qr_print_line_indices,
    print_qty_remaining as _qr_print_qty_remaining,
    print_qty_target as _qr_print_qty_target,
    print_qty_total as _qr_print_qty_total,
    print_status as _qr_print_status,
    qty_delivered as _qr_qty_delivered,
    qty_packed as _qr_qty_packed,
    remaining_delivery_note_updates as _qr_remaining_delivery_note_updates,
    requires_printing as _qr_requires_printing,
    shipment_line_breakdown as _qr_shipment_line_breakdown,
    shipments_stored as _qr_shipments_stored,
    shipment_is_active as _qr_shipment_is_active,
    shipment_qty_frozen_reason as _qr_shipment_qty_frozen_reason,
    surat_jalan_status_semantic as _qr_surat_jalan_status_semantic,
    validate_delivery_qty as _qr_validate_delivery_qty,
    validate_helper_prepared_qty as _qr_validate_helper_prepared_qty,
    validate_loading_increments as _qr_validate_loading_increments,
    validate_loading_confirmation as _qr_validate_loading_confirmation,
    validate_cancel_loading_confirmation as _qr_validate_cancel_loading_confirmation,
    validate_loading_lines as _qr_validate_loading_lines,
    validate_operator_assignment_total as _qr_validate_operator_assignment_total,
    validate_packing_lines as _qr_validate_packing_lines,
    validate_packing_qty as _qr_validate_packing_qty,
    validate_print_completed_total as _qr_validate_print_completed_total,
    validate_screener_qty as _qr_validate_screener_qty,
    validate_shipment_lines as _qr_validate_shipment_lines,
    merge_packing_line_updates as _qr_merge_packing_line_updates,
    apply_packing_increments as _qr_apply_packing_increments,
)
from workflow_gates import (
    HELPER_GATE_OPERATOR_MSG,
    SCREENER_GATE_OPERATOR_MSG,
    SCREENER_GATE_SAMPLE_MSG,
    design_ok as _wg_design_ok,
    group2_complete as _wg_group2_complete,
    helper_done as _wg_helper_done,
    helper_gate_reason as _wg_helper_gate_reason,
    packing_qty_input as _wg_packing_qty_input,
    payment_ok as _wg_payment_ok,
    penjadwalan_done as _wg_penjadwalan_done,
    print_qty_gate_reason as _wg_print_qty_gate_reason,
    print_qty_input as _wg_print_qty_input,
    sample_ok as _wg_sample_ok,
    sample_uploaded as _wg_sample_uploaded,
    screener_done as _wg_screener_done,
    screener_gate_reason as _wg_screener_gate_reason,
    sync_workflow_stage as _wg_sync_workflow_stage,
    workflow_ready as _wg_workflow_ready,
)


def _group2_complete(order: dict) -> bool:
    return _wg_group2_complete(order)


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]


async def _load_accurate_creator_settings():
    return await db.app_settings.find_one({"key": accurate_creator_cfg.SETTINGS_KEY}, {"_id": 0})


accurate_creator_cfg.set_mapping_loader(_load_accurate_creator_settings)

app = FastAPI()
api_router = APIRouter(prefix="/api")

JWT_ALGORITHM = "HS256"
ROLES = ["admin", "crm", "designer", "head_production", "head_operations", "screener", "helper", "operator", "driver"]

# Workflow stages (in Indonesian) - single source of truth
STAGES = [
    "Order Masuk",         # 0
    "Design",              # 1
    "Approval Design",     # 2
    "Payment",             # 3
    "Approval Payment",    # 4
    "Penjadwalan",         # 5
    "Screener",            # 6
    "Persiapan Helper",    # 7
    "Operator",            # 8
    "Konfirmasi Sample",   # 9
    "Cetak",               # 10
    "Packing",             # 11
    "Surat Jalan",         # 12
    "Loading",             # 13
    "Pengiriman",          # 14  (label tahap; internal key/status tetap delivery/terkirim)
]

# Task 13 workflow-15 bugfix: Driver dihapus sebagai stage produksi.
# Driver tetap ada sebagai role/user/data metadata (nama pengantar, delivery
# proof, dst). Semua reference stage lama 14/15 dipetakan konsisten:
#   OLD 14 (Driver) → NEW 13 (Loading) bila belum "Terkirim"; NEW 14 bila sudah
#   OLD 15 (Terkirim) → NEW 14 (Terkirim)
LEGACY_STAGE_MAX = 15  # untuk safety comparison PO existing yang belum dimigrasi

# Who is allowed to advance from a given stage (index) to next
STAGE_EDIT_ROLES = {
    0: ["admin", "head_production", "head_operations"],
    1: ["admin", "designer", "head_production", "head_operations"],
    2: ["admin", "designer", "head_production", "head_operations"],
    3: ["admin", "head_production", "head_operations"],
    4: ["admin", "head_production", "head_operations"],
    5: ["admin", "head_production", "head_operations"],
    6: ["admin", "head_production", "head_operations", "screener"],
    7: ["admin", "head_production", "head_operations", "helper"],
    8: ["admin", "head_production", "head_operations", "operator"],
    9: ["admin", "head_production", "head_operations", "operator", "designer"],
    10: ["admin", "head_production", "head_operations", "operator"],
    11: ["admin", "head_production", "head_operations", "helper"],
    12: ["admin", "head_production", "head_operations"],
    13: ["admin", "head_production", "head_operations", "driver", "helper"],
    14: ["admin", "head_production", "head_operations", "driver"],
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def token_for(user):
    return jwt.encode(
        {"sub": user["id"], "email": user["email"], "role": user["role"], "name": user.get("name", ""),
         "exp": datetime.now(timezone.utc) + timedelta(hours=8)},
        os.environ["JWT_SECRET"], algorithm=JWT_ALGORITHM,
    )


async def current_user(request: Request):
    token = request.cookies.get("access_token") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        token = request.query_params.get("auth", "")
    if not token:
        raise HTTPException(401, "Silakan masuk terlebih dahulu")
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Sesi berakhir, silakan masuk kembali")
    # Look up user for status + fresh permission override
    if payload.get("email"):
        u = await db.users.find_one({"email": payload["email"]}, {"password_hash": 0})
        if u:
            if u.get("status", "active") != "active":
                raise HTTPException(403, "Akun dinonaktifkan")
            payload["permissions_override"] = u.get("permissions_override") or {}
            payload["user_id"] = u.get("id")
    enforce_sales_eternal_surface(request, payload)
    return payload


async def _role_defaults_override():
    """Read persisted per-role permission defaults (from admin edits)."""
    docs = await db.role_permissions.find({}, {"_id": 0}).to_list(50)
    return {d["role"]: d.get("permissions", []) for d in docs}


async def user_permissions(user: dict) -> set:
    """Compute effective permissions for the JWT payload / user."""
    # Super admin gets everything, always.
    if user.get("email"):
        u = await db.users.find_one({"email": user["email"]}, {"is_super_admin": 1, "role": 1})
        if u and u.get("is_super_admin"):
            return set(ALL_PERMISSIONS)
    role_override = await _role_defaults_override()
    # Inactive roles: user with an inactive role gets empty defaults (still keeps overrides).
    inactive = await _inactive_role_slugs()
    if user.get("role") in inactive:
        base = set()
        for perm, allowed in (user.get("permissions_override") or {}).items():
            if allowed:
                base.add(perm)
        return base
    return effective_permissions(user.get("role"), user.get("permissions_override"), role_override)


async def _inactive_role_slugs() -> set:
    docs = await db.roles.find({"status": "inactive"}, {"role": 1, "_id": 0}).to_list(100)
    return {d["role"] for d in docs}


def require_perm(*perms):
    """Dependency that ensures caller has ALL the given permission keys."""
    async def checker(request: Request, user=Depends(current_user)):
        enforce_sales_eternal_surface(request, user)
        eff = await user_permissions(user)
        for p in perms:
            if p not in eff:
                raise HTTPException(403, f"Permission '{p}' diperlukan")
        return user
    return checker


def require(*roles):
    async def checker(request: Request, user=Depends(current_user)):
        enforce_sales_eternal_surface(request, user)
        if roles and user.get("role") not in roles and user.get("role") != "admin":
            raise HTTPException(403, "Aksi ini tidak tersedia untuk role Anda")
        return user
    return checker


async def _sync_chat_user_best_effort(user_id: str) -> None:
    try:
        await on_chat_user_changed(db, user_id)
    except Exception as exc:
        logger.warning("Chat membership sync failed for user %s: %s", user_id, exc)


class LoginInput(BaseModel):
    email: str
    password: str


class StageAdvancePayload(BaseModel):
    note: Optional[str] = None
    operator_id: Optional[str] = None  # if advancing per-operator progress


class CancelPayload(BaseModel):
    reason: str


class DeletePayload(BaseModel):
    reason: str
    confirm: str  # user must type PO number or "HAPUS" to confirm


class BulkPayload(BaseModel):
    ids: List[str]
    reason: str
    confirm: Optional[str] = None  # required for bulk delete only


class ApprovalPayload(BaseModel):
    approved: bool = True
    note: Optional[str] = None


class BypassPayload(BaseModel):
    reason: str


class RollbackPayload(BaseModel):
    to_stage: int
    reason: str


class HelperPrepPayload(BaseModel):
    qty_required: int
    qty_prepared: int
    notes: Optional[str] = None


class HelperPrepItem(BaseModel):
    line_idx: int
    qty_prepared: Optional[int] = None
    qty: Optional[int] = None
    action_id: Optional[str] = None


class HelperPrepItemsPayload(BaseModel):
    items: List[HelperPrepItem]
    notes: Optional[str] = None
    action_id: Optional[str] = None


class ScreenerInfoPayload(BaseModel):
    screen_qty: Optional[int] = None
    qty: Optional[int] = None
    notes: Optional[str] = None
    action_id: Optional[str] = None


class OperatorSplit(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    operator_email: str
    operator_name: str
    quantity: int
    stage: int = 8  # start at Operator stage
    updated_at: str = Field(default_factory=now_iso)


class OperatorSplitInput(BaseModel):
    operator_email: str
    operator_name: str
    quantity: int
    id: Optional[str] = None
    # Revisi routing per line item: additive & backward-compatible. Kalau None,
    # split ini dianggap "legacy print-scope" (unassigned line) — akan divalidasi
    # secara agregat terhadap total print qty. Kalau ada nilai, wajib mengarah
    # ke line item dengan production_process == "print".
    line_idx: Optional[int] = None
    # Fix: tanggal pengerjaan per assignment. Optional; ISO date "YYYY-MM-DD"
    # atau None untuk membiarkan kosong. Additive & tidak mengubah stage.
    work_date: Optional[str] = None
    # Planning metadata only (Screen Besar / Screen Kecil). Omit preserves existing.
    screen_size: Optional[str] = None


class CancelSplitPayload(BaseModel):
    qty: int
    reason: Optional[str] = None


class AssignOperatorsPayload(BaseModel):
    splits: List[OperatorSplitInput]


class UrgentPayload(BaseModel):
    urgent: bool


class AttachmentInput(BaseModel):
    name: str
    data_url: str  # base64 encoded
    kind: str  # "design" | "sample" | "payment" | "surat_jalan"
    delivery_date: Optional[str] = None  # YYYY-MM-DD operational date for surat_jalan


class NotePayload(BaseModel):
    note: str


class OperatorProgressPayload(BaseModel):
    operator_id: str
    qty_completed: Optional[int] = None
    qty: Optional[int] = None
    action_id: Optional[str] = None


class LoadingLineInput(BaseModel):
    line_idx: int
    loaded_qty: Optional[int] = None
    qty: Optional[int] = None
    action_id: Optional[str] = None


class LoadingConfirmLineInput(BaseModel):
    line_idx: Optional[int] = None
    qty: int
    accurate_item_id: Optional[str] = None


class LoadingConfirmPayload(BaseModel):
    surat_jalan_id: str
    lines: List[LoadingConfirmLineInput]
    action_id: Optional[str] = None


class LoadingMatchConfirmPayload(BaseModel):
    surat_jalan_id: str
    action_id: Optional[str] = None


class LoadingDiscrepancyLineInput(BaseModel):
    line_idx: int
    actual_qty: int


class LoadingDiscrepancyPayload(BaseModel):
    surat_jalan_id: str
    mismatches: List[LoadingDiscrepancyLineInput]
    note: Optional[str] = None
    action_id: Optional[str] = None


class LoadingChecklistPayload(BaseModel):
    vehicle_plate: Optional[str] = None
    driver_name: Optional[str] = None
    items_loaded: Optional[bool] = None
    seal_ok: Optional[bool] = None
    notes: Optional[str] = None
    lines: Optional[List[LoadingLineInput]] = None


def strip_id(doc):
    if not doc:
        return doc
    doc.pop("_id", None)
    return doc


def add_timeline(order, stage_idx, user, note=""):
    entry = {
        "stage": stage_idx,
        "stage_label": STAGES[stage_idx] if stage_idx < len(STAGES) else str(stage_idx),
        "at": now_iso(),
        "by_email": user.get("email"),
        "by_role": user.get("role"),
        "by_name": user.get("name") or user.get("email"),
        "note": note or "",
    }
    order.setdefault("timeline", []).append(entry)
    return entry


def build_seed_orders():
    today = datetime.now(timezone.utc)
    def mkdate(days):
        return (today + timedelta(days=days)).isoformat()

    seed = [
        {
            "po_number": "PO-1048",
            "outlet": "Kopi Rumah — Kemang",
            "customer": "Kopi Rumah",
            "recipient": "Dimas Putra",
            "address": "Jl. Kemang Raya 18, Jakarta Selatan",
            "payment_terms": "Net 14",
            "payment_status": "Approved",
            "design_status": "Approved",
            "sales_user": "Vira",  # legacy; new schema uses `sales` + `created_by_user`
            "sales": "Vira",
            "created_by_user": None,
            "expedition": "Internal fleet",
            "delivery_zone": "Jakarta Selatan",
            "seller": "Vira",
            "priority": "urgent",
            "urgency_approved_by": "production@eternalpackaging.com",
            "line_items": [
                {"item_code": "PC-12", "name": "Paper cup 12oz", "quantity": 1400, "unit": "pcs", "color": "Navy / putih", "notes": "Jaga register cetak"},
                {"item_code": "PC-16", "name": "Paper cup 16oz", "quantity": 1000, "unit": "pcs", "color": "Navy / putih", "notes": ""},
            ],
            "operator_splits": [
                {"id": str(uuid.uuid4()), "operator_email": "operator@eternalpackaging.com", "operator_name": "Rafi Operator", "quantity": 1400, "stage": 10, "updated_at": now_iso()},
                {"id": str(uuid.uuid4()), "operator_email": "operator2@eternalpackaging.com", "operator_name": "Nanda", "quantity": 1000, "stage": 9, "updated_at": now_iso()},
            ],
            "current_stage": 10,
            "packing_status": "Belum",
            "delivery_status": "Belum Terkirim",
        },
        {
            "po_number": "PO-1047",
            "outlet": "Bumi Bowl — BSD",
            "customer": "Bumi Bowl",
            "recipient": "Sari Lim",
            "address": "Ruko Boulevard BSD, Tangerang",
            "payment_terms": "Cash on delivery",
            "payment_status": "Approved",
            "design_status": "Approved",
            "sales_user": "Alfi",
            "sales": "Alfi",
            "created_by_user": None,
            "expedition": "Third-party",
            "delivery_zone": "BSD",
            "seller": "Alfi",
            "priority": "normal",
            "urgency_approved_by": None,
            "line_items": [
                {"item_code": "TP-750", "name": "Takeaway plastic 750ml", "quantity": 1800, "unit": "pcs", "color": "Forest green", "notes": ""},
            ],
            "operator_splits": [
                {"id": str(uuid.uuid4()), "operator_email": "operator@eternalpackaging.com", "operator_name": "Rafi Operator", "quantity": 1800, "stage": 7, "updated_at": now_iso()},
            ],
            "current_stage": 7,
            "packing_status": "Belum",
            "delivery_status": "Belum Terkirim",
        },
        {
            "po_number": "PO-1046",
            "outlet": "Daily Grind — Senopati",
            "customer": "Daily Grind",
            "recipient": "Nia W",
            "address": "Jl. Senopati 44, Jakarta",
            "payment_terms": "Net 30",
            "payment_status": "Waiting",
            "design_status": "Waiting",
            "sales_user": "Vira",
            "sales": "Vira",
            "created_by_user": None,
            "expedition": "Internal fleet",
            "delivery_zone": "Jakarta Pusat",
            "seller": "Vira",
            "priority": "normal",
            "urgency_approved_by": None,
            "line_items": [
                {"item_code": "SC-16", "name": "Screen-printed cup 16oz", "quantity": 2200, "unit": "pcs", "color": "Merah / hitam / krem", "notes": "Cek final logo lockup"},
                {"item_code": "LB-M", "name": "Lunchbox medium", "quantity": 1000, "unit": "pcs", "color": "Merah / hitam / krem", "notes": ""},
            ],
            "operator_splits": [],
            "current_stage": 2,
            "packing_status": "Belum",
            "delivery_status": "Belum Terkirim",
        },
        {
            "po_number": "PO-1045",
            "outlet": "Nasi Sore — Cilandak",
            "customer": "Nasi Sore",
            "recipient": "Bayu",
            "address": "Cilandak Town Square, Jakarta Selatan",
            "payment_terms": "Net 14",
            "payment_status": "Approved",
            "design_status": "Approved",
            "sales_user": "Alfi",
            "sales": "Alfi",
            "created_by_user": None,
            "expedition": "Internal fleet",
            "delivery_zone": "Jakarta Selatan",
            "seller": "Alfi",
            "priority": "normal",
            "urgency_approved_by": None,
            "line_items": [
                {"item_code": "JP-1L", "name": "Jumbo plastic 1L", "quantity": 900, "unit": "pcs", "color": "Hitam", "notes": ""},
            ],
            "operator_splits": [],
            "current_stage": 5,
            "packing_status": "Belum",
            "delivery_status": "Belum Terkirim",
        },
    ]

    orders = []
    day_offsets = [0, 0, 1, 2]  # deadlines relative
    for i, base in enumerate(seed):
        total_qty = sum(li["quantity"] for li in base["line_items"])
        po_id = str(uuid.uuid4())
        order_date = (today - timedelta(days=1)).isoformat()
        deadline = (today + timedelta(days=day_offsets[i])).isoformat()
        order = {
            "id": po_id,
            "po_number": base["po_number"],
            "order_date": order_date,
            "estimated_delivery": deadline,
            "deadline": deadline,
            "priority": base["priority"],
            "urgency_approved_by": base["urgency_approved_by"],
            "customer": base["customer"],
            "outlet": base["outlet"],
            "recipient": base["recipient"],
            "address": base["address"],
            "payment_terms": base["payment_terms"],
            "payment_status": base["payment_status"],
            "design_status": base["design_status"],
            "sales_user": base["sales_user"],  # legacy field kept for backward compat
            "sales": base.get("sales"),
            "created_by_user": base.get("created_by_user"),
            "expedition": base["expedition"],
            "delivery_zone": base["delivery_zone"],
            "seller": base["seller"],
            "line_items": base["line_items"],
            "total_quantity": total_qty,
            "item_count": len(base["line_items"]),
            "operator_splits": base["operator_splits"],
            "design_files": [],
            "sample_files": [],
            "notes": "",
            "current_stage": base["current_stage"],
            "status_label": STAGES[base["current_stage"]],
            "packing_status": base["packing_status"],
            "delivery_status": base["delivery_status"],
            "delivery_note_number": "",
            "loading_checklist": None,
            "timeline": [],
            "created_at": order_date,
            "updated_at": now_iso(),
        }
        # Seed some timeline based on current_stage
        seeded_by = {"email": "admin@eternalpackaging.com", "role": "admin", "name": "Admin"}
        for s in range(base["current_stage"]):
            add_timeline(order, s, seeded_by, note="Data awal")
        orders.append(order)
    return orders


# ---------- Auth ----------
@api_router.get("/")
async def root():
    return {"message": "Eternal Packaging Operations API"}


@api_router.get("/system/health")
async def system_health():
    """Live health probe. Returns MongoDB connectivity status. Used by frontend
    connection indicator. NEVER expose secret/stack traces."""
    mongo_ok = False
    try:
        # Minimal ping — cheap operation that fails fast if DB unavailable
        await db.command("ping")
        mongo_ok = True
    except Exception:
        mongo_ok = False
    return {
        "mongo": mongo_ok,
        "mode": "operational" if mongo_ok else "degraded",
        "checked_at": now_iso(),
    }


@api_router.post("/auth/login")
async def login(input: LoginInput, response: Response):
    user = await db.users.find_one({"email": input.email.lower()})
    if not user or not bcrypt.checkpw(input.password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "Email atau kata sandi salah")
    if user.get("status", "active") != "active":
        raise HTTPException(403, "Akun Anda dinonaktifkan. Hubungi admin.")
    safe = {"id": str(user["_id"]), "email": user["email"], "name": user["name"], "role": user["role"]}
    # Cookie keeps existing web session behavior. access_token in the body is for
    # mobile/native clients that cannot rely on httpOnly cookies (Authorization: Bearer).
    token = token_for(safe)
    response.set_cookie("access_token", token, httponly=True, samesite="lax", max_age=28800)
    return {**safe, "access_token": token}


@api_router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


@api_router.get("/auth/me")
async def me(user=Depends(current_user)):
    perms = sorted(await user_permissions(user))
    is_sa = False
    if user.get("email"):
        u = await db.users.find_one({"email": user["email"]}, {"is_super_admin": 1})
        is_sa = bool(u and u.get("is_super_admin"))
    return {
        "id": user.get("user_id") or user.get("sub") or user.get("id"),
        "email": user.get("email"),
        "role": user.get("role"),
        "name": user.get("name"),
        "permissions": perms,
        "is_super_admin": is_sa,
    }


# ---------- Orders ----------
_ORDERS_SUMMARY_LIMIT = 500
_ORDERS_SUMMARY_STRIP_KEYS = (
    "design_files",
    "payment_files",
    "sample_files",
    "surat_jalan_files",
    "delivery_proofs",
    "timeline",
    "hold_reasons",
    "_has_design_files",
    "_has_payment_files",
)


def _orders_list_summary_projection() -> dict:
    """Lean list fields plus file-existence flags. Never returns file bytes."""
    return {
        "_id": 0,
        "id": 1,
        "po_number": 1,
        "customer": 1,
        "customer_name": 1,
        "outlet": 1,
        "item": 1,
        "product_name": 1,
        "total_quantity": 1,
        "current_stage": 1,
        "status_label": 1,
        "deadline": 1,
        "work_date": 1,
        "cancelled": 1,
        "created_at": 1,
        "design_status": 1,
        "design_bypassed": 1,
        "payment_status": 1,
        "payment_bypassed": 1,
        "_has_design_files": _mongo_array_nonempty_expr("design_files"),
        "_has_payment_files": _mongo_array_nonempty_expr("payment_files"),
        "line_items": {
            "$map": {
                "input": {"$ifNull": ["$line_items", []]},
                "as": "li",
                "in": {
                    "name": "$$li.name",
                    "quantity": "$$li.quantity",
                    "color": "$$li.color",
                },
            }
        },
    }


def _finalize_orders_summary_row(order: dict) -> dict:
    """Apply canonical HOLD using existence flags, then drop helper blobs."""
    order["design_files"] = [True] if order.get("_has_design_files") else []
    order["payment_files"] = [True] if order.get("_has_payment_files") else []
    _apply_hold_snapshot(order)
    if not order.get("customer_name"):
        order["customer_name"] = order.get("customer") or ""
    for key in _ORDERS_SUMMARY_STRIP_KEYS:
        order.pop(key, None)
    return order


def _orders_list_match(st: str, extra: Optional[dict] = None) -> dict:
    """Same Mongo filter as default GET /orders. Status extras stay post-filter."""
    q: dict = {}
    if st == "cancelled":
        q["cancelled"] = True
    else:
        q["cancelled"] = {"$ne": True}
    if extra:
        q.update(extra)
    return q


async def _list_orders_summary(st: str, extra: Optional[dict] = None) -> dict:
    """Mobile PO list: same status semantics, no file blobs, no stage_counts scan."""
    q = _orders_list_match(st, extra)
    raw = await db.orders.aggregate([
        {"$match": q},
        {"$sort": {"created_at": -1}},
        {"$limit": _ORDERS_SUMMARY_LIMIT},
        {"$project": _orders_list_summary_projection()},
    ]).to_list(_ORDERS_SUMMARY_LIMIT)
    orders = [_finalize_orders_summary_row(o) for o in raw]
    if st == "hold":
        orders = [o for o in orders if o.get("hold")]
    elif st == "aktif":
        orders = [o for o in orders if not o.get("hold") and o.get("current_stage", 0) < 14]
    elif st == "selesai":
        orders = [o for o in orders if o.get("current_stage", 0) >= 14]
    for o in orders:
        o.pop("hold", None)
        o.pop("design_status", None)
        o.pop("design_bypassed", None)
        o.pop("payment_status", None)
        o.pop("payment_bypassed", None)
        o.pop("cancelled", None)
    extra = extra or {}
    counts = {
        "aktif": await db.orders.count_documents({"cancelled": {"$ne": True}, "current_stage": {"$lt": 14}, **extra}),
        "selesai": await db.orders.count_documents({"cancelled": {"$ne": True}, "current_stage": {"$gte": 14}, **extra}),
        "cancelled": await db.orders.count_documents({"cancelled": True, **extra}),
    }
    return {
        "orders": orders,
        "last_sync": now_iso(),
        "counts": counts,
        "stage_counts": {},
        "status": st,
        "view": "summary",
    }


@api_router.get("/orders")
async def list_orders(
    status: Optional[str] = None,
    view: Optional[str] = None,
    user=Depends(current_user),
):
    """List orders. Optional `status` filter:
       - `aktif`   → non-cancelled, not HOLD, current_stage<14 (default)
       - `hold`    → non-cancelled, currently HOLD
       - `selesai` → non-cancelled, current_stage>=14
       - `cancelled` → cancelled only
       - `all`     → everything (used for admin/debug)
       - `stage_view` → non-cancelled ONLY (no HOLD/stage filter). Used by
         mobile workflow-shortcut list so `stage_counts` == filtered list.

       Optional `view=summary` is additive: lean list rows for /m/orders.
       Optional `view=dashboard` is additive: lean desktop `/` Dashboard rows
       with the same status filters, pending_queues, and stage_counts.
       Default (no view) keeps full documents + stage_counts for compatibility.
    """
    reject_sales_pwa_bypass(user)
    q: dict = {}
    st = (status or "aktif").lower()
    view_norm = (view or "").strip().lower()
    scope = sales_owner_match(user)
    if view_norm == "summary":
        return await _list_orders_summary(st, extra=scope)
    if view_norm == "dashboard":
        return await _list_orders_dashboard(st, extra=scope)
    if st == "cancelled":
        q["cancelled"] = True
    else:
        # Non-cancelled by default (Aktif/HOLD/Selesai/stage_view)
        q["cancelled"] = {"$ne": True}
    q.update(scope)
    orders = await db.orders.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    _dd_settings = await _get_delivery_deadline_settings()
    for o in orders:
        _apply_hold_snapshot(o)
        # Bagian 7: attach semantic work-queue membership so frontend can filter
        # via `?stage=<slug>` using the SAME predicate as the badge counts.
        o["pending_queues"] = _compute_pending_queues(o)
        # Batch 1: attach delivery deadline effective + status (source of truth
        # sama untuk Dashboard/Schedule/PODetail).
        _attach_deadline_snapshot(o, _dd_settings["lead_time_days"])
        # Task 14 bugfix: expose terkirim_confirmed juga di list (agar Jadwal
        # dan Timeline konsisten memakai source of truth yang sama).
        o["terkirim_confirmed"] = _terkirim_confirmed(o)
        _strip_tracking_secret(o)
    orders = [_strip_sj_bytes(o) for o in orders]
    # Post-filter by computed status when requested
    if st == "hold":
        orders = [o for o in orders if o.get("hold")]
    elif st == "aktif":
        orders = [o for o in orders if not o.get("hold") and o.get("current_stage", 0) < 14]
    elif st == "selesai":
        orders = [o for o in orders if o.get("current_stage", 0) >= 14]
    # stage_view / all / cancelled → no additional post-filter
    counts = {
        "aktif": await db.orders.count_documents({"cancelled": {"$ne": True}, "current_stage": {"$lt": 14}}),
        "selesai": await db.orders.count_documents({"cancelled": {"$ne": True}, "current_stage": {"$gte": 14}}),
        "cancelled": await db.orders.count_documents({"cancelled": True}),
    }
    # Bagian 7 & 11: canonical work-queue counts. Compute from ALL non-cancelled
    # orders (not filtered by status). Multi-membership allowed — same PO can
    # count di beberapa queue kalau memang outstanding di keduanya (mis. design
    # + payment approval sama-sama pending).
    all_active = await db.orders.find({"cancelled": {"$ne": True}}, {"_id": 0}).to_list(1000)
    for a in all_active:
        _apply_hold_snapshot(a)
    stage_counts: Dict[str, int] = {slug: 0 for slug in WORK_QUEUE_SLUGS}
    for a in all_active:
        for slug in _compute_pending_queues(a):
            stage_counts[slug] = stage_counts.get(slug, 0) + 1
    return {"orders": orders, "last_sync": now_iso(), "counts": counts, "stage_counts": stage_counts, "status": st}


_ORDERS_DASHBOARD_LIMIT = 500
_ORDERS_DASHBOARD_STAGE_LIMIT = 1000
_ORDERS_DASHBOARD_RESPONSE_STRIP = (
    "shipments",
    "line_items",
    "delivery_reschedules",
    "design_approved_at",
    "design_bypassed_at",
    "payment_approved_at",
    "payment_bypassed_at",
    "delivery_note_created_by",
    "quantity",
    "qty_packed",
    "tracking_token",
    "hold_reasons",
    "loading_checklist",
)


def _mongo_str_expr(expr) -> dict:
    return {"$convert": {"input": expr, "to": "string", "onError": "", "onNull": ""}}


def _mongo_nonempty_str_expr(expr) -> dict:
    return {"$gt": [{"$strLenCP": _mongo_str_expr(expr)}, 0]}


def _orders_dashboard_design_files_expr() -> dict:
    """Metadata + embedded-upload sentinel. Never returns data_url bytes."""
    return {
        "$map": {
            "input": {
                "$cond": [{"$isArray": "$design_files"}, "$design_files", []],
            },
            "as": "f",
            "in": {
                "name": {"$ifNull": ["$$f.name", ""]},
                "url": {
                    "$cond": [
                        _mongo_nonempty_str_expr("$$f.url"),
                        "$$f.url",
                        {
                            "$cond": [
                                _mongo_nonempty_str_expr("$$f.data_url"),
                                "embedded",
                                "",
                            ]
                        },
                    ]
                },
                "uploaded_at": {"$ifNull": ["$$f.uploaded_at", ""]},
            },
        }
    }


def _orders_dashboard_file_presence_expr(field: str) -> dict:
    return {
        "$cond": [
            _mongo_array_nonempty_expr(field),
            [{}],
            [],
        ]
    }


def _orders_dashboard_sj_files_expr() -> dict:
    """Live SJ identity/evidence without PDF/base64 bytes."""
    return {
        "$map": {
            "input": {
                "$cond": [{"$isArray": "$surat_jalan_files"}, "$surat_jalan_files", []],
            },
            "as": "f",
            "in": {
                "id": "$$f.id",
                "file_id": "$$f.file_id",
                "name": {"$ifNull": ["$$f.name", ""]},
                "uploaded_at": "$$f.uploaded_at",
                "is_deleted": "$$f.is_deleted",
                "has_file": "$$f.has_file",
                "storage_path": {
                    "$cond": [
                        {
                            "$or": [
                                _mongo_nonempty_str_expr("$$f.storage_path"),
                                {
                                    "$eq": [
                                        {"$substrCP": [_mongo_str_expr("$$f.data_url"), 0, 5]},
                                        "data:",
                                    ]
                                },
                            ]
                        },
                        "1",
                        "",
                    ]
                },
            },
        }
    }


def _orders_dashboard_sj_hist_expr() -> dict:
    return {
        "$map": {
            "input": {
                "$cond": [{"$isArray": "$sj_files"}, "$sj_files", []],
            },
            "as": "f",
            "in": {
                "id": "$$f.id",
                "name": {"$ifNull": ["$$f.name", ""]},
                "has_file": "$$f.has_file",
                "is_deleted": "$$f.is_deleted",
            },
        }
    }


def _orders_dashboard_line_items_expr() -> dict:
    return {
        "$map": {
            "input": {"$ifNull": ["$line_items", []]},
            "as": "li",
            "in": {
                "line_id": "$$li.line_id",
                "line_idx": "$$li.line_idx",
                "production_process": "$$li.production_process",
                "quantity": "$$li.quantity",
            },
        }
    }


def _orders_dashboard_operator_splits_expr() -> dict:
    return {
        "$map": {
            "input": {"$ifNull": ["$operator_splits", []]},
            "as": "s",
            "in": {
                "operator_name": "$$s.operator_name",
                "qty_completed": "$$s.qty_completed",
                "quantity": "$$s.quantity",
                "work_date": "$$s.work_date",
            },
        }
    }


def _orders_dashboard_shipments_expr() -> dict:
    return {
        "$map": {
            "input": {"$ifNull": ["$shipments", []]},
            "as": "sh",
            "in": {
                "id": "$$sh.id",
                "number": "$$sh.number",
                "sales_delivery_id": "$$sh.sales_delivery_id",
                "lines": {
                    "$map": {
                        "input": {"$ifNull": ["$$sh.lines", []]},
                        "as": "ln",
                        "in": {
                            "line_idx": "$$ln.line_idx",
                            "qty": "$$ln.qty",
                            "quantity": "$$ln.quantity",
                        },
                    }
                },
            },
        }
    }


def _orders_dashboard_loading_confirmations_expr() -> dict:
    return {
        "$map": {
            "input": {"$ifNull": ["$loading_confirmations", []]},
            "as": "c",
            "in": {
                "cancelled": "$$c.cancelled",
                "deleted": "$$c.deleted",
                "surat_jalan_id": "$$c.surat_jalan_id",
                "lines": {
                    "$map": {
                        "input": {"$ifNull": ["$$c.lines", []]},
                        "as": "ln",
                        "in": {
                            "line_idx": "$$ln.line_idx",
                            "qty": "$$ln.qty",
                            "quantity": "$$ln.quantity",
                        },
                    }
                },
            },
        }
    }


def _orders_dashboard_packing_info_expr() -> dict:
    return {
        "qty_packed": "$packing_info.qty_packed",
        "lines": {
            "$map": {
                "input": {"$ifNull": ["$packing_info.lines", []]},
                "as": "ln",
                "in": {
                    "line_id": "$$ln.line_id",
                    "line_idx": "$$ln.line_idx",
                    "packed_qty": "$$ln.packed_qty",
                },
            }
        },
    }


def _orders_dashboard_loading_info_expr() -> dict:
    return {
        "lines": {
            "$map": {
                "input": {"$ifNull": ["$loading_info.lines", []]},
                "as": "ln",
                "in": {
                    "line_idx": "$$ln.line_idx",
                    "loaded_qty": "$$ln.loaded_qty",
                },
            }
        },
        "events": {
            "$map": {
                "input": {"$ifNull": ["$loading_info.events", []]},
                "as": "ev",
                "in": {
                    "line_idx": "$$ev.line_idx",
                    "qty": "$$ev.qty",
                },
            }
        },
    }


def _orders_dashboard_reschedules_expr() -> dict:
    return {
        "$map": {
            "input": {"$ifNull": ["$delivery_reschedules", []]},
            "as": "r",
            "in": {
                "status": "$$r.status",
                "new_date": "$$r.new_date",
            },
        }
    }


def _orders_dashboard_stage_projection() -> dict:
    """Fields read by hold snapshot + `_compute_pending_queues` (transitive)."""
    return {
        "_id": 0,
        "cancelled": 1,
        "current_stage": 1,
        "design_bypassed": 1,
        "design_status": 1,
        "payment_bypassed": 1,
        "payment_status": 1,
        "sample_approved": 1,
        "total_quantity": 1,
        "quantity": 1,
        "delivery_note_number": 1,
        "delivery_note_created_at": 1,
        "delivery_note_created_by": 1,
        "qty_packed": 1,
        "qty_delivered": 1,
        "delivery_status": 1,
        "loading_completed": 1,
        "customer_approval.status": 1,
        "screener_info.screen_qty": 1,
        "helper_prep.qty_prepared": 1,
        "loading_checklist.items_loaded": 1,
        "loading_checklist.seal_ok": 1,
        "design_files": _orders_dashboard_design_files_expr(),
        "payment_files": _orders_dashboard_file_presence_expr("payment_files"),
        "sample_files": _orders_dashboard_file_presence_expr("sample_files"),
        "surat_jalan_files": _orders_dashboard_sj_files_expr(),
        "line_items": _orders_dashboard_line_items_expr(),
        "operator_splits": _orders_dashboard_operator_splits_expr(),
        "shipments": _orders_dashboard_shipments_expr(),
        "loading_confirmations": _orders_dashboard_loading_confirmations_expr(),
        "packing_info": _orders_dashboard_packing_info_expr(),
        "loading_info": _orders_dashboard_loading_info_expr(),
    }


def _orders_dashboard_list_projection() -> dict:
    """Stage-count fields plus Dashboard card / StageBar evidence."""
    proj = _orders_dashboard_stage_projection()
    proj.update({
        "id": 1,
        "po_number": 1,
        "outlet": 1,
        "item_count": 1,
        "priority": 1,
        "deadline": 1,
        "work_date": 1,
        "cancelled_by": 1,
        "cancelled_by_name": 1,
        "cancelled_at": 1,
        "cancel_reason": 1,
        "tanggal_pengiriman": 1,
        "effective_delivery_date": 1,
        "delivery_deadline_effective": 1,
        "scheduled_delivery_date": 1,
        "delivery_deadline_manual": 1,
        "buffer_stock": 1,
        "design_approved_at": 1,
        "design_bypassed_at": 1,
        "payment_approved_at": 1,
        "payment_bypassed_at": 1,
        "print_completed_qty": 1,
        "print_qty_target": 1,
        "operator_assigned_qty": 1,
        "print_qty_total": 1,
        "print_status": 1,
        "requires_printing": 1,
        "surat_jalan_created": 1,
        "surat_jalan_status_semantic": 1,
        "loading_qty_status": 1,
        "loading_verification_status": 1,
        "eligible_delivery_qty": 1,
        "terkirim_confirmed": 1,
        "sj_files": _orders_dashboard_sj_hist_expr(),
        "delivery_reschedules": _orders_dashboard_reschedules_expr(),
    })
    return proj


def _dashboard_design_file_evidence(files) -> list:
    """Python equivalent of `_orders_dashboard_design_files_expr` for tests."""
    out = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        url = str(f.get("url") or "").strip()
        if not url and str(f.get("data_url") or "").strip():
            url = "embedded"
        out.append({
            "name": f.get("name") or "",
            "url": url,
            "uploaded_at": f.get("uploaded_at") or "",
        })
    return out


def _dashboard_sj_file_evidence(files) -> list:
    out = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        data_url = str(f.get("data_url") or "")
        storage = str(f.get("storage_path") or "").strip()
        out.append({
            "id": f.get("id"),
            "file_id": f.get("file_id"),
            "name": f.get("name") or "",
            "uploaded_at": f.get("uploaded_at"),
            "is_deleted": f.get("is_deleted"),
            "has_file": f.get("has_file"),
            "storage_path": "1" if (storage or data_url.startswith("data:")) else "",
        })
    return out


def _shape_order_for_dashboard_compute(order: dict) -> dict:
    """In-memory mirror of the dashboard Mongo inclusion projection (tests/parity)."""
    src = order or {}
    shaped = {
        "cancelled": src.get("cancelled"),
        "current_stage": src.get("current_stage"),
        "design_bypassed": src.get("design_bypassed"),
        "design_status": src.get("design_status"),
        "payment_bypassed": src.get("payment_bypassed"),
        "payment_status": src.get("payment_status"),
        "sample_approved": src.get("sample_approved"),
        "total_quantity": src.get("total_quantity"),
        "quantity": src.get("quantity"),
        "delivery_note_number": src.get("delivery_note_number"),
        "delivery_note_created_at": src.get("delivery_note_created_at"),
        "delivery_note_created_by": src.get("delivery_note_created_by"),
        "qty_packed": src.get("qty_packed"),
        "qty_delivered": src.get("qty_delivered"),
        "delivery_status": src.get("delivery_status"),
        "loading_completed": src.get("loading_completed"),
        "design_files": _dashboard_design_file_evidence(src.get("design_files")),
        "payment_files": [{}] if src.get("payment_files") else [],
        "sample_files": [{}] if src.get("sample_files") else [],
        "surat_jalan_files": _dashboard_sj_file_evidence(src.get("surat_jalan_files")),
        "line_items": [
            {
                "line_id": li.get("line_id"),
                "line_idx": li.get("line_idx"),
                "production_process": li.get("production_process"),
                "quantity": li.get("quantity"),
            }
            for li in (src.get("line_items") or [])
            if isinstance(li, dict)
        ],
        "operator_splits": [
            {
                "operator_name": s.get("operator_name"),
                "qty_completed": s.get("qty_completed"),
                "quantity": s.get("quantity"),
                "work_date": s.get("work_date"),
            }
            for s in (src.get("operator_splits") or [])
            if isinstance(s, dict)
        ],
        "shipments": [
            {
                "id": sh.get("id"),
                "number": sh.get("number"),
                "sales_delivery_id": sh.get("sales_delivery_id"),
                "lines": [
                    {
                        "line_idx": ln.get("line_idx"),
                        "qty": ln.get("qty"),
                        "quantity": ln.get("quantity"),
                    }
                    for ln in (sh.get("lines") or [])
                    if isinstance(ln, dict)
                ],
            }
            for sh in (src.get("shipments") or [])
            if isinstance(sh, dict)
        ],
        "loading_confirmations": [
            {
                "cancelled": c.get("cancelled"),
                "deleted": c.get("deleted"),
                "surat_jalan_id": c.get("surat_jalan_id"),
                "lines": [
                    {
                        "line_idx": ln.get("line_idx"),
                        "qty": ln.get("qty"),
                        "quantity": ln.get("quantity"),
                    }
                    for ln in (c.get("lines") or [])
                    if isinstance(ln, dict)
                ],
            }
            for c in (src.get("loading_confirmations") or [])
            if isinstance(c, dict)
        ],
        "packing_info": {
            "qty_packed": (src.get("packing_info") or {}).get("qty_packed"),
            "lines": [
                {
                    "line_id": ln.get("line_id"),
                    "line_idx": ln.get("line_idx"),
                    "packed_qty": ln.get("packed_qty"),
                }
                for ln in ((src.get("packing_info") or {}).get("lines") or [])
                if isinstance(ln, dict)
            ],
        },
        "loading_info": {
            "lines": [
                {"line_idx": ln.get("line_idx"), "loaded_qty": ln.get("loaded_qty")}
                for ln in ((src.get("loading_info") or {}).get("lines") or [])
                if isinstance(ln, dict)
            ],
            "events": [
                {"line_idx": ev.get("line_idx"), "qty": ev.get("qty")}
                for ev in ((src.get("loading_info") or {}).get("events") or [])
                if isinstance(ev, dict)
            ],
        },
    }
    snap = src.get("customer_approval")
    if isinstance(snap, dict):
        shaped["customer_approval"] = {"status": snap.get("status")}
    si = src.get("screener_info")
    if isinstance(si, dict):
        shaped["screener_info"] = {"screen_qty": si.get("screen_qty")}
    hp = src.get("helper_prep")
    if isinstance(hp, dict):
        shaped["helper_prep"] = {"qty_prepared": hp.get("qty_prepared")}
    lc = src.get("loading_checklist")
    if isinstance(lc, dict):
        shaped["loading_checklist"] = {
            "items_loaded": lc.get("items_loaded"),
            "seal_ok": lc.get("seal_ok"),
        }
    return shaped


def _finalize_orders_dashboard_row(order: dict) -> dict:
    """Drop server-only helper fields after authoritative compute."""
    snap = order.get("customer_approval")
    if isinstance(snap, dict):
        order["customer_approval"] = {"status": snap.get("status")}
    ver = order.get("loading_verification_status")
    if isinstance(ver, dict):
        order["loading_verification_status"] = {
            "code": ver.get("code"),
            "label": ver.get("label"),
        }
    confs = order.get("loading_confirmations")
    if isinstance(confs, list):
        order["loading_confirmations"] = [{} for c in confs if isinstance(c, dict)]
    for key in _ORDERS_DASHBOARD_RESPONSE_STRIP:
        order.pop(key, None)
    return _strip_sj_bytes(order)


async def _orders_dashboard_tab_counts(extra: Optional[dict] = None) -> dict:
    extra = extra or {}
    return {
        "aktif": await db.orders.count_documents({"cancelled": {"$ne": True}, "current_stage": {"$lt": 14}, **extra}),
        "selesai": await db.orders.count_documents({"cancelled": {"$ne": True}, "current_stage": {"$gte": 14}, **extra}),
        "cancelled": await db.orders.count_documents({"cancelled": True, **extra}),
    }


async def _list_orders_dashboard(st: str, extra: Optional[dict] = None) -> dict:
    """Desktop `/` Dashboard: same status + queue semantics, lean documents."""
    q = _orders_list_match(st, extra)
    raw = await db.orders.aggregate([
        {"$match": q},
        {"$sort": {"created_at": -1}},
        {"$limit": _ORDERS_DASHBOARD_LIMIT},
        {"$project": _orders_dashboard_list_projection()},
    ]).to_list(_ORDERS_DASHBOARD_LIMIT)
    _dd_settings = await _get_delivery_deadline_settings()
    for o in raw:
        _apply_hold_snapshot(o)
        o["pending_queues"] = _compute_pending_queues(o)
        _attach_deadline_snapshot(o, _dd_settings["lead_time_days"])
        o["terkirim_confirmed"] = _terkirim_confirmed(o)
        _strip_tracking_secret(o)
    orders = [_finalize_orders_dashboard_row(o) for o in raw]
    if st == "hold":
        orders = [o for o in orders if o.get("hold")]
    elif st == "aktif":
        orders = [o for o in orders if not o.get("hold") and o.get("current_stage", 0) < 14]
    elif st == "selesai":
        orders = [o for o in orders if o.get("current_stage", 0) >= 14]
    counts = await _orders_dashboard_tab_counts(extra)
    stage_match = {"cancelled": {"$ne": True}}
    if extra:
        stage_match.update(extra)
    stage_raw = await db.orders.aggregate([
        {"$match": stage_match},
        {"$limit": _ORDERS_DASHBOARD_STAGE_LIMIT},
        {"$project": _orders_dashboard_stage_projection()},
    ]).to_list(_ORDERS_DASHBOARD_STAGE_LIMIT)
    for a in stage_raw:
        _apply_hold_snapshot(a)
    stage_counts: Dict[str, int] = {slug: 0 for slug in WORK_QUEUE_SLUGS}
    for a in stage_raw:
        for slug in _compute_pending_queues(a):
            stage_counts[slug] = stage_counts.get(slug, 0) + 1
    return {
        "orders": orders,
        "last_sync": now_iso(),
        "counts": counts,
        "stage_counts": stage_counts,
        "status": st,
        "view": "dashboard",
    }


@api_router.get("/orders/{order_id}")
async def get_order(order_id: str, user=Depends(current_user)):
    reject_sales_pwa_bypass(user)
    query = {"id": order_id}
    query.update(sales_owner_match(user))
    order = await db.orders.find_one(query, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    if user.get("role") != "admin":
        eff = await user_permissions(user)
        view_ok = any(p in eff for p in (
            "po.view", "work_panel.view", "schedule.view",
            "screen.update", "helper.prepare", "operator.update",
            "packing.update", "delivery_note.view", "delivery_note.manage",
            "loading.update", "delivery.update", "design.view", "payment.view",
        ))
        if not view_ok:
            raise HTTPException(403, "Anda tidak punya permission untuk melihat PO ini")
    _apply_hold_snapshot(order)
    # Workflow 06-12 targeted bugfix: expose prereq status untuk UI gating.
    order["packing_prereq_reasons"] = _prereq_reasons_for_packing(order)
    order["packing_ready"] = len(order["packing_prereq_reasons"]) == 0
    # Task 12 bugfix: expose semantic packing status.
    order["packing_status_semantic"] = _packing_status_semantic(order)
    # Task 13 bugfix: expose SJ completion + Loading gate.
    order["surat_jalan_created"] = _surat_jalan_created(order)
    order["loading_prereq_reasons"] = _prereq_reasons_for_loading(order)
    order["loading_ready"] = len(order["loading_prereq_reasons"]) == 0
    # Task 14 bugfix: expose Loading completion + Terkirim gate.
    order["loading_completed"] = _loading_completed(order)
    order["terkirim_prereq_reasons"] = _prereq_reasons_for_terkirim(order)
    order["terkirim_ready"] = len(order["terkirim_prereq_reasons"]) == 0
    order["terkirim_confirmed"] = _terkirim_confirmed(order)
    _attach_quantity_snapshot(order)
    # quantity snapshot already attaches workflow_ready flags
    # Batch 1: delivery deadline + sales delivery summary are independent reads
    _dd_settings, _sales_delivery_summary = await asyncio.gather(
        _get_delivery_deadline_settings(),
        _get_po_delivery_summary(db, order),
    )
    _attach_deadline_snapshot(order, _dd_settings["lead_time_days"])
    attach_schedule_snapshot(order)
    order["sales_delivery_summary"] = _sales_delivery_summary
    try:
        from services.line_product_replacement import evaluate_all_line_product_replacements
        order["line_product_replace"] = await evaluate_all_line_product_replacements(db, order)
    except Exception:
        order["line_product_replace"] = []
    _strip_tracking_secret(order)
    return {"order": _strip_inline_attachment_bytes(_strip_sj_bytes(order))}


def _check_stage_permission(current_stage: int, role: str):
    allowed = STAGE_EDIT_ROLES.get(current_stage, ["admin", "head_production"])
    if role not in allowed and role != "admin":
        raise HTTPException(403, "Role Anda tidak diizinkan memajukan tahap ini")


# ---- Delivery Deadline (Batch 1) ----
# Setting disimpan di app_settings key "delivery_deadline".
# Format: {"lead_time_days": 1|3|5}. Calendar days (existing seed/deadline
# calculation semua pakai timedelta(days=X); tidak ada working-day helper di
# aplikasi ini).
_DELIVERY_DEADLINE_DEFAULT = {"lead_time_days": 3}
_DELIVERY_DEADLINE_ALLOWED_DAYS = [1, 3, 5]


async def _get_delivery_deadline_settings() -> dict:
    doc = await db.app_settings.find_one({"key": "delivery_deadline"}, {"_id": 0})
    if not doc:
        return dict(_DELIVERY_DEADLINE_DEFAULT)
    return {"lead_time_days": int(doc.get("lead_time_days", _DELIVERY_DEADLINE_DEFAULT["lead_time_days"]))}


def _latest_approval_at(order: dict) -> Optional[str]:
    """Compatibility wrapper — authoritative logic lives in tanggal_pengiriman."""
    from services.tanggal_pengiriman import latest_required_approval_at

    latest = latest_required_approval_at(order)
    return latest.isoformat() if latest else None


def _calculated_delivery_deadline(order: dict, lead_time_days: int) -> Optional[str]:
    from services.tanggal_pengiriman import automatic_tanggal_pengiriman, calendar_date_to_wib_iso

    key = automatic_tanggal_pengiriman(order, lead_time_days)
    return calendar_date_to_wib_iso(key) if key else None


def _deadline_status_for(deadline_iso: Optional[str], order: dict) -> str:
    """Predicate priority: overdue > h6 > h1 > normal. Kalau PO Terkirim →
    'terkirim' (warning aktif hilang; Tanggal Pengiriman tetap untuk audit).

    Buffer Stock is not a customer SLA commitment: never return overdue.
    Missing/None/false buffer_stock is treated as a normal PO.
    """
    if order.get("delivery_status") == "Terkirim" or (order.get("current_stage") or 0) >= 14:
        return "terkirim"
    if not deadline_iso:
        return "unknown"
    try:
        dt = datetime.fromisoformat(str(deadline_iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return "unknown"
    now = datetime.now(timezone.utc)
    delta = dt - now
    if delta.total_seconds() <= 0:
        return "normal" if bool(order.get("buffer_stock")) else "overdue"
    hours = delta.total_seconds() / 3600.0
    if hours <= 6:
        return "h6"
    if hours <= 24:
        return "h1"
    return "normal"


def _attach_deadline_snapshot(order: dict, lead_time_days: int):
    """Attach authoritative Tanggal Pengiriman (+ legacy delivery_deadline_* mirrors).

    Manual override lives in delivery_deadline_manual / scheduled_delivery_date.
    Legacy order.deadline is never used as the scheduling source.
    """
    from services.tanggal_pengiriman import attach_tanggal_pengiriman_snapshot

    attach_tanggal_pengiriman_snapshot(order, lead_time_days)
    order["delivery_deadline_status"] = _deadline_status_for(
        order.get("delivery_deadline_effective"), order
    )


_DERIVED_ORDER_KEYS = (
    "packing_prereq_reasons", "packing_ready", "packing_status_semantic",
    "surat_jalan_created", "loading_prereq_reasons", "loading_ready",
    "loading_completed", "terkirim_prereq_reasons", "terkirim_ready",
    "terkirim_confirmed",     "delivery_deadline_calculated",
    "delivery_deadline_effective", "delivery_deadline_source",
    "delivery_deadline_lead_time_days", "delivery_deadline_status",
    "tanggal_pengiriman", "tanggal_pengiriman_source",
    "tanggal_pengiriman_calculated", "tanggal_pengiriman_lead_time_days",
    "requires_printing", "print_qty_total", "packing_required_total",
    "non_print_qty_total", "print_completed_qty", "available_for_packing",
    "operator_assigned_qty", "helper_prepared_print_qty",
    "helper_target_qty", "helper_over_prepared_qty",
    "operator_assignment_cap", "print_available_for_packing",
    "print_qty_target", "print_qty_remaining", "print_status",
    "workflow_ready", "operator_unlocked", "screener_gate_reason",
    "print_qty_gate_reason",
    "packing_lines",
    "shipment_lines",
    "surat_jalan_status_semantic",
    "sj_files",
    "loading_qty_status",
    "loading_verification_status",
    "eligible_delivery_qty",
    "effective_delivery_date", "delivery_schedule_status",
    "delivery_history", "terkirim_total_po_qty", "terkirim_remaining_qty",
    "sj_integrity",
    "sales_ownership_admin_label",
)


def _attach_quantity_snapshot(order: dict) -> None:
    """Derived qty fields (non-persisted) untuk UI + invariant checks."""
    attach_sales_ownership_admin_fields(order)
    order["requires_printing"] = _requires_printing(order)
    order["print_qty_total"] = _print_qty_total(order)
    order["non_print_qty_total"] = _non_print_qty_total(order)
    order["packing_required_total"] = _packing_required_total(order)
    order["print_completed_qty"] = _print_completed_qty(order)
    order["operator_assigned_qty"] = _operator_assigned_qty(order)
    order["helper_prepared_print_qty"] = _helper_prepared_print_qty(order)
    order["helper_target_qty"] = _qr_helper_target_qty(order)
    order["helper_over_prepared_qty"] = _qr_helper_over_prepared_qty(order)
    order["operator_assignment_cap"] = _operator_assignment_cap(order)
    order["print_available_for_packing"] = _print_available_for_packing(order)
    order["available_for_packing"] = _available_for_packing(order)
    order["print_qty_target"] = _qr_print_qty_target(order)
    order["print_qty_remaining"] = _qr_print_qty_remaining(order)
    order["print_status"] = _qr_print_status(order)
    order["packing_lines"] = _qr_packing_line_breakdown(order)
    order["shipment_lines"] = _qr_shipment_line_breakdown(order)
    order["surat_jalan_status_semantic"] = _qr_surat_jalan_status_semantic(order)
    order["sj_files"] = _sj_history_entries(order)
    order["loading_qty_status"] = _qr_loading_status_semantic(order)
    order["loading_verification_status"] = _qr_loading_verification_status(order)
    order["eligible_delivery_qty"] = _qr_eligible_delivery_qty(order)
    try:
        from services.sj_integrity import order_integrity_summary
        order["sj_integrity"] = order_integrity_summary(order)
    except Exception:
        order["sj_integrity"] = None
    attach_delivery_history(order)
    _attach_workflow_snapshot(order)


def _attach_workflow_snapshot(order: dict) -> None:
    """Derived workflow flags (non-persisted). Backend is source of truth."""
    ready = _wg_workflow_ready(order)
    order["workflow_ready"] = ready
    order["operator_unlocked"] = bool(ready.get("operator"))
    order["screener_gate_reason"] = _wg_screener_gate_reason(order, for_sample=True)
    order["print_qty_gate_reason"] = _wg_print_qty_gate_reason(order)


def _sync_workflow_stage(order: dict, *, force: bool = False) -> bool:
    """Idempotent evidence-based current_stage bump. No timeline side effects."""
    return _wg_sync_workflow_stage(order, force=force)


def _sync_dispatch_stage(order: dict) -> bool:
    """Increase-only current_stage from Terkirim evidence.

    Loading no longer advances stage. SJ stays at 12 until full delivery (14).
    Partial delivery never jumps the whole PO to Terkirim.
    """
    cs = int(order.get("current_stage") or 0)
    changed = False
    if _qr_is_fully_delivered(order) and cs < 14:
        order["current_stage"] = 14
        changed = True
    return changed


def _ensure_screener_for_operator_work(order: dict, *, for_sample: bool = False):
    reason = _wg_screener_gate_reason(order, for_sample=for_sample)
    if reason:
        raise HTTPException(400, reason)


def _ensure_helper_for_operator_work(order: dict):
    reason = _wg_helper_gate_reason(order)
    if reason:
        raise HTTPException(400, reason)


def _ensure_print_qty_allowed(order: dict):
    reason = _wg_print_qty_gate_reason(order)
    if reason:
        raise HTTPException(400, reason)


_QUANTITY_LOCK_RETRIES = 5
_CONFLICT_MSG = "PO baru saja diubah oleh user lain. Muat ulang halaman lalu coba lagi."


async def _load_order_for_mutation(order_id: str) -> dict:
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    return order


async def _with_quantity_lock(order_id: str, mutate):
    """Atomic optimistic lock + retry untuk mutasi qty kritis.

    `mutate(order)` memvalidasi & memodifikasi order in-place.
    Setiap attempt: baca fresh → validasi → update dengan filter updated_at.
    """
    last_token = None
    for attempt in range(_QUANTITY_LOCK_RETRIES):
        order = await _load_order_for_mutation(order_id)
        lock_token = order.get("updated_at")
        last_token = lock_token
        mutate(order)
        order["_expected_updated_at"] = lock_token
        try:
            return await _persist_order(order, require_lock=True)
        except HTTPException as exc:
            if exc.status_code == 409 and attempt < _QUANTITY_LOCK_RETRIES - 1:
                continue
            raise
    raise HTTPException(409, _CONFLICT_MSG)


async def _persist_order(order, *, require_lock: bool = False, persist_keys: Optional[tuple] = None):
    expected_updated = order.pop("_expected_updated_at", None)
    if require_lock and not expected_updated:
        raise HTTPException(409, _CONFLICT_MSG)
    order["updated_at"] = now_iso()
    if order.get("cancelled"):
        order["status_label"] = "Cancelled"
    else:
        order["status_label"] = STAGES[min(order["current_stage"], len(STAGES) - 1)]
    _apply_hold_snapshot(order)
    # Never let a stripped API projection wipe Surat Jalan bytes on the next persist.
    incoming_files = order.get("surat_jalan_files")
    merge_sj = persist_keys is None or "surat_jalan_files" in persist_keys
    if merge_sj and isinstance(incoming_files, list) and incoming_files:
        existing = await db.orders.find_one({"id": order["id"]}, {"surat_jalan_files": 1, "_id": 0})
        old_by_id = {
            str(f.get("id") or ""): f
            for f in ((existing or {}).get("surat_jalan_files") or [])
            if isinstance(f, dict) and f.get("id")
        }
        if old_by_id:
            merged = []
            for f in incoming_files:
                if not isinstance(f, dict):
                    continue
                old = old_by_id.get(str(f.get("id") or ""))
                if old:
                    if not str(f.get("storage_path") or "").strip() and old.get("storage_path"):
                        f = {**f, "storage_path": old.get("storage_path")}
                    if not str(f.get("data_url") or "").startswith("data:") and str(old.get("data_url") or "").startswith("data:"):
                        f = {**f, "data_url": old.get("data_url")}
                merged.append(f)
            order["surat_jalan_files"] = merged
    # DB write pakai field yang persistable (skip yang derived).
    to_write = {k: v for k, v in order.items() if k not in _DERIVED_ORDER_KEYS}
    if persist_keys is not None:
        allowed = set(persist_keys) | {"updated_at"}
        to_write = {k: v for k, v in to_write.items() if k in allowed}
    query = {"id": order["id"]}
    if expected_updated:
        query["updated_at"] = expected_updated
    elif require_lock:
        raise HTTPException(409, _CONFLICT_MSG)
    result = await db.orders.update_one(
        query,
        {"$set": to_write},
        upsert=not bool(expected_updated or require_lock),
    )
    if expected_updated and result.matched_count == 0:
        raise HTTPException(409, _CONFLICT_MSG)
    if require_lock and result.modified_count == 0 and result.matched_count == 0:
        raise HTTPException(409, _CONFLICT_MSG)
    # Attach derived fields untuk response (workflow 06-12 bugfix).
    order["packing_prereq_reasons"] = _prereq_reasons_for_packing(order)
    order["packing_ready"] = len(order["packing_prereq_reasons"]) == 0
    # Task 12 bugfix: derived packing_status semantic — konsistensi antara
    # Detail PO dan Jadwal Produksi. Ignores stale persisted "Sudah" untuk PO
    # yang current_stage < 11.
    order["packing_status_semantic"] = _packing_status_semantic(order)
    # Task 13 bugfix: derived Surat Jalan state.
    order["surat_jalan_created"] = _surat_jalan_created(order)
    order["loading_prereq_reasons"] = _prereq_reasons_for_loading(order)
    order["loading_ready"] = len(order["loading_prereq_reasons"]) == 0
    # Task 14 bugfix: derived Loading + Terkirim state.
    order["loading_completed"] = _loading_completed(order)
    order["terkirim_prereq_reasons"] = _prereq_reasons_for_terkirim(order)
    order["terkirim_ready"] = len(order["terkirim_prereq_reasons"]) == 0
    order["terkirim_confirmed"] = _terkirim_confirmed(order)
    _attach_quantity_snapshot(order)
    _attach_workflow_snapshot(order)
    # Batch 1: attach deadline snapshot (best-effort — safe kalau settings belum ada).
    try:
        # NOTE: sync call di persist path — read settings sekali per persist.
        # Motor collection method returns coroutine; namun kita fetch dari cache
        # tidak available. Simplify: refresh via a lightweight fetch.
        _dd_doc = await db.app_settings.find_one({"key": "delivery_deadline"}, {"_id": 0})
        _lt = int((_dd_doc or {}).get("lead_time_days", _DELIVERY_DEADLINE_DEFAULT["lead_time_days"]))
        _attach_deadline_snapshot(order, _lt)
    except Exception:
        pass
    attach_schedule_snapshot(order)
    _strip_tracking_secret(order)
    public = _strip_sj_bytes(order)
    if public is not order:
        order["surat_jalan_files"] = public.get("surat_jalan_files") or []
        order["sj_files"] = public.get("sj_files") or order.get("sj_files")
    return order


def _packing_status_semantic(order: dict) -> str:
    """Belum / Berjalan / Sudah — quantity-aware (partial packing → Berjalan)."""
    return _qr_packing_status_semantic(order)


def _ensure_not_cancelled(order: dict):
    """Guard: refuse any workflow mutation on a Cancelled PO. History is preserved."""
    if order.get("cancelled"):
        raise HTTPException(409, {
            "message": "PO sudah dibatalkan (Cancelled). Aksi workflow ditolak.",
            "po_number": order.get("po_number"),
            "cancelled_at": order.get("cancelled_at"),
            "cancel_reason": order.get("cancel_reason"),
        })


# ---- Approval / Bypass / HOLD helpers (Phase A) --------------------------
def _design_ok(order: dict) -> bool:
    return _wg_design_ok(order)


def _payment_ok(order: dict) -> bool:
    return _wg_payment_ok(order)


def _hold_reasons(order: dict) -> list:
    reasons = []
    if not order.get("design_files"):
        reasons.append("Belum ada design")
    elif not _design_ok(order):
        reasons.append("Menunggu approval design")
    if not order.get("payment_files"):
        reasons.append("Belum ada bukti payment")
    elif order.get("payment_status") != "Approved" and not order.get("payment_bypassed"):
        reasons.append("Menunggu approval payment")
    return reasons


def _apply_hold_snapshot(order: dict):
    """Compute HOLD flag on the order. HOLD applies until the PO has passed
    the Approval Payment gate and entered Penjadwalan (stage 5)."""
    if order.get("current_stage", 0) >= 5:
        order["hold"] = False
        order["hold_reasons"] = []
    else:
        reasons = _hold_reasons(order)
        order["hold"] = bool(reasons)
        order["hold_reasons"] = reasons


# ---------- Semantic Work Queues (Bagian 7) ----------
# Slug → human label; slug juga menjadi query param `?stage=<slug>` di frontend.
WORK_QUEUE_SLUGS = [
    "order_in",           # 01
    "design",             # 02
    "approval_design",    # 03
    "payment",            # 04
    "approval_payment",   # 05
    "scheduling",         # 06
    "screener",           # 07
    "helper",             # 08
    "operator",           # 09
    "sample",             # 10
    "printing",           # 11
    "packing",            # 12
    "delivery_note",      # 13
    "loading",            # 14
    "driver",             # 15
    "delivered",          # 16
]


def _compute_pending_queues(order: dict) -> List[str]:
    """Canonical resolver: mengembalikan daftar work-queue slug tempat PO ini
    masih outstanding. SATU PO boleh muncul di BEBERAPA queue jika beberapa
    action independen belum selesai (mis. Approval Design + Approval Payment
    keduanya pending). Cancelled selalu return [].

    Digunakan oleh badge count DAN filter list — predicate SAMA di kedua tempat.
    """
    if order.get("cancelled"):
        return []
    queues: List[str] = []
    cs = int(order.get("current_stage") or 0)
    design_files = order.get("design_files") or []
    payment_files = order.get("payment_files") or []
    design_approved = _design_ok(order)
    payment_approved = (order.get("payment_status") == "Approved") or order.get("payment_bypassed")

    # Delivered (final): PO benar-benar sudah di stage terkirim (new index 14).
    if cs >= 14:
        return ["delivered"]

    # 01 Order Masuk — PO baru masuk (stage 0)
    if cs == 0:
        queues.append("order_in")

    # 02 Design — belum ada file design (dan design belum bypass/approved)
    if not design_files and not design_approved:
        queues.append("design")

    # 03 Approval Design — design file sudah ada tetapi belum approved dan tidak bypassed
    if design_files and not design_approved:
        queues.append("approval_design")

    # 04 Payment — bukti payment belum ada dan payment belum bypass/approved
    if not payment_files and not order.get("payment_bypassed") and order.get("payment_status") != "Approved":
        queues.append("payment")

    # 05 Approval Payment — bukti payment sudah ada tetapi belum approved dan tidak bypassed
    if payment_files and not order.get("payment_bypassed") and order.get("payment_status") != "Approved":
        queues.append("approval_payment")

    # 06 Penjadwalan — HOLD gate terpenuhi TAPI belum dijadwalkan.
    hold_gate_ok = bool(design_approved and payment_approved)
    if hold_gate_ok and not _stage_penjadwalan_done(order) and cs < 12:
        queues.append("scheduling")

    # 07-11 Semantic / parallel queues — NOT current_stage linear.
    # Screener dan Helper boleh outstanding bersamaan. Operator butuh Screener.
    req_print = _requires_printing(order)
    if hold_gate_ok and _stage_penjadwalan_done(order) and req_print and cs < 12:
        if not _stage_screener_done(order):
            queues.append("screener")
        if not _stage_helper_done(order):
            queues.append("helper")
        if _stage_screener_done(order):
            if not _sample_uploaded(order):
                queues.append("operator")
            elif not _sample_ok(order):
                queues.append("sample")
            elif not _print_qty_input(order):
                queues.append("printing")

    if cs == 11:
        queues.append("packing")
    elif not _surat_jalan_created(order) and cs >= 11:
        queues.append("delivery_note")
    elif _surat_jalan_created(order) and not _terkirim_confirmed(order):
        # Early sales-delivery / SJ allocation must not skip Loading work.
        # Driver/Pengiriman queue only when physically eligible to deliver.
        if _qr_eligible_delivery_qty(order) > 0:
            queues.append("driver")
        else:
            queues.append("loading")

    return queues


async def _audit_po(order: dict, action: str, actor: dict, before=None, after=None, note: str = ""):
    await _write_audit(
        action=action, actor=actor,
        target_email=f"po:{order.get('po_number')}",
        before=before, after=after, note=note,
    )


async def _require_perm(user: dict, perm: str):
    """Deny if the user lacks `perm` in their effective set (admin still passes)."""
    reject_sales_pwa_bypass(user)
    if user.get("role") == "admin":
        return
    eff = await user_permissions(user)
    if perm not in eff:
        raise HTTPException(403, f"Anda tidak punya permission '{perm}'")


# ---- Workflow 06-12 prerequisite validator (targeted bugfix) --------------
# Central rule: menandai suatu tahap "selesai" harus berdasarkan data nyata,
# BUKAN sekadar current_stage > idx. Setiap tahap punya predikat semantik.
# STAGES index: 5=Penjadwalan, 6=Screener, 7=Persiapan Helper, 8=Operator,
#               9=Konfirmasi Sample, 10=Cetak, 11=Packing.

def _stage_screener_done(order: dict) -> bool:
    """Screener selesai jika screen_qty telah diinput (int/float, bukan None/'').
    Revisi routing: Screener adalah persiapan alat cetak. Full non-print PO
    (requires_printing==False) TIDAK DIPERLUKAN Screener."""
    return _wg_screener_done(order)


def _stage_helper_done(order: dict) -> bool:
    """Helper selesai jika qty_prepared telah diinput (int/float, bukan None/'').
    Nilai 0 yang eksplisit tetap dihitung sebagai "sudah diinput".
    Revisi routing: kalau PO full non-print (requires_printing == False), tahap
    Persiapan Helper TIDAK DIPERLUKAN → langsung dianggap done. Jangan memaksa
    user mengisi Qty Helper untuk PO tanpa barang cetak."""
    return _wg_helper_done(order)


def _stage_penjadwalan_done(order: dict) -> bool:
    """Penjadwalan selesai jika minimal 1 operator sudah diassign.
    Revisi routing: Penjadwalan = pembagian Operator (khusus barang Cetak).
    Full non-print PO tidak memerlukan pembagian Operator, jadi Penjadwalan
    langsung dianggap done (TIDAK DIPERLUKAN)."""
    return _wg_penjadwalan_done(order)


def _sample_uploaded(order: dict) -> bool:
    return _wg_sample_uploaded(order)


def _sample_ok(order: dict) -> bool:
    """Sample cetak sudah diapprove pihak berwenang."""
    return _wg_sample_ok(order)


def _stage_operator_done(order: dict) -> bool:
    """Operator done jika sample sudah diupload (min 1 kali) — jembatan ke Konfirmasi Sample."""
    return _sample_uploaded(order)


def _stage_konfirmasi_sample_done(order: dict) -> bool:
    """Konfirmasi Sample done jika sample sudah diapprove."""
    return _sample_uploaded(order) and _sample_ok(order)


def _print_qty_input(order: dict) -> bool:
    """Cetak done jika minimal 1 operator split punya qty_completed > 0.
    Field qty_completed sudah existing di operator_splits — tidak menciptakan
    field duplikat."""
    return _wg_print_qty_input(order)


def _packing_qty_input(order: dict) -> bool:
    """Packing done jika packing_info.qty_packed sudah diinput valid (>0)."""
    return _wg_packing_qty_input(order)


def _prereq_reasons_for_surat_jalan(order: dict) -> list:
    """Task 12: gate Packing → Surat Jalan. Butuh Qty Packing valid."""
    reasons = []
    if not _packing_qty_input(order):
        reasons.append("Qty Packing harus diisi terlebih dahulu.")
    return reasons


def _surat_jalan_created(order: dict) -> bool:
    """SJ done from stored file, imported/manual shipment, or legacy fields."""
    from quantity_rules import surat_jalan_created as _qr_sj_created
    return _qr_sj_created(order)


def _loading_completed(order: dict) -> bool:
    """Loading selesai dari qty actual (cap terpenuhi) atau fallback checklist historis."""
    if _qr_loading_status_semantic(order) == "completed":
        return True
    lc = order.get("loading_checklist") or {}
    return bool(lc.get("items_loaded")) and bool(lc.get("seal_ok"))


def _terkirim_confirmed(order: dict) -> bool:
    """Terkirim penuh hanya jika delivered qty == total PO (atau legacy full mark)."""
    return _qr_is_fully_delivered(order)


def _prereq_reasons_for_loading(order: dict) -> list:
    """Task 13: gate Surat Jalan → Loading. Butuh SJ dibuat valid."""
    reasons = []
    if not _surat_jalan_created(order):
        reasons.append("Surat Jalan harus dibuat terlebih dahulu.")
    return reasons


def _prereq_reasons_for_terkirim(order: dict) -> list:
    """Gate Surat Jalan → Terkirim (advance / mark_delivered). Loading is required for new delivery."""
    from services.delivery_loading_gate import new_delivery_block_reason
    reasons = []
    if not _surat_jalan_created(order):
        reasons.append("Surat Jalan belum dibuat.")
    blocked = new_delivery_block_reason(order)
    if blocked and blocked not in reasons:
        reasons.append(blocked)
    return reasons


def _stage_cetak_done(order: dict) -> bool:
    return _print_qty_input(order)


# Reason messages Bahasa Indonesia.
def _requires_printing(order: dict) -> bool:
    return _qr_requires_printing(order)


def _is_print_line(li: dict) -> bool:
    return _qr_is_print_line(li)


def _print_line_indices(order: dict) -> list:
    return _qr_print_line_indices(order)


def _print_qty_total(order: dict) -> int:
    return _qr_print_qty_total(order)


def _non_print_qty_total(order: dict) -> int:
    return _qr_non_print_qty_total(order)


def _print_completed_qty(order: dict) -> int:
    return _qr_print_completed_qty(order)


def _operator_assigned_qty(order: dict) -> int:
    return _qr_operator_assigned_qty(order)


def _available_for_packing(order: dict) -> int:
    return _qr_available_for_packing(order)


def _helper_prepared_print_qty(order: dict):
    return _qr_helper_prepared_print_qty(order)


def _operator_assignment_cap(order: dict) -> int:
    return _qr_operator_assignment_cap(order)


def _print_available_for_packing(order: dict) -> int:
    return _qr_print_available_for_packing(order)


def _packing_required_total(order: dict) -> int:
    return _qr_packing_required_total(order)


def _prereq_reasons_for_advance(order: dict, from_stage: int) -> list:
    """Return list of blocker reasons preventing advance from `from_stage`
    to `from_stage+1`. Empty list = OK to advance."""
    reasons = []
    req_print = _requires_printing(order)
    # 5 Penjadwalan -> 6 Screener
    if from_stage == 5:
        if not _stage_penjadwalan_done(order):
            reasons.append("Belum ada operator yang di-assign pada Penjadwalan.")
    # 6 Screener -> 7 Persiapan Helper: PARALLEL — jangan blok Helper dengan Screener
    elif from_stage == 6:
        pass
    # 7 Persiapan Helper -> 8 Operator
    elif from_stage == 7:
        if req_print and not _stage_helper_done(order):
            reasons.append("Qty persiapan Helper belum diinput.")
    # 8 Operator -> 9 Konfirmasi Sample (SKIP kalau non-print)
    elif from_stage == 8:
        if req_print:
            if not _stage_helper_done(order):
                reasons.append("Selesaikan Persiapan Helper terlebih dahulu.")
            elif not _sample_uploaded(order):
                reasons.append("Operator harus upload sampel cetak terlebih dahulu.")
    # 9 Konfirmasi Sample -> 10 Cetak (SKIP kalau non-print)
    elif from_stage == 9:
        if req_print:
            if not _sample_uploaded(order):
                reasons.append("Operator harus upload sampel cetak terlebih dahulu.")
            elif not _sample_ok(order):
                reasons.append("Sampel cetak belum disetujui.")
    # 10 Cetak -> 11 Packing: sample approval unlocks packing (qty = quantity safety)
    elif from_stage == 10:
        if req_print and not _sample_ok(order):
            reasons.append("Sampel cetak belum disetujui.")
    return reasons


def _prereq_reasons_for_packing(order: dict) -> list:
    """Blocker reasons untuk Packing. Qty Tidak Cetak tidak menunggu Operator;
    Qty Cetak membutuhkan sample + qty_completed. Screener/Helper/Penjadwalan
    tetap untuk PO yang requires_printing (auto-skip untuk full non-print)."""
    reasons = []
    req_print = _requires_printing(order)
    if not _stage_penjadwalan_done(order):
        reasons.append("Belum ada operator yang di-assign pada Penjadwalan.")
    if not _stage_screener_done(order):
        reasons.append("Qty Screen belum diinput.")
    if not _stage_helper_done(order):
        reasons.append("Qty persiapan (qty_prepared) belum diinput.")
    if req_print:
        non_print = _non_print_qty_total(order)
        print_done = _print_completed_qty(order)
        # Print-only: wajib sample + cetak selesai (minimal partial).
        # Mixed dengan hanya non-print tersedia: jangan blokir packing.
        # Mixed dengan print_completed > 0: sample harus OK.
        if non_print <= 0 or print_done > 0:
            if not _sample_uploaded(order):
                reasons.append("Operator harus upload sampel cetak terlebih dahulu.")
            if not _sample_ok(order):
                reasons.append("Sampel cetak belum disetujui.")
            if non_print <= 0 and not _print_qty_input(order):
                reasons.append("Qty Cetak belum diinput.")
    return reasons


@api_router.post("/orders/{order_id}/advance")
async def advance_stage(order_id: str, payload: StageAdvancePayload, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)

    # Per-operator advancement path
    if payload.operator_id:
        splits = order.get("operator_splits", [])
        found = next((s for s in splits if s["id"] == payload.operator_id), None)
        if not found:
            raise HTTPException(404, "Operator split tidak ditemukan")
        # Only operator, head_production, or admin can advance operator-level progress
        if user.get("role") not in ("operator", "head_production", "admin"):
            raise HTTPException(403, "Role Anda tidak diizinkan memajukan progres operator")
        # If operator, must match their own record
        if user.get("role") == "operator" and found["operator_email"] != user.get("email"):
            raise HTTPException(403, "Anda hanya dapat memperbarui progres Anda sendiri")
        _ensure_helper_for_operator_work(order)
        # Advance operator stage from 8 (Operator) up to 11 (Packing) — after that, PO-level flow takes over
        new_stage = min(found["stage"] + 1, 11)
        found["stage"] = new_stage
        found["updated_at"] = now_iso()
        # Also bump PO current_stage to min of operator stages if all operators reach a level
        if splits:
            min_op_stage = min(s["stage"] for s in splits)
            order["current_stage"] = max(order["current_stage"], min_op_stage)
        add_timeline(order, new_stage, user, note=f"Operator {found['operator_name']}: {payload.note or 'progress diperbarui'}")
        await _persist_order(order)
        return {"order": order}

    # PO-level advancement
    cur = order["current_stage"]
    _check_stage_permission(cur, user.get("role"))
    # Extra granular permission check (per-user override may deny even if role allows)
    perm_needed = STAGE_PERMISSION.get(cur)
    if perm_needed:
        eff = await user_permissions(user)
        if perm_needed not in eff and user.get("role") != "admin":
            raise HTTPException(403, f"Anda tidak punya permission '{perm_needed}' untuk memajukan tahap ini")
    # Guardrails: cannot advance past 2 (Approval Design) unless design is approved OR bypassed
    if cur == 2 and not _design_ok(order):
        raise HTTPException(400, "Design belum di-approve atau di-bypass — upload design & approve terlebih dahulu")
    if cur == 4 and not _payment_ok(order):
        raise HTTPException(400, "Payment belum di-approve atau di-bypass — upload bukti payment & approve terlebih dahulu")
    # HOLD before Penjadwalan: leaving stage 4 (Approval Payment) into stage 5 (Penjadwalan)
    if cur == 4:
        if not (_design_ok(order) and _payment_ok(order)):
            reasons = _hold_reasons(order) or ["Approval belum lengkap"]
            raise HTTPException(400, {
                "message": "PO masih HOLD — tidak dapat masuk Penjadwalan",
                "hold_reasons": reasons,
            })
    new_stage = min(cur + 1, len(STAGES) - 1)
    # Workflow 06-12 targeted bugfix: central prerequisite validator for
    # stages 5..10 (advance) + stage 11 (Packing→Surat Jalan).
    if 5 <= cur <= 10:
        reasons = _prereq_reasons_for_advance(order, cur)
        if reasons:
            raise HTTPException(400, {
                "message": "Tahap belum dapat dilanjutkan.",
                "prereq_reasons": reasons,
                "from_stage": cur,
                "to_stage": new_stage,
            })
    # Packing → Surat Jalan gate (Task 12 bugfix): Qty Packing wajib diinput.
    if cur == 11:
        reasons = _prereq_reasons_for_surat_jalan(order)
        if reasons:
            raise HTTPException(400, {
                "message": "Tahap belum dapat dilanjutkan.",
                "prereq_reasons": reasons,
                "from_stage": cur,
                "to_stage": new_stage,
            })
    # Surat Jalan → Loading gate (Task 13 bugfix): SJ wajib dibuat valid.
    if cur == 12:
        reasons = _prereq_reasons_for_loading(order)
        if reasons:
            raise HTTPException(400, {
                "message": "Tahap belum dapat dilanjutkan.",
                "prereq_reasons": reasons,
                "from_stage": cur,
                "to_stage": new_stage,
            })
    # Loading → Terkirim gate (Task 14 bugfix): SJ valid + Loading checklist
    # complete (items_loaded && seal_ok). Semua jalur ke Terkirim (advance +
    # mark_delivered) pakai validator ini.
    if cur == 13:
        reasons = _prereq_reasons_for_terkirim(order)
        if reasons:
            raise HTTPException(400, {
                "message": "Tahap belum dapat dilanjutkan.",
                "prereq_reasons": reasons,
                "from_stage": cur,
                "to_stage": new_stage,
            })
    order["current_stage"] = new_stage
    # Task 12 bugfix: log Cetak completion by Operator (bukan Packing).
    # Actor Operator melakukan action Cetak selesai; Packing baru dicatat saat
    # user dengan packing.update benar-benar meng-eksekusi Packing.
    if new_stage == 11 and cur == 10:
        add_timeline(order, cur, user, note="Cetak selesai — menuju Packing")
    elif new_stage == 12 and cur == 11:
        # Task 13 Surat Jalan bugfix: log Packing completion oleh actor Packing,
        # BUKAN membuat entry Surat Jalan otomatis. Actor SJ ditentukan saat
        # user berwenang benar-benar membuat SJ via POST /surat-jalan.
        add_timeline(order, cur, user, note="Packing selesai — menuju Surat Jalan")
    elif new_stage == 13 and cur == 12:
        # Task 14 Loading bugfix: log Surat Jalan completion oleh actor SJ,
        # BUKAN entry Loading. Actor Loading dicatat saat user dengan
        # loading.update benar-benar update checklist Loading.
        add_timeline(order, cur, user, note="Surat Jalan selesai — menuju Loading")
    else:
        add_timeline(order, new_stage, user, note=payload.note or "")
    # Side effects
    # Task 12 bugfix: JANGAN auto-set packing_status="Sudah" hanya karena PO
    # memasuki tahap 11. Completion Packing hanya dari action /packed atau
    # advance 11→12 yang valid (keduanya butuh Qty Packing).
    if new_stage == 12 and cur == 11:
        required_total = _packing_required_total(order)
        packed_qty = _qr_qty_packed(order)
        order["packing_status"] = "Sudah" if (required_total and packed_qty >= required_total) else "Berjalan"
    if new_stage == 14:
        required_total = _packing_required_total(order)
        packed_qty = _qr_qty_packed(order)
        if required_total and packed_qty < required_total:
            raise HTTPException(400, {
                "message": "PO belum terkirim penuh — qty packing belum lengkap.",
                "prereq_reasons": [
                    f"Qty packing ({packed_qty}) belum mencapai total PO ({required_total})."
                ],
                "from_stage": cur,
                "to_stage": new_stage,
            })
        order["delivery_status"] = "Terkirim"
        order["qty_delivered"] = required_total or packed_qty
        if not order.get("delivered_at"):
            order["delivered_at"] = now_iso()
        if not order.get("delivered_by"):
            order["delivered_by"] = user.get("email")
            order["delivered_by_name"] = user.get("name")
    # Task 13 bugfix: JANGAN auto-generate delivery_note_number saat masuk
    # stage 12. Surat Jalan hanya dibuat via POST /orders/{id}/surat-jalan
    # oleh user dengan permission `delivery_note.manage`.
    await _persist_order(order)
    return {"order": order}


@api_router.post("/orders/{order_id}/approve-design")
async def approve_design(order_id: str, payload: ApprovalPayload, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    await _require_perm(user, "design.approve")
    if not order.get("design_files"):
        raise HTTPException(400, "Upload design terlebih dahulu sebelum melakukan approval.")
    before = {"design_status": order.get("design_status"), "design_approved_by": order.get("design_approved_by")}
    if payload.approved:
        order["design_status"] = "Approved"
        order["design_approved_by"] = user.get("email")
        order["design_approved_by_name"] = user.get("name")
        order["design_approved_at"] = now_iso()
        # History (never cleared)
        order.setdefault("design_approval_history", []).append({
            "action": "approve",
            "by_email": user.get("email"),
            "by_name": user.get("name"),
            "at": now_iso(),
            "note": payload.note or "",
        })
        if order["current_stage"] < 3:
            order["current_stage"] = 3
        _sync_workflow_stage(order)
        add_timeline(order, order["current_stage"], user, note=payload.note or "Design di-approve")
    else:
        order["design_status"] = "Waiting"
        order["design_approved_by"] = None
        order["design_approved_by_name"] = None
        order["design_approved_at"] = None
        order.setdefault("design_approval_history", []).append({
            "action": "revoke",
            "by_email": user.get("email"),
            "by_name": user.get("name"),
            "at": now_iso(),
            "note": payload.note or "Approval design dibatalkan",
        })
        add_timeline(order, order["current_stage"], user, note=payload.note or "Approval design dibatalkan")
    await _persist_order(order)
    await _audit_po(order, "design.approve" if payload.approved else "design.approve.revoke", user, before=before,
                    after={"design_status": order["design_status"]}, note=payload.note or "")
    return {"order": order}


@api_router.post("/orders/{order_id}/approve-payment")
async def approve_payment(order_id: str, payload: ApprovalPayload, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    await _require_perm(user, "payment.approve")
    if not order.get("payment_files"):
        raise HTTPException(400, "Upload bukti pembayaran terlebih dahulu sebelum melakukan approval.")
    before = {"payment_status": order.get("payment_status"), "payment_approved_by": order.get("payment_approved_by")}
    if payload.approved:
        order["payment_status"] = "Approved"
        order["payment_approved_by"] = user.get("email")
        order["payment_approved_by_name"] = user.get("name")
        order["payment_approved_at"] = now_iso()
        order.setdefault("payment_approval_history", []).append({
            "action": "approve",
            "by_email": user.get("email"),
            "by_name": user.get("name"),
            "at": now_iso(),
            "note": payload.note or "",
        })
        if order["current_stage"] < 4:
            order["current_stage"] = 4
        _sync_workflow_stage(order)
        add_timeline(order, order["current_stage"], user, note=payload.note or "Payment di-approve")
    else:
        order["payment_status"] = "Waiting"
        order["payment_approved_by"] = None
        order["payment_approved_by_name"] = None
        order["payment_approved_at"] = None
        order.setdefault("payment_approval_history", []).append({
            "action": "revoke",
            "by_email": user.get("email"),
            "by_name": user.get("name"),
            "at": now_iso(),
            "note": payload.note or "Approval payment dibatalkan",
        })
        add_timeline(order, order["current_stage"], user, note=payload.note or "Approval payment dibatalkan")
    await _persist_order(order)
    await _audit_po(order, "payment.approve" if payload.approved else "payment.approve.revoke", user, before=before,
                    after={"payment_status": order["payment_status"]}, note=payload.note or "")
    return {"order": order}


# ---- Bypass approval (Requirement 11) ----
@api_router.post("/orders/{order_id}/design/bypass")
async def bypass_design(order_id: str, payload: BypassPayload, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    await _require_perm(user, "design.bypass_approval")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(400, "Alasan bypass wajib diisi.")
    order["design_bypassed"] = True
    order["design_bypassed_by"] = user.get("email")
    order["design_bypassed_by_name"] = user.get("name")
    order["design_bypassed_at"] = now_iso()
    order["design_bypass_reason"] = reason
    order.setdefault("design_approval_history", []).append({
        "action": "bypass",
        "by_email": user.get("email"),
        "by_name": user.get("name"),
        "at": now_iso(),
        "note": reason,
    })
    if order["current_stage"] < 3:
        order["current_stage"] = 3
    _sync_workflow_stage(order)
    add_timeline(order, order["current_stage"], user, note=f"Bypass approval design: {reason}")
    await _persist_order(order)
    await _audit_po(order, "design.bypass", user, after={"design_bypassed": True}, note=reason)
    return {"order": order}


@api_router.post("/orders/{order_id}/payment/bypass")
async def bypass_payment(order_id: str, payload: BypassPayload, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    await _require_perm(user, "payment.bypass_approval")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(400, "Alasan bypass wajib diisi.")
    order["payment_bypassed"] = True
    order["payment_bypassed_by"] = user.get("email")
    order["payment_bypassed_by_name"] = user.get("name")
    order["payment_bypassed_at"] = now_iso()
    order["payment_bypass_reason"] = reason
    order.setdefault("payment_approval_history", []).append({
        "action": "bypass",
        "by_email": user.get("email"),
        "by_name": user.get("name"),
        "at": now_iso(),
        "note": reason,
    })
    if order["current_stage"] < 4:
        order["current_stage"] = 4
    _sync_workflow_stage(order)
    add_timeline(order, order["current_stage"], user, note=f"Bypass approval payment: {reason}")
    await _persist_order(order)
    await _audit_po(order, "payment.bypass", user, after={"payment_bypassed": True}, note=reason)
    return {"order": order}


# ---- Rollback (Requirement 7) ----
@api_router.post("/orders/{order_id}/rollback")
async def rollback_stage(order_id: str, payload: RollbackPayload, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    await _require_perm(user, "workflow.rollback")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(400, "Alasan rollback wajib diisi.")
    if payload.to_stage < 0 or payload.to_stage >= len(STAGES):
        raise HTTPException(400, "Stage tujuan tidak valid.")
    from_stage = order.get("current_stage", 0)
    if payload.to_stage >= from_stage:
        raise HTTPException(400, "Stage tujuan harus lebih kecil dari stage saat ini.")
    entry = {
        "id": str(uuid.uuid4()),
        "from_stage": from_stage,
        "from_stage_label": STAGES[min(from_stage, len(STAGES) - 1)],
        "to_stage": payload.to_stage,
        "to_stage_label": STAGES[payload.to_stage],
        "by_email": user.get("email"),
        "by_role": user.get("role"),
        "by_name": user.get("name"),
        "at": now_iso(),
        "reason": reason,
    }
    order.setdefault("rollback_history", []).append(entry)
    order["current_stage"] = payload.to_stage
    # If rolling back TO design/approval region, invalidate downstream approval if dependencies broken
    if payload.to_stage <= 1:  # rolled back to before Approval Design
        if order.get("design_status") == "Approved":
            order["design_status"] = "Waiting"
            order["design_approved_by"] = None
            order["design_approved_by_name"] = None
            order["design_approved_at"] = None
            order.setdefault("design_approval_history", []).append({
                "action": "reset-on-rollback", "by_email": user.get("email"), "by_name": user.get("name"),
                "at": now_iso(), "note": f"Rollback ke {STAGES[payload.to_stage]}",
            })
    if payload.to_stage <= 3:
        if order.get("payment_status") == "Approved":
            order["payment_status"] = "Waiting"
            order["payment_approved_by"] = None
            order["payment_approved_by_name"] = None
            order["payment_approved_at"] = None
            order.setdefault("payment_approval_history", []).append({
                "action": "reset-on-rollback", "by_email": user.get("email"), "by_name": user.get("name"),
                "at": now_iso(), "note": f"Rollback ke {STAGES[payload.to_stage]}",
            })
    add_timeline(order, payload.to_stage, user, note=f"Rollback dari {entry['from_stage_label']} → {entry['to_stage_label']}: {reason}")
    await _persist_order(order)
    await _audit_po(order, "workflow.rollback", user, before={"stage": from_stage}, after={"stage": payload.to_stage}, note=reason)
    return {"order": order}


# ---- Cancel & Delete PO (Requirement 5) ----
@api_router.post("/orders/{order_id}/cancel")
async def cancel_po(order_id: str, payload: CancelPayload, user=Depends(current_user)):
    await _require_perm(user, "po.cancel")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(400, "Alasan pembatalan wajib diisi.")
    if order.get("cancelled"):
        raise HTTPException(400, "PO sudah dalam status Cancelled.")
    now = now_iso()
    order["cancelled"] = True
    order["cancelled_by"] = user.get("email")
    order["cancelled_by_name"] = user.get("name")
    order["cancelled_at"] = now
    order["cancel_reason"] = reason
    order["status_label"] = "Cancelled"
    add_timeline(order, order["current_stage"], user, note=f"PO Cancelled: {reason}")
    await _persist_order(order)
    await _audit_po(order, "po.cancel", user, after={"cancel_reason": reason})
    return {"order": order, "message": "PO berhasil dibatalkan"}


def _describe_po_impact(order: dict) -> dict:
    """Snapshot data terkait yang akan hilang bila PO dihapus permanen."""
    return {
        "current_stage": order.get("current_stage", 0),
        "status_label": order.get("status_label"),
        "line_items": len(order.get("line_items", [])),
        "design_files": len(order.get("design_files", []) or []),
        "payment_files": len(order.get("payment_files", []) or []),
        "sample_files": len(order.get("sample_files", []) or []),
        "delivery_proofs": len(order.get("delivery_proofs", []) or []),
        "timeline_entries": len(order.get("timeline", []) or []),
        "operator_splits": len(order.get("operator_splits", []) or []),
        "has_source_pdf": bool(order.get("source_pdf")),
        "cancelled": bool(order.get("cancelled")),
    }


@api_router.get("/orders/{order_id}/delete-impact")
async def delete_impact(order_id: str, user=Depends(current_user)):
    await _require_perm(user, "po.delete")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    return {
        "po_number": order.get("po_number"),
        "customer": order.get("customer"),
        "accurate_id": order.get("accurate_id"),
        "impact": _describe_po_impact(order),
    }


@api_router.delete("/orders/{order_id}")
async def delete_po(order_id: str, payload: DeletePayload, user=Depends(current_user)):
    await _require_perm(user, "po.delete")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(400, "Alasan penghapusan wajib diisi.")
    # Strong confirmation: must type the PO number OR the literal word HAPUS
    confirm = (payload.confirm or "").strip()
    po_num = order.get("po_number") or ""
    if confirm != po_num and confirm.upper() != "HAPUS":
        raise HTTPException(400, {
            "message": "Konfirmasi tidak cocok. Ketik nomor PO atau 'HAPUS' untuk melanjutkan.",
            "expected": po_num,
        })
    # === DEPENDENCY CHECKS ===
    pay_count = await db.sales_receipts.count_documents({"po_id": order["id"]})
    if pay_count > 0:
        raise HTTPException(400, "Hapus Penerimaan Penjualan terlebih dahulu.")
    inv_count = await db.sales_invoices.count_documents({"po_id": order["id"]})
    if inv_count > 0:
        raise HTTPException(400, "Hapus Faktur Penjualan terlebih dahulu.")
    sj_count = await db.sales_deliveries.count_documents({"po_id": order["id"]})
    if sj_count > 0:
        raise HTTPException(400, "Hapus Pengiriman Pesanan (Surat Jalan) terlebih dahulu.")
    # Check production activity (stage > 1 means some process started)
    current_stage = order.get("current_stage", 0)
    if current_stage > 1:
        raise HTTPException(400, "PO tidak dapat dihapus karena sudah memiliki aktivitas proses.")
    # === END DEPENDENCY CHECKS ===
    impact = _describe_po_impact(order)
    # Audit BEFORE delete so we retain the snapshot even after the row is gone
    await _audit_po(order, "po.delete", user, before={
        "po_number": po_num, "customer": order.get("customer"),
        "current_stage": order.get("current_stage"), "impact": impact,
    }, note=reason)
    # Soft-mark associated files as deleted so /api/files/{path} no longer serves them
    await db.files.update_many({"order_id": order["id"]}, {"$set": {"is_deleted": True, "deleted_at": now_iso(), "deleted_by": user.get("email"), "deleted_reason": reason}})
    # Hard delete the order document
    await db.orders.delete_one({"id": order["id"]})
    # Keep db.import_logs (audit trail — Accurate remains untouched, this is Eternal-only)
    return {"deleted": True, "po_number": po_num, "message": f"PO {po_num} telah dihapus permanen"}


@api_router.post("/orders-bulk/cancel")
async def bulk_cancel(payload: BulkPayload, user=Depends(current_user)):
    await _require_perm(user, "po.cancel")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(400, "Alasan pembatalan wajib diisi.")
    if not payload.ids:
        raise HTTPException(400, "Pilih minimal 1 PO.")
    unique_ids = list(dict.fromkeys(payload.ids))
    found = await db.orders.find({"id": {"$in": unique_ids}}, {"_id": 0}).to_list(len(unique_ids) or 1)
    by_id = {o.get("id"): o for o in found}
    succeeded = []
    failed = []
    now = now_iso()
    for oid in unique_ids:
        order = by_id.get(oid)
        if not order:
            failed.append({"id": oid, "reason": "PO tidak ditemukan"})
            continue
        if order.get("cancelled"):
            failed.append({
                "id": oid,
                "po_number": order.get("po_number"),
                "reason": "PO sudah dalam status Cancelled.",
            })
            continue
        order["cancelled"] = True
        order["cancelled_by"] = user.get("email")
        order["cancelled_by_name"] = user.get("name")
        order["cancelled_at"] = now
        order["cancel_reason"] = reason
        order["status_label"] = "Cancelled"
        add_timeline(order, order.get("current_stage", 0), user, note=f"Bulk cancel: {reason}")
        await _persist_order(order)
        succeeded.append({"id": oid, "po_number": order.get("po_number")})
    cancelled = [s.get("po_number") for s in succeeded]
    await _write_audit(action="po.bulk_cancel", actor=user, target_email=f"po:{len(succeeded)} PO",
                       after={"po_numbers": cancelled, "count": len(succeeded), "failed": failed}, note=reason)
    return {
        "cancelled_count": len(succeeded),
        "po_numbers": cancelled,
        "reason": reason,
        "succeeded": succeeded,
        "failed": failed,
    }


@api_router.post("/orders-bulk/delete")
async def bulk_delete(payload: BulkPayload, user=Depends(current_user)):
    await _require_perm(user, "po.delete")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(400, "Alasan penghapusan wajib diisi.")
    if not payload.ids:
        raise HTTPException(400, "Pilih minimal 1 PO.")
    if (payload.confirm or "").strip().upper() != "HAPUS":
        raise HTTPException(400, {
            "message": "Konfirmasi tidak cocok. Ketik 'HAPUS' untuk konfirmasi.",
            "expected": "HAPUS",
        })
    unique_ids = list(dict.fromkeys(payload.ids))
    orders = await db.orders.find({"id": {"$in": unique_ids}}, {"_id": 0}).to_list(len(unique_ids) or 1)
    by_id = {o.get("id"): o for o in orders}
    succeeded = []
    failed = []
    to_delete = []
    for oid in unique_ids:
        order = by_id.get(oid)
        if not order:
            failed.append({"id": oid, "reason": "PO tidak ditemukan"})
            continue
        # Dependency checks
        pay_c = await db.sales_receipts.count_documents({"po_id": order["id"]})
        if pay_c > 0:
            failed.append({"id": oid, "po_number": order.get("po_number"), "reason": "Hapus Penerimaan Penjualan terlebih dahulu."})
            continue
        inv_c = await db.sales_invoices.count_documents({"po_id": order["id"]})
        if inv_c > 0:
            failed.append({"id": oid, "po_number": order.get("po_number"), "reason": "Hapus Faktur Penjualan terlebih dahulu."})
            continue
        sj_c = await db.sales_deliveries.count_documents({"po_id": order["id"]})
        if sj_c > 0:
            failed.append({"id": oid, "po_number": order.get("po_number"), "reason": "Hapus Pengiriman Pesanan terlebih dahulu."})
            continue
        if order.get("current_stage", 0) > 1:
            failed.append({"id": oid, "po_number": order.get("po_number"), "reason": "PO sudah memiliki aktivitas proses."})
            continue
        to_delete.append(order)
    # ALL-OR-NOTHING: if any blocked, delete none
    if failed:
        raise HTTPException(400, {"message": "Tidak dapat menghapus. Beberapa PO memiliki dependensi.", "blockers": failed})
    delete_ids = [o["id"] for o in to_delete]
    for o in to_delete:
        await _audit_po(o, "po.delete", user, before={
            "po_number": o.get("po_number"), "customer": o.get("customer"),
            "current_stage": o.get("current_stage"), "impact": _describe_po_impact(o),
        }, note=f"Bulk: {reason}")
        succeeded.append({"id": o.get("id"), "po_number": o.get("po_number")})
    if delete_ids:
        await db.files.update_many({"order_id": {"$in": delete_ids}}, {"$set": {"is_deleted": True, "deleted_at": now_iso(), "deleted_by": user.get("email"), "deleted_reason": reason}})
        result = await db.orders.delete_many({"id": {"$in": delete_ids}})
        deleted_count = result.deleted_count
    else:
        deleted_count = 0
    po_numbers = [s.get("po_number") for s in succeeded]
    await _write_audit(action="po.bulk_delete", actor=user, target_email=f"po:{deleted_count} PO",
                       before={"po_numbers": po_numbers}, after={"deleted_count": deleted_count, "failed": failed}, note=reason)
    return {
        "deleted_count": deleted_count,
        "po_numbers": po_numbers,
        "reason": reason,
        "succeeded": succeeded,
        "failed": failed,
    }


# ---- Helper qty (Requirement 9) ----
@api_router.post("/orders/{order_id}/helper-prep")
async def update_helper_prep(order_id: str, payload: HelperPrepPayload, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    await _require_perm(user, "helper.prepare")
    if _requires_printing(order) and not _stage_penjadwalan_done(order):
        raise HTTPException(400, "Tahap Persiapan Helper belum dapat diproses. Selesaikan Penjadwalan terlebih dahulu.")
    if payload.qty_required < 0 or payload.qty_prepared < 0:
        raise HTTPException(400, "Qty tidak boleh negatif")
    print_cap = _print_qty_total(order)
    if payload.qty_required > print_cap:
        raise HTTPException(
            400,
            f"Qty dibutuhkan Helper ({payload.qty_required}) melebihi total qty Cetak ({print_cap}). "
            "Persiapan Helper hanya untuk barang Cetak."
        )
    if payload.qty_prepared > print_cap:
        raise HTTPException(
            400,
            f"Qty disiapkan Helper ({payload.qty_prepared}) melebihi total qty Cetak ({print_cap})."
        )
    err = _qr_validate_helper_prepared_qty(payload.qty_prepared, order)
    if err:
        raise HTTPException(400, err)
    order["_expected_updated_at"] = order.get("updated_at")
    shortage = max(payload.qty_required - payload.qty_prepared, 0)
    before = order.get("helper_prep") or {}
    order["helper_prep"] = {
        "qty_required": payload.qty_required,
        "qty_prepared": payload.qty_prepared,
        "qty_shortage": shortage,
        "shortage_status": "Barang Kurang" if shortage > 0 else "Cukup",
        "notes": (payload.notes or "").strip() or None,
        "updated_by": user.get("email"),
        "updated_by_name": user.get("name"),
        "updated_at": now_iso(),
    }
    add_timeline(order, order["current_stage"], user, note=f"Persiapan Helper: {payload.qty_prepared}/{payload.qty_required} (kurang {shortage})")
    _sync_workflow_stage(order)
    await _persist_order(order)
    await _audit_po(order, "helper.prep.update", user, before=before, after=order["helper_prep"])
    return {"order": order}


# ---- Helper qty PER LINE ITEM (Batch 1) ----
@api_router.post("/orders/{order_id}/helper-prep/items")
async def update_helper_prep_items(order_id: str, payload: HelperPrepItemsPayload, user=Depends(current_user)):
    """Helper mengisi Qty Disiapkan per line item. Qty Dibutuhkan = read-only
    dari line_items[i].quantity (source of truth PO). Qty Order TIDAK berubah."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    await _require_perm(user, "helper.prepare")
    if _requires_printing(order) and not _stage_penjadwalan_done(order):
        raise HTTPException(400, "Tahap Persiapan Helper belum dapat diproses. Selesaikan Penjadwalan terlebih dahulu.")
    line_items = order.get("line_items") or []
    if not line_items:
        raise HTTPException(400, "PO tidak memiliki line item")
    # Revisi routing: Helper hanya menyiapkan barang Cetak. Non-print line
    # DIABAIKAN dari total qty_required/qty_prepared/qty_shortage.
    print_idx_set = set(_print_line_indices(order))
    hp_before = order.get("helper_prep") or {}
    entries = list(hp_before.get("entries") or [])
    increment_mode = any(it.qty is not None for it in payload.items)
    if increment_mode:
        if not entries:
            for it in (hp_before.get("items") or []):
                q = int(it.get("qty_prepared") or 0)
                if q > 0:
                    entries, _, _ = append_qty_entry(
                        entries, qty=q, stage="helper", user={"email": hp_before.get("updated_by"), "name": hp_before.get("updated_by_name")},
                        now=hp_before.get("updated_at") or now_iso(),
                        action_id=f"legacy-helper-{it.get('line_idx')}",
                        po_id=order.get("id"),
                        line_idx=it.get("line_idx"),
                        extra={"legacy": True},
                    )
        for it in payload.items:
            if it.qty is None:
                continue
            aid = it.action_id or payload.action_id
            if find_by_action_id(entries, aid):
                continue
            inc = max(0, int(it.qty))
            if inc <= 0:
                continue
            if it.line_idx not in print_idx_set:
                raise HTTPException(400, "Line item bukan proses Cetak — tidak masuk Persiapan Helper.")
            line_qty = int((line_items[it.line_idx] or {}).get("quantity") or 0)
            already = sum(int(e.get("qty") or 0) for e in entries if e.get("line_idx") == it.line_idx and not e.get("cancelled"))
            new_total = already + inc
            if new_total > line_qty:
                raise HTTPException(
                    400,
                    f"Qty disiapkan untuk '{line_items[it.line_idx].get('name')}' ({new_total}) melebihi qty Cetak line ({line_qty}).",
                )
            target = _qr_helper_target_for_line(order, it.line_idx)
            new_cap = min(line_qty, max(target, already))
            if new_total > new_cap:
                raise HTTPException(
                    400,
                    f"Qty disiapkan untuk '{line_items[it.line_idx].get('name')}' ({new_total}) melebihi target jadwal ({target}).",
                )
            entries, _, created = append_qty_entry(
                entries, qty=inc, stage="helper", user=user, now=now_iso(),
                action_id=aid, po_id=order.get("id"), line_idx=it.line_idx,
            )
        prepared_map = {}
        for e in entries:
            if e.get("cancelled"):
                continue
            idx = int(e.get("line_idx") if e.get("line_idx") is not None else -1)
            if idx < 0:
                continue
            prepared_map[idx] = prepared_map.get(idx, 0) + int(e.get("qty") or 0)
    else:
        if entries:
            raise HTTPException(409, "Entry Helper sudah dikonfirmasi. Kirim pekerjaan tambahan sebagai qty baru.")
        prepared_map = {int(it.line_idx): max(0, int(it.qty_prepared or 0)) for it in payload.items}
    items_out = []
    total_required = 0
    total_prepared = 0
    for idx, li in enumerate(line_items):
        req_full = int(li.get("quantity", 0) or 0)
        prep_input = int(prepared_map.get(idx, 0))
        if prep_input < 0:
            prep_input = 0
        if idx in print_idx_set:
            req = _qr_helper_target_for_line(order, idx)
            prep = prep_input
            if prep_input > req_full:
                raise HTTPException(
                    400,
                    f"Qty disiapkan untuk '{li.get('name')}' ({prep_input}) melebihi qty Cetak line ({req_full})."
                )
            shortage = max(req - prep, 0)
            total_required += req
            total_prepared += prep
            items_out.append({
                "line_idx": idx,
                "name": li.get("name"),
                "color": li.get("color"),
                "qty_required": req,
                "qty_prepared": prep,
                "qty_shortage": shortage,
            })
        elif prep_input > 0:
            raise HTTPException(
                400,
                f"Line item '{li.get('name')}' bukan proses Cetak — tidak masuk Persiapan Helper."
            )
        # Non-print line dihilangkan dari items_out & totals (out of scope).
    err = _qr_validate_helper_prepared_qty(total_prepared, order)
    if err:
        raise HTTPException(400, err)
    total_shortage = max(total_required - total_prepared, 0)
    before = order.get("helper_prep") or {}
    order["_expected_updated_at"] = order.get("updated_at")
    if not increment_mode:
        for it in items_out:
            q = int(it.get("qty_prepared") or 0)
            if q <= 0:
                continue
            entries, _, _ = append_qty_entry(
                entries, qty=q, stage="helper", user=user, now=now_iso(),
                action_id=payload.action_id or f"helper-first-{it.get('line_idx')}",
                po_id=order.get("id"), line_idx=it.get("line_idx"),
            )
    order["helper_prep"] = {
        # Keep total fields for backward compatibility (Timeline & schedule pakai ini)
        "qty_required": total_required,
        "qty_prepared": total_prepared,
        "qty_shortage": total_shortage,
        "shortage_status": "Barang Kurang" if total_shortage > 0 else "Cukup",
        "notes": (payload.notes or "").strip() or None,
        "items": items_out,
        "entries": entries,
        "updated_by": user.get("email"),
        "updated_by_name": user.get("name"),
        "updated_at": now_iso(),
    }
    add_timeline(order, order["current_stage"], user,
                 note=f"Persiapan Helper (per-line): {total_prepared}/{total_required} (kurang {total_shortage})")
    _sync_workflow_stage(order)
    await _persist_order(order)
    await _audit_po(order, "helper.prep.items.update", user, before=before, after=order["helper_prep"])
    return {"order": order}


# ---- Batch 3: Production routing (Cetak / Non-Cetak) ----
class LineItemRouteItem(BaseModel):
    line_idx: int
    production_process: str  # "print" | "non_print"


class LineItemRoutePayload(BaseModel):
    items: List[LineItemRouteItem]


@api_router.post("/orders/{order_id}/line-items/route")
async def set_line_items_route(order_id: str, payload: LineItemRoutePayload, user=Depends(current_user)):
    """Menentukan production routing per line item (print vs non_print).
    TIDAK mengubah quantity, name, color, atau data Accurate source."""
    await _require_perm(user, "production.route.edit")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    lis = order.get("line_items") or []
    if not lis:
        raise HTTPException(400, "PO tidak memiliki line item")
    route_map = {}
    for it in payload.items:
        p = (it.production_process or "").strip().lower()
        if p not in ("print", "non_print"):
            raise HTTPException(400, f"production_process harus 'print' atau 'non_print' (got {p})")
        if it.line_idx < 0 or it.line_idx >= len(lis):
            raise HTTPException(400, f"line_idx {it.line_idx} tidak valid")
        route_map[it.line_idx] = p
    before = [(i, li.get("production_process")) for i, li in enumerate(lis)]
    for idx, p in route_map.items():
        # HANYA update field production_process — tidak menyentuh qty/name/color
        lis[idx]["production_process"] = p
    order["line_items"] = lis
    await _persist_order(order)
    await _audit_po(order, "production.route.update", user,
                    before={"routes": {str(i): p for i, p in before}},
                    after={"routes": {str(i): li.get("production_process") for i, li in enumerate(lis)}})
    return {"order": order}


# ---- Batch 3: Dashboard Action Center ----
_ACTION_CENTER_LIMIT = 2000


def _orders_action_center_line_items_expr() -> dict:
    """Keep array order / index identity. Never merge by name."""
    return {
        "$map": {
            "input": {"$ifNull": ["$line_items", []]},
            "as": "li",
            "in": {
                "name": "$$li.name",
                "production_process": "$$li.production_process",
                "quantity": "$$li.quantity",
            },
        }
    }


def _orders_action_center_operator_splits_expr() -> dict:
    return {
        "$map": {
            "input": {"$ifNull": ["$operator_splits", []]},
            "as": "s",
            "in": {
                "operator_email": "$$s.operator_email",
                "operator_name": "$$s.operator_name",
                "quantity": "$$s.quantity",
                "qty_completed": "$$s.qty_completed",
                "stage": "$$s.stage",
                "work_date": "$$s.work_date",
            },
        }
    }


def _orders_action_center_sj_files_expr() -> dict:
    """SJ identity + qty metadata. data_url bytes become a storage_path sentinel."""
    return {
        "$map": {
            "input": {
                "$cond": [{"$isArray": "$surat_jalan_files"}, "$surat_jalan_files", []],
            },
            "as": "f",
            "in": {
                "id": "$$f.id",
                "file_id": "$$f.file_id",
                "name": {"$ifNull": ["$$f.name", ""]},
                "uploaded_at": "$$f.uploaded_at",
                "is_deleted": "$$f.is_deleted",
                "shipment_id": "$$f.shipment_id",
                "sj_number": "$$f.sj_number",
                "number": "$$f.number",
                "sales_delivery_id": "$$f.sales_delivery_id",
                "total_qty": "$$f.total_qty",
                "qty": "$$f.qty",
                "storage_path": {
                    "$cond": [
                        {
                            "$or": [
                                _mongo_nonempty_str_expr("$$f.storage_path"),
                                {
                                    "$eq": [
                                        {"$substrCP": [_mongo_str_expr("$$f.data_url"), 0, 5]},
                                        "data:",
                                    ]
                                },
                            ]
                        },
                        "1",
                        "",
                    ]
                },
                "lines": {
                    "$map": {
                        "input": {"$ifNull": ["$$f.lines", []]},
                        "as": "ln",
                        "in": {
                            "line_idx": "$$ln.line_idx",
                            "qty": "$$ln.qty",
                            "quantity": "$$ln.quantity",
                        },
                    }
                },
            },
        }
    }


def _orders_action_center_shipments_expr() -> dict:
    return {
        "$map": {
            "input": {"$ifNull": ["$shipments", []]},
            "as": "sh",
            "in": {
                "id": "$$sh.id",
                "number": "$$sh.number",
                "file_id": "$$sh.file_id",
                "sales_delivery_id": "$$sh.sales_delivery_id",
                "lines": {
                    "$map": {
                        "input": {"$ifNull": ["$$sh.lines", []]},
                        "as": "ln",
                        "in": {
                            "line_idx": "$$ln.line_idx",
                            "qty": "$$ln.qty",
                            "quantity": "$$ln.quantity",
                        },
                    }
                },
            },
        }
    }


def _orders_action_center_projection() -> dict:
    """Fields read by Action Center predicates + hold/deadline snapshots.

    Dedicated to GET /dashboard/action-items. Not the Phase 2B dashboard projection.
    """
    return {
        "_id": 0,
        "id": 1,
        "po_number": 1,
        "outlet": 1,
        "customer": 1,
        "work_date": 1,
        "cancelled": 1,
        "current_stage": 1,
        "total_quantity": 1,
        "quantity": 1,
        "design_status": 1,
        "design_bypassed": 1,
        "design_approved_at": 1,
        "design_bypassed_at": 1,
        "payment_status": 1,
        "payment_bypassed": 1,
        "payment_approved_at": 1,
        "payment_bypassed_at": 1,
        "sample_approved": 1,
        "customer_approval.status": 1,
        "screener_info.screen_qty": 1,
        "helper_prep.qty_prepared": 1,
        "delivery_status": 1,
        "qty_delivered": 1,
        "delivery_note": 1,
        "delivery_note_number": 1,
        "delivery_note_created_at": 1,
        "delivery_note_created_by": 1,
        "surat_jalan_created": 1,
        "qty_packed": 1,
        "loading_checklist.items_loaded": 1,
        "loading_checklist.seal_ok": 1,
        "delivery_deadline_manual": 1,
        "scheduled_delivery_date": 1,
        "buffer_stock": 1,
        "design_files": _orders_dashboard_file_presence_expr("design_files"),
        "payment_files": _orders_dashboard_file_presence_expr("payment_files"),
        "sample_files": _orders_dashboard_file_presence_expr("sample_files"),
        "surat_jalan_files": _orders_action_center_sj_files_expr(),
        "line_items": _orders_action_center_line_items_expr(),
        "operator_splits": _orders_action_center_operator_splits_expr(),
        "shipments": _orders_action_center_shipments_expr(),
        "loading_confirmations": _orders_dashboard_loading_confirmations_expr(),
        "packing_info": _orders_dashboard_packing_info_expr(),
        "loading_info": _orders_dashboard_loading_info_expr(),
        "delivery_reschedules": _orders_dashboard_reschedules_expr(),
    }


def _action_center_file_presence(files) -> list:
    return [{}] if isinstance(files, list) and files else []


def _action_center_sj_file_evidence(files) -> list:
    out = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        data_url = str(f.get("data_url") or "")
        storage = str(f.get("storage_path") or "").strip()
        out.append({
            "id": f.get("id"),
            "file_id": f.get("file_id"),
            "name": f.get("name") or "",
            "uploaded_at": f.get("uploaded_at"),
            "is_deleted": f.get("is_deleted"),
            "shipment_id": f.get("shipment_id"),
            "sj_number": f.get("sj_number"),
            "number": f.get("number"),
            "sales_delivery_id": f.get("sales_delivery_id"),
            "total_qty": f.get("total_qty"),
            "qty": f.get("qty"),
            "storage_path": "1" if (storage or data_url.startswith("data:")) else "",
            "lines": [
                {
                    "line_idx": ln.get("line_idx"),
                    "qty": ln.get("qty"),
                    "quantity": ln.get("quantity"),
                }
                for ln in (f.get("lines") or [])
                if isinstance(ln, dict)
            ],
        })
    return out


def _shape_order_for_action_center_compute(order: dict) -> dict:
    """In-memory mirror of `_orders_action_center_projection` (tests/parity)."""
    src = order or {}
    shaped = {
        "id": src.get("id"),
        "po_number": src.get("po_number"),
        "outlet": src.get("outlet"),
        "customer": src.get("customer"),
        "work_date": src.get("work_date"),
        "cancelled": src.get("cancelled"),
        "current_stage": src.get("current_stage"),
        "total_quantity": src.get("total_quantity"),
        "quantity": src.get("quantity"),
        "design_status": src.get("design_status"),
        "design_bypassed": src.get("design_bypassed"),
        "design_approved_at": src.get("design_approved_at"),
        "design_bypassed_at": src.get("design_bypassed_at"),
        "payment_status": src.get("payment_status"),
        "payment_bypassed": src.get("payment_bypassed"),
        "payment_approved_at": src.get("payment_approved_at"),
        "payment_bypassed_at": src.get("payment_bypassed_at"),
        "sample_approved": src.get("sample_approved"),
        "delivery_status": src.get("delivery_status"),
        "qty_delivered": src.get("qty_delivered"),
        "delivery_note": src.get("delivery_note"),
        "delivery_note_number": src.get("delivery_note_number"),
        "delivery_note_created_at": src.get("delivery_note_created_at"),
        "delivery_note_created_by": src.get("delivery_note_created_by"),
        "surat_jalan_created": src.get("surat_jalan_created"),
        "qty_packed": src.get("qty_packed"),
        "delivery_deadline_manual": src.get("delivery_deadline_manual"),
        "scheduled_delivery_date": src.get("scheduled_delivery_date"),
        "buffer_stock": src.get("buffer_stock"),
        "design_files": _action_center_file_presence(src.get("design_files")),
        "payment_files": _action_center_file_presence(src.get("payment_files")),
        "sample_files": _action_center_file_presence(src.get("sample_files")),
        "surat_jalan_files": _action_center_sj_file_evidence(src.get("surat_jalan_files")),
        "line_items": [
            {
                "name": li.get("name"),
                "production_process": li.get("production_process"),
                "quantity": li.get("quantity"),
            }
            for li in (src.get("line_items") or [])
            if isinstance(li, dict)
        ],
        "operator_splits": [
            {
                "operator_email": s.get("operator_email"),
                "operator_name": s.get("operator_name"),
                "quantity": s.get("quantity"),
                "qty_completed": s.get("qty_completed"),
                "stage": s.get("stage"),
                "work_date": s.get("work_date"),
            }
            for s in (src.get("operator_splits") or [])
            if isinstance(s, dict)
        ],
        "shipments": [
            {
                "id": sh.get("id"),
                "number": sh.get("number"),
                "file_id": sh.get("file_id"),
                "sales_delivery_id": sh.get("sales_delivery_id"),
                "lines": [
                    {
                        "line_idx": ln.get("line_idx"),
                        "qty": ln.get("qty"),
                        "quantity": ln.get("quantity"),
                    }
                    for ln in (sh.get("lines") or [])
                    if isinstance(ln, dict)
                ],
            }
            for sh in (src.get("shipments") or [])
            if isinstance(sh, dict)
        ],
        "loading_confirmations": [
            {
                "cancelled": c.get("cancelled"),
                "deleted": c.get("deleted"),
                "surat_jalan_id": c.get("surat_jalan_id"),
                "lines": [
                    {
                        "line_idx": ln.get("line_idx"),
                        "qty": ln.get("qty"),
                        "quantity": ln.get("quantity"),
                    }
                    for ln in (c.get("lines") or [])
                    if isinstance(ln, dict)
                ],
            }
            for c in (src.get("loading_confirmations") or [])
            if isinstance(c, dict)
        ],
        "packing_info": {
            "qty_packed": (src.get("packing_info") or {}).get("qty_packed"),
            "lines": [
                {
                    "line_id": ln.get("line_id"),
                    "line_idx": ln.get("line_idx"),
                    "packed_qty": ln.get("packed_qty"),
                }
                for ln in ((src.get("packing_info") or {}).get("lines") or [])
                if isinstance(ln, dict)
            ],
        },
        "loading_info": {
            "lines": [
                {"line_idx": ln.get("line_idx"), "loaded_qty": ln.get("loaded_qty")}
                for ln in ((src.get("loading_info") or {}).get("lines") or [])
                if isinstance(ln, dict)
            ],
            "events": [
                {"line_idx": ev.get("line_idx"), "qty": ev.get("qty")}
                for ev in ((src.get("loading_info") or {}).get("events") or [])
                if isinstance(ev, dict)
            ],
        },
        "delivery_reschedules": [
            {"status": r.get("status"), "new_date": r.get("new_date")}
            for r in (src.get("delivery_reschedules") or [])
            if isinstance(r, dict)
        ],
    }
    snap = src.get("customer_approval")
    if isinstance(snap, dict):
        shaped["customer_approval"] = {"status": snap.get("status")}
    si = src.get("screener_info")
    if isinstance(si, dict):
        shaped["screener_info"] = {"screen_qty": si.get("screen_qty")}
    hp = src.get("helper_prep")
    if isinstance(hp, dict):
        shaped["helper_prep"] = {"qty_prepared": hp.get("qty_prepared")}
    lc = src.get("loading_checklist")
    if isinstance(lc, dict):
        shaped["loading_checklist"] = {
            "items_loaded": lc.get("items_loaded"),
            "seal_ok": lc.get("seal_ok"),
        }
    return shaped


def _po_needs_screener(o: dict) -> bool:
    # Revisi routing: Screener adalah persiapan cetak. Full non-print PO tidak
    # membutuhkan Screener. Tersedia setelah Penjadwalan disimpan.
    if not _requires_printing(o) or not _stage_penjadwalan_done(o):
        return False
    return not _stage_screener_done(o)


def _po_needs_helper(o: dict) -> bool:
    # Helper target mengikuti jadwal aktif. Actual prepared tidak diubah penjadwalan.
    if not _requires_printing(o) or not _stage_penjadwalan_done(o):
        return False
    target = _qr_helper_target_qty(o)
    hp = o.get("helper_prep") or {}
    total_prep = hp.get("qty_prepared") or 0
    return total_prep < target


def _po_needs_design_upload(o: dict) -> bool:
    return not (o.get("design_files") or [])


def _po_needs_design_approve(o: dict) -> bool:
    if o.get("design_status") == "Approved" or o.get("design_bypassed"):
        return False
    return bool(o.get("design_files"))


def _po_needs_payment_approve(o: dict) -> bool:
    if o.get("payment_status") == "Approved" or o.get("payment_bypassed"):
        return False
    return bool(o.get("payment_files"))


def _po_needs_penjadwalan(o: dict) -> bool:
    if not (_design_ok(o) and _payment_ok(o)):
        return False
    return not _stage_penjadwalan_done(o)


def _po_needs_sample_upload(o: dict) -> bool:
    return _requires_printing(o) and _stage_screener_done(o) and _stage_penjadwalan_done(o) and not _sample_uploaded(o)


def _po_needs_sample_approve(o: dict) -> bool:
    return _requires_printing(o) and _sample_uploaded(o) and not _sample_ok(o)


def _po_needs_cetak_qty(o: dict) -> bool:
    return _requires_printing(o) and _sample_ok(o) and not _print_qty_input(o)


def _po_needs_packing(o: dict) -> bool:
    # Packing dibutuhkan jika prereq semua sudah OK tapi belum masuk stage packing (>=11).
    # Full non-cetak: Packing di-skip (langsung Loading setelah prerequisite SJ).
    if not _requires_printing(o):
        return False
    if (o.get("current_stage") or 0) >= 12:
        return False
    return len(_prereq_reasons_for_packing(o)) == 0


def _po_needs_surat_jalan(o: dict) -> bool:
    if _surat_jalan_created(o) or o.get("delivery_status") == "Terkirim":
        return False
    return (o.get("current_stage") or 0) >= 11 and not o.get("delivery_note")


def _po_needs_loading(o: dict) -> bool:
    """Loading confirm is optional work: SJ exists and remaining qty > 0."""
    if o.get("delivery_status") == "Terkirim" or _terkirim_confirmed(o):
        return False
    return _po_needs_loading_task(o)


def _po_needs_terkirim(o: dict) -> bool:
    if o.get("delivery_status") == "Terkirim" or _terkirim_confirmed(o):
        return False
    if not _surat_jalan_created(o):
        return False
    # Delivery record / SJ existence alone is not Pengiriman readiness.
    return _qr_eligible_delivery_qty(o) > 0


def _op_task_predicate(o: dict, user_email: str, is_supervisor: bool = False):
    """Tugas Saya Operator: only splits assigned to the authenticated user.

    `is_supervisor` is ignored — Tugas Saya is always MY assignments, never team.
    """
    if not _requires_printing(o):
        return []
    if not _sample_ok(o):
        return []
    email = (user_email or "").strip()
    if not email:
        return []
    return _pending_splits_for_user(o, email)


# Mapping: action_key → (label, permission_required, predicate)
_ACTION_DEFS = [
    ("design_upload", "Upload Design", "design.upload", _po_needs_design_upload),
    ("design_approve", "Approval Design", "design.approve", _po_needs_design_approve),
    ("payment_approve", "Approval Payment", "payment.approve", _po_needs_payment_approve),
    ("penjadwalan", "Penjadwalan", "schedule.edit", _po_needs_penjadwalan),
    ("screener", "Isi Qty Screen", "screen.update", _po_needs_screener),
    ("helper", "Persiapan Helper", "helper.prepare", _po_needs_helper),
    ("sample_upload", "Upload Sampel Cetak", "operator.sample_upload", _po_needs_sample_upload),
    ("sample_approve", "Approval Sampel", "operator.sample_approve", _po_needs_sample_approve),
    ("cetak_qty", "Input Qty Cetak", "operator.update", _po_needs_cetak_qty),
    ("packing", "Packing", "packing.update", _po_needs_packing),
    ("surat_jalan", "Buat Surat Jalan", "delivery_note.manage", _po_needs_surat_jalan),
    ("loading", "Konfirmasi Loading", "loading.update", _po_needs_loading),
    ("terkirim", "Tandai Terkirim", "delivery.update", _po_needs_terkirim),
]


def _slim_action(o: dict) -> dict:
    lis = o.get("line_items") or []
    item = ""
    names = [li.get("name") for li in lis if isinstance(li, dict) and li.get("name")]
    if len(names) == 1:
        item = names[0]
    elif names:
        item = f"{len(names)} item"
    return {
        "id": o.get("id"),
        "po_number": o.get("po_number"),
        "outlet": o.get("outlet"),
        "customer": o.get("customer"),
        "item": item or o.get("outlet") or "",
        "work_date": o.get("work_date"),
        "current_stage": o.get("current_stage"),
        "total_quantity": o.get("total_quantity"),
        "delivery_deadline_effective": o.get("delivery_deadline_effective"),
        "delivery_deadline_status": o.get("delivery_deadline_status"),
        "delivery_deadline_source": o.get("delivery_deadline_source"),
        "requires_printing": o.get("requires_printing"),
        "design_status": o.get("design_status"),
    }


def _deadline_priority(o: dict) -> int:
    return ({"overdue": 0, "h6": 1, "h1": 2, "normal": 3, "unknown": 4, "terkirim": 5}
            .get(o.get("delivery_deadline_status") or "unknown", 4))


@api_router.get("/dashboard/action-items")
async def dashboard_action_items(user=Depends(current_user)):
    """Batch 3: Canonical action center. Backend menghitung PO yang benar-benar
    membutuhkan tindakan `action_key` DAN user punya permission efektif untuk
    action tsb. Frontend hanya render hasil. Count == list length."""
    # Effective permissions: admin bypass, else union role + overrides.
    if user.get("role") == "admin":
        perms = set(ALL_PERMISSIONS)
    else:
        perms = set(await user_permissions(user))
    user_email = (user.get("email") or "").strip()
    action_keys_needed = [key for key, _label, req_perm, _pred in _ACTION_DEFS if req_perm in perms and key != "cetak_qty"]
    operator_only = (
        "operator.update" in perms
        and not action_keys_needed
    )
    query: dict = {"cancelled": {"$ne": True}}
    if operator_only:
        query["operator_splits.operator_email"] = user_email
    raw = await db.orders.aggregate([
        {"$match": query},
        {"$limit": _ACTION_CENTER_LIMIT},
        {"$project": _orders_action_center_projection()},
    ]).to_list(_ACTION_CENTER_LIMIT)
    dd_settings = await _get_delivery_deadline_settings()
    for o in raw:
        _apply_hold_snapshot(o)
        _attach_deadline_snapshot(o, dd_settings["lead_time_days"])
        o["requires_printing"] = _requires_printing(o)
        o["terkirim_confirmed"] = _terkirim_confirmed(o)

    actions = []
    for key, label, req_perm, predicate in _ACTION_DEFS:
        if req_perm not in perms:
            continue
        if key == "cetak_qty":
            continue
        matched = []
        for o in raw:
            # Cancelled/Terkirim final: skip (kecuali action=terkirim yang sudah
            # difilter di predicate).
            if o.get("delivery_status") == "Terkirim" and key != "terkirim":
                continue
            if predicate(o):
                # HOLD PO tetap ditampilkan sebagai tugas (biarkan user tahu &
                # ambil tindakan HOLD-related). Tidak di-skip.
                matched.append(o)
        matched.sort(key=_deadline_priority)
        actions.append({
            "key": key,
            "label": label,
            "permission": req_perm,
            "count": len(matched),
            "items": [_slim_action(o) for o in matched],
        })

    # Operator task (spesial: filter by assignment kalau bukan supervisor)
    if "operator.update" in perms:
        matched = []
        for o in raw:
            if o.get("delivery_status") == "Terkirim":
                continue
            splits = _op_task_predicate(o, user_email)
            if not splits:
                continue
            slim = _slim_action(o)
            slim["my_operator_splits"] = [{
                "operator_email": s.get("operator_email"),
                "operator_name": s.get("operator_name"),
                "qty_assigned": s.get("quantity"),
                "qty_completed": s.get("qty_completed", 0),
                "qty_remaining": max(int(s.get("quantity", 0)) - int(s.get("qty_completed", 0) or 0), 0),
                "stage": s.get("stage"),
                "work_date": s.get("work_date"),
            } for s in splits]
            matched.append((slim, o))
        matched.sort(key=lambda pair: _deadline_priority(pair[1]))
        actions.append({
            "key": "operator_work",
            "label": "Pekerjaan Operator",
            "permission": "operator.update",
            "scope": "mine",
            "count": len(matched),
            "items": [m[0] for m in matched],
        })

    return {"actions": actions, "generated_at": now_iso()}


@api_router.get("/work/division-tasks")
async def work_division_tasks(
    division: str,
    scope: str = "mine",
    user=Depends(current_user),
):
    """One list query per division. Server scopes Operator rows to the session user."""
    div = _resolve_division(division)
    if not div:
        raise HTTPException(400, "Divisi tidak dikenali")
    cfg = _DIVISION_TASK_DEFS[div]
    await _require_perm(user, cfg["submit_perm"])
    if user.get("role") == "admin":
        perms = set(ALL_PERMISSIONS)
    else:
        perms = set(await user_permissions(user))
    view_all = _can_view_all_division(perms)
    mine_only = (scope or "mine").strip().lower() != "all" or not view_all
    query: dict = {"cancelled": {"$ne": True}}
    if div == "operator" and mine_only:
        email = (user.get("email") or "").strip()
        if email:
            query["operator_splits.operator_email"] = email
    raw = await db.orders.find(query, _DIVISION_TASK_PROJECTION).to_list(1000)
    dd_settings = await _get_delivery_deadline_settings()
    lead_days = dd_settings["lead_time_days"]
    for o in raw:
        _attach_deadline_snapshot(o, lead_days)
    tasks = _build_division_tasks(raw, div, user, mine_only=mine_only)
    # Kepala Produksi: one batch Persediaan read-model lookup (no N+1, no writes).
    if div == "kepala_produksi":
        stock_rows, _, _ = await _load_stock_context()
        _enrich_tasks_current_stock(tasks, stock_rows)
    return {
        "division": div,
        "slug": cfg["slug"],
        "label": cfg["label"],
        "scope": "mine" if mine_only else "all",
        "can_view_all": view_all,
        "can_view_po": "po.view" in perms,
        "required_input": cfg["required_input"],
        "tasks": tasks,
        "count": len(tasks),
        "generated_at": now_iso(),
    }


# ---- Screener info (Requirement 10) ----
@api_router.post("/orders/{order_id}/screener-info")
async def update_screener_info(order_id: str, payload: ScreenerInfoPayload, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    await _require_perm(user, "screen.update")
    if _requires_printing(order) and not _stage_penjadwalan_done(order):
        raise HTTPException(400, "Tahap Screener belum dapat diproses. Selesaikan Penjadwalan terlebih dahulu.")
    screener_err = _qr_validate_screener_qty(
        payload.qty if payload.qty is not None else payload.screen_qty, order
    )
    if screener_err:
        raise HTTPException(400, screener_err)
    before = order.get("screener_info") or {}
    entries = list(before.get("entries") or [])
    increment = payload.qty
    if increment is None and payload.screen_qty is not None and not entries:
        increment = int(payload.screen_qty)
        entries = []
    elif increment is None and payload.screen_qty is not None and entries:
        raise HTTPException(409, "Entry Screener sudah dikonfirmasi. Kirim pekerjaan tambahan sebagai qty baru.")
    if increment is None:
        raise HTTPException(400, "Qty Screener wajib diisi.")
    if int(increment) <= 0:
        raise HTTPException(400, "Qty Screener harus lebih besar dari 0.")
    if find_by_action_id(entries, payload.action_id):
        return {"order": order}
    entries = seed_legacy(
        entries,
        snapshot_qty=before.get("screen_qty"),
        stage="screener",
        now=now_iso(),
        at=before.get("updated_at"),
        by=before.get("updated_by"),
        by_name=before.get("updated_by_name"),
        po_id=order.get("id"),
    )
    entries, _entry, created = append_qty_entry(
        entries,
        qty=int(increment),
        stage="screener",
        user=user,
        now=now_iso(),
        action_id=payload.action_id,
        po_id=order.get("id"),
        extra={"notes": (payload.notes or "").strip() or None},
    )
    if not created:
        return {"order": order}
    total = sum_entry_qty(entries)
    order["screener_info"] = {
        "screen_qty": total,
        "notes": (payload.notes or "").strip() or before.get("notes"),
        "entries": entries,
        "updated_by": user.get("email"),
        "updated_by_name": user.get("name"),
        "updated_at": now_iso(),
    }
    # Auto-progress: setelah Screener Save, Operator menjadi READY.
    # Tidak menandai Helper selesai (paralel).
    add_timeline(order, order["current_stage"], user, note=f"Screen qty +{increment} (total {total})")
    _sync_workflow_stage(order)
    await _persist_order(order)
    await _audit_po(order, "screener.info.update", user, before=before, after=order["screener_info"])
    return {"order": order}


@api_router.post("/orders/{order_id}/assign-operators")
async def assign_operators(order_id: str, payload: AssignOperatorsPayload, user=Depends(current_user)):
    await _require_perm(user, "schedule.edit")

    def mutate(order: dict):
        rows = []
        for s in payload.splits:
            wd = s.work_date
            if wd is not None:
                wd = _parse_work_date(wd)
            dumped = s.model_dump(exclude_unset=True)
            row = {
                "id": s.id,
                "operator_email": s.operator_email,
                "operator_name": s.operator_name,
                "quantity": int(s.quantity),
                "line_idx": s.line_idx,
                "work_date": wd,
            }
            if "screen_size" in dumped:
                row["screen_size"] = dumped.get("screen_size")
            rows.append(row)
        try:
            new_splits, cancel_notes = _apply_operator_assignments(
                order, rows, user, now_iso(), new_id_fn=lambda: str(uuid.uuid4()),
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        note = f"Assign {len(new_splits)} operator"
        if cancel_notes:
            released = sum(int(c.get("cancel_qty") or 0) for c in cancel_notes)
            note = f"{note}; lepas sisa {released}"
        add_timeline(order, order["current_stage"], user, note=note)
        _sync_workflow_stage(order)

    order = await _with_quantity_lock(order_id, mutate)
    return {"order": order}


@api_router.post("/orders/{order_id}/operator-splits/{split_id}/cancel")
async def cancel_operator_split_remaining(
    order_id: str, split_id: str, payload: CancelSplitPayload, user=Depends(current_user),
):
    """Partial cancel of remaining scheduled qty. Completed qty and assignment history stay."""
    await _require_perm(user, "schedule.edit")

    def mutate(order: dict):
        try:
            found = _cancel_split_remaining(
                order, split_id, int(payload.qty), user, now_iso(), payload.reason,
            )
        except ValueError as e:
            msg = str(e)
            code = 404 if "tidak ditemukan" in msg else 400
            raise HTTPException(code, msg)
        add_timeline(
            order, order["current_stage"], user,
            note=f"Lepas sisa {payload.qty} dari {found.get('operator_name')} (completed {found.get('qty_completed')})",
        )
        _sync_workflow_stage(order)

    order = await _with_quantity_lock(order_id, mutate)
    return {"order": order}


# ---------- Req 13: Tanggal Pengerjaan (work_date) ----------
class WorkDatePayload(BaseModel):
    work_date: Optional[str] = None  # ISO date string (YYYY-MM-DD) or ISO datetime; null clears
    note: Optional[str] = None


def _parse_work_date(v: Optional[str]) -> Optional[str]:
    if v is None or v == "":
        return None
    s = str(v).strip()
    # Accept YYYY-MM-DD or full ISO — normalize to YYYY-MM-DD
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
        # Validate as date
        return datetime.strptime(s[:10], "%Y-%m-%d").date().isoformat()
    except Exception:
        raise HTTPException(400, "Format tanggal tidak valid (gunakan YYYY-MM-DD)")


@api_router.patch("/orders/{order_id}/work-date")
async def set_order_work_date(order_id: str, payload: WorkDatePayload, user=Depends(current_user)):
    """Req 13: Set global Tanggal Pengerjaan for a PO (used when no operator split
    is present). Does NOT modify deadline or estimated_delivery."""
    await _require_perm(user, "schedule.work_date.edit")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    new_val = _parse_work_date(payload.work_date)
    before_val = order.get("work_date")
    order["work_date"] = new_val
    add_timeline(order, order["current_stage"], user, note=(payload.note or f"Tanggal Pengerjaan: {new_val or '(dikosongkan)'}"))
    await _persist_order(order)
    await _write_audit(
        "schedule.work_date.update", user,
        target_email=None,
        before={"po_number": order["po_number"], "work_date": before_val},
        after={"po_number": order["po_number"], "work_date": new_val},
        note=f"PO {order['po_number']}: {before_val or '-'} → {new_val or '-'}",
    )
    return {"order": order}


@api_router.patch("/orders/{order_id}/operator-splits/{split_id}/work-date")
async def set_split_work_date(order_id: str, split_id: str, payload: WorkDatePayload, user=Depends(current_user)):
    """Req 13: Set Tanggal Pengerjaan for a specific operator split. Supports
    different dates per operator."""
    await _require_perm(user, "schedule.work_date.edit")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    splits = order.get("operator_splits", [])
    found = next((s for s in splits if s.get("id") == split_id), None)
    if not found:
        raise HTTPException(404, "Operator split tidak ditemukan")
    new_val = _parse_work_date(payload.work_date)
    before_val = found.get("work_date")
    found["work_date"] = new_val
    found["updated_at"] = now_iso()
    add_timeline(order, order["current_stage"], user, note=f"Tanggal Pengerjaan {found['operator_name']}: {new_val or '(dikosongkan)'}")
    await _persist_order(order)
    await _write_audit(
        "schedule.work_date.update", user,
        target_email=None,
        before={"po_number": order["po_number"], "split_id": split_id, "operator": found.get("operator_name"), "work_date": before_val},
        after={"po_number": order["po_number"], "split_id": split_id, "operator": found.get("operator_name"), "work_date": new_val},
        note=f"PO {order['po_number']} · Operator {found.get('operator_name')}: {before_val or '-'} → {new_val or '-'}",
    )
    return {"order": order}


@api_router.post("/orders/{order_id}/operator-progress")
async def update_operator_progress(order_id: str, payload: OperatorProgressPayload, user=Depends(current_user)):
    await _require_perm(user, "operator.update")
    eff = await user_permissions(user)
    wage_doc = await db.app_settings.find_one({"key": OPERATOR_WAGE_SETTINGS_KEY}, {"_id": 0}) or {}

    existing_order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not existing_order:
        raise HTTPException(404, "Order tidak ditemukan")
    splits_pre = existing_order.get("operator_splits", [])
    found_split_pre = next((s for s in splits_pre if s["id"] == payload.operator_id), None)

    # Resolve live physical-stock enablement once per request for Cetak helpers.
    # Skip the settings read when the order snapshot is already inactive.
    snap_enabled = bool((existing_order.get("physical_stock_snapshot") or {}).get("enabled"))
    live_enabled = False
    if snap_enabled:
        from services.physical_stock_settings import get_physical_stock_settings
        live_enabled = bool((await get_physical_stock_settings(db)).get("enabled"))

    if found_split_pre:
        inc_check = payload.qty
        if inc_check is None and payload.qty_completed is not None and not found_split_pre.get("progress_entries"):
            inc_check = int(payload.qty_completed)
        if inc_check and int(inc_check) > 0:
            from services.physical_stock_engine import precheck_operator_progress
            await precheck_operator_progress(
                db, existing_order, found_split_pre, int(inc_check), live_enabled=live_enabled
            )

    act_id = (payload.action_id or "").strip() or str(uuid.uuid4())
    payload.action_id = act_id
    claimed_stock = None
    if found_split_pre:
        inc_val = payload.qty
        if inc_val is None and payload.qty_completed is not None and not found_split_pre.get("progress_entries"):
            inc_val = int(payload.qty_completed)
        if inc_val and int(inc_val) > 0:
            from services.physical_stock_engine import (
                should_claim_operator_stock,
                claim_operator_stock,
            )
            if await should_claim_operator_stock(
                db, existing_order, found_split_pre, live_enabled=live_enabled
            ):
                claimed_stock = await claim_operator_stock(
                    db, existing_order, found_split_pre, int(inc_val), act_id, user
                )

    op_applied = {}

    def mutate(order: dict):
        splits = order.get("operator_splits", [])
        found = next((s for s in splits if s["id"] == payload.operator_id), None)
        if not found:
            raise HTTPException(404, "Operator split tidak ditemukan")
        if "schedule.edit" not in eff and found.get("operator_email") != user.get("email"):
            raise HTTPException(403, "Anda hanya dapat memperbarui qty assignment Anda sendiri")
        _ensure_print_qty_allowed(order)
        cap = int(found["quantity"])
        entries = list(found.get("progress_entries") or [])
        if find_by_action_id(entries, payload.action_id):
            return
        increment = payload.qty
        if increment is None and payload.qty_completed is not None and not entries:
            increment = int(payload.qty_completed)
        elif increment is None and payload.qty_completed is not None and entries:
            raise HTTPException(409, "Entry Operator sudah dikonfirmasi. Kirim pekerjaan tambahan sebagai qty baru.")
        if increment is None:
            raise HTTPException(400, "Qty cetak selesai wajib diisi.")
        increment = int(increment)
        if increment < 0:
            raise HTTPException(400, "Qty cetak selesai tidak boleh negatif.")
        entries = seed_legacy(
            entries,
            snapshot_qty=found.get("qty_completed"),
            stage="operator",
            now=now_iso(),
            at=found.get("updated_at"),
            by=found.get("operator_email"),
            by_name=found.get("operator_name"),
            po_id=order.get("id"),
            line_idx=found.get("line_idx"),
            split_id=found.get("id"),
        )
        new_total = sum_entry_qty(entries) + increment
        if new_total > cap:
            raise HTTPException(
                400,
                f"Qty selesai ({new_total}) melebihi alokasi operator ({cap}).",
            )
        others = sum(
            int(s.get("qty_completed") or 0)
            for s in splits if s.get("id") != found.get("id")
        )
        err = _qr_validate_print_completed_total(others + new_total, order)
        if err:
            raise HTTPException(400, err)
        lis = order.get("line_items") or []
        ident = {}
        try:
            idx = int(found.get("line_idx"))
            if 0 <= idx < len(lis):
                ident = _wage_item_identity(lis[idx])
        except (TypeError, ValueError):
            ident = {}
        rate = _wage_effective_rate(ident, wib_today(), wage_doc, operator=found)
        extra = {"rate_per_pcs": rate} if rate is not None else None
        entries, _entry, created = append_qty_entry(
            entries,
            qty=increment,
            stage="operator",
            user=user,
            now=now_iso(),
            action_id=payload.action_id,
            po_id=order.get("id"),
            line_idx=found.get("line_idx"),
            split_id=found.get("id"),
            extra=extra,
        )
        if not created:
            return
        found["progress_entries"] = entries
        found["qty_completed"] = sum_entry_qty(entries)
        found["updated_at"] = now_iso()
        op_applied["split"] = found
        op_applied["increment"] = increment
        op_applied["created"] = created
        add_timeline(order, order["current_stage"], user, note=f"Qty {found['operator_name']}: +{increment} (total {found['qty_completed']}/{found['quantity']})")

    try:
        order = await _with_quantity_lock(order_id, mutate)
    except Exception:
        if claimed_stock:
            from services.physical_stock_engine import release_operator_stock
            await release_operator_stock(db, claimed_stock)
        raise

    if claimed_stock:
        from services.physical_stock_engine import finalize_operator_stock
        await finalize_operator_stock(db, claimed_stock, user)
    elif op_applied.get("created") and op_applied.get("increment", 0) > 0 and payload.action_id:
        from services.physical_stock_engine import handle_operator_progress
        await handle_operator_progress(
            db,
            order,
            op_applied["split"],
            op_applied["increment"],
            payload.action_id,
            user,
            live_enabled=live_enabled,
        )
    return {"order": order}


@api_router.post("/orders/{order_id}/urgent")
async def set_urgent(order_id: str, payload: UrgentPayload, user=Depends(current_user)):
    await _require_perm(user, "urgent.manage")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    order["priority"] = "urgent" if payload.urgent else "normal"
    order["urgency_approved_by"] = user.get("email") if payload.urgent else None
    add_timeline(order, order["current_stage"], user, note=f"Prioritas diubah ke {order['priority']}")
    await _persist_order(order)
    return {"order": order}


class BufferStockPayload(BaseModel):
    buffer_stock: bool


@api_router.post("/orders/{order_id}/buffer-stock")
async def set_buffer_stock(order_id: str, payload: BufferStockPayload, user=Depends(current_user)):
    """CRM / user berwenang menandai PO sebagai Buffer Stock (persisted, filterable)."""
    await _require_perm(user, "buffer_stock.manage")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    before = bool(order.get("buffer_stock"))
    order["buffer_stock"] = bool(payload.buffer_stock)
    order["buffer_stock_updated_by"] = user.get("email")
    order["buffer_stock_updated_by_name"] = user.get("name")
    order["buffer_stock_updated_at"] = now_iso()
    label = "Buffer Stock" if order["buffer_stock"] else "Non Buffer Stock"
    add_timeline(order, order["current_stage"], user, note=f"Ditandai {label}")
    await _persist_order(order)
    await _audit_po(
        order, "buffer_stock.update", user,
        before={"buffer_stock": before},
        after={"buffer_stock": order["buffer_stock"]},
    )
    return {"order": order}


@api_router.post("/orders/{order_id}/attachment")
async def add_attachment(order_id: str, payload: AttachmentInput, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    kind = payload.kind
    if kind == "design":
        await _require_perm(user, "design.upload")
    elif kind == "payment":
        await _require_perm(user, "payment.upload")
    elif kind == "sample":
        await _require_perm(user, "operator.sample_upload")
        _ensure_screener_for_operator_work(order, for_sample=True)
    elif kind == "surat_jalan":
        await _require_perm(user, "delivery_note.manage")
    else:
        raise HTTPException(400, "Jenis attachment tidak dikenal")
    if len(payload.data_url) > 8_000_000:
        raise HTTPException(400, "File terlalu besar (maks ~6MB)")
    # Batch 2: MIME validation — hanya izinkan image (jpg/png/webp/gif) + PDF.
    # Cek dari data URL header (client-provided) — cukup untuk gate pertama.
    # Executable/script ditolak.
    dh = payload.data_url[:40] if payload.data_url else ""
    if not dh.startswith("data:"):
        raise HTTPException(400, "Format data URL tidak valid")
    try:
        detected_ct = dh.split("data:", 1)[1].split(";", 1)[0].lower()
    except Exception:
        detected_ct = ""
    _ALLOWED_ATT_MIME = {
        "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
        "application/pdf",
    }
    if detected_ct not in _ALLOWED_ATT_MIME:
        raise HTTPException(400, f"Tipe file tidak didukung ({detected_ct or 'unknown'}). Gunakan JPG, PNG, WEBP, GIF, atau PDF.")
    entry = {
        "id": str(uuid.uuid4()),
        "name": payload.name,
        "data_url": payload.data_url,
        "content_type": detected_ct,
        "uploaded_by": user.get("email"),
        "uploaded_by_role": user.get("role"),
        "uploaded_by_name": user.get("name"),
        "uploaded_at": now_iso(),
    }
    audit_action = None
    if kind == "design":
        was_approved = order.get("design_status") == "Approved"
        order.setdefault("design_files", []).append(entry)
        # New design upload resets active approval (history retained)
        if was_approved:
            order["design_status"] = "Waiting"
            order["design_approved_by"] = None
            order["design_approved_by_name"] = None
            order["design_approved_at"] = None
            order.setdefault("design_approval_history", []).append({
                "action": "reset-on-new-upload",
                "by_email": user.get("email"),
                "by_name": user.get("name"),
                "at": now_iso(),
                "note": "Design baru diupload — status kembali Menunggu Approval",
            })
        else:
            # First-time upload transitions from "Belum Ada Design" -> "Menunggu Approval Design"
            if order.get("design_status", "Waiting") in ("Waiting", None, ""):
                order["design_status"] = "Waiting"
        # Customer Approval binding — link versi lama harus otomatis invalid.
        try:
            await _ca_router._invalidate_on_design_reupload(order, user)
        except Exception:
            # Never fail upload karena customer approval bookkeeping — soft best-effort.
            logger.exception("Gagal invalidate customer approval link")
        audit_action = "design.upload"
    elif kind == "payment":
        deny = payment_proof_reject_reason(order.get("payment_files"))
        if deny:
            raise HTTPException(400, deny)
        was_approved = order.get("payment_status") == "Approved"
        order.setdefault("payment_files", []).append(entry)
        if was_approved:
            order["payment_status"] = "Waiting"
            order["payment_approved_by"] = None
            order["payment_approved_by_name"] = None
            order["payment_approved_at"] = None
            order.setdefault("payment_approval_history", []).append({
                "action": "reset-on-new-upload",
                "by_email": user.get("email"),
                "by_name": user.get("name"),
                "at": now_iso(),
                "note": "Bukti payment baru diupload — status kembali Menunggu Approval",
            })
        audit_action = "payment.upload"
    elif kind == "sample":
        was_approved = bool(order.get("sample_approved"))
        order.setdefault("sample_files", []).append(entry)
        # Workflow 06-12 targeted bugfix: re-upload sampel reset approval aktif.
        # History disimpan — file/history lama TIDAK dihapus.
        if was_approved:
            order["sample_approved"] = False
            order["sample_approved_by"] = None
            order["sample_approved_by_name"] = None
            order["sample_approved_at"] = None
            order.setdefault("sample_approval_history", []).append({
                "action": "reset-on-new-upload",
                "by_email": user.get("email"),
                "by_name": user.get("name"),
                "at": now_iso(),
                "note": "Sampel baru diupload — status kembali Menunggu Approval",
            })
            _sync_workflow_stage(order, force=True)
        else:
            _sync_workflow_stage(order)
        audit_action = "sample.upload"
    elif kind == "surat_jalan":
        # File-only. Independent of packing / Loading / previous production stage.
        # Item/Qty is confirmed later on the Loading card, linked to this file id.
        stamp = now_iso()
        selected_raw = str(payload.delivery_date or "").strip()
        try:
            op_key = resolve_sj_operational_date(order, payload.delivery_date)
        except ValueError as e:
            raise HTTPException(400, str(e))
        op_iso = operational_iso(op_key)
        try:
            raw_bytes, decoded_ct = _sj_decode_data_url(payload.data_url)
        except ValueError as e:
            raise HTTPException(400, str(e))
        ct = _sj_sniff_ct(raw_bytes, payload.name, decoded_ct or detected_ct)
        entry["content_type"] = ct
        ext = obj_storage.ext_for(ct, "bin")
        storage_path = f"{obj_storage.APP_NAME}/surat-jalan/{order.get('id')}/{entry['id']}.{ext}"
        try:
            stored = obj_storage.put_object(storage_path, raw_bytes, ct)
            entry["storage_path"] = stored.get("path") or storage_path
            await db.files.insert_one({
                "id": entry["id"],
                "storage_path": entry["storage_path"],
                "original_filename": payload.name,
                "content_type": ct,
                "size": stored.get("size") or len(raw_bytes),
                "kind": "surat_jalan",
                "order_id": order["id"],
                "uploaded_by": user.get("email"),
                "uploaded_at": stamp,
                "is_deleted": False,
            })
            # Bytes live in object storage; do not keep a second copy on the PO.
            entry.pop("data_url", None)
        except Exception:
            logger.exception("Surat Jalan object storage write failed; keeping data_url fallback")
        order.setdefault("surat_jalan_files", []).append(entry)
        if not order.get("delivery_note_number"):
            from services.sales_deliveries import derive_base_sj_number
            order["delivery_note_number"] = derive_base_sj_number(str(order.get("po_number") or ""))
        # Operational date for Pengiriman Hari Ini — not file uploaded_at.
        order["delivery_note_created_at"] = op_iso
        if selected_raw or not parse_operational_date_key(order.get("scheduled_delivery_date")):
            order["scheduled_delivery_date"] = op_key
        if not order.get("delivery_note_created_by"):
            order["delivery_note_created_by"] = user.get("email")
            order["delivery_note_created_by_name"] = user.get("name")
        _sync_workflow_stage(order)
        audit_action = "delivery_note.upload"
    else:
        raise HTTPException(400, "Jenis attachment tidak dikenal")
    # Attribute timeline to the workflow stage of the upload kind — never to
    # whatever current_stage happens to be (Payment-first flow must not bury
    # Design uploads under Approval Payment stage).
    _KIND_TIMELINE_STAGE = {"design": 1, "payment": 3, "sample": 8, "surat_jalan": 12}
    tl_stage = _KIND_TIMELINE_STAGE.get(kind, order.get("current_stage") or 0)
    add_timeline(order, tl_stage, user, note=f"Upload {kind}: {payload.name}")
    await _persist_order(order)
    if audit_action:
        await _audit_po(order, audit_action, user, note=f"{payload.name}")
    return {"order": _strip_sj_bytes(order)}


_STAFF_ORDER_VIEW_PERMS = (
    "po.view", "work_panel.view", "schedule.view",
    "screen.update", "helper.prepare", "operator.update",
    "packing.update", "delivery_note.view", "delivery_note.manage",
    "loading.update", "delivery.update", "design.view", "payment.view",
)


async def _require_staff_order_view(user) -> None:
    """Same view gate as GET /orders/:id. Admin skips; others need one view perm."""
    if user.get("role") == "admin":
        return
    eff = await user_permissions(user)
    if not any(p in eff for p in _STAFF_ORDER_VIEW_PERMS):
        raise HTTPException(403, "Anda tidak punya permission untuk melihat PO ini")


@api_router.get("/orders/{order_id}/attachments/{kind}/{file_id}")
async def get_order_attachment(order_id: str, kind: str, file_id: str, user=Depends(current_user)):
    """Authenticated staff fetch of one design/payment file stored as data_url."""
    reject_sales_pwa_bypass(user)
    kind_key = (kind or "").strip().lower()
    field = _inline_attachment_field(kind_key)
    if kind_key not in _INLINE_ATTACHMENT_KINDS or not field:
        raise HTTPException(400, "Jenis file tidak valid")
    query = {"id": order_id}
    query.update(sales_owner_match(user))
    order = await db.orders.find_one(query, {"_id": 0, "id": 1, field: 1})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    await _require_staff_order_view(user)
    entry = _find_inline_attachment(order, kind_key, file_id)
    if not entry:
        raise HTTPException(404, "File tidak ditemukan")
    try:
        data, ct = _decode_inline_attachment(entry)
    except _UnsafeInlineMime:
        raise HTTPException(415, "Tipe file tidak didukung untuk preview")
    except ValueError:
        raise HTTPException(404, "Isi file tidak tersedia")
    filename = str(entry.get("name") or "file")
    headers = {
        "Content-Disposition": _inline_attachment_disposition(filename),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    return FastAPIResponse(content=data, media_type=ct, headers=headers)


async def _safe_db_call(coro_or_val):
    if inspect.isawaitable(coro_or_val):
        return await coro_or_val
    return None


@api_router.delete("/orders/{order_id}/design/files/{file_id}")
async def delete_design_file(order_id: str, file_id: str, user=Depends(current_user)):
    """Delete an exact design file attachment from an order.

    Requires design.upload permission.
    Preserves order, design record, revisions, and approval history.
    Invalidates any active approval link bound to this exact deleted file.
    """
    await _require_perm(user, "design.upload")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)

    design_files = list(order.get("design_files") or [])
    target = next((f for f in design_files if isinstance(f, dict) and f.get("id") == file_id), None)
    if not target:
        raise HTTPException(404, "File design tidak ditemukan")

    order_snapshot = copy.deepcopy(order)
    storage_path = str(target.get("storage_path") or "").strip()
    original_bytes = None
    original_ct = target.get("content_type") or "application/pdf"

    orig_ca = None
    try:
        orig_ca = await _safe_db_call(db.customer_approvals.find_one(
            {"order_id": order["id"], "design_version": file_id, "status": "active"},
            {"_id": 0},
        ))
    except Exception:
        pass

    orig_file = None
    if storage_path:
        try:
            orig_file = await _safe_db_call(db.files.find_one({"id": file_id, "order_id": order["id"]}, {"_id": 0}))
        except Exception:
            pass

        try:
            original_bytes, original_ct = obj_storage.get_object(storage_path)
        except Exception:
            logger.warning("Could not pre-fetch storage object bytes for %s", storage_path)

        deleted = False
        try:
            deleted = bool(obj_storage.delete_object(storage_path))
        except Exception:
            logger.exception("Object storage cleanup failed for design file %s", file_id)
            deleted = False
        if not deleted:
            raise HTTPException(500, "Gagal menghapus file design dari storage. Silakan coba lagi.")

    # Protected DB persistence block with full snapshot rollback
    try:
        try:
            inval_res = await _ca_router._invalidate_on_design_file_delete(order, file_id, user)
            if not orig_ca and inval_res:
                orig_ca = inval_res
        except Exception:
            logger.exception("Gagal invalidate customer approval link on design file delete")

        if storage_path:
            await _safe_db_call(db.files.update_one(
                {"id": file_id, "order_id": order["id"]},
                {"$set": {"is_deleted": True, "deleted_at": now_iso(), "deleted_by": user.get("email")}},
            ))

        order["design_files"] = [f for f in design_files if not (isinstance(f, dict) and f.get("id") == file_id)]
        filename = target.get("name") or file_id
        add_timeline(order, 1, user, note=f"Hapus file design: {filename}")
        await _persist_order(order)
        await _audit_po(order, "design.delete_file", user, note=str(filename))
    except Exception as db_exc:
        logger.exception("Database update failed during design file delete; executing full rollback")
        put_ok = True
        if storage_path and original_bytes:
            try:
                obj_storage.put_object(storage_path, original_bytes, original_ct)
                logger.info("Compensating restore succeeded for storage object %s", storage_path)
            except Exception:
                put_ok = False
                logger.exception("CRITICAL: Compensating storage restore failed for %s", storage_path)

        if orig_ca:
            try:
                await _safe_db_call(db.customer_approvals.update_one(
                    {"id": orig_ca["id"]},
                    {"$set": {
                        "status": "active",
                        "revoke_reason": orig_ca.get("revoke_reason"),
                        "revoked_by": orig_ca.get("revoked_by"),
                        "revoked_by_name": orig_ca.get("revoked_by_name"),
                        "revoked_at": orig_ca.get("revoked_at"),
                    }},
                ))
                logger.info("Compensating restore succeeded for customer approval %s", orig_ca["id"])
            except Exception:
                logger.exception("CRITICAL: Failed to restore customer approval %s during rollback", orig_ca["id"])

        if orig_file:
            try:
                await _safe_db_call(db.files.update_one(
                    {"id": file_id, "order_id": order["id"]},
                    {"$set": {
                        "is_deleted": orig_file.get("is_deleted", False),
                        "deleted_at": orig_file.get("deleted_at"),
                        "deleted_by": orig_file.get("deleted_by"),
                    }},
                ))
                logger.info("Compensating restore succeeded for db.files %s", file_id)
            except Exception:
                logger.exception("CRITICAL: Failed to restore db.files %s during rollback", file_id)

        try:
            to_restore = {k: v for k, v in order_snapshot.items() if k not in _DERIVED_ORDER_KEYS}
            await _safe_db_call(db.orders.update_one({"id": order["id"]}, {"$set": to_restore}))
            logger.info("Compensating restore succeeded for order %s", order["id"])
        except Exception:
            logger.exception("CRITICAL: Failed to restore order document %s during rollback", order["id"])

        if storage_path and original_bytes and not put_ok:
            raise HTTPException(500, "CRITICAL: Gagal memperbarui database dan gagal memulihkan file storage. Hubungi administrator sistem.")
        if isinstance(db_exc, HTTPException):
            raise
        raise HTTPException(500, "Gagal memperbarui data order di database. Perubahan dibatalkan dan status telah dipulihkan.")

    return {"order": _strip_sj_bytes(order)}


@api_router.get("/orders/{order_id}/surat-jalan/files/{file_id}")

async def download_surat_jalan_file(
    order_id: str,
    file_id: str,
    download: bool = False,
    user=Depends(current_user),
):
    """Preview or download a Surat Jalan file. Requires delivery_note.view_file.

    Never returns a permanent public object-storage URL. Bytes are streamed
    through this authenticated proxy after the permission check.
    """
    reject_sales_pwa_bypass(user)
    await _require_perm(user, _SJ_VIEW_FILE)
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    entry = _sj_find_file(order, file_id) or {}
    rec = await db.files.find_one(
        {"id": file_id, "kind": "surat_jalan", "order_id": order_id, "is_deleted": {"$ne": True}},
        {"_id": 0},
    )
    if not rec:
        rec = await db.files.find_one(
            {"id": file_id, "kind": "surat_jalan", "is_deleted": {"$ne": True}},
            {"_id": 0},
        )
    if not entry and not rec:
        raise HTTPException(404, "File Surat Jalan tidak ditemukan")
    data = None
    filename = (entry.get("name") or (rec or {}).get("original_filename") or "surat-jalan")
    ct = (entry.get("content_type") or (rec or {}).get("content_type") or "").strip() or "application/octet-stream"
    storage_path = (entry.get("storage_path") or (rec or {}).get("storage_path") or "").strip()
    signed_path = (
        str(entry.get("signed_storage_path") or "").strip()
        or str((rec or {}).get("signed_storage_path") or "").strip()
    )
    # Customer-facing preference: signed rendition when available (still streams via proxy)
    prefer_path = signed_path or storage_path
    if prefer_path:
        if not rec:
            rec = await db.files.find_one({"storage_path": prefer_path, "is_deleted": {"$ne": True}}, {"_id": 0})
        try:
            data, fetched_ct = obj_storage.get_object(prefer_path)
            ct = (rec or {}).get("content_type") or fetched_ct or ct
        except FileNotFoundError:
            data = None
            if signed_path and storage_path and signed_path != storage_path:
                try:
                    data, fetched_ct = obj_storage.get_object(storage_path)
                    ct = (rec or {}).get("content_type") or fetched_ct or ct
                except Exception:
                    data = None
        except Exception:
            logger.exception("Failed to fetch Surat Jalan object %s", prefer_path)
            raise HTTPException(502, "Gagal mengambil file Surat Jalan.")
    if data is None and str(entry.get("data_url") or "").startswith("data:"):
        try:
            data, decoded_ct = _sj_decode_data_url(entry["data_url"])
            ct = decoded_ct or ct
        except ValueError as e:
            raise HTTPException(400, str(e))
    if not data:
        raise HTTPException(404, "Isi file Surat Jalan tidak tersedia")
    ct = _sj_sniff_ct(data, filename, ct)
    headers = {
        "Content-Disposition": _sj_content_disposition(filename, download=download),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    return FastAPIResponse(content=data, media_type=ct, headers=headers)


@api_router.delete("/orders/{order_id}/surat-jalan/files/{file_id}")
async def delete_surat_jalan_file(
    order_id: str,
    file_id: str,
    user=Depends(require_perm(_SJ_DELETE_FILE)),
):
    """Delete one Surat Jalan file. Permission-gated.

    Generated (Pengiriman Pesanan) SJ files also release the matching active
    sales_delivery allocation when safe, so Aksi PO remain / create-SJ action
    returns with stageflow. Loading/Delivered blockers are preserved.
    Manual uploads remain file-only (no sales_delivery cascade).
    """
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)

    existing = _sj_find_file(order, file_id)
    if not existing:
        raise HTTPException(404, "File Surat Jalan tidak ditemukan")

    linked_delivery = None
    if _sj_is_generated(existing):
        delivery_id = str(existing.get("id") or existing.get("shipment_id") or file_id or "").strip()
        if delivery_id:
            linked_delivery = await db.sales_deliveries.find_one({"id": delivery_id}, {"_id": 0})
        if linked_delivery:
            blocker = await _sales_delivery_delete_blocker(linked_delivery)
            if blocker:
                raise HTTPException(400, blocker)

    removed = _sj_remove_file(order, file_id)
    if not removed:
        raise HTTPException(404, "File Surat Jalan tidak ditemukan")
    await db.files.update_one(
        {"id": file_id, "kind": "surat_jalan", "order_id": order["id"]},
        {"$set": {"is_deleted": True, "deleted_at": now_iso(), "deleted_by": user.get("email")}},
    )

    if linked_delivery:
        await db.sales_deliveries.delete_one({"id": linked_delivery["id"]})
        await _remove_matching_business_sj_shipment(order.get("id"), linked_delivery)
        # Reload order after shipment / stamp cleanup so response is current.
        fresh = await db.orders.find_one({"id": order_id}, {"_id": 0})
        if fresh:
            order = fresh

    add_timeline(
        order, 12, user,
        note=f"Hapus file Surat Jalan: {removed.get('name') or file_id}",
    )
    _sync_workflow_stage(order)
    await _persist_order(order)
    await _audit_po(
        order, "delivery_note.delete_file", user,
        note=str(removed.get("name") or file_id),
    )
    # Recompute derived SJ / stage flags + remain so Aksi PO matches stageflow
    # immediately (no stale "Seluruh Qty Selesai Dibuatkan SJ").
    order["surat_jalan_created"] = _surat_jalan_created(order)
    _attach_quantity_snapshot(order)
    order["sales_delivery_summary"] = await _get_po_delivery_summary(db, order)
    return {"order": _strip_sj_bytes(order)}


@api_router.post("/orders/{order_id}/note")
async def add_note(order_id: str, payload: NotePayload, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    if user.get("role") == "crm":
        raise HTTPException(403, "CRM hanya read-only")
    order["notes"] = (order.get("notes", "") + "\n" + payload.note).strip()
    add_timeline(order, order["current_stage"], user, note=f"Catatan: {payload.note}")
    await _persist_order(order)
    return {"order": order}


@api_router.post("/orders/{order_id}/loading")
async def update_loading(order_id: str, payload: LoadingChecklistPayload, user=Depends(current_user)):
    """Deprecated checklist write. Use POST /loading-confirm for SJ qty confirmation."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    await _require_perm(user, "loading.update")
    raise HTTPException(400, "Gunakan Konfirmasi Loading (item/qty Surat Jalan). Data historis tetap tersimpan.")


@api_router.post("/orders/{order_id}/loading-confirm")
async def confirm_loading(order_id: str, payload: LoadingConfirmPayload, user=Depends(current_user)):
    """Confirm Item + Qty after reading the Surat Jalan file. Append-safe."""
    await _require_perm(user, "loading.update")

    existing_order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if existing_order and payload.lines:
        from services.physical_stock_engine import precheck_loading_confirmed
        await precheck_loading_confirmed(db, existing_order, [ln.model_dump() for ln in payload.lines])

    act_id = (payload.action_id or "").strip() or str(uuid.uuid4())
    payload.action_id = act_id
    claimed_stock = None
    if existing_order and payload.lines:
        from services.physical_stock_engine import (
            should_claim_loading_stock,
            claim_loading_stock,
        )
        if await should_claim_loading_stock(db, existing_order):
            claimed_stock = await claim_loading_stock(
                db, existing_order, [ln.model_dump() for ln in payload.lines], act_id, user
            )

    record_holder = {}

    def _mutate(order):
        _ensure_not_cancelled(order)
        action_id = (payload.action_id or "").strip() or None
        if action_id:
            for existing in order.get("loading_confirmations") or []:
                if isinstance(existing, dict) and existing.get("action_id") == action_id:
                    return
        lines = [ln.model_dump() for ln in (payload.lines or [])]
        err = _qr_validate_loading_confirmation(order, payload.surat_jalan_id, lines)
        if err:
            raise HTTPException(400, err)
        stamp = now_iso()
        try:
            record = _qr_apply_loading_confirmation(
                order,
                surat_jalan_id=payload.surat_jalan_id,
                lines=lines,
                actor=user,
                confirmation_id=str(uuid.uuid4()),
                confirmed_at=stamp,
                action_id=action_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not record.get("id"):
            record["id"] = str(uuid.uuid4())
            order["loading_confirmations"][-1]["id"] = record["id"]
        record_holder["record"] = record
        names = ", ".join(f"{ln.get('name')} {ln.get('qty')}" for ln in record.get("lines") or [])
        add_timeline(order, 13, user, note=f"Konfirmasi Loading ({record.get('surat_jalan_name')}): {names}")
        _sync_workflow_stage(order)

    try:
        order = await _with_quantity_lock(order_id, _mutate)
    except Exception:
        if claimed_stock:
            from services.physical_stock_engine import release_loading_stock
            await release_loading_stock(db, claimed_stock)
        raise

    if claimed_stock:
        from services.physical_stock_engine import finalize_loading_stock
        await finalize_loading_stock(db, claimed_stock, user)
    elif record_holder.get("record"):
        from services.physical_stock_engine import handle_loading_confirmed
        await handle_loading_confirmed(db, order, record_holder["record"], user)
    await _audit_po(order, "loading.confirm", user, note=payload.surat_jalan_id)
    return {"order": _strip_sj_bytes(order)}




@api_router.post("/orders/{order_id}/loading-confirm-match")
async def confirm_loading_match(order_id: str, payload: LoadingMatchConfirmPayload, user=Depends(current_user)):
    """One-click: Semua Barang Sesuai SJ — backend generates lines from current SJ."""
    # Granular key only. Legacy loading.update expands via with_loading_permission_defaults
    # until Role & Hak Akses stamps loading.granular_configured (then revocation sticks).
    await _require_perm(user, "loading.confirm_match")
    existing_order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not existing_order:
        raise HTTPException(404, "PO tidak ditemukan")

    # Preview generated lines for stock precheck (authoritative SJ, not client qty)
    preview_lines = _qr_build_sj_loading_lines(existing_order, payload.surat_jalan_id)
    if preview_lines:
        from services.physical_stock_engine import precheck_loading_confirmed
        await precheck_loading_confirmed(db, existing_order, preview_lines)

    act_id = (payload.action_id or "").strip() or str(uuid.uuid4())
    claimed_stock = None
    if preview_lines:
        from services.physical_stock_engine import should_claim_loading_stock, claim_loading_stock
        if await should_claim_loading_stock(db, existing_order):
            claimed_stock = await claim_loading_stock(db, existing_order, preview_lines, act_id, user)

    record_holder = {}

    def _mutate(order):
        _ensure_not_cancelled(order)
        if act_id:
            for existing in order.get("loading_confirmations") or []:
                if isinstance(existing, dict) and existing.get("action_id") == act_id:
                    record_holder["record"] = existing
                    record_holder["idempotent"] = True
                    return
            for snap in order.get("loading_snapshots") or []:
                if isinstance(snap, dict) and snap.get("action_id") == act_id:
                    record_holder["record"] = snap
                    record_holder["idempotent"] = True
                    return
        err = _qr_validate_loading_match_confirmation(order, payload.surat_jalan_id)
        if err:
            raise HTTPException(400, err)
        # Already complete for this SJ → idempotent no-op success
        remaining = _qr_build_sj_loading_lines(order, payload.surat_jalan_id)
        if not remaining:
            record_holder["record"] = {"id": None, "surat_jalan_id": payload.surat_jalan_id, "lines": [], "source": "sj_match"}
            record_holder["idempotent"] = True
            return
        stamp = now_iso()
        try:
            record = _qr_apply_loading_match_confirmation(
                order,
                surat_jalan_id=payload.surat_jalan_id,
                actor=user,
                confirmation_id=str(uuid.uuid4()),
                confirmed_at=stamp,
                action_id=act_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not record.get("id"):
            record["id"] = str(uuid.uuid4())
            if order.get("loading_confirmations"):
                order["loading_confirmations"][-1]["id"] = record["id"]
        record_holder["record"] = record
        names = ", ".join(f"{ln.get('name')} {ln.get('qty')}" for ln in record.get("lines") or [])
        add_timeline(order, 13, user, note=f"Loading sesuai SJ ({record.get('surat_jalan_name') or payload.surat_jalan_id}): {names}")
        _sync_workflow_stage(order)

    try:
        order = await _with_quantity_lock(order_id, _mutate)
    except Exception:
        if claimed_stock:
            from services.physical_stock_engine import release_loading_stock
            await release_loading_stock(db, claimed_stock)
        raise

    if claimed_stock and not record_holder.get("idempotent"):
        from services.physical_stock_engine import finalize_loading_stock
        await finalize_loading_stock(db, claimed_stock, user)
    elif record_holder.get("record") and not record_holder.get("idempotent"):
        from services.physical_stock_engine import handle_loading_confirmed
        await handle_loading_confirmed(db, order, record_holder["record"], user)
    await _audit_po(order, "loading.confirm_match", user, note=payload.surat_jalan_id)
    return {"order": _strip_sj_bytes(order), "confirmation": record_holder.get("record"), "idempotent": bool(record_holder.get("idempotent"))}


@api_router.post("/orders/{order_id}/loading-report-discrepancy")
async def report_loading_discrepancy(order_id: str, payload: LoadingDiscrepancyPayload, user=Depends(current_user)):
    """Exception path: physical qty differs from SJ — holds Loading, notifies CRM."""
    await _require_perm(user, "loading.report_discrepancy")

    act_id = (payload.action_id or "").strip() or str(uuid.uuid4())
    disc_holder = {}

    def _mutate(order):
        _ensure_not_cancelled(order)
        if act_id:
            for existing in order.get("loading_discrepancies") or []:
                if isinstance(existing, dict) and existing.get("action_id") == act_id:
                    disc_holder["disc"] = existing
                    disc_holder["idempotent"] = True
                    return
        mismatches = [m.model_dump() for m in (payload.mismatches or [])]
        err = _qr_validate_loading_discrepancy(order, payload.surat_jalan_id, mismatches)
        if err:
            raise HTTPException(400, err)
        stamp = now_iso()
        disc = _qr_apply_loading_discrepancy(
            order,
            surat_jalan_id=payload.surat_jalan_id,
            mismatches=mismatches,
            note=payload.note,
            actor=user,
            discrepancy_id=str(uuid.uuid4()),
            reported_at=stamp,
            action_id=act_id,
        )
        if not disc.get("id"):
            disc["id"] = str(uuid.uuid4())
            order["loading_discrepancies"][-1]["id"] = disc["id"]
        disc_holder["disc"] = disc
        summary = ", ".join(
            f"{ln.get('name')} SJ {ln.get('qty_sj')} fisik {ln.get('qty_actual')} ({ln.get('classification')})"
            for ln in disc.get("lines") or []
        )
        add_timeline(order, 13, user, note=f"Selisih qty Loading vs SJ: {summary}")
        _sync_workflow_stage(order)

    order = await _with_quantity_lock(order_id, _mutate)
    notifications = []
    if disc_holder.get("disc") and not disc_holder.get("idempotent"):
        try:
            from services.notifications import emit_loading_qty_discrepancy_notifications
            notifications = await emit_loading_qty_discrepancy_notifications(
                db, order=order, discrepancy=disc_holder["disc"],
            )
            if notifications and order.get("loading_discrepancies"):
                # stamp notification ids on last discrepancy (best-effort, non-fatal)
                def _stamp(o):
                    for d in o.get("loading_discrepancies") or []:
                        if isinstance(d, dict) and d.get("id") == disc_holder["disc"].get("id"):
                            d["notification_ids"] = [n.get("id") for n in notifications if n.get("id")]
                            d["notification_count"] = len(d["notification_ids"])
                            break
                try:
                    order = await _with_quantity_lock(order_id, _stamp)
                except Exception:
                    pass
        except Exception:
            logging.getLogger(__name__).exception("CRM discrepancy notification failed")
    await _audit_po(order, "loading.report_discrepancy", user, note=payload.surat_jalan_id)
    return {
        "order": _strip_sj_bytes(order),
        "discrepancy": disc_holder.get("disc"),
        "notifications_created": len(notifications),
        "idempotent": bool(disc_holder.get("idempotent")),
    }

@api_router.delete("/orders/{order_id}/loading-confirm/{confirmation_id}")
async def delete_loading_confirmation(order_id: str, confirmation_id: str, user=Depends(current_user)):
    """Soft-cancel one Loading confirmation when eligible (not fully delivered, no delivered qty)."""
    await _require_perm(user, "loading.delete")

    def _mutate(order):
        _ensure_not_cancelled(order)
        err = _qr_validate_cancel_loading_confirmation(order, confirmation_id)
        if err:
            raise HTTPException(400, err)
        stamp = now_iso()
        try:
            record = _qr_cancel_loading_confirmation(
                order, confirmation_id, actor=user, cancelled_at=stamp,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not record:
            raise HTTPException(404, "Konfirmasi Loading tidak ditemukan")
        add_timeline(
            order, 13, user,
            note=f"Hapus Loading ({record.get('surat_jalan_name') or record.get('surat_jalan_id')}): {record.get('id')}",
        )
        _sync_workflow_stage(order)

    order = await _with_quantity_lock(order_id, _mutate)
    await _audit_po(order, "loading.delete", user, note=confirmation_id)
    return {"order": _strip_sj_bytes(order), "cancelled_id": confirmation_id}


@api_router.post("/orders/{order_id}/approve-sample")
async def approve_sample(order_id: str, payload: ApprovalPayload, user=Depends(current_user)):
    """Approve / revoke approval Sampel Cetak. Butuh permission
    `operator.sample_approve`. Prerequisite: minimal 1 sample_files sudah ada.
    Re-approve setelah revoke tetap mempertahankan history lama."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    if user.get("role") != "admin":
        eff = await user_permissions(user)
        if "operator.sample_approve" not in eff and "schedule.edit" not in eff:
            raise HTTPException(403, "Anda tidak punya permission untuk approval sampel cetak")
    if _requires_printing(order) and not _stage_helper_done(order):
        raise HTTPException(400, "Tahap Operator belum selesai. Selesaikan Persiapan Helper terlebih dahulu.")
    if not order.get("sample_files"):
        raise HTTPException(400, "Belum ada file sampel yang diupload — tidak dapat approve.")
    before = {"sample_approved": bool(order.get("sample_approved")), "sample_approved_by": order.get("sample_approved_by")}
    was_approved = bool(order.get("sample_approved"))
    if payload.approved:
        order["sample_approved"] = True
        order["sample_approved_by"] = user.get("email")
        order["sample_approved_by_name"] = user.get("name")
        order["sample_approved_at"] = now_iso()
        order.setdefault("sample_approval_history", []).append({
            "action": "approve",
            "by_email": user.get("email"),
            "by_name": user.get("name"),
            "at": now_iso(),
            "note": payload.note or "",
        })
        audit_action = "sample.approve"
        _sync_workflow_stage(order)
    else:
        order["sample_approved"] = False
        order["sample_approved_by"] = None
        order["sample_approved_by_name"] = None
        order["sample_approved_at"] = None
        reject_action = "revoke" if was_approved else "reject"
        order.setdefault("sample_approval_history", []).append({
            "action": reject_action,
            "by_email": user.get("email"),
            "by_name": user.get("name"),
            "at": now_iso(),
            "note": payload.note or "",
        })
        audit_action = "sample.approve.revoke" if was_approved else "sample.reject"
        _sync_workflow_stage(order, force=True)
    add_timeline(
        order,
        order["current_stage"],
        user,
        note=(
            "Sampel disetujui" if payload.approved
            else ("Approval sampel dibatalkan" if was_approved else "Sampel ditolak")
        ),
    )
    await _persist_order(order)
    await _audit_po(order, audit_action, user, before=before,
                    after={"sample_approved": bool(order.get("sample_approved")), "sample_approved_by": order.get("sample_approved_by")},
                    note=payload.note or "")
    return {"order": order}


class PackingLineInput(BaseModel):
    line_idx: int
    packed_qty: Optional[int] = None
    qty: Optional[int] = None
    action_id: Optional[str] = None


class PackingInfoPayload(BaseModel):
    qty_packed: Optional[int] = None
    lines: Optional[List[PackingLineInput]] = None
    notes: Optional[str] = None


@api_router.post("/orders/{order_id}/packing-info")
async def save_packing_info(order_id: str, payload: PackingInfoPayload, user=Depends(current_user)):
    """Save Qty Packing per line. qty_packed total = SUM(lines) untuk downstream."""
    await _require_perm(user, "packing.update")
    existing_order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    line_incs = [{"line_idx": x.line_idx, "qty": x.qty, "action_id": x.action_id} for x in (payload.lines or []) if x.qty is not None]
    if existing_order and line_incs:
        from services.physical_stock_engine import precheck_packing_completed
        await precheck_packing_completed(db, existing_order, line_incs)

    claimed_stock = None
    if existing_order and line_incs:
        from services.physical_stock_engine import (
            should_claim_packing_stock,
            claim_packing_stock,
        )
        if await should_claim_packing_stock(db, existing_order):
            claimed_stock = await claim_packing_stock(db, existing_order, line_incs, user)

    before_holder = {}
    incs_holder = []

    def mutate(order: dict):
        pi = order.get("packing_info") or {}
        before_holder["qty_packed"] = pi.get("qty_packed")
        before_holder["lines"] = list(pi.get("lines") or [])
        events = list(pi.get("events") or [])
        increment_rows = []
        if payload.lines:
            increment_rows = [x for x in payload.lines if x.qty is not None]
        if increment_rows:
            for x in increment_rows:
                if x.action_id and any(ev.get("action_id") == x.action_id for ev in events):
                    return
            abs_updates = []
            incs = []
            for x in increment_rows:
                inc = int(x.qty)
                if inc <= 0:
                    continue
                idx = int(x.line_idx)
                current = 0
                for row in (pi.get("lines") or []):
                    if int(row.get("line_idx") or 0) == idx:
                        current = int(row.get("packed_qty") or 0)
                abs_updates.append({"line_idx": idx, "packed_qty": current + inc})
                incs.append({"line_idx": idx, "qty": inc, "action_id": x.action_id})
            if not incs:
                raise HTTPException(400, "Qty Packing per line wajib diisi.")
            err = _qr_validate_packing_lines(abs_updates, order)
            if err:
                raise HTTPException(400, err)
            merged, new_events = _qr_apply_packing_increments(order, incs)
            incs_holder.clear()
            incs_holder.extend(incs)
            stamped = []
            for ev in new_events:
                if ev.get("created_at"):
                    stamped.append(ev)
                    continue
                stamped.append({
                    **ev,
                    "id": str(uuid.uuid4()),
                    "created_by": user.get("email"),
                    "created_by_name": user.get("name"),
                    "created_at": now_iso(),
                })
            total = sum(max(0, int(x.get("packed_qty") or 0)) for x in merged)
            order.setdefault("packing_info", {})
            order["packing_info"]["lines"] = merged
            order["packing_info"]["qty_packed"] = total
            order["packing_info"]["events"] = stamped
            order["packing_info"]["notes"] = payload.notes or ""
            order["packing_info"]["updated_by"] = user.get("email")
            order["packing_info"]["updated_by_name"] = user.get("name")
            order["packing_info"]["updated_at"] = now_iso()
            detail = ", ".join(f"#{u['line_idx']}+{u['qty']}" for u in incs)
            add_timeline(order, 11, user, note=f"Qty Packing +{detail} (total {total} pcs)")
            _qr_apply_confirmed_packing_progress(order)
            return
        if events:
            raise HTTPException(409, "Entry Packing sudah dikonfirmasi. Kirim pekerjaan tambahan sebagai qty baru.")
        updates = None
        if payload.lines:
            updates = [{"line_idx": int(x.line_idx), "packed_qty": int(x.packed_qty or 0)} for x in payload.lines]
            err = _qr_validate_packing_lines(updates, order)
            if err:
                raise HTTPException(400, err)
        elif payload.qty_packed is not None:
            lis = order.get("line_items") or []
            if len(lis) > 1:
                raise HTTPException(
                    400,
                    "Qty Packing harus diisi per line item, bukan total PO.",
                )
            err = _qr_validate_packing_qty(int(payload.qty_packed), order)
            if err:
                raise HTTPException(400, err)
            updates = [{"line_idx": 0, "packed_qty": int(payload.qty_packed)}]
            err = _qr_validate_packing_lines(updates, order)
            if err:
                raise HTTPException(400, err)
        else:
            raise HTTPException(400, "Qty Packing per line wajib diisi.")
        merged = _qr_merge_packing_line_updates(order, updates)
        total = sum(max(0, int(x.get("packed_qty") or 0)) for x in merged)
        seeded = []
        for x in merged:
            q = int(x.get("packed_qty") or 0)
            if q <= 0:
                continue
            seeded.append({
                "id": str(uuid.uuid4()),
                "line_idx": x["line_idx"],
                "qty": q,
                "packed_qty_after": q,
                "created_by": user.get("email"),
                "created_by_name": user.get("name"),
                "created_at": now_iso(),
            })
        order.setdefault("packing_info", {})
        order["packing_info"]["lines"] = merged
        order["packing_info"]["qty_packed"] = total
        order["packing_info"]["events"] = seeded
        order["packing_info"]["notes"] = payload.notes or ""
        order["packing_info"]["updated_by"] = user.get("email")
        order["packing_info"]["updated_by_name"] = user.get("name")
        order["packing_info"]["updated_at"] = now_iso()
        detail = ", ".join(f"#{x['line_idx']}={x['packed_qty']}" for x in merged if int(x.get("packed_qty") or 0) > 0)
        add_timeline(order, 11, user, note=f"Qty Packing: {total} pcs ({detail or 'per line'})")
        _qr_apply_confirmed_packing_progress(order)

    try:
        order = await _with_quantity_lock(order_id, mutate)
    except Exception:
        if claimed_stock:
            from services.physical_stock_engine import release_packing_stock
            await release_packing_stock(db, claimed_stock)
        raise

    if claimed_stock:
        from services.physical_stock_engine import finalize_packing_stock
        await finalize_packing_stock(db, claimed_stock, user)
    elif incs_holder:
        from services.physical_stock_engine import handle_packing_completed
        await handle_packing_completed(db, order, incs_holder, user)
    await _audit_po(order, "packing.update", user,
                    before={"qty_packed": before_holder.get("qty_packed"), "lines": before_holder.get("lines")},
                    after={"qty_packed": (order.get("packing_info") or {}).get("qty_packed"),
                           "lines": (order.get("packing_info") or {}).get("lines")},
                    note=payload.notes or "")
    return {"order": order}


class SuratJalanLineInput(BaseModel):
    line_idx: Optional[int] = None
    qty: int
    accurate_item_id: Optional[str] = None


class SuratJalanPayload(BaseModel):
    delivery_note_number: Optional[str] = None
    notes: Optional[str] = None
    lines: Optional[List[SuratJalanLineInput]] = None


class ImportSuratJalanPayload(BaseModel):
    accurate_shipment_id: str
    number: Optional[str] = None
    notes: Optional[str] = None
    lines: Optional[List[SuratJalanLineInput]] = None


def _stamp_legacy_delivery_note(order: dict, shipment: dict, user: dict):
    """Keep historical delivery_note_* fields in sync with the latest shipment."""
    number = shipment.get("number") or order.get("delivery_note_number")
    order["delivery_note_number"] = number
    order["delivery_note_created_by"] = shipment.get("created_by") or user.get("email")
    order["delivery_note_created_by_name"] = shipment.get("created_by_name") or user.get("name")
    order["delivery_note_created_at"] = shipment.get("created_at") or order.get("delivery_note_created_at") or now_iso()
    if shipment.get("notes"):
        order["delivery_note_notes"] = shipment.get("notes")


def _shipment_line_payload(rows: list) -> list:
    out = []
    for x in rows:
        row = {"qty": int(x.qty)}
        if x.line_idx is not None:
            row["line_idx"] = int(x.line_idx)
        if x.accurate_item_id:
            row["accurate_item_id"] = str(x.accurate_item_id)
        out.append(row)
    return out


@api_router.post("/orders/{order_id}/surat-jalan")
async def create_surat_jalan(order_id: str, payload: SuratJalanPayload, user=Depends(current_user)):
    """Buat Surat Jalan parsial per line. Permission `delivery_note.manage`.

    Tanpa `lines`: alokasi sisa packing (PO pertama) atau perbarui nomor/catatan
    shipment terakhir (kompatibel test legacy).
    """
    await _require_perm(user, "delivery_note.manage")
    before_holder = {}

    def mutate(order: dict):
        if (order.get("current_stage") or 0) < 12:
            raise HTTPException(400, "Belum bisa membuat Surat Jalan — tahap Packing belum selesai.")
        before_holder["delivery_note_number"] = order.get("delivery_note_number")
        before_holder["delivery_note_created_by"] = order.get("delivery_note_created_by")
        from services.sales_deliveries import derive_base_sj_number
        number = (
            payload.delivery_note_number
            or order.get("delivery_note_number")
            or derive_base_sj_number(str(order.get("po_number") or ""))
        )
        existing_all = _qr_shipments_stored(order)
        existing = [sh for sh in existing_all if _qr_shipment_is_active(sh)]
        explicit_lines = _shipment_line_payload(payload.lines) if payload.lines else None
        creating_new = bool(explicit_lines) or not existing
        if creating_new:
            updates = explicit_lines if explicit_lines else _qr_remaining_delivery_note_updates(order)
            err = _qr_validate_shipment_lines(updates, order, source=_QR_SRC_MANUAL)
            if err:
                raise HTTPException(400, err)
            stamp = now_iso()
            try:
                record = _qr_apply_shipment_record(
                    order,
                    number=number,
                    lines=updates,
                    source=_QR_SRC_MANUAL,
                    actor=user,
                    notes=payload.notes,
                    shipment_id=str(uuid.uuid4()),
                    created_at=stamp,
                )
            except ValueError as e:
                raise HTTPException(400, str(e))
            if not record.get("id"):
                record["id"] = str(uuid.uuid4())
                order["shipments"][-1]["id"] = record["id"]
            _stamp_legacy_delivery_note(order, record, user)
            add_timeline(order, 12, user, note=f"Surat Jalan dibuat: {number}")
        else:
            latest = existing[-1]
            freeze_err = _qr_shipment_qty_frozen_reason(order, latest)
            if freeze_err:
                if str(latest.get("number") or "").strip() != str(number or "").strip() or payload.lines:
                    raise HTTPException(400, freeze_err)
                return
            latest["number"] = number
            if payload.notes:
                latest["notes"] = payload.notes
            latest["updated_by"] = user.get("email")
            latest["updated_by_name"] = user.get("name")
            latest["updated_at"] = now_iso()
            order["delivery_note_number"] = number
            order["delivery_note_created_by"] = user.get("email")
            order["delivery_note_created_by_name"] = user.get("name")
            order["delivery_note_created_at"] = order.get("delivery_note_created_at") or now_iso()
            if payload.notes:
                order["delivery_note_notes"] = payload.notes
            add_timeline(order, 12, user, note=f"Surat Jalan dibuat: {number}")

    order = await _with_quantity_lock(order_id, mutate)
    await _audit_po(
        order,
        "delivery_note.create",
        user,
        before={
            "delivery_note_number": before_holder.get("delivery_note_number"),
            "delivery_note_created_by": before_holder.get("delivery_note_created_by"),
        },
        after={
            "delivery_note_number": order.get("delivery_note_number"),
            "delivery_note_created_by": order.get("delivery_note_created_by"),
            "shipments": order.get("shipments"),
        },
        note=payload.notes or "",
    )
    return {"order": order}


@api_router.get("/orders/{order_id}/surat-jalan/accurate-preview")
async def preview_accurate_surat_jalan(order_id: str, user=Depends(current_user)):
    """Preview mock Accurate Pengiriman Pesanan for this PO. Never hits production."""
    await _require_perm(user, "delivery_note.manage")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    client = AccurateClient()
    result = await fetch_delivery_notes(
        client,
        accurate_so_id=order.get("accurate_id"),
        po_number=order.get("po_number"),
    )
    return {
        "order_id": order_id,
        "po_number": order.get("po_number"),
        "accurate_id": order.get("accurate_id"),
        "endpoint_available": ACCURATE_DELIVERY_ENDPOINT_AVAILABLE,
        **result,
    }


@api_router.post("/orders/{order_id}/surat-jalan/import")
async def import_surat_jalan(order_id: str, payload: ImportSuratJalanPayload, user=Depends(current_user)):
    """Import Surat Jalan / Pengiriman from Accurate (mock catalog or posted lines).

    Live Accurate Pengiriman Pesanan is not called — endpoint is not in repo.
    Idempotent on accurate_shipment_id. Does not auto-complete Loading.
    """
    await _require_perm(user, "delivery_note.manage")
    before_holder = {}

    def mutate(order: dict):
        acc_id = (payload.accurate_shipment_id or "").strip()
        if not acc_id:
            raise HTTPException(400, "accurate_shipment_id wajib.")
        before_holder["shipments"] = list(order.get("shipments") or [])
        lines = _shipment_line_payload(payload.lines) if payload.lines else None
        number = payload.number
        notes = payload.notes
        if not lines:
            mock = get_mock_delivery_note(
                accurate_shipment_id=acc_id,
                accurate_so_id=order.get("accurate_id"),
                po_number=order.get("po_number"),
            )
            if not mock:
                raise HTTPException(
                    400,
                    "Shipment Accurate tidak ditemukan di catalog mock. "
                    "Kirim lines + accurate_item_id, atau pastikan PO terhubung ke mock Accurate.",
                )
            lines = [
                {"accurate_item_id": x.get("accurate_item_id"), "qty": x.get("quantity")}
                for x in (mock.get("lines") or [])
            ]
            number = number or mock.get("number")
            notes = notes or mock.get("notes")

        err = _qr_validate_shipment_lines(
            lines, order, source=_QR_SRC_ACCURATE, exclude_shipment_id=acc_id,
        )
        if err:
            raise HTTPException(400, err)
        stamp = now_iso()
        existing = None
        for sh in _qr_shipments_stored(order):
            if str(sh.get("accurate_shipment_id") or "") == acc_id:
                existing = sh
                break
        record = None
        try:
            record = _qr_apply_shipment_record(
                order,
                number=number or (existing or {}).get("number") or acc_id,
                lines=lines,
                source=_QR_SRC_ACCURATE,
                actor=user,
                accurate_shipment_id=acc_id,
                notes=notes,
                shipment_id=(existing or {}).get("id") or str(uuid.uuid4()),
                created_at=stamp,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not record.get("id"):
            record["id"] = (existing or {}).get("id") or str(uuid.uuid4())
            for sh in order.get("shipments") or []:
                if str(sh.get("accurate_shipment_id") or "") == acc_id:
                    sh["id"] = record["id"]
        _stamp_legacy_delivery_note(order, record, user)
        if (order.get("current_stage") or 0) < 12:
            order["current_stage"] = 12
        mismatches = record.get("mismatch_reasons") or []
        note = f"Surat Jalan diimport dari Accurate: {record.get('number')}"
        if mismatches:
            note += " · mismatch packing"
        add_timeline(order, 12, user, note=note)
        before_holder["imported"] = record
        before_holder["idempotent"] = bool(existing)

    order = await _with_quantity_lock(order_id, mutate)
    imported = None
    for sh in order.get("shipments") or []:
        if str(sh.get("accurate_shipment_id") or "") == (payload.accurate_shipment_id or "").strip():
            imported = sh
            break
    await _audit_po(
        order,
        "delivery_note.import",
        user,
        before={"shipments": before_holder.get("shipments")},
        after={"shipment": imported, "source": "accurate"},
        note=payload.notes or payload.accurate_shipment_id,
    )
    return {
        "order": order,
        "shipment": imported,
        "idempotent": bool(before_holder.get("idempotent")),
        "mismatch": bool((imported or {}).get("mismatch")),
        "mismatch_reasons": (imported or {}).get("mismatch_reasons") or [],
        "endpoint_available": ACCURATE_DELIVERY_ENDPOINT_AVAILABLE,
        "endpoint_reason": ACCURATE_DELIVERY_ENDPOINT_REASON if not ACCURATE_DELIVERY_ENDPOINT_AVAILABLE else None,
    }


@api_router.post("/orders/{order_id}/packed")
async def mark_packed(order_id: str, user=Depends(current_user)):
    """Partial packing boleh lanjut ke Surat Jalan."""
    await _require_perm(user, "packing.update")

    def mutate(order: dict):
        reasons = _prereq_reasons_for_packing(order)
        if reasons:
            raise HTTPException(400, {
                "message": "Belum bisa Packing — prerequisite belum lengkap.",
                "prereq_reasons": reasons,
            })
        if not _packing_qty_input(order):
            raise HTTPException(400, {
                "message": "Qty Packing harus diisi terlebih dahulu.",
                "prereq_reasons": ["Qty Packing harus diisi terlebih dahulu."],
            })
        packed_qty = _qr_qty_packed(order)
        avail = _available_for_packing(order)
        if packed_qty > avail:
            raise HTTPException(400, {
                "message": f"Qty Packing ({packed_qty}) melebihi qty tersedia ({avail}).",
                "prereq_reasons": [
                    f"Qty Packing ({packed_qty}) melebihi qty tersedia untuk packing ({avail})."
                ],
            })
        required_total = _packing_required_total(order)
        _qr_apply_confirmed_packing_progress(order)
        if required_total and packed_qty >= required_total:
            note = f"Packing lengkap: {packed_qty} pcs"
        else:
            shortage = max((required_total or 0) - packed_qty, 0)
            note = f"Packing sebagian: {packed_qty}/{required_total} pcs (kurang {shortage})"
        add_timeline(order, 11, user, note=note)

    order = await _with_quantity_lock(order_id, mutate)
    return {"order": order}


class DeliveredPayload(BaseModel):
    """Optional qty for partial delivery. None = deliver all currently packed (legacy)."""
    qty_delivered: Optional[int] = None


@api_router.post("/orders/{order_id}/delivered")
async def mark_delivered(
    order_id: str,
    user=Depends(current_user),
    qty_delivered: Optional[int] = Query(None),
):
    """Konfirmasi pengiriman (cumulative qty_delivered)."""
    await _require_perm(user, "delivery.update")

    def mutate(order: dict):
        packed = _qr_qty_packed(order)
        required = _packing_required_total(order)
        new_delivered = packed if qty_delivered is None else int(qty_delivered)
        current = _qr_qty_delivered(order)
        from services.delivery_loading_gate import (
            new_delivery_block_reason,
            po_is_fully_delivered,
        )
        # Already Terkirim: do not rewrite historical timestamps/actors/qty.
        if po_is_fully_delivered(order) and new_delivered <= current:
            return
        reasons = []
        if not _surat_jalan_created(order):
            reasons.append("Surat Jalan belum dibuat.")
        # Gate must see the requested qty so partial Loading can cap — not reject — a valid increment.
        gate = new_delivery_block_reason(order, requested_qty=new_delivered)
        if gate and gate not in reasons:
            reasons.append(gate)
        if reasons:
            raise HTTPException(400, {
                "message": "Belum bisa Terkirim — prerequisite belum lengkap.",
                "prereq_reasons": reasons,
            })
        if user.get("role") == "driver":
            blocked = _qr_confirm_delivery_block_reason(order)
            if blocked:
                raise HTTPException(400, blocked)
        err = _qr_validate_delivery_qty(new_delivered, order)
        if err:
            raise HTTPException(400, err)
        order["qty_delivered"] = new_delivered
        if required and new_delivered >= required:
            order["delivery_status"] = "Terkirim"
            order["delivered_at"] = now_iso()
            order["delivered_by"] = user.get("email")
            order["delivered_by_name"] = user.get("name")
            order["current_stage"] = 14
            add_timeline(order, 14, user, note=f"Terkirim penuh: {new_delivered}/{required} pcs")
        else:
            order["delivery_status"] = "Sebagian Terkirim"
            order["delivered_at"] = now_iso()
            order["delivered_by"] = user.get("email")
            order["delivered_by_name"] = user.get("name")
            add_timeline(
                order,
                order.get("current_stage") or 13,
                user,
                note=f"Terkirim sebagian: {new_delivered}/{required} pcs",
            )

    order = await _with_quantity_lock(order_id, mutate)
    return {"order": order}


class ConfirmDeliveredPayload(BaseModel):
    action_id: Optional[str] = None
    shipment_id: Optional[str] = None
    recipient_name: Optional[str] = None


@api_router.post("/orders/{order_id}/confirm-delivered")
async def confirm_delivered(order_id: str, payload: Optional[ConfirmDeliveredPayload] = None, user=Depends(current_user)):
    """Explicit Konfirmasi Terkirim after >= 2 bukti kirim + recipient name + e-signature.

    Upload alone / save signature alone does not deliver.
    """
    await _require_perm(user, "delivery.update")
    action_id = (payload.action_id if payload else None) or None
    if action_id:
        action_id = str(action_id).strip() or None
    selected_shipment_id = (payload.shipment_id if payload else None) or None
    if selected_shipment_id:
        selected_shipment_id = str(selected_shipment_id).strip() or None

    existing_order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    act_id = action_id or str(uuid.uuid4())
    claimed_stock = None
    if existing_order:
        from services.physical_stock_engine import prepare_delivery_confirmed_stock
        claimed_stock = await prepare_delivery_confirmed_stock(
            db, existing_order, selected_shipment_id, act_id, user,
        )

    frozen_sig_holder = {"sig": None, "driver_sig": None}
    conf_holder = {}
    reconcile_holder = {"result": None}

    def mutate(order: dict):
        ids = list(order.get("delivery_confirm_action_ids") or [])
        if action_id and action_id in ids:
            return
        selected_sh, resolve_err = _qr_resolve_delivery_shipment(order, selected_shipment_id)
        if resolve_err:
            raise HTTPException(400, resolve_err)
        if selected_sh and _qr_shipment_is_delivered(order, selected_sh):
            return
        incoming_name = payload.recipient_name if payload else None
        if incoming_name is not None:
            from services.delivery_pod_signature import apply_recipient_name_to_active_signature
            name_err = apply_recipient_name_to_active_signature(order, incoming_name)
            if name_err:
                raise HTTPException(400, name_err)
        blocked = _qr_confirm_delivery_block_reason(order, selected_sh)
        if blocked:
            raise HTTPException(400, blocked)
        evidence = _qr_apply_delivery_from_proof_evidence(order, selected_sh)
        if not evidence:
            raise HTTPException(400, "Belum ada qty packing/Surat Jalan yang eligible untuk dikonfirmasi terkirim.")
        now = now_iso()
        current_delivered = _qr_qty_delivered(order)
        increment = int(evidence.get("increment") if evidence.get("increment") is not None else (
            int(evidence["qty_delivered"]) - int(current_delivered or 0)
        ))
        if increment <= 0:
            return
        order["qty_delivered"] = evidence["qty_delivered"]
        order["delivery_status"] = evidence["delivery_status"]
        order["delivered_at"] = now
        order["delivered_by"] = user.get("email")
        order["delivered_by_name"] = user.get("name")
        if selected_sh:
            _qr_mark_shipment_delivered(
                selected_sh, now=now, qty=increment,
            )
        # Reconcile operational SJ qty down to loaded/actual for this confirmation.
        # Packing and Loading quantities are not mutated. Idempotent on retry.
        reconcile_holder["result"] = _qr_reconcile_sj_quantities_to_loaded_delivery(order)
        conf = _append_delivery_confirmation(
            order, user, qty_increment=increment, now=now, action_id=action_id,
            shipment_id=_qr_delivery_batch_identity(selected_sh) if selected_sh else None,
            shipment=selected_sh,
        )
        conf_holder["conf"] = conf
        try:
            from services.delivery_pod_signature import (
                freeze_active_signature,
                freeze_active_driver_signature,
            )
            frozen = freeze_active_signature(order, confirmation_id=(conf or {}).get("id"))
            frozen_driver = freeze_active_driver_signature(order, confirmation_id=(conf or {}).get("id"))
            frozen_sig_holder["sig"] = frozen
            frozen_sig_holder["driver_sig"] = frozen_driver
            if conf and frozen:
                conf["recipient_name"] = frozen.get("recipient_name")
                conf["recipient_signature_id"] = frozen.get("id")
                conf["recipient_signed_at"] = frozen.get("recipient_signed_at")
                conf["sj_file_id"] = frozen.get("sj_file_id")
            if conf and frozen_driver:
                conf["driver_signature_id"] = frozen_driver.get("id")
                conf["driver_signed_at"] = frozen_driver.get("driver_signed_at")
                conf["driver_name"] = frozen_driver.get("driver_name") or conf.get("driver_name")
        except Exception:
            logging.getLogger(__name__).warning("freeze POD signatures failed", exc_info=True)
        _sync_dispatch_stage(order)
        required = evidence["required"] or evidence["qty_delivered"]
        if evidence["full"]:
            add_timeline(
                order, 14, user,
                note=f"Terkirim penuh: {evidence['qty_delivered']}/{required} pcs",
            )
        else:
            add_timeline(
                order, order.get("current_stage") or 13, user,
                note=f"Terkirim sebagian: {evidence['qty_delivered']}/{required} pcs",
            )
            # Keep frozen copies in history; clear active slots for next partial POD
            if frozen_sig_holder["sig"]:
                order.pop("delivery_recipient_signature", None)
            if frozen_sig_holder["driver_sig"]:
                order.pop("delivery_driver_signature", None)
        if action_id:
            ids.append(action_id)
            order["delivery_confirm_action_ids"] = ids[-20:]

    try:
        order = await _with_quantity_lock(order_id, mutate)
    except Exception:
        if claimed_stock:
            from services.physical_stock_engine import release_delivery_confirmed_stock
            await release_delivery_confirmed_stock(db, claimed_stock)
        raise

    # Keep sales_deliveries operational qty in sync with reconciled order.shipments
    # so canonical remain (ordered − Σ sales_deliveries qty) reflects returned shortfall.
    try:
        touched = set((reconcile_holder.get("result") or {}).get("shipment_ids") or [])
        if touched:
            for sh in order.get("shipments") or []:
                if not isinstance(sh, dict):
                    continue
                sid = str(sh.get("sales_delivery_id") or sh.get("id") or "").strip()
                if not sid or sid not in touched:
                    continue
                sh_lines = sh.get("lines") if isinstance(sh.get("lines"), list) else []
                if not sh_lines:
                    continue
                delivery = await db.sales_deliveries.find_one(
                    {"id": sid, "is_cancelled": {"$ne": True}},
                    {"_id": 0, "line_items": 1},
                )
                if not delivery:
                    continue
                by_idx = {
                    int(ln.get("line_idx", 0)): ln
                    for ln in sh_lines
                    if isinstance(ln, dict)
                }
                new_items = []
                total_qty = 0
                for li in delivery.get("line_items") or []:
                    if not isinstance(li, dict):
                        continue
                    item = dict(li)
                    idx = int(item.get("line_idx", 0))
                    src = by_idx.get(idx)
                    if src is not None:
                        q = max(0, int(src.get("qty") if src.get("qty") is not None else src.get("quantity") or 0))
                        item["qty"] = q
                        if "quantity" in item:
                            item["quantity"] = q
                    total_qty += max(0, int(item.get("qty") if item.get("qty") is not None else item.get("quantity") or 0))
                    new_items.append(item)
                await db.sales_deliveries.update_one(
                    {"id": sid, "is_cancelled": {"$ne": True}},
                    {"$set": {
                        "line_items": new_items,
                        "total_qty": total_qty,
                        "updated_at": now_iso(),
                    }},
                )
    except Exception:
        logging.getLogger(__name__).warning("sales_delivery SJ reconcile sync failed", exc_info=True)

    if claimed_stock:
        from services.physical_stock_engine import finalize_delivery_confirmed_stock
        await finalize_delivery_confirmed_stock(db, claimed_stock, user)
    elif conf_holder.get("conf"):
        from services.physical_stock_engine import handle_delivery_confirmed
        await handle_delivery_confirmed(db, order, conf_holder["conf"], user)
    # Generate signed SJ after persist — document rendering only (no qty mutation)
    sig = frozen_sig_holder.get("sig")
    driver_sig = frozen_sig_holder.get("driver_sig")
    if sig:
        try:
            from services.sales_deliveries import generate_signed_sj_pdf_for_order
            packed_before = order.get("packing_info")
            loading_before = order.get("loading_info")
            qty_del_before = order.get("qty_delivered")
            signed_path, err = await generate_signed_sj_pdf_for_order(
                db, order, sig, obj_storage, user=user, driver_sig=driver_sig,
            )
            if err:
                logging.getLogger(__name__).warning("signed SJ generation error: %s", err)
            if signed_path and not err:
                def attach_signed(o: dict):
                    active = o.get("delivery_recipient_signature")
                    if isinstance(active, dict) and str(active.get("id")) == str(sig.get("id")):
                        active["signed_sj_storage_path"] = signed_path
                        active["signed_sj_generated_at"] = now_iso()
                    for s in o.get("delivery_recipient_signatures") or []:
                        if str(s.get("id")) == str(sig.get("id")):
                            s["signed_sj_storage_path"] = signed_path
                            s["signed_sj_generated_at"] = now_iso()
                    if o.get("packing_info") != packed_before:
                        o["packing_info"] = packed_before
                    if o.get("loading_info") != loading_before:
                        o["loading_info"] = loading_before
                    if o.get("qty_delivered") != qty_del_before:
                        o["qty_delivered"] = qty_del_before
                order = await _with_quantity_lock(order_id, attach_signed)
                await _audit_po(
                    order, "delivery.recipient_signature.signed_sj",
                    user, note=f"signed_sj={sig.get('sj_file_id') or ''}",
                )
        except Exception:
            logging.getLogger(__name__).warning("signed SJ generation failed", exc_info=True)
    on_confirmed = getattr(_delivery_route_api, "_hooks", {}).get("on_delivery_confirmed")
    if callable(on_confirmed):
        try:
            await on_confirmed(order)
        except Exception:
            logging.getLogger(__name__).warning("route stop progress update failed", exc_info=True)
    return {"order": order}


class DeliveryRecipientSignaturePayload(BaseModel):
    recipient_name: str
    signature_data_url: Optional[str] = None
    shipment_id: Optional[str] = None
    sj_file_id: Optional[str] = None


@api_router.post("/orders/{order_id}/delivery-recipient-signature")
async def upload_delivery_recipient_signature(
    order_id: str,
    recipient_name: Optional[str] = Form(None),
    shipment_id: Optional[str] = Form(None),
    sj_file_id: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    signature_data_url: Optional[str] = Form(None),
    user=Depends(current_user),
):
    """Save recipient electronic signature. Does NOT mark delivered."""
    await _require_perm(user, "delivery.update")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    if (order.get("delivery_status") or "") == "Terkirim":
        raise HTTPException(400, "Pengiriman sudah terkirim — tanda tangan tidak dapat diganti.")

    from services.delivery_pod_signature import (
        build_signature_record,
        clear_active_signature,
        decode_data_url_png,
        get_active_signature,
        normalize_recipient_name,
        resolve_requested_signature_identity,
        storage_key_for_signature,
        validate_recipient_name,
        validate_signature_bytes,
        now_iso as sig_now,
    )

    name_err = validate_recipient_name(recipient_name)
    if name_err:
        raise HTTPException(400, name_err)
    name = normalize_recipient_name(recipient_name)

    existing = get_active_signature(order)
    if existing and existing.get("frozen"):
        raise HTTPException(400, "Tanda tangan sudah terkunci sebagai bukti pengiriman.")

    body = None
    media = None
    if file is not None:
        body = await file.read()
        media, err = validate_signature_bytes(body, file.content_type)
        if err:
            raise HTTPException(400, err)
    elif signature_data_url:
        body, media_or_err = decode_data_url_png(signature_data_url)
        if body is None:
            raise HTTPException(400, media_or_err or "Tanda tangan tidak valid.")
        media = media_or_err
    else:
        raise HTTPException(400, "File atau signature_data_url wajib.")

    sid, sj_id, identity_err = resolve_requested_signature_identity(
        order,
        shipment_id=shipment_id,
        sj_file_id=sj_file_id,
    )
    if identity_err:
        raise HTTPException(400, identity_err)

    file_id = str(uuid.uuid4())
    ext = "png" if media == "image/png" else ("jpg" if media in ("image/jpeg", "image/jpg") else "png")
    path = storage_key_for_signature(order.get("po_number") or order_id, file_id, ext)
    try:
        result = obj_storage.put_object(path, body, media)
        path = result.get("path") or path
    except Exception:
        logger.exception("Signature storage upload failed")
        raise HTTPException(502, "Gagal menyimpan tanda tangan. Silakan coba lagi.")

    signed_at = sig_now()  # server authoritative
    clear_active_signature(order)
    rec = build_signature_record(
        recipient_name=name,
        signature_file_id=file_id,
        storage_path=path,
        content_type=media,
        size=len(body),
        user=user,
        shipment_id=sid,
        sj_file_id=sj_id,
        signed_at=signed_at,
    )
    order["delivery_recipient_signature"] = rec
    file_doc = {
        "id": file_id,
        "storage_path": path,
        "original_filename": f"recipient-signature.{ext}",
        "content_type": media,
        "size": len(body),
        "kind": "delivery-signature",
        "order_id": order["id"],
        "po_number": order.get("po_number"),
        "shipment_id": sid,
        "sj_file_id": sj_id,
        "uploaded_by": user.get("email"),
        "uploaded_by_role": user.get("role"),
        "uploaded_by_name": user.get("name"),
        "uploaded_at": signed_at,
        "is_deleted": False,
    }
    await db.files.insert_one(file_doc)
    add_timeline(order, order.get("current_stage") or 13, user, note="Tanda tangan penerima disimpan")
    await _persist_order(order)
    await _audit_po(order, "delivery.recipient_signature.saved", user, note=f"sig={file_id}")
    # Never echo storage path / raw bytes to client beyond metadata needed for UI
    safe = {
        "id": rec["id"],
        "recipient_name": rec["recipient_name"],
        "recipient_signed_at": rec["recipient_signed_at"],
        "shipment_id": rec.get("shipment_id"),
        "sj_file_id": rec.get("sj_file_id"),
        "frozen": False,
        "content_type": media,
    }
    return {"order": order, "signature": safe}


@api_router.delete("/orders/{order_id}/delivery-recipient-signature")
async def clear_delivery_recipient_signature(order_id: str, user=Depends(current_user)):
    """Clear mutable recipient signature before Confirm Delivered. Frozen = rejected."""
    await _require_perm(user, "delivery.update")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    from services.delivery_pod_signature import clear_active_signature, get_active_signature
    active = get_active_signature(order)
    if not active and not order.get("delivery_recipient_signature"):
        return {"order": order, "cleared": True}
    frozen = order.get("delivery_recipient_signature") or {}
    if isinstance(frozen, dict) and frozen.get("frozen"):
        raise HTTPException(400, "Tanda tangan sudah terkunci sebagai bukti pengiriman.")
    if (order.get("delivery_status") or "") == "Terkirim":
        raise HTTPException(400, "Pengiriman sudah terkirim — tanda tangan tidak dapat dihapus.")
    clear_active_signature(order)
    await _persist_order(order)
    await _audit_po(order, "delivery.recipient_signature.cleared", user)
    return {"order": order, "cleared": True}


@api_router.post("/orders/{order_id}/delivery-driver-signature")
async def upload_delivery_driver_signature(
    order_id: str,
    shipment_id: Optional[str] = Form(None),
    sj_file_id: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    signature_data_url: Optional[str] = Form(None),
    user=Depends(current_user),
):
    """Save driver/pengirim electronic signature. Does NOT mark delivered."""
    await _require_perm(user, "delivery.update")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    if (order.get("delivery_status") or "") == "Terkirim":
        raise HTTPException(400, "Pengiriman sudah terkirim — tanda tangan tidak dapat diganti.")

    from services.delivery_pod_signature import (
        authoritative_driver_name,
        build_driver_signature_record,
        clear_active_driver_signature,
        decode_data_url_png,
        get_active_driver_signature,
        resolve_requested_signature_identity,
        storage_key_for_driver_signature,
        validate_signature_bytes,
        now_iso as sig_now,
    )

    existing = get_active_driver_signature(order)
    if existing and existing.get("frozen"):
        raise HTTPException(400, "Tanda tangan driver sudah terkunci sebagai bukti pengiriman.")

    body = None
    media = None
    if file is not None:
        body = await file.read()
        media, err = validate_signature_bytes(body, file.content_type, empty_message="Tanda tangan driver kosong.")
        if err:
            raise HTTPException(400, err)
    elif signature_data_url:
        body, media_or_err = decode_data_url_png(signature_data_url)
        if body is None:
            raise HTTPException(400, media_or_err or "Tanda tangan driver tidak valid.")
        media = media_or_err
    else:
        raise HTTPException(400, "File atau signature_data_url wajib.")

    sid, sj_id, identity_err = resolve_requested_signature_identity(
        order,
        shipment_id=shipment_id,
        sj_file_id=sj_file_id,
    )
    if identity_err:
        raise HTTPException(400, identity_err)

    file_id = str(uuid.uuid4())
    ext = "png" if media == "image/png" else ("jpg" if media in ("image/jpeg", "image/jpg") else "png")
    path = storage_key_for_driver_signature(order.get("po_number") or order_id, file_id, ext)
    try:
        result = obj_storage.put_object(path, body, media)
        path = result.get("path") or path
    except Exception:
        logger.exception("Driver signature storage upload failed")
        raise HTTPException(502, "Gagal menyimpan tanda tangan driver. Silakan coba lagi.")

    signed_at = sig_now()
    clear_active_driver_signature(order)
    rec = build_driver_signature_record(
        signature_file_id=file_id,
        storage_path=path,
        content_type=media,
        size=len(body),
        user=user,
        order=order,
        shipment_id=sid,
        sj_file_id=sj_id,
        signed_at=signed_at,
    )
    order["delivery_driver_signature"] = rec
    file_doc = {
        "id": file_id,
        "storage_path": path,
        "original_filename": f"driver-signature.{ext}",
        "content_type": media,
        "size": len(body),
        "kind": "delivery-driver-signature",
        "order_id": order["id"],
        "po_number": order.get("po_number"),
        "shipment_id": sid,
        "sj_file_id": sj_id,
        "uploaded_by": user.get("email"),
        "uploaded_by_role": user.get("role"),
        "uploaded_by_name": user.get("name"),
        "uploaded_at": signed_at,
        "is_deleted": False,
    }
    await db.files.insert_one(file_doc)
    add_timeline(order, order.get("current_stage") or 13, user, note="Tanda tangan driver/pengirim disimpan")
    await _persist_order(order)
    await _audit_po(order, "delivery.driver_signature.saved", user, note=f"sig={file_id}")
    safe = {
        "id": rec["id"],
        "driver_name": rec.get("driver_name") or authoritative_driver_name(order, user),
        "driver_signed_at": rec["driver_signed_at"],
        "shipment_id": rec.get("shipment_id"),
        "sj_file_id": rec.get("sj_file_id"),
        "frozen": False,
        "content_type": media,
    }
    return {"order": order, "signature": safe}


@api_router.delete("/orders/{order_id}/delivery-driver-signature")
async def clear_delivery_driver_signature(order_id: str, user=Depends(current_user)):
    """Clear mutable driver signature before Confirm Delivered. Frozen = rejected."""
    await _require_perm(user, "delivery.update")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    from services.delivery_pod_signature import clear_active_driver_signature, get_active_driver_signature
    active = get_active_driver_signature(order)
    if not active and not order.get("delivery_driver_signature"):
        return {"order": order, "cleared": True}
    frozen = order.get("delivery_driver_signature") or {}
    if isinstance(frozen, dict) and frozen.get("frozen"):
        raise HTTPException(400, "Tanda tangan driver sudah terkunci sebagai bukti pengiriman.")
    if (order.get("delivery_status") or "") == "Terkirim":
        raise HTTPException(400, "Pengiriman sudah terkirim — tanda tangan tidak dapat dihapus.")
    clear_active_driver_signature(order)
    await _persist_order(order)
    await _audit_po(order, "delivery.driver_signature.cleared", user)
    return {"order": order, "cleared": True}


class DeliveryReschedulePayload(BaseModel):
    reason: str
    new_date: Optional[str] = None
    shipment_id: Optional[str] = None
    action_id: Optional[str] = None


class DeliveryRescheduleSetDatePayload(BaseModel):
    new_date: str
    reason: Optional[str] = None
    action_id: Optional[str] = None


async def _require_any_perm(user: dict, *perms: str):
    reject_sales_pwa_bypass(user)
    if user.get("role") == "admin":
        return
    eff = await user_permissions(user)
    if not any(p in eff for p in perms):
        raise HTTPException(403, f"Permission {' / '.join(perms)} diperlukan")


@api_router.post("/orders/{order_id}/delivery-reschedule")
async def create_delivery_reschedule(order_id: str, payload: DeliveryReschedulePayload, user=Depends(current_user)):
    """Request or confirm a delivery reschedule. Does not mark Terkirim."""
    await _require_any_perm(user, "delivery.reschedule", "delivery.reschedule.set_date")
    eff = await user_permissions(user)
    can_set_date = user.get("role") == "admin" or "delivery.reschedule.set_date" in eff

    def mutate(order: dict):
        deadline_before = order.get("deadline")
        proofs_before = list(order.get("delivery_proofs") or [])
        packed_before = order.get("packing_info")
        loading_before = order.get("loading_info")
        status_before = order.get("delivery_status")
        try:
            rec, mutated = _apply_delivery_reschedule(
                order,
                reason=payload.reason,
                new_date=payload.new_date,
                can_set_date=can_set_date,
                user=user,
                action_id=payload.action_id,
                shipment_id=payload.shipment_id,
            )
        except RescheduleError as e:
            raise HTTPException(400, str(e))
        if order.get("deadline") != deadline_before:
            raise HTTPException(500, "Deadline asli tidak boleh berubah")
        if not mutated:
            return rec, False
        if list(order.get("delivery_proofs") or []) != proofs_before:
            raise HTTPException(500, "Bukti kirim tidak boleh berubah")
        if order.get("packing_info") != packed_before or order.get("loading_info") != loading_before:
            raise HTTPException(500, "Qty packing/loading tidak boleh berubah")
        if order.get("delivery_status") != status_before:
            raise HTTPException(500, "Status pengiriman tidak boleh berubah")
        return rec, True

    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    rec, mutated = mutate(order)
    if mutated and rec.get("status") == "scheduled" and rec.get("new_date"):
        from services.sales_deliveries import sync_sj_numbers_after_reschedule
        await sync_sj_numbers_after_reschedule(
            db,
            order,
            previous_date=rec.get("previous_date"),
            new_date=rec.get("new_date"),
            shipment_id=payload.shipment_id,
            user=user,
        )
    persisted = await _persist_order(order)
    attach_schedule_snapshot(persisted)
    on_rs = getattr(_delivery_route_api, "_hooks", {}).get("on_rescheduled")
    if callable(on_rs):
        try:
            await on_rs(persisted, payload.shipment_id)
        except Exception:
            logging.getLogger(__name__).warning("route reschedule hook failed", exc_info=True)
    return {"order": persisted, "reschedule": rec}


@api_router.get("/delivery/schedule")
async def list_delivery_schedule(user=Depends(current_user)):
    await _require_any_perm(user, "delivery.schedule.view", "schedule.view", "nav.delivery_schedule")
    docs = await db.orders.find(
        {"cancelled": {"$ne": True}},
        SCHEDULE_PROJECTION,
    ).sort("deadline", 1).to_list(None)
    rows = []
    for o in docs:
        rows.extend(_delivery_schedule_rows(o))
    # Enrich optimization status (pure read; no writes).
    try:
        from services.independent_route.delivery_bridge import optimization_status_map
        ids = [str(r.get("id") or "") for r in rows if r.get("id")]
        status_map = await optimization_status_map(db, ids)
        for r in rows:
            info = status_map.get(str(r.get("id") or "")) or {}
            r["optimization_status"] = info.get("optimization_status") or "belum_dioptimasi"
            r["optimization_status_label"] = info.get("optimization_status_label") or "Belum Dioptimasi"
            r["route_id"] = info.get("route_id")
            if not r.get("driver_user_id") and info.get("driver_user_id"):
                r["driver_user_id"] = info.get("driver_user_id")
    except Exception:
        for r in rows:
            r.setdefault("optimization_status", "belum_dioptimasi")
            r.setdefault("optimization_status_label", "Belum Dioptimasi")
            r.setdefault("route_id", None)
    return {"rows": rows, "generated_at": now_iso(), "total": len(rows)}


@api_router.get("/delivery/reschedules")
async def list_delivery_reschedules(user=Depends(current_user)):
    await _require_any_perm(
        user,
        "delivery.reschedule",
        "delivery.reschedule.set_date",
        "nav.delivery_reschedule",
        "schedule.edit",
    )
    docs = await db.orders.find(
        {"cancelled": {"$ne": True}, "delivery_reschedules.0": {"$exists": True}},
        RESCHEDULE_PROJECTION,
    ).to_list(1000)
    rows = []
    for o in docs:
        rows.extend(_delivery_reschedule_rows(o))
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return {"rows": rows, "generated_at": now_iso(), "total": len(rows)}


@api_router.patch("/delivery/reschedules/{reschedule_id}")
async def set_delivery_reschedule_date(
    reschedule_id: str,
    payload: DeliveryRescheduleSetDatePayload,
    user=Depends(current_user),
):
    await _require_perm(user, "delivery.reschedule.set_date")
    rid = (reschedule_id or "").strip()
    if not rid:
        raise HTTPException(400, "reschedule_id wajib")
    order = await db.orders.find_one(
        {"delivery_reschedules.reschedule_id": rid, "cancelled": {"$ne": True}},
        {"_id": 0},
    )
    if not order:
        raise HTTPException(404, "Permintaan penjadwalan ulang tidak ditemukan")
    deadline_before = order.get("deadline")
    try:
        rec, mutated = _apply_delivery_set_date(
            order,
            rid,
            new_date=payload.new_date,
            user=user,
            reason=payload.reason,
            action_id=payload.action_id,
        )
    except RescheduleError as e:
        raise HTTPException(400, str(e))
    if order.get("deadline") != deadline_before:
        raise HTTPException(500, "Deadline asli tidak boleh berubah")
    if mutated and rec.get("status") == "scheduled" and rec.get("new_date"):
        from services.sales_deliveries import sync_sj_numbers_after_reschedule
        await sync_sj_numbers_after_reschedule(
            db,
            order,
            previous_date=rec.get("previous_date"),
            new_date=rec.get("new_date"),
            shipment_id=rec.get("shipment_id"),
            user=user,
        )
    persisted = await _persist_order(order)
    attach_schedule_snapshot(persisted)
    return {"order": persisted, "reschedule": rec, "mutated": mutated}


# ---- Batch 1: Delivery Deadline Settings + Manual Override ----
class DeliveryDeadlineSettingsPayload(BaseModel):
    lead_time_days: int = Field(default=3)


class DeliveryDeadlineManualPayload(BaseModel):
    # ISO datetime string dengan timezone (mis. "2026-08-25T15:00:00+07:00").
    # Backend menormalkan ke UTC ISO saat persist.
    deadline_at: str
    reason: Optional[str] = None


@api_router.get("/delivery-deadline/settings")
async def get_delivery_deadline_settings(user=Depends(current_user)):
    # View available untuk siapa saja dengan permission delivery.update / dashboard view.
    settings = await _get_delivery_deadline_settings()
    return {
        "settings": settings,
        "allowed_lead_time_days": _DELIVERY_DEADLINE_ALLOWED_DAYS,
        "unit": "calendar_days",
    }


@api_router.put("/delivery-deadline/settings")
async def put_delivery_deadline_settings(payload: DeliveryDeadlineSettingsPayload, user=Depends(current_user)):
    await _require_perm(user, "delivery.deadline.settings")
    if payload.lead_time_days not in _DELIVERY_DEADLINE_ALLOWED_DAYS:
        raise HTTPException(400, f"lead_time_days harus salah satu dari {_DELIVERY_DEADLINE_ALLOWED_DAYS}")
    doc = {
        "key": "delivery_deadline",
        "lead_time_days": int(payload.lead_time_days),
        "updated_by": user.get("email"),
        "updated_by_name": user.get("name"),
        "updated_at": now_iso(),
    }
    await db.app_settings.update_one({"key": "delivery_deadline"}, {"$set": doc}, upsert=True)
    await _write_audit(action="delivery.deadline.settings.update", actor=user,
                       target_email="-", after={"lead_time_days": doc["lead_time_days"]})
    return {"settings": await _get_delivery_deadline_settings()}


class WorkPanelSettingsPayload(BaseModel):
    divisions: Optional[List[Any]] = None


@api_router.get("/work-panel/settings")
async def get_work_panel_settings(user=Depends(current_user)):
    await _require_perm(user, "po.view")
    doc = await db.app_settings.find_one({"key": WORK_PANEL_SETTINGS_KEY}, {"_id": 0})
    return {"settings": work_panel_cfg.merge_settings(doc)}


@api_router.put("/work-panel/settings")
async def put_work_panel_settings(payload: WorkPanelSettingsPayload, user=Depends(current_user)):
    await _require_perm(user, "settings.work_panel.manage")
    merged = work_panel_cfg.sanitize_put(payload.model_dump())
    doc = {
        "key": WORK_PANEL_SETTINGS_KEY,
        "divisions": merged["divisions"],
        "updated_by": user.get("email"),
        "updated_by_name": user.get("name"),
        "updated_at": now_iso(),
    }
    await db.app_settings.update_one({"key": WORK_PANEL_SETTINGS_KEY}, {"$set": doc}, upsert=True)
    await _write_audit(
        action="settings.work_panel.update",
        actor=user,
        target_email="-",
        after={"divisions": [{"id": d["id"], "active": d["active"], "input_label": d["input_label"]} for d in merged["divisions"]]},
    )
    return {"settings": work_panel_cfg.merge_settings(doc)}


class AccurateCreatorMappingsPayload(BaseModel):
    mappings: Optional[List[Any]] = None


async def _can_manage_accurate_creators(user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    eff = await user_permissions(user)
    return bool({"users.manage", "accurate.import", "permissions.manage"} & eff)


@api_router.get("/settings/accurate-creators")
async def get_accurate_creator_mappings(user=Depends(current_user)):
    if user.get("role") != "admin":
        eff = await user_permissions(user)
        if not ({"users.view", "users.manage", "accurate.import", "permissions.manage"} & eff):
            raise HTTPException(403, "Permission untuk melihat mapping Pembuat PO Accurate diperlukan")
    doc = await db.app_settings.find_one({"key": accurate_creator_cfg.SETTINGS_KEY}, {"_id": 0}) or {}
    sanitized = accurate_creator_cfg.sanitize_settings(doc)
    return {"settings": sanitized, "can_edit": await _can_manage_accurate_creators(user)}


class AccurateCreatorScanPayload(BaseModel):
    limit: Optional[int] = 100


class AccurateCreatorVerifyPayload(BaseModel):
    accurate_created_by_id: str
    display_name: str


async def _persist_accurate_creator_mappings(sanitized: dict, user: dict) -> dict:
    doc = {
        "key": accurate_creator_cfg.SETTINGS_KEY,
        "mappings": sanitized["mappings"],
        "updated_at": sanitized["updated_at"],
        "updated_by": user.get("email"),
        "updated_by_name": user.get("name"),
    }
    await db.app_settings.update_one({"key": accurate_creator_cfg.SETTINGS_KEY}, {"$set": doc}, upsert=True)
    accurate_creator_cfg.clear_creator_map_cache()
    await _write_audit(
        action="settings.accurate_creator.update",
        actor=user,
        target_email="-",
        after={"count": len(sanitized["mappings"])},
    )
    return sanitized


@api_router.put("/settings/accurate-creators")
async def put_accurate_creator_mappings(payload: AccurateCreatorMappingsPayload, user=Depends(current_user)):
    if not await _can_manage_accurate_creators(user):
        raise HTTPException(403, "Permission untuk mengubah mapping Pembuat PO Accurate diperlukan")
    sanitized = accurate_creator_cfg.sanitize_settings(payload.model_dump(), updated_at=now_iso())
    await _persist_accurate_creator_mappings(sanitized, user)
    return {"settings": sanitized}


@api_router.post("/settings/accurate-creators/scan")
async def scan_accurate_creators(payload: AccurateCreatorScanPayload, user=Depends(current_user)):
    if not await _can_manage_accurate_creators(user):
        raise HTTPException(403, "Permission untuk scan Pembuat PO Accurate diperlukan")
    limit = accurate_creator_cfg.normalize_scan_limit(payload.limit)
    try:
        result = await _accurate_client.scan_sales_order_creators(limit)
    except AccurateRequestError as e:
        raise _accurate_http_error(e)
    result["wrote_mappings"] = False
    return result


@api_router.post("/settings/accurate-creators/verify")
async def verify_accurate_creator(payload: AccurateCreatorVerifyPayload, user=Depends(current_user)):
    if not await _can_manage_accurate_creators(user):
        raise HTTPException(403, "Permission untuk mengubah mapping Pembuat PO Accurate diperlukan")
    existing = await db.app_settings.find_one({"key": accurate_creator_cfg.SETTINGS_KEY}, {"_id": 0}) or {}
    try:
        sanitized = accurate_creator_cfg.upsert_verified_mapping(
            existing,
            payload.accurate_created_by_id,
            payload.display_name,
            updated_at=now_iso(),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    await _persist_accurate_creator_mappings(sanitized, user)
    return {"settings": sanitized}


@api_router.post("/orders/{order_id}/delivery-deadline")
async def set_delivery_deadline_manual(order_id: str, payload: DeliveryDeadlineManualPayload, user=Depends(current_user)):
    """Legacy endpoint — sets authoritative Tanggal Pengiriman (manual).

    Prefer PATCH /orders/{id}/info with tanggal_pengiriman. Kept for API
    compatibility; does not expose Deadline terminology in new UI.
    """
    await _require_perm(user, "delivery.deadline.edit")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    from services.tanggal_pengiriman import apply_manual_tanggal_pengiriman, parse_calendar_date, wib_calendar_date

    raw = (payload.deadline_at or "").strip()
    if not raw:
        raise HTTPException(400, "Tanggal Pengiriman wajib diisi")
    # Accept date-only or datetime; store as WIB calendar date (no UTC day shift).
    key = parse_calendar_date(raw) or wib_calendar_date(raw)
    if not key:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone(timedelta(hours=7)))
            key = wib_calendar_date(dt.isoformat())
        except Exception:
            key = None
    if not key:
        raise HTTPException(400, "Format datetime tidak valid — gunakan ISO 8601 (YYYY-MM-DD atau YYYY-MM-DDTHH:MM)")
    before = order.get("delivery_deadline_manual")
    apply_manual_tanggal_pengiriman(
        order,
        key,
        user=user,
        reason=(payload.reason or "").strip() or "",
    )
    await _persist_order(order)
    await _audit_po(order, "delivery.deadline.manual.set", user,
                    before={"deadline_manual": before},
                    after={"deadline_manual": order["delivery_deadline_manual"], "tanggal_pengiriman": key},
                    note=payload.reason or "")
    dd_settings = await _get_delivery_deadline_settings()
    _attach_deadline_snapshot(order, dd_settings["lead_time_days"])
    attach_schedule_snapshot(order, dd_settings["lead_time_days"])
    return {"order": order}


@api_router.post("/orders/{order_id}/delivery-deadline/reset")
async def reset_delivery_deadline_manual(order_id: str, user=Depends(current_user)):
    await _require_perm(user, "delivery.deadline.edit")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    from services.tanggal_pengiriman import clear_manual_tanggal_pengiriman, manual_tanggal_pengiriman

    if not manual_tanggal_pengiriman(order):
        raise HTTPException(400, "Tidak ada Tanggal Pengiriman manual yang perlu direset")
    before = order.get("delivery_deadline_manual")
    clear_manual_tanggal_pengiriman(order, user=user)
    await _persist_order(order)
    await _audit_po(order, "delivery.deadline.manual.reset", user,
                    before={"deadline_manual": before}, after={"deadline_manual": None})
    dd_settings = await _get_delivery_deadline_settings()
    _attach_deadline_snapshot(order, dd_settings["lead_time_days"])
    attach_schedule_snapshot(order, dd_settings["lead_time_days"])
    return {"order": order}


# ---- Batch 1: Helper Preparation per LINE ITEM ----


# ---------- Dashboard aggregation (Head of Production) ----------
def _mongo_keep_if_present(expr):
    """Omit missing keys so Python `.get(key, default)` stays equivalent to find()."""
    return {
        "$cond": [
            {"$eq": [{"$type": expr}, "missing"]},
            "$$REMOVE",
            expr,
        ]
    }


def _orders_dashboard_summary_line_items_expr() -> dict:
    """Keep array order / index identity. Never merge by name."""
    return {
        "$map": {
            "input": {"$ifNull": ["$line_items", []]},
            "as": "li",
            "in": {
                "name": _mongo_keep_if_present("$$li.name"),
                "production_process": _mongo_keep_if_present("$$li.production_process"),
                "quantity": _mongo_keep_if_present("$$li.quantity"),
            },
        }
    }


def _orders_dashboard_summary_operator_splits_expr() -> dict:
    return {
        "$map": {
            "input": {"$ifNull": ["$operator_splits", []]},
            "as": "s",
            "in": {
                "operator_email": _mongo_keep_if_present("$$s.operator_email"),
                "operator_name": _mongo_keep_if_present("$$s.operator_name"),
                "quantity": _mongo_keep_if_present("$$s.quantity"),
                "qty_completed": _mongo_keep_if_present("$$s.qty_completed"),
                "stage": _mongo_keep_if_present("$$s.stage"),
                "work_date": _mongo_keep_if_present("$$s.work_date"),
            },
        }
    }


def _orders_dashboard_summary_shipments_expr() -> dict:
    return {
        "$map": {
            "input": {"$ifNull": ["$shipments", []]},
            "as": "sh",
            "in": {
                "created_at": _mongo_keep_if_present("$$sh.created_at"),
                "number": _mongo_keep_if_present("$$sh.number"),
                "lines": {
                    "$map": {
                        "input": {"$ifNull": ["$$sh.lines", []]},
                        "as": "ln",
                        "in": {
                            "line_idx": _mongo_keep_if_present("$$ln.line_idx"),
                            "qty": _mongo_keep_if_present("$$ln.qty"),
                            "quantity": _mongo_keep_if_present("$$ln.quantity"),
                        },
                    }
                },
            },
        }
    }


def _orders_dashboard_summary_delivery_proofs_expr() -> dict:
    return {
        "$map": {
            "input": {"$ifNull": ["$delivery_proofs", []]},
            "as": "p",
            "in": {
                "uploaded_at": _mongo_keep_if_present("$$p.uploaded_at"),
            },
        }
    }


def _orders_dashboard_summary_loading_info_expr() -> dict:
    """Quantity lines plus event timestamps for shipments_today."""
    shaped = {
        "lines": {
            "$map": {
                "input": {"$ifNull": ["$loading_info.lines", []]},
                "as": "ln",
                "in": {
                    "line_idx": _mongo_keep_if_present("$$ln.line_idx"),
                    "loaded_qty": _mongo_keep_if_present("$$ln.loaded_qty"),
                },
            }
        },
        "events": {
            "$map": {
                "input": {"$ifNull": ["$loading_info.events", []]},
                "as": "ev",
                "in": {
                    "line_idx": _mongo_keep_if_present("$$ev.line_idx"),
                    "qty": _mongo_keep_if_present("$$ev.qty"),
                    "at": _mongo_keep_if_present("$$ev.at"),
                    "created_at": _mongo_keep_if_present("$$ev.created_at"),
                },
            }
        },
    }
    return {
        "$cond": [
            {"$eq": [{"$type": "$loading_info"}, "object"]},
            shaped,
            "$$REMOVE",
        ]
    }


def _orders_dashboard_summary_packing_info_expr() -> dict:
    shaped = _orders_dashboard_packing_info_expr()
    return {
        "$cond": [
            {"$eq": [{"$type": "$packing_info"}, "object"]},
            shaped,
            "$$REMOVE",
        ]
    }


def _orders_dashboard_summary_projection() -> dict:
    """Fields read by HoP GET /dashboard/summary. Dedicated; not Phase 2B or Action Center."""
    return {
        "_id": 0,
        "id": 1,
        "po_number": 1,
        "outlet": 1,
        "customer": 1,
        "current_stage": 1,
        "priority": 1,
        "deadline": 1,
        "status_label": 1,
        "work_date": 1,
        "total_quantity": 1,
        "quantity": 1,
        "cancelled": 1,
        "design_status": 1,
        "design_bypassed": 1,
        "design_approved_at": 1,
        "design_bypassed_at": 1,
        "payment_status": 1,
        "payment_bypassed": 1,
        "payment_approved_at": 1,
        "payment_bypassed_at": 1,
        "delivery_status": 1,
        "qty_delivered": 1,
        "delivered_at": 1,
        "delivery_note_number": 1,
        "delivery_note_created_at": 1,
        "loading_completed_at": 1,
        "delivery_deadline_manual": 1,
        "scheduled_delivery_date": 1,
        "buffer_stock": 1,
        "qty_packed": 1,
        "customer_approval.status": 1,
        "loading_checklist.items_loaded": 1,
        "loading_checklist.seal_ok": 1,
        "design_files": _orders_dashboard_file_presence_expr("design_files"),
        "payment_files": _orders_dashboard_file_presence_expr("payment_files"),
        "line_items": _orders_dashboard_summary_line_items_expr(),
        "operator_splits": _orders_dashboard_summary_operator_splits_expr(),
        "shipments": _orders_dashboard_summary_shipments_expr(),
        "packing_info": _orders_dashboard_summary_packing_info_expr(),
        "loading_info": _orders_dashboard_summary_loading_info_expr(),
        "loading_confirmations": _orders_dashboard_loading_confirmations_expr(),
        "delivery_proofs": _orders_dashboard_summary_delivery_proofs_expr(),
        "delivery_reschedules": _orders_dashboard_reschedules_expr(),
    }


def _dashboard_summary_file_presence(files) -> list:
    return [{}] if isinstance(files, list) and files else []


def _shape_order_for_dashboard_summary_compute(order: dict) -> dict:
    """In-memory mirror of `_orders_dashboard_summary_projection` (tests/parity)."""
    src = order or {}
    shaped = {
        "id": src.get("id"),
        "po_number": src.get("po_number"),
        "outlet": src.get("outlet"),
        "customer": src.get("customer"),
        "current_stage": src.get("current_stage"),
        "priority": src.get("priority"),
        "deadline": src.get("deadline"),
        "status_label": src.get("status_label"),
        "work_date": src.get("work_date"),
        "total_quantity": src.get("total_quantity"),
        "quantity": src.get("quantity"),
        "cancelled": src.get("cancelled"),
        "design_status": src.get("design_status"),
        "design_bypassed": src.get("design_bypassed"),
        "design_approved_at": src.get("design_approved_at"),
        "design_bypassed_at": src.get("design_bypassed_at"),
        "payment_status": src.get("payment_status"),
        "payment_bypassed": src.get("payment_bypassed"),
        "payment_approved_at": src.get("payment_approved_at"),
        "payment_bypassed_at": src.get("payment_bypassed_at"),
        "delivery_status": src.get("delivery_status"),
        "qty_delivered": src.get("qty_delivered"),
        "delivered_at": src.get("delivered_at"),
        "delivery_note_number": src.get("delivery_note_number"),
        "delivery_note_created_at": src.get("delivery_note_created_at"),
        "loading_completed_at": src.get("loading_completed_at"),
        "delivery_deadline_manual": src.get("delivery_deadline_manual"),
        "scheduled_delivery_date": src.get("scheduled_delivery_date"),
        "buffer_stock": src.get("buffer_stock"),
        "qty_packed": src.get("qty_packed"),
        "design_files": _dashboard_summary_file_presence(src.get("design_files")),
        "payment_files": _dashboard_summary_file_presence(src.get("payment_files")),
        "line_items": [
            {
                **({"name": li.get("name")} if "name" in li else {}),
                **({"production_process": li.get("production_process")} if "production_process" in li else {}),
                **({"quantity": li.get("quantity")} if "quantity" in li else {}),
            }
            for li in (src.get("line_items") or [])
            if isinstance(li, dict)
        ],
        "operator_splits": [
            {
                **({"operator_email": s.get("operator_email")} if "operator_email" in s else {}),
                **({"operator_name": s.get("operator_name")} if "operator_name" in s else {}),
                **({"quantity": s.get("quantity")} if "quantity" in s else {}),
                **({"qty_completed": s.get("qty_completed")} if "qty_completed" in s else {}),
                **({"stage": s.get("stage")} if "stage" in s else {}),
                **({"work_date": s.get("work_date")} if "work_date" in s else {}),
            }
            for s in (src.get("operator_splits") or [])
            if isinstance(s, dict)
        ],
        "shipments": [
            {
                **({"created_at": sh.get("created_at")} if "created_at" in sh else {}),
                **({"number": sh.get("number")} if "number" in sh else {}),
                "lines": [
                    {
                        **({"line_idx": ln.get("line_idx")} if "line_idx" in ln else {}),
                        **({"qty": ln.get("qty")} if "qty" in ln else {}),
                        **({"quantity": ln.get("quantity")} if "quantity" in ln else {}),
                    }
                    for ln in (sh.get("lines") or [])
                    if isinstance(ln, dict)
                ],
            }
            for sh in (src.get("shipments") or [])
            if isinstance(sh, dict)
        ],
        "loading_confirmations": [
            {
                "cancelled": c.get("cancelled"),
                "deleted": c.get("deleted"),
                "surat_jalan_id": c.get("surat_jalan_id"),
                "lines": [
                    {
                        "line_idx": ln.get("line_idx"),
                        "qty": ln.get("qty"),
                        "quantity": ln.get("quantity"),
                    }
                    for ln in (c.get("lines") or [])
                    if isinstance(ln, dict)
                ],
            }
            for c in (src.get("loading_confirmations") or [])
            if isinstance(c, dict)
        ],
        "delivery_proofs": [
            {**({"uploaded_at": p.get("uploaded_at")} if "uploaded_at" in p else {})}
            for p in (src.get("delivery_proofs") or [])
            if isinstance(p, dict)
        ],
        "delivery_reschedules": [
            {"status": r.get("status"), "new_date": r.get("new_date")}
            for r in (src.get("delivery_reschedules") or [])
            if isinstance(r, dict)
        ],
    }
    pi = src.get("packing_info")
    if isinstance(pi, dict):
        shaped["packing_info"] = {
            "qty_packed": pi.get("qty_packed"),
            "lines": [
                {
                    "line_id": ln.get("line_id"),
                    "line_idx": ln.get("line_idx"),
                    "packed_qty": ln.get("packed_qty"),
                }
                for ln in (pi.get("lines") or [])
                if isinstance(ln, dict)
            ],
        }
    li = src.get("loading_info")
    if isinstance(li, dict):
        shaped["loading_info"] = {
            "lines": [
                {"line_idx": ln.get("line_idx"), "loaded_qty": ln.get("loaded_qty")}
                for ln in (li.get("lines") or [])
                if isinstance(ln, dict)
            ],
            "events": [
                {
                    "line_idx": ev.get("line_idx"),
                    "qty": ev.get("qty"),
                    "at": ev.get("at"),
                    "created_at": ev.get("created_at"),
                }
                for ev in (li.get("events") or [])
                if isinstance(ev, dict)
            ],
        }
    snap = src.get("customer_approval")
    if isinstance(snap, dict):
        shaped["customer_approval"] = {"status": snap.get("status")}
    lc = src.get("loading_checklist")
    if isinstance(lc, dict):
        shaped["loading_checklist"] = {
            "items_loaded": lc.get("items_loaded"),
            "seal_ok": lc.get("seal_ok"),
        }
    return shaped


@api_router.get("/dashboard/summary")
async def dashboard_summary(user=Depends(require_perm("dashboard.production.view"))):
    raw_orders = await db.orders.aggregate([
        {"$match": {"cancelled": {"$ne": True}}},
        {"$project": _orders_dashboard_summary_projection()},
    ]).to_list(None)
    # Cancelled PO tidak boleh ikut card apa pun di Dashboard Produksi.
    orders = [o for o in raw_orders if not o.get("cancelled")]
    # Attach HOLD snapshot (canonical predicate) — dipakai untuk card HOLD.
    # Batch 1: attach delivery deadline snapshot (source of truth H-1/H-6/Overdue).
    _dd_settings = await _get_delivery_deadline_settings()
    for o in orders:
        _apply_hold_snapshot(o)
        _attach_deadline_snapshot(o, _dd_settings["lead_time_days"])

    now = datetime.now(timezone.utc)
    today = wib_today(now)

    active_orders, completed_orders = partition_orders_by_delivery(
        orders, _qr_is_fully_delivered
    )
    urgent_orders = [o for o in active_orders if o.get("priority") == "urgent"]

    total_active = len(active_orders)
    total_urgent = len(urgent_orders)
    total_completed = len(completed_orders)

    due_today = due_today_orders(active_orders, today)
    overdue = []
    deadline_h1, deadline_h6 = [], []
    waiting_design, waiting_payment = [], []
    hold_list = []
    # SEMANTIC: satu PO boleh muncul di beberapa list (design + payment + hold,
    # dsb.) — predicate independen, TIDAK bergantung pada current_stage.
    for o in active_orders:
        # Batch 1: overdue/h1/h6 memakai `delivery_deadline_status` sebagai
        # SOURCE OF TRUTH (sama predicate untuk Dashboard/Schedule/PODetail).
        st = o.get("delivery_deadline_status")
        if st == "overdue":
            overdue.append(o)
        elif st == "h6":
            deadline_h6.append(o)
        elif st == "h1":
            deadline_h1.append(o)
        if o.get("design_status") != "Approved" and not o.get("design_bypassed"):
            waiting_design.append(o)
        if o.get("payment_status") != "Approved" and not o.get("payment_bypassed"):
            waiting_payment.append(o)
        if o.get("hold"):
            hold_list.append(o)

    # Per-stage breakdown (count of active orders at each stage, exclude cancelled)
    stage_breakdown = []
    for idx, label in enumerate(STAGES):
        cnt = sum(1 for o in active_orders if o.get("current_stage", 0) == idx)
        stage_breakdown.append({"stage": idx, "label": label, "count": cnt})

    # Operator load (aggregate qty_scheduled / completed / remaining across active orders)
    op_map = {}
    for o in active_orders:
        for s in o.get("operator_splits", []):
            email = s.get("operator_email")
            if not email: continue
            rec = op_map.setdefault(email, {
                "email": email,
                "name": s.get("operator_name", email),
                "qty_scheduled": 0,
                "qty_completed": 0,
                "active_po_count": 0,
                "pos": [],
            })
            rec["qty_scheduled"] += int(s.get("quantity", 0))
            rec["qty_completed"] += int(s.get("qty_completed", 0))
            rec["active_po_count"] += 1
            rec["pos"].append({
                "po_id": o.get("id"),
                "po_number": o.get("po_number"),
                "outlet": o.get("outlet"),
                "quantity": int(s.get("quantity", 0)),
                "qty_completed": int(s.get("qty_completed", 0)),
                "stage": s.get("stage"),
                "stage_label": STAGES[min(s.get("stage", 8), len(STAGES) - 1)],
                "deadline": o.get("deadline"),
                "priority": o.get("priority"),
            })
    operator_load = []
    for rec in op_map.values():
        rec["qty_remaining"] = max(0, rec["qty_scheduled"] - rec["qty_completed"])
        operator_load.append(rec)
    operator_load.sort(key=lambda r: -r["qty_remaining"])

    # Slim list for tables
    def slim(o):
        return {
            "id": o.get("id"),
            "po_number": o.get("po_number"),
            "outlet": o.get("outlet"),
            "customer": o.get("customer"),
            "deadline": o.get("deadline"),
            "priority": o.get("priority"),
            "current_stage": o.get("current_stage"),
            "status_label": o.get("status_label"),
            "design_status": o.get("design_status"),
            "payment_status": o.get("payment_status"),
            "design_bypassed": bool(o.get("design_bypassed")),
            "payment_bypassed": bool(o.get("payment_bypassed")),
            "hold": bool(o.get("hold")),
            "work_date": o.get("work_date"),
            "total_quantity": o.get("total_quantity"),
            # Batch 1: expose effective deadline + status untuk badge.
            "delivery_deadline_effective": o.get("delivery_deadline_effective"),
            "delivery_deadline_status": o.get("delivery_deadline_status"),
            "delivery_deadline_source": o.get("delivery_deadline_source"),
        }

    return {
        "kpi": {
            "total_active": total_active,
            "total_urgent": total_urgent,
            "due_today": len(due_today),
            "overdue": len(overdue),
            "deadline_h1": len(deadline_h1),
            "deadline_h6": len(deadline_h6),
            "waiting_design_approval": len(waiting_design),
            "waiting_payment_approval": len(waiting_payment),
            "hold": len(hold_list),
            "completed": total_completed,
        },
        "active": [slim(o) for o in active_orders],
        "urgent": [slim(o) for o in urgent_orders],
        "due_today": [slim(o) for o in due_today],
        "overdue": [slim(o) for o in overdue],
        "deadline_h1": [slim(o) for o in deadline_h1],
        "deadline_h6": [slim(o) for o in deadline_h6],
        "waiting_design_approval": [slim(o) for o in waiting_design],
        "waiting_payment_approval": [slim(o) for o in waiting_payment],
        "hold": [slim(o) for o in hold_list],
        "completed": [slim(o) for o in completed_orders],
        "stage_breakdown": stage_breakdown,
        "operator_load": operator_load,
        "generated_at": now_iso(),
        "work_today": build_work_today(orders, today),
        "shipments_today": build_shipments_today(
            await db.orders.find(
                shipments_today_mongo_query(today), SCHEDULE_PROJECTION
            ).to_list(None),
            today,
        ),
    }


@api_router.get("/dashboard/today")
async def dashboard_today(user=Depends(require_perm("po.view"))):
    """Compact Pengerjaan/Pengiriman Hari Ini. Targeted queries, not a full DB scan."""
    today = wib_today()
    work_docs = await db.orders.find(work_today_mongo_query(today), TODAY_WORK_PROJECTION).to_list(500)
    ship_docs = await db.orders.find(
        shipments_today_mongo_query(today), SCHEDULE_PROJECTION
    ).to_list(None)
    return {
        "date": today.isoformat(),
        "timezone": "WIB",
        "work_today": build_work_today(work_docs, today),
        "shipments_today": build_shipments_today(ship_docs, today),
        "generated_at": now_iso(),
    }


# ---------- Production schedule (Req 6) — real-data table view + Excel export ----------
def _production_row(o: dict) -> dict:
    """Return one row per PO for the Jadwal Produksi table. If PO is split
    across operators, the caller may expand `operator_splits` into extra rows
    for the Excel export — the main row still contains the aggregate qty."""
    splits = o.get("operator_splits", []) or []
    hp = o.get("helper_prep") or {}
    helper_target = _qr_helper_target_qty(o) if (splits or hp) else hp.get("qty_required")
    helper_prepared = hp.get("qty_prepared")
    helper_shortage = (
        max(int(helper_target or 0) - int(helper_prepared or 0), 0)
        if (splits or hp) else hp.get("qty_shortage")
    )
    helper_status = (
        "Barang Kurang" if helper_shortage else "Cukup"
    ) if (splits or hp) else hp.get("shortage_status")
    sc = o.get("screener_info") or {}
    loading = o.get("loading_checklist") or {}
    hold = bool(o.get("hold"))
    if o.get("cancelled"):
        status_final = "Cancelled"
    elif o.get("current_stage", 0) >= 14:
        status_final = "Terkirim"
    elif hold:
        status_final = "HOLD"
    else:
        status_final = o.get("status_label") or STAGES[min(o.get("current_stage", 0), len(STAGES) - 1)]
    line_items = o.get("line_items") or []

    def _split_printable(s):
        try:
            idx = int(s.get("line_idx"))
        except (TypeError, ValueError):
            return False
        if 0 <= idx < len(line_items):
            return _is_print_line(line_items[idx])
        return False

    def _split_line_identity(s):
        try:
            idx = int(s.get("line_idx"))
        except (TypeError, ValueError):
            return {"line_id": None, "line_name": None, "accurate_item_id": None, "master_item_id": None}
        if not (0 <= idx < len(line_items)):
            return {"line_id": None, "line_name": None, "accurate_item_id": None, "master_item_id": None}
        li = line_items[idx]
        return {
            "line_id": li.get("id") or li.get("line_id"),
            "line_name": li.get("name") or li.get("item_name"),
            "accurate_item_id": li.get("accurate_item_id"),
            "master_item_id": li.get("master_item_id") or li.get("item_id"),
        }

    return {
        "id": o.get("id"),
        "po_number": o.get("po_number"),
        "customer": o.get("customer"),
        "outlet": o.get("outlet"),
        "deadline": o.get("deadline"),
        "order_date": o.get("order_date"),
        "priority": o.get("priority", "normal"),
        "urgent": o.get("priority") == "urgent",
        "buffer_stock": bool(o.get("buffer_stock")),
        "total_quantity": o.get("total_quantity", 0),
        "print_qty_total": _print_qty_total(o),
        "non_print_qty_total": _non_print_qty_total(o),
        "print_completed_qty": _print_completed_qty(o),
        "operator_assigned_qty": _operator_assigned_qty(o),
        "available_for_packing": _available_for_packing(o),
        "qty_delivered": _qr_qty_delivered(o),
        "operators": [{
            "split_id": s.get("id"),
            "operator_name": s.get("operator_name"),
            "operator_email": s.get("operator_email"),
            "operator_user_id": s.get("operator_user_id") or s.get("operator_id"),
            "quantity": s.get("quantity", 0),
            "qty_completed": s.get("qty_completed", 0),
            "line_idx": s.get("line_idx"),
            **_split_line_identity(s),
            "line_printable": _split_printable(s),
            "stage": s.get("stage"),
            "stage_label": STAGES[min(s.get("stage", 8), len(STAGES) - 1)],
            "work_date": s.get("work_date"),
            "screen_size": _line_screen_size(o, s.get("line_idx")) or s.get("screen_size") or None,
        } for s in splits],
        "design_status": o.get("design_status"),
        "design_bypassed": bool(o.get("design_bypassed")),
        "payment_status": o.get("payment_status"),
        "payment_bypassed": bool(o.get("payment_bypassed")),
        "hold": hold,
        "hold_reasons": o.get("hold_reasons", []),
        # Batch 1: expose effective deadline + status untuk kolom Jadwal.
        "delivery_deadline_effective": o.get("delivery_deadline_effective"),
        "delivery_deadline_status": o.get("delivery_deadline_status"),
        "delivery_deadline_source": o.get("delivery_deadline_source"),
        "screen_qty": sc.get("screen_qty"),
        "screener_notes": sc.get("notes"),
        "helper_required": helper_target,
        "helper_prepared": helper_prepared,
        "helper_shortage": helper_shortage,
        "helper_shortage_status": helper_status,
        # Task 12 bugfix: kolom Packing memakai semantic derived dari
        # current_stage — konsisten dengan Status Akhir (STAGES[current_stage]).
        # PO existing dengan stale packing_status="Sudah" (dulu di-set otomatis
        # saat masuk stage 11) tidak lagi ditampilkan sebagai "Sudah" bila
        # belum benar-benar keluar dari Packing.
        "packing_status": _packing_status_semantic(o),
        "packing_qty": (o.get("packing_info") or {}).get("qty_packed"),
        "role_status": _role_status_from_order(o),
        "delivery_note_number": o.get("delivery_note_number"),
        "loading_ok": _loading_completed(o),
        "vehicle_plate": (loading or {}).get("vehicle_plate"),
        "driver_name": (loading or {}).get("driver_name"),
        "delivery_status": o.get("delivery_status"),
        "delivery_proof_count": len(o.get("delivery_proofs") or []),
        "current_stage": o.get("current_stage", 0),
        "status_label": o.get("status_label"),
        "status_final": status_final,
        "cancelled": bool(o.get("cancelled")),
        "cancel_reason": o.get("cancel_reason"),
        "sales": o.get("sales") or o.get("sales_user"),
        # Req 13: Tanggal Pengerjaan (global + per-operator dates)
        "work_date": o.get("work_date"),
        "operator_work_dates": [
            {"split_id": s.get("id"), "operator_name": s.get("operator_name"), "work_date": s.get("work_date")}
            for s in splits if s.get("work_date")
        ],
    }


_PRODUCTION_DASHBOARD_LIMIT = 1000
# Lean fields for /m/schedule. Enough for canonical `_production_row`; no file bytes.
_PRODUCTION_DASHBOARD_SUMMARY_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "outlet": 1,
    "deadline": 1,
    "order_date": 1,
    "work_date": 1,
    "priority": 1,
    "buffer_stock": 1,
    "total_quantity": 1,
    "quantity": 1,
    "qty_packed": 1,
    "current_stage": 1,
    "status_label": 1,
    "cancelled": 1,
    "cancel_reason": 1,
    "sales": 1,
    "sales_user": 1,
    "design_status": 1,
    "design_bypassed": 1,
    "design_approved_at": 1,
    "design_bypassed_at": 1,
    "payment_status": 1,
    "payment_bypassed": 1,
    "payment_approved_at": 1,
    "payment_bypassed_at": 1,
    "delivery_deadline_manual": 1,
    "delivery_status": 1,
    "qty_delivered": 1,
    "delivery_note_number": 1,
    "delivery_note_created_at": 1,
    "delivery_note_created_by": 1,
    "surat_jalan_created": 1,
    "packing_status": 1,
    "packing_info": 1,
    "helper_prep": 1,
    "screener_info": 1,
    "loading_checklist": 1,
    "loading_info": 1,
    "loading_confirmations": 1,
    "loading_completed": 1,
    "line_items.id": 1,
    "line_items.line_id": 1,
    "line_items.name": 1,
    "line_items.item_name": 1,
    "line_items.quantity": 1,
    "line_items.production_process": 1,
    "line_items.accurate_item_id": 1,
    "line_items.master_item_id": 1,
    "line_items.item_id": 1,
    "line_items.screen_size": 1,
    "operator_splits.id": 1,
    "operator_splits.operator_name": 1,
    "operator_splits.operator_email": 1,
    "operator_splits.operator_user_id": 1,
    "operator_splits.operator_id": 1,
    "operator_splits.quantity": 1,
    "operator_splits.qty_completed": 1,
    "operator_splits.line_idx": 1,
    "operator_splits.stage": 1,
    "operator_splits.work_date": 1,
    "operator_splits.screen_size": 1,
    "design_files.id": 1,
    "payment_files.id": 1,
    "surat_jalan_files.id": 1,
    "surat_jalan_files.name": 1,
    "surat_jalan_files.uploaded_at": 1,
    "surat_jalan_files.storage_path": 1,
    "delivery_proofs.uploaded_at": 1,
    "shipments.id": 1,
    "shipments.lines": 1,
    "shipments.accurate_shipment_id": 1,
}


@api_router.get("/dashboard/production")
async def production_dashboard(
    include_cancelled: bool = False,
    include_completed: bool = True,
    view: Optional[str] = None,
    user=Depends(require_perm("schedule.view")),
):
    """Return Jadwal Produksi rows (real Mongo data). Filters/search happen on
    the client. Excluding cancelled by default; explicit flag to include.

    Optional `view=summary` is additive: same canonical rows via a lean
    projection (no file bytes). Default (no view) keeps the full-document
    load for desktop /produksi.
    """
    q: dict = {}
    if not include_cancelled:
        q["cancelled"] = {"$ne": True}
    if (view or "").strip().lower() == "summary":
        orders = await db.orders.find(q, _PRODUCTION_DASHBOARD_SUMMARY_PROJECTION).sort("deadline", 1).to_list(_PRODUCTION_DASHBOARD_LIMIT)
    else:
        orders = await db.orders.find(q, {"_id": 0}).sort("deadline", 1).to_list(_PRODUCTION_DASHBOARD_LIMIT)
    _dd_settings = await _get_delivery_deadline_settings()
    for o in orders:
        _apply_hold_snapshot(o)
        # Batch 1: attach delivery deadline snapshot (source of truth sama).
        _attach_deadline_snapshot(o, _dd_settings["lead_time_days"])
    if not include_completed:
        orders = [o for o in orders if o.get("current_stage", 0) < 14]
    rows = [_production_row(o) for o in orders]
    return {"rows": rows, "generated_at": now_iso(), "total": len(rows)}


@api_router.get("/dashboard/shipment-status")
async def shipment_status_dashboard(
    include_cancelled: bool = False,
    user=Depends(require_perm("schedule.view")),
):
    """Read-only Status Pengiriman Barang. One query; percents from existing qty rules."""
    q: dict = {}
    if not include_cancelled:
        q["cancelled"] = {"$ne": True}
    orders = await db.orders.find(q, SHIPMENT_STATUS_PROJECTION).sort("deadline", 1).to_list(1000)
    _dd_settings = await _get_delivery_deadline_settings()
    rows = []
    for o in orders:
        _attach_deadline_snapshot(o, _dd_settings["lead_time_days"])
        rows.append(shipment_status_row(o))
    return {"rows": rows, "generated_at": now_iso(), "total": len(rows)}


# Dedicated Status PO inclusion — presence-only file arrays, no embedded bytes.
# Must cover effective_workflow_status + shipment % + dashboard view filters.
STATUS_PO_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "order_date": 1,
    "deadline": 1,
    "delivery_deadline_manual": 1,
    "scheduled_delivery_date": 1,
    "buffer_stock": 1,
    "design_approved_at": 1,
    "design_bypassed_at": 1,
    "payment_approved_at": 1,
    "payment_bypassed_at": 1,
    "delivered_at": 1,
    "delivery_proofs.uploaded_at": 1,
    "delivery_proofs.created_at": 1,
    "qty_delivered": 1,
    "qty_packed": 1,
    "delivery_status": 1,
    "current_stage": 1,
    "total_quantity": 1,
    "quantity": 1,
    "packing_required_total": 1,
    "line_items.name": 1,
    "line_items.item_name": 1,
    "line_items.product_name": 1,
    "line_items.quantity": 1,
    "line_items.production_process": 1,
    "requires_printing": 1,
    "operator_splits.qty_completed": 1,
    "operator_splits.line_idx": 1,
    "operator_splits.quantity": 1,
    "operator_splits.work_date": 1,
    "operator_splits.operator_name": 1,
    "shipments.id": 1,
    "shipments.lines": 1,
    "shipments.created_at": 1,
    "shipments.number": 1,
    "cancelled": 1,
    "work_date": 1,
    "hold": 1,
    "priority": 1,
    "status_label": 1,
    "design_status": 1,
    "design_bypassed": 1,
    "design_files.id": 1,
    "payment_status": 1,
    "payment_bypassed": 1,
    "payment_files.id": 1,
    "customer_approval.status": 1,
    "screener_info.screen_qty": 1,
    "helper_prep.qty_prepared": 1,
    "sample_files.id": 1,
    "sample_approved": 1,
    "packing_status": 1,
    "packing_status_semantic": 1,
    "packing_info.qty_packed": 1,
    "packing_info.lines.line_id": 1,
    "packing_info.lines.line_idx": 1,
    "packing_info.lines.packed_qty": 1,
    "surat_jalan_created": 1,
    "surat_jalan_status_semantic": 1,
    "delivery_note_number": 1,
    "delivery_note_created_at": 1,
    "delivery_note_created_by": 1,
    "loading_completed": 1,
    "loading_completed_at": 1,
    "loading_qty_status": 1,
    "loading_info.events.at": 1,
    "loading_info.events.created_at": 1,
    "loading_info.events.qty": 1,
    "loading_info.events.line_idx": 1,
    "loading_info.lines.line_idx": 1,
    "loading_info.lines.loaded_qty": 1,
    "loading_confirmations.id": 1,
    "loading_confirmations.surat_jalan_id": 1,
    "loading_confirmations.shipment_id": 1,
    "loading_confirmations.cancelled": 1,
    "loading_confirmations.deleted": 1,
    "loading_confirmations.lines.line_idx": 1,
    "loading_confirmations.lines.qty": 1,
    "loading_confirmations.lines.quantity": 1,
    "legacy_loading_allocations.status": 1,
    "legacy_loading_allocations.shipment_id": 1,
    "legacy_loading_allocations.surat_jalan_id": 1,
    "legacy_loading_allocations.lines.line_idx": 1,
    "legacy_loading_allocations.lines.qty": 1,
    "legacy_loading_allocations.lines.quantity": 1,
    "shipments.delivery_status": 1,
    "shipments.delivered_at": 1,
    "shipments.sales_delivery_id": 1,
    "shipments.status": 1,
    "shipments.sj_status": 1,
    "shipments.cancelled": 1,
    "shipments.superseded": 1,
    "shipments.superseded_by_shipment_id": 1,
    "shipments.qty_delivered": 1,
    "shipments.total_qty": 1,
    "surat_jalan_files.id": 1,
    "surat_jalan_files.shipment_id": 1,
    "surat_jalan_files.sj_number": 1,
    "surat_jalan_files.number": 1,
    "surat_jalan_files.sales_delivery_id": 1,
    "surat_jalan_files.status": 1,
    "surat_jalan_files.lines.line_idx": 1,
    "surat_jalan_files.lines.qty": 1,
    "surat_jalan_files.lines.quantity": 1,
    "delivery_confirmations.shipment_id": 1,
    "delivery_confirmations.qty": 1,
    "terkirim_confirmed": 1,
    "print_status": 1,
    "print_completed_qty": 1,
}


@api_router.get("/dashboard/status-po")
async def status_po_dashboard(
    view: Optional[str] = None,
    po: Optional[str] = None,
    customer: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_cancelled: bool = False,
    user=Depends(require_perm("schedule.view")),
):
    """Laporan Status PO — one query, dashboard card subsets, shipment % reuse."""
    reject_sales_pwa_bypass(user)
    q: dict = {}
    if not include_cancelled:
        q["cancelled"] = {"$ne": True}
    q.update(sales_owner_match(user))
    orders = await db.orders.find(q, STATUS_PO_PROJECTION).sort("deadline", 1).to_list(1000)
    _dd_settings = await _get_delivery_deadline_settings()
    for o in orders:
        _apply_hold_snapshot(o)
        _attach_deadline_snapshot(o, _dd_settings["lead_time_days"])
    data = build_status_po(orders, view, filters={
        "po": po or "",
        "customer": customer or "",
        "status": status or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
    })
    data["generated_at"] = now_iso()
    return data


class ProductionExportPayload(BaseModel):
    ids: Optional[List[str]] = None  # if provided, export only these
    include_cancelled: bool = False
    include_completed: bool = True
    scope: str = "filtered"  # informational; "filtered" or "all"


@api_router.post("/dashboard/production/export")
async def export_production_excel(payload: ProductionExportPayload, user=Depends(require_perm("dashboard.production.export"))):
    """Generate an Excel (.xlsx) export of the Jadwal Produksi. No DB writes.
    - One header row
    - AutoFilter + Freeze top row
    - Excel dates & numeric qty (no merged cells)
    - Optional Ringkasan sheet with factual counts (no KPI/score)
    """
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter
    from datetime import datetime as _dt

    q: dict = {}
    if payload.ids:
        q["id"] = {"$in": payload.ids}
    else:
        if not payload.include_cancelled:
            q["cancelled"] = {"$ne": True}
    orders = await db.orders.find(q, {"_id": 0}).sort("deadline", 1).to_list(2000)
    for o in orders:
        _apply_hold_snapshot(o)
    if not payload.include_completed and not payload.ids:
        orders = [o for o in orders if o.get("current_stage", 0) < 14]

    wb = Workbook()
    ws = wb.active
    ws.title = "Jadwal Produksi"
    headers = [
        "Deadline", "Prioritas", "No PO", "Customer", "Outlet", "Sales",
        "Total Qty", "Operator", "Qty Operator", "Qty Selesai Operator",
        "Approval Design", "Design Bypass", "Approval Payment", "Payment Bypass",
        "HOLD", "Alasan HOLD",
        "Qty Screen", "Catatan Screener",
        "Qty Dibutuhkan (Helper)", "Qty Disiapkan (Helper)", "Qty Kurang (Helper)", "Status Barang",
        "Stage Sekarang", "Packing", "No Surat Jalan", "Loading OK", "Kendaraan", "Driver",
        "Status Kirim", "Jumlah Bukti Kirim", "Status Akhir", "Alasan Cancel",
    ]
    ws.append(headers)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="17313E")
    for col_idx, _ in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col_idx)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal="left", vertical="center")

    def parse_iso_date(iso: Optional[str]):
        if not iso:
            return None
        try:
            return _dt.fromisoformat(iso.replace("Z", "+00:00")).date()
        except Exception:
            return None

    row_idx = 1
    for o in orders:
        splits = o.get("operator_splits") or []
        hp = o.get("helper_prep") or {}
        sc = o.get("screener_info") or {}
        loading = o.get("loading_checklist") or {}
        hold = bool(o.get("hold"))
        if o.get("cancelled"):
            status_final = "Cancelled"
        elif o.get("current_stage", 0) >= 14:
            status_final = "Terkirim"
        elif hold:
            status_final = "HOLD"
        else:
            status_final = o.get("status_label") or STAGES[min(o.get("current_stage", 0), len(STAGES) - 1)]

        base = [
            parse_iso_date(o.get("deadline")),
            "Urgent" if o.get("priority") == "urgent" else "Normal",
            o.get("po_number"),
            o.get("customer"),
            o.get("outlet"),
            o.get("sales") or o.get("sales_user"),
            int(o.get("total_quantity") or 0),
        ]
        if splits:
            for i, s in enumerate(splits):
                row_idx += 1
                op_row = list(base)
                # Only first row shows Total Qty to avoid double counting
                if i > 0:
                    op_row[6] = None
                op_row += [
                    s.get("operator_name") or s.get("operator_email"),
                    int(s.get("quantity") or 0),
                    int(s.get("qty_completed") or 0),
                ]
                # After the first operator row, blank the remaining PO-level cells
                if i == 0:
                    op_row += [
                        o.get("design_status") or "-",
                        "Ya" if o.get("design_bypassed") else "Tidak",
                        o.get("payment_status") or "-",
                        "Ya" if o.get("payment_bypassed") else "Tidak",
                        "HOLD" if hold else "-",
                        "; ".join(o.get("hold_reasons") or []),
                        sc.get("screen_qty"),
                        sc.get("notes") or "",
                        _qr_helper_target_qty(o),
                        hp.get("qty_prepared"),
                        max(_qr_helper_target_qty(o) - int(hp.get("qty_prepared") or 0), 0),
                        ("Barang Kurang" if max(_qr_helper_target_qty(o) - int(hp.get("qty_prepared") or 0), 0) > 0 else "Cukup"),
                        STAGES[min(o.get("current_stage", 0), len(STAGES) - 1)],
                        o.get("packing_status") or "-",
                        o.get("delivery_note_number") or "",
                        "OK" if _loading_completed(o) else "-",
                        loading.get("vehicle_plate") or "",
                        loading.get("driver_name") or "",
                        o.get("delivery_status") or "-",
                        len(o.get("delivery_proofs") or []),
                        status_final,
                        o.get("cancel_reason") or "",
                    ]
                else:
                    op_row += [""] * 22
                ws.append(op_row)
        else:
            row_idx += 1
            ws.append(base + [
                "", "", "",
                o.get("design_status") or "-",
                "Ya" if o.get("design_bypassed") else "Tidak",
                o.get("payment_status") or "-",
                "Ya" if o.get("payment_bypassed") else "Tidak",
                "HOLD" if hold else "-",
                "; ".join(o.get("hold_reasons") or []),
                sc.get("screen_qty"),
                sc.get("notes") or "",
                _qr_helper_target_qty(o),
                hp.get("qty_prepared"),
                max(_qr_helper_target_qty(o) - int(hp.get("qty_prepared") or 0), 0),
                ("Barang Kurang" if max(_qr_helper_target_qty(o) - int(hp.get("qty_prepared") or 0), 0) > 0 else "Cukup"),
                STAGES[min(o.get("current_stage", 0), len(STAGES) - 1)],
                o.get("packing_status") or "-",
                o.get("delivery_note_number") or "",
                "OK" if _loading_completed(o) else "-",
                loading.get("vehicle_plate") or "",
                loading.get("driver_name") or "",
                o.get("delivery_status") or "-",
                len(o.get("delivery_proofs") or []),
                status_final,
                o.get("cancel_reason") or "",
            ])

    # Auto width (approx)
    widths = [12, 10, 18, 22, 22, 12, 10, 22, 12, 16, 18, 14, 18, 15, 8, 30, 12, 24, 20, 20, 16, 16, 18, 12, 18, 12, 18, 20, 14, 16, 16, 24]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    # Freeze header + autofilter
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(row_idx,1)}"
    # Format date column (A) & qty columns as numeric
    for r in range(2, row_idx + 1):
        d = ws.cell(row=r, column=1).value
        if isinstance(d, _dt):
            ws.cell(row=r, column=1).number_format = "yyyy-mm-dd"
        for qty_col in (7, 9, 10, 17, 19, 20, 21, 30):
            ws.cell(row=r, column=qty_col).number_format = "#,##0"

    # ---------- Ringkasan sheet ----------
    ws2 = wb.create_sheet("Ringkasan")
    total = len(orders)
    aktif = sum(1 for o in orders if not o.get("cancelled") and o.get("current_stage", 0) < 14 and not o.get("hold"))
    hold_n = sum(1 for o in orders if not o.get("cancelled") and o.get("hold"))
    urgent = sum(1 for o in orders if o.get("priority") == "urgent" and not o.get("cancelled"))
    completed = sum(1 for o in orders if o.get("current_stage", 0) >= 14 and not o.get("cancelled"))
    cancelled = sum(1 for o in orders if o.get("cancelled"))
    now = datetime.now(timezone.utc)
    overdue = 0
    for o in orders:
        if o.get("cancelled") or o.get("current_stage", 0) >= 14: continue
        try:
            dl = datetime.fromisoformat((o.get("deadline") or "").replace("Z", "+00:00"))
            if dl.tzinfo is None: dl = dl.replace(tzinfo=timezone.utc)
            if dl < now: overdue += 1
        except Exception:
            pass
    total_qty = sum(int(o.get("total_quantity") or 0) for o in orders if not o.get("cancelled"))
    rows2 = [
        ["Metrik", "Nilai"],
        ["Total PO (scope)", total],
        ["Total Qty (scope)", total_qty],
        ["Aktif", aktif],
        ["HOLD", hold_n],
        ["Urgent", urgent],
        ["Overdue", overdue],
        ["Selesai", completed],
        ["Cancelled", cancelled],
    ]
    for r in rows2: ws2.append(r)
    # PO per operator
    ws2.append([])
    ws2.append(["Jumlah PO per Operator", "Total Qty"])
    op_map: dict = {}
    for o in orders:
        if o.get("cancelled"): continue
        for s in o.get("operator_splits") or []:
            name = s.get("operator_name") or s.get("operator_email") or "-"
            op_map.setdefault(name, {"po": 0, "qty": 0})
            op_map[name]["po"] += 1
            op_map[name]["qty"] += int(s.get("quantity") or 0)
    for name, v in sorted(op_map.items(), key=lambda x: -x[1]["po"]):
        ws2.append([name, v["po"], v["qty"]])
    ws2.column_dimensions["A"].width = 32
    ws2.column_dimensions["B"].width = 14
    ws2.column_dimensions["C"].width = 14
    ws2["A1"].font = Font(bold=True, color="FFFFFF")
    ws2["A1"].fill = header_fill
    ws2["B1"].font = Font(bold=True, color="FFFFFF")
    ws2["B1"].fill = header_fill

    # Audit (informational only, no DB write to orders)
    await _write_audit("dashboard.production.export", user, target_email=None,
                       after={"scope": payload.scope, "count": len(orders), "ids": bool(payload.ids)},
                       note=f"Export Excel {payload.scope} ({len(orders)} PO)")

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"jadwal-produksi-{now.strftime('%Y%m%d-%H%M')}.xlsx"
    return FastAPIResponse(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )




# ---------- Admin: Employee & Role Management ----------
class EmployeeCreate(BaseModel):
    email: str
    name: str
    role: str
    nickname: Optional[str] = None
    phone: Optional[str] = None
    password: Optional[str] = None
    joined_at: Optional[str] = None
    notes: Optional[str] = None
    permissions_override: Optional[Dict[str, bool]] = None


class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    nickname: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    joined_at: Optional[str] = None
    notes: Optional[str] = None
    permissions_override: Optional[Dict[str, bool]] = None


class PasswordResetPayload(BaseModel):
    new_password: str


class RolePermissionsPayload(BaseModel):
    permissions: List[str]


class RoleCreatePayload(BaseModel):
    role: str          # slug, lowercase, unique
    label: str
    description: Optional[str] = ""
    permissions: List[str] = []


class RoleUpdatePayload(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[List[str]] = None


class SuperAdminTransferPayload(BaseModel):
    to_user_id: str
    reason: Optional[str] = ""


def _sanitize_user(u: dict) -> dict:
    """Strip mongo _id + password fields for JSON responses."""
    if not u:
        return u
    u.pop("_id", None)
    u.pop("password_hash", None)
    return u


async def _write_audit(action: str, actor: dict, target_email: Optional[str], before=None, after=None, note: str = ""):
    """Record an audit entry. NEVER stores raw password / credential values."""
    def _scrub(v):
        if not isinstance(v, dict):
            return v
        return {k: ("[REDACTED]" if k in ("password", "password_hash", "access_token", "token", "client_secret") else vv)
                for k, vv in v.items()}
    doc = {
        "id": str(uuid.uuid4()),
        "action": action,
        "actor_email": actor.get("email"),
        "actor_role": actor.get("role"),
        "actor_name": actor.get("name"),
        "target_email": target_email,
        "before": _scrub(before) if isinstance(before, dict) else before,
        "after": _scrub(after) if isinstance(after, dict) else after,
        "note": note,
        "at": now_iso(),
    }
    await db.audit_logs.insert_one(doc)
    return doc


async def _next_employee_id() -> str:
    """Simple sequential employee id (EMP-0001, EMP-0002, ...)."""
    last = await db.users.find({"employee_id": {"$exists": True}}, {"employee_id": 1}).sort("employee_id", -1).limit(1).to_list(1)
    if not last:
        return "EMP-0001"
    try:
        num = int((last[0]["employee_id"] or "EMP-0000").split("-")[-1])
    except ValueError:
        num = 0
    return f"EMP-{num + 1:04d}"


@api_router.get("/admin/permissions/catalog")
async def perm_catalog(user=Depends(require_perm("users.view"))):
    """List all permission keys grouped by module — for the Hak Akses matrix."""
    modules = {}
    from permissions import PERMISSION_DESCRIPTIONS
    for module, key, label in PERMISSION_CATALOG:
        if key == NAV_CONFIGURED:
            continue
        item = {"key": key, "label": label}
        desc = PERMISSION_DESCRIPTIONS.get(key)
        if desc:
            item["description"] = desc
        modules.setdefault(module, []).append(item)
    return {
        "modules": modules,
        "all_keys": [k for k in ALL_PERMISSIONS if k != NAV_CONFIGURED],
        "nav_menu": [{"key": k, "label": lbl, "parent": parent} for k, lbl, parent in NAV_MENU_ITEMS],
    }


BUILTIN_ROLES = {"admin", "head_production", "head_operations", "designer", "screener", "helper", "operator", "driver", "crm"}
ROLE_SLUG_RX = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


async def _seed_roles_collection():
    """Idempotent: make sure every built-in role has a row in `db.roles` so
    the Roles UI can list them with metadata (label/description/status)."""
    for slug in BUILTIN_ROLES:
        existing = await db.roles.find_one({"role": slug})
        if not existing:
            await db.roles.insert_one({
                "role": slug,
                "label": ROLE_LABELS.get(slug, slug),
                "description": "",
                "is_builtin": True,
                "status": "active",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            })


async def _load_role_doc(slug: str) -> Optional[dict]:
    return await db.roles.find_one({"role": slug}, {"_id": 0})


async def _role_user_count(slug: str) -> int:
    return await db.users.count_documents({"role": slug})


async def _role_slug_is_valid(slug: str) -> bool:
    """Accept any built-in role OR an ACTIVE custom role stored in db.roles."""
    if not slug:
        return False
    if slug in ROLE_DEFAULT_PERMISSIONS:
        return True
    doc = await db.roles.find_one({"role": slug}, {"status": 1, "_id": 0})
    return bool(doc and doc.get("status", "active") == "active")


@api_router.get("/admin/roles")
async def list_roles(user=Depends(require_perm("users.view"))):
    await _seed_roles_collection()
    overrides = await _role_defaults_override()
    docs = await db.roles.find({}, {"_id": 0}).sort("is_builtin", -1).to_list(200)
    slugs_seen = set()
    out = []
    for d in docs:
        slug = d["role"]
        slugs_seen.add(slug)
        raw = list(overrides.get(slug, ROLE_DEFAULT_PERMISSIONS.get(slug, [])))
        base = sorted(with_nav_defaults(set(raw)))
        out.append({
            "role": slug,
            "label": d.get("label") or ROLE_LABELS.get(slug, slug),
            "description": d.get("description", ""),
            "is_builtin": bool(d.get("is_builtin")),
            "status": d.get("status", "active"),
            "permissions": base,
            "is_customized": slug in overrides,
            "builtin_permissions": sorted(with_nav_defaults(set(ROLE_DEFAULT_PERMISSIONS.get(slug, [])))),
            "user_count": await _role_user_count(slug),
        })
    # Include any built-in that isn't in the collection yet (safety)
    for slug in BUILTIN_ROLES - slugs_seen:
        raw = list(overrides.get(slug, ROLE_DEFAULT_PERMISSIONS.get(slug, [])))
        base = sorted(with_nav_defaults(set(raw)))
        out.append({
            "role": slug, "label": ROLE_LABELS.get(slug, slug), "description": "",
            "is_builtin": True, "status": "active",
            "permissions": base, "is_customized": slug in overrides,
            "builtin_permissions": sorted(with_nav_defaults(set(ROLE_DEFAULT_PERMISSIONS.get(slug, [])))),
            "user_count": await _role_user_count(slug),
        })
    return {"roles": out}


@api_router.post("/admin/roles")
async def create_role(payload: RoleCreatePayload, user=Depends(require_perm("permissions.manage"))):
    slug = payload.role.strip().lower()
    if not ROLE_SLUG_RX.match(slug):
        raise HTTPException(400, "Slug role harus huruf kecil, angka, atau underscore (2-31 karakter, awal huruf).")
    if slug in BUILTIN_ROLES:
        raise HTTPException(409, f"Role '{slug}' adalah role built-in.")
    if await db.roles.find_one({"role": slug}):
        raise HTTPException(409, f"Role '{slug}' sudah ada.")
    invalid = [p for p in payload.permissions if p not in ALL_PERMISSIONS]
    if invalid:
        raise HTTPException(400, f"Permission tidak dikenal: {invalid}")
    now = now_iso()
    doc = {
        "role": slug,
        "label": payload.label.strip() or slug,
        "description": (payload.description or "").strip(),
        "is_builtin": False,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "created_by": user.get("email"),
    }
    await db.roles.insert_one(doc)
    # Persist permissions in role_permissions so they load into effective set
    if payload.permissions:
        await db.role_permissions.update_one(
            {"role": slug},
            {"$set": {"role": slug, "permissions": sorted(set(payload.permissions)), "updated_at": now, "updated_by": user.get("email")}},
            upsert=True,
        )
    doc.pop("_id", None)
    await _write_audit("role.create", user, target_email=None, before=None, after={**doc, "permissions": payload.permissions}, note=f"Buat role {slug}")
    return {"role": doc, "permissions": payload.permissions}


@api_router.patch("/admin/roles/{role}")
async def update_role(role: str, payload: RoleUpdatePayload, user=Depends(require_perm("permissions.manage"))):
    slug = role.strip().lower()
    existing = await _load_role_doc(slug)
    if not existing:
        raise HTTPException(404, "Role tidak dikenal.")
    if slug == "admin" and payload.permissions is not None:
        raise HTTPException(400, "Permission role admin tidak dapat diubah.")
    updates = {"updated_at": now_iso(), "updated_by": user.get("email")}
    if payload.label is not None: updates["label"] = payload.label.strip() or existing.get("label")
    if payload.description is not None: updates["description"] = payload.description.strip()
    await db.roles.update_one({"role": slug}, {"$set": updates})
    perm_before = await db.role_permissions.find_one({"role": slug}, {"_id": 0})
    if payload.permissions is not None:
        invalid = [p for p in payload.permissions if p not in ALL_PERMISSIONS]
        if invalid:
            raise HTTPException(400, f"Permission tidak dikenal: {invalid}")
        await db.role_permissions.update_one(
            {"role": slug},
            {"$set": {"role": slug, "permissions": sorted(set(payload.permissions)), "updated_at": now_iso(), "updated_by": user.get("email")}},
            upsert=True,
        )
    after = await _load_role_doc(slug)
    perm_after = await db.role_permissions.find_one({"role": slug}, {"_id": 0})
    await _write_audit("role.update", user, target_email=None,
                       before={**existing, "permissions": (perm_before or {}).get("permissions", [])},
                       after={**after, "permissions": (perm_after or {}).get("permissions", [])},
                       note=f"Update role {slug}")
    return {"role": after}


@api_router.put("/admin/roles/{role}/permissions")
async def update_role_permissions(role: str, payload: RolePermissionsPayload, user=Depends(require_perm("permissions.manage"))):
    slug = role.strip().lower()
    existing = await _load_role_doc(slug)
    if not existing and slug not in BUILTIN_ROLES:
        raise HTTPException(404, "Role tidak dikenal")
    if slug == "admin":
        raise HTTPException(400, "Permission role admin tidak dapat diubah")
    invalid = [p for p in payload.permissions if p not in ALL_PERMISSIONS]
    if invalid:
        raise HTTPException(400, f"Permission tidak dikenal: {invalid}")
    before = await db.role_permissions.find_one({"role": slug}, {"_id": 0})
    await db.role_permissions.update_one(
        {"role": slug},
        {"$set": {"role": slug, "permissions": sorted(set(payload.permissions)), "updated_at": now_iso(), "updated_by": user.get("email")}},
        upsert=True,
    )
    after = await db.role_permissions.find_one({"role": slug}, {"_id": 0})
    await _write_audit("role.permissions.update", user, target_email=None, before=before, after=after, note=f"Role {slug} permissions")
    return {"role": slug, "permissions": after.get("permissions", [])}


@api_router.post("/admin/roles/{role}/deactivate")
async def deactivate_role(role: str, user=Depends(require_perm("permissions.manage"))):
    slug = role.strip().lower()
    existing = await _load_role_doc(slug)
    if not existing:
        raise HTTPException(404, "Role tidak dikenal")
    if slug == "admin":
        raise HTTPException(400, "Role admin tidak dapat dinonaktifkan.")
    if existing.get("status") == "inactive":
        return {"role": existing}
    await db.roles.update_one({"role": slug}, {"$set": {"status": "inactive", "updated_at": now_iso(), "updated_by": user.get("email")}})
    after = await _load_role_doc(slug)
    await _write_audit("role.deactivate", user, target_email=None, before=existing, after=after, note=f"Nonaktifkan role {slug}")
    return {"role": after}


@api_router.post("/admin/roles/{role}/activate")
async def activate_role(role: str, user=Depends(require_perm("permissions.manage"))):
    slug = role.strip().lower()
    existing = await _load_role_doc(slug)
    if not existing:
        raise HTTPException(404, "Role tidak dikenal")
    if existing.get("status") == "active":
        return {"role": existing}
    await db.roles.update_one({"role": slug}, {"$set": {"status": "active", "updated_at": now_iso(), "updated_by": user.get("email")}})
    after = await _load_role_doc(slug)
    await _write_audit("role.activate", user, target_email=None, before=existing, after=after, note=f"Aktifkan role {slug}")
    return {"role": after}


@api_router.delete("/admin/roles/{role}")
async def delete_role(role: str, user=Depends(require_perm("permissions.manage"))):
    slug = role.strip().lower()
    existing = await _load_role_doc(slug)
    if not existing:
        raise HTTPException(404, "Role tidak dikenal")
    if existing.get("is_builtin") or slug in BUILTIN_ROLES:
        raise HTTPException(400, "Role built-in tidak dapat dihapus permanen. Gunakan Nonaktifkan.")
    n = await _role_user_count(slug)
    if n > 0:
        raise HTTPException(400, {
            "message": f"Role '{slug}' masih digunakan oleh {n} karyawan. Gunakan Nonaktifkan.",
            "user_count": n,
        })
    await db.roles.delete_one({"role": slug})
    await db.role_permissions.delete_one({"role": slug})
    await _write_audit("role.delete", user, target_email=None, before=existing, after=None, note=f"Hapus role {slug}")
    return {"deleted": True, "role": slug}


# ---- Super Admin management ----
async def _active_super_admin_count(exclude_user_id: Optional[str] = None) -> int:
    q: dict = {"is_super_admin": True, "status": {"$ne": "inactive"}}
    if exclude_user_id:
        q["id"] = {"$ne": exclude_user_id}
    return await db.users.count_documents(q)


async def _require_super_admin(actor: dict):
    """Only Super Admin (or the built-in admin role for bootstrap) may manage SA."""
    if actor.get("email"):
        u = await db.users.find_one({"email": actor["email"]}, {"is_super_admin": 1, "role": 1})
        if u and (u.get("is_super_admin") or u.get("role") == "admin"):
            return
    raise HTTPException(403, "Hanya Super Admin yang dapat mengelola Super Admin.")


@api_router.post("/admin/users/{user_id}/super-admin/grant")
async def grant_super_admin(user_id: str, user=Depends(current_user)):
    await _require_super_admin(user)
    existing = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    if not existing:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    if existing.get("status") != "active":
        raise HTTPException(400, "Karyawan non-aktif tidak dapat dijadikan Super Admin.")
    if existing.get("is_super_admin"):
        return {"user": existing}
    await db.users.update_one({"id": user_id}, {"$set": {"is_super_admin": True, "updated_at": now_iso()}})
    after = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    await _write_audit("super_admin.grant", user, target_email=existing.get("email"), before=existing, after=after, note="Jadikan Super Admin")
    return {"user": after}


@api_router.post("/admin/users/{user_id}/super-admin/revoke")
async def revoke_super_admin(user_id: str, user=Depends(current_user)):
    await _require_super_admin(user)
    existing = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    if not existing:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    if not existing.get("is_super_admin"):
        return {"user": existing}
    # Invariant: at least 1 active Super Admin must remain
    remaining = await _active_super_admin_count(exclude_user_id=existing.get("id"))
    if remaining < 1:
        raise HTTPException(400, "Tidak dapat mencabut Super Admin terakhir. Jadikan user lain Super Admin terlebih dahulu.")
    await db.users.update_one({"id": user_id}, {"$set": {"is_super_admin": False, "updated_at": now_iso()}})
    after = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    await _write_audit("super_admin.revoke", user, target_email=existing.get("email"), before=existing, after=after, note="Cabut Super Admin")
    return {"user": after}


@api_router.post("/admin/users/{user_id}/super-admin/transfer")
async def transfer_super_admin(user_id: str, payload: SuperAdminTransferPayload, user=Depends(current_user)):
    await _require_super_admin(user)
    src = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    if not src:
        raise HTTPException(404, "Karyawan sumber tidak ditemukan")
    if not src.get("is_super_admin"):
        raise HTTPException(400, "User sumber bukan Super Admin.")
    dst = await db.users.find_one({"id": payload.to_user_id}, {"password_hash": 0, "_id": 0})
    if not dst:
        raise HTTPException(404, "Karyawan tujuan tidak ditemukan")
    if dst.get("status") != "active":
        raise HTTPException(400, "Karyawan tujuan tidak aktif — tidak dapat menjadi Super Admin.")
    if src.get("id") == dst.get("id"):
        raise HTTPException(400, "Sumber dan tujuan tidak boleh sama.")
    # Atomic: grant destination + revoke source
    now = now_iso()
    await db.users.update_one({"id": dst["id"]}, {"$set": {"is_super_admin": True, "updated_at": now}})
    await db.users.update_one({"id": src["id"]}, {"$set": {"is_super_admin": False, "updated_at": now}})
    # Post-check invariant
    if await _active_super_admin_count() < 1:
        # Should never happen because we granted first; roll back for safety
        await db.users.update_one({"id": src["id"]}, {"$set": {"is_super_admin": True, "updated_at": now}})
        raise HTTPException(500, "Gagal transfer: tidak ada Super Admin aktif.")
    src_after = await db.users.find_one({"id": src["id"]}, {"password_hash": 0, "_id": 0})
    dst_after = await db.users.find_one({"id": dst["id"]}, {"password_hash": 0, "_id": 0})
    await _write_audit("super_admin.transfer", user,
                       target_email=f"{src.get('email')} → {dst.get('email')}",
                       before={"from": src, "to": dst},
                       after={"from": src_after, "to": dst_after},
                       note=(payload.reason or "Transfer Super Admin"))
    return {"from": src_after, "to": dst_after}


@api_router.get("/admin/users")
async def list_users_admin(status: Optional[str] = None, user=Depends(require_perm("users.view"))):
    q = {}
    if status:
        q["status"] = status
    users = await db.users.find(q, {"password_hash": 0, "_id": 0}).sort("created_at", -1).to_list(500)
    return {"users": users, "count": len(users)}


@api_router.get("/admin/users/{user_id}")
async def get_user_admin(user_id: str, user=Depends(require_perm("users.view"))):
    u = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    if not u:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    return {"user": u}


@api_router.post("/admin/users")
async def create_user_admin(payload: EmployeeCreate, user=Depends(require_perm("users.manage"))):
    saved = await create_canonical_user(
        email=payload.email,
        name=payload.name,
        role=payload.role,
        password=payload.password,
        nickname=payload.nickname,
        phone=payload.phone,
        joined_at=payload.joined_at,
        notes=payload.notes,
        permissions_override=payload.permissions_override,
        actor=user,
        require_password=False,
    )
    return {"user": saved}


async def create_canonical_user(
    *,
    email: str,
    name: str,
    role: str,
    password: Optional[str] = None,
    nickname: Optional[str] = None,
    phone: Optional[str] = None,
    joined_at: Optional[str] = None,
    notes: Optional[str] = None,
    permissions_override: Optional[Dict[str, bool]] = None,
    actor: dict,
    require_password: bool = False,
) -> dict:
    """Shared Settings Karyawan user provisioning. Does not create hr_employees."""
    email_norm = (email or "").strip().lower()
    name_clean = (name or "").strip()
    role_slug = (role or "").strip()
    if not email_norm:
        raise HTTPException(400, "Email wajib diisi")
    if not name_clean:
        raise HTTPException(400, "Nama wajib diisi")
    if not await _role_slug_is_valid(role_slug):
        raise HTTPException(400, f"Role '{role_slug}' tidak dikenal")
    if await db.users.find_one({"email": email_norm}):
        raise HTTPException(409, "Email sudah digunakan oleh akun Eternal lain.")
    if require_password:
        if not password or len(str(password)) < 6:
            raise HTTPException(400, "Password minimal 6 karakter")
        raw_password = str(password)
    else:
        # Settings existing behavior: optional password falls back to trial default.
        raw_password = password or os.environ.get("TRIAL_DEFAULT_PASSWORD", "Eternal123!")
    now = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "employee_id": await _next_employee_id(),
        "email": email_norm,
        "name": name_clean,
        "nickname": (nickname or "").strip() or None,
        "phone": (phone or "").strip() or None,
        "role": role_slug,
        "status": "active",
        "joined_at": joined_at,
        "deactivated_at": None,
        "notes": notes,
        "permissions_override": permissions_override or {},
        "password_hash": hash_password(raw_password),
        "created_at": now,
        "updated_at": now,
        "created_by": (actor or {}).get("email"),
    }
    await db.users.insert_one(doc)
    await _write_audit(
        "user.create",
        actor,
        target_email=email_norm,
        before=None,
        after=_sanitize_user(dict(doc)),
        note=f"Tambah karyawan {name_clean}",
    )
    saved = await db.users.find_one({"id": doc["id"]}, {"password_hash": 0, "_id": 0})
    await _sync_chat_user_best_effort(doc["id"])
    return saved


@api_router.put("/admin/users/{user_id}")
async def update_user_admin(user_id: str, payload: EmployeeUpdate, user=Depends(require_perm("users.manage"))):
    existing = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    if not existing:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    before = dict(existing)
    updates = {"updated_at": now_iso(), "updated_by": user.get("email")}
    if payload.name is not None: updates["name"] = payload.name.strip()
    if payload.nickname is not None: updates["nickname"] = payload.nickname.strip() or None
    if payload.phone is not None: updates["phone"] = payload.phone.strip() or None
    if payload.joined_at is not None: updates["joined_at"] = payload.joined_at
    if payload.notes is not None: updates["notes"] = payload.notes
    if payload.role is not None:
        if not await _role_slug_is_valid(payload.role):
            raise HTTPException(400, "Role tidak dikenal")
        updates["role"] = payload.role
    if payload.permissions_override is not None:
        # Reject unknown permission keys
        invalid = [k for k in payload.permissions_override if k not in ALL_PERMISSIONS]
        if invalid:
            raise HTTPException(400, f"Permission tidak dikenal: {invalid}")
        updates["permissions_override"] = payload.permissions_override
    await db.users.update_one({"id": user_id}, {"$set": updates})
    after = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    await _write_audit("user.update", user, target_email=existing.get("email"), before=before, after=after, note=f"Update karyawan {existing.get('email')}")
    await _sync_chat_user_best_effort(user_id)
    return {"user": after}


@api_router.get("/admin/users/{user_id}/permissions/effective")
async def user_permissions_effective(user_id: str, user=Depends(require_perm("users.view"))):
    """Req 15: Return the breakdown for a user's permissions.

    - `default_from_role`: permissions inherited from the user's role (after
      any admin edits to role defaults via /admin/roles/{r}/permissions).
    - `override`: user's per-permission override dict ({key: True|False});
      missing keys mean "inherit from role".
    - `effective`: final set actually enforced (role_default ∪ override(true) − override(false));
      Super Admin returns ALL_PERMISSIONS.
    """
    existing = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    if not existing:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    role = existing.get("role")
    role_override = await _role_defaults_override()
    inactive = await _inactive_role_slugs()
    if role in inactive:
        default_from_role = []
    elif role_override and role in role_override:
        default_from_role = list(with_nav_defaults(set(role_override[role] or [])))
    else:
        default_from_role = list(with_nav_defaults(set(ROLE_DEFAULT_PERMISSIONS.get(role, []))))
    override = dict(existing.get("permissions_override") or {})
    if existing.get("is_super_admin"):
        effective = list(ALL_PERMISSIONS)
    else:
        effective = sorted(effective_permissions(role, override, role_override))
    return {
        "user_id": user_id,
        "email": existing.get("email"),
        "role": role,
        "is_super_admin": bool(existing.get("is_super_admin")),
        "default_from_role": sorted(default_from_role),
        "override": override,
        "effective": effective,
    }


@api_router.post("/admin/users/{user_id}/permissions/reset-override")
async def user_permissions_reset(user_id: str, user=Depends(require_perm("permissions.manage"))):
    """Req 15: Clear a user's `permissions_override` so effective perms revert
    to pure role-default. Idempotent. Audited."""
    existing = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    if not existing:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    before_override = dict(existing.get("permissions_override") or {})
    if not before_override:
        return {"user": existing, "changed": False, "message": "Tidak ada override yang perlu direset"}
    await db.users.update_one({"id": user_id}, {"$set": {"permissions_override": {}, "updated_at": now_iso(), "updated_by": user.get("email")}})
    after = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    await _write_audit(
        "user.permissions.reset_override", user,
        target_email=existing.get("email"),
        before={"permissions_override": before_override},
        after={"permissions_override": {}},
        note=f"Reset {len(before_override)} override → mengikuti role default",
    )
    return {"user": after, "changed": True, "message": f"{len(before_override)} override direset"}


@api_router.post("/admin/users/{user_id}/deactivate")
async def deactivate_user(user_id: str, user=Depends(require_perm("users.manage"))):
    existing = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    if not existing:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    if existing.get("email") == user.get("email"):
        raise HTTPException(400, "Anda tidak dapat menonaktifkan akun Anda sendiri")
    # Invariant: cannot deactivate the last active Super Admin
    if existing.get("is_super_admin"):
        remaining = await _active_super_admin_count(exclude_user_id=existing.get("id"))
        if remaining < 1:
            raise HTTPException(400, "Tidak dapat menonaktifkan Super Admin terakhir. Transfer Super Admin terlebih dahulu.")
    await db.users.update_one({"id": user_id}, {"$set": {"status": "inactive", "deactivated_at": now_iso(), "updated_at": now_iso()}})
    after = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    await _write_audit("user.deactivate", user, target_email=existing.get("email"), before=existing, after=after, note="Nonaktifkan karyawan")
    await _sync_chat_user_best_effort(user_id)
    return {"user": after}


@api_router.post("/admin/users/{user_id}/activate")
async def activate_user(user_id: str, user=Depends(require_perm("users.manage"))):
    existing = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    if not existing:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    await db.users.update_one({"id": user_id}, {"$set": {"status": "active", "deactivated_at": None, "updated_at": now_iso()}})
    after = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    await _write_audit("user.activate", user, target_email=existing.get("email"), before=existing, after=after, note="Aktifkan kembali karyawan")
    await _sync_chat_user_best_effort(user_id)
    return {"user": after}


@api_router.post("/admin/users/{user_id}/reset-password")
async def reset_password_admin(user_id: str, payload: PasswordResetPayload, user=Depends(require_perm("users.manage"))):
    if len(payload.new_password or "") < 6:
        raise HTTPException(400, "Password minimal 6 karakter")
    existing = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    if not existing:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    await db.users.update_one({"id": user_id}, {"$set": {"password_hash": hash_password(payload.new_password), "updated_at": now_iso()}})
    # NEVER log the password value.
    await _write_audit("user.reset_password", user, target_email=existing.get("email"), before=None, after=None, note="Password di-reset admin (nilai tidak disimpan)")
    return {"ok": True}


@api_router.get("/admin/audit-logs")
async def list_audit_logs(limit: int = 200, action: Optional[str] = None, user=Depends(require_perm("audit.view"))):
    q = {}
    if action:
        q["action"] = action
    logs = await db.audit_logs.find(q, {"_id": 0}).sort("at", -1).to_list(min(limit, 1000))
    return {"logs": logs, "count": len(logs)}


# ---------- Accurate integration boundary ----------
_accurate_client = AccurateClient()


class ImportPreviewRequest(BaseModel):
    so_number: str


def _build_preview(so: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a preview payload for a single Accurate Sales Order.

    IMPORTANT: `sales` (tenaga penjual) and `created_by_user` (user pembuat PO)
    are SEPARATE fields. If Accurate does not provide one, leave it as None
    so the UI can render 'Perlu verifikasi' rather than guessing.
    """
    total_qty = sum(
        coerce_accurate_quantity(li.get("quantity"))
        for li in so.get("line_items", [])
        if isinstance(li, dict)
    )
    order_date, deadline = so_preview_dates(so)
    return {
        "accurate_id": so.get("accurate_id"),
        "po_number": so.get("po_number"),
        "customer": so.get("customer"),
        "outlet": so.get("outlet"),
        "recipient": so.get("recipient"),
        "address": so.get("address"),
        "payment_terms": so.get("payment_terms"),
        "sales": so.get("sales"),                   # tenaga penjual (bukan user)
        "created_by_user": so.get("created_by_user"),  # user pembuat PO
        "expedition": so.get("expedition"),
        "delivery_zone": so.get("delivery_zone"),
        "seller": so.get("seller"),
        "order_date": order_date,
        "estimated_delivery": deadline,
        "deadline": deadline,
        "notes": so.get("notes") or "",
        "line_items": so.get("line_items", []),
        "total_quantity": total_qty,
        "item_count": len(so.get("line_items", [])),
    }


async def _find_existing_import(so: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return existing PO (if any) that already references this Accurate SO."""
    query = {"$or": [
        {"accurate_id": so.get("accurate_id")},
        {"po_number": so.get("po_number")},
    ]}
    # accurate_id may be None for file imports; drop that clause
    query["$or"] = [c for c in query["$or"] if list(c.values())[0]]
    if not query["$or"]:
        return None
    return await db.orders.find_one(query, {"_id": 0})


async def _persist_imported_po(preview: Dict[str, Any], *, source: str, user: dict) -> Dict[str, Any]:
    """Insert a new PO document at stage 0 from a preview payload.

    `source` is one of: 'accurate_live', 'accurate_mock', 'file'.
    Never overwrites existing orders — caller must check duplicates first.
    """
    now = now_iso()
    new = {
        "id": str(uuid.uuid4()),
        "po_number": preview["po_number"],
        "accurate_id": preview.get("accurate_id"),
        "accurate_synced_at": now if source.startswith("accurate") else None,
        "accurate_source": source,
        "import_source": source,
        "import_filename": preview.get("filename"),
        "source_pdf": preview.get("source_pdf"),
        "order_date": preview["order_date"],
        "estimated_delivery": preview["estimated_delivery"],
        "deadline": preview["deadline"],
        "priority": "normal",
        "urgency_approved_by": None,
        "customer": preview.get("customer"),
        "outlet": preview.get("outlet"),
        "recipient": preview.get("recipient"),
        "address": preview.get("address"),
        "payment_terms": preview.get("payment_terms"),
        "payment_status": "Waiting",
        "design_status": "Waiting",
        "sales": preview.get("sales"),
        "created_by_user": preview.get("created_by_user"),
        "imported_by": user.get("email"),
        "imported_by_name": user.get("name"),
        "imported_at": now,
        "expedition": preview.get("expedition"),
        "delivery_zone": preview.get("delivery_zone"),
        "seller": preview.get("seller") or preview.get("sales"),
        "line_items": preview.get("line_items", []),
        "total_quantity": preview.get("total_quantity", 0),
        "item_count": preview.get("item_count", 0),
        "operator_splits": [],
        "design_files": [],
        "sample_files": [],
        "delivery_proofs": [],
        "notes": preview.get("notes", ""),
        "current_stage": 0,
        "status_label": STAGES[0],
        "packing_status": "Belum",
        "delivery_status": "Belum Terkirim",
        "delivery_note_number": "",
        "loading_checklist": None,
        "timeline": [{
            "stage": 0,
            "stage_label": STAGES[0],
            "at": now,
            "by_email": user.get("email"),
            "by_role": user.get("role"),
            "by_name": user.get("name"),
            "note": f"Import dari {source}" + (f" · SO {preview.get('accurate_id')}" if preview.get('accurate_id') else "") + (f" · file {preview.get('filename')}" if preview.get('filename') else ""),
        }],
        "created_at": now,
        "updated_at": now,
    }
    from services.physical_stock_engine import attach_order_stock_snapshot
    stock_warnings = await validate_sales_order_availability(db, new.get("line_items") or [])
    await attach_order_stock_snapshot(db, new)
    await attach_sales_ownership(db, new)
    await db.orders.insert_one(new)
    # If we have a source PDF uploaded during preview, record it in db.files
    # so the /api/files/{path} route (soft-delete aware) can serve it.
    sp = preview.get("source_pdf")
    if sp and sp.get("storage_path"):
        await db.files.insert_one({
            "id": str(uuid.uuid4()),
            "storage_path": sp["storage_path"],
            "original_filename": sp.get("original_filename") or "upload.pdf",
            "content_type": "application/pdf",
            "size": sp.get("size") or 0,
            "kind": "import-source-pdf",
            "order_id": new["id"],
            "po_number": new["po_number"],
            "uploaded_by": user.get("email"),
            "uploaded_by_role": user.get("role"),
            "uploaded_by_name": user.get("name"),
            "uploaded_at": sp.get("uploaded_at") or now,
            "note": "PDF asli import",
            "is_deleted": False,
        })
    # In-memory only — not persisted; stock shortage is informational for Import PO UI.
    if stock_warnings:
        new["stock_warnings"] = list(stock_warnings)
    return new


@api_router.get("/accurate/status")
async def accurate_status(user=Depends(require_perm("po.import.view"))):
    live = _accurate_client.is_live
    auth_mode = _accurate_client.auth_mode
    # Booleans only — never expose value or partial credential.
    diagnostics = {
        "api_token_present": bool(_accurate_client.api_token),
        "api_signature_secret_present": bool(_accurate_client.api_signature_secret),
        "db_id_present": bool(_accurate_client.db_id),
        "oauth_client_present": bool(_accurate_client.client_id and _accurate_client.client_secret),
        "oauth_token_present": bool(_accurate_client.access_token or _accurate_client.refresh_token),
    }
    if live:
        message = f"Accurate live client active ({auth_mode}). Read-only single-PO import via /accurate/import."
    elif diagnostics["api_token_present"] and diagnostics["db_id_present"] and not diagnostics["api_signature_secret_present"]:
        message = "Accurate MOCK mode: ACCURATE_API_TOKEN + ACCURATE_DB_ID terdeteksi, tetapi ACCURATE_API_SIGNATURE_SECRET belum diisi. App Accurate Anda memerlukan signature. Tambahkan secret tersebut ke backend/.env lalu restart backend."
    else:
        message = "Accurate is running in MOCK mode. Provide ACCURATE_API_TOKEN + ACCURATE_API_SIGNATURE_SECRET + ACCURATE_DB_ID (recommended) or OAuth credentials in backend env."
    return {
        "mode": "live" if live else "mock",
        "auth_mode": auth_mode,
        "host": _accurate_client.host,
        "credentials_present": live,
        "diagnostics": diagnostics,
        "read_only": True,
        "message": message,
    }


def _accurate_http_error(exc: AccurateRequestError) -> HTTPException:
    """Map a typed Accurate failure to a safe HTTP error. Never includes stack traces."""
    status = 502
    if exc.category == AccurateErrorCategory.NOT_FOUND:
        status = 404
    elif exc.category == AccurateErrorCategory.RATE_LIMITED:
        status = 429
    elif exc.category == AccurateErrorCategory.TIMEOUT:
        status = 504
    elif exc.category == AccurateErrorCategory.TOKEN_EXPIRED:
        status = 401
    elif exc.category == AccurateErrorCategory.UNAUTHORIZED:
        status = 403
    return HTTPException(status, exc.user_message)


@api_router.post("/accurate/import/preview")
async def accurate_import_preview(payload: ImportPreviewRequest, user=Depends(require_perm("accurate.import"))):
    """Fetch ONE Sales Order from Accurate (or mock) and return a preview. Never
    writes to `orders`. Reports whether this SO has already been imported."""
    try:
        so = await _accurate_client.fetch_single_sales_order(payload.so_number)
    except AccurateRequestError as e:
        raise _accurate_http_error(e)
    if not so:
        raise HTTPException(404, f"Sales Order '{payload.so_number}' tidak ditemukan di Accurate")
    existing = await _find_existing_import(so)
    return {
        "preview": _build_preview(so),
        "duplicate": bool(existing),
        "existing_po_id": existing.get("id") if existing else None,
        "existing_po_number": existing.get("po_number") if existing else None,
        "mode": "live" if _accurate_client.is_live else "mock",
    }


@api_router.post("/accurate/import/confirm")
async def accurate_import_confirm(payload: ImportPreviewRequest, user=Depends(require_perm("accurate.import"))):
    """Confirm import of a single Accurate SO into `orders`. Idempotent by
    (accurate_id, po_number). Writes an entry to `import_logs`."""
    log_base = {
        "id": str(uuid.uuid4()),
        "so_number": payload.so_number,
        "accurate_id": None,
        "po_number": None,
        "imported_by": user.get("email"),
        "imported_by_name": user.get("name"),
        "imported_at": now_iso(),
        "mode": "live" if _accurate_client.is_live else "mock",
        "read_only_source": True,
    }
    try:
        so = await _accurate_client.fetch_single_sales_order(payload.so_number)
    except AccurateRequestError as e:
        await db.import_logs.insert_one({**log_base, "status": "failed", "message": e.user_message})
        raise _accurate_http_error(e)
    log_base["accurate_id"] = (so or {}).get("accurate_id")
    log_base["po_number"] = (so or {}).get("po_number")
    if not so:
        await db.import_logs.insert_one({**log_base, "status": "failed", "message": "SO tidak ditemukan"})
        raise HTTPException(404, f"Sales Order '{payload.so_number}' tidak ditemukan di Accurate")
    existing = await _find_existing_import(so)
    if existing:
        await db.import_logs.insert_one({**log_base, "status": "duplicate", "message": "PO ini sudah pernah diimport", "existing_po_id": existing.get("id")})
        raise HTTPException(409, {
            "message": "PO ini sudah pernah diimport",
            "existing_po_id": existing.get("id"),
            "existing_po_number": existing.get("po_number"),
        })
    preview = _build_preview(so)
    source = "accurate_live" if _accurate_client.is_live else "accurate_mock"
    try:
        preview["line_items"] = await resolve_po_line_items(
            db, _accurate_client, preview.get("line_items") or [], require_master=True,
        )
        new = await _persist_imported_po(preview, source=source, user=user)
    except MasterItemUnresolvedError as e:
        await db.import_logs.insert_one({**log_base, "status": "failed", "message": str(e)})
        raise HTTPException(400, str(e))
    except DuplicateKeyError:
        existing = await _find_existing_import(so)
        await db.import_logs.insert_one({**log_base, "status": "duplicate", "message": "PO ini sudah pernah diimport", "existing_po_id": (existing or {}).get("id")})
        raise HTTPException(409, {
            "message": "PO ini sudah pernah diimport",
            "existing_po_id": (existing or {}).get("id"),
            "existing_po_number": (existing or {}).get("po_number"),
        })
    except HTTPException:
        raise
    except Exception:
        await db.import_logs.insert_one({**log_base, "status": "failed", "message": "Insert gagal"})
        raise HTTPException(500, "Gagal menyimpan PO baru")
    await db.import_logs.insert_one({**log_base, "status": "success", "message": "PO baru berhasil diimport", "created_po_id": new["id"]})
    saved = await db.orders.find_one({"id": new["id"]}, {"_id": 0})
    warnings = list(new.get("stock_warnings") or [])
    return {
        "order": saved,
        "message": "PO berhasil diimport dari Accurate",
        "warnings": warnings,
    }


class BulkImportRequest(BaseModel):
    so_numbers: List[str]


@api_router.post("/accurate/import/preview-bulk")
async def accurate_import_preview_bulk(payload: BulkImportRequest, user=Depends(require_perm("accurate.import.bulk"))):
    """Fetch MULTIPLE Sales Orders from Accurate (or mock) with bounded concurrency.

    Per-SO result: success/ok, preview, duplicate flags, error_code, user_message,
    retryable. NEVER writes to Mongo. One SO failure does not fail the batch.
    """
    numbers = [n.strip() for n in (payload.so_numbers or []) if n and n.strip()]
    if not numbers:
        raise HTTPException(400, "Minimal pilih 1 Sales Order")
    if len(numbers) > 25:
        raise HTTPException(400, "Maksimal 25 Sales Order per batch")

    cid = new_correlation_id()
    fetched = await _accurate_client.fetch_sales_order_results(numbers, correlation_id=cid)
    items: List[Dict[str, Any]] = []
    for row in fetched:
        sn = row.get("so_number") or row.get("sales_order_number") or ""
        if not row.get("success"):
            user_msg = row.get("user_message") or "Terjadi kesalahan saat memproses Sales Order."
            items.append({
                "so_number": sn,
                "sales_order_number": sn,
                "success": False,
                "ok": False,
                "data": None,
                "preview": None,
                "duplicate": False,
                "existing_po_id": None,
                "existing_po_number": None,
                "has_verify": False,
                "error_code": row.get("error_code") or AccurateErrorCategory.INTERNAL_ERROR,
                "user_message": user_msg,
                "retryable": bool(row.get("retryable")),
                "error": user_msg,
            })
            continue
        so = row.get("data") or {}
        existing = await _find_existing_import(so)
        prev = _build_preview(so)
        needs_verify = any(li.get("color_needs_verify") for li in prev.get("line_items", []))
        items.append({
            "so_number": sn,
            "sales_order_number": sn,
            "success": True,
            "ok": True,
            "data": prev,
            "preview": prev,
            "duplicate": bool(existing),
            "existing_po_id": existing.get("id") if existing else None,
            "existing_po_number": existing.get("po_number") if existing else None,
            "has_verify": needs_verify,
            "error_code": None,
            "user_message": None,
            "retryable": False,
            "error": None,
        })
    ready = [i for i in items if i.get("ok") and not i.get("duplicate")]
    dupes = [i for i in items if i.get("ok") and i.get("duplicate")]
    errs = [i for i in items if not i.get("ok")]
    total_qty = sum(int((i.get("preview") or {}).get("total_quantity") or 0) for i in ready)
    total_items = sum(int((i.get("preview") or {}).get("item_count") or 0) for i in ready)
    needs_verify = [i["so_number"] for i in ready if i.get("has_verify")]
    return {
        "items": items,
        "summary": {
            "total": len(items),
            "ready": len(ready),
            "duplicates": len(dupes),
            "errors": len(errs),
            "total_qty": total_qty,
            "total_items": total_items,
            "needs_verify": needs_verify,
        },
        "mode": "live" if _accurate_client.is_live else "mock",
        "correlation_id": cid,
    }


@api_router.post("/accurate/import/confirm-bulk")
async def accurate_import_confirm_bulk(payload: BulkImportRequest, user=Depends(require_perm("accurate.import.bulk"))):
    """Confirm import of MULTIPLE Sales Orders sequentially. Idempotent per SO.
    Duplicates are reported (not inserted). Returns per-SO status."""
    numbers = [n.strip() for n in (payload.so_numbers or []) if n and n.strip()]
    if not numbers:
        raise HTTPException(400, "Minimal pilih 1 Sales Order")
    if len(numbers) > 25:
        raise HTTPException(400, "Maksimal 25 Sales Order per batch")

    results = []
    imported_count = 0
    for sn in numbers:
        log_base = {
            "id": str(uuid.uuid4()),
            "so_number": sn,
            "imported_by": user.get("email"),
            "imported_by_name": user.get("name"),
            "imported_at": now_iso(),
            "mode": "live" if _accurate_client.is_live else "mock",
            "read_only_source": True,
            "batch": True,
        }
        try:
            so = await _accurate_client.fetch_single_sales_order(sn)
        except AccurateRequestError as e:
            await db.import_logs.insert_one({**log_base, "status": "failed", "message": e.user_message})
            results.append({
                "so_number": sn,
                "status": "failed",
                "message": e.user_message,
                "error_code": e.category,
                "retryable": e.retryable,
            })
            continue
        except Exception:
            await db.import_logs.insert_one({**log_base, "status": "failed", "message": "Fetch gagal"})
            results.append({
                "so_number": sn,
                "status": "failed",
                "message": "Terjadi kesalahan saat memproses Sales Order.",
                "error_code": AccurateErrorCategory.INTERNAL_ERROR,
                "retryable": False,
            })
            continue
        if not so:
            await db.import_logs.insert_one({**log_base, "status": "failed", "message": "SO tidak ditemukan"})
            results.append({"so_number": sn, "status": "failed", "message": "SO tidak ditemukan"})
            continue
        log_base["accurate_id"] = so.get("accurate_id")
        log_base["po_number"] = so.get("po_number")
        existing = await _find_existing_import(so)
        if existing:
            await db.import_logs.insert_one({**log_base, "status": "duplicate", "message": "PO ini sudah pernah diimport", "existing_po_id": existing.get("id")})
            results.append({
                "so_number": sn, "status": "duplicate",
                "existing_po_id": existing.get("id"),
                "existing_po_number": existing.get("po_number"),
            })
            continue
        preview = _build_preview(so)
        source = "accurate_live" if _accurate_client.is_live else "accurate_mock"
        try:
            preview["line_items"] = await resolve_po_line_items(
                db, _accurate_client, preview.get("line_items") or [], require_master=True,
            )
            new = await _persist_imported_po(preview, source=source, user=user)
        except MasterItemUnresolvedError as e:
            await db.import_logs.insert_one({**log_base, "status": "failed", "message": str(e)})
            results.append({"so_number": sn, "status": "failed", "message": str(e)})
            continue
        except DuplicateKeyError:
            existing = await _find_existing_import(so)
            await db.import_logs.insert_one({**log_base, "status": "duplicate", "message": "PO ini sudah pernah diimport", "existing_po_id": (existing or {}).get("id")})
            results.append({
                "so_number": sn, "status": "duplicate",
                "existing_po_id": (existing or {}).get("id"),
                "existing_po_number": (existing or {}).get("po_number"),
            })
            continue
        except HTTPException:
            raise
        except Exception:
            await db.import_logs.insert_one({**log_base, "status": "failed", "message": "Insert gagal"})
            results.append({"so_number": sn, "status": "failed", "message": "Gagal menyimpan PO baru"})
            continue
        stock_warnings = list(new.get("stock_warnings") or [])
        await db.import_logs.insert_one({**log_base, "status": "success", "message": "PO baru berhasil diimport", "created_po_id": new["id"]})
        row = {
            "so_number": sn,
            "status": "success",
            "po_id": new["id"],
            "po_number": new["po_number"],
        }
        if stock_warnings:
            row["warnings"] = stock_warnings
        results.append(row)
        imported_count += 1

    all_warnings = []
    for r in results:
        for w in r.get("warnings") or []:
            if w not in all_warnings:
                all_warnings.append(w)
    return {
        "results": results,
        "summary": {
            "total": len(numbers),
            "imported": imported_count,
            "duplicates": sum(1 for r in results if r["status"] == "duplicate"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
        },
        "message": f"{imported_count} PO berhasil diimport dari Accurate",
        "warnings": all_warnings,
    }


# ---------- File-based PO import (XLSX / CSV) ----------
@api_router.post("/import/file/preview")
async def import_file_preview(file: UploadFile = File(...), user=Depends(require_perm("po.import.spreadsheet"))):
    """Parse an uploaded XLSX or CSV. Returns a preview WITHOUT persisting."""
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Ukuran file maksimum 5 MB")
    try:
        preview = parse_import_file(data, file.filename or "upload.xlsx")
    except ImportParseError as e:
        raise HTTPException(400, str(e))
    existing = await _find_existing_import({"po_number": preview["po_number"], "accurate_id": preview.get("accurate_id")})
    return {
        "preview": preview,
        "duplicate": bool(existing),
        "existing_po_id": existing.get("id") if existing else None,
        "existing_po_number": existing.get("po_number") if existing else None,
        "mode": "file",
    }


class FileConfirmRequest(BaseModel):
    preview: Dict[str, Any]
    source: str = "file"  # 'file' | 'pdf'


@api_router.post("/import/file/confirm")
async def import_file_confirm(payload: FileConfirmRequest, user=Depends(current_user)):
    """Persist a parsed PO (from XLSX/CSV or PDF). Client sends back the (possibly-edited) preview.
    Permission dienforce berdasarkan `source`: `po.import.pdf` untuk PDF, `po.import.spreadsheet` untuk file lainnya.
    """
    preview = payload.preview or {}
    src = payload.source if payload.source in ("file", "pdf") else "file"
    required_perm = "po.import.pdf" if src == "pdf" else "po.import.spreadsheet"
    await _require_perm(user, required_perm)
    po_number = (preview.get("po_number") or "").strip()
    if not po_number:
        raise HTTPException(400, "Nomor PO wajib pada preview")
    if not (preview.get("customer") or "").strip():
        raise HTTPException(400, "Customer wajib pada preview")
    if not preview.get("line_items"):
        raise HTTPException(400, "Preview tidak berisi line item")
    for i, li in enumerate(preview["line_items"]):
        if not (li.get("name") or "").strip():
            raise HTTPException(400, f"Line item #{i+1}: nama barang wajib")
        try:
            q = int(li.get("quantity") or 0)
        except (TypeError, ValueError):
            q = 0
        if q <= 0:
            raise HTTPException(400, f"Line item #{i+1}: quantity harus angka > 0")
        li["quantity"] = q
    log_base = {
        "id": str(uuid.uuid4()),
        "so_number": po_number,
        "accurate_id": None,
        "po_number": po_number,
        "imported_by": user.get("email"),
        "imported_by_name": user.get("name"),
        "imported_at": now_iso(),
        "mode": src,
        "read_only_source": True,
        "filename": preview.get("filename"),
    }
    existing = await _find_existing_import({"po_number": po_number, "accurate_id": None})
    if existing:
        await db.import_logs.insert_one({**log_base, "status": "duplicate", "message": "PO ini sudah pernah diimport", "existing_po_id": existing.get("id")})
        raise HTTPException(409, {
            "message": "PO ini sudah pernah diimport",
            "existing_po_id": existing.get("id"),
            "existing_po_number": existing.get("po_number"),
            "previous_imported_by": existing.get("imported_by_name") or existing.get("imported_by"),
            "previous_imported_at": existing.get("imported_at"),
            "previous_customer": existing.get("customer"),
        })
    now = datetime.now(timezone.utc)
    preview.setdefault("order_date", now.isoformat())
    preview.setdefault("estimated_delivery", (now + timedelta(days=3)).isoformat())
    preview.setdefault("deadline", preview["estimated_delivery"])
    preview["total_quantity"] = sum(int(li.get("quantity") or 0) for li in preview.get("line_items", []))
    preview["item_count"] = len(preview.get("line_items", []))
    try:
        new = await _persist_imported_po(preview, source=src, user=user)
    except Exception as e:
        await db.import_logs.insert_one({**log_base, "status": "failed", "message": f"Insert gagal: {e}"})
        raise HTTPException(500, "Gagal menyimpan PO baru")
    await db.import_logs.insert_one({**log_base, "status": "success", "message": f"PO baru berhasil diimport dari {src}", "created_po_id": new["id"]})
    saved = await db.orders.find_one({"id": new["id"]}, {"_id": 0})
    return {
        "order": saved,
        "message": f"PO berhasil diimport dari {src}",
        "warnings": list(new.get("stock_warnings") or []),
    }


from services.sales_deliveries import (
    get_po_delivery_summary as _get_po_delivery_summary,
    create_sales_delivery as _create_sales_delivery,
    list_sales_deliveries as _list_sales_deliveries,
    get_sales_delivery_detail as _get_sales_delivery_detail,
    generate_sales_delivery_pdf_bytes as _generate_sales_delivery_pdf_bytes,
)
from services.sales_invoices import (
    get_po_invoice_preview as _get_po_invoice_preview,
    list_charge_account_options as _list_charge_account_options,
    create_sales_invoice as _create_sales_invoice,
    generate_sales_invoice_pdf_bytes,
    list_sales_invoices as _list_sales_invoices,
    get_sales_invoice_detail as _get_sales_invoice_detail,
    update_sales_invoice as _update_sales_invoice,
    delete_sales_invoice as _delete_sales_invoice,
    bulk_delete_sales_invoices as _bulk_delete_sales_invoices,
    finalize_sales_invoice as _finalize_sales_invoice,
    cancel_sales_invoice as _cancel_sales_invoice,
)
from services.sales_invoice_posting import (
    PERM_POST as _SALES_ACCOUNTING_POST_PERM,
    PERM_REVERSE as _SALES_ACCOUNTING_REVERSE_PERM,
    post_sales_invoice_accounting as _post_sales_invoice_accounting,
    reverse_sales_invoice_accounting as _reverse_sales_invoice_accounting,
)
from services.sales_receipt_posting import (
    PERM_POST as _SALES_RECEIPT_ACCOUNTING_POST_PERM,
    PERM_REVERSE as _SALES_RECEIPT_ACCOUNTING_REVERSE_PERM,
    list_cash_bank_options as _list_sales_receipt_cash_bank_options,
    post_sales_receipt_accounting as _post_sales_receipt_accounting,
    reverse_sales_receipt_accounting as _reverse_sales_receipt_accounting,
)
from services.sales_receipts import (
    list_sales_receipts as _list_sales_receipts,
    get_sales_receipt_detail as _get_sales_receipt_detail,
    create_sales_receipt as _create_sales_receipt,
    generate_sales_receipt_pdf_bytes,
    delete_sales_receipt as _delete_sales_receipt,
    bulk_delete_sales_receipts as _bulk_delete_sales_receipts,
    attach_payment_proof_to_receipt as _attach_sales_receipt_payment_proof,
)
from services.sales_receipt_payment_proof import (
    load_proof_bytes as _load_sales_receipt_payment_proof_bytes,
    content_disposition as _sales_receipt_proof_content_disposition,
)
from services.sales_customer_credit import (
    apply_customer_credit as _apply_customer_credit,
    list_available_credit as _list_available_credit,
    reverse_customer_credit_application as _reverse_customer_credit_application,
)
from services.sales_customers import (
    COLLECTION as _SALES_CUSTOMERS_COLLECTION,
    list_sales_customers as _list_sales_customers,
    list_unified_sales_customers as _list_unified_sales_customers,
    get_sales_customer as _get_sales_customer,
    create_sales_customer as _create_sales_customer,
    update_sales_customer as _update_sales_customer,
    upsert_sales_customer_by_key as _upsert_sales_customer_by_key,
    set_sales_customer_active_status as _set_sales_customer_active_status,
    set_sales_customer_active_status_by_key as _set_sales_customer_active_status_by_key,
    customer_is_active as _customer_is_active,
    prepare_customer_for_po as _prepare_customer_for_po,
    link_or_create_customer_for_po as _link_or_create_customer_for_po,
    resolve_or_create_customer_for_po as _resolve_or_create_customer_for_po,
    ensure_sales_customer_indexes as _ensure_sales_customer_indexes,
    SalesCustomerCreatePayload,
    SalesCustomerUpdatePayload,
    SalesCustomerByKeyUpdatePayload,
    SalesCustomerStatusPayload,
    SalesCustomerStatusByKeyPayload,
)

# ---------- Manual Sales Order (Pesanan Penjualan) ----------
class SalesOrderLineItemInput(BaseModel):
    item_code: Optional[str] = None
    name: str
    quantity: int
    unit: Optional[str] = "pcs"
    color: Optional[str] = ""
    production_process: Optional[str] = "print"  # "print" | "non_print"
    notes: Optional[str] = ""
    accurate_item_id: Optional[str] = None
    master_item_id: Optional[str] = None
    unit_price: Optional[Union[int, float, str]] = None
    price_source: Optional[str] = None
    price_review: Optional[bool] = None


class SalesOrderPriceQuotePayload(BaseModel):
    accurate_item_id: Optional[str] = None
    unit: Optional[str] = None
    quantity: Optional[Union[int, float, str]] = None


class SalesOrderDesignFileInput(BaseModel):
    """Optional design seed on create (e.g. Repeat Order). New file ids assigned server-side."""
    name: str
    data_url: str
    content_type: Optional[str] = None


class SalesOrderCreatePayload(BaseModel):
    po_number: Optional[str] = None
    order_date: Optional[str] = None
    customer: str
    customer_id: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_address: Optional[str] = None
    customer_pic: Optional[str] = None
    customer_email: Optional[str] = None
    customer_city: Optional[str] = None
    outlet: Optional[str] = None
    recipient: Optional[str] = None
    address: Optional[str] = None
    delivery_zone: Optional[str] = None
    payment_terms: Optional[str] = None
    sales: Optional[str] = None
    created_by_user: Optional[str] = None
    expedition: Optional[str] = None
    estimated_delivery: Optional[str] = None
    deadline: Optional[str] = None
    notes: Optional[str] = ""
    line_items: List[SalesOrderLineItemInput]
    design_files: Optional[List[SalesOrderDesignFileInput]] = None


# ---------- Sales Delivery (Pengiriman Pesanan) ----------
class SalesDeliveryLineItemInput(BaseModel):
    line_idx: int = 0
    line_id: Optional[str] = None
    qty: int
    notes: Optional[str] = ""


class SalesDeliveryLocationInput(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None
    place_id: Optional[str] = None
    google_place_id: Optional[str] = None
    formatted_address: Optional[str] = None


class SalesDeliveryCreatePayload(BaseModel):
    po_id: str
    delivery_date: Optional[str] = None
    recipient: Optional[str] = None
    address: Optional[str] = None
    delivery_zone: Optional[str] = None
    outlet: Optional[str] = None
    sales: Optional[str] = None
    expedition: Optional[str] = None
    notes: Optional[str] = ""
    line_items: List[SalesDeliveryLineItemInput]
    delivery_location: Optional[SalesDeliveryLocationInput] = None


# ---------- Sales Invoice (Faktur Penjualan) ----------
class SalesInvoiceLineItemInput(BaseModel):
    line_idx: int = 0
    line_id: Optional[str] = None
    quantity: Optional[Union[int, float, str]] = None
    invoice_qty: Optional[Union[int, float, str]] = None
    unit_price: Optional[Union[int, float, str]] = None


class SalesInvoiceOtherChargeInput(BaseModel):
    id: Optional[str] = None
    account_id: Optional[str] = None
    description: Optional[str] = None
    keterangan: Optional[str] = None
    amount: Optional[Union[int, float, str]] = None


class SalesInvoiceCreatePayload(BaseModel):
    po_id: str
    invoice_date: Optional[str] = None
    payment_term_days: Optional[Union[int, str]] = 0
    discount_amount: Optional[Union[int, float, str]] = 0
    ppn_percent: Optional[Union[int, float, str]] = 0
    notes: Optional[str] = ""
    idempotency_key: Optional[str] = None
    lines: Optional[List[SalesInvoiceLineItemInput]] = None
    line_items: Optional[List[SalesInvoiceLineItemInput]] = None
    other_charges: Optional[List[SalesInvoiceOtherChargeInput]] = None


class SalesInvoiceUpdatePayload(BaseModel):
    invoice_date: Optional[str] = None
    payment_term_days: Optional[Union[int, str]] = None
    discount_amount: Optional[Union[int, float, str]] = None
    ppn_percent: Optional[Union[int, float, str]] = None
    notes: Optional[str] = ""
    lines: Optional[List[SalesInvoiceLineItemInput]] = None
    line_items: Optional[List[SalesInvoiceLineItemInput]] = None
    other_charges: Optional[List[SalesInvoiceOtherChargeInput]] = None


class SalesInvoiceReverseAccountingPayload(BaseModel):
    reason: str


# ---------- Sales Receipt (Penerimaan Penjualan) ----------
class SalesReceiptPaymentProofPayload(BaseModel):
    name: str
    data_url: str


class SalesReceiptCreatePayload(BaseModel):
    invoice_id: str
    receipt_date: Optional[str] = None
    amount: Union[int, float, str]
    allocated_amount: Optional[Union[int, float, str]] = None
    notes: Optional[str] = ""
    cash_bank_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    payment_proof: Optional[SalesReceiptPaymentProofPayload] = None


class SalesCustomerCreditApplyPayload(BaseModel):
    source_receipt_id: str
    invoice_id: str
    amount: Union[int, float, str]
    application_date: Optional[str] = None
    idempotency_key: Optional[str] = None


class SalesReceiptPostAccountingPayload(BaseModel):
    cash_bank_id: Optional[str] = None


class SalesReceiptAttachProofPayload(BaseModel):
    payment_proof: SalesReceiptPaymentProofPayload


def _parse_order_date_iso(raw: Optional[str], default_days_ahead: int = 0) -> str:
    now = datetime.now(timezone.utc)
    if not raw or not str(raw).strip():
        dt = now + timedelta(days=default_days_ahead)
        return dt.isoformat()
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            clean = s[:19] if "T" in s else s[:10]
            dt = datetime.strptime(clean, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        raise HTTPException(400, f"Format tanggal tidak valid: {raw}")


async def _next_suggested_po_number(database, target_date: Optional[Union[datetime, date, str]] = None) -> str:
    dt = None
    if target_date is not None:
        if isinstance(target_date, datetime):
            dt = target_date if target_date.tzinfo else target_date.replace(tzinfo=timezone.utc)
        elif isinstance(target_date, date):
            dt = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
        elif isinstance(target_date, str) and target_date.strip():
            try:
                iso_s = _parse_order_date_iso(target_date)
                dt = datetime.fromisoformat(iso_s)
            except Exception:
                dt = None
    if dt is None:
        dt = datetime.now(timezone.utc)

    today_str = dt.strftime("%d/%m/%y")
    prefix = f"PO/{today_str}/"
    regex = f"^{re.escape(prefix)}\\d+"
    cursor = database.orders.find({"po_number": {"$regex": regex}}, {"po_number": 1, "_id": 0})
    existing = await cursor.to_list(1000)
    max_seq = 0
    for doc in existing:
        pn = str(doc.get("po_number") or "")
        suffix = pn[len(prefix):]
        m = re.match(r"^(\d+)", suffix)
        if m:
            try:
                seq = int(m.group(1))
                if seq > max_seq:
                    max_seq = seq
            except ValueError:
                pass
    return f"{prefix}{max_seq + 1:03d}"


# ---------- Sales Customers (Data Pelanggan) ----------
@api_router.get("/sales-customers")
async def list_sales_customers_endpoint(
    q: Optional[str] = None,
    view: Optional[str] = None,
    user=Depends(current_user),
):
    """List sales customers with optional search term.

    Default: persisted master only.
    view=unified: read-only union of master + live PO + prediction history.
    GET is zero-write in both modes.
    """
    reject_sales_pwa_bypass(user)
    await _require_any_perm(
        user,
        "sales.customer.view",
        "sales.customer.manage",
        "sales.customer.edit",
        "sales.customer.manage_status",
    )
    if str(view or "").strip().lower() == "unified":
        customers = await _list_unified_sales_customers(db, q=q)
    else:
        customers = await _list_sales_customers(db, q=q)
        for c in customers:
            c["is_active"] = _customer_is_active(c)
    return {"customers": customers}


@api_router.post("/sales-customers")
async def create_sales_customer_endpoint(
    payload: SalesCustomerCreatePayload,
    user=Depends(current_user),
):
    """Create a new customer in sales customer master."""
    await _require_any_perm(user, "sales.customer.manage")
    cust = await _create_sales_customer(db, payload, user_email=user.get("email"))
    return {"customer": cust, "message": "Data pelanggan berhasil disimpan"}


@api_router.put("/sales-customers/by-key")
async def upsert_sales_customer_by_key_endpoint(
    payload: SalesCustomerByKeyUpdatePayload,
    user=Depends(current_user),
):
    """Update or materialize customer profile by canonical customer_key."""
    await _require_any_perm(user, "sales.customer.edit")
    cust = await _upsert_sales_customer_by_key(db, payload, user_email=user.get("email"))
    return {"customer": cust, "message": "Data pelanggan berhasil diperbarui"}


@api_router.patch("/sales-customers/by-key/status")
async def set_sales_customer_status_by_key_endpoint(
    payload: SalesCustomerStatusByKeyPayload,
    user=Depends(current_user),
):
    """Set Aktif/Nonaktif by canonical customer_key (materialize if needed)."""
    await _require_any_perm(user, "sales.customer.manage_status")
    cust = await _set_sales_customer_active_status_by_key(db, payload, user_email=user.get("email"))
    return {
        "customer": cust,
        "message": "Pelanggan diaktifkan kembali" if payload.is_active else "Pelanggan dinonaktifkan",
    }


@api_router.get("/sales-customers/{customer_id}")
async def get_sales_customer_endpoint(
    customer_id: str,
    user=Depends(current_user),
):
    """Get single sales customer by id."""
    reject_sales_pwa_bypass(user)
    await _require_any_perm(
        user,
        "sales.customer.view",
        "sales.customer.manage",
        "sales.customer.edit",
        "sales.customer.manage_status",
    )
    cust = await _get_sales_customer(db, customer_id)
    if not cust:
        raise HTTPException(404, "Pelanggan tidak ditemukan.")
    cust["is_active"] = _customer_is_active(cust)
    return {"customer": cust}


@api_router.put("/sales-customers/{customer_id}")
async def update_sales_customer_endpoint(
    customer_id: str,
    payload: SalesCustomerUpdatePayload,
    user=Depends(current_user),
):
    """Update sales customer by id. Requires Edit Pelanggan permission."""
    await _require_any_perm(user, "sales.customer.edit")
    cust = await _update_sales_customer(db, customer_id, payload, user_email=user.get("email"))
    return {"customer": cust, "message": "Data pelanggan berhasil diperbarui"}


@api_router.patch("/sales-customers/{customer_id}/status")
async def set_sales_customer_status_endpoint(
    customer_id: str,
    payload: SalesCustomerStatusPayload,
    user=Depends(current_user),
):
    """Set Aktif/Nonaktif. Same permission for deactivate and reactivate."""
    await _require_any_perm(user, "sales.customer.manage_status")
    cust = await _set_sales_customer_active_status(
        db, customer_id, payload.is_active, user_email=user.get("email"),
    )
    return {
        "customer": cust,
        "message": "Pelanggan diaktifkan kembali" if payload.is_active else "Pelanggan dinonaktifkan",
    }


@api_router.get("/sales-orders/repeat-source")
async def get_sales_order_repeat_source(
    customer_id: Optional[str] = None,
    order_id: Optional[str] = None,
    user=Depends(current_user),
):
    """Read-only Repeat Order source.

    Modes:
    - order_id: exact selected PO
    - customer_id: latest valid PO for canonical customer_id (name fallback only
      for still-unlinked legacy rows)

    Zero writes. Does not return raw order documents, artwork, or workflow fields.
    """
    await _require_any_perm(user, "sales_order.create", "po.edit", "po.import.view")
    oid = (order_id or "").strip()
    cid = (customer_id or "").strip()
    if not oid and not cid:
        raise HTTPException(400, "order_id atau customer_id wajib diisi.")

    if oid:
        source = await find_repeat_source_by_order_id(db, oid)
        if not source:
            return {"found": False, "mode": "order", "order_id": oid, "source": None}
        dto = build_repeat_source_dto(source)
        return {"found": True, "mode": "order", "order_id": oid, "source": dto}

    source = await find_latest_valid_repeat_source(db, cid)
    if not source:
        return {"found": False, "mode": "customer", "customer_id": cid, "source": None}

    master = await db.sales_customers.find_one({"id": cid}, {"_id": 0, "id": 1, "name": 1})
    master_name = (master or {}).get("name") or ""
    dto = stamp_canonical_customer(build_repeat_source_dto(source), cid, master_name)
    if str(dto.get("customer_id") or "").strip() != cid:
        return {"found": False, "mode": "customer", "customer_id": cid, "source": None}

    return {"found": True, "mode": "customer", "customer_id": cid, "source": dto}


@api_router.get("/sales-orders/options")
async def get_sales_order_options(user=Depends(current_user)):
    """Fetch autocomplete suggestions for Customer, Outlet, Sales, Master Items, and next No PO."""
    await _require_any_perm(user, "sales_order.view", "sales_order.create", "po.view", "po.edit", "po.import.view")
    master_customers = await _list_sales_customers(db)
    customers = await db.orders.distinct("customer", {"cancelled": {"$ne": True}})
    outlet_custs = await db.delivery_outlets.distinct("customer_name")
    master_cust_names = [c["name"] for c in master_customers if c.get("name")]
    all_customers = sorted({c.strip() for c in (customers + outlet_custs + master_cust_names) if c and str(c).strip()})

    raw_outlets = await db.delivery_outlets.find({}, {"_id": 0}).to_list(1000)
    recent_orders = await db.orders.find(
        {"cancelled": {"$ne": True}},
        {"_id": 0, "customer": 1, "outlet": 1, "recipient": 1, "address": 1, "delivery_zone": 1, "sales": 1, "payment_terms": 1}
    ).sort("created_at", -1).limit(500).to_list(500)

    outlets_by_customer: Dict[str, List[dict]] = {}
    for o in raw_outlets:
        cname = (o.get("customer_name") or "").strip()
        if cname:
            outlets_by_customer.setdefault(cname, []).append({
                "id": str(o.get("id") or ""),
                "outlet": o.get("name") or "",
                "address": o.get("formatted_address") or "",
                "recipient": "",
                "delivery_zone": "",
            })
    for ro in recent_orders:
        cname = (ro.get("customer") or "").strip()
        out_name = (ro.get("outlet") or "").strip()
        if cname:
            existing_list = outlets_by_customer.setdefault(cname, [])
            if not any(x.get("outlet") == out_name for x in existing_list):
                existing_list.append({
                    "outlet": out_name,
                    "address": ro.get("address") or "",
                    "recipient": ro.get("recipient") or "",
                    "delivery_zone": ro.get("delivery_zone") or "",
                    "sales": ro.get("sales") or "",
                    "payment_terms": ro.get("payment_terms") or "",
                })

    sales_from_orders = await db.orders.distinct("sales", {"cancelled": {"$ne": True}})
    sales_users = await db.users.find(
        {"role": {"$in": ["admin", "crm", "head_production", "head_operations"]}, "status": {"$ne": "inactive"}},
        {"_id": 0, "name": 1, "email": 1}
    ).to_list(200)
    all_sales = sorted({s.strip() for s in sales_from_orders if s and str(s).strip()} | {u["name"] for u in sales_users if u.get("name")})

    master_items = await db.master_items.find(
        {"is_active": True},
        {"_id": 0, "id": 1, "accurate_item_id": 1, "item_code": 1, "item_name": 1, "unit": 1, "item_type": 1}
    ).sort("item_name", 1).to_list(1000)

    suggested_po = await _next_suggested_po_number(db)
    return {
        "customers": all_customers,
        "master_customers": master_customers,
        "outlets_by_customer": outlets_by_customer,
        "sales_options": all_sales,
        "master_items": master_items,
        "suggested_po_number": suggested_po,
    }


@api_router.post("/sales-orders")
async def create_sales_order(payload: SalesOrderCreatePayload, user=Depends(current_user)):
    """Create a new Sales Order (Pesanan Penjualan) manually.
    
    Generates a normal PO document with stage 0 (Order Masuk), fully compatible with
    imported POs and downstream production workflows.
    """
    await _require_any_perm(user, "sales_order.create", "po.edit", "po.import.view")
    
    cust = (payload.customer or "").strip()
    if not cust:
        raise HTTPException(400, "Customer wajib diisi.")
    
    if not payload.line_items:
        raise HTTPException(400, "Minimal 1 line item.")
    
    normalized_lines = []
    for i, li in enumerate(payload.line_items):
        name = (li.name or "").strip()
        if not name:
            raise HTTPException(400, f"Line item #{i+1}: Nama barang/jasa wajib diisi.")
        try:
            q = int(li.quantity)
        except (TypeError, ValueError):
            q = 0
        if q <= 0:
            raise HTTPException(400, f"Line item #{i+1}: Quantity harus > 0.")
        
        proc = (li.production_process or "").strip().lower()
        if proc not in ("print", "non_print"):
            proc = "print"
        
        normalized_lines.append({
            "item_code": (li.item_code or "").strip() or None,
            "name": name,
            "quantity": q,
            "unit": (li.unit or "pcs").strip() or "pcs",
            "color": (li.color or "").strip() or None,
            "production_process": proc,
            "notes": (li.notes or "").strip() or None,
            "accurate_item_id": (li.accurate_item_id or "").strip() or None,
            "master_item_id": (li.master_item_id or "").strip() or None,
            "unit_price": li.unit_price,
            "price_source": li.price_source,
            "price_review": bool(li.price_review),
        })
    
    order_date = _parse_order_date_iso(payload.order_date, default_days_ahead=0)
    deadline = _parse_order_date_iso(payload.deadline or payload.estimated_delivery, default_days_ahead=3)
    estimated_delivery = _parse_order_date_iso(payload.estimated_delivery or payload.deadline, default_days_ahead=3)
    # Legacy ``deadline`` on create is retained for historical fields only.
    # Authoritative Tanggal Pengiriman is resolved after required approvals
    # (or when set manually in Detail Informasi PO) — do not treat the create
    # form default date as a permanent manual override.

    po_num = (payload.po_number or "").strip()
    if not po_num:
        for _ in range(5):
            candidate = await _next_suggested_po_number(db, target_date=order_date)
            if not await db.orders.find_one({"po_number": candidate}, {"_id": 1}):
                po_num = candidate
                break
        if not po_num:
            po_num = candidate

    # Duplicate check
    existing = await db.orders.find_one({"po_number": po_num}, {"_id": 0, "id": 1, "po_number": 1})
    if existing:
        raise HTTPException(409, {
            "message": f"Nomor PO '{po_num}' sudah pernah digunakan.",
            "existing_po_number": po_num,
            "existing_po_id": existing.get("id"),
        })

    # Prepare customer (does NOT persist new customer yet)
    customer_id, customer_name, is_new, candidate_customer_doc = await _prepare_customer_for_po(
        db,
        customer_name=cust,
        customer_id=payload.customer_id,
        phone=payload.customer_phone,
        address=payload.customer_address or payload.address,
        pic=payload.customer_pic or payload.recipient,
        email=payload.customer_email,
        city=payload.customer_city,
        user_email=user.get("email"),
    )

    from services.sales_order_prices import price_order_lines
    normalized_lines, selling_total, selling_prices_complete = await price_order_lines(db, normalized_lines)

    now = now_iso()
    total_qty = sum(li["quantity"] for li in normalized_lines)
    item_cnt = len(normalized_lines)
    order_id = str(uuid.uuid4())

    seeded_design_files = []
    _ALLOWED_ATT_MIME = {
        "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
        "application/pdf",
    }
    for df in payload.design_files or []:
        data_url = (df.data_url or "").strip()
        if not data_url.startswith("data:"):
            raise HTTPException(400, "Design file: format data URL tidak valid")
        if len(data_url) > 8_000_000:
            raise HTTPException(400, "Design file terlalu besar (maks ~6MB)")
        try:
            detected_ct = data_url[:40].split("data:", 1)[1].split(";", 1)[0].lower()
        except Exception:
            detected_ct = ""
        if detected_ct not in _ALLOWED_ATT_MIME:
            raise HTTPException(
                400,
                f"Design file tipe tidak didukung ({detected_ct or 'unknown'}). Gunakan JPG, PNG, WEBP, GIF, atau PDF.",
            )
        seeded_design_files.append({
            "id": str(uuid.uuid4()),
            "name": (df.name or "").strip() or "design",
            "data_url": data_url,
            "content_type": detected_ct,
            "uploaded_by": user.get("email"),
            "uploaded_by_role": user.get("role"),
            "uploaded_by_name": user.get("name"),
            "uploaded_at": now,
        })

    new_order = {
        "id": order_id,
        "po_number": po_num,
        "accurate_id": None,
        "accurate_synced_at": None,
        "accurate_source": "manual",
        "import_source": "sales_order_manual",
        "import_filename": None,
        "source_pdf": None,
        "order_date": order_date,
        "estimated_delivery": estimated_delivery,
        "deadline": deadline,
        "priority": "normal",
        "urgency_approved_by": None,
        "customer": customer_name,
        "customer_id": customer_id,
        "customer_phone": (payload.customer_phone or "").strip() or None,
        "outlet": (payload.outlet or "").strip() or None,
        "recipient": (payload.recipient or "").strip() or None,
        "address": (payload.address or "").strip() or None,
        "payment_terms": (payload.payment_terms or "").strip() or None,
        "payment_status": "Waiting",
        "design_status": "Waiting",
        "sales": (payload.sales or "").strip() or None,
        "created_by_user": (payload.created_by_user or "").strip() or user.get("name") or user.get("email"),
        "imported_by": user.get("email"),
        "imported_by_name": user.get("name"),
        "imported_at": now,
        "expedition": (payload.expedition or "").strip() or None,
        "delivery_zone": (payload.delivery_zone or "").strip() or None,
        "seller": (payload.sales or "").strip() or None,
        "line_items": normalized_lines,
        "total_quantity": total_qty,
        "item_count": item_cnt,
        "selling_total": selling_total,
        "selling_prices_complete": selling_prices_complete,
        "operator_splits": [],
        "design_files": seeded_design_files,
        "sample_files": [],
        "delivery_proofs": [],
        "notes": (payload.notes or "").strip(),
        "current_stage": 0,
        "status_label": STAGES[0],
        "packing_status": "Belum",
        "delivery_status": "Belum Terkirim",
        "delivery_note_number": "",
        "loading_checklist": None,
        "timeline": [{
            "stage": 0,
            "stage_label": STAGES[0],
            "at": now,
            "by_email": user.get("email"),
            "by_role": user.get("role"),
            "by_name": user.get("name"),
            "note": "Pesanan Penjualan dibuat manual",
        }],
        "created_at": now,
        "updated_at": now,
    }

    from services.physical_stock_engine import attach_order_stock_snapshot
    stock_warnings = await validate_sales_order_availability(db, new_order.get("line_items") or [])
    await attach_order_stock_snapshot(db, new_order)
    await attach_sales_ownership(db, new_order)

    try:
        await db.orders.insert_one(new_order)
    except DuplicateKeyError:
        raise HTTPException(409, {
            "message": f"Nomor PO '{po_num}' sudah pernah digunakan.",
            "existing_po_number": po_num,
        })

    # If new customer was prepared, link/persist customer master immediately after PO persistence
    if is_new and candidate_customer_doc:
        final_customer_id = await _link_or_create_customer_for_po(
            db,
            order_id=order_id,
            candidate_doc=candidate_customer_doc,
        )
        new_order["customer_id"] = final_customer_id

    new_order.pop("_id", None)
    await _audit_po(new_order, "sales_order.create", user, note=f"Buat Pesanan Penjualan {po_num}")
    return {
        "order": new_order,
        "message": "Pesanan Penjualan berhasil dibuat",
        "warnings": list(stock_warnings or []),
    }


class OrderInfoUpdatePayload(BaseModel):
    """Field-scoped Informasi PO edit — address / Tanggal Pengiriman / estimasi.

    ``tanggal_pengiriman`` is the authoritative manual delivery date.
    Legacy ``deadline`` is accepted only for backward-compatible clients and is
    mapped to Tanggal Pengiriman (never used as a competing schedule source).
    """
    address: Optional[str] = None
    tanggal_pengiriman: Optional[str] = None
    deadline: Optional[str] = None  # legacy alias → tanggal_pengiriman
    estimated_delivery: Optional[str] = None


@api_router.patch("/orders/{order_id}/info")
async def update_order_info(order_id: str, payload: OrderInfoUpdatePayload, user=Depends(current_user)):
    """Update Alamat / Tanggal Pengiriman / Estimasi Kirim on Detail PO.

    Permission: po.edit (existing). Field-scoped $set — does not touch
    workflow quantities, delivery_location, or line items.

    Tanggal Pengiriman (manual) becomes the authoritative schedule date and is
    written to delivery_deadline_manual + scheduled_delivery_date. Clearing it
    falls back to automatic latest-approval + lead days.
    """
    await _require_perm(user, "po.edit")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            400,
            "Tidak ada field yang diubah. Kirim address, tanggal_pengiriman, dan/atau estimated_delivery.",
        )

    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)

    from services.tanggal_pengiriman import (
        apply_manual_tanggal_pengiriman,
        clear_manual_tanggal_pengiriman,
        parse_calendar_date,
    )

    before = {
        "address": order.get("address"),
        "deadline": order.get("deadline"),
        "estimated_delivery": order.get("estimated_delivery"),
        "delivery_deadline_manual": order.get("delivery_deadline_manual"),
        "scheduled_delivery_date": order.get("scheduled_delivery_date"),
        "tanggal_pengiriman": order.get("scheduled_delivery_date")
        or (str(order.get("delivery_deadline_manual") or "")[:10] or None),
    }
    patch: dict = {}
    if "address" in data:
        patch["address"] = (data.get("address") or "").strip() or None

    tanggal_raw = None
    tanggal_provided = False
    if "tanggal_pengiriman" in data:
        tanggal_provided = True
        tanggal_raw = data.get("tanggal_pengiriman")
    elif "deadline" in data:
        # Legacy clients still posting deadline → treat as Tanggal Pengiriman.
        tanggal_provided = True
        tanggal_raw = data.get("deadline")

    if tanggal_provided:
        raw = (str(tanggal_raw).strip() if tanggal_raw is not None else "")
        if not raw:
            clear_manual_tanggal_pengiriman(order, user=user)
        else:
            key = parse_calendar_date(raw)
            if not key:
                raise HTTPException(400, "Tanggal Pengiriman tidak valid")
            apply_manual_tanggal_pengiriman(order, key, user=user, reason="Detail Informasi PO")
            # Keep legacy deadline column in sync for historical exports only —
            # it is not the scheduling source.
            patch["deadline"] = _parse_order_date_iso(key, default_days_ahead=0)

    if "estimated_delivery" in data:
        raw = data.get("estimated_delivery")
        if raw is None or not str(raw).strip():
            raise HTTPException(400, "Estimasi kirim tidak boleh kosong")
        patch["estimated_delivery"] = _parse_order_date_iso(str(raw).strip(), default_days_ahead=3)

    for k, v in patch.items():
        order[k] = v

    note_bits = []
    if "address" in patch and patch.get("address") != before.get("address"):
        note_bits.append("alamat")
    if tanggal_provided:
        note_bits.append("tanggal pengiriman")
    if "estimated_delivery" in patch and patch.get("estimated_delivery") != before.get("estimated_delivery"):
        note_bits.append("estimasi kirim")
    note = "Edit Informasi PO: " + (", ".join(note_bits) if note_bits else "tanpa perubahan nilai")
    add_timeline(order, order.get("current_stage") or 0, user, note=note)

    persist_keys = list(patch.keys()) + [
        "timeline",
        "delivery_deadline_manual",
        "delivery_deadline_manual_by",
        "delivery_deadline_manual_by_name",
        "delivery_deadline_manual_at",
        "delivery_deadline_manual_reason",
        "delivery_deadline_history",
        "scheduled_delivery_date",
    ]
    await _persist_order(order, persist_keys=tuple(dict.fromkeys(persist_keys)))
    dd_settings = await _get_delivery_deadline_settings()
    _attach_deadline_snapshot(order, dd_settings["lead_time_days"])
    attach_schedule_snapshot(order, dd_settings["lead_time_days"])
    await _audit_po(
        order,
        "po.info.update",
        user,
        before=before,
        after={
            "address": order.get("address"),
            "deadline": order.get("deadline"),
            "estimated_delivery": order.get("estimated_delivery"),
            "tanggal_pengiriman": order.get("tanggal_pengiriman"),
            "delivery_deadline_manual": order.get("delivery_deadline_manual"),
            "scheduled_delivery_date": order.get("scheduled_delivery_date"),
        },
        note=note,
    )
    return {"order": order}


class OrderSalesUpdatePayload(BaseModel):
    """Reassignment of Accurate sales label. Does not guess users.id."""
    sales: Optional[str] = None


@api_router.patch("/orders/{order_id}/sales")
async def update_order_sales(order_id: str, payload: OrderSalesUpdatePayload, user=Depends(current_user)):
    """Update the display label `orders.sales` and re-resolve `sales_user_id`.

    Unmatched labels explicitly clear sales_user_id. The label itself is kept
    as submitted (including empty). Permission: po.edit or sales_order.create.
    """
    await _require_any_perm(user, "po.edit", "sales_order.create")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    before = {
        "sales": order.get("sales"),
        "sales_user_id": order.get("sales_user_id"),
    }
    new_label = (payload.sales or "").strip() or None
    order["sales"] = new_label
    await attach_sales_ownership(db, order)
    add_timeline(order, order.get("current_stage") or 0, user, note="Edit Sales")
    await _persist_order(order, persist_keys=("sales", "sales_user_id", "sales_ownership_status", "sales_needs_verification", "timeline"))
    after = {
        "sales": order.get("sales"),
        "sales_user_id": order.get("sales_user_id"),
    }
    await _audit_po(order, "orders.sales.update", user, before=before, after=after, note="Edit / reassignment Sales")
    return {"order": order}


class LineProductReplacePayload(BaseModel):
    """Atomic order-line edit: canonical catalog identity and/or quantity."""
    master_item_id: Optional[str] = None
    accurate_item_id: Optional[str] = None
    quantity: Optional[int] = None
    # Optional explicit process — if provided must match source line class.
    production_process: Optional[str] = None


@api_router.patch("/sales-orders/{order_id}/lines/{line_idx}/product")
async def replace_sales_order_line_product(
    order_id: str,
    line_idx: int,
    payload: LineProductReplacePayload,
    user=Depends(current_user),
):
    """Edit product identity and/or quantity on one exact order line.

    Permission: po.edit (not production.route.edit).
    Preserves line_idx; never rewrites SJ/loading/delivery/stock history.
    """
    await _require_perm(user, "po.edit")

    from services.line_product_replacement import (
        apply_product_fields_to_line,
        evaluate_all_line_product_replacements,
        evaluate_line_product_replacement,
        line_production_process,
        map_master_to_line_fields,
        current_product_snapshot,
        reject_cross_type_explicit,
        resolve_replacement_master,
        REASON_CROSS_TYPE,
        MSG_CROSS_TYPE,
        REASON_SAME_PRODUCT,
        MSG_SAME,
    )

    # Authoritative re-load at write time (race safety).
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)

    lis = order.get("line_items") or []
    if line_idx < 0 or line_idx >= len(lis) or not isinstance(lis[line_idx], dict):
        raise HTTPException(400, "Line item tidak ditemukan.")

    source_li = lis[line_idx]
    source_process = line_production_process(source_li)
    reject_cross_type_explicit(source_process, payload.production_process)

    target_requested = bool(
        (payload.master_item_id or "").strip()
        or (payload.accurate_item_id or "").strip()
    )
    if payload.quantity is not None and payload.quantity <= 0:
        raise HTTPException(400, "Qty baru harus lebih dari 0.")
    if not target_requested and payload.quantity is None:
        raise HTTPException(400, "Pilih barang pengganti atau ubah qty.")

    guard = await evaluate_line_product_replacement(db, order, line_idx)
    if not guard["allowed"]:
        raise HTTPException(
            409,
            {
                "code": guard["reason_code"],
                "message": guard["reason_message"],
                "line_idx": guard["line_idx"],
                "production_process": guard["production_process"],
                "downstream_sources": guard.get("downstream_sources") or [],
            },
        )

    mapped = None
    if target_requested:
        master = await resolve_replacement_master(
            db,
            master_item_id=payload.master_item_id,
            accurate_item_id=payload.accurate_item_id,
        )
        master_proc = (master.get("production_process") or "").strip().lower()
        if master_proc in ("print", "non_print") and master_proc != source_process:
            raise HTTPException(400, {"code": REASON_CROSS_TYPE, "message": MSG_CROSS_TYPE})

        mapped = map_master_to_line_fields(master)
        if not mapped.get("name"):
            raise HTTPException(400, "Produk target tidak memiliki nama yang valid.")
        if not mapped.get("accurate_item_id") and not mapped.get("master_item_id"):
            raise HTTPException(400, "Produk target tidak memiliki identitas kanonis.")

    before = current_product_snapshot(source_li)
    same_aid = bool(target_requested and (
        before.get("accurate_item_id")
        and mapped.get("accurate_item_id")
        and str(before["accurate_item_id"]) == str(mapped["accurate_item_id"])
    ))
    same_mid = bool(target_requested and (
        before.get("master_item_id")
        and mapped.get("master_item_id")
        and str(before["master_item_id"]) == str(mapped["master_item_id"])
        and not mapped.get("accurate_item_id")
        and not before.get("accurate_item_id")
    ))
    product_changed = target_requested and not (same_aid or same_mid)
    current_qty = int(source_li.get("quantity") or 0)
    quantity_changed = payload.quantity is not None and payload.quantity != current_qty
    if not product_changed and not quantity_changed:
        raise HTTPException(400, {"code": REASON_SAME_PRODUCT, "message": MSG_SAME})

    # Final race re-check immediately before persist
    fresh = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not fresh:
        raise HTTPException(404, "PO tidak ditemukan")
    race = await evaluate_line_product_replacement(db, fresh, line_idx)
    if not race["allowed"]:
        raise HTTPException(
            409,
            {
                "code": race["reason_code"],
                "message": race["reason_message"],
                "line_idx": race["line_idx"],
                "production_process": race["production_process"],
                "downstream_sources": race.get("downstream_sources") or [],
            },
        )
    fresh_lis = list(fresh.get("line_items") or [])
    if line_idx >= len(fresh_lis) or not isinstance(fresh_lis[line_idx], dict):
        raise HTTPException(400, "Line item tidak ditemukan.")

    fresh_line = fresh_lis[line_idx]
    mutated = apply_product_fields_to_line(fresh_line, mapped) if product_changed else dict(fresh_line)
    for keep in ("production_process", "color", "notes", "id", "line_id", "accurate_line_id", "color_source"):
        if keep in fresh_lis[line_idx]:
            mutated[keep] = fresh_lis[line_idx].get(keep)
    mutated["quantity"] = payload.quantity if payload.quantity is not None else fresh_line.get("quantity")
    if line_production_process(mutated) != source_process:
        raise HTTPException(400, {"code": REASON_CROSS_TYPE, "message": MSG_CROSS_TYPE})

    fresh_lis[line_idx] = mutated
    fresh["line_items"] = fresh_lis
    fresh["total_quantity"] = sum(int((line or {}).get("quantity") or 0) for line in fresh_lis)
    fresh["_expected_updated_at"] = fresh.get("updated_at")
    await _persist_order(fresh, require_lock=True)
    after = current_product_snapshot(mutated)
    await _audit_po(
        fresh,
        "sales_order.line_item.update",
        user,
        before={"line_idx": line_idx, "product": before, "quantity": current_qty},
        after={"line_idx": line_idx, "product": after, "quantity": mutated["quantity"]},
        note=f"Ubah item PO line_idx={line_idx}",
    )

    eligibility = await evaluate_all_line_product_replacements(db, fresh)
    fresh["line_product_replace"] = eligibility
    return {
        "order": fresh,
        "line_idx": line_idx,
        "line": mutated,
        "before": before,
        "after": after,
        "before_quantity": current_qty,
        "after_quantity": mutated["quantity"],
        "eligibility": eligibility[line_idx] if line_idx < len(eligibility) else None,
        "message": "Barang dan qty berhasil diperbarui" if product_changed and quantity_changed else (
            "Barang berhasil diganti" if product_changed else "Qty berhasil diperbarui"
        ),
    }


def _quantity_by_unit(lines) -> List[dict]:
    """Sum quantity per stored unit. Different units are not folded into pcs."""
    totals: Dict[str, int] = {}
    order: List[str] = []
    for li in lines or []:
        if not isinstance(li, dict):
            continue
        unit = str(li.get("unit") or "").strip() or "pcs"
        try:
            qty = int(li.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        if unit not in totals:
            order.append(unit)
            totals[unit] = 0
        totals[unit] += qty
    return [{"unit": unit, "quantity": totals[unit]} for unit in order]


def _mongo_array_nonempty_expr(field: str) -> dict:
    """True when `field` is a non-empty array, without returning array contents."""
    return {
        "$gt": [
            {
                "$size": {
                    "$cond": [
                        {"$isArray": f"${field}"},
                        f"${field}",
                        [],
                    ]
                }
            },
            0,
        ]
    }


_SALES_ORDER_LIST_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "order_date": 1,
    "created_at": 1,
    "customer": 1,
    "outlet": 1,
    "total_quantity": 1,
    "sales": 1,
    "sales_user": 1,
    "status_label": 1,
    "current_stage": 1,
    "cancelled": 1,
    "design_status": 1,
    "design_bypassed": 1,
    "payment_status": 1,
    "payment_bypassed": 1,
    "design_approved_at": 1,
    "design_bypassed_at": 1,
    "payment_approved_at": 1,
    "payment_bypassed_at": 1,
    "delivery_deadline_manual": 1,
    "buffer_stock": 1,
    "delivery_status": 1,
    "_has_design_files": _mongo_array_nonempty_expr("design_files"),
    "_has_payment_files": _mongo_array_nonempty_expr("payment_files"),
    "line_items": {
        "$map": {
            "input": {"$ifNull": ["$line_items", []]},
            "as": "li",
            "in": {
                "quantity": "$$li.quantity",
                "unit": "$$li.unit",
            },
        }
    },
}


def _sales_order_status_clause(status: Optional[str]) -> dict:
    """Existing list classifications. `histori` is selesai + batal, not a new state."""
    st = (status or "all").lower()
    if st == "cancelled":
        return {"cancelled": True}
    if st == "aktif":
        return {"cancelled": {"$ne": True}, "current_stage": {"$lt": 14}}
    if st == "selesai":
        return {"cancelled": {"$ne": True}, "current_stage": {"$gte": 14}}
    if st == "hold":
        return {"cancelled": {"$ne": True}}
    if st == "histori":
        return {"$or": [
            {"cancelled": True},
            {"cancelled": {"$ne": True}, "current_stage": {"$gte": 14}},
        ]}
    return {}


def _sales_order_date_clause(date_from: Optional[str], date_to: Optional[str]) -> dict:
    clause = {}
    start = (date_from or "").strip()[:10]
    end = (date_to or "").strip()[:10]
    if start:
        clause["$gte"] = start
    if end:
        clause["$lte"] = f"{end}T23:59:59.999999"
    return {"order_date": clause} if clause else {}


@api_router.post("/sales-orders/price-quote")
async def quote_sales_order_price(payload: SalesOrderPriceQuotePayload, user=Depends(current_user)):
    """Published selling price for one exact item, unit, and quantity tier.

    Returns no amount when the match is missing or ambiguous.
    HPP and the tier matrix are not included.
    """
    reject_sales_pwa_bypass(user)
    await _require_any_perm(user, "sales_order.view", "sales_order.create", "po.view", "po.edit")
    from services.sales_order_prices import quote_selling_price
    return await quote_selling_price(
        db,
        accurate_item_id=payload.accurate_item_id,
        unit=payload.unit,
        quantity=payload.quantity,
    )


@api_router.get("/sales-orders/item-images")
async def list_sales_order_item_images(
    accurate_item_ids: Optional[str] = None,
    master_item_ids: Optional[str] = None,
    user=Depends(current_user),
):
    """Batch product-photo URLs for sales order lines. No price or HPP fields."""
    reject_sales_pwa_bypass(user)
    await _require_any_perm(user, "sales_order.view", "sales_order.create", "po.view", "po.edit")
    from services.sales_order_item_images import (
        public_image_map,
        resolve_product_image_refs,
        split_id_param,
    )
    refs = await resolve_product_image_refs(
        db,
        accurate_item_ids=split_id_param(accurate_item_ids),
        master_item_ids=split_id_param(master_item_ids),
    )
    return {"images": public_image_map(refs)}


@api_router.get("/sales-orders/item-images/{accurate_item_id}")
async def get_sales_order_item_image(
    accurate_item_id: str,
    user=Depends(current_user),
):
    """Serve one Customer Pricing product photo. Bytes only."""
    reject_sales_pwa_bypass(user)
    await _require_any_perm(user, "sales_order.view", "sales_order.create", "po.view", "po.edit")
    from fastapi.responses import Response as BinaryResponse
    from services.customer_pricing_catalog import get_product_image
    from services.sales_order_item_images import resolve_product_image_refs

    refs = await resolve_product_image_refs(db, accurate_item_ids=[accurate_item_id])
    ref = refs.get(str(accurate_item_id or "").strip())
    if not ref or not ref.get("group_id"):
        raise HTTPException(404, "Foto produk tidak tersedia.")
    data, content_type, _filename = await get_product_image(
        db, group_id=ref["group_id"], accurate_item_id=ref["accurate_item_id"]
    )
    return BinaryResponse(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@api_router.get("/sales-orders")
async def list_sales_orders(
    q: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    user=Depends(current_user),
):
    """List sales orders with search and status filtering for Pesanan Penjualan page."""
    reject_sales_pwa_bypass(user)
    await _require_any_perm(user, "sales_order.view", "sales_order.create", "po.view")

    clauses = []
    owner = sales_owner_match(user)
    if owner:
        clauses.append(owner)
    st = (status or "all").lower()
    status_clause = _sales_order_status_clause(st)
    if status_clause:
        clauses.append(status_clause)
    if q and q.strip():
        needle = re.escape(q.strip())
        clauses.append({"$or": [
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"customer": {"$regex": needle, "$options": "i"}},
            {"outlet": {"$regex": needle, "$options": "i"}},
            {"sales": {"$regex": needle, "$options": "i"}},
        ]})
    date_clause = _sales_order_date_clause(date_from, date_to)
    if date_clause:
        clauses.append(date_clause)
    query: dict = {"$and": clauses} if clauses else {}
    
    orders = await db.orders.aggregate([
        {"$match": query},
        {"$sort": {"created_at": -1}},
        {"$limit": 1000},
        {"$project": _SALES_ORDER_LIST_PROJECTION},
    ]).to_list(1000)
    dd_settings = await _get_delivery_deadline_settings()
    
    # Batch calculate delivery status summary for all returned orders
    po_ids = [o["id"] for o in orders if o.get("id")]
    shipped_aggregates: Dict[str, dict] = {}
    if po_ids:
        pipe = [
            {"$match": {"po_id": {"$in": po_ids}, "is_cancelled": {"$ne": True}}},
            {"$group": {"_id": "$po_id", "total_shipped": {"$sum": "$total_qty"}, "count": {"$sum": 1}}}
        ]
        shipped_aggregates = {doc["_id"]: doc async for doc in db.sales_deliveries.aggregate(pipe)}

    fetched_count = len(orders)
    for o in orders:
        o["quantity_by_unit"] = _quantity_by_unit(o.pop("line_items", None))
        o["design_files"] = [True] if o.pop("_has_design_files", False) else []
        o["payment_files"] = [True] if o.pop("_has_payment_files", False) else []
        _apply_hold_snapshot(o)
        _attach_deadline_snapshot(o, dd_settings["lead_time_days"])
        _strip_tracking_secret(o)
        o.pop("design_files", None)
        o.pop("payment_files", None)
        
        agg = shipped_aggregates.get(o.get("id"), {})
        tot_shipped = int(agg.get("total_shipped", 0))
        tot_ordered = int(o.get("total_quantity", 0))
        tot_remain = max(0, tot_ordered - tot_shipped)
        if o.get("cancelled"):
            deliv_st = "DIBATALKAN"
        elif tot_shipped == 0:
            deliv_st = "BELUM DIKIRIM"
        elif tot_remain > 0:
            deliv_st = "SEBAGIAN"
        else:
            deliv_st = "SELESAI"
        o["delivery_summary"] = {
            "total_ordered": tot_ordered,
            "total_shipped": tot_shipped,
            "total_remaining": tot_remain,
            "status": deliv_st,
            "shipment_count": int(agg.get("count", 0)),
        }
    
    if st == "hold":
        orders = [o for o in orders if o.get("hold")]
    elif st == "aktif":
        orders = [o for o in orders if not o.get("hold")]

    paging = page is not None or page_size is not None
    if not paging:
        return {"orders": orders, "count": len(orders)}

    size = page_size if page_size is not None else 10
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 10
    size = min(50, max(1, size))
    try:
        current = int(page or 1)
    except (TypeError, ValueError):
        current = 1
    current = max(1, current)
    total = len(orders)
    start = (current - 1) * size
    page_rows = orders[start:start + size]
    return {
        "orders": page_rows,
        "count": len(page_rows),
        "total": total,
        "page": current,
        "page_size": size,
        "capped": fetched_count >= 1000,
    }


# ==================================================
# SALES DELIVERY (PENGIRIMAN PESANAN) ENDPOINTS
# ==================================================
@api_router.get("/sales-deliveries")
async def list_sales_deliveries_endpoint(
    q: Optional[str] = None,
    po_id: Optional[str] = None,
    status: Optional[str] = None,
    user=Depends(current_user),
):
    """List sales deliveries (Pengiriman Pesanan) with search and status filtering."""
    reject_sales_pwa_bypass(user)
    await _require_any_perm(user, "sales.delivery.view", "sales_order.view", "po.view")
    if po_id:
        await ensure_owned_po_id(db, user, po_id)
    deliveries = await _list_sales_deliveries(db, q=q, po_id=po_id, status=status)
    return {"deliveries": deliveries, "count": len(deliveries)}


@api_router.get("/sales-deliveries/po/{po_id}/summary")
async def get_po_delivery_summary_endpoint(po_id: str, user=Depends(current_user)):
    """Get delivery summary, remaining line item quantities, and past shipments for a PO."""
    await _require_any_perm(user, "sales.delivery.view", "sales.delivery.create", "sales_order.view", "sales_order.create", "po.view")
    await ensure_owned_po_id(db, user, po_id)
    summary = await _get_po_delivery_summary(db, po_id)
    return summary


@api_router.post("/sales-deliveries")
async def create_sales_delivery_endpoint(payload: SalesDeliveryCreatePayload, user=Depends(current_user)):
    """Create a new Sales Delivery (Pengiriman Pesanan) backend-authoritatively."""
    await _require_any_perm(user, "sales.delivery.create", "sales_order.create", "po.edit")
    delivery = await _create_sales_delivery(db, payload.model_dump(), user)
    await _audit_po(
        {"id": delivery.get("po_id"), "po_number": delivery.get("po_number")},
        "sales.delivery.create",
        user,
        note=f"Buat Pengiriman Pesanan #{delivery.get('shipment_index')} {delivery.get('sj_number')} (Total {delivery.get('total_qty')} pcs)"
    )
    return {"delivery": delivery, "message": f"Pengiriman Pesanan {delivery.get('sj_number')} berhasil dibuat"}


@api_router.get("/sales-deliveries/{id}")
async def get_sales_delivery_endpoint(id: str, user=Depends(current_user)):
    """Get detail of a single sales delivery including PO summary and shipment history."""
    await _require_any_perm(user, "sales.delivery.view", "sales_order.view", "po.view")
    detail = await _get_sales_delivery_detail(db, id)
    await ensure_owned_po_id(db, user, (detail or {}).get("po_id") or ((detail or {}).get("delivery") or {}).get("po_id"))
    return detail


@api_router.get("/sales-deliveries/{id}/pdf")
async def get_sales_delivery_pdf_endpoint(id: str, user=Depends(current_user)):
    """Generate and return official PDF for Sales Delivery / Surat Jalan."""
    await _require_any_perm(user, "sales.delivery.print", "sales.delivery.view", "sales_order.view", "po.view")
    detail = await _get_sales_delivery_detail(db, id)
    await ensure_owned_po_id(db, user, ((detail or {}).get("delivery") or {}).get("po_id"))
    pdf_bytes = _generate_sales_delivery_pdf_bytes(detail["delivery"], detail.get("po_summary"))
    sj_num = str(detail["delivery"].get("sj_number", "SJ")).replace("/", "_").replace("\\", "_")
    filename = f"Pengiriman_Pesanan_{sj_num}.pdf"
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'}
    )


# ==================================================
# SALES INVOICE (FAKTUR PENJUALAN) ENDPOINTS
# ==================================================
@api_router.get("/sales-invoices")
async def list_sales_invoices_endpoint(
    q: Optional[str] = None,
    po_id: Optional[str] = None,
    user=Depends(current_user),
):
    """List sales invoices (Faktur Penjualan) with search and po_id filtering."""
    reject_sales_pwa_bypass(user)
    await _require_any_perm(user, "sales.invoice.view", "sales_order.view", "po.view", "sales.delivery.view")
    if po_id:
        await ensure_owned_po_id(db, user, po_id)
    invoices = await _list_sales_invoices(db, q=q, po_id=po_id)
    return {"invoices": invoices, "count": len(invoices)}


@api_router.get("/sales-invoices/charge-accounts")
async def list_sales_invoice_charge_accounts_endpoint(
    q: Optional[str] = None,
    user=Depends(current_user),
):
    """Search COA accounts that may be credited as Biaya Lainnya. Does not post or create accounts."""
    await _require_any_perm(
        user,
        "sales.invoice.create",
        "sales.invoice.edit",
        "sales_order.create",
        "po.edit",
    )
    items = await _list_charge_account_options(db, q=q)
    return {"items": items, "total": len(items)}


@api_router.get("/sales-invoices/po/{po_id}/preview")
async def get_po_invoice_preview_endpoint(po_id: str, user=Depends(current_user)):
    """Preview exact PO snapshot fields and line items for sales invoice creation."""
    await _require_any_perm(user, "sales.invoice.view", "sales.invoice.create", "sales_order.view", "sales_order.create", "po.view")
    await ensure_owned_po_id(db, user, po_id)
    preview = await _get_po_invoice_preview(db, po_id)
    return preview


@api_router.post("/sales-invoices")
async def create_sales_invoice_endpoint(payload: SalesInvoiceCreatePayload, user=Depends(current_user)):
    """Create a new Sales Invoice (Faktur Penjualan) backend-authoritatively."""
    await _require_any_perm(user, "sales.invoice.create", "sales_order.create", "po.edit")
    inv = await _create_sales_invoice(db, payload.model_dump(), user)
    return {"invoice": inv, "message": "Faktur Penjualan berhasil dibuat"}


@api_router.get("/sales-invoices/{id}")
async def get_sales_invoice_endpoint(id: str, user=Depends(current_user)):
    """Get detail of a single sales invoice."""
    await _require_any_perm(user, "sales.invoice.view", "sales_order.view", "po.view", "sales.delivery.view")
    inv = await _get_sales_invoice_detail(db, id)
    await ensure_owned_po_id(db, user, (inv or {}).get("po_id"))
    return {"invoice": inv}



@api_router.get("/sales-invoices/{id}/pdf")
async def get_sales_invoice_pdf_endpoint(id: str, user=Depends(current_user)):
    """Generate and return official PDF for Sales Invoice (Faktur Penjualan)."""
    await _require_any_perm(user, "sales.invoice.view", "sales_order.view", "po.view", "sales.delivery.view")
    detail = await _get_sales_invoice_detail(db, id)
    await ensure_owned_po_id(db, user, (detail or {}).get("po_id"))
    pdf_bytes = generate_sales_invoice_pdf_bytes(detail)
    inv_num = str(detail.get("invoice_number", "INV")).replace("/", "_").replace("\\", "_")
    filename = f"Faktur_Penjualan_{inv_num}.pdf"
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'}
    )


@api_router.put("/sales-invoices/{id}")
async def update_sales_invoice_endpoint(id: str, payload: SalesInvoiceUpdatePayload, user=Depends(current_user)):
    """Update unit prices or notes on an existing sales invoice."""
    await _require_any_perm(user, "sales.invoice.edit", "sales.invoice.create", "sales_order.create", "po.edit")
    inv = await _update_sales_invoice(db, id, payload.model_dump(), user)
    return {"invoice": inv, "message": "Faktur Penjualan berhasil diperbarui"}


@api_router.post("/sales-invoices/{id}/finalize")
async def finalize_sales_invoice_endpoint(id: str, user=Depends(current_user)):
    """Lock financial fields. Does not post a journal or change production workflow."""
    await _require_any_perm(user, "sales.invoice.edit", "sales.invoice.create", "sales_order.create", "po.edit")
    inv = await _finalize_sales_invoice(db, id, user)
    return {"invoice": inv, "message": "Faktur Penjualan berhasil difinalisasi"}


@api_router.post("/sales-invoices/{id}/cancel")
async def cancel_sales_invoice_endpoint(id: str, user=Depends(current_user)):
    """Cancel a finalized invoice with no receipts. Does not reverse a journal."""
    await _require_any_perm(user, "sales.invoice.edit", "sales.invoice.create", "sales_order.create", "po.edit")
    inv = await _cancel_sales_invoice(db, id, user)
    return {"invoice": inv, "message": "Faktur Penjualan dibatalkan"}


@api_router.post("/sales-invoices/{id}/post-accounting")
async def post_sales_invoice_accounting_endpoint(id: str, user=Depends(current_user)):
    """Explicit journal post for one finalized invoice. Does not run on GET or finalize."""
    await _require_perm(user, _SALES_ACCOUNTING_POST_PERM)
    inv = await _post_sales_invoice_accounting(db, id, user)
    return {"invoice": inv, "message": "Faktur Penjualan diposting ke accounting"}


@api_router.post("/sales-invoices/{id}/reverse-accounting")
async def reverse_sales_invoice_accounting_endpoint(
    id: str,
    payload: SalesInvoiceReverseAccountingPayload,
    user=Depends(current_user),
):
    """Reverse a posted sales invoice journal. Does not cascade receipt reversals."""
    await _require_perm(user, _SALES_ACCOUNTING_REVERSE_PERM)
    inv = await _reverse_sales_invoice_accounting(db, id, user, payload.reason)
    return {"invoice": inv, "message": "Faktur Penjualan di-reverse dari accounting"}


# ==================================================
# SALES RECEIPT (PENERIMAAN PENJUALAN) ENDPOINTS
# ==================================================
@api_router.get("/sales-receipts")
async def list_sales_receipts_endpoint(
    q: Optional[str] = None,
    invoice_id: Optional[str] = None,
    po_id: Optional[str] = None,
    user=Depends(current_user),
):
    """List sales receipts (Penerimaan Penjualan) with search and filters."""
    await _require_any_perm(user, "sales.receipt.view", "sales.invoice.view", "sales_order.view", "po.view")
    receipts = await _list_sales_receipts(db, q=q, invoice_id=invoice_id, po_id=po_id)
    return {"receipts": receipts, "count": len(receipts)}


@api_router.get("/sales-receipts/cash-bank-options")
async def list_sales_receipt_cash_bank_options_endpoint(user=Depends(current_user)):
    """Active Kas/Bank options for Penerimaan Penjualan. Does not require cash_bank.view."""
    await _require_any_perm(
        user,
        "sales.receipt.view",
        "sales.receipt.create",
        "sales.invoice.view",
        "sales_order.view",
        "po.view",
    )
    items = await _list_sales_receipt_cash_bank_options(db)
    return {"items": items, "total": len(items)}


@api_router.get("/sales-receipts/customer-credit")
async def list_sales_customer_credit_endpoint(
    customer_id: Optional[str] = None,
    invoice_id: Optional[str] = None,
    user=Depends(current_user),
):
    """Available posted customer credit. Does not post and does not treat a load failure as zero."""
    await _require_any_perm(user, "sales.receipt.view", "sales.receipt.create", "sales.invoice.view")
    if invoice_id:
        invoice = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0, "po_id": 1})
        if invoice:
            await ensure_owned_po_id(db, user, invoice.get("po_id"))
    return await _list_available_credit(db, customer_id=customer_id or "", invoice_id=invoice_id or "")


@api_router.post("/sales-receipts/customer-credit/applications")
async def apply_sales_customer_credit_endpoint(
    payload: SalesCustomerCreditApplyPayload,
    user=Depends(current_user),
):
    """Apply posted customer credit to one invoice. Posts Dr deposit / Cr AR. No cash line."""
    await _require_perm(user, _SALES_RECEIPT_ACCOUNTING_POST_PERM)
    return {
        "application": await _apply_customer_credit(db, payload.model_dump(), user),
        "message": "Saldo pelanggan dipakai untuk faktur.",
    }


@api_router.post("/sales-receipts/customer-credit/applications/{application_id}/reverse")
async def reverse_sales_customer_credit_endpoint(application_id: str, user=Depends(current_user)):
    """Reverse one credit application once and restore the source balance."""
    await _require_perm(user, _SALES_RECEIPT_ACCOUNTING_REVERSE_PERM)
    return {
        "application": await _reverse_customer_credit_application(db, application_id, user),
        "message": "Pemakaian saldo dibalik.",
    }


@api_router.get("/sales-receipts/{id}")
async def get_sales_receipt_endpoint(id: str, user=Depends(current_user)):
    """Get detail of a single sales receipt."""
    await _require_any_perm(user, "sales.receipt.view", "sales.invoice.view", "sales_order.view", "po.view")
    receipt = await _get_sales_receipt_detail(db, id)
    return {"receipt": receipt}


@api_router.post("/sales-receipts")
async def create_sales_receipt_endpoint(payload: SalesReceiptCreatePayload, user=Depends(current_user)):
    """Create a new Sales Receipt (Penerimaan Penjualan) backend-authoritatively."""
    await _require_any_perm(user, "sales.receipt.create", "sales.invoice.create", "sales_order.create", "po.edit")
    data = payload.model_dump()
    if data.get("payment_proof") is None:
        data.pop("payment_proof", None)
    receipt = await _create_sales_receipt(db, data, user)
    return {"receipt": receipt, "message": "Penerimaan Penjualan berhasil dibuat"}


@api_router.post("/sales-receipts/{id}/post-accounting")
async def post_sales_receipt_accounting_endpoint(
    id: str,
    payload: SalesReceiptPostAccountingPayload = SalesReceiptPostAccountingPayload(),
    user=Depends(current_user),
):
    """Explicit journal post for one sales receipt. Does not run on create."""
    await _require_perm(user, _SALES_RECEIPT_ACCOUNTING_POST_PERM)
    receipt = await _post_sales_receipt_accounting(db, id, user, cash_bank_id=payload.cash_bank_id)
    return {"receipt": receipt, "message": "Penerimaan Penjualan diposting ke accounting"}


@api_router.post("/sales-receipts/{id}/reverse-accounting")
async def reverse_sales_receipt_accounting_endpoint(id: str, user=Depends(current_user)):
    """Reverse a posted sales receipt journal and exclude it from invoice outstanding."""
    await _require_perm(user, _SALES_RECEIPT_ACCOUNTING_REVERSE_PERM)
    receipt = await _reverse_sales_receipt_accounting(db, id, user)
    return {"receipt": receipt, "message": "Penerimaan Penjualan di-reverse dari accounting"}


@api_router.post("/sales-receipts/{id}/payment-proof")
async def attach_sales_receipt_payment_proof_endpoint(
    id: str,
    payload: SalesReceiptAttachProofPayload,
    user=Depends(current_user),
):
    """Attach Bukti Payment to a receipt that has none. Idempotent if already present. No replace."""
    await _require_any_perm(user, "sales.receipt.create", "sales.invoice.create", "sales_order.create", "po.edit")
    receipt = await _attach_sales_receipt_payment_proof(db, id, payload.model_dump(), user)
    return {"receipt": receipt, "message": "Bukti Payment berhasil dilampirkan"}


@api_router.get("/sales-receipts/{id}/payment-proof")
async def get_sales_receipt_payment_proof_endpoint(
    id: str,
    download: bool = False,
    user=Depends(current_user),
):
    """Preview or download Bukti Payment. Authenticated proxy — never raw Object Storage URL.

    Pure read: does not mutate receipt, payment, workflow, or file metadata.
    """
    # Financial/receipt gate only — po.view alone must NOT open Bukti Payment.
    await _require_any_perm(
        user,
        "sales.receipt.view",
        "payment.view",
    )
    receipt_for_scope = await db.sales_receipts.find_one({"id": str(id)}, {"_id": 0, "po_id": 1})
    if not receipt_for_scope:
        receipt_for_scope = await db.sales_receipts.find_one({"receipt_number": str(id)}, {"_id": 0, "id": 1, "po_id": 1})
    if not receipt_for_scope:
        raise HTTPException(404, "Penerimaan Penjualan tidak ditemukan.")
    await ensure_owned_po_id(db, user, receipt_for_scope.get("po_id"))
    data, ct, filename = await _load_sales_receipt_payment_proof_bytes(db, receipt_for_scope.get("id") or id)
    headers = {
        "Content-Disposition": _sales_receipt_proof_content_disposition(filename, download=download),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    return FastAPIResponse(content=data, media_type=ct, headers=headers)


@api_router.get("/sales-receipts/{id}/pdf")
async def get_sales_receipt_pdf_endpoint(id: str, user=Depends(current_user)):
    """Generate and return official PDF for Sales Receipt (Penerimaan Penjualan)."""
    await _require_any_perm(user, "sales.receipt.view", "sales.invoice.view", "sales_order.view", "po.view")
    detail = await _get_sales_receipt_detail(db, id)
    pdf_bytes = generate_sales_receipt_pdf_bytes(detail)
    rec_num = str(detail.get("receipt_number", "PAY")).replace("/", "_").replace("\\", "_")
    filename = f"Penerimaan_Penjualan_{rec_num}.pdf"
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'}
    )


# ==================================================
# DELETE ENDPOINTS — SAFE DELETE WITH DEPENDENCY CHECKS
# ==================================================

async def _sales_delivery_delete_blocker(delivery: dict) -> Optional[str]:
    """Return a human-friendly reject reason, or None if the business SJ may be deleted."""
    po_id = delivery.get("po_id")
    sj_number = delivery.get("sj_number")
    delivery_id = delivery.get("id")
    if po_id:
        pay_count = await db.sales_receipts.count_documents({"po_id": po_id})
        if pay_count > 0:
            return "Hapus Penerimaan Penjualan terlebih dahulu."
    order = await db.orders.find_one({"id": po_id}, {"_id": 0}) if po_id else None
    if not order:
        return None

    for lc in (order.get("loading_confirmations") or []):
        if lc.get("sj_number") == sj_number or lc.get("sales_delivery_id") == delivery_id:
            return "Surat Jalan tidak dapat dihapus karena sudah memiliki proses Loading."

    for sh in (order.get("shipments") or []):
        same = (
            sh.get("sj_number") == sj_number
            or sh.get("number") == sj_number
            or sh.get("sales_delivery_id") == delivery_id
            or sh.get("id") == delivery_id
        )
        if not same:
            continue
        if (sh.get("loaded_qty") or 0) > 0:
            return "Surat Jalan tidak dapat dihapus karena sudah memiliki proses Loading."
        if (sh.get("delivered_qty") or 0) > 0 or sh.get("delivered") or sh.get("status") in ("Terkirim", "DELIVERED"):
            return "Surat Jalan tidak dapat dihapus karena sudah memiliki proses Pengiriman."

    proofs = [p for p in (order.get("delivery_proofs") or []) if not p.get("is_deleted")]
    if proofs:
        return "Surat Jalan tidak dapat dihapus karena sudah memiliki proses Pengiriman."
    if (order.get("delivery_status") or "") == "Terkirim":
        return "Surat Jalan tidak dapat dihapus karena sudah memiliki proses Pengiriman."

    for f in (order.get("surat_jalan_files") or []):
        if f.get("is_deleted"):
            continue
        if _sj_is_generated(f):
            continue
        if f.get("sj_number") == sj_number or f.get("delivery_note_number") == sj_number:
            return "Surat Jalan tidak dapat dihapus karena memiliki file lampiran."
    return None


async def _remove_matching_business_sj_shipment(po_id: str, delivery: dict) -> None:
    """Remove the exact business SJ shipment row and its system-generated PDF only.

    Soft-deletes the generated PDF blob for audit. Does not erase packing,
    loading, or delivery history. When no active SJ evidence remains, clears
    delivery_note_* stamps so stageflow no longer treats deleted history as
    an active Surat Jalan (same rule as remove_file_from_order).
    """
    if not po_id:
        return
    delivery_id = delivery.get("id")
    if delivery_id:
        await db.orders.update_one({"id": po_id}, {"$pull": {"shipments": {"sales_delivery_id": delivery_id}}})
        await db.orders.update_one({"id": po_id}, {"$pull": {"shipments": {"id": delivery_id}}})
        await db.orders.update_one(
            {"id": po_id},
            {"$pull": {"surat_jalan_files": {"id": delivery_id, "source": _SJ_SOURCE_GENERATED}}},
        )
        await db.files.update_many(
            {
                "kind": "surat_jalan",
                "order_id": po_id,
                "id": delivery_id,
                "source": _SJ_SOURCE_GENERATED,
                "is_deleted": {"$ne": True},
            },
            {"$set": {"is_deleted": True, "deleted_at": now_iso(), "deleted_reason": "sales.delivery.delete"}},
        )
    order = await db.orders.find_one(
        {"id": po_id},
        {"_id": 0, "shipments": 1, "surat_jalan_files": 1},
    )
    if not order:
        return
    shipments = [sh for sh in (order.get("shipments") or []) if isinstance(sh, dict)]
    files = [
        f for f in (order.get("surat_jalan_files") or [])
        if isinstance(f, dict) and not f.get("is_deleted")
    ]
    if shipments or files:
        return
    await db.orders.update_one(
        {"id": po_id},
        {"$set": {
            "delivery_note_number": None,
            "delivery_note_created_at": None,
            "delivery_note_created_by": None,
            "delivery_note_created_by_name": None,
        }},
    )


@api_router.delete("/sales-receipts/{id}")
async def delete_sales_receipt_endpoint(id: str, user=Depends(current_user)):
    """Delete a single sales receipt (Penerimaan Penjualan) with dependency safety."""
    await _require_any_perm(user, "sales.receipt.delete", "sales.receipt.create", "po.edit")
    result = await _delete_sales_receipt(db, id, user)
    await _write_audit(action="sales.receipt.delete", actor=user, target_email=f"receipt:{result.get('receipt_number')}",
                       before={"receipt_number": result.get("receipt_number"), "id": result.get("id")}, after={"deleted": True})
    return result


@api_router.post("/sales-receipts-bulk/delete")
async def bulk_delete_sales_receipts_endpoint(payload: BulkPayload, user=Depends(current_user)):
    """Bulk delete sales receipts — ALL OR NOTHING."""
    await _require_any_perm(user, "sales.receipt.delete", "sales.receipt.create", "po.edit")
    if not payload.ids:
        raise HTTPException(400, "Pilih minimal 1 Penerimaan.")
    result = await _bulk_delete_sales_receipts(db, payload.ids, user)
    await _write_audit(action="sales.receipt.bulk_delete", actor=user, target_email=f"receipts:{result['deleted_count']}",
                       before={"ids": payload.ids}, after=result)
    return result


@api_router.delete("/sales-invoices/{id}")
async def delete_sales_invoice_endpoint(id: str, user=Depends(current_user)):
    """Delete a single sales invoice (Faktur Penjualan) with dependency safety."""
    await _require_any_perm(user, "sales.invoice.delete", "sales.invoice.create", "po.edit")
    result = await _delete_sales_invoice(db, id, user)
    await _write_audit(action="sales.invoice.delete", actor=user, target_email=f"invoice:{result.get('invoice_number')}",
                       before={"invoice_number": result.get("invoice_number"), "id": result.get("id")}, after={"deleted": True})
    return result


@api_router.post("/sales-invoices-bulk/delete")
async def bulk_delete_sales_invoices_endpoint(payload: BulkPayload, user=Depends(current_user)):
    """Bulk delete sales invoices — ALL OR NOTHING."""
    await _require_any_perm(user, "sales.invoice.delete", "sales.invoice.create", "po.edit")
    if not payload.ids:
        raise HTTPException(400, "Pilih minimal 1 Faktur.")
    result = await _bulk_delete_sales_invoices(db, payload.ids, user)
    await _write_audit(action="sales.invoice.bulk_delete", actor=user, target_email=f"invoices:{result['deleted_count']}",
                       before={"ids": payload.ids}, after=result)
    return result


@api_router.delete("/sales-deliveries/{id}")
async def delete_sales_delivery_endpoint(id: str, user=Depends(current_user)):
    """Delete a single sales delivery (Surat Jalan) with dependency safety."""
    await _require_any_perm(user, "sales.delivery.delete", "sales.delivery.create", "po.edit")
    delivery = await db.sales_deliveries.find_one({"id": str(id)}, {"_id": 0})
    if not delivery:
        raise HTTPException(404, "Pengiriman Pesanan tidak ditemukan.")
    blocker = await _sales_delivery_delete_blocker(delivery)
    if blocker:
        raise HTTPException(400, blocker)
    sj_number = delivery.get("sj_number", delivery["id"])
    await db.sales_deliveries.delete_one({"id": delivery["id"]})
    await _remove_matching_business_sj_shipment(delivery.get("po_id"), delivery)
    await _write_audit(action="sales.delivery.delete", actor=user, target_email=f"delivery:{sj_number}",
                       before={"sj_number": sj_number, "id": delivery["id"]}, after={"deleted": True})
    return {"deleted": True, "sj_number": sj_number, "id": delivery["id"]}


@api_router.post("/sales-deliveries-bulk/delete")
async def bulk_delete_sales_deliveries_endpoint(payload: BulkPayload, user=Depends(current_user)):
    """Bulk delete sales deliveries — ALL OR NOTHING."""
    await _require_any_perm(user, "sales.delivery.delete", "sales.delivery.create", "po.edit")
    if not payload.ids:
        raise HTTPException(400, "Pilih minimal 1 Pengiriman.")
    unique_ids = list(dict.fromkeys(payload.ids))
    deliveries = await db.sales_deliveries.find({"id": {"$in": unique_ids}}, {"_id": 0}).to_list(len(unique_ids) or 1)
    by_id = {d["id"]: d for d in deliveries}
    blockers = []
    to_delete = []
    for did in unique_ids:
        delivery = by_id.get(did)
        if not delivery:
            blockers.append({"id": did, "number": did, "reason": "Pengiriman tidak ditemukan."})
            continue
        reason = await _sales_delivery_delete_blocker(delivery)
        if reason:
            blockers.append({"id": did, "number": delivery.get("sj_number", did), "reason": reason})
            continue
        to_delete.append(delivery)
    if blockers:
        raise HTTPException(400, {"message": "Tidak dapat menghapus. Beberapa pengiriman memiliki dependensi.", "blockers": blockers})
    if to_delete:
        delete_ids = [d["id"] for d in to_delete]
        await db.sales_deliveries.delete_many({"id": {"$in": delete_ids}})
        for d in to_delete:
            await _remove_matching_business_sj_shipment(d.get("po_id"), d)
    await _write_audit(action="sales.delivery.bulk_delete", actor=user, target_email=f"deliveries:{len(to_delete)}",
                       before={"ids": payload.ids}, after={"deleted_count": len(to_delete)})
    return {"deleted_count": len(to_delete), "deleted": [{"id": d["id"], "sj_number": d.get("sj_number", "")} for d in to_delete]}


# ---------- PDF PO import ----------
@api_router.post("/import/pdf/preview")
async def import_pdf_preview(file: UploadFile = File(...), user=Depends(require_perm("po.import.pdf"))):
    """Parse an uploaded PDF PO and return an editable preview. NO persistence."""
    fname = (file.filename or "").lower()
    if not fname.endswith(".pdf"):
        raise HTTPException(400, "Hanya file PDF yang diterima.")
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Ukuran file maksimum 8 MB")
    if not data[:5].startswith(b"%PDF-"):
        raise HTTPException(400, "File yang diunggah bukan PDF valid.")
    try:
        preview = parse_pdf_po(data, file.filename or "upload.pdf")
    except PDFParseError as e:
        raise HTTPException(400, str(e))
    # Upload the original PDF to Object Storage so the imported PO keeps
    # a full-fidelity source-of-truth. Never store the binary in Mongo.
    file_id = str(uuid.uuid4())
    safe_po = re.sub(r"[^A-Za-z0-9_\-]+", "-", (preview.get("po_number") or "unknown"))[:80] or "unknown"
    storage_path = f"{obj_storage.APP_NAME}/pdf-import/{safe_po}/{file_id}.pdf"
    try:
        result = obj_storage.put_object(storage_path, data, "application/pdf")
        preview["source_pdf"] = {
            "storage_path": result["path"],
            "original_filename": file.filename or "upload.pdf",
            "size": result.get("size") or len(data),
            "uploaded_at": now_iso(),
        }
    except Exception as e:
        logger.warning("PDF source upload skipped: %s", e)
        preview["source_pdf"] = None
    # Duplicate check based on parsed po_number (may be None → skip until user edits)
    existing = None
    if preview.get("po_number"):
        existing = await _find_existing_import({"po_number": preview["po_number"], "accurate_id": None})
    return {
        "preview": preview,
        "duplicate": bool(existing),
        "existing_po_id": existing.get("id") if existing else None,
        "existing_po_number": existing.get("po_number") if existing else None,
        "previous_imported_by": (existing.get("imported_by_name") or existing.get("imported_by")) if existing else None,
        "previous_imported_at": existing.get("imported_at") if existing else None,
        "previous_customer": existing.get("customer") if existing else None,
        "mode": "pdf",
    }


@api_router.get("/accurate/import/logs")
async def accurate_import_logs(limit: int = 100, user=Depends(require_perm("po.import.view"))):
    logs = await db.import_logs.find({}, {"_id": 0}).sort("imported_at", -1).to_list(min(limit, 500))
    return {"logs": logs, "count": len(logs)}


@api_router.get("/accurate/import/available")
async def accurate_available(user=Depends(require_perm("po.import.view"))):
    """Convenience: list mock SO numbers so admin can pick during testing.
    In live mode this simply returns an empty hint list (admins should search
    by SO number they already know)."""
    if _accurate_client.is_live:
        return {"mode": "live", "available": [], "note": "Live mode — cari langsung berdasarkan nomor SO Accurate."}
    from services.accurate import _MOCK_SALES_ORDERS as MOCK
    return {
        "mode": "mock",
        "available": [
            {"accurate_id": s.get("accurate_id"), "po_number": s.get("po_number"), "customer": s.get("customer"), "outlet": s.get("outlet")}
            for s in MOCK
        ],
    }


@api_router.get("/accurate/list")
async def accurate_list(
    limit: int = 20,
    refresh: bool = False,
    page: int = 1,
    keyword: str = "",
    user=Depends(require_perm("po.import.view")),
):
    """Return one page of Sales Orders (READ-ONLY) with import status per row.

    - `limit`: page size (1..100), default 20
    - `page` : 1-based
    - `keyword`: optional search — matched by Accurate `keywords` filter
      (nomor SO / customer). When Accurate returns rows, we further filter
      client-side by number/customer substring as a safety net.
    - `refresh=true`: bypass 60s cache

    Response fields: rows[], total, page, pageSize, pageCount, host, mode,
    (optional) error/message/detail on Accurate-side failure. Each row also
    has `import_status` ('none' | 'imported'), `existing_po_id`, and
    `existing_po_number` for badge rendering. Never returns credentials.
    """
    try:
        payload = await _accurate_client.list_recent_sales_orders(
            limit=limit, force=refresh, page=page, keyword=keyword
        )
    except Exception as e:
        msg = str(e)
        # Sanitize — never bubble credentials
        if _accurate_client.api_signature_secret and _accurate_client.api_signature_secret in msg:
            msg = "Accurate error"
        if _accurate_client.api_token and _accurate_client.api_token in msg:
            msg = "Accurate error"
        last_err = get_last_list_error()
        # Structured log for diagnosis (no credentials in this record)
        try:
            await db.accurate_errors.insert_one({
                "at": now_iso(),
                "operation": "sales-order/list.do",
                "detail": msg[:200],
                "tenant_message": last_err.get("message", "")[:200],
            })
        except Exception:
            pass
        return {
            "rows": [], "total": 0, "page": page, "pageSize": limit, "pageCount": 0,
            "host": _accurate_client.host,
            "mode": "live" if _accurate_client.is_live else "mock",
            "error": True,
            "message": "Daftar Sales Order gagal dimuat. Pencarian nomor SO tetap dapat digunakan.",
            "detail": msg[:200],
            "tenant_message": (last_err.get("message") or "")[:200],
        }

    # Enrich each row with import_status by matching accurate_id / po_number
    # against MongoDB `orders`.
    rows = payload.get("rows") or []
    if rows:
        acc_ids = [str(r.get("accurate_id")) for r in rows if r.get("accurate_id")]
        numbers = [r.get("number") for r in rows if r.get("number")]
        existing_docs = await db.orders.find(
            {"$or": [{"accurate_id": {"$in": acc_ids}}, {"po_number": {"$in": numbers}}]},
            {"_id": 0, "id": 1, "po_number": 1, "accurate_id": 1, "current_stage": 1, "status_label": 1, "cancelled": 1},
        ).to_list(500) if (acc_ids or numbers) else []
        by_acc = {str(d.get("accurate_id")): d for d in existing_docs if d.get("accurate_id")}
        by_num = {d.get("po_number"): d for d in existing_docs if d.get("po_number")}
        for r in rows:
            existing = by_acc.get(str(r.get("accurate_id"))) or by_num.get(r.get("number"))
            if existing:
                r["import_status"] = "imported"
                r["existing_po_id"] = existing.get("id")
                r["existing_po_number"] = existing.get("po_number")
                r["existing_stage_label"] = existing.get("status_label")
            else:
                r["import_status"] = "none"

    return payload


@api_router.get("/master-items")
async def master_items_get(
    q: str = "",
    item_type: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 50,
    picker: bool = False,
    user=Depends(current_user),
):
    """List Master Barang/Jasa with server-side search, filter, and pagination.

    picker=true is the autocomplete path: one limited find, no catalog summary.
    product_request.create may use picker only — not the full inventory catalog.
    """
    if picker:
        await _require_any_perm(user, "master_items.view", "product_request.create")
        return await list_master_items_picker(
            db, q=q, status=status, page_size=page_size,
        )
    await _require_any_perm(user, "master_items.view")
    return await list_master_items(
        db, q=q, item_type=item_type, status=status, page=page, page_size=page_size,
    )


@api_router.post("/master-items/sync")
async def master_items_sync(user=Depends(require_perm("master_items.sync"))):
    """Manual Accurate item sync. Same client/auth as Import PO. Never truncates."""
    try:
        result = await sync_master_items(db, _accurate_client)
    except SyncInProgressError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        log_master_items_sync_failure(_accurate_client, e)
        last_err = get_last_list_error()
        msg = sanitize_accurate_error(_accurate_client, str(e))
        try:
            await db.accurate_errors.insert_one({
                "at": now_iso(),
                "operation": "master_items_sync",
                "endpoint": (last_err.get("operation") or "item/list.do")[:80],
                "exception": type(e).__name__,
                "status": last_err.get("status"),
                "page": last_err.get("page"),
                "attempt": last_err.get("attempt"),
                "detail": msg[:200],
                "tenant_message": sanitize_accurate_error(
                    _accurate_client, last_err.get("message") or "",
                )[:200],
            })
        except Exception:
            pass
        raise HTTPException(
            502,
            "Gagal sinkronisasi Barang/Jasa dari Accurate. Data existing tidak diubah.",
        )
    return result


class UnitConversionPayload(BaseModel):
    transaction_uom: Optional[str] = None
    base_uom: Optional[str] = None
    conversion_factor: Optional[float] = None


@api_router.get("/master-items/{item_id}/detail")
async def master_item_detail_get(
    item_id: str,
    user=Depends(require_perm("master_items.view")),
):
    """Pure read. Returns product info, unit setting, and authoritative stock summary."""
    item = await db.master_items.find_one({"$or": [{"id": item_id}, {"accurate_item_id": item_id}]}, {"_id": 0})
    if not item:
        raise HTTPException(404, f"Produk '{item_id}' tidak ditemukan")

    mid = item["id"]
    from services.product_unit_conversion import get_product_unit_setting
    from services.product_stock_state import get_product_stock_state

    unit_setting = await get_product_unit_setting(db, mid)
    base_uom = (unit_setting or {}).get("base_uom") or item.get("unit") or "PCS"
    tx_uom = (unit_setting or {}).get("transaction_uom") or base_uom
    factor = (unit_setting or {}).get("conversion_factor") or 1.0

    stock_state = await get_product_stock_state(db, mid)
    if stock_state and stock_state.get("initialized") and stock_state.get("status") == "INITIALIZED":
        stock_summary = {
            "stock_total": stock_state.get("total_qty"),
            "stock_unprinted_cup": stock_state.get("unprinted_qty"),
            "stock_printed_cup": stock_state.get("printed_qty"),
            "base_uom": stock_state.get("base_uom") or base_uom,
            "transaction_uom": tx_uom,
            "conversion_factor": factor,
            "authority_status": "TERSEDIA",
            "initialized_at": stock_state.get("initialized_at"),
            "initialized_by": stock_state.get("initialized_by"),
        }
    else:
        stock_summary = {
            "stock_total": None,
            "stock_unprinted_cup": None,
            "stock_printed_cup": None,
            "base_uom": base_uom,
            "transaction_uom": tx_uom,
            "conversion_factor": factor,
            "authority_status": "BELUM_TERSEDIA",
        }

    return {
        "item": item,
        "unit_setting": unit_setting or {
            "master_item_id": mid,
            "accurate_item_id": item.get("accurate_item_id"),
            "transaction_uom": tx_uom,
            "base_uom": base_uom,
            "conversion_factor": factor,
        },
        "stock_summary": stock_summary,
        "stock_state": stock_state,
    }


@api_router.put("/master-items/{item_id}/unit-conversion")
async def master_item_unit_conversion_put(
    item_id: str,
    payload: UnitConversionPayload,
    user=Depends(current_user),
):
    """Save or update product unit conversion setting."""
    await _require_any_perm(user, "current_stock.update", "master_items.sync")
    from services.product_unit_conversion import save_product_unit_setting
    setting = await save_product_unit_setting(db, item_id, payload.model_dump(), user)
    return {"unit_setting": setting, "message": "Pengaturan konversi satuan berhasil disimpan"}


@api_router.post("/master-items/{item_id}/opening-balance")
async def master_item_opening_balance_post(
    item_id: str,
    payload: dict = Body(...),
    user=Depends(current_user),
):
    """Atomically initialize physical stock opening balance for an exact product."""
    await _require_any_perm(user, "current_stock.update", "permissions.manage")
    from services.product_stock_state import initialize_opening_balance
    res = await initialize_opening_balance(db, item_id, payload, user=user)
    return res


@api_router.post("/master-items/{item_id}/opening-balance/correction")
async def master_item_opening_balance_correction_post(
    item_id: str,
    payload: dict = Body(...),
    user=Depends(current_user),
):
    """HOLDING_TEAM-only: append KOREKSI to set current physical buckets. Does not rewrite SALDO_AWAL."""
    from services.product_stock_state import correct_opening_balance, require_holding_team
    require_holding_team(user)
    res = await correct_opening_balance(db, item_id, payload or {}, user=user)
    await _write_audit(
        "opening_balance.correction",
        user,
        target_email=f"item:{item_id}",
        before=res.get("old_qty"),
        after={
            "new_qty": res.get("new_qty"),
            "movement_id": (res.get("movement") or {}).get("id"),
            "idempotent": res.get("idempotent"),
        },
        note=str((payload or {}).get("note") or (payload or {}).get("reason") or ""),
    )
    return res


@api_router.post("/master-items/{item_id}/opening-balance/void")
async def master_item_opening_balance_void_post(
    item_id: str,
    user=Depends(current_user),
):
    """Void an unused mistaken SALDO_AWAL without deleting its audit record."""
    from services.product_stock_state import (
        OPENING_VOID_REASON,
        require_physical_stock_correction_access,
        void_opening_balance,
    )

    app_env = os.environ.get("APP_ENV")
    mongo_url = os.environ.get("MONGO_URL")
    require_physical_stock_correction_access(user, app_env, mongo_url)
    result = await void_opening_balance(
        db,
        item_id,
        user=user,
        app_env=app_env,
        mongo_url=mongo_url,
    )
    await _write_audit(
        "opening_balance.void",
        user,
        target_email=f"item:{item_id}",
        before=result.get("old_qty"),
        after={
            "state": result.get("stock_state"),
            "movement_id": (result.get("movement") or {}).get("id"),
            "movement_status": (result.get("movement") or {}).get("status"),
            "idempotent": result.get("idempotent"),
        },
        note=OPENING_VOID_REASON,
    )
    return result


@api_router.get("/master-items/{item_id}/mutations")
async def master_item_mutations_get(
    item_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    movement_type: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    user=Depends(require_perm("master_items.view")),
):
    """Pure read. Returns paginated stock movements for a product."""
    from services.stock_ledger import list_product_mutations
    return await list_product_mutations(
        db,
        item_id,
        start_date=start_date,
        end_date=end_date,
        movement_type=movement_type,
        q=q,
        page=page,
        page_size=page_size,
    )


@api_router.get("/master-items/{item_id}/mutations/{mutation_id}")
async def master_item_mutation_detail_get(
    item_id: str,
    mutation_id: str,
    user=Depends(require_perm("master_items.view")),
):
    """Pure read. Returns detail of a single stock movement."""
    from services.stock_ledger import get_mutation_detail
    detail = await get_mutation_detail(db, mutation_id)
    if not detail:
        raise HTTPException(404, "Detail mutasi tidak ditemukan")
    return {"mutation": detail}


@api_router.get("/settings/physical-stock")
async def physical_stock_settings_get(user=Depends(current_user)):
    """Pure read. Returns physical stock configuration."""
    await _require_any_perm(user, "current_stock.view", "current_stock.update", "permissions.manage")
    from services.physical_stock_settings import get_physical_stock_settings
    settings = await get_physical_stock_settings(db)
    return {"settings": settings}


@api_router.put("/settings/physical-stock")
async def physical_stock_settings_put(payload: dict = Body(...), user=Depends(current_user)):
    """Update physical stock configuration."""
    await _require_any_perm(user, "current_stock.update", "permissions.manage")
    from services.physical_stock_settings import update_physical_stock_settings
    settings = await update_physical_stock_settings(db, payload, user=user)
    return {"settings": settings, "message": "Pengaturan stok fisik berhasil disimpan"}


class CurrentStockPatch(BaseModel):
    physical_qty: Any
    note: str
    client_request_id: str


async def _physical_stock_enabled() -> bool:
    from services.physical_stock_settings import get_physical_stock_settings
    settings = await get_physical_stock_settings(db)
    return bool(settings.get("enabled"))


async def _load_legacy_stock_context():
    """Pre-V2 Stock Saat Ini: item_stock_manual + receipts + PO/print aggregates."""
    masters = await db.master_items.find({}, {"_id": 0}).to_list(10000)
    orders = await db.orders.find(
        {"cancelled": {"$ne": True}}, STOCK_ORDER_PROJECTION,
    ).to_list(20000)
    manuals = await db[STOCK_MANUAL_COLLECTION].find({}, {"_id": 0}).to_list(10000)
    receipts = await load_receipts(db)
    meta = await load_sync_meta(db)
    source = purchase_source_from_meta(meta)
    rows = build_stock_rows(
        masters, orders, manuals, receipts, purchase_source=source,
    )
    return rows, source, receipt_last_success_at(meta)


async def _load_v2_stock_context():
    """Stock Engine V2 read model: physical ledger + outstanding SO."""
    rows = await build_current_stock_rows(db)
    return rows, "GOODS_RECEIPT", None


async def _load_stock_context():
    """Toggle is the authority boundary: OFF = legacy read, ON = V2 read.

    GET path is read-only against app_settings / product_stock_state / stock_movements.
    """
    if await _physical_stock_enabled():
        return await _load_v2_stock_context()
    return await _load_legacy_stock_context()


def _current_stock_correction_allowed(user: dict) -> bool:
    from services.product_stock_state import can_correct_physical_stock

    return can_correct_physical_stock(
        user,
        os.environ.get("APP_ENV"),
        os.environ.get("MONGO_URL"),
    )



def _purchase_source_label(source: str) -> str:
    if source in ("GOODS_RECEIPT",):
        return "Penerimaan Barang"
    return "Accurate" if source == STOCK_PURCHASE_ACCURATE else "Manual"


def _stock_xlsx(rows: list, *, v2: bool = True) -> FastAPIResponse:
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Stock Saat Ini"
    if v2:
        headers = [
            "Kode", "Nama Item", "Satuan", "Stock Fisik", "Belum Dicetak", "Sudah Dicetak",
            "Outstanding SO", "Stock Saat Ini", "Terakhir Update",
        ]
    else:
        headers = [
            "Kode", "Nama Item", "Satuan", "Qty Saat Ini", "Pembelian Barang",
            "Qty PO", "Qty Cetak Operator", "Stock Saat Ini", "Sumber Pembelian",
            "Terakhir Update",
        ]
    ws.append(headers)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="17313E")
    for col_idx, _ in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col_idx)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal="left", vertical="center")
    for r in rows:
        updated = r.get("updated_at") or ""
        if r.get("updated_by_name") or r.get("updated_by"):
            who = r.get("updated_by_name") or r.get("updated_by")
            updated = f"{updated} · {who}" if updated else who
        src = _purchase_source_label(r.get("purchase_source") or STOCK_PURCHASE_SOURCE)
        if v2:
            ws.append([
                r.get("item_code") or "",
                r.get("item_name") or "",
                r.get("unit") or "",
                r.get("physical_qty") if r.get("physical_qty") is not None else 0,
                r.get("unprinted_qty") if r.get("unprinted_qty") is not None else 0,
                r.get("printed_qty") if r.get("printed_qty") is not None else 0,
                r.get("outstanding_so_qty") if r.get("outstanding_so_qty") is not None else 0,
                r.get("stock_current") if r.get("stock_current") is not None else 0,
                updated,
            ])
        else:
            ws.append([
                r.get("item_code") or "",
                r.get("item_name") or "",
                r.get("unit") or "",
                r.get("manual_current_qty") if r.get("manual_current_qty") is not None else 0,
                r.get("purchase_qty") if r.get("purchase_qty") is not None else 0,
                r.get("qty_po") if r.get("qty_po") is not None else 0,
                r.get("operator_print_qty") if r.get("operator_print_qty") is not None else 0,
                r.get("stock_current") if r.get("stock_current") is not None else 0,
                src,
                updated,
            ])
    last_num_col = 8 if v2 else 8
    for col in range(4, last_num_col + 1):
        for row_idx in range(2, ws.max_row + 1):
            ws.cell(row=row_idx, column=col).number_format = "0"
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"stock-saat-ini-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.xlsx"
    return FastAPIResponse(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_router.get("/inventory-valuation/item")
async def inventory_valuation_item(
    master_item_id: str = "",
    user=Depends(require_perm("inventory_valuation.view")),
):
    """Read one Nilai Persediaan row by exact master_item_id. Does not write."""
    from services.inventory_valuation import lookup_inventory_valuation_item

    return await lookup_inventory_valuation_item(db, master_item_id)


@api_router.get("/inventory-valuation")
async def inventory_valuation_list(
    q: str = "",
    status_filter: str = "all",
    sort_by: str = "name",
    sort_dir: str = "asc",
    page: int = 1,
    page_size: int = 50,
    user=Depends(require_perm("inventory_valuation.view")),
):
    """Read-only Stock V2 × canonical moving-average HPP view."""
    from services.inventory_valuation import (
        build_inventory_valuation_rows,
        filter_sort_page,
    )

    view = await build_inventory_valuation_rows(db)
    payload = filter_sort_page(
        view["rows"],
        q=q,
        status_filter=status_filter,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )
    payload.update({
        "summary": view["summary"],
        "method": view["method"],
        "active_version": view["active_version"],
        "cutover_id": view["cutover_id"],
    })
    return payload


@api_router.get("/inventory-valuation/export")
async def inventory_valuation_export(
    q: str = "",
    status_filter: str = "all",
    sort_by: str = "name",
    sort_dir: str = "asc",
    user=Depends(require_perm("inventory_valuation.export")),
):
    """Read-only Excel export; no inventory or HPP data is mutated."""
    from services.inventory_valuation import (
        build_inventory_valuation_rows,
        export_xlsx,
        filter_sort_all,
    )

    view = await build_inventory_valuation_rows(db)
    filtered = filter_sort_all(
        view["rows"],
        q=q,
        status_filter=status_filter,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    filename = f"nilai-persediaan-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.xlsx"
    return FastAPIResponse(
        content=export_xlsx(filtered),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _opening_http(exc):
    from services.financial_inventory_opening import OpeningBlocked
    if isinstance(exc, OpeningBlocked):
        raise HTTPException(exc.status_code, exc.as_detail())
    raise exc


@api_router.get("/inventory/financial-opening")
async def financial_opening_list(user=Depends(require_perm("inventory_valuation.view"))):
    from services.financial_inventory_opening import list_financial_openings
    return {"items": await list_financial_openings(db), "writes": 0}


@api_router.get("/inventory/financial-opening/reconciliation")
async def financial_opening_reconciliation(user=Depends(require_perm("inventory_valuation.view"))):
    from services.financial_inventory_opening import build_financial_book_reconciliation
    return await build_financial_book_reconciliation(db)


@api_router.post("/inventory/financial-opening/preview")
async def financial_opening_preview(payload: dict, user=Depends(require_perm("inventory_valuation.view"))):
    from services.financial_inventory_opening import preview_financial_opening
    try:
        return await preview_financial_opening(db, payload or {})
    except Exception as exc:
        _opening_http(exc)


@api_router.post("/inventory/financial-opening")
async def financial_opening_post(payload: dict, user=Depends(require_perm("inventory_valuation.manage_opening"))):
    from services.financial_inventory_opening import post_financial_opening
    try:
        saved = await post_financial_opening(db, payload or {}, user)
    except Exception as exc:
        _opening_http(exc)
    return {"item": saved}


@api_router.post("/inventory/financial-opening/recover")
async def financial_opening_recover(payload: dict, user=Depends(require_perm("inventory_valuation.manage_opening"))):
    from services.financial_inventory_opening import recover_financial_opening
    try:
        saved = await recover_financial_opening(
            db,
            client_request_id=str((payload or {}).get("client_request_id") or ""),
            user=user,
        )
    except Exception as exc:
        _opening_http(exc)
    return {"item": saved}


@api_router.post("/inventory/financial-opening/{opening_id}/reverse")
async def financial_opening_reverse(opening_id: str, payload: dict, user=Depends(require_perm("inventory_valuation.manage_opening"))):
    from services.financial_inventory_opening import reverse_financial_opening
    try:
        saved = await reverse_financial_opening(
            db,
            opening_id,
            user,
            str((payload or {}).get("reason") or ""),
        )
    except Exception as exc:
        _opening_http(exc)
    return {"item": saved}


@api_router.post("/inventory/financial-opening/snapshot")
async def financial_opening_snapshot(payload: dict, user=Depends(require_perm("inventory_valuation.manage_opening"))):
    from services.financial_inventory_opening import capture_financial_opening_snapshot
    try:
        saved = await capture_financial_opening_snapshot(db, payload or {}, user)
    except Exception as exc:
        _opening_http(exc)
    return {"item": saved}


@api_router.get("/inventory/reports/purchases")
async def inventory_purchase_activity_report(
    start_date: str = "",
    end_date: str = "",
    page: int = 1,
    page_size: int = 50,
    user=Depends(require_perm("inventory_valuation.view")),
):
    from services.commercial_activity_reports import purchase_activity_report
    return await purchase_activity_report(
        db, start_date=start_date, end_date=end_date, page=page, page_size=page_size,
    )


@api_router.get("/inventory/reports/sales")
async def inventory_sales_activity_report(
    start_date: str = "",
    end_date: str = "",
    page: int = 1,
    page_size: int = 50,
    user=Depends(require_perm("inventory_valuation.view")),
):
    from services.commercial_activity_reports import sales_activity_report
    return await sales_activity_report(
        db, start_date=start_date, end_date=end_date, page=page, page_size=page_size,
    )


@api_router.get("/current-stock")
async def current_stock_list(
    q: str = "",
    stock_filter: str = "all",
    sort_by: str = "name",
    sort_dir: str = "asc",
    page: int = 1,
    page_size: int = 50,
    user=Depends(require_perm("current_stock.view")),
):
    v2_on = await _physical_stock_enabled()
    rows, source, last_sync = await (_load_v2_stock_context() if v2_on else _load_legacy_stock_context())
    if v2_on:
        payload = filter_sort_page_v2(
            rows, q=q, stock_filter=stock_filter, sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )
    else:
        payload = filter_stock_page(
            rows, q=q, stock_filter=stock_filter, sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size, purchase_source=source,
            last_receipt_sync_at=last_sync,
        )
        payload["stock_engine"] = "legacy"
        payload["formula"] = "(manual_current_qty + purchase_qty) - po_qty - operator_print_qty"
    payload["purchase_source"] = source
    payload["last_receipt_sync_at"] = last_sync
    payload["physical_stock_enabled"] = v2_on
    payload["can_correct_physical_stock"] = bool(
        v2_on and _current_stock_correction_allowed(user)
    )
    return payload


@api_router.get("/current-stock/export")
async def current_stock_export(
    q: str = "",
    stock_filter: str = "all",
    sort_by: str = "name",
    sort_dir: str = "asc",
    user=Depends(require_perm("current_stock.export")),
):
    v2_on = await _physical_stock_enabled()
    rows, source, _last_sync = await (_load_v2_stock_context() if v2_on else _load_legacy_stock_context())
    if v2_on:
        payload = filter_sort_page_v2(
            rows, q=q, stock_filter=stock_filter, sort_by=sort_by, sort_dir=sort_dir,
            page=1, page_size=max(1, len(rows) or 1),
        )
    else:
        payload = filter_stock_page(
            rows, q=q, stock_filter=stock_filter, sort_by=sort_by, sort_dir=sort_dir,
            page=1, page_size=max(1, len(rows) or 1), purchase_source=source,
            last_receipt_sync_at=_last_sync,
        )
    filtered = payload.get("items") or payload.get("rows") or []
    await _write_audit(
        "current_stock.export", user, target_email=None,
        before=None, after={"count": len(filtered), "stock_filter": stock_filter, "q": q},
        note="Export Stock Saat Ini",
    )
    return _stock_xlsx(filtered, v2=v2_on)


@api_router.patch("/current-stock/{item_id}")
async def current_stock_update(
    item_id: str,
    payload: CurrentStockPatch,
    user=Depends(current_user),
):
    """HOLDING_TEAM-only append-only correction of total physical stock."""
    from services.product_stock_state import (
        correct_opening_balance,
        get_product_stock_state,
        require_physical_stock_correction_access,
        validate_non_negative_number,
    )
    app_env = os.environ.get("APP_ENV")
    mongo_url = os.environ.get("MONGO_URL")
    require_physical_stock_correction_access(user, app_env, mongo_url)
    request_id = str(payload.client_request_id or "").strip()
    if not request_id:
        raise HTTPException(400, "client_request_id wajib diisi")
    note = str(payload.note or "").strip()
    if not note:
        raise HTTPException(400, "Alasan koreksi wajib diisi")

    state = await get_product_stock_state(db, item_id)
    if not state or not state.get("initialized"):
        raise HTTPException(400, "Saldo awal stok fisik produk belum ditetapkan")
    new_total = validate_non_negative_number(payload.physical_qty, "physical_qty")
    printed = validate_non_negative_number(state.get("printed_qty", 0), "printed_qty")
    if new_total < printed:
        raise HTTPException(
            400,
            "Stock Fisik tidak boleh lebih kecil dari bucket Sudah Dicetak",
        )

    result = await correct_opening_balance(
        db,
        item_id,
        {
            "unprinted_qty": new_total - printed,
            "printed_qty": printed,
            "total_qty": new_total,
            "note": note,
            "client_request_id": request_id,
        },
        user=user,
        app_env=app_env,
        mongo_url=mongo_url,
    )
    await _write_audit(
        "current_stock.physical_correction",
        user,
        target_email=f"item:{item_id}",
        before=result.get("old_qty"),
        after={
            "new_qty": result.get("new_qty"),
            "movement_id": (result.get("movement") or {}).get("id"),
            "idempotent": result.get("idempotent"),
        },
        note=note,
    )
    return result


class StockOpnamePayload(BaseModel):
    master_item_id: str
    target_stock_current_qty: Any
    note: Optional[str] = ""
    client_request_id: Optional[str] = None


@api_router.post("/stock-opname")
async def stock_opname_apply(
    payload: StockOpnamePayload,
    user=Depends(current_user),
):
    from services.product_stock_state import require_physical_stock_correction_access
    require_physical_stock_correction_access(
        user,
        os.environ.get("APP_ENV"),
        os.environ.get("MONGO_URL"),
    )
    stock_row = await available_for_item(db, master_item_id=payload.master_item_id)
    result = await apply_stock_opname(
        db,
        master_item_id=payload.master_item_id,
        target_stock_current_qty=payload.target_stock_current_qty,
        outstanding_so_qty=stock_row.get("outstanding_so_qty"),
        note=payload.note or "",
        user=user,
        client_request_id=payload.client_request_id,
    )
    await _write_audit(
        "stock_opname.apply",
        user,
        target_email=f"item:{payload.master_item_id}",
        before=None,
        after={
            "master_item_id": payload.master_item_id,
            "target_stock_current_qty": payload.target_stock_current_qty,
            "delta_total": result.get("delta_total"),
        },
        note=payload.note or "Stock Opname",
    )
    return result


@api_router.get("/stock-opname/items")
async def stock_opname_items(
    q: str = "",
    user=Depends(require_perm("current_stock.view")),
):
    rows, _source, _last = await _load_v2_stock_context()
    payload = filter_sort_page_v2(rows, q=q, page=1, page_size=200)
    payload["can_apply_stock_opname"] = _current_stock_correction_allowed(user)
    return payload


@api_router.get("/stock-opname/export-template")
async def stock_opname_export_template(
    user=Depends(require_perm("current_stock.view")),
):
    rows, _source, _last = await _load_v2_stock_context()
    masters = await db.master_items.find({}, {"_id": 0}).to_list(20000)
    catalog = filter_active_stock_rows(rows, masters)
    content = build_opname_template_xlsx(catalog)
    filename = f"stock-opname-template-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.xlsx"
    return FastAPIResponse(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_router.post("/stock-opname/import/preview")
async def stock_opname_import_preview(
    file: UploadFile = File(...),
    user=Depends(current_user),
):
    from services.product_stock_state import require_physical_stock_correction_access
    require_physical_stock_correction_access(
        user,
        os.environ.get("APP_ENV"),
        os.environ.get("MONGO_URL"),
    )
    data = await file.read()
    if not data:
        raise HTTPException(400, "File kosong")
    rows, _source, _last = await _load_v2_stock_context()
    masters = await db.master_items.find({}, {"_id": 0}).to_list(20000)
    catalog = filter_active_stock_rows(rows, masters)
    preview = build_opname_import_preview(
        data,
        file.filename or "upload.xlsx",
        stock_rows=catalog,
    )
    return preview


class StockOpnameImportApply(BaseModel):
    rows: List[dict]
    filename: Optional[str] = "upload.xlsx"
    note: Optional[str] = ""
    batch_request_id: str


@api_router.post("/stock-opname/import/apply")
async def stock_opname_import_apply(
    payload: StockOpnameImportApply,
    user=Depends(current_user),
):
    from services.product_stock_state import require_physical_stock_correction_access
    require_physical_stock_correction_access(
        user,
        os.environ.get("APP_ENV"),
        os.environ.get("MONGO_URL"),
    )
    rows, _source, _last = await _load_v2_stock_context()
    masters = await db.master_items.find({}, {"_id": 0}).to_list(20000)
    catalog = filter_active_stock_rows(rows, masters)
    validated_rows = revalidate_opname_apply_rows(
        payload.rows or [],
        stock_rows=catalog,
        batch_request_id=payload.batch_request_id,
    )
    result = await apply_opname_import_batch(
        db,
        preview_rows=validated_rows,
        filename=payload.filename or "upload.xlsx",
        user=user,
        note=payload.note or "",
        batch_request_id=payload.batch_request_id,
    )
    await _write_audit(
        "stock_opname.import_apply",
        user,
        target_email=f"batch:{(result.get('batch') or {}).get('id')}",
        before=None,
        after={
            "filename": payload.filename,
            "success_count": result.get("success_count"),
            "failure_count": result.get("failure_count"),
            "skipped_count": result.get("skipped_count"),
            "batch_id": (result.get("batch") or {}).get("id"),
        },
        note=payload.note or "Import Stock Opname Excel",
    )
    return result



class CurrentStockImportApply(BaseModel):
    rows: List[dict]


@api_router.post("/current-stock/import/preview")
async def current_stock_import_preview(
    file: UploadFile = File(...),
    user=Depends(require_perm(STOCK_IMPORT_PERM)),
):
    data = await file.read()
    if not data:
        raise HTTPException(400, "File kosong")
    rows, _source, _last = await _load_stock_context()
    masters = await db.master_items.find({}, {"_id": 0}).to_list(5000)
    try:
        preview = build_stock_import_preview(
            data, file.filename or "upload.csv", stock_rows=rows, masters=masters,
        )
    except Exception as e:
        raise HTTPException(400, str(e))
    return preview


@api_router.post("/current-stock/import/apply")
async def current_stock_import_apply(
    payload: CurrentStockImportApply,
    user=Depends(require_perm(STOCK_IMPORT_PERM)),
):
    rows, source, _last = await _load_stock_context()
    masters = await db.master_items.find({}, {"_id": 0}).to_list(5000)
    accepted, rejected = validate_stock_import_apply(
        payload.rows or [], stock_rows=rows, masters=masters,
    )
    if not accepted:
        raise HTTPException(400, {
            "message": "Tidak ada baris valid yang dipilih",
            "rejected": rejected,
        })
    applied = []
    now = now_iso()
    for item in accepted:
        master_id = item["master_item_id"]
        accurate_id = item.get("accurate_item_id")
        existing = await db[STOCK_MANUAL_COLLECTION].find_one(
            {"$or": [
                {"master_item_id": master_id},
                {"accurate_item_id": accurate_id} if accurate_id else {"master_item_id": master_id},
            ]},
            {"_id": 0},
        )
        before = {
            "stock_before": item["stock_before"],
            "manual": public_stock_manual(existing),
        }
        fields = {
            "manual_current_qty": item["manual_current_qty"],
            "updated_at": now,
            "updated_by": user.get("email"),
            "updated_by_name": user.get("name") or user.get("email"),
        }
        if existing:
            await db[STOCK_MANUAL_COLLECTION].update_one({"id": existing["id"]}, {"$set": fields})
            doc_id = existing["id"]
        else:
            doc_id = str(uuid.uuid4())
            await db[STOCK_MANUAL_COLLECTION].insert_one({
                "id": doc_id,
                "master_item_id": master_id,
                "accurate_item_id": accurate_id,
                "manual_current_qty": item["manual_current_qty"],
                "manual_purchase_qty": 0,
                **{k: v for k, v in fields.items() if k != "manual_current_qty"},
            })
        fresh = await db[STOCK_MANUAL_COLLECTION].find_one({"id": doc_id}, {"_id": 0})
        await _write_audit(
            "current_stock.import", user,
            target_email=f"item:{accurate_id or master_id}",
            before=before,
            after={
                "stock_after": item["stock_after"],
                "delta": item["delta"],
                "stock_file": item["stock_file"],
                "manual": public_stock_manual(fresh),
            },
            note="Impor sesuaikan Stock Saat Ini",
        )
        applied.append({
            "master_item_id": master_id,
            "accurate_item_id": accurate_id,
            "item_code": item.get("item_code"),
            "stock_before": item["stock_before"],
            "stock_after": item["stock_after"],
            "delta": item["delta"],
        })
    fresh_rows, source, last_sync = await _load_stock_context()
    return {
        "applied": applied,
        "applied_count": len(applied),
        "rejected": rejected,
        "rejected_count": len(rejected),
        "purchase_source": source,
        "last_receipt_sync_at": last_sync,
        "items": fresh_rows,
    }


async def _masters_by_accurate_id():
    masters = await db.master_items.find(
        {}, {"_id": 0, "id": 1, "accurate_item_id": 1, "item_code": 1, "item_name": 1, "unit": 1},
    ).to_list(5000)
    return {str(m.get("accurate_item_id")): m for m in masters if m.get("accurate_item_id")}


async def _public_receipt_rows():
    recs = await load_receipts(db)
    by_aid = await _masters_by_accurate_id()
    rows = [public_receipt(r, by_aid) for r in recs]
    rows.sort(key=lambda r: (
        str(r.get("receipt_date") or ""),
        str(r.get("receipt_number") or ""),
        str(r.get("accurate_receipt_id") or ""),
    ), reverse=True)
    return rows


def _receipts_xlsx(rows: list) -> FastAPIResponse:
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Penerimaan Barang"
    headers = [
        "Tanggal", "No Penerimaan", "Kode", "Nama Item", "Satuan",
        "Qty Diterima", "Status", "Accurate Receipt ID",
    ]
    ws.append(headers)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="17313E")
    for col_idx, _ in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col_idx)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal="left", vertical="center")
    for rec in rows:
        lines = rec.get("lines") or []
        if not lines:
            ws.append([
                rec.get("receipt_date") or "",
                rec.get("receipt_number") or "",
                "", "", "", 0,
                rec.get("status") or "",
                rec.get("accurate_receipt_id") or "",
            ])
            continue
        for li in lines:
            ws.append([
                rec.get("receipt_date") or "",
                rec.get("receipt_number") or "",
                li.get("item_code") or "",
                li.get("item_name") or "",
                li.get("unit") or "",
                li.get("received_qty") if li.get("received_qty") is not None else 0,
                rec.get("status") or "",
                rec.get("accurate_receipt_id") or "",
            ])
    for row_idx in range(2, ws.max_row + 1):
        ws.cell(row=row_idx, column=6).number_format = "0"
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"penerimaan-barang-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.xlsx"
    return FastAPIResponse(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_router.get("/purchase-receipts")
async def purchase_receipts_list(
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    user=Depends(require_perm("purchase_receipts.view")),
):
    rows = filter_public_receipts(await _public_receipt_rows(), q)
    meta = await load_sync_meta(db)
    payload = page_public_receipts(rows, page=page, page_size=page_size)
    payload["last_synced_at"] = receipt_last_success_at(meta)
    payload["purchase_source"] = purchase_source_from_meta(meta)
    payload["source"] = "accurate"
    return payload


class PurchaseReceiptCreatePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


@api_router.post("/purchase-receipts")
async def purchase_receipts_create(
    payload: PurchaseReceiptCreatePayload,
    user=Depends(require_perm("purchase_receipts.receive")),
):
    """Manual Penerimaan Barang. Receive permission only — sync is Accurate ingest, not create."""
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    doc = await create_manual_purchase_receipt(db, data or {}, user)
    return {"receipt": public_receipt(doc), "message": "Penerimaan Barang berhasil dibuat"}


class PurchaseReceiptCancelPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    reason: str = ""


@api_router.post("/purchase-receipts/{receipt_id}/cancel")
async def purchase_receipts_cancel(
    receipt_id: str,
    payload: Optional[PurchaseReceiptCancelPayload] = None,
    user=Depends(require_perm("purchase_receipts.receive")),
):
    """Soft-cancel one ERP manual receipt. Accurate history is immutable."""
    data = payload.model_dump() if payload is not None and hasattr(payload, "model_dump") else (
        payload.dict() if payload is not None else {}
    )
    doc = await cancel_manual_purchase_receipt(
        db, receipt_id, user, reason=str((data or {}).get("reason") or "")
    )
    return {"receipt": public_receipt(doc), "message": "Penerimaan Barang dibatalkan"}


@api_router.get("/purchase-receipts/export")
async def purchase_receipts_export(
    q: str = "",
    user=Depends(require_perm("purchase_receipts.export")),
):
    rows = filter_public_receipts(await _public_receipt_rows(), q)
    await _write_audit(
        "purchase_receipts.export", user, target_email=None,
        before=None, after={"count": len(rows), "q": q},
        note="Export Penerimaan Barang",
    )
    return _receipts_xlsx(rows)


@api_router.post("/purchase-receipts/sync")
async def purchase_receipts_sync(user=Depends(require_perm("purchase_receipts.sync"))):
    """Manual sync Penerimaan Barang Accurate into modul Pembelian."""
    result = await sync_purchase_receipts(db, _accurate_client)
    await _write_audit(
        "purchase_receipts.sync", user, target_email=None,
        before=None,
        after={
            "ok": result.get("ok"),
            "receipts_found": result.get("receipts_found"),
            "created": result.get("created"),
            "updated": result.get("updated"),
            "unchanged": result.get("unchanged"),
            "failed": result.get("failed"),
            "mode": result.get("mode"),
        },
        note="Sinkronkan Penerimaan Barang dari Accurate",
    )
    return result


register_purchase_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    require_any_perm=_require_any_perm,
    Depends=Depends,
)
register_product_request_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    user_permissions=user_permissions,
    require_any_perm=_require_any_perm,
    Depends=Depends,
)
register_spk_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    user_permissions=user_permissions,
    require_any_perm=_require_any_perm,
    Depends=Depends,
)
register_customer_pricing_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_any_perm=_require_any_perm,
    Depends=Depends,
)
register_sales_accounting_settings_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_any_perm=_require_any_perm,
    Depends=Depends,
)
register_purchase_accounting_settings_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_any_perm=_require_any_perm,
    Depends=Depends,
)
register_sales_user_mapping_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_any_perm=_require_any_perm,
    Depends=Depends,
)
register_sales_pwa_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    Depends=Depends,
    obj_storage=obj_storage,
    user_permissions=user_permissions,
)
register_payroll_accounting_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_any_perm=_require_any_perm,
    Depends=Depends,
)
register_public_customer_pricing_routes(
    api_router,
    get_db=lambda: db,
)
register_employee_admin_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    require_any_perm=_require_any_perm,
    user_permissions=user_permissions,
    Depends=Depends,
)
register_chart_of_accounts_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_cash_bank_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_chat_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
    user_permissions=user_permissions,
)
from services.notification_http import register_notification_routes
register_notification_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=_require_perm,
    Depends=Depends,
)
register_general_journal_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_opening_balance_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_accounting_periods_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_general_ledger_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_inventory_issue_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_accounts_receivable_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_accounts_payable_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_trial_balance_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_profit_loss_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_financial_dashboard_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    user_permissions=user_permissions,
    Depends=Depends,
)
register_sales_dashboard_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    user_permissions=user_permissions,
    Depends=Depends,
)
register_balance_sheet_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_inventory_reconciliation_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    Depends=Depends,
)
register_hr_payroll_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=require_perm,
    require_any_perm=_require_any_perm,
    user_permissions=user_permissions,
    Depends=Depends,
    create_canonical_user=create_canonical_user,
)
register_sales_activity_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=_require_perm,
    require_any_perm=_require_any_perm,
    user_permissions=user_permissions,
    Depends=Depends,
)
register_independent_route_routes(
    api_router,
    get_db=lambda: db,
    current_user=current_user,
    require_perm=_require_perm,
    require_any_perm=_require_any_perm,
    user_permissions=user_permissions,
    Depends=Depends,
)


@api_router.post("/accurate/sync")
async def accurate_sync(user=Depends(require("admin", "head_production"))):
    """Legacy bulk sync — kept behind admin/HoP for backfill only. Bulk auto-sync
    is intentionally NOT wired to any scheduler; import single-SO instead."""
    result = await sync_sales_orders(db, _accurate_client)
    return result


# ---------- Files (Emergent Object Storage) ----------
@api_router.get("/files/{path:path}")
async def download_file(path: str, user=Depends(current_user)):
    """Serve a file from object storage. Requires auth. Path is the storage key
    as returned by put_object() (starts with the app-name prefix)."""
    reject_sales_pwa_bypass(user)
    # Look up metadata to enforce content-type + soft-delete filter
    rec = await db.files.find_one({"storage_path": path, "is_deleted": {"$ne": True}}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "File tidak ditemukan")
    if rec.get("kind") == "surat_jalan":
        await _require_perm(user, _SJ_VIEW_FILE)
    if rec.get("kind") == "employee_admin":
        raise HTTPException(403, "File Administrasi Karyawan hanya dapat diakses lewat rute modul.")
    if rec.get("kind") == "hr_payroll":
        raise HTTPException(403, "File HR & Payroll hanya dapat diakses lewat rute modul.")
    if rec.get("kind") == "sales_visit_photo":
        raise HTTPException(403, "Foto visit hanya dapat diakses lewat rute Sales Activity.")
    if is_sales_pwa_role(user):
        oid = str(rec.get("order_id") or rec.get("po_id") or "").strip()
        if not oid:
            raise HTTPException(404, "File tidak ditemukan")
        owned = await db.orders.find_one({"id": oid, **sales_owner_match(user)}, {"_id": 0, "id": 1})
        if not owned:
            raise HTTPException(404, "File tidak ditemukan")
    try:
        data, ct = obj_storage.get_object(path)
    except FileNotFoundError:
        raise HTTPException(404, "File tidak ditemukan")
    except Exception:
        logger.exception("Failed to fetch object %s", path)
        raise HTTPException(502, "Gagal mengambil file.")
    media = rec.get("content_type") or ct
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    if rec.get("kind") == "surat_jalan":
        media = _sj_sniff_ct(data, rec.get("original_filename") or "", media)
        headers["Content-Disposition"] = _sj_content_disposition(
            rec.get("original_filename") or "surat-jalan", download=False,
        )
    return FastAPIResponse(content=data, media_type=media, headers=headers)


class DeliveryProofDeletePayload(BaseModel):
    proof_id: str


@api_router.post("/orders/{order_id}/delivery-proof")
async def upload_delivery_proof(
    order_id: str,
    file: UploadFile = File(...),
    note: Optional[str] = Form(None),
    shipment_id: Optional[str] = Form(None),
    user=Depends(require_perm("delivery.update")),
):
    """Upload a delivery-proof photo (from driver's phone). Stores to object
    storage and references it on the PO document — never as inline binary.
    Max 5 photos per Surat Jalan when multiple SJ exist; otherwise max 5 per PO."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    selected_sh, resolve_err = _qr_resolve_delivery_shipment(order, shipment_id)
    if resolve_err:
        raise HTTPException(400, resolve_err)
    photo_blocked = _qr_delivery_batch_photo_block_reason(order, selected_sh)
    if photo_blocked:
        raise HTTPException(400, photo_blocked)
    # Content-type and size first, so error messages remain actionable
    ct = (file.content_type or "").lower()
    if ct not in ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"):
        raise HTTPException(400, "Hanya file gambar yang diperbolehkan")
    existing_proofs = _qr_delivery_proofs_for_upload_limit(order, selected_sh)
    if len(existing_proofs) >= _QR_MAX_PUBLIC_DELIVERY_PROOFS:
        grain = "Surat Jalan" if selected_sh and len([
            sh for sh in (order.get("shipments") or []) if isinstance(sh, dict)
        ]) > 1 else "PO"
        raise HTTPException(
            400,
            f"Maksimum {_QR_MAX_PUBLIC_DELIVERY_PROOFS} foto bukti kirim per {grain} — hapus salah satu untuk mengganti",
        )

    body = await file.read()
    if len(body) > 5 * 1024 * 1024:
        raise HTTPException(400, "Ukuran file maksimal 5MB (silakan kompres di HP)")
    if len(body) == 0:
        raise HTTPException(400, "File kosong")

    ext = obj_storage.ext_for(ct, "jpg")
    file_id = str(uuid.uuid4())
    path = f"{obj_storage.APP_NAME}/delivery-proof/{order['po_number']}/{file_id}.{ext}"
    try:
        result = obj_storage.put_object(path, body, ct)
    except Exception:
        logger.exception("Storage upload failed")
        raise HTTPException(502, "Gagal mengunggah foto. Silakan coba lagi.")

    now = now_iso()
    orig_name = Path(file.filename or f"proof.{ext}").name
    orig_name = re.sub(r"[^A-Za-z0-9._-]+", "_", orig_name).strip("._") or f"proof.{ext}"
    file_doc = {
        "id": file_id,
        "storage_path": result.get("path") or path,
        "original_filename": orig_name,
        "content_type": ct,
        "size": result.get("size") or len(body),
        "kind": "delivery-proof",
        "order_id": order["id"],
        "po_number": order["po_number"],
        "uploaded_by": user.get("email"),
        "uploaded_by_role": user.get("role"),
        "uploaded_by_name": user.get("name"),
        "uploaded_at": now,
        "note": note or "",
        "is_deleted": False,
    }
    await db.files.insert_one(file_doc)

    proof_ref = {
        "id": file_id,
        "storage_path": result["path"],
        "content_type": ct,
        "size": file_doc["size"],
        "original_filename": file_doc["original_filename"],
        "uploaded_by": user.get("email"),
        "uploaded_by_role": user.get("role"),
        "uploaded_by_name": user.get("name"),
        "uploaded_at": now,
        "note": note or "",
    }
    order.setdefault("delivery_proofs", []).append(proof_ref)
    stamp_sh = selected_sh
    if stamp_sh:
        if stamp_sh.get("id"):
            proof_ref["shipment_id"] = stamp_sh.get("id")
        if stamp_sh.get("number"):
            proof_ref["shipment_number"] = stamp_sh.get("number")
        if stamp_sh.get("accurate_shipment_id"):
            proof_ref["accurate_shipment_id"] = stamp_sh.get("accurate_shipment_id")
    add_timeline(order, order["current_stage"], user, note=f"Upload bukti kirim: {file_doc['original_filename']}")
    await _persist_order(order)
    return {"order": order, "proof": proof_ref}


@api_router.delete("/orders/{order_id}/delivery-proof/{proof_id}")
async def delete_delivery_proof(order_id: str, proof_id: str, user=Depends(require_perm("delivery.update"))):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    proofs = order.get("delivery_proofs", [])
    target = next((p for p in proofs if p["id"] == proof_id), None)
    if not target:
        raise HTTPException(404, "Bukti kirim tidak ditemukan")
    # Driver can only delete their own upload; admin/HoP anyone
    if user.get("role") == "driver" and target.get("uploaded_by") != user.get("email"):
        raise HTTPException(403, "Driver hanya dapat menghapus bukti yang di-upload sendiri")
    order["delivery_proofs"] = [p for p in proofs if p["id"] != proof_id]
    await db.files.update_one(
        {"id": proof_id, "order_id": order["id"]},
        {"$set": {"is_deleted": True, "deleted_at": now_iso(), "deleted_by": user.get("email")}},
    )
    add_timeline(order, order["current_stage"], user, note=f"Hapus bukti kirim {target.get('original_filename', proof_id)}")
    # Write only proof/timeline fields. Full-document $set rewrote unrelated binaries
    # (design/sample/SJ data_url) and made delete wait on a huge Mongo update.
    await _persist_order(order, persist_keys=("delivery_proofs", "timeline"))
    return {"order": order}


# ---------- Analytics (raw base data, no KPI assumption) ----------
@api_router.get("/analytics/operators")
async def analytics_operators(user=Depends(require("admin", "head_production"))):
    """Base analytics data per operator. Aggregates from operator_splits +
    timeline. Does NOT compute any performance rating — raw metrics only."""
    orders = await db.orders.find({}, {"_id": 0, "design_files": 0, "sample_files": 0, "delivery_proofs": 0}).to_list(2000)

    # Build op -> aggregated map
    ops: Dict[str, Dict[str, Any]] = {}
    for o in orders:
        for s in o.get("operator_splits", []):
            email = s.get("operator_email")
            if not email:
                continue
            rec = ops.setdefault(email, {
                "email": email,
                "name": s.get("operator_name", email),
                "qty_scheduled": 0,
                "qty_completed": 0,
                "active_po_count": 0,
                "completed_po_count": 0,
                "all_time_po_count": 0,
                "po_history": [],
            })
            # Update raw counts
            rec["qty_scheduled"] += int(s.get("quantity", 0))
            rec["qty_completed"] += int(s.get("qty_completed", 0))
            rec["all_time_po_count"] += 1
            if o.get("current_stage", 0) >= 14:
                rec["completed_po_count"] += 1
            else:
                rec["active_po_count"] += 1

            # Timeline-derived time markers
            tl = o.get("timeline", [])
            assigned_at = None
            first_progress_at = None
            last_progress_at = None
            for t in tl:
                note = (t.get("note") or "")
                # Assignment event mentions 'Assign' or 'operator' name
                if "Assign" in note and assigned_at is None:
                    assigned_at = t.get("at")
                # Progress events by this operator
                if t.get("by_email") == email:
                    if first_progress_at is None:
                        first_progress_at = t.get("at")
                    last_progress_at = t.get("at")
            rec["po_history"].append({
                "po_id": o.get("id"),
                "po_number": o.get("po_number"),
                "outlet": o.get("outlet"),
                "customer": o.get("customer"),
                "quantity": int(s.get("quantity", 0)),
                "qty_completed": int(s.get("qty_completed", 0)),
                "qty_remaining": max(0, int(s.get("quantity", 0)) - int(s.get("qty_completed", 0))),
                "stage": s.get("stage"),
                "stage_label": STAGES[min(s.get("stage", 8), len(STAGES) - 1)],
                "priority": o.get("priority"),
                "assigned_at": assigned_at,
                "first_progress_at": first_progress_at,
                "last_progress_at": last_progress_at,
                "split_updated_at": s.get("updated_at"),
                "po_created_at": o.get("created_at"),
                "po_delivered_at": o.get("delivered_at"),
                "po_current_stage": o.get("current_stage"),
            })

    # Finalize
    result = []
    for r in ops.values():
        r["qty_remaining"] = max(0, r["qty_scheduled"] - r["qty_completed"])
        r["po_history"].sort(key=lambda p: p.get("split_updated_at") or p.get("po_created_at") or "", reverse=True)
        result.append(r)
    result.sort(key=lambda r: r["email"])
    return {
        "operators": result,
        "note": "Data mentah (qty terjadwal / selesai / sisa, jumlah PO, waktu mulai & selesai per PO, riwayat pembagian). KPI rating akan disiapkan setelah rule ditentukan.",
        "generated_at": now_iso(),
    }


@api_router.get("/analytics/operators/kpis")
async def analytics_operator_kpis(
    period: Optional[str] = Query("month"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    operator_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
    user=Depends(require_perm("schedule.view")),
):
    """Read-only operator KPIs for Jadwal Produksi → Analitik Output.

    Does not write orders/progress. Qty selesai = operator_splits.qty_completed
    (bukan qty PO / assigned / target).
    """
    start, end, preset = resolve_period(period, date_from, date_to)
    pipeline = build_operator_analytics_pipeline(operator_id, customer, item, po)
    facet_rows = await db.orders.aggregate(pipeline).to_list(1)
    blob = facet_rows[0] if facet_rows else {}
    targets = normalize_daily_targets(await db.app_settings.find_one({"key": "operator_output_targets"}, {"_id": 0}))
    result = compute_operator_kpis(
        blob.get("docs") or [],
        date_from=start,
        date_to=end,
        operator_id=operator_id,
        customer=customer,
        item=item,
        po=po,
        daily_targets=targets,
    )
    grouping = series_grouping(preset, start, end)
    result["period"]["preset"] = preset
    result["period"]["grouping"] = grouping
    result["output_series"] = compute_output_series(
        blob.get("docs") or [],
        date_from=start,
        date_to=end,
        grouping=grouping,
        operator_id=operator_id,
        customer=customer,
        item=item,
        po=po,
    )
    result["filters"] = {
        "operators": [
            {"id": x.get("_id"), "name": x.get("name") or x.get("_id")}
            for x in (blob.get("operators") or []) if x.get("_id")
        ],
        "customers": [x.get("_id") for x in (blob.get("customers") or []) if x.get("_id")],
        "items": [x.get("_id") for x in (blob.get("items") or []) if x.get("_id")],
        "pos": sorted({
            (o.get("po_number") or o.get("id"))
            for o in (blob.get("docs") or [])
            if (o.get("po_number") or o.get("id"))
        }),
    }
    docs = blob.get("docs") or []
    result["rows"] = iter_operator_export_rows(
        docs, date_from=start, date_to=end,
        operator_id=operator_id, customer=customer, item=item, po=po, daily_targets=targets,
    )
    result["people"] = summarize_people(
        result["rows"], id_key="operator_id", name_key="operator", qty_key="qty_completed",
    )
    result["kpis"]["operator_count"] = len(result.get("operators") or [])
    result["kpis"]["po_count"] = result["kpis"].get("total_jobs")
    result["generated_at"] = now_iso()
    result["note"] = (
        "Read-only. Qty selesai = qty yang diselesaikan masing-masing operator "
        "(operator_splits.qty_completed), bukan qty PO."
    )
    return result


@api_router.get("/analytics/operators/{operator_id}")
async def analytics_operator_detail(
    operator_id: str,
    period: Optional[str] = Query("month"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    user=Depends(require_perm("schedule.view")),
):
    """Read-only drill-down for one operator. Job history is paginated."""
    start, end, preset = resolve_period(period, date_from, date_to)
    pipeline = build_operator_analytics_pipeline(operator_id, customer, item, po)
    facet_rows = await db.orders.aggregate(pipeline).to_list(1)
    blob = facet_rows[0] if facet_rows else {}
    targets = normalize_daily_targets(await db.app_settings.find_one({"key": "operator_output_targets"}, {"_id": 0}))
    result = compute_operator_detail(
        blob.get("docs") or [],
        operator_id=operator_id,
        date_from=start,
        date_to=end,
        customer=customer,
        item=item,
        po=po,
        daily_targets=targets,
        status=status,
        page=page,
        page_size=page_size,
    )
    result["period"]["preset"] = preset
    result["generated_at"] = now_iso()
    return result


@api_router.get("/analytics/packing")
async def analytics_packing(
    period: Optional[str] = Query("month"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
    user=Depends(require_perm("schedule.view")),
):
    """Read-only packing qty report. Uses Save Packing timeline deltas, not PO openers."""
    start, end, preset = resolve_period(period, date_from, date_to)
    pipeline = build_activity_analytics_pipeline(customer, item, po)
    facet_rows = await db.orders.aggregate(pipeline).to_list(1)
    blob = facet_rows[0] if facet_rows else {}
    result = compute_packing_report(
        blob.get("docs") or [],
        date_from=start,
        date_to=end,
        user_id=user_id,
        role=role,
        customer=customer,
        item=item,
        po=po,
    )
    result["period"]["preset"] = preset
    extra_customers = [x.get("_id") for x in (blob.get("customers") or []) if x.get("_id")]
    extra_items = [x.get("_id") for x in (blob.get("items") or []) if x.get("_id")]
    extra_pos = [x.get("_id") for x in (blob.get("pos") or []) if x.get("_id")]
    result["filters"] = {
        **(result.get("filters") or {}),
        "customers": extra_customers,
        "items": extra_items,
        "pos": extra_pos,
    }
    result["generated_at"] = now_iso()
    return result


@api_router.get("/analytics/preparation")
async def analytics_preparation(
    period: Optional[str] = Query("month"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
    user=Depends(require_perm("schedule.view")),
):
    """Read-only persiapan Cup/barang cetak. Qty from Save persiapan timeline deltas."""
    start, end, preset = resolve_period(period, date_from, date_to)
    pipeline = build_activity_analytics_pipeline(customer, item, po)
    facet_rows = await db.orders.aggregate(pipeline).to_list(1)
    blob = facet_rows[0] if facet_rows else {}
    result = compute_preparation_report(
        blob.get("docs") or [],
        date_from=start,
        date_to=end,
        user_id=user_id,
        role=role,
        customer=customer,
        item=item,
        po=po,
    )
    result["period"]["preset"] = preset
    extra_customers = [x.get("_id") for x in (blob.get("customers") or []) if x.get("_id")]
    extra_items = [x.get("_id") for x in (blob.get("items") or []) if x.get("_id")]
    extra_pos = [x.get("_id") for x in (blob.get("pos") or []) if x.get("_id")]
    result["filters"] = {
        **(result.get("filters") or {}),
        "customers": extra_customers,
        "items": extra_items,
        "pos": extra_pos,
    }
    result["rows"] = iter_helper_export_rows(
        blob.get("docs") or [], date_from=start, date_to=end,
        user_id=user_id, role=role, customer=customer, item=item, po=po,
    )
    result["people"] = summarize_people(
        result["rows"], id_key="helper_id", name_key="helper", qty_key="qty_prepared",
    )
    result["kpis"]["total_qty_packing"] = sum(
        int(r["qty_packed"]) for r in result["rows"] if r.get("qty_packed") is not None
    )
    result["kpis"]["total_qty"] = sum(
        int(r["qty_prepared"]) for r in result["rows"] if r.get("qty_prepared") is not None
    )
    result["kpis"]["item_count"] = len({r.get("cup_item") for r in result["rows"] if r.get("cup_item")})
    result["generated_at"] = now_iso()
    return result


class OperatorDailyTargetItem(BaseModel):
    operator_id: str
    email: Optional[str] = None
    qty_per_day: int = 0


class OperatorDailyTargetsPayload(BaseModel):
    targets: List[OperatorDailyTargetItem]


@api_router.get("/analytics/screener")
async def analytics_screener(
    period: Optional[str] = Query("month"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
    user=Depends(require_perm("schedule.view")),
):
    """Read-only screener qty. Uses Screen qty timeline deltas, not PO qty."""
    start, end, preset = resolve_period(period, date_from, date_to)
    pipeline = build_activity_analytics_pipeline(customer, item, po)
    facet_rows = await db.orders.aggregate(pipeline).to_list(1)
    blob = facet_rows[0] if facet_rows else {}
    result = compute_screener_report(
        blob.get("docs") or [],
        date_from=start,
        date_to=end,
        user_id=user_id,
        role=role,
        customer=customer,
        item=item,
        po=po,
    )
    result["period"]["preset"] = preset
    extra_customers = [x.get("_id") for x in (blob.get("customers") or []) if x.get("_id")]
    extra_items = [x.get("_id") for x in (blob.get("items") or []) if x.get("_id")]
    extra_pos = [x.get("_id") for x in (blob.get("pos") or []) if x.get("_id")]
    result["filters"] = {
        **(result.get("filters") or {}),
        "customers": extra_customers,
        "items": extra_items,
        "pos": extra_pos,
    }
    result["rows"] = iter_screener_export_rows(
        blob.get("docs") or [], date_from=start, date_to=end,
        user_id=user_id, role=role, customer=customer, item=item, po=po,
    )
    result["people"] = summarize_people(
        result["rows"], id_key="screener_id", name_key="screener", qty_key="qty_screener",
    )
    result["kpis"]["item_count"] = len({r.get("cup_item") for r in result["rows"] if r.get("cup_item")})
    result["kpis"]["total_qty"] = sum(
        int(r["qty_screener"]) for r in result["rows"] if r.get("qty_screener") is not None
    )
    result["generated_at"] = now_iso()
    return result


@api_router.get("/analytics/designs")
async def analytics_designs(
    period: Optional[str] = Query("month"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    operator_id: Optional[str] = None,
    user_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
    user=Depends(require_perm("schedule.view")),
):
    """Designer report from design_files.uploaded_by. Distinct printable line = 1 design."""
    start, end, preset = resolve_period(period, date_from, date_to)
    who = user_id or operator_id
    pipeline = build_design_analytics_pipeline(customer, item, po)
    facet_rows = await db.orders.aggregate(pipeline).to_list(1)
    blob = facet_rows[0] if facet_rows else {}
    result = compute_designer_report(
        blob.get("docs") or [],
        date_from=start,
        date_to=end,
        user_id=who,
        customer=customer,
        item=item,
        po=po,
    )
    result["period"]["preset"] = preset
    extra_customers = [x.get("_id") for x in (blob.get("customers") or []) if x.get("_id")]
    extra_items = [x.get("_id") for x in (blob.get("items") or []) if x.get("_id")]
    extra_pos = [x.get("_id") for x in (blob.get("pos") or []) if x.get("_id")]
    result["filters"] = {
        **(result.get("filters") or {}),
        "customers": extra_customers,
        "items": extra_items,
        "pos": extra_pos,
    }
    result["people"] = [
        {
            "id": p.get("email"),
            "email": p.get("email"),
            "name": p.get("name"),
            "qty": p.get("design_count") or 0,
            "po_count": p.get("po_count") or 0,
            "item_count": p.get("item_count") or 0,
            "design_count": p.get("design_count") or 0,
        }
        for p in (result.get("designers") or [])
    ]
    result["generated_at"] = now_iso()
    return result


@api_router.get("/analytics/driver")
async def analytics_driver(
    period: Optional[str] = Query("month"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    user_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
    user=Depends(require_perm("schedule.view")),
):
    """Read-only Driver output: confirmed Pengiriman/Terkirim qty, not loading."""
    start, end, preset = resolve_period(period, date_from, date_to)
    pipeline = build_driver_analytics_pipeline(customer, item, po)
    facet_rows = await db.orders.aggregate(pipeline).to_list(1)
    blob = facet_rows[0] if facet_rows else {}
    result = compute_driver_report(
        blob.get("docs") or [],
        date_from=start,
        date_to=end,
        user_id=user_id,
        customer=customer,
        item=item,
        po=po,
    )
    result["period"]["preset"] = preset
    extra_customers = [x.get("_id") for x in (blob.get("customers") or []) if x.get("_id")]
    extra_items = [x.get("_id") for x in (blob.get("items") or []) if x.get("_id")]
    extra_pos = [x.get("_id") for x in (blob.get("pos") or []) if x.get("_id")]
    result["filters"] = {
        **(result.get("filters") or {}),
        "customers": extra_customers,
        "items": extra_items,
        "pos": extra_pos,
    }
    result["generated_at"] = now_iso()
    return result


def _analytics_export_bytes(tab: str, rows: List[dict], fmt: str, date_from=None, date_to=None) -> Tuple[bytes, str, str]:
    cols = EXPORT_COLUMNS.get(tab) or EXPORT_COLUMNS["operator"]
    keys = [c[0] for c in cols]
    headers = [c[1] for c in cols]
    fmt = (fmt or "csv").strip().lower()
    filename = export_filename(tab, date_from or date.today(), date_to or date.today(), fmt)
    if fmt == "xlsx":
        from io import BytesIO
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = tab[:31] or "export"
        ws.append(headers)
        for row in rows:
            ws.append([_export_na(row.get(k, "")) for k in keys])
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename
    import csv
    from io import StringIO
    buf = StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_export_na(row.get(k, "")) for k in keys])
    return buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8", filename


@api_router.get("/analytics/output/export")
async def analytics_output_export(
    tab: str = Query("operator"),
    fmt: str = Query("csv"),
    period: Optional[str] = Query("month"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    operator_id: Optional[str] = None,
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
    user=Depends(require_perm("schedule.view")),
):
    """CSV UTF-8 (Excel-safe) or XLSX export of Analitik Output, matching active filters."""
    start, end, _preset = resolve_period(period, date_from, date_to)
    key = (tab or "operator").strip().lower()
    if key not in EXPORT_COLUMNS:
        raise HTTPException(400, "tab harus operator, helper, screener, design, atau driver")
    who = user_id or operator_id
    if key == "operator":
        pipeline = build_operator_analytics_pipeline(who, customer, item, po)
        blob = (await db.orders.aggregate(pipeline).to_list(1) or [{}])[0]
        targets = normalize_daily_targets(await db.app_settings.find_one({"key": "operator_output_targets"}, {"_id": 0}))
        rows = iter_operator_export_rows(
            blob.get("docs") or [], date_from=start, date_to=end,
            operator_id=who, customer=customer, item=item, po=po, daily_targets=targets,
        )
    elif key == "helper":
        pipeline = build_activity_analytics_pipeline(customer, item, po)
        blob = (await db.orders.aggregate(pipeline).to_list(1) or [{}])[0]
        rows = iter_helper_export_rows(
            blob.get("docs") or [], date_from=start, date_to=end,
            user_id=who, role=role, customer=customer, item=item, po=po,
        )
    elif key == "screener":
        pipeline = build_activity_analytics_pipeline(customer, item, po)
        blob = (await db.orders.aggregate(pipeline).to_list(1) or [{}])[0]
        rows = iter_screener_export_rows(
            blob.get("docs") or [], date_from=start, date_to=end,
            user_id=who, role=role, customer=customer, item=item, po=po,
        )
    elif key == "driver":
        pipeline = build_driver_analytics_pipeline(customer, item, po)
        blob = (await db.orders.aggregate(pipeline).to_list(1) or [{}])[0]
        rows = iter_driver_export_rows(
            blob.get("docs") or [], date_from=start, date_to=end,
            user_id=who, customer=customer, item=item, po=po,
        )
    else:
        pipeline = build_design_analytics_pipeline(customer, item, po)
        blob = (await db.orders.aggregate(pipeline).to_list(1) or [{}])[0]
        rows = iter_design_export_rows(
            blob.get("docs") or [], date_from=start, date_to=end,
            operator_id=who, user_id=who, customer=customer, item=item, po=po,
        )
    payload, media, filename = _analytics_export_bytes(key, rows, fmt, start, end)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return FastAPIResponse(content=payload, media_type=media, headers=headers)


class OperatorWageOverrideIn(BaseModel):
    operator_user_id: Optional[str] = None
    operator_email: Optional[str] = None
    accurate_item_id: Optional[str] = None
    master_item_id: Optional[str] = None
    item_code: Optional[str] = None
    item_name: Optional[str] = None
    rate_per_pcs: Optional[float] = None
    rate_type: Optional[str] = None
    effective_from: Optional[str] = None
    active: bool = True


class OperatorWageOperatorIn(BaseModel):
    operator_user_id: Optional[str] = None
    operator_email: Optional[str] = None
    operator_name: Optional[str] = None
    default_rate_per_pcs: Optional[float] = None
    effective_from: Optional[str] = None
    active: Optional[bool] = True


class OperatorWageBonusIn(BaseModel):
    target_qty: Optional[float] = None
    amount: Optional[float] = None
    mode: Optional[str] = None
    effective_from: Optional[str] = None
    active: Optional[bool] = True


class OperatorWageProductAdditionIn(BaseModel):
    operator_user_id: Optional[str] = None
    operator_email: Optional[str] = None
    accurate_item_id: Optional[str] = None
    master_item_id: Optional[str] = None
    item_code: Optional[str] = None
    item_name: Optional[str] = None
    additional_rate_per_pcs: Optional[float] = None
    effective_from: Optional[str] = None
    active: bool = True


class OperatorWageSettingsPayload(BaseModel):
    default_rate_per_pcs: Optional[float] = None
    operators: Optional[List[OperatorWageOperatorIn]] = None
    overrides: Optional[List[OperatorWageOverrideIn]] = None
    bonus: Optional[OperatorWageBonusIn] = None
    product_additions: Optional[List[OperatorWageProductAdditionIn]] = None


async def _require_gaji_view(user: dict):
    reject_sales_pwa_bypass(user)
    if user.get("role") == "admin":
        return
    eff = await user_permissions(user)
    if "reports.gaji_operator.view" not in eff and "schedule.view" not in eff:
        raise HTTPException(403, "Permission 'reports.gaji_operator.view' diperlukan")


async def _require_order_prediction_view(user: dict):
    reject_sales_pwa_bypass(user)
    if user.get("role") == "admin":
        return
    eff = await user_permissions(user)
    if not can_view_order_prediction(eff):
        raise HTTPException(403, f"Permission '{ORDER_PREDICTION_PERM}' diperlukan")


async def _require_order_prediction_import(user: dict):
    reject_sales_pwa_bypass(user)
    if user.get("role") == "admin":
        return
    eff = await user_permissions(user)
    if ORDER_PREDICTION_IMPORT_PERM not in eff:
        raise HTTPException(403, f"Permission '{ORDER_PREDICTION_IMPORT_PERM}' diperlukan")


async def _load_prediction_history(user: Optional[dict] = None):
    q: dict = {"cancelled": {"$ne": True}}
    if is_sales_pwa_role(user):
        uid = session_user_id(user)
        if not uid:
            return []
        q["sales_user_id"] = uid
    return await db[ORDER_PREDICTION_HISTORY_COLLECTION].find(
        q, {"_id": 0},
    ).to_list(10000)


async def _load_prediction_orders(user: Optional[dict] = None):
    q: dict = {}
    q.update(sales_owner_match(user))
    return await db.orders.find(q, ORDER_PREDICTION_PROJECTION).to_list(5000)


async def _load_prediction_masters():
    return await db[_SALES_CUSTOMERS_COLLECTION].find(
        {}, {"_id": 0, "id": 1, "name": 1},
    ).to_list(1000)


@api_router.get("/reports/order-prediction")
async def order_prediction_report(
    search: Optional[str] = None,
    status: Optional[str] = None,
    pattern: Optional[str] = None,
    confidence: Optional[str] = None,
    sales: Optional[str] = None,
    skip: int = 0,
    limit: int = 500,
    user=Depends(current_user),
):
    await _require_order_prediction_view(user)
    raw = await _load_prediction_orders(user)
    supplemental = await _load_prediction_history(user)
    masters = await _load_prediction_masters()
    sales_filter = "" if is_sales_pwa_role(user) else (sales or "")
    data = build_order_prediction(
        raw,
        search=search or "",
        status=status or "",
        pattern=pattern or "",
        confidence=confidence or "",
        sales=sales_filter,
        skip=skip,
        limit=limit,
        supplemental=supplemental,
        masters=masters,
    )
    data["generated_at"] = now_iso()
    return data


class OrderPredictionImportApply(BaseModel):
    rows: List[dict]


@api_router.post("/reports/order-prediction/import/preview")
async def order_prediction_import_preview(
    file: UploadFile = File(...),
    user=Depends(current_user),
):
    await _require_order_prediction_import(user)
    data = await file.read()
    if not data:
        raise HTTPException(400, "File kosong")
    live = await db.orders.find({}, ORDER_PREDICTION_PROJECTION).to_list(5000)
    existing = await _load_prediction_history()
    try:
        preview = build_history_import_preview(
            data, file.filename or "upload.csv",
            live_orders=live, existing_imports=existing,
        )
    except ImportParseError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "File Excel tidak dapat dibaca.")
    return preview


@api_router.post("/reports/order-prediction/import/apply")
async def order_prediction_import_apply(
    payload: OrderPredictionImportApply,
    user=Depends(current_user),
):
    await _require_order_prediction_import(user)
    live = await db.orders.find({}, ORDER_PREDICTION_PROJECTION).to_list(5000)
    existing = await _load_prediction_history()
    accepted, rejected = validate_history_apply_rows(
        payload.rows or [], live_orders=live, existing_imports=existing,
    )
    if not accepted:
        raise HTTPException(400, {
            "message": "Tidak ada baris valid yang dipilih",
            "rejected": rejected,
        })
    now = now_iso()
    docs = []
    for row in accepted:
        doc = {
            **row,
            "imported_at": now,
            "imported_by": user.get("email"),
            "imported_by_name": user.get("name") or user.get("email"),
            "cancelled": False,
        }
        docs.append(doc)
    if docs:
        await db[ORDER_PREDICTION_HISTORY_COLLECTION].insert_many(docs)
    await _write_audit(
        "reports.order_prediction.import", user, target_email=None,
        before=None,
        after={"imported_count": len(docs), "rejected_count": len(rejected)},
        note="Impor histori Prediksi Order",
    )
    supplemental = await _load_prediction_history()
    masters = await _load_prediction_masters()
    data = build_order_prediction(live, supplemental=supplemental, masters=masters)
    data["generated_at"] = now
    return {
        "imported_count": len(docs),
        "rejected": rejected,
        "rejected_count": len(rejected),
        "prediction": data,
    }


@api_router.get("/reports/order-prediction/{customer_key}")
async def order_prediction_detail(customer_key: str, user=Depends(current_user)):
    await _require_order_prediction_view(user)
    raw = await _load_prediction_orders(user)
    supplemental = await _load_prediction_history(user)
    masters = await _load_prediction_masters()
    data = build_order_prediction_detail(raw, customer_key, supplemental=supplemental, masters=masters)
    if not data:
        raise HTTPException(404, "Customer/outlet tidak ditemukan pada Order Masuk yang valid")
    data["generated_at"] = now_iso()
    return data


@api_router.get("/reports/gaji-operator")
async def gaji_operator_report(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    operator_id: Optional[str] = None,
    operator_ids: Optional[str] = None,
    item: Optional[str] = None,
    user=Depends(current_user),
):
    await _require_gaji_view(user)
    start, end, preset = resolve_period("custom" if (date_from or date_to) else "month", date_from, date_to)
    ids = _parse_operator_ids(operator_id, operator_ids)
    # One aggregation: keep the full operator dropdown; apply selection in compute.
    pipeline = build_operator_analytics_pipeline(None, None, item, None, operator_ids=None)
    blob = (await db.orders.aggregate(pipeline).to_list(1) or [{}])[0]
    wage = await db.app_settings.find_one({"key": OPERATOR_WAGE_SETTINGS_KEY}, {"_id": 0}) or {}
    docs = blob.get("docs") or []
    result = compute_operator_payroll(
        docs,
        wage,
        date_from=start,
        date_to=end,
        operator_ids=ids or None,
        item=item,
    )
    ops, items = _payroll_filter_options(docs)
    result["filters"] = {"operators": ops, "items": items}
    result["period"]["preset"] = preset
    result["generated_at"] = now_iso()
    return result


@api_router.get("/reports/gaji-operator/export")
async def gaji_operator_export(
    fmt: str = Query("xlsx"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    operator_id: Optional[str] = None,
    operator_ids: Optional[str] = None,
    item: Optional[str] = None,
    user=Depends(current_user),
):
    await _require_gaji_view(user)
    start, end, _preset = resolve_period("custom" if (date_from or date_to) else "month", date_from, date_to)
    ids = _parse_operator_ids(operator_id, operator_ids)
    pipeline = build_operator_analytics_pipeline(None, None, item, None, operator_ids=None)
    blob = (await db.orders.aggregate(pipeline).to_list(1) or [{}])[0]
    wage = await db.app_settings.find_one({"key": OPERATOR_WAGE_SETTINGS_KEY}, {"_id": 0}) or {}
    result = compute_operator_payroll(
        blob.get("docs") or [],
        wage,
        date_from=start,
        date_to=end,
        operator_ids=ids or None,
        item=item,
    )
    rows = result.get("rows") or []
    people = result.get("people") or []
    keys = [c[0] for c in PAYROLL_EXPORT_COLUMNS]
    headers = [c[1] for c in PAYROLL_EXPORT_COLUMNS]
    rekap_keys = [c[0] for c in PAYROLL_REKAP_COLUMNS]
    rekap_headers = [c[1] for c in PAYROLL_REKAP_COLUMNS]
    fmt = (fmt or "xlsx").strip().lower()
    filename = f"Gaji_Operator_{start.isoformat()}_{end.isoformat()}." + ("xlsx" if fmt == "xlsx" else "csv")
    numeric = {"qty_completed", "rate_per_pcs", "base_wage", "product_addition", "total_upah", "qty", "bonus"}
    if fmt == "xlsx":
        from io import BytesIO
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Rekap"
        ws.append(rekap_headers)
        for row in people:
            ws.append([row.get(k, "") if k not in numeric else (row.get(k) if row.get(k) is not None else 0) for k in rekap_keys])
        detail = wb.create_sheet("Rincian")
        detail.append(headers)
        for row in rows:
            detail.append([row.get(k, "") if k not in numeric else (row.get(k) if row.get(k) is not None else 0) for k in keys])
        buf = BytesIO()
        wb.save(buf)
        payload, media = buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        import csv
        from io import StringIO
        buf = StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(rekap_headers)
        for row in people:
            writer.writerow([row.get(k, "") if k not in numeric else (row.get(k) if row.get(k) is not None else 0) for k in rekap_keys])
        writer.writerow([])
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(k, "") if k not in numeric else (row.get(k) if row.get(k) is not None else 0) for k in keys])
        payload, media = buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8"
    return FastAPIResponse(content=payload, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@api_router.get("/settings/operator-wage")
async def get_operator_wage_settings(user=Depends(current_user)):
    if user.get("role") != "admin":
        eff = await user_permissions(user)
        if not any(p in eff for p in ("settings.operator_wage.view", "settings.operator_wage.manage", "reports.gaji_operator.view", "schedule.view")):
            raise HTTPException(403, "Permission 'settings.operator_wage.view' diperlukan")
    doc = await db.app_settings.find_one({"key": OPERATOR_WAGE_SETTINGS_KEY}, {"_id": 0}) or {}
    settings = normalize_wage_settings(doc)
    ops = await db.users.find(
        {"role": "operator", "status": {"$ne": "inactive"}},
        {"_id": 0, "password_hash": 0},
    ).to_list(200)
    return {
        "settings": settings,
        "operators": _merge_wage_operators(ops, settings),
    }


@api_router.put("/settings/operator-wage")
async def put_operator_wage_settings(payload: OperatorWageSettingsPayload, user=Depends(current_user)):
    if user.get("role") != "admin":
        await _require_perm(user, "settings.operator_wage.manage")
    prev = await db.app_settings.find_one({"key": OPERATOR_WAGE_SETTINGS_KEY}, {"_id": 0}) or {}
    incoming = {
        "default_rate_per_pcs": payload.default_rate_per_pcs if payload.default_rate_per_pcs is not None else prev.get("default_rate_per_pcs"),
        "operators": (
            [o.model_dump() if hasattr(o, "model_dump") else o.dict() for o in payload.operators]
            if payload.operators is not None else (prev.get("operators") or [])
        ),
        "overrides": (
            [o.model_dump() if hasattr(o, "model_dump") else o.dict() for o in payload.overrides]
            if payload.overrides is not None else (prev.get("overrides") or [])
        ),
        "bonus": (
            (payload.bonus.model_dump() if hasattr(payload.bonus, "model_dump") else payload.bonus.dict())
            if payload.bonus is not None else prev.get("bonus")
        ),
        "product_additions": (
            [o.model_dump() if hasattr(o, "model_dump") else o.dict() for o in payload.product_additions]
            if payload.product_additions is not None else (prev.get("product_additions") or [])
        ),
    }
    err = _validate_wage_settings(incoming)
    if err:
        raise HTTPException(400, err)
    saved = _wage_snapshot_on_save(prev, incoming, user, now_iso())
    saved["key"] = OPERATOR_WAGE_SETTINGS_KEY
    await db.app_settings.update_one({"key": OPERATOR_WAGE_SETTINGS_KEY}, {"$set": saved}, upsert=True)
    await _write_audit(
        "settings.operator_wage.update", user,
        target_email=None,
        before={"operators": (prev.get("operators") or [])[:20]},
        after={"operators": (saved.get("operators") or [])[:20]},
        note="Update upah operator / pcs",
    )
    ops = await db.users.find(
        {"role": "operator", "status": {"$ne": "inactive"}},
        {"_id": 0, "password_hash": 0},
    ).to_list(200)
    return {"settings": normalize_wage_settings(saved), "operators": _merge_wage_operators(ops, saved)}


@api_router.get("/analytics/operator-targets")
async def get_operator_output_targets(user=Depends(require_perm("schedule.view"))):
    """Current daily output targets per operator. Analytics uses this current value for all periods."""
    doc = await db.app_settings.find_one({"key": "operator_output_targets"}, {"_id": 0}) or {}
    targets = doc.get("targets") or []
    if isinstance(targets, dict):
        targets = [
            {"operator_id": k, "email": (v.get("email") if isinstance(v, dict) else k),
             "qty_per_day": (v.get("qty_per_day") if isinstance(v, dict) else v)}
            for k, v in targets.items()
        ]
    ops = await db.users.find(
        {"role": "operator", "status": {"$ne": "inactive"}},
        {"_id": 0, "password_hash": 0},
    ).to_list(200)
    by_email = {t.get("email"): t for t in targets if t.get("email")}
    by_id = {str(t.get("operator_id")): t for t in targets if t.get("operator_id")}
    merged = []
    for op in ops:
        t = by_id.get(str(op.get("id"))) or by_email.get(op.get("email")) or {}
        merged.append({
            "operator_id": op.get("id") or op.get("email"),
            "email": op.get("email"),
            "name": op.get("name") or op.get("email"),
            "qty_per_day": int(t.get("qty_per_day") or 0),
        })
    extra_emails = set(by_email) - {o.get("email") for o in ops}
    for em in sorted(extra_emails):
        t = by_email[em]
        merged.append({
            "operator_id": t.get("operator_id") or em,
            "email": em,
            "name": t.get("name") or em,
            "qty_per_day": int(t.get("qty_per_day") or 0),
        })
    eff = await user_permissions(user)
    can_edit = user.get("role") == "admin" or "schedule.edit" in eff
    return {
        "targets": merged,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "limitation": (
            "Current target only. Settings tidak punya effective_from — perubahan target "
            "berlaku ke semua periode analytics (historical tidak di-restate)."
        ),
        "can_edit": can_edit,
    }


@api_router.put("/analytics/operator-targets")
async def put_operator_output_targets(payload: OperatorDailyTargetsPayload, user=Depends(current_user)):
    await _require_perm(user, "schedule.edit")
    cleaned = []
    seen = set()
    for t in payload.targets or []:
        email = (t.email or t.operator_id or "").strip()
        oid = (t.operator_id or email).strip()
        if not oid:
            continue
        if oid in seen:
            continue
        seen.add(oid)
        qty = int(t.qty_per_day or 0)
        if qty < 0:
            raise HTTPException(400, "Target per hari tidak boleh negatif")
        cleaned.append({"operator_id": oid, "email": email or oid, "qty_per_day": qty})
    doc = {
        "key": "operator_output_targets",
        "targets": cleaned,
        "updated_by": user.get("email"),
        "updated_by_name": user.get("name"),
        "updated_at": now_iso(),
    }
    await db.app_settings.update_one({"key": "operator_output_targets"}, {"$set": doc}, upsert=True)
    await _write_audit(
        action="operator.output_target.settings.update",
        actor=user,
        target_email="-",
        after={"targets": cleaned},
    )
    return {
        "targets": cleaned,
        "updated_at": doc["updated_at"],
        "updated_by": doc["updated_by"],
        "limitation": (
            "Current target only. Settings tidak punya effective_from — perubahan target "
            "berlaku ke semua periode analytics (historical tidak di-restate)."
        ),
    }


# ---------- Reminders (deadline monitoring) ----------
def _deadline_status(deadline_iso: Optional[str], current_stage: int) -> Dict[str, Any]:
    """Compute reminder status for a PO based on deadline vs now.

    Returns a dict with `bucket` in {ok, warn_h1, warn_6h, overdue, done}
    plus `hours_left` (may be negative for overdue).
    """
    if current_stage >= 14:
        return {"bucket": "done", "hours_left": None}
    if not deadline_iso:
        return {"bucket": "ok", "hours_left": None}
    try:
        d = datetime.fromisoformat(deadline_iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
    except Exception:
        return {"bucket": "ok", "hours_left": None}
    delta = d - datetime.now(timezone.utc)
    hours_left = delta.total_seconds() / 3600.0
    if hours_left < 0:
        bucket = "overdue"
    elif hours_left <= 6:
        bucket = "warn_6h"
    elif hours_left <= 24:
        bucket = "warn_h1"
    else:
        bucket = "ok"
    return {"bucket": bucket, "hours_left": round(hours_left, 1)}


# Role responsible for a stage (who currently holds the progress)
STAGE_OWNER_ROLE = {
    0: "admin",
    1: "designer",
    2: "designer",
    3: "admin",
    4: "head_production",
    5: "head_production",
    6: "screener",
    7: "helper",
    8: "operator",
    9: "operator",
    10: "operator",
    11: "helper",
    12: "head_production",
    13: "driver",
    14: "driver",
    15: "driver",
}


_REMINDERS_LIMIT = 1000


def _orders_reminders_operator_splits_expr() -> dict:
    """Email only — reminders operator scope. Preserve array order."""
    return {
        "$map": {
            "input": {"$ifNull": ["$operator_splits", []]},
            "as": "s",
            "in": {
                "operator_email": "$$s.operator_email",
            },
        }
    }


def _orders_reminders_projection() -> dict:
    """Fields read by GET /reminders. Dedicated; not HoP summary or Action Center."""
    return {
        "_id": 0,
        "id": 1,
        "po_number": 1,
        "outlet": 1,
        "customer": 1,
        "deadline": 1,
        "priority": 1,
        "current_stage": 1,
        "status_label": 1,
        "operator_splits": _orders_reminders_operator_splits_expr(),
    }


def _shape_order_for_reminders_compute(order: dict) -> dict:
    """In-memory mirror of `_orders_reminders_projection` (tests/parity)."""
    src = order or {}
    return {
        "id": src.get("id"),
        "po_number": src.get("po_number"),
        "outlet": src.get("outlet"),
        "customer": src.get("customer"),
        "deadline": src.get("deadline"),
        "priority": src.get("priority"),
        "current_stage": src.get("current_stage"),
        "status_label": src.get("status_label"),
        "operator_splits": [
            {"operator_email": s.get("operator_email")}
            for s in (src.get("operator_splits") or [])
            if isinstance(s, dict)
        ],
    }


@api_router.get("/reminders")
async def reminders(user=Depends(current_user)):
    """Return active POs with reminder status. Filters by role — HoP/admin see all,
    other roles see only reminders where they are the current owner."""
    orders = await db.orders.aggregate([
        {"$limit": _REMINDERS_LIMIT},
        {"$project": _orders_reminders_projection()},
    ]).to_list(_REMINDERS_LIMIT)
    role = user.get("role")
    role_email = user.get("email")
    items = []
    for o in orders:
        if o.get("current_stage", 0) >= 14:
            continue
        ds = _deadline_status(o.get("deadline"), o.get("current_stage", 0))
        if ds["bucket"] == "ok":
            continue
        owner_role = STAGE_OWNER_ROLE.get(o.get("current_stage", 0), "admin")
        # Filter for shop-floor roles
        if role not in ("admin", "head_production", "crm"):
            if owner_role != role:
                continue
            # For operator, only if they are on this PO's operator_splits
            if role == "operator":
                is_on = any(s.get("operator_email") == role_email for s in o.get("operator_splits", []))
                if not is_on:
                    continue
        items.append({
            "id": o.get("id"),
            "po_number": o.get("po_number"),
            "outlet": o.get("outlet"),
            "customer": o.get("customer"),
            "deadline": o.get("deadline"),
            "priority": o.get("priority"),
            "current_stage": o.get("current_stage"),
            "status_label": o.get("status_label"),
            "owner_role": owner_role,
            "bucket": ds["bucket"],
            "hours_left": ds["hours_left"],
        })
    # Sort by severity: overdue first, then 6h, then h1
    order_map = {"overdue": 0, "warn_6h": 1, "warn_h1": 2}
    items.sort(key=lambda x: (order_map.get(x["bucket"], 9), x["hours_left"] if x["hours_left"] is not None else 999))
    return {
        "reminders": items,
        "counts": {
            "overdue": sum(1 for i in items if i["bucket"] == "overdue"),
            "warn_6h": sum(1 for i in items if i["bucket"] == "warn_6h"),
            "warn_h1": sum(1 for i in items if i["bucket"] == "warn_h1"),
        },
        "role_scope": "all" if role in ("admin", "head_production", "crm") else "own",
        "generated_at": now_iso(),
    }


# ---------- Users ----------
@api_router.get("/users/operators")
async def list_operators(user=Depends(current_user)):
    """Only ACTIVE operators are selectable for new assignments. Inactive
    users remain visible in historical splits/timelines but are never offered
    as a new assignment target."""
    ops = await db.users.find(
        {"role": "operator", "status": {"$ne": "inactive"}},
        {"_id": 0, "password_hash": 0}
    ).to_list(50)
    return {"operators": ops}


# ---------- App bootstrap ----------
_delivery_route_api.register(api_router, {
    "db": db,
    "current_user": current_user,
    "require_perm": _require_perm,
    "require_any_perm": _require_any_perm,
    "user_permissions": user_permissions,
    "Depends": Depends,
})

app.include_router(api_router)

# ---- Customer Design Approval router (registered before CORS/middleware) ----
import customer_approval as _ca_mod
_ca_router = _ca_mod.build_router({
    "db": db,
    "current_user": current_user,
    "require_perm": _require_perm,
    "persist_order": _persist_order,
    "audit_po": _audit_po,
    "add_timeline": add_timeline,
    "sync_workflow_stage": _sync_workflow_stage,
    "obj_storage": obj_storage,
    "logger": logging.getLogger("customer_approval"),
})
app.include_router(_ca_router)

import services.customer_tracking as _ct_mod
_ct_router = _ct_mod.build_router({
    "db": db,
    "get_db": lambda: db,
    "current_user": current_user,
    "require_perm": _require_perm,
    "logger": logging.getLogger("customer_tracking"),
    "obj_storage": obj_storage,
})
app.include_router(_ct_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Type"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
_ct_mod.install_access_log_mask()


async def _ensure_unique_business_number_index(collection, field: str) -> None:
    """Additive unique index for PO/SJ/INV/PAY business numbers.

    Idempotent. If historical duplicates exist, skip without crashing startup.
    """
    try:
        await collection.create_index(field, unique=True)
    except DuplicateKeyError as exc:
        logger.warning(
            "Unique index skipped (duplicates exist): %s.%s — %s",
            getattr(collection, "name", collection),
            field,
            exc,
        )
    except OperationFailure as exc:
        msg = str(exc)
        if getattr(exc, "code", None) == 11000 or "E11000" in msg or "duplicate key" in msg.lower():
            logger.warning(
                "Unique index skipped (duplicates exist): %s.%s — %s",
                getattr(collection, "name", collection),
                field,
                exc,
            )
            return
        raise


@app.on_event("shutdown")
async def shutdown_db_client():
    from services.sales_activity.evaluator import stop_evaluator_loop
    stop_evaluator_loop()
    client.close()


@app.on_event("startup")
async def seed():
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True, sparse=True)
    await db.users.create_index("employee_id", sparse=True)
    await db.orders.create_index("id", unique=True)
    await _ensure_unique_business_number_index(db.orders, "po_number")
    await db.orders.create_index("sales_user_id", sparse=True)
    await db.orders.create_index("accurate_id", sparse=True)
    await db.orders.create_index(
        [("tracking_token", 1)],
        unique=True,
        name="tracking_token_unique",
        partialFilterExpression={"tracking_token": {"$exists": True, "$gt": ""}},
    )
    await db.master_items.create_index("id", unique=True)
    await db[MASTER_ITEMS_COLLECTION].create_index("accurate_item_id", unique=True)
    await db[STOCK_MANUAL_COLLECTION].create_index("id", unique=True)
    await db[STOCK_MANUAL_COLLECTION].create_index("accurate_item_id", unique=True, sparse=True)
    await db[STOCK_MANUAL_COLLECTION].create_index("master_item_id", unique=True, sparse=True)
    await db[ORDER_PREDICTION_HISTORY_COLLECTION].create_index("id", unique=True)
    await db[ORDER_PREDICTION_HISTORY_COLLECTION].create_index("fingerprint", unique=True, sparse=True)
    await db[ORDER_PREDICTION_HISTORY_COLLECTION].create_index("order_date")
    await db[RECEIPT_COLLECTION].create_index("accurate_receipt_id", unique=True)
    await db[RECEIPT_SYNC_META_COLLECTION].create_index("id", unique=True)
    await db.files.create_index("id", unique=True)
    await db.files.create_index("storage_path")
    await db.import_logs.create_index("imported_at")
    await db.import_logs.create_index("accurate_id")
    await db.audit_logs.create_index("at")
    await db.audit_logs.create_index("actor_email")
    await db.audit_logs.create_index("target_email")
    await db.role_permissions.create_index("role", unique=True)
    await db.roles.create_index("role", unique=True)
    # Customer Approval indexes
    await db.customer_approvals.create_index("id", unique=True)
    await db.customer_approvals.create_index("token_hash", unique=True)
    await db.customer_approvals.create_index("order_id")
    await db.customer_approvals.create_index("status")
    await db.app_settings.create_index("key", unique=True)
    await db.delivery_batches.create_index("id", unique=True)
    await db.delivery_batches.create_index("status")
    await db.delivery_route_plans.create_index("id", unique=True)
    await db.delivery_route_plans.create_index("batch_id")
    await db.delivery_route_plans.create_index("status")
    await db.delivery_route_plans.create_index("routes.driver_user_id")
    await db.delivery_vehicles.create_index("id", unique=True)
    # Chart of Accounts (Akun Perkiraan) — indexes only; seed is manual CLI
    await db[LEDGER_ACCOUNTS_COLLECTION].create_index("id", unique=True)
    await db[LEDGER_ACCOUNTS_COLLECTION].create_index("code", unique=True)
    # Sales Deliveries (Pengiriman Pesanan) indexes
    await db.sales_deliveries.create_index("id", unique=True)
    await _ensure_unique_business_number_index(db.sales_deliveries, "sj_number")
    await db.sales_deliveries.create_index([("po_id", 1), ("shipment_index", 1)], unique=True)
    await db.sales_deliveries.create_index("po_id")
    await db.sales_deliveries.create_index("created_at")
    # Sales Invoices (Faktur Penjualan) indexes
    await db.sales_invoices.create_index("id", unique=True)
    await _ensure_unique_business_number_index(db.sales_invoices, "invoice_number")
    await db.sales_invoices.create_index("po_id")
    await db.sales_invoices.create_index("created_at")
    await db.sales_invoices.create_index("invoice_date")
    await db.sales_invoices.create_index("financial_status")
    
    await db.sales_receipts.create_index("id", unique=True)
    await _ensure_unique_business_number_index(db.sales_receipts, "receipt_number")
    await db.sales_receipts.create_index("invoice_id")
    await db.sales_receipts.create_index("created_at")
    await db.sales_receipts.create_index("customer_id")
    from services.sales_customer_credit import ensure_indexes as _credit_ensure_indexes
    await _credit_ensure_indexes(db)
    await _ensure_sales_customer_indexes(db)
    # Purchasing documents — additive unique business numbers (not sales PO/SJ/INV/PAY)
    await db.purchase_requests.create_index("id", unique=True)
    await _ensure_unique_business_number_index(db.purchase_requests, "request_number")
    await db.purchase_requests.create_index("created_at")
    from services.product_requests import ensure_indexes as _product_request_ensure_indexes
    await _product_request_ensure_indexes(db)
    from services.spk import ensure_indexes as _spk_ensure_indexes
    await _spk_ensure_indexes(db)
    await db.purchase_orders.create_index("id", unique=True)
    await _ensure_unique_business_number_index(db.purchase_orders, "po_number")
    await db.purchase_orders.create_index("request_id")
    await db.purchase_orders.create_index("created_at")
    await db.purchase_goods_receipts.create_index("id", unique=True)
    await _ensure_unique_business_number_index(db.purchase_goods_receipts, "receipt_number")
    await db.purchase_goods_receipts.create_index("purchase_order_id")
    await db.purchase_goods_receipts.create_index("created_at")
    await db.purchase_receipt_po_links.create_index("id", unique=True)
    await db.purchase_receipt_po_links.create_index("purchase_order_id")
    await db.purchase_receipt_po_links.create_index("accurate_receipt_id")
    await db.purchase_invoices.create_index("id", unique=True)
    await _ensure_unique_business_number_index(db.purchase_invoices, "invoice_number")
    await db.purchase_invoices.create_index("purchase_order_id")
    await db.purchase_invoices.create_index("created_at")
    await db.purchase_payments.create_index("id", unique=True)
    await _ensure_unique_business_number_index(db.purchase_payments, "payment_number")
    await db.purchase_payments.create_index("purchase_invoice_id")
    await db.purchase_payments.create_index("purchase_order_id")
    await db.purchase_payments.create_index("created_at")
    await db.employee_request_types.create_index("id", unique=True)
    await db.employee_request_types.create_index("code", unique=True)
    await db.employee_requests.create_index("id", unique=True)
    await _ensure_unique_business_number_index(db.employee_requests, "request_number")
    await db.employee_requests.create_index("employee.id")
    await db.employee_requests.create_index("status")
    await db.employee_requests.create_index("type_id")
    await db.employee_requests.create_index("submitted_at")
    from services.hr_payroll import ensure_indexes as _hr_payroll_ensure_indexes
    await _hr_payroll_ensure_indexes(db)
    from services.sales_activity.indexes import ensure_indexes as _sales_activity_ensure_indexes
    await _sales_activity_ensure_indexes(db)
    from services.independent_route.store import ensure_indexes as _independent_route_ensure_indexes
    await _independent_route_ensure_indexes(db)
    from services.sales_activity.evaluator import start_evaluator_loop
    start_evaluator_loop(db)
    for coll in (db.purchase_requests, db.purchase_orders, db.purchase_goods_receipts, db.purchase_invoices, db.purchase_payments):
        for idx_name in ("idempotency_key_1", "idempotency_key_partial_unique"):
            try:
                await coll.drop_index(idx_name)
            except Exception:
                pass
        await coll.create_index(
            "idempotency_key",
            unique=True,
            partialFilterExpression={"idempotency_key": {"$type": "string"}},
            name="idempotency_key_partial_unique",
        )
    await db.hpp_cost_versions.create_index("id", unique=True)
    await db.hpp_cost_versions.create_index("version")
    await db.hpp_cost_versions.create_index("status")
    await db.hpp_cost_layers.create_index("calculation_version")
    await db.hpp_cost_layers.create_index([("calculation_version", 1), ("layer_id", 1)], unique=True)
    from services.hpp_cutover import ensure_indexes as _hpp_cutover_ensure_indexes
    await _hpp_cutover_ensure_indexes(db)
    from services.financial_inventory_opening import ensure_indexes as _financial_opening_ensure_indexes
    await _financial_opening_ensure_indexes(db)
    from services.customer_pricing import ensure_indexes as _customer_pricing_ensure_indexes
    await _customer_pricing_ensure_indexes(db)
    from services.cash_bank_master import ensure_indexes as _cash_bank_master_ensure_indexes
    from services.cash_bank_vouchers import ensure_indexes as _cash_bank_voucher_ensure_indexes
    from services.journal_core import ensure_indexes as _journal_core_ensure_indexes
    from services.cash_bank_reconciliation import ensure_indexes as _cash_bank_recon_ensure_indexes
    from services.general_journal import ensure_indexes as _general_journal_ensure_indexes
    from services.opening_balance import ensure_indexes as _opening_balance_ensure_indexes
    from services.accounting_periods import ensure_indexes as _accounting_periods_ensure_indexes
    from services.accounting_audit import ensure_indexes as _accounting_audit_ensure_indexes
    from services.accounts_receivable import ensure_indexes as _accounts_receivable_ensure_indexes
    from services.accounts_payable import ensure_indexes as _accounts_payable_ensure_indexes
    from services.inventory_issue_accounting import ensure_indexes as _inventory_issue_ensure_indexes
    await _cash_bank_master_ensure_indexes(db)
    await _cash_bank_voucher_ensure_indexes(db)
    await _journal_core_ensure_indexes(db)
    await _cash_bank_recon_ensure_indexes(db)
    await _general_journal_ensure_indexes(db)
    await _opening_balance_ensure_indexes(db)
    await _accounting_periods_ensure_indexes(db)
    await _accounting_audit_ensure_indexes(db)
    await _accounts_receivable_ensure_indexes(db)
    await _accounts_payable_ensure_indexes(db)
    await _inventory_issue_ensure_indexes(db)
    await ensure_chat_indexes(db)
    try:
        await ensure_v2_physical_settings(db)
    except Exception as e:
        logger.warning("Stock Engine V2 settings ensure failed: %s", e)
    from services.notification_indexes import ensure_indexes as ensure_notification_indexes
    await ensure_notification_indexes(db)
    # Object storage init (idempotent) — do not crash the app if unavailable
    try:
        obj_storage.init_storage()
    except Exception as e:
        logger.warning("Object storage init failed: %s", e)

    # ---- Task 13 workflow-15 bugfix: Migrate legacy current_stage 14/15 ----
    # Old workflow had 16 stages including "Driver" at index 14 and "Terkirim"
    # at 15. New workflow has 15 stages (Driver removed as stage — hanya
    # metadata). Migration is IDEMPOTENT dan BOUNDED:
    #   • current_stage == 15                     → 14 (Terkirim)
    #   • current_stage == 14 + delivered flag    → 14 (Terkirim)
    #   • current_stage == 14 + belum delivered   → 13 (Loading) — dulu Driver
    #                                                 in-transit, sekarang
    #                                                 tetap di Loading sampai
    #                                                 delivery diselesaikan.
    # Timeline/history Driver TIDAK dihapus.
    migrated = 0
    async for o in db.orders.find({"current_stage": {"$in": [14, 15]}}, {"id": 1, "current_stage": 1, "delivery_status": 1}):
        cs = o.get("current_stage")
        new_cs = None
        if cs == 15:
            new_cs = 14
        elif cs == 14:
            if o.get("delivery_status") == "Terkirim":
                new_cs = 14  # tetap 14 (Terkirim)
            else:
                new_cs = 13  # legacy Driver in-transit → new Loading
        if new_cs is not None and new_cs != cs:
            await db.orders.update_one({"id": o["id"]}, {"$set": {"current_stage": new_cs}})
            migrated += 1
    if migrated:
        logger.info("Task 13 stage migration: %d orders remapped (legacy 14/15 → new 13/14).", migrated)

    # ---- Migrate existing users (idempotent) ----
    now = now_iso()
    async for u in db.users.find({}):
        patch = {}
        if "id" not in u:
            patch["id"] = str(uuid.uuid4())
        if "employee_id" not in u:
            patch["employee_id"] = await _next_employee_id()
        if "status" not in u:
            patch["status"] = "active"
        if "permissions_override" not in u:
            patch["permissions_override"] = {}
        for k, default in (("nickname", None), ("phone", None), ("joined_at", None),
                           ("deactivated_at", None), ("notes", None),
                           ("created_at", u.get("_id").generation_time.isoformat() if u.get("_id") else now),
                           ("updated_at", now)):
            if k not in u:
                patch[k] = default
        # Migrate legacy operasional trial (was head_production) → head_operations
        if u.get("email") == "operasional@eternalpackaging.com" and u.get("role") == "head_production":
            patch["role"] = "head_operations"
            patch["name"] = "Kepala Operasional"
        if patch:
            await db.users.update_one({"_id": u["_id"]}, {"$set": patch})
    defaults = [
        ("admin@eternalpackaging.com", "Admin", "admin"),
        ("production@eternalpackaging.com", "Kepala Produksi", "head_production"),
        # Kepala Operasional sekarang punya role backend sendiri (head_operations).
        ("operasional@eternalpackaging.com", "Kepala Operasional", "head_operations"),
        ("designer@eternalpackaging.com", "Ayu Designer", "designer"),
        ("screener@eternalpackaging.com", "Beni Screener", "screener"),
        ("helper@eternalpackaging.com", "Cici Helper", "helper"),
        # Trial helper terpisah untuk skenario persiapan vs packing — semua
        # tetap role 'helper' backend (tidak ada perubahan workflow).
        ("helper.persiapan@eternalpackaging.com", "Helper Persiapan (trial)", "helper"),
        ("helper.packing@eternalpackaging.com", "Helper Packing (trial)", "helper"),
        ("operator@eternalpackaging.com", "Rafi Operator", "operator"),
        ("operator2@eternalpackaging.com", "Nanda Operator", "operator"),
        ("driver@eternalpackaging.com", "Budi Driver", "driver"),
        ("crm@eternalpackaging.com", "CRM Viewer", "crm"),
    ]
    # Trial password (rotate before production). Loaded from env if provided
    # so it can be changed without editing code; hash is stored in DB.
    trial_pwd = os.environ.get("TRIAL_DEFAULT_PASSWORD", "Eternal123!")
    for email, name, role in defaults:
        if not await db.users.find_one({"email": email}):
            await db.users.insert_one({
                "id": str(uuid.uuid4()),
                "employee_id": await _next_employee_id(),
                "email": email,
                "name": name,
                "nickname": None,
                "phone": None,
                "role": role,
                "status": "active",
                "joined_at": None,
                "deactivated_at": None,
                "notes": None,
                "permissions_override": {},
                "password_hash": hash_password(trial_pwd),
                "created_at": now_iso(),
                "updated_at": now_iso(),
            })

    # Seed orders — DISABLED by default for production. Enable dengan
    # env `SEED_DEMO_ORDERS=1` untuk sandbox/testing. Menghindari data demo
    # muncul otomatis setelah operasional dimulai.
    if os.environ.get("SEED_DEMO_ORDERS", "0") == "1":
        if await db.orders.count_documents({}) == 0:
            for o in build_seed_orders():
                await db.orders.insert_one(o)
            logger.info("Seeded %d demo orders (SEED_DEMO_ORDERS=1)", await db.orders.count_documents({}))
    else:
        logger.info("Demo order seeding DISABLED (production mode). Set SEED_DEMO_ORDERS=1 to enable.")

    # Seed roles collection (idempotent — one row per built-in role)
    await _seed_roles_collection()

    try:
        await bootstrap_chat_system_groups(db)
    except Exception as e:
        logger.warning("Chat system group bootstrap failed: %s", e)

    # Phase B migration: patch stale role_permissions overrides so newly added
    # built-in permissions (dashboard.production.*, po.cancel/po.delete on
    # head_operations) are added to any existing customization. This is idempotent.
    PHASE_B_ROLE_TOP_UPS = {
        "head_production": [
            "dashboard.production.view", "dashboard.production.export",
            # Req 13 + Req 16 top-ups (idempotent)
            "schedule.work_date.view", "schedule.work_date.edit",
            "dashboard.production.input", "dashboard.production.edit",
            # Workflow 06-12 targeted bugfix
            "operator.sample_approve",
            # Customer Approval
            "customer_approval.view", "customer_approval.manage", "customer_approval.settings",
            # Delivery Deadline (Batch 1)
            "delivery.deadline.edit", "delivery.deadline.settings",
            # Batch 3: Production routing (Cetak/Non-Cetak)
            "production.route.edit",
            # Buffer Stock (CRM workflow)
            "buffer_stock.manage",
            "current_stock.view", "current_stock.update", "current_stock.import", "current_stock.export",
            "purchase_receipts.view", "purchase_receipts.sync", "purchase_receipts.export",
            "nav.inventory.current_stock",
            "nav.purchasing.receipts",
            "sales_order.view", "sales_order.create", "nav.sales.sales_order",
        ],
        "head_operations": [
            "dashboard.production.view", "dashboard.production.export", "po.cancel",
            "schedule.work_date.view", "schedule.work_date.edit",
            "dashboard.production.input", "dashboard.production.edit",
            # Delivery Deadline (Batch 1)
            "delivery.deadline.edit", "delivery.deadline.settings",
            # Batch 3: Production routing (Cetak/Non-Cetak)
            "production.route.edit",
            "buffer_stock.manage",
            "current_stock.view", "current_stock.update", "current_stock.import", "current_stock.export",
            "purchase_receipts.view", "purchase_receipts.sync", "purchase_receipts.export",
            "nav.inventory.current_stock",
            "nav.purchasing.receipts",
            "sales_order.view", "sales_order.create", "nav.sales.sales_order",
        ],
        # Import PO permissions: admin gets all (idempotent top-up if a custom
        # admin override was persisted before these keys existed).
        "admin": [
            "po.import.view", "accurate.import", "accurate.import.bulk",
            "po.import.pdf", "po.import.spreadsheet",
            "sales_order.view", "sales_order.create", "nav.sales.sales_order",
            "master_items.view", "master_items.sync",
            "nav.inventory.master_items",
            "current_stock.view", "current_stock.update", "current_stock.import", "current_stock.export",
            "purchase_receipts.view", "purchase_receipts.sync", "purchase_receipts.export",
            "nav.inventory.current_stock",
            "nav.purchasing.receipts",
            # Req 13/14/16 top-ups (idempotent) — admin should always have every new perm.
            "schedule.work_date.view", "schedule.work_date.edit",
            "dashboard.production.input", "dashboard.production.edit",
            # Workflow 06-12 targeted bugfix
            "operator.sample_approve",
            # Customer Approval — admin punya semua.
            "customer_approval.view", "customer_approval.manage", "customer_approval.settings",
            # Delivery Deadline (Batch 1)
            "delivery.deadline.edit", "delivery.deadline.settings",
            # Batch 3: Production routing (Cetak/Non-Cetak)
            "production.route.edit",
            "buffer_stock.manage",
        ],
        "designer": [
            "customer_approval.view", "customer_approval.manage",
        ],
        "crm": [
            "customer_approval.view",
            "buffer_stock.manage",
            "sales_order.view", "sales_order.create", "nav.sales.sales_order",
        ],
    }
    for role_slug, need in PHASE_B_ROLE_TOP_UPS.items():
        override = await db.role_permissions.find_one({"role": role_slug}, {"_id": 0})
        if not override:
            continue
        missing = [p for p in need if p not in (override.get("permissions") or [])]
        if missing:
            await db.role_permissions.update_one(
                {"role": role_slug},
                {"$addToSet": {"permissions": {"$each": missing}}, "$set": {"updated_at": now_iso(), "updated_by": "system.migration.phaseB"}},
            )
            logger.info("Phase B migration: %s override topped-up with %s", role_slug, missing)

    # Internal Work Chat: grant use/nav to every stored role override (idempotent).
    # chat.group.manage / chat.moderate stay admin-only unless explicitly granted.
    async for override in db.role_permissions.find({}, {"_id": 0, "role": 1, "permissions": 1}):
        role_slug = override.get("role")
        if not role_slug:
            continue
        need = ["chat.use", "nav.chat"]
        missing = [p for p in need if p not in (override.get("permissions") or [])]
        if missing:
            await db.role_permissions.update_one(
                {"role": role_slug},
                {"$addToSet": {"permissions": {"$each": missing}}, "$set": {"updated_at": now_iso(), "updated_by": "system.migration.chat_v1"}},
            )
            logger.info("Chat V1 migration: %s override topped-up with %s", role_slug, missing)

    # Bootstrap Super Admin: if none exists, promote admin@eternalpackaging.com
    if await db.users.count_documents({"is_super_admin": True}) == 0:
        seed_admin = await db.users.find_one({"email": "admin@eternalpackaging.com"})
        if seed_admin:
            await db.users.update_one({"id": seed_admin["id"]}, {"$set": {"is_super_admin": True, "updated_at": now_iso()}})
            logger.info("Bootstrapped Super Admin: %s", seed_admin["email"])
    # Ensure admin@ canonical role/name (drift protection)
    admin_seed = await db.users.find_one({"email": "admin@eternalpackaging.com"})
    if admin_seed and (admin_seed.get("role") != "admin" or admin_seed.get("name") != "Admin"):
        await db.users.update_one({"id": admin_seed["id"]}, {"$set": {"role": "admin", "name": "Admin", "updated_at": now_iso()}})
