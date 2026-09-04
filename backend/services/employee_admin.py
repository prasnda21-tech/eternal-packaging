"""Administrasi Karyawan — configurable employee self-service requests.

Reusable request types (form + eligibility + sequential approval + optional PDF).
Historical submissions keep immutable snapshots of form, workflow, and document template.
"""
from __future__ import annotations

import copy
import html
import io
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.purchase_common import (
    actor_snapshot as _purchase_actor,
    get_named_lock,
    insert_with_unique_number,
    next_dated_seq,
    now_iso,
)
from services.pdf_date import format_pdf_date

TYPES_COLLECTION = "employee_request_types"
REQUESTS_COLLECTION = "employee_requests"
FILES_KIND = "employee_admin"

PREFIX_REQUEST = "PKY"
PREFIX_DOCUMENT = "SKY"

PERM_VIEW_OWN = "employee_admin.view_own"
PERM_SUBMIT = "employee_admin.submit"
PERM_APPROVE = "employee_admin.approve"
PERM_VIEW_ALL = "employee_admin.view_all"
PERM_SETTINGS = "employee_admin.settings"
PERM_DOCUMENT = "employee_admin.document_view"
NAV_PARENT = "nav.employee_administration"
NAV_MINE = "nav.employee_administration.mine"
NAV_CREATE = "nav.employee_administration.create"
NAV_APPROVE = "nav.employee_administration.approve"
NAV_ARCHIVE = "nav.employee_administration.archive"

STATUS_DRAFT = "DRAFT"
STATUS_SUBMITTED = "DIAJUKAN"
STATUS_WAITING = "MENUNGGU_PERSETUJUAN"
STATUS_APPROVED = "DISETUJUI"
STATUS_REJECTED = "DITOLAK"

STATUS_LABELS = {
    STATUS_DRAFT: "Draft",
    STATUS_SUBMITTED: "Diajukan",
    STATUS_WAITING: "Menunggu Persetujuan",
    STATUS_APPROVED: "Disetujui",
    STATUS_REJECTED: "Ditolak",
}

FIELD_TYPES = ("text", "textarea", "number", "currency", "date", "datetime", "select", "checkbox", "file")
ELIGIBILITY_MODES = ("any", "all")
APPROVER_KINDS = ("user", "role", "division")
MAX_FILE_BYTES = 8 * 1024 * 1024
ALLOWED_FILE_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}
CODE_RX = re.compile(r"^[A-Z][A-Z0-9_]{1,23}$")
KEY_RX = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PLACEHOLDER_RX = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

# Existing production divisions + role-mapped HR-facing labels. Not a new master.
DIVISION_OPTIONS = (
    ("designer", "Designer"),
    ("screener", "Screener"),
    ("kepala_produksi", "Kepala Produksi"),
    ("helper", "Helper"),
    ("operator", "Operator"),
    ("packing", "Packing"),
    ("surat_jalan", "Surat Jalan"),
    ("loading", "Loading"),
    ("terkirim", "Pengiriman"),
    ("crm", "CRM"),
    ("head_operations", "Kepala Operasional"),
)
DIVISION_IDS = {d[0] for d in DIVISION_OPTIONS}

ROLE_DIVISION_IDS = {
    "designer": ("designer",),
    "screener": ("screener",),
    "helper": ("helper", "packing"),
    "operator": ("operator",),
    "driver": ("loading", "terkirim", "surat_jalan"),
    "head_production": ("kepala_produksi",),
    "head_operations": ("head_operations",),
    "crm": ("crm",),
}


