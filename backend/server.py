from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, UploadFile, File, Form, Header, Query
from fastapi.responses import Response as FastAPIResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os, logging, uuid, base64, re, secrets, hashlib
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Any, Dict, Tuple
from datetime import date, datetime, timezone, timedelta
import bcrypt
import jwt
from services.accurate import (
    AccurateClient,
    ACCURATE_DELIVERY_ENDPOINT_AVAILABLE,
    ACCURATE_DELIVERY_ENDPOINT_REASON,
    fetch_delivery_notes,
    get_last_list_error,
    get_mock_delivery_note,
    log_master_items_sync_failure,
    sanitize_accurate_error,
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
    resolve_po_line_items,
    sync_master_items,
)
from services.shipment_status import (
    SHIPMENT_STATUS_PROJECTION,
    shipment_status_row,
)
from services.operator_schedule import (
    apply_operator_assignments as _apply_operator_assignments,
    cancel_split_remaining as _cancel_split_remaining,
    pending_splits_for_user as _pending_splits_for_user,
)
from services.operator_payroll import (
    PAYROLL_EXPORT_COLUMNS,
    SETTINGS_KEY as OPERATOR_WAGE_SETTINGS_KEY,
    compute_operator_payroll,
    item_identity as _wage_item_identity,
    operators_and_items as _payroll_filter_options,
    snapshot_on_save as _wage_snapshot_on_save,
    effective_rate_as_of as _wage_effective_rate,
    normalize_wage_settings,
    parse_operator_ids as _parse_operator_ids,
    merge_operators_for_settings as _merge_wage_operators,
)
from services.payment_files import (
    MAX_PAYMENT_PROOFS,
    payment_proof_reject_reason,
)
from services.status_po import build_status_po
from services.workflow_entries import append_qty_entry, find_by_action_id, seed_legacy, sum_entry_qty
from services.customer_tracking import strip_tracking_secret as _strip_tracking_secret
from services.work_panel_settings import SETTINGS_KEY as WORK_PANEL_SETTINGS_KEY
from services import work_panel_settings as work_panel_cfg
from services.dashboard_today import (
    SHIP_PROJECTION as TODAY_SHIP_PROJECTION,
    WORK_PROJECTION as TODAY_WORK_PROJECTION,
    build_shipments_today,
    build_work_today,
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
from services.purchase_receipts import (
    COLLECTION as RECEIPT_COLLECTION,
    PURCHASE_SOURCE_ACCURATE as STOCK_PURCHASE_ACCURATE,
    SYNC_META_COLLECTION as RECEIPT_SYNC_META_COLLECTION,
    filter_public_receipts,
    last_success_at as receipt_last_success_at,
    load_receipts,
    load_sync_meta,
    page_public_receipts,
    public_receipt,
    purchase_source_from_meta,
    sync_purchase_receipts,
)
from permissions import (
    PERMISSION_CATALOG, ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS,
    ROLE_LABELS, STAGE_PERMISSION, effective_permissions,
    NAV_MENU_ITEMS, NAV_CONFIGURED, with_nav_defaults,
)
from services.division_tasks import (
    DIVISION_DEFS as _DIVISION_TASK_DEFS,
    TASK_PROJECTION as _DIVISION_TASK_PROJECTION,
    build_division_tasks as _build_division_tasks,
    can_view_all_division as _can_view_all_division,
    resolve_division as _resolve_division,
)
from operator_analytics import (
    build_activity_analytics_pipeline,
    build_design_analytics_pipeline,
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
    compute_output_series,
    compute_screener_report,
    export_filename,
    iter_design_export_rows,
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
    valid_delivery_proof_count as _qr_valid_delivery_proof_count,
    apply_loading_increments as _qr_apply_loading_increments,
    effective_loaded_qty as _qr_effective_loaded_qty,
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
    surat_jalan_status_semantic as _qr_surat_jalan_status_semantic,
    validate_delivery_qty as _qr_validate_delivery_qty,
    validate_helper_prepared_qty as _qr_validate_helper_prepared_qty,
    validate_loading_increments as _qr_validate_loading_increments,
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
    SCREENER_GATE_OPERATOR_MSG,
    SCREENER_GATE_SAMPLE_MSG,
    design_ok as _wg_design_ok,
    helper_done as _wg_helper_done,
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


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

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
    "Terkirim",            # 14  (Driver dihapus sebagai stage — hanya metadata)
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
    async def checker(user=Depends(current_user)):
        eff = await user_permissions(user)
        for p in perms:
            if p not in eff:
                raise HTTPException(403, f"Permission '{p}' diperlukan")
        return user
    return checker


def require(*roles):
    async def checker(user=Depends(current_user)):
        if roles and user.get("role") not in roles and user.get("role") != "admin":
            raise HTTPException(403, "Aksi ini tidak tersedia untuk role Anda")
        return user
    return checker


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
    response.set_cookie("access_token", token_for(safe), httponly=True, samesite="lax", max_age=28800)
    return safe


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
        "email": user.get("email"),
        "role": user.get("role"),
        "name": user.get("name"),
        "permissions": perms,
        "is_super_admin": is_sa,
    }


# ---------- Orders ----------
@api_router.get("/orders")
async def list_orders(status: Optional[str] = None, user=Depends(current_user)):
    """List orders. Optional `status` filter:
       - `aktif`   → non-cancelled, not HOLD, current_stage<14 (default)
       - `hold`    → non-cancelled, currently HOLD
       - `selesai` → non-cancelled, current_stage>=14
       - `cancelled` → cancelled only
       - `all`     → everything (used for admin/debug)
       - `stage_view` → non-cancelled ONLY (no HOLD/stage filter). Used by
         mobile workflow-shortcut list so `stage_counts` == filtered list.
    """
    q: dict = {}
    st = (status or "aktif").lower()
    if st == "cancelled":
        q["cancelled"] = True
    else:
        # Non-cancelled by default (Aktif/HOLD/Selesai/stage_view)
        q["cancelled"] = {"$ne": True}
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


@api_router.get("/orders/{order_id}")
async def get_order(order_id: str, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
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
    _attach_workflow_snapshot(order)
    # Batch 1: delivery deadline effective/source/status
    _dd_settings = await _get_delivery_deadline_settings()
    _attach_deadline_snapshot(order, _dd_settings["lead_time_days"])
    _strip_tracking_secret(order)
    return {"order": order}


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
    """Base timestamp untuk perhitungan deadline otomatis.
    Ambil timestamp completion semantik dari Design & Payment approval:
      - Design: `design_approved_at` (approved) atau `design_bypassed_at` (bypass).
      - Payment: `payment_approved_at` (approved) atau `payment_bypassed_at` (bypass).
    Precedence eksplisit: approved_at > bypassed_at. Jika salah satu belum
    complete (baik approved maupun bypass), return None → tidak menghitung
    deadline otomatis.
    """
    design_at = order.get("design_approved_at") or order.get("design_bypassed_at")
    payment_at = order.get("payment_approved_at") or order.get("payment_bypassed_at")
    if not (design_at and payment_at):
        return None
    try:
        dd = datetime.fromisoformat(str(design_at).replace("Z", "+00:00"))
        pd_ = datetime.fromisoformat(str(payment_at).replace("Z", "+00:00"))
    except Exception:
        return None
    latest = max(dd, pd_)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return latest.isoformat()


def _calculated_delivery_deadline(order: dict, lead_time_days: int) -> Optional[str]:
    base = _latest_approval_at(order)
    if not base:
        return None
    try:
        dt = datetime.fromisoformat(base.replace("Z", "+00:00"))
    except Exception:
        return None
    return (dt + timedelta(days=int(lead_time_days))).isoformat()


def _deadline_status_for(deadline_iso: Optional[str], order: dict) -> str:
    """Predicate priority: overdue > h6 > h1 > normal. Kalau PO Terkirim →
    'terkirim' (warning aktif hilang tapi deadline tetap ditampilkan untuk audit)."""
    if order.get("delivery_status") == "Terkirim" or (order.get("current_stage") or 0) >= 14:
        return "terkirim"
    if not deadline_iso:
        return "unknown"
    try:
        dt = datetime.fromisoformat(deadline_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return "unknown"
    now = datetime.now(timezone.utc)
    delta = dt - now
    if delta.total_seconds() <= 0:
        return "overdue"
    hours = delta.total_seconds() / 3600.0
    if hours <= 6:
        return "h6"
    if hours <= 24:
        return "h1"
    return "normal"


def _attach_deadline_snapshot(order: dict, lead_time_days: int):
    """Compute derived fields (non-persisted): calculated + effective + status.
    Manual override disimpan permanent di order["delivery_deadline_manual"];
    calculated selalu dihitung ulang dari approvals + settings."""
    calc = _calculated_delivery_deadline(order, lead_time_days)
    manual = order.get("delivery_deadline_manual")
    effective = manual if manual else calc
    source = "manual" if manual else ("automatic" if calc else "none")
    order["delivery_deadline_calculated"] = calc
    order["delivery_deadline_effective"] = effective
    order["delivery_deadline_source"] = source
    order["delivery_deadline_lead_time_days"] = int(lead_time_days)
    order["delivery_deadline_status"] = _deadline_status_for(effective, order)


_DERIVED_ORDER_KEYS = (
    "packing_prereq_reasons", "packing_ready", "packing_status_semantic",
    "surat_jalan_created", "loading_prereq_reasons", "loading_ready",
    "loading_completed", "terkirim_prereq_reasons", "terkirim_ready",
    "terkirim_confirmed", "delivery_deadline_calculated",
    "delivery_deadline_effective", "delivery_deadline_source",
    "delivery_deadline_lead_time_days", "delivery_deadline_status",
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
    "loading_qty_status",
)


def _attach_quantity_snapshot(order: dict) -> None:
    """Derived qty fields (non-persisted) untuk UI + invariant checks."""
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
    order["loading_qty_status"] = _qr_loading_status_semantic(order)
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
    """Increase-only current_stage from Loading / Terkirim evidence.

    SJ record alone stays at 12 (Loading UI is gated by surat_jalan_created).
    First loaded qty opens stage 13. Full delivered qty opens stage 14.
    Partial delivery never jumps the whole PO to Terkirim.
    """
    cs = int(order.get("current_stage") or 0)
    changed = False
    if _surat_jalan_created(order) and _qr_effective_loaded_qty(order) > 0 and cs < 13:
        order["current_stage"] = 13
        changed = True
        cs = 13
    if _qr_is_fully_delivered(order) and cs < 14:
        order["current_stage"] = 14
        changed = True
    return changed


def _ensure_screener_for_operator_work(order: dict, *, for_sample: bool = False):
    reason = _wg_screener_gate_reason(order, for_sample=for_sample)
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


async def _persist_order(order, *, require_lock: bool = False):
    expected_updated = order.pop("_expected_updated_at", None)
    if require_lock and not expected_updated:
        raise HTTPException(409, _CONFLICT_MSG)
    order["updated_at"] = now_iso()
    if order.get("cancelled"):
        order["status_label"] = "Cancelled"
    else:
        order["status_label"] = STAGES[min(order["current_stage"], len(STAGES) - 1)]
    _apply_hold_snapshot(order)
    # DB write pakai field yang persistable (skip yang derived).
    to_write = {k: v for k, v in order.items() if k not in _DERIVED_ORDER_KEYS}
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
    _strip_tracking_secret(order)
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
    elif order.get("design_status") != "Approved" and not order.get("design_bypassed"):
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
    design_approved = (order.get("design_status") == "Approved") or order.get("design_bypassed")
    payment_approved = (order.get("payment_status") == "Approved") or order.get("payment_bypassed")

    # Delivered (final): PO benar-benar sudah di stage terkirim (new index 14).
    if cs >= 14:
        return ["delivered"]

    # 01 Order Masuk — PO baru masuk (stage 0)
    if cs == 0:
        queues.append("order_in")

    # 02 Design — belum ada file design (dan design belum bypass/approved)
    if not design_files and not order.get("design_bypassed") and order.get("design_status") != "Approved":
        queues.append("design")

    # 03 Approval Design — design file sudah ada tetapi belum approved dan tidak bypassed
    if design_files and not order.get("design_bypassed") and order.get("design_status") != "Approved":
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
    elif cs == 12:
        queues.append("delivery_note")
    elif cs == 13:
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
    """SJ done from actual evidence: imported/manual shipment, or legacy fields.

    One partial SJ does not mark the whole PO packed-complete; it does open Loading.
    """
    if _qr_shipments_stored(order):
        return True
    return bool(
        order.get("delivery_note_number")
        and order.get("delivery_note_created_at")
        and order.get("delivery_note_created_by")
    )


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
    """Gate Loading → Terkirim penuh (advance / mark_delivered). Minimal:
      - Surat Jalan valid
      - Loading qty mencapai cap (atau fallback checklist historis)
    Bukti pengiriman parsial tidak memakai gate ini — lihat upload_delivery_proof.
    """
    reasons = []
    if not _surat_jalan_created(order):
        reasons.append("Surat Jalan belum dibuat.")
    if not _loading_completed(order):
        reasons.append("Loading belum lengkap.")
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
    # 5 Penjadwalan -> 6 Screener (Helper paralel, bukan gate)
    if from_stage == 5:
        if not _stage_penjadwalan_done(order):
            reasons.append("Belum ada operator yang di-assign pada Penjadwalan.")
    # 6 Screener -> 7 Persiapan Helper: PARALLEL — jangan blok Helper dengan Screener
    elif from_stage == 6:
        pass
    # 7 Persiapan Helper -> 8 Operator: gate adalah SCREENER, bukan Helper
    elif from_stage == 7:
        if not _stage_screener_done(order):
            reasons.append(SCREENER_GATE_OPERATOR_MSG)
    # 8 Operator -> 9 Konfirmasi Sample (SKIP kalau non-print)
    elif from_stage == 8:
        if req_print and not _stage_screener_done(order):
            reasons.append(SCREENER_GATE_SAMPLE_MSG)
        elif req_print and not _sample_uploaded(order):
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
        _ensure_screener_for_operator_work(order)
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
    cursor = db.orders.find({"id": {"$in": payload.ids}, "cancelled": {"$ne": True}}, {"_id": 0})
    cancelled = []
    now = now_iso()
    async for order in cursor:
        order["cancelled"] = True
        order["cancelled_by"] = user.get("email")
        order["cancelled_by_name"] = user.get("name")
        order["cancelled_at"] = now
        order["cancel_reason"] = reason
        order["status_label"] = "Cancelled"
        add_timeline(order, order.get("current_stage", 0), user, note=f"Bulk cancel: {reason}")
        await _persist_order(order)
        cancelled.append(order.get("po_number"))
    await _write_audit(action="po.bulk_cancel", actor=user, target_email=f"po:{len(cancelled)} PO",
                       after={"po_numbers": cancelled, "count": len(cancelled)}, note=reason)
    return {"cancelled_count": len(cancelled), "po_numbers": cancelled, "reason": reason}


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
    orders = await db.orders.find({"id": {"$in": payload.ids}}, {"_id": 0}).to_list(500)
    po_numbers = [o.get("po_number") for o in orders]
    for o in orders:
        await _audit_po(o, "po.delete", user, before={
            "po_number": o.get("po_number"), "customer": o.get("customer"),
            "current_stage": o.get("current_stage"), "impact": _describe_po_impact(o),
        }, note=f"Bulk: {reason}")
    await db.files.update_many({"order_id": {"$in": payload.ids}}, {"$set": {"is_deleted": True, "deleted_at": now_iso(), "deleted_by": user.get("email"), "deleted_reason": reason}})
    result = await db.orders.delete_many({"id": {"$in": payload.ids}})
    await _write_audit(action="po.bulk_delete", actor=user, target_email=f"po:{result.deleted_count} PO",
                       before={"po_numbers": po_numbers}, after={"deleted_count": result.deleted_count}, note=reason)
    return {"deleted_count": result.deleted_count, "po_numbers": po_numbers, "reason": reason}


# ---- Helper qty (Requirement 9) ----
@api_router.post("/orders/{order_id}/helper-prep")
async def update_helper_prep(order_id: str, payload: HelperPrepPayload, user=Depends(current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    await _require_perm(user, "helper.prepare")
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
    if (o.get("current_stage") or 0) >= 12:
        return False
    return len(_prereq_reasons_for_packing(o)) == 0


def _po_needs_surat_jalan(o: dict) -> bool:
    if (o.get("current_stage") or 0) >= 13:
        return False
    return (o.get("current_stage") or 0) >= 11 and not o.get("delivery_note")


def _po_needs_loading(o: dict) -> bool:
    if o.get("delivery_status") == "Terkirim":
        return False
    return bool(o.get("delivery_note") or o.get("surat_jalan_created")) and not _loading_completed(o)


def _po_needs_terkirim(o: dict) -> bool:
    if o.get("delivery_status") == "Terkirim":
        return False
    return _loading_completed(o)


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
    ("loading", "Loading", "loading.update", _po_needs_loading),
    ("terkirim", "Tandai Terkirim", "delivery.update", _po_needs_terkirim),
]


def _slim_action(o: dict) -> dict:
    return {
        "id": o.get("id"),
        "po_number": o.get("po_number"),
        "outlet": o.get("outlet"),
        "customer": o.get("customer"),
        "work_date": o.get("work_date"),
        "current_stage": o.get("current_stage"),
        "total_quantity": o.get("total_quantity"),
        "delivery_deadline_effective": o.get("delivery_deadline_effective"),
        "delivery_deadline_status": o.get("delivery_deadline_status"),
        "delivery_deadline_source": o.get("delivery_deadline_source"),
        "requires_printing": o.get("requires_printing"),
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
    raw = await db.orders.find(query, {"_id": 0}).to_list(2000)
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
    tasks = _build_division_tasks(raw, div, user, mine_only=mine_only)
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
            rows.append({
                "id": s.id,
                "operator_email": s.operator_email,
                "operator_name": s.operator_name,
                "quantity": int(s.quantity),
                "line_idx": s.line_idx,
                "work_date": wd,
            })
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
        add_timeline(order, order["current_stage"], user, note=f"Qty {found['operator_name']}: +{increment} (total {found['qty_completed']}/{found['quantity']})")

    order = await _with_quantity_lock(order_id, mutate)
    return {"order": order}


@api_router.post("/orders/{order_id}/urgent")
async def set_urgent(order_id: str, payload: UrgentPayload, user=Depends(require("admin", "head_production"))):
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
        if (order.get("current_stage") or 0) < 12:
            raise HTTPException(400, "Belum bisa membuat Surat Jalan — tahap Packing belum selesai.")
        updates = _qr_remaining_delivery_note_updates(order)
        if not updates:
            raise HTTPException(400, "Tidak ada qty packing tersisa untuk Surat Jalan.")
        err = _qr_validate_shipment_lines(updates, order, source=_QR_SRC_MANUAL)
        if err:
            raise HTTPException(400, err)
        number = (
            order.get("delivery_note_number")
            or f"SJ-{str(order.get('po_number') or '').replace('PO-', '').replace('PO.', '')}"
        )
        stamp = now_iso()
        order.setdefault("surat_jalan_files", []).append(entry)
        record = _qr_apply_shipment_record(
            order,
            number=number,
            lines=updates,
            source=_QR_SRC_MANUAL,
            actor=user,
            notes=f"File: {payload.name}",
            shipment_id=str(uuid.uuid4()),
            created_at=stamp,
        )
        if not record.get("id"):
            record["id"] = str(uuid.uuid4())
            order["shipments"][-1]["id"] = record["id"]
        order["shipments"][-1]["file_id"] = entry["id"]
        order["shipments"][-1]["file_name"] = entry["name"]
        _stamp_legacy_delivery_note(order, record, user)
        _sync_workflow_stage(order)
        audit_action = "delivery_note.upload"
    else:
        order.setdefault("sample_files", []).append(entry)
        _sync_workflow_stage(order)
    # Attribute timeline to the workflow stage of the upload kind — never to
    # whatever current_stage happens to be (Payment-first flow must not bury
    # Design uploads under Approval Payment stage).
    _KIND_TIMELINE_STAGE = {"design": 1, "payment": 3, "sample": 8, "surat_jalan": 12}
    tl_stage = _KIND_TIMELINE_STAGE.get(kind, order.get("current_stage") or 0)
    add_timeline(order, tl_stage, user, note=f"Upload {kind}: {payload.name}")
    await _persist_order(order)
    if audit_action:
        await _audit_po(order, audit_action, user, note=f"{payload.name}")
    return {"order": order}


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
    """Loading qty/events (additive) + optional checklist metadata.
    Perm-based (`loading.update`). Saving loaded qty bumps stage to Loading
    (13) from evidence; never jumps to Terkirim.
    Completion = qty actual mencapai cap, dengan fallback checklist historis.
    """
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    await _require_perm(user, "loading.update")

    def mutate(order: dict):
        if not _surat_jalan_created(order):
            raise HTTPException(400, "Surat Jalan harus dibuat terlebih dahulu.")
        prev_complete = _loading_completed(order)
        existing = order.get("loading_checklist") or {}
        items_loaded = existing.get("items_loaded") if payload.items_loaded is None else bool(payload.items_loaded)
        seal_ok = existing.get("seal_ok") if payload.seal_ok is None else bool(payload.seal_ok)
        order["loading_checklist"] = {
            "vehicle_plate": payload.vehicle_plate if payload.vehicle_plate is not None else existing.get("vehicle_plate"),
            "driver_name": payload.driver_name if payload.driver_name is not None else existing.get("driver_name"),
            "items_loaded": items_loaded,
            "seal_ok": seal_ok,
            "notes": payload.notes if payload.notes is not None else existing.get("notes"),
            "updated_by": user.get("email"),
            "updated_by_name": user.get("name"),
            "updated_at": now_iso(),
        }
        qty_note = ""
        if payload.lines is not None:
            increments = []
            absolutes = []
            for x in payload.lines:
                if x.qty is not None:
                    increments.append({"line_idx": int(x.line_idx), "qty": int(x.qty), "action_id": x.action_id})
                elif x.loaded_qty is not None:
                    absolutes.append({"line_idx": int(x.line_idx), "loaded_qty": int(x.loaded_qty)})
            order.setdefault("loading_info", {})
            existing_events = list((order.get("loading_info") or {}).get("events") or [])
            if increments:
                remaining = []
                for u in increments:
                    if u.get("action_id") and any(ev.get("action_id") == u.get("action_id") for ev in existing_events):
                        continue
                    remaining.append(u)
                increments = remaining
            if increments:
                err = _qr_validate_loading_increments(increments, order)
                if err:
                    raise HTTPException(400, err)
                merged, events = _qr_apply_loading_increments(order, increments)
                stamped = []
                for ev in events:
                    if ev.get("created_at"):
                        stamped.append(ev)
                        continue
                    match = next((u for u in increments if int(u["line_idx"]) == int(ev.get("line_idx")) and not u.get("_used")), None)
                    if match:
                        match["_used"] = True
                    stamped.append({
                        **ev,
                        "id": str(uuid.uuid4()),
                        "created_by": user.get("email"),
                        "created_by_name": user.get("name"),
                        "created_at": now_iso(),
                        "action_id": (match or {}).get("action_id"),
                    })
                order["loading_info"]["lines"] = merged
                order["loading_info"]["events"] = stamped
                detail = ", ".join(f"#{u['line_idx']}+{u['qty']}" for u in increments)
                qty_note = f" · qty {detail}"
            if absolutes:
                err = _qr_validate_loading_lines(absolutes, order)
                if err:
                    raise HTTPException(400, err)
                merged = _qr_merge_loading_line_updates(order, absolutes)
                order["loading_info"]["lines"] = merged
                detail = ", ".join(
                    f"#{x['line_idx']}={x['loaded_qty']}" for x in merged if int(x.get("loaded_qty") or 0) > 0
                )
                qty_note = f" · qty {detail or '0'}"
            order["loading_info"]["updated_by"] = user.get("email")
            order["loading_info"]["updated_by_name"] = user.get("name")
            order["loading_info"]["updated_at"] = now_iso()
        now_complete = _loading_completed(order)
        if now_complete and not order.get("loading_completed_at"):
            order["loading_completed_at"] = now_iso()
            order["loading_completed_by"] = user.get("email")
            order["loading_completed_by_name"] = user.get("name")
        _sync_dispatch_stage(order)
        if now_complete and not prev_complete:
            add_timeline(order, 13, user, note=f"Loading selesai{qty_note}")
        else:
            add_timeline(
                order,
                order["current_stage"],
                user,
                note=f"Loading diperbarui ({'lengkap' if now_complete else 'parsial/berjalan'}){qty_note}",
            )

    order = await _with_quantity_lock(order_id, mutate)
    return {"order": order}


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
    before_holder = {}

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

    order = await _with_quantity_lock(order_id, mutate)
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
        number = (
            payload.delivery_note_number
            or order.get("delivery_note_number")
            or f"SJ-{order['po_number'].replace('PO-', '').replace('PO.', '')}"
        )
        existing = _qr_shipments_stored(order)
        explicit_lines = _shipment_line_payload(payload.lines) if payload.lines else None
        creating_new = bool(explicit_lines) or not existing
        if creating_new:
            updates = explicit_lines if explicit_lines else _qr_remaining_delivery_note_updates(order)
            err = _qr_validate_shipment_lines(updates, order, source=_QR_SRC_MANUAL)
            if err:
                raise HTTPException(400, err)
            stamp = now_iso()
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
            if not record.get("id"):
                record["id"] = str(uuid.uuid4())
                order["shipments"][-1]["id"] = record["id"]
            _stamp_legacy_delivery_note(order, record, user)
            add_timeline(order, 12, user, note=f"Surat Jalan dibuat: {number}")
        else:
            latest = existing[-1]
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
        reasons = _prereq_reasons_for_terkirim(order)
        if reasons:
            raise HTTPException(400, {
                "message": "Belum bisa Terkirim — prerequisite belum lengkap.",
                "prereq_reasons": reasons,
            })
        if user.get("role") == "driver":
            blocked = _qr_confirm_delivery_block_reason(order)
            if blocked:
                raise HTTPException(400, blocked)
        packed = _qr_qty_packed(order)
        required = _packing_required_total(order)
        new_delivered = packed if qty_delivered is None else int(qty_delivered)
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


@api_router.post("/orders/{order_id}/confirm-delivered")
async def confirm_delivered(order_id: str, payload: Optional[ConfirmDeliveredPayload] = None, user=Depends(current_user)):
    """Explicit Konfirmasi Terkirim after >= 2 bukti kirim. Upload alone does not deliver."""
    await _require_perm(user, "delivery.update")
    action_id = (payload.action_id if payload else None) or None
    if action_id:
        action_id = str(action_id).strip() or None

    def mutate(order: dict):
        ids = list(order.get("delivery_confirm_action_ids") or [])
        if action_id and action_id in ids:
            return
        blocked = _qr_confirm_delivery_block_reason(order)
        if blocked:
            raise HTTPException(400, blocked)
        evidence = _qr_apply_delivery_from_proof_evidence(order)
        if not evidence:
            raise HTTPException(400, "Belum ada qty loading untuk dikonfirmasi terkirim.")
        now = now_iso()
        order["qty_delivered"] = evidence["qty_delivered"]
        order["delivery_status"] = evidence["delivery_status"]
        order["delivered_at"] = now
        order["delivered_by"] = user.get("email")
        order["delivered_by_name"] = user.get("name")
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
        if action_id:
            ids.append(action_id)
            order["delivery_confirm_action_ids"] = ids[-20:]

    order = await _with_quantity_lock(order_id, mutate)
    return {"order": order}


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


@api_router.post("/orders/{order_id}/delivery-deadline")
async def set_delivery_deadline_manual(order_id: str, payload: DeliveryDeadlineManualPayload, user=Depends(current_user)):
    await _require_perm(user, "delivery.deadline.edit")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    # Parse & normalise datetime
    raw = (payload.deadline_at or "").strip()
    if not raw:
        raise HTTPException(400, "Tanggal + Jam Pengiriman wajib diisi")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # No tz info → assume WIB (UTC+7) — konsisten dengan area operasional Eternal.
            dt = dt.replace(tzinfo=timezone(timedelta(hours=7)))
        dt_utc = dt.astimezone(timezone.utc)
    except Exception:
        raise HTTPException(400, "Format datetime tidak valid — gunakan ISO 8601 (YYYY-MM-DDTHH:MM)")
    before = order.get("delivery_deadline_manual")
    order["delivery_deadline_manual"] = dt_utc.isoformat()
    order["delivery_deadline_manual_by"] = user.get("email")
    order["delivery_deadline_manual_by_name"] = user.get("name")
    order["delivery_deadline_manual_at"] = now_iso()
    order["delivery_deadline_manual_reason"] = (payload.reason or "").strip() or None
    order.setdefault("delivery_deadline_history", []).append({
        "action": "manual.set",
        "by_email": user.get("email"),
        "by_name": user.get("name"),
        "at": now_iso(),
        "before": before,
        "after": order["delivery_deadline_manual"],
        "note": (payload.reason or "").strip() or "",
    })
    await _persist_order(order)
    await _audit_po(order, "delivery.deadline.manual.set", user,
                    before={"deadline_manual": before},
                    after={"deadline_manual": order["delivery_deadline_manual"]},
                    note=payload.reason or "")
    return {"order": order}


@api_router.post("/orders/{order_id}/delivery-deadline/reset")
async def reset_delivery_deadline_manual(order_id: str, user=Depends(current_user)):
    await _require_perm(user, "delivery.deadline.edit")
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    if not order.get("delivery_deadline_manual"):
        raise HTTPException(400, "Tidak ada manual override yang perlu direset")
    before = order.get("delivery_deadline_manual")
    order["delivery_deadline_manual"] = None
    order["delivery_deadline_manual_by"] = None
    order["delivery_deadline_manual_by_name"] = None
    order["delivery_deadline_manual_at"] = None
    order["delivery_deadline_manual_reason"] = None
    order.setdefault("delivery_deadline_history", []).append({
        "action": "manual.reset",
        "by_email": user.get("email"),
        "by_name": user.get("name"),
        "at": now_iso(),
        "before": before,
        "after": None,
    })
    await _persist_order(order)
    await _audit_po(order, "delivery.deadline.manual.reset", user,
                    before={"deadline_manual": before}, after={"deadline_manual": None})
    return {"order": order}


# ---- Batch 1: Helper Preparation per LINE ITEM ----


# ---------- Dashboard aggregation (Head of Production) ----------
@api_router.get("/dashboard/summary")
async def dashboard_summary(user=Depends(require_perm("dashboard.production.view"))):
    raw_orders = await db.orders.find({}, {"_id": 0}).to_list(1000)
    # Cancelled PO tidak boleh ikut card apa pun di Dashboard Produksi.
    orders = [o for o in raw_orders if not o.get("cancelled")]
    # Attach HOLD snapshot (canonical predicate) — dipakai untuk card HOLD.
    # Batch 1: attach delivery deadline snapshot (source of truth H-1/H-6/Overdue).
    _dd_settings = await _get_delivery_deadline_settings()
    for o in orders:
        _apply_hold_snapshot(o)
        _attach_deadline_snapshot(o, _dd_settings["lead_time_days"])

    now = datetime.now(timezone.utc)
    today_iso_date = now.strftime("%Y-%m-%d")

    active_orders = [o for o in orders if o.get("current_stage", 0) < 14]
    urgent_orders = [o for o in active_orders if o.get("priority") == "urgent"]
    completed_orders = [o for o in orders if o.get("current_stage", 0) >= 14]

    total_active = len(active_orders)
    total_urgent = len(urgent_orders)
    total_completed = len(completed_orders)

    def parse_dt(iso):
        if not iso: return None
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    due_today, overdue = [], []
    deadline_h1, deadline_h6 = [], []
    waiting_design, waiting_payment = [], []
    hold_list = []
    # SEMANTIC: satu PO boleh muncul di beberapa list (design + payment + hold,
    # dsb.) — predicate independen, TIDAK bergantung pada current_stage.
    for o in active_orders:
        # Legacy `deadline` for due_today only (H-1/H-6/overdue use effective).
        d = parse_dt(o.get("deadline"))
        if d and d >= now and d.strftime("%Y-%m-%d") == today_iso_date:
            due_today.append(o)
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
        cnt = sum(1 for o in orders if o.get("current_stage", 0) == idx)
        stage_breakdown.append({"stage": idx, "label": label, "count": cnt})

    # Operator load (aggregate qty_scheduled / completed / remaining across active orders)
    op_map = {}
    for o in orders:
        if o.get("current_stage", 0) >= 14: continue
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
        "work_today": build_work_today(orders),
        "shipments_today": build_shipments_today(orders),
    }


@api_router.get("/dashboard/today")
async def dashboard_today(user=Depends(require_perm("po.view"))):
    """Compact Pengerjaan/Pengiriman Hari Ini. Targeted queries, not a full DB scan."""
    today = wib_today()
    work_docs = await db.orders.find(work_today_mongo_query(today), TODAY_WORK_PROJECTION).to_list(500)
    ship_docs = await db.orders.find(shipments_today_mongo_query(today), TODAY_SHIP_PROJECTION).to_list(500)
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


@api_router.get("/dashboard/production")
async def production_dashboard(
    include_cancelled: bool = False,
    include_completed: bool = True,
    user=Depends(require_perm("schedule.view")),
):
    """Return Jadwal Produksi rows (real Mongo data). Filters/search happen on
    the client. Excluding cancelled by default; explicit flag to include."""
    q: dict = {}
    if not include_cancelled:
        q["cancelled"] = {"$ne": True}
    orders = await db.orders.find(q, {"_id": 0}).sort("deadline", 1).to_list(1000)
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


STATUS_PO_PROJECTION = {
    **SHIPMENT_STATUS_PROJECTION,
    "work_date": 1,
    "operator_splits.work_date": 1,
    "operator_splits.operator_name": 1,
    "hold": 1,
    "priority": 1,
    "status_label": 1,
    "design_status": 1,
    "design_bypassed": 1,
    "design_files": 1,
    "payment_status": 1,
    "payment_bypassed": 1,
    "payment_files": 1,
    "screener_info.screen_qty": 1,
    "helper_prep.qty_prepared": 1,
    "sample_files": 1,
    "sample_approved": 1,
    "packing_info": 1,
    "packing_status_semantic": 1,
    "surat_jalan_created": 1,
    "surat_jalan_status_semantic": 1,
    "delivery_note_number": 1,
    "delivery_note_created_at": 1,
    "loading_info": 1,
    "loading_completed": 1,
    "loading_completed_at": 1,
    "delivery_proofs.uploaded_at": 1,
    "terkirim_confirmed": 1,
    "print_status": 1,
    "print_completed_qty": 1,
    "shipments.created_at": 1,
    "shipments.number": 1,
}


@api_router.get("/dashboard/status-po")
async def status_po_dashboard(
    view: Optional[str] = None,
    include_cancelled: bool = False,
    user=Depends(require_perm("schedule.view")),
):
    """Laporan Status PO — one query, dashboard card subsets, shipment % reuse."""
    q: dict = {}
    if not include_cancelled:
        q["cancelled"] = {"$ne": True}
    orders = await db.orders.find(q, STATUS_PO_PROJECTION).sort("deadline", 1).to_list(1000)
    _dd_settings = await _get_delivery_deadline_settings()
    for o in orders:
        _apply_hold_snapshot(o)
        _attach_deadline_snapshot(o, _dd_settings["lead_time_days"])
    data = build_status_po(orders, view)
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
    for module, key, label in PERMISSION_CATALOG:
        if key == NAV_CONFIGURED:
            continue
        modules.setdefault(module, []).append({"key": key, "label": label})
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
    email = payload.email.strip().lower()
    if not await _role_slug_is_valid(payload.role):
        raise HTTPException(400, f"Role '{payload.role}' tidak dikenal")
    if await db.users.find_one({"email": email}):
        raise HTTPException(409, "Email sudah terdaftar")
    now = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "employee_id": await _next_employee_id(),
        "email": email,
        "name": payload.name.strip(),
        "nickname": (payload.nickname or "").strip() or None,
        "phone": (payload.phone or "").strip() or None,
        "role": payload.role,
        "status": "active",
        "joined_at": payload.joined_at,
        "deactivated_at": None,
        "notes": payload.notes,
        "permissions_override": payload.permissions_override or {},
        "password_hash": hash_password(payload.password or os.environ.get("TRIAL_DEFAULT_PASSWORD", "Eternal123!")),
        "created_at": now,
        "updated_at": now,
        "created_by": user.get("email"),
    }
    await db.users.insert_one(doc)
    await _write_audit("user.create", user, target_email=email, before=None, after=_sanitize_user(dict(doc)), note=f"Tambah karyawan {payload.name}")
    saved = await db.users.find_one({"email": email}, {"password_hash": 0, "_id": 0})
    return {"user": saved}


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
    return {"user": after}


@api_router.post("/admin/users/{user_id}/activate")
async def activate_user(user_id: str, user=Depends(require_perm("users.manage"))):
    existing = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    if not existing:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    await db.users.update_one({"id": user_id}, {"$set": {"status": "active", "deactivated_at": None, "updated_at": now_iso()}})
    after = await db.users.find_one({"id": user_id}, {"password_hash": 0, "_id": 0})
    await _write_audit("user.activate", user, target_email=existing.get("email"), before=existing, after=after, note="Aktifkan kembali karyawan")
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
    total_qty = sum(int(li.get("quantity", 0)) for li in so.get("line_items", []))
    now = datetime.now(timezone.utc)
    offset = so.get("order_date_offset_days", 0)
    order_date = (now + timedelta(days=offset)).isoformat()
    deadline = (now + timedelta(days=offset + 3)).isoformat()
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


@api_router.post("/accurate/import/preview")
async def accurate_import_preview(payload: ImportPreviewRequest, user=Depends(require_perm("accurate.import"))):
    """Fetch ONE Sales Order from Accurate (or mock) and return a preview. Never
    writes to `orders`. Reports whether this SO has already been imported."""
    so = await _accurate_client.fetch_single_sales_order(payload.so_number)
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
    so = await _accurate_client.fetch_single_sales_order(payload.so_number)
    log_base = {
        "id": str(uuid.uuid4()),
        "so_number": payload.so_number,
        "accurate_id": (so or {}).get("accurate_id"),
        "po_number": (so or {}).get("po_number"),
        "imported_by": user.get("email"),
        "imported_by_name": user.get("name"),
        "imported_at": now_iso(),
        "mode": "live" if _accurate_client.is_live else "mock",
        "read_only_source": True,
    }
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
    except HTTPException:
        raise
    except Exception as e:
        await db.import_logs.insert_one({**log_base, "status": "failed", "message": f"Insert gagal: {e}"})
        raise HTTPException(500, "Gagal menyimpan PO baru")
    await db.import_logs.insert_one({**log_base, "status": "success", "message": "PO baru berhasil diimport", "created_po_id": new["id"]})
    saved = await db.orders.find_one({"id": new["id"]}, {"_id": 0})
    return {"order": saved, "message": "PO berhasil diimport dari Accurate"}


class BulkImportRequest(BaseModel):
    so_numbers: List[str]


@api_router.post("/accurate/import/preview-bulk")
async def accurate_import_preview_bulk(payload: BulkImportRequest, user=Depends(require_perm("accurate.import.bulk"))):
    """Fetch MULTIPLE Sales Orders from Accurate (or mock) in parallel and return
    per-SO previews with duplicate flags. NEVER writes to Mongo.
    Returns {items:[{so_number, ok, preview?, duplicate, existing_po_id,
    existing_po_number, error?, has_verify?}], summary:{total, ready, duplicates,
    errors, total_qty, total_items, needs_verify}}"""
    import asyncio as _aio
    numbers = [n.strip() for n in (payload.so_numbers or []) if n and n.strip()]
    if not numbers:
        raise HTTPException(400, "Minimal pilih 1 Sales Order")
    if len(numbers) > 25:
        raise HTTPException(400, "Maksimal 25 Sales Order per batch")

    sem = _aio.Semaphore(4)
    async def _one(sn: str) -> Dict[str, Any]:
        async with sem:
            try:
                so = await _accurate_client.fetch_single_sales_order(sn)
                if not so:
                    return {"so_number": sn, "ok": False, "error": "SO tidak ditemukan"}
                existing = await _find_existing_import(so)
                prev = _build_preview(so)
                needs_verify = any(li.get("color_needs_verify") for li in prev.get("line_items", []))
                return {
                    "so_number": sn,
                    "ok": True,
                    "preview": prev,
                    "duplicate": bool(existing),
                    "existing_po_id": existing.get("id") if existing else None,
                    "existing_po_number": existing.get("po_number") if existing else None,
                    "has_verify": needs_verify,
                }
            except Exception as e:
                msg = str(e)
                if _accurate_client.api_token and _accurate_client.api_token in msg:
                    msg = "Accurate error"
                if _accurate_client.api_signature_secret and _accurate_client.api_signature_secret in msg:
                    msg = "Accurate error"
                return {"so_number": sn, "ok": False, "error": msg[:180]}

    items = await _aio.gather(*[_one(n) for n in numbers])
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
        except Exception as e:
            msg = str(e)
            if _accurate_client.api_token and _accurate_client.api_token in msg:
                msg = "Accurate error"
            await db.import_logs.insert_one({**log_base, "status": "failed", "message": f"Fetch gagal: {msg[:120]}"})
            results.append({"so_number": sn, "status": "failed", "message": msg[:180]})
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
        except HTTPException:
            raise
        except Exception as e:
            await db.import_logs.insert_one({**log_base, "status": "failed", "message": f"Insert gagal: {e}"})
            results.append({"so_number": sn, "status": "failed", "message": "Gagal menyimpan PO baru"})
            continue
        await db.import_logs.insert_one({**log_base, "status": "success", "message": "PO baru berhasil diimport", "created_po_id": new["id"]})
        results.append({"so_number": sn, "status": "success", "po_id": new["id"], "po_number": new["po_number"]})
        imported_count += 1

    return {
        "results": results,
        "summary": {
            "total": len(numbers),
            "imported": imported_count,
            "duplicates": sum(1 for r in results if r["status"] == "duplicate"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
        },
        "message": f"{imported_count} PO berhasil diimport dari Accurate",
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
    return {"order": saved, "message": f"PO berhasil diimport dari {src}"}


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
    user=Depends(require_perm("master_items.view")),
):
    """List Master Barang/Jasa with server-side search, filter, and pagination."""
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
            "Gagal sinkronisasi Master Barang/Jasa dari Accurate. Data existing tidak diubah.",
        )
    return result


class CurrentStockPatch(BaseModel):
    manual_current_qty: Optional[Any] = None
    manual_purchase_qty: Optional[Any] = None


async def _load_stock_context():
    masters = await db.master_items.find({}, {"_id": 0}).to_list(5000)
    orders = await db.orders.find(
        {"cancelled": {"$ne": True}}, STOCK_ORDER_PROJECTION,
    ).to_list(5000)
    manuals = await db[STOCK_MANUAL_COLLECTION].find({}, {"_id": 0}).to_list(5000)
    receipts = await load_receipts(db)
    meta = await load_sync_meta(db)
    source = purchase_source_from_meta(meta)
    rows = build_stock_rows(
        masters, orders, manuals, receipts, purchase_source=source,
    )
    return rows, source, receipt_last_success_at(meta)


def _purchase_source_label(source: str) -> str:
    return "Accurate" if source == STOCK_PURCHASE_ACCURATE else "Manual"


def _stock_xlsx(rows: list) -> FastAPIResponse:
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Stock Saat Ini"
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
    for col in range(4, 9):
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
    rows, source, last_sync = await _load_stock_context()
    payload = filter_stock_page(
        rows, q=q, stock_filter=stock_filter, sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size, purchase_source=source,
        last_receipt_sync_at=last_sync,
    )
    payload["purchase_source"] = source
    payload["last_receipt_sync_at"] = last_sync
    return payload


@api_router.get("/current-stock/export")
async def current_stock_export(
    q: str = "",
    stock_filter: str = "all",
    sort_by: str = "name",
    sort_dir: str = "asc",
    user=Depends(require_perm("current_stock.export")),
):
    rows, _source, _last_sync = await _load_stock_context()
    from services.current_stock import matches_search, stock_sign_filter, qty_num, SORT_KEYS
    filtered = [
        r for r in rows
        if matches_search(r, q) and stock_sign_filter(qty_num(r.get("stock_current")), stock_filter)
    ]
    key = SORT_KEYS.get((sort_by or "name").strip().lower(), "item_name")
    reverse = (sort_dir or "asc").strip().lower() == "desc"

    def sort_val(r):
        v = r.get(key)
        if key == "item_name":
            return str(v or "").lower()
        return qty_num(v)

    filtered.sort(key=sort_val, reverse=reverse)
    await _write_audit(
        "current_stock.export", user, target_email=None,
        before=None, after={"count": len(filtered), "stock_filter": stock_filter, "q": q},
        note="Export Stock Saat Ini",
    )
    return _stock_xlsx(filtered)


@api_router.patch("/current-stock/{item_id}")
async def current_stock_update(
    item_id: str,
    payload: CurrentStockPatch,
    user=Depends(require_perm("current_stock.update")),
):
    """Save manual Qty Saat Ini and/or Pembelian Barang (fallback). Recompute stock."""
    master = await db.master_items.find_one(
        {"$or": [{"id": item_id}, {"accurate_item_id": str(item_id)}]},
        {"_id": 0},
    )
    if not master:
        raise HTTPException(404, "Master item tidak ditemukan")
    source = purchase_source_from_meta(await load_sync_meta(db))
    fields = {}
    if payload.manual_current_qty is not None:
        try:
            fields["manual_current_qty"] = parse_manual_qty(payload.manual_current_qty, allow_negative=True)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if payload.manual_purchase_qty is not None:
        if source == STOCK_PURCHASE_ACCURATE:
            raise HTTPException(
                409,
                "Pembelian Barang memakai Penerimaan Barang Accurate. Sinkronkan di Pembelian → Penerimaan Barang.",
            )
        try:
            fields["manual_purchase_qty"] = parse_manual_qty(payload.manual_purchase_qty)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if not fields:
        raise HTTPException(400, "Tidak ada nilai yang diubah")
    now = now_iso()
    fields["updated_at"] = now
    fields["updated_by"] = user.get("email")
    fields["updated_by_name"] = user.get("name") or user.get("email")
    existing = await db[STOCK_MANUAL_COLLECTION].find_one(
        {"$or": [
            {"master_item_id": master.get("id")},
            {"accurate_item_id": master.get("accurate_item_id")},
        ]},
        {"_id": 0},
    )
    before = public_stock_manual(existing)
    if existing:
        await db[STOCK_MANUAL_COLLECTION].update_one(
            {"id": existing["id"]},
            {"$set": fields},
        )
        doc_id = existing["id"]
    else:
        doc_id = str(uuid.uuid4())
        doc = {
            "id": doc_id,
            "master_item_id": master.get("id"),
            "accurate_item_id": master.get("accurate_item_id"),
            "manual_current_qty": 0,
            "manual_purchase_qty": 0,
            **fields,
        }
        await db[STOCK_MANUAL_COLLECTION].insert_one(doc)
    fresh = await db[STOCK_MANUAL_COLLECTION].find_one({"id": doc_id}, {"_id": 0})
    await _write_audit(
        "current_stock.update", user,
        target_email=f"item:{master.get('accurate_item_id') or master.get('id')}",
        before=before, after=public_stock_manual(fresh),
        note="Update qty stock manual",
    )
    rows, source, _last_sync = await _load_stock_context()
    row = next(
        (
            r for r in rows
            if r.get("master_item_id") == master.get("id")
            or str(r.get("accurate_item_id") or "") == str(master.get("accurate_item_id") or "")
        ),
        None,
    )
    return {"item": row, "purchase_source": source}


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
    # Look up metadata to enforce content-type + soft-delete filter
    rec = await db.files.find_one({"storage_path": path, "is_deleted": {"$ne": True}}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "File tidak ditemukan")
    try:
        data, ct = obj_storage.get_object(path)
    except FileNotFoundError:
        raise HTTPException(404, "File tidak ditemukan")
    except Exception:
        logger.exception("Failed to fetch object %s", path)
        raise HTTPException(502, "Gagal mengambil file.")
    return FastAPIResponse(content=data, media_type=rec.get("content_type") or ct)


class DeliveryProofDeletePayload(BaseModel):
    proof_id: str


@api_router.post("/orders/{order_id}/delivery-proof")
async def upload_delivery_proof(
    order_id: str,
    file: UploadFile = File(...),
    note: Optional[str] = Form(None),
    user=Depends(require("admin", "head_production", "driver")),
):
    """Upload a delivery-proof photo (from driver's phone). Stores to object
    storage and references it on the PO document — never as inline binary.
    Max 5 photos per PO."""
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    _ensure_not_cancelled(order)
    # Content-type and size first, so error messages remain actionable
    ct = (file.content_type or "").lower()
    if ct not in ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"):
        raise HTTPException(400, "Hanya file gambar yang diperbolehkan")
    existing_proofs = order.get("delivery_proofs", [])
    if len(existing_proofs) >= 5:
        raise HTTPException(400, "Maksimum 5 foto bukti kirim per PO — hapus salah satu untuk mengganti")

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
    shipments = _qr_shipments_stored(order)
    if shipments:
        latest = shipments[-1]
        if latest.get("id"):
            proof_ref["shipment_id"] = latest.get("id")
        if latest.get("number"):
            proof_ref["shipment_number"] = latest.get("number")
        if latest.get("accurate_shipment_id"):
            proof_ref["accurate_shipment_id"] = latest.get("accurate_shipment_id")
    add_timeline(order, order["current_stage"], user, note=f"Upload bukti kirim: {file_doc['original_filename']}")
    await _persist_order(order)
    return {"order": order, "proof": proof_ref}


@api_router.delete("/orders/{order_id}/delivery-proof/{proof_id}")
async def delete_delivery_proof(order_id: str, proof_id: str, user=Depends(require("admin", "head_production", "driver"))):
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
    await _persist_order(order)
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
        raise HTTPException(400, "tab harus operator, helper, screener, atau design")
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
    active: bool = True


class OperatorWageOperatorIn(BaseModel):
    operator_user_id: Optional[str] = None
    operator_email: Optional[str] = None
    operator_name: Optional[str] = None
    default_rate_per_pcs: Optional[float] = None


class OperatorWageSettingsPayload(BaseModel):
    default_rate_per_pcs: Optional[float] = None
    operators: Optional[List[OperatorWageOperatorIn]] = None
    overrides: Optional[List[OperatorWageOverrideIn]] = None


async def _require_gaji_view(user: dict):
    if user.get("role") == "admin":
        return
    eff = await user_permissions(user)
    if "reports.gaji_operator.view" not in eff and "schedule.view" not in eff:
        raise HTTPException(403, "Permission 'reports.gaji_operator.view' diperlukan")


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
    pipeline = build_operator_analytics_pipeline(
        ids[0] if len(ids) == 1 else None, None, item, None, operator_ids=ids or None,
    )
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
    ops_facet = [
        {"id": r.get("_id"), "name": r.get("name") or r.get("_id")}
        for r in (blob.get("operators") or [])
        if r.get("_id")
    ]
    ops, items = _payroll_filter_options(blob.get("docs") or [])
    if ops_facet:
        ops = ops_facet
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
    pipeline = build_operator_analytics_pipeline(
        ids[0] if len(ids) == 1 else None, None, item, None, operator_ids=ids or None,
    )
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
    keys = [c[0] for c in PAYROLL_EXPORT_COLUMNS]
    headers = [c[1] for c in PAYROLL_EXPORT_COLUMNS]
    fmt = (fmt or "xlsx").strip().lower()
    filename = f"Gaji_Operator_{start.isoformat()}_{end.isoformat()}." + ("xlsx" if fmt == "xlsx" else "csv")
    numeric = {"qty_completed", "rate_per_pcs", "total_upah"}
    if fmt == "xlsx":
        from io import BytesIO
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Gaji Operator"
        ws.append(headers)
        for row in rows:
            ws.append([row.get(k, "") if k not in numeric else (row.get(k) if row.get(k) is not None else 0) for k in keys])
        buf = BytesIO()
        wb.save(buf)
        payload, media = buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        import csv
        from io import StringIO
        buf = StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf, lineterminator="\n")
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
    }
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


@api_router.get("/reminders")
async def reminders(user=Depends(current_user)):
    """Return active POs with reminder status. Filters by role — HoP/admin see all,
    other roles see only reminders where they are the current owner."""
    orders = await db.orders.find({}, {"_id": 0, "design_files": 0, "sample_files": 0}).to_list(1000)
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
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
_ct_mod.install_access_log_mask()


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()


@app.on_event("startup")
async def seed():
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True, sparse=True)
    await db.users.create_index("employee_id", sparse=True)
    await db.orders.create_index("id", unique=True)
    await db.orders.create_index("po_number", unique=True)
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
            "current_stock.view", "current_stock.update", "current_stock.export",
            "purchase_receipts.view", "purchase_receipts.sync", "purchase_receipts.export",
            "nav.inventory.current_stock",
            "nav.purchasing.receipts",
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
            "current_stock.view", "current_stock.update", "current_stock.export",
            "purchase_receipts.view", "purchase_receipts.sync", "purchase_receipts.export",
            "nav.inventory.current_stock",
            "nav.purchasing.receipts",
        ],
        # Import PO permissions: admin gets all (idempotent top-up if a custom
        # admin override was persisted before these keys existed).
        "admin": [
            "po.import.view", "accurate.import", "accurate.import.bulk",
            "po.import.pdf", "po.import.spreadsheet",
            "master_items.view", "master_items.sync",
            "nav.inventory.master_items",
            "current_stock.view", "current_stock.update", "current_stock.export",
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