def public_request(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = dict(doc)
    out.pop("_id", None)
    for att in out.get("attachments") or []:
        if isinstance(att, dict):
            att.pop("storage_path", None)
    docu = out.get("document")
    if isinstance(docu, dict):
        docu.pop("storage_path", None)
    out["status_label"] = STATUS_LABELS.get(out.get("status"), out.get("status"))
    return out


def public_type(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = dict(doc)
    out.pop("_id", None)
    return out


def actor_from_user(user: Optional[dict], employee: Optional[dict] = None) -> dict:
    src = employee or user or {}
    snap = _purchase_actor(src)
    uid = str(src.get("user_id") or src.get("id") or user.get("user_id") or user.get("id") or user.get("sub") or "") if user else str(src.get("id") or "")
    if uid:
        snap["id"] = uid
    snap["email"] = str(src.get("email") or (user or {}).get("email") or "")
    snap["employee_id"] = str(src.get("employee_id") or "")
    return snap


def user_division_ids(employee: dict) -> set:
    role = str(employee.get("role") or "")
    ids = set(ROLE_DIVISION_IDS.get(role, ()))
    if role:
        ids.add(role)
    return ids


def normalize_eligibility(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    mode = str(src.get("mode") or "any").strip().lower()
    if mode not in ELIGIBILITY_MODES:
        raise HTTPException(400, "Mode hak pengajuan harus 'any' (Cocok Salah Satu) atau 'all' (Cocok Semua).")
    user_ids = _str_list(src.get("user_ids"))
    roles = _str_list(src.get("roles"))
    divisions = _str_list(src.get("divisions"))
    unknown = [d for d in divisions if d not in DIVISION_IDS]
    if unknown:
        raise HTTPException(400, f"Divisi tidak dikenal: {', '.join(unknown)}")
    all_employees = bool(src.get("all_employees"))
    return {
        "mode": mode,
        "all_employees": all_employees,
        "user_ids": user_ids,
        "roles": roles,
        "divisions": divisions,
    }


def is_eligible(employee: dict, rule: dict) -> bool:
    if not employee or str(employee.get("status") or "active") == "inactive":
        return False
    rule = rule or {}
    if rule.get("all_employees"):
        return True
    uid = str(employee.get("id") or "")
    role = str(employee.get("role") or "")
    checks = []
    if rule.get("user_ids"):
        checks.append(uid in set(rule["user_ids"]))
    if rule.get("roles"):
        checks.append(role in set(rule["roles"]))
    if rule.get("divisions"):
        checks.append(bool(user_division_ids(employee) & set(rule["divisions"])))
    if not checks:
        return False
    if rule.get("mode") == "all":
        return all(checks)
    return any(checks)


def normalize_field(raw: Any, idx: int) -> dict:
    if not isinstance(raw, dict):
        raise HTTPException(400, f"Field form ke-{idx + 1} tidak valid.")
    key = str(raw.get("key") or "").strip().lower()
    if not KEY_RX.match(key):
        raise HTTPException(400, f"Kunci field '{key or idx + 1}' tidak valid. Gunakan huruf kecil, angka, underscore.")
    ftype = str(raw.get("type") or "text").strip().lower()
    if ftype not in FIELD_TYPES:
        raise HTTPException(400, f"Tipe field '{ftype}' tidak didukung.")
    label = str(raw.get("label") or "").strip()
    if not label:
        raise HTTPException(400, f"Label field '{key}' wajib diisi.")
    options = []
    if ftype == "select":
        options = [str(x).strip() for x in (raw.get("options") or []) if str(x).strip()]
        if not options:
            raise HTTPException(400, f"Field select '{key}' wajib punya pilihan.")
    order = idx
    if raw.get("order") is not None and str(raw.get("order")).strip() != "":
        try:
            order = int(raw.get("order"))
        except (TypeError, ValueError):
            order = idx
    field = {
        "key": key,
        "label": label[:120],
        "type": ftype,
        "required": bool(raw.get("required")),
        "placeholder": str(raw.get("placeholder") or "").strip()[:160],
        "options": options,
        "order": order,
        "active": False if raw.get("active") is False else True,
    }
    if ftype in ("number", "currency"):
        mn = _opt_number(raw.get("minimum"), f"minimum {key}")
        mx = _opt_number(raw.get("maximum"), f"maximum {key}")
        field["minimum"] = float(mn) if mn is not None else None
        field["maximum"] = float(mx) if mx is not None else None
        if field["minimum"] is not None and field["maximum"] is not None and field["minimum"] > field["maximum"]:
            raise HTTPException(400, f"minimum {key} tidak boleh lebih besar dari maximum.")
    return field


def normalize_fields(raw: Any) -> List[dict]:
    rows = raw if isinstance(raw, list) else []
    fields = [normalize_field(r, i) for i, r in enumerate(rows)]
    keys = [f["key"] for f in fields]
    if len(keys) != len(set(keys)):
        raise HTTPException(400, "Kunci field harus unik.")
    fields.sort(key=lambda f: (f.get("order") or 0, f["key"]))
    return fields


def normalize_approval_steps(raw: Any) -> List[dict]:
    rows = raw if isinstance(raw, list) else []
    steps = []
    for i, raw_step in enumerate(rows):
        if not isinstance(raw_step, dict):
            raise HTTPException(400, f"Langkah persetujuan ke-{i + 1} tidak valid.")
        user_ids = _str_list(raw_step.get("user_ids"))
        roles = _str_list(raw_step.get("roles"))
        divisions = _str_list(raw_step.get("divisions"))
        unknown = [d for d in divisions if d not in DIVISION_IDS]
        if unknown:
            raise HTTPException(400, f"Divisi approver tidak dikenal: {', '.join(unknown)}")
        if not user_ids and not roles and not divisions:
            raise HTTPException(400, f"Langkah persetujuan ke-{i + 1} wajib punya approver (user / role / divisi).")
        sid = str(raw_step.get("id") or "").strip() or str(uuid.uuid4())
        level = i + 1
        if raw_step.get("level") is not None:
            try:
                level = int(raw_step.get("level"))
            except (TypeError, ValueError):
                level = i + 1
        steps.append({
            "id": sid,
            "level": level,
            "label": str(raw_step.get("label") or f"Level {level}").strip()[:80],
            "active": False if raw_step.get("active") is False else True,
            "user_ids": user_ids,
            "roles": roles,
            "divisions": divisions,
        })
    steps.sort(key=lambda s: (s.get("level") or 0, s.get("label") or ""))
    for i, step in enumerate(steps, start=1):
        step["level"] = i
    return steps


def normalize_document_template(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    sections = []
    for i, sec in enumerate(src.get("sections") or []):
        if not isinstance(sec, dict):
            continue
        heading = str(sec.get("heading") or "").strip()[:120]
        body = str(sec.get("body") or "").strip()[:4000]
        if heading or body:
            sections.append({"heading": heading, "body": body})
    prefix = str(src.get("numbering_prefix") or PREFIX_DOCUMENT).strip().upper() or PREFIX_DOCUMENT
    if not CODE_RX.match(prefix):
        prefix = PREFIX_DOCUMENT
    return {
        "title": str(src.get("title") or "Surat Keterangan").strip()[:160],
        "numbering_prefix": prefix[:16],
        "header_text": str(src.get("header_text") or "ETERNAL PACKAGING").strip()[:400],
        "footer_text": str(src.get("footer_text") or "Dokumen ini disetujui secara elektronik.").strip()[:400],
        "sections": sections or [{"heading": "Isi Pengajuan", "body": "{{submitted_fields}}"}],
    }


def active_fields(fields: List[dict]) -> List[dict]:
    return [f for f in (fields or []) if f.get("active") is not False]


def active_steps(steps: List[dict]) -> List[dict]:
    return [s for s in (steps or []) if s.get("active") is not False]


def validate_values(fields: List[dict], values: Any, *, require_files: bool = True) -> dict:
    src = values if isinstance(values, dict) else {}
    extra = [k for k in src.keys() if k not in {f["key"] for f in fields}]
    if extra:
        raise HTTPException(400, f"Field tidak dikenal: {', '.join(sorted(extra)[:8])}")
    out = {}
    for field in active_fields(fields):
        key = field["key"]
        raw = src.get(key)
        ftype = field["type"]
        empty = raw is None or raw == "" or raw is False
        if ftype == "checkbox":
            out[key] = bool(raw)
            if field["required"] and not out[key]:
                raise HTTPException(400, f"{field['label']} wajib diisi.")
            continue
        if ftype == "file":
            if field["required"] and require_files and not _file_ref(raw):
                raise HTTPException(400, f"{field['label']} wajib dilampirkan.")
            out[key] = _file_ref(raw) if raw else None
            continue
        if empty:
            if field["required"]:
                raise HTTPException(400, f"{field['label']} wajib diisi.")
            out[key] = None
            continue
        if ftype in ("number", "currency"):
            num = _parse_number(raw, field["label"])
            mn = field.get("minimum")
            mx = field.get("maximum")
            num_f = float(num)
            if mn is not None and num_f < float(mn):
                raise HTTPException(400, f"{field['label']} minimal {mn}.")
            if mx is not None and num_f > float(mx):
                raise HTTPException(400, f"{field['label']} maksimal {mx}.")
            out[key] = num_f if ftype == "number" else float(Decimal(str(num)).quantize(Decimal("0.01")))
            continue
        if ftype == "select":
            val = str(raw).strip()
            if val not in field.get("options") or []:
                raise HTTPException(400, f"{field['label']} harus salah satu pilihan yang tersedia.")
            out[key] = val
            continue
        if ftype in ("date", "datetime"):
            out[key] = str(raw).strip()[:32]
            continue
        out[key] = str(raw).strip()[:4000 if ftype == "textarea" else 500]
    return out


def is_step_approver(employee: dict, step: dict) -> bool:
    if not employee or not step:
        return False
    uid = str(employee.get("id") or "")
    role = str(employee.get("role") or "")
    if uid and uid in set(step.get("user_ids") or []):
        return True
    if role and role in set(step.get("roles") or []):
        return True
    if step.get("divisions") and (user_division_ids(employee) & set(step.get("divisions") or [])):
        return True
    return False


def current_pending_step(request: dict) -> Optional[dict]:
    if request.get("status") not in (STATUS_SUBMITTED, STATUS_WAITING):
        return None
    level = int(request.get("current_step_level") or 1)
    for step in active_steps((request.get("approval_snapshot") or {}).get("steps") or []):
        if int(step.get("level") or 0) == level:
            return step
    return None


def can_view_request(employee: dict, request: dict, perms: set) -> bool:
    if PERM_VIEW_ALL in perms:
        return True
    uid = str(employee.get("id") or "")
    owner = str((request.get("employee") or {}).get("id") or "")
    if uid and uid == owner and (PERM_VIEW_OWN in perms or PERM_SUBMIT in perms):
        return True
    if PERM_APPROVE in perms and is_step_approver(employee, current_pending_step(request)):
        return True
    if PERM_DOCUMENT in perms and request.get("status") == STATUS_APPROVED:
        return True
    history = request.get("approval_history") or []
    if PERM_APPROVE in perms and any(str((h.get("actor") or {}).get("id") or "") == uid for h in history):
        return True
    return False


def can_view_document(employee: dict, request: dict, perms: set) -> bool:
    if request.get("status") != STATUS_APPROVED:
        return False
    if PERM_DOCUMENT in perms or PERM_VIEW_ALL in perms:
        return True
    uid = str(employee.get("id") or "")
    owner = str((request.get("employee") or {}).get("id") or "")
    return bool(uid and uid == owner and PERM_VIEW_OWN in perms)


def _str_list(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out = []
    seen = set()
    for item in raw:
        val = str(item or "").strip()
        if val and val not in seen:
            seen.add(val)
            out.append(val)
    return out


def _opt_number(raw: Any, label: str):
    if raw is None or raw == "":
        return None
    return _parse_number(raw, label)


def _parse_number(raw: Any, label: str) -> Decimal:
    try:
        d = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{label} harus angka.")
    return d


def _file_ref(raw: Any) -> Optional[dict]:
    if isinstance(raw, dict) and str(raw.get("id") or "").strip():
        return {
            "id": str(raw.get("id")),
            "name": str(raw.get("name") or "lampiran"),
            "content_type": str(raw.get("content_type") or ""),
            "field_key": str(raw.get("field_key") or ""),
        }
    if isinstance(raw, str) and raw.strip():
        return {"id": raw.strip(), "name": "lampiran", "content_type": "", "field_key": ""}
    return None


def _type_code(raw: str) -> str:
    code = re.sub(r"[^A-Z0-9_]", "_", str(raw or "").strip().upper())
    code = re.sub(r"_+", "_", code).strip("_")
    if not CODE_RX.match(code):
        raise HTTPException(400, "Kode internal harus diawali huruf, 2–24 karakter (A-Z, 0-9, _).")
    return code


def snapshot_type(typ: dict) -> dict:
    return copy.deepcopy({
        "id": typ.get("id"),
        "code": typ.get("code"),
        "name": typ.get("name"),
        "category": typ.get("category"),
        "config_version": int(typ.get("config_version") or 1),
        "generates_pdf": bool(typ.get("generates_pdf")),
        "fields": copy.deepcopy(typ.get("form_fields") or []),
        "eligibility": copy.deepcopy(typ.get("eligibility") or {}),
        "steps": copy.deepcopy(typ.get("approval_steps") or []),
        "document_template": copy.deepcopy(typ.get("document_template") or {}),
    })


def default_seed_types() -> List[dict]:
    kasbon_fields = [
        {"key": "nominal", "label": "Nominal", "type": "currency", "required": True, "placeholder": "0", "options": [], "order": 1, "active": True, "minimum": 1, "maximum": None},
        {"key": "alasan", "label": "Alasan", "type": "textarea", "required": True, "placeholder": "Alasan kasbon", "options": [], "order": 2, "active": True},
        {"key": "tanggal_pengajuan", "label": "Tanggal Pengajuan", "type": "date", "required": True, "placeholder": "", "options": [], "order": 3, "active": True},
        {"key": "tanggal_potong", "label": "Rencana Tanggal Potong Gaji", "type": "date", "required": False, "placeholder": "", "options": [], "order": 4, "active": True},
        {"key": "lampiran", "label": "Lampiran", "type": "file", "required": False, "placeholder": "", "options": [], "order": 5, "active": True},
    ]
    izin_fields = [
        {"key": "tanggal_mulai", "label": "Tanggal Mulai", "type": "date", "required": True, "placeholder": "", "options": [], "order": 1, "active": True},
        {"key": "tanggal_selesai", "label": "Tanggal Selesai", "type": "date", "required": True, "placeholder": "", "options": [], "order": 2, "active": True},
        {"key": "jenis_izin", "label": "Jenis Izin", "type": "select", "required": True, "placeholder": "", "options": ["Izin Sakit", "Izin Pribadi", "Izin Keluarga", "Lainnya"], "order": 3, "active": True},
        {"key": "alasan", "label": "Alasan", "type": "textarea", "required": True, "placeholder": "Alasan izin", "options": [], "order": 4, "active": True},
        {"key": "lampiran", "label": "Lampiran", "type": "file", "required": False, "placeholder": "", "options": [], "order": 5, "active": True},
    ]
    return [
        _new_type_doc(
            name="Pengajuan Kasbon",
            code="KASBON",
            description="Pengajuan kasbon karyawan.",
            category="Keuangan",
            generates_pdf=True,
            fields=kasbon_fields,
            eligibility={"mode": "any", "all_employees": True, "user_ids": [], "roles": [], "divisions": []},
            steps=[{"label": "Level 1", "roles": ["admin"], "user_ids": [], "divisions": []}],
            template={
                "title": "Surat Persetujuan Kasbon",
                "numbering_prefix": "SKY",
                "header_text": "ETERNAL PACKAGING",
                "footer_text": "Dokumen ini disetujui secara elektronik. Bukan tanda tangan digital tersertifikasi.",
                "sections": [{"heading": "Rincian Kasbon", "body": "{{submitted_fields}}"}],
            },
        ),
        _new_type_doc(
            name="Pengajuan Izin",
            code="IZIN",
            description="Pengajuan izin karyawan.",
            category="Kehadiran",
            generates_pdf=True,
            fields=izin_fields,
            eligibility={"mode": "any", "all_employees": True, "user_ids": [], "roles": [], "divisions": []},
            steps=[{"label": "Level 1", "roles": ["admin"], "user_ids": [], "divisions": []}],
            template={
                "title": "Surat Persetujuan Izin",
                "numbering_prefix": "SKY",
                "header_text": "ETERNAL PACKAGING",
                "footer_text": "Dokumen ini disetujui secara elektronik. Bukan tanda tangan digital tersertifikasi.",
                "sections": [{"heading": "Rincian Izin", "body": "{{submitted_fields}}"}],
            },
        ),
    ]


def _new_type_doc(*, name, code, description="", category="", generates_pdf=False, fields=None, eligibility=None, steps=None, template=None, active=True) -> dict:
    ts = now_iso()
    return {
        "id": str(uuid.uuid4()),
        "code": code,
        "name": name,
        "description": description,
        "category": category,
        "active": active,
        "generates_pdf": bool(generates_pdf),
        "eligibility": normalize_eligibility(eligibility or {"mode": "any", "all_employees": True}),
        "form_fields": normalize_fields(fields or []),
        "approval_steps": normalize_approval_steps(steps or []),
        "document_template": normalize_document_template(template),
        "config_version": 1,
        "created_at": ts,
        "updated_at": ts,
    }


async def ensure_indexes(db) -> None:
    await db[TYPES_COLLECTION].create_index("id", unique=True)
    await db[TYPES_COLLECTION].create_index("code", unique=True)
    await db[REQUESTS_COLLECTION].create_index("id", unique=True)
    try:
        await db[REQUESTS_COLLECTION].create_index("request_number", unique=True)
    except Exception:
        pass
    try:
        await db[REQUESTS_COLLECTION].create_index("document.document_number", unique=True, sparse=True)
    except Exception:
        pass
    await db[REQUESTS_COLLECTION].create_index("employee.id")
    await db[REQUESTS_COLLECTION].create_index("status")
    await db[REQUESTS_COLLECTION].create_index("type_id")


async def seed_default_types(db) -> None:
    if await db[TYPES_COLLECTION].count_documents({}) > 0:
        return
    for doc in default_seed_types():
        try:
            await db[TYPES_COLLECTION].insert_one(doc)
        except DuplicateKeyError:
            continue


async def list_types(db, *, include_inactive: bool = True) -> List[dict]:
    await seed_default_types(db)
    q = {} if include_inactive else {"active": {"$ne": False}}
    rows = await db[TYPES_COLLECTION].find(q, {"_id": 0}).sort("name", 1).to_list(500)
    return [public_type(r) for r in rows]


async def get_type(db, type_id: str) -> dict:
    doc = await db[TYPES_COLLECTION].find_one({"id": type_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Jenis pengajuan tidak ditemukan.")
    return public_type(doc)


async def create_type(db, payload: dict, user: dict) -> dict:
    code = _type_code(payload.get("code") or payload.get("name"))
    if await db[TYPES_COLLECTION].find_one({"code": code}):
        raise HTTPException(409, f"Kode '{code}' sudah dipakai.")
    doc = _new_type_doc(
        name=str(payload.get("name") or "").strip() or code,
        code=code,
        description=str(payload.get("description") or "").strip()[:500],
        category=str(payload.get("category") or "").strip()[:80],
        generates_pdf=bool(payload.get("generates_pdf")),
        fields=payload.get("form_fields") or payload.get("fields") or [],
        eligibility=payload.get("eligibility"),
        steps=payload.get("approval_steps") or payload.get("steps") or [],
        template=payload.get("document_template"),
        active=False if payload.get("active") is False else True,
    )
    await db[TYPES_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return public_type(doc)


async def update_type(db, type_id: str, payload: dict, user: dict) -> dict:
    existing = await db[TYPES_COLLECTION].find_one({"id": type_id})
    if not existing:
        raise HTTPException(404, "Jenis pengajuan tidak ditemukan.")
    updates = {"updated_at": now_iso(), "updated_by": actor_from_user(user)}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Nama jenis pengajuan wajib diisi.")
        updates["name"] = name[:120]
    if "description" in payload:
        updates["description"] = str(payload.get("description") or "").strip()[:500]
    if "category" in payload:
        updates["category"] = str(payload.get("category") or "").strip()[:80]
    if "active" in payload:
        updates["active"] = bool(payload.get("active"))
    if "generates_pdf" in payload:
        updates["generates_pdf"] = bool(payload.get("generates_pdf"))
    if "code" in payload and payload.get("code"):
        code = _type_code(payload.get("code"))
        other = await db[TYPES_COLLECTION].find_one({"code": code, "id": {"$ne": type_id}})
        if other:
            raise HTTPException(409, f"Kode '{code}' sudah dipakai.")
        updates["code"] = code
    if "eligibility" in payload:
        updates["eligibility"] = normalize_eligibility(payload.get("eligibility"))
    if "form_fields" in payload or "fields" in payload:
        updates["form_fields"] = normalize_fields(payload.get("form_fields") or payload.get("fields"))
    if "approval_steps" in payload or "steps" in payload:
        updates["approval_steps"] = normalize_approval_steps(payload.get("approval_steps") or payload.get("steps"))
    if "document_template" in payload:
        updates["document_template"] = normalize_document_template(payload.get("document_template"))
    if any(k in updates for k in ("form_fields", "eligibility", "approval_steps", "document_template", "generates_pdf", "name", "code")):
        updates["config_version"] = int(existing.get("config_version") or 1) + 1
    await db[TYPES_COLLECTION].update_one({"id": type_id}, {"$set": updates})
    return await get_type(db, type_id)


async def resolve_employee(db, user: dict) -> dict:
    email = str((user or {}).get("email") or "").strip().lower()
    if email:
        row = await db.users.find_one({"email": email}, {"password_hash": 0, "_id": 0})
        if row:
            return row
    uid = str((user or {}).get("user_id") or (user or {}).get("id") or "")
    if uid:
        row = await db.users.find_one({"id": uid}, {"password_hash": 0, "_id": 0})
        if row:
            return row
    raise HTTPException(403, "Data karyawan tidak ditemukan.")


async def list_eligible_types(db, employee: dict) -> List[dict]:
    await seed_default_types(db)
    rows = await db[TYPES_COLLECTION].find({"active": {"$ne": False}}, {"_id": 0}).sort("name", 1).to_list(500)
    return [public_type(r) for r in rows if is_eligible(employee, r.get("eligibility") or {})]


def _append_audit(doc: dict, action: str, actor: dict, *, step=None, note: str = "") -> None:
    events = list(doc.get("audit_events") or [])
    events.append({
        "id": str(uuid.uuid4()),
        "action": action,
        "actor": copy.deepcopy(actor),
        "timestamp": now_iso(),
        "step_level": step,
        "note": str(note or ""),
    })
    doc["audit_events"] = events


async def submit_request(db, payload: dict, user: dict, employee: dict) -> dict:
    async with get_named_lock("employee_admin_request"):
        type_id = str(payload.get("type_id") or "").strip()
        typ = await db[TYPES_COLLECTION].find_one({"id": type_id}, {"_id": 0})
        if not typ or typ.get("active") is False:
            raise HTTPException(404, "Jenis pengajuan tidak tersedia.")
        if not is_eligible(employee, typ.get("eligibility") or {}):
            raise HTTPException(403, "Anda tidak berhak mengajukan jenis ini.")
        snap = snapshot_type(typ)
        values = validate_values(snap["fields"], payload.get("values") or {})
        actor = actor_from_user(user, employee)
        steps = active_steps(snap["steps"])
        status = STATUS_WAITING if steps else STATUS_APPROVED
        if steps:
            status = STATUS_SUBMITTED if steps[0]["level"] == 1 else STATUS_WAITING
        ts = now_iso()
        emp_snap = {
            "id": str(employee.get("id") or ""),
            "name": str(employee.get("name") or employee.get("email") or ""),
            "email": str(employee.get("email") or ""),
            "employee_id": str(employee.get("employee_id") or ""),
            "role": str(employee.get("role") or ""),
        }
        doc = {
            "id": str(uuid.uuid4()),
            "type_id": typ["id"],
            "type_code": typ.get("code"),
            "type_name": typ.get("name"),
            "status": status,
            "employee": emp_snap,
            "form_snapshot": {"config_version": snap["config_version"], "fields": snap["fields"]},
            "approval_snapshot": {"config_version": snap["config_version"], "steps": snap["steps"]},
            "type_snapshot": snap,
            "values": values,
            "attachments": _attachments_from_values(values, snap["fields"]),
            "current_step_level": steps[0]["level"] if steps else None,
            "current_step_label": steps[0]["label"] if steps else None,
            "approval_history": [],
            "audit_events": [],
            "document": None,
            "submitted_at": ts,
            "created_at": ts,
            "updated_at": ts,
            "created_by": actor,
        }
        _append_audit(doc, "submitted", actor, note=str(payload.get("note") or ""))
        if not steps:
            _append_audit(doc, "approved", actor, note="Otomatis disetujui (tanpa alur persetujuan).")
        saved = await insert_with_unique_number(
            db, REQUESTS_COLLECTION, "request_number", doc,
            lambda: next_dated_seq(db, REQUESTS_COLLECTION, "request_number", PREFIX_REQUEST),
        )
        if saved.get("status") == STATUS_APPROVED and snap.get("generates_pdf"):
            saved = await generate_official_pdf(db, saved, actor)
        return public_request(saved)


def _attachments_from_values(values: dict, fields: List[dict]) -> List[dict]:
    out = []
    for field in fields:
        if field.get("type") != "file":
            continue
        ref = values.get(field["key"])
        if isinstance(ref, dict) and ref.get("id"):
            item = dict(ref)
            item["field_key"] = field["key"]
            out.append(item)
    return out


async def get_request(db, request_id: str) -> dict:
    doc = await db[REQUESTS_COLLECTION].find_one({"id": request_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Pengajuan tidak ditemukan.")
    return doc


async def list_requests(db, *, employee: dict, perms: set, scope: str, q: str = "") -> List[dict]:
    filt: Dict[str, Any] = {}
    uid = str(employee.get("id") or "")
    if scope == "mine":
        filt["employee.id"] = uid
    elif scope == "approve":
        rows = await db[REQUESTS_COLLECTION].find(
            {"status": {"$in": [STATUS_SUBMITTED, STATUS_WAITING]}}, {"_id": 0}
        ).sort("submitted_at", -1).to_list(500)
        out = []
        for row in rows:
            step = current_pending_step(row)
            if step and is_step_approver(employee, step):
                out.append(public_request(row))
        return _filter_q(out, q)
    elif scope == "archive":
        filt["status"] = STATUS_APPROVED
        if PERM_VIEW_ALL not in perms and PERM_DOCUMENT not in perms:
            filt["employee.id"] = uid
    elif scope == "all":
        if PERM_VIEW_ALL not in perms:
            filt["employee.id"] = uid
    else:
        filt["employee.id"] = uid
    rows = await db[REQUESTS_COLLECTION].find(filt, {"_id": 0}).sort("submitted_at", -1).to_list(500)
    visible = [public_request(r) for r in rows if can_view_request(employee, r, perms)]
    return _filter_q(visible, q)


def _filter_q(rows: List[dict], q: str) -> List[dict]:
    needle = (q or "").strip().lower()
    if not needle:
        return rows
    out = []
    for row in rows:
        blob = " ".join([
            str(row.get("request_number") or ""),
            str(row.get("type_name") or ""),
            str((row.get("employee") or {}).get("name") or ""),
            str(row.get("status") or ""),
        ]).lower()
        if needle in blob:
            out.append(row)
    return out


async def decide_request(db, request_id: str, payload: dict, user: dict, employee: dict) -> dict:
    async with get_named_lock(f"employee_admin_decide:{request_id}"):
        doc = await get_request(db, request_id)
        action = str(payload.get("action") or "").strip().lower()
        if action in ("approve", "setujui"):
            action = "approved"
        elif action in ("reject", "tolak"):
            action = "rejected"
        else:
            raise HTTPException(400, "Aksi harus Setujui atau Tolak.")
        if doc.get("status") not in (STATUS_SUBMITTED, STATUS_WAITING):
            raise HTTPException(409, "Pengajuan ini tidak menunggu persetujuan.")
        step = current_pending_step(doc)
        if not step:
            raise HTTPException(409, "Tidak ada langkah persetujuan yang aktif.")
        if not is_step_approver(employee, step):
            raise HTTPException(403, "Anda tidak berhak menyetujui langkah ini.")
        expected_level = int(doc.get("current_step_level") or 0)
        if int(step.get("level") or 0) != expected_level:
            raise HTTPException(409, "Langkah persetujuan sudah berubah.")
        if expected_level > 1:
            prior_ok = any(
                int(h.get("level") or 0) == expected_level - 1 and h.get("action") == "approved"
                for h in (doc.get("approval_history") or [])
            )
            if not prior_ok:
                raise HTTPException(409, "Level sebelumnya belum disetujui.")
        already = any(
            int(h.get("level") or 0) == expected_level and h.get("action") in ("approved", "rejected")
            for h in (doc.get("approval_history") or [])
        )
        if already:
            raise HTTPException(409, "Langkah ini sudah diputuskan.")
        actor = actor_from_user(user, employee)
        note = str(payload.get("note") or "").strip()[:1000]
        rec = {
            "id": str(uuid.uuid4()),
            "level": expected_level,
            "step_id": step.get("id"),
            "step_label": step.get("label"),
            "action": action,
            "actor": copy.deepcopy(actor),
            "timestamp": now_iso(),
            "note": note,
        }
        history = list(doc.get("approval_history") or [])
        history.append(rec)
        updates: Dict[str, Any] = {
            "approval_history": history,
            "updated_at": now_iso(),
        }
        audit_doc = dict(doc)
        audit_doc["approval_history"] = history
        _append_audit(audit_doc, action, actor, step=expected_level, note=note)
        updates["audit_events"] = audit_doc["audit_events"]
        remaining = [s for s in active_steps((doc.get("approval_snapshot") or {}).get("steps") or []) if int(s.get("level") or 0) > expected_level]
        if action == "rejected":
            updates["status"] = STATUS_REJECTED
            updates["current_step_level"] = expected_level
            updates["current_step_label"] = step.get("label")
        elif remaining:
            nxt = remaining[0]
            updates["status"] = STATUS_WAITING
            updates["current_step_level"] = nxt["level"]
            updates["current_step_label"] = nxt["label"]
        else:
            updates["status"] = STATUS_APPROVED
            updates["current_step_level"] = expected_level
            updates["current_step_label"] = step.get("label")
        result = await db[REQUESTS_COLLECTION].find_one_and_update(
            {
                "id": request_id,
                "status": {"$in": [STATUS_SUBMITTED, STATUS_WAITING]},
                "current_step_level": expected_level,
            },
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        if not result:
            raise HTTPException(409, "Pengajuan sudah diproses oleh approver lain.")
        result.pop("_id", None)
        if result.get("status") == STATUS_APPROVED and (result.get("type_snapshot") or {}).get("generates_pdf"):
            result = await generate_official_pdf(db, result, actor)
        return public_request(result)


def _safe_text(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _placeholder_map(request: dict) -> Dict[str, str]:
    emp = request.get("employee") or {}
    values = request.get("values") or {}
    fields = (request.get("form_snapshot") or {}).get("fields") or []
    lines = []
    data = {
        "employee.name": str(emp.get("name") or "-"),
        "employee.email": str(emp.get("email") or "-"),
        "employee.employee_id": str(emp.get("employee_id") or "-"),
        "employee.role": str(emp.get("role") or "-"),
        "request.number": str(request.get("request_number") or "-"),
        "request.type": str(request.get("type_name") or "-"),
        "request.date": format_pdf_date(request.get("submitted_at"), fallback="-"),
        "document.number": str((request.get("document") or {}).get("document_number") or "-"),
        "company.name": "Eternal Packaging",
    }
    for field in fields:
        if field.get("active") is False:
            continue
        key = field.get("key")
        val = values.get(key)
        if field.get("type") == "checkbox":
            shown = "Ya" if val else "Tidak"
        elif field.get("type") == "file":
            shown = (val or {}).get("name") if isinstance(val, dict) else (str(val or "-") or "-")
        elif field.get("type") in ("date", "datetime") and val is not None and val != "":
            shown = format_pdf_date(val, fallback="-")
        elif field.get("type") == "currency" and val is not None:
            shown = f"Rp {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        else:
            shown = "-" if val is None or val == "" else str(val)
        data[f"field.{key}"] = shown
        lines.append(f"{field.get('label')}: {shown}")
    data["submitted_fields"] = "\n".join(lines) if lines else "-"
    return data


def render_template_text(text: str, data: Dict[str, str]) -> str:
    def repl(match):
        key = match.group(1)
        return data.get(key, "")
    return PLACEHOLDER_RX.sub(repl, text or "")


def generate_pdf_bytes(request: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    template = copy.deepcopy((request.get("document") or {}).get("template_snapshot") or (request.get("type_snapshot") or {}).get("document_template") or {})
    data = _placeholder_map(request)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.6 * cm, leftMargin=1.6 * cm, topMargin=1.6 * cm, bottomMargin=1.6 * cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("EaTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=colors.HexColor("#0f172a"))
    h_style = ParagraphStyle("EaH", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=10, leading=14, textColor=colors.HexColor("#0f172a"), spaceBefore=8)
    body_style = ParagraphStyle("EaBody", parent=styles["Normal"], fontName="Helvetica", fontSize=9, leading=13, textColor=colors.HexColor("#1e293b"))
    muted = ParagraphStyle("EaMuted", parent=styles["Normal"], fontName="Helvetica", fontSize=8, leading=11, textColor=colors.HexColor("#64748b"))
    elements = []
    header = _safe_text(render_template_text(template.get("header_text") or "ETERNAL PACKAGING", data)).replace("\n", "<br/>")
    title = _safe_text(render_template_text(template.get("title") or "Surat Keterangan", data))
    doc_no = _safe_text((request.get("document") or {}).get("document_number") or request.get("request_number") or "")
    elements.append(Paragraph(f"<b>{header}</b>", title_style))
    elements.append(Paragraph(title, h_style))
    elements.append(Paragraph(f"Nomor: {doc_no}", body_style))
    elements.append(Spacer(1, 0.15 * cm))
    elements.append(HRFlowable(width="100%", thickness=1.2, color=colors.HexColor("#0369a1"), spaceAfter=8))
    emp = request.get("employee") or {}
    meta = [
        [Paragraph("<b>Karyawan</b>", body_style), Paragraph(_safe_text(emp.get("name")), body_style)],
        [Paragraph("<b>ID Karyawan</b>", body_style), Paragraph(_safe_text(emp.get("employee_id")), body_style)],
        [Paragraph("<b>No Pengajuan</b>", body_style), Paragraph(_safe_text(request.get("request_number")), body_style)],
        [Paragraph("<b>Tanggal</b>", body_style), Paragraph(_safe_text(format_pdf_date(request.get("submitted_at"), fallback="-")), body_style)],
    ]
    tbl = Table(meta, colWidths=[4.5 * cm, 12 * cm])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(tbl)
    for sec in template.get("sections") or []:
        heading = _safe_text(render_template_text(sec.get("heading") or "", data))
        body = _safe_text(render_template_text(sec.get("body") or "", data)).replace("\n", "<br/>")
        if heading:
            elements.append(Paragraph(heading, h_style))
        if body:
            elements.append(Paragraph(body, body_style))
    elements.append(Paragraph("Jejak persetujuan elektronik", h_style))
    elements.append(Paragraph("Disetujui secara elektronik. Bukan tanda tangan digital tersertifikasi.", muted))
    for rec in request.get("approval_history") or []:
        if rec.get("action") != "approved":
            continue
        actor = rec.get('actor') or {}
        ts_str = format_pdf_date(rec.get('timestamp')) if rec.get('timestamp') else "-"
        line = f"Level {rec.get('level')}: {_safe_text(actor.get('name'))} — {_safe_text(ts_str)}"
        if rec.get("note"):
            line += f"<br/>Catatan: {_safe_text(rec.get('note'))}"
        elements.append(Paragraph(line, body_style))
    footer = _safe_text(render_template_text(template.get("footer_text") or "", data))
    if footer:
        elements.append(Spacer(1, 0.4 * cm))
        elements.append(Paragraph(footer, muted))
    doc.build(elements)
    return buffer.getvalue()


async def generate_official_pdf(db, request: dict, actor: dict) -> dict:
    existing = request.get("document") or {}
    if existing.get("generated") and existing.get("file_id"):
        return request
    prefix = str(((request.get("type_snapshot") or {}).get("document_template") or {}).get("numbering_prefix") or PREFIX_DOCUMENT)
    if not CODE_RX.match(prefix):
        prefix = PREFIX_DOCUMENT
    async with get_named_lock("employee_admin_pdf"):
        fresh = await db[REQUESTS_COLLECTION].find_one({"id": request["id"]}, {"_id": 0})
        if not fresh:
            return request
        if (fresh.get("document") or {}).get("generated") and (fresh.get("document") or {}).get("file_id"):
            return fresh
        if fresh.get("status") != STATUS_APPROVED:
            return fresh
        claimed = await db[REQUESTS_COLLECTION].find_one_and_update(
            {"id": request["id"], "status": STATUS_APPROVED, "document.generated": {"$ne": True}},
            {"$set": {"document": {"generated": True, "generating": True}, "updated_at": now_iso()}},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            latest = await db[REQUESTS_COLLECTION].find_one({"id": request["id"]}, {"_id": 0})
            return latest or fresh
        claimed.pop("_id", None)
        try:
            doc_number = await next_dated_seq(db, REQUESTS_COLLECTION, "document.document_number", prefix)
            template_snap = copy.deepcopy((claimed.get("type_snapshot") or {}).get("document_template") or {})
            claimed["document"] = {
                "generated": True,
                "generating": False,
                "document_number": doc_number,
                "template_snapshot": template_snap,
                "generated_at": now_iso(),
                "file_id": None,
                "content_type": "application/pdf",
            }
            pdf_bytes = generate_pdf_bytes(claimed)
            from services import storage as obj_storage
            file_id = str(uuid.uuid4())
            storage_path = f"{obj_storage.APP_NAME}/employee-admin/{claimed['id']}/{file_id}.pdf"
            obj_storage.put_object(storage_path, pdf_bytes, "application/pdf")
            claimed["document"]["file_id"] = file_id
            claimed["document"]["storage_path"] = storage_path
            _append_audit(claimed, "pdf_generated", actor, note=doc_number)
            await db.files.insert_one({
                "id": file_id,
                "kind": FILES_KIND,
                "request_id": claimed["id"],
                "storage_path": storage_path,
                "content_type": "application/pdf",
                "original_filename": f"{doc_number}.pdf",
                "uploaded_at": now_iso(),
                "is_deleted": False,
            })
            await db[REQUESTS_COLLECTION].update_one(
                {"id": claimed["id"]},
                {"$set": {"document": claimed["document"], "audit_events": claimed["audit_events"], "updated_at": now_iso()}},
            )
            claimed.pop("_id", None)
            return claimed
        except Exception:
            await db[REQUESTS_COLLECTION].update_one(
                {"id": request["id"], "document.generating": True},
                {"$unset": {"document": ""}},
            )
            raise


async def attach_upload_meta(db, request_id: str, field_key: str, file_id: str, name: str, content_type: str, storage_path: str) -> dict:
    return {
        "id": file_id,
        "name": name,
        "content_type": content_type,
        "field_key": field_key,
        "storage_path": storage_path,
        "request_id": request_id,
    }


def sniff_content_type(data: bytes, filename: str, claimed: str) -> str:
    name = (filename or "").lower()
    claimed = (claimed or "").lower()
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"%PDF":
        return "application/pdf"
    if claimed in ALLOWED_FILE_TYPES:
        return claimed
    if name.endswith(".jpg") or name.endswith(".jpeg"):
        return "image/jpeg"
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".pdf"):
        return "application/pdf"
    raise HTTPException(400, "Tipe file tidak diizinkan. Gunakan JPG, PNG, WEBP, atau PDF.")
