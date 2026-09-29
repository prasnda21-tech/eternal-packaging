"""Stored print templates. Drafts do not change the active PDF."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.print_template_model import (
    AUDIENCES,
    DOCUMENT_DELIVERY,
    DOCUMENT_INVOICE,
    DOCUMENT_TYPES,
    STATUSES,
    builtin_design,
    clone_design,
    print_cache_token,
    sample_delivery,
    sample_invoice,
    validate_design,
    build_invoice_context,
    build_delivery_context,
)
from services.print_template_render import DesignRenderError, render_design_pdf

TEMPLATES = "print_templates"
VERSIONS = "print_template_versions"
ASSETS = "print_template_assets"
DEFAULTS = "print_template_defaults"

# Test hooks. They sit inside the real write path so two requests can pass the same check.
_before_revision_write = None
_before_builtin_insert = None
_before_pointer_write = None
_before_archive_rollback = None
_indexed_databases = set()

BUILTIN_IDS = {
    (DOCUMENT_INVOICE, "customer"): "builtin-sales-invoice-customer",
    (DOCUMENT_INVOICE, "internal"): "builtin-sales-invoice-internal",
    (DOCUMENT_DELIVERY, "customer"): "builtin-sales-delivery-customer",
    (DOCUMENT_DELIVERY, "internal"): "builtin-sales-delivery-internal",
}

DOCUMENT_LABELS = {
    DOCUMENT_INVOICE: "Faktur Penjualan",
    DOCUMENT_DELIVERY: "Surat Jalan",
}
AUDIENCE_LABELS = {"customer": "Pelanggan", "internal": "Internal"}
STATUS_LABELS = {"draft": "Draf", "active": "Aktif", "archived": "Diarsipkan"}

INVOICE_PRINT_PERMS = (
    "sales.invoice.view",
    "sales_order.view",
    "po.view",
    "sales.delivery.view",
)
DELIVERY_PRINT_PERMS = (
    "sales.delivery.print",
    "sales.delivery.view",
    "sales_order.view",
    "po.view",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def actor_of(user: Optional[dict]) -> dict:
    user = user or {}
    return {
        "id": str(user.get("user_id") or user.get("id") or user.get("email") or ""),
        "name": str(user.get("name") or user.get("email") or "Pengguna"),
    }


def _public_template(doc: dict, *, include_design: bool = False, default_ids: Optional[set] = None) -> dict:
    if default_ids is None:
        is_default = bool(doc.get("is_default"))
    else:
        is_default = doc.get("id") in default_ids
    row = {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "document_type": doc.get("document_type"),
        "document_label": DOCUMENT_LABELS.get(doc.get("document_type"), ""),
        "audience": doc.get("audience"),
        "audience_label": AUDIENCE_LABELS.get(doc.get("audience"), ""),
        "status": doc.get("status"),
        "status_label": STATUS_LABELS.get(doc.get("status"), ""),
        "is_default": is_default,
        "is_builtin": bool(doc.get("is_builtin")),
        "legacy_renderer": bool(doc.get("legacy_renderer")),
        "has_draft": bool(doc.get("has_draft")),
        "revision": int(doc.get("revision") or 0),
        "active_version": int(doc.get("active_version") or 0),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "created_at": doc.get("created_at"),
    }
    if include_design:
        row["design"] = doc.get("design")
        row["active_design"] = doc.get("active_design")
    return row


async def _insert_version(db, template_id: str, action: str, user: dict, design: Optional[dict], version: int, note: str = ""):
    await db[VERSIONS].insert_one({
        "id": str(uuid.uuid4()),
        "template_id": template_id,
        "version": int(version),
        "action": action,
        "design": clone_design(design) if design is not None else None,
        "actor": actor_of(user),
        "at": now_iso(),
        "note": note,
    })


def _builtin_record(document_type: str, audience: str) -> dict:
    stamp = now_iso()
    design = builtin_design(document_type)
    template_id = BUILTIN_IDS[(document_type, audience)]
    return {
        "_id": template_id,
        "id": template_id,
        "name": f"Bawaan {DOCUMENT_LABELS[document_type]} ({AUDIENCE_LABELS[audience]})",
        "document_type": document_type,
        "audience": audience,
        "status": "active",
        "is_default": True,
        "is_builtin": True,
        "legacy_renderer": True,
        "has_draft": False,
        "design": design,
        "active_design": clone_design(design),
        "active_version": 1,
        "revision": 1,
        "created_at": stamp,
        "updated_at": stamp,
        "created_by": {"id": "system", "name": "Sistem"},
        "updated_by": {"id": "system", "name": "Sistem"},
    }


async def _ensure_unique_indexes(db) -> None:
    """Unique id indexes exist before the first insert. _id is also the logical id."""
    key = id(db)
    if key in _indexed_databases:
        return
    await db[TEMPLATES].create_index("id", unique=True, name="print_template_id_unique")
    await db[DEFAULTS].create_index("id", unique=True, name="print_template_default_id_unique")
    _indexed_databases.add(key)


async def ensure_builtins(db) -> None:
    await _ensure_unique_indexes(db)
    for key, template_id in BUILTIN_IDS.items():
        existing = await db[TEMPLATES].find_one({"id": template_id}, {"_id": 0, "id": 1})
        if not existing:
            if _before_builtin_insert is not None:
                await _before_builtin_insert(template_id)
            try:
                await db[TEMPLATES].insert_one(_builtin_record(*key))
            except DuplicateKeyError:
                pass
        await _ensure_pointer(db, key[0], key[1])


async def list_templates(db, *, document_type: str = "", status: str = "") -> List[dict]:
    await ensure_builtins(db)
    query: Dict[str, Any] = {}
    if document_type:
        if document_type not in DOCUMENT_TYPES:
            raise HTTPException(400, "Jenis dokumen tidak didukung.")
        query["document_type"] = document_type
    if status:
        if status not in STATUSES:
            raise HTTPException(400, "Status tidak dikenal.")
        query["status"] = status
    rows = await db[TEMPLATES].find(query, {"_id": 0}).to_list(500)
    rows.sort(key=lambda row: (row.get("document_type") or "", row.get("audience") or "", row.get("name") or ""))
    defaults = await _default_ids(db)
    return [_public_template(row, default_ids=defaults) for row in rows]


async def get_template(db, template_id: str, *, include_design: bool = True) -> dict:
    await ensure_builtins(db)
    doc = await db[TEMPLATES].find_one({"id": str(template_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Template tidak ditemukan.")
    return _public_template(doc, include_design=include_design, default_ids=await _default_ids(db))


async def _require(db, template_id: str) -> dict:
    await ensure_builtins(db)
    doc = await db[TEMPLATES].find_one({"id": str(template_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Template tidak ditemukan.")
    return doc


def _conflict(current_revision: int) -> HTTPException:
    return HTTPException(409, {
        "message": "Desain ini sudah diubah di tempat lain. Isi editor Anda tetap dipertahankan.",
        "revision": int(current_revision or 0),
    })


def _check_revision(doc: dict, expected: int):
    if int(expected) != int(doc.get("revision") or 0):
        raise _conflict(doc.get("revision") or 0)


def _matched_count(result) -> int:
    if result is None:
        return 0
    value = getattr(result, "matched_count", None)
    if value is None and isinstance(result, dict):
        value = result.get("matched_count")
    return int(value or 0)


async def _commit_if_revision(db, template_id: str, expected_revision: int, changes: dict) -> None:
    """Write only when revision still matches. A loser records nothing."""
    if _before_revision_write is not None:
        await _before_revision_write(template_id)
    result = await db[TEMPLATES].update_one(
        {"id": str(template_id), "revision": int(expected_revision)},
        {"$set": {**changes, "revision": int(expected_revision) + 1}},
    )
    if _matched_count(result) == 1:
        return
    current = await db[TEMPLATES].find_one({"id": str(template_id)}, {"_id": 0, "revision": 1})
    raise _conflict((current or {}).get("revision") or 0)


def _pointer_id(document_type: str, audience: str) -> str:
    return f"default:{document_type}:{audience}"


async def _ensure_pointer(db, document_type: str, audience: str) -> dict:
    pointer_id = _pointer_id(document_type, audience)
    builtin_id = BUILTIN_IDS[(document_type, audience)]
    try:
        await db[DEFAULTS].update_one(
            {"_id": pointer_id},
            {"$setOnInsert": {
                "id": pointer_id,
                "document_type": document_type,
                "audience": audience,
                "template_id": builtin_id,
            }},
            upsert=True,
        )
    except DuplicateKeyError:
        pass
    return await db[DEFAULTS].find_one({"id": pointer_id}, {"_id": 0})


async def _default_ids(db) -> set:
    rows = await db[DEFAULTS].find({}, {"_id": 0, "template_id": 1}).to_list(20)
    return {row.get("template_id") for row in rows if row.get("template_id")}


async def _resolve_authoritative(db, document_type: str, audience: str) -> Optional[dict]:
    """One pointer document decides the default. An unusable target falls back to the builtin."""
    pointer = await _ensure_pointer(db, document_type, audience)
    target_id = str((pointer or {}).get("template_id") or "")
    doc = await db[TEMPLATES].find_one({"id": target_id}, {"_id": 0}) if target_id else None
    if doc and doc.get("status") == "active" and doc.get("document_type") == document_type and doc.get("audience") == audience:
        return doc
    builtin_id = BUILTIN_IDS[(document_type, audience)]
    builtin = await db[TEMPLATES].find_one({"id": builtin_id}, {"_id": 0})
    if builtin and builtin.get("status") == "active":
        return builtin
    return None


async def create_template(db, payload: dict, user: dict) -> dict:
    await ensure_builtins(db)
    document_type = str(payload.get("document_type") or "")
    audience = str(payload.get("audience") or "customer")
    name = str(payload.get("name") or "").strip()
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(400, "Jenis dokumen tidak didukung.")
    if audience not in AUDIENCES:
        raise HTTPException(400, "Penggunaan template harus Pelanggan atau Internal.")
    if not name:
        raise HTTPException(400, "Nama template wajib diisi.")
    source_id = BUILTIN_IDS[(document_type, audience)]
    source = await _require(db, source_id)
    design = clone_design(source.get("design") or builtin_design(document_type))
    design["document_type"] = document_type
    stamp = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "name": name[:120],
        "document_type": document_type,
        "audience": audience,
        "status": "draft",
        "is_default": False,
        "is_builtin": False,
        "legacy_renderer": False,
        "has_draft": True,
        "design": design,
        "active_design": None,
        "active_version": 0,
        "revision": 1,
        "created_at": stamp,
        "updated_at": stamp,
        "created_by": actor_of(user),
        "updated_by": actor_of(user),
    }
    await db[TEMPLATES].insert_one(doc)
    await _insert_version(db, doc["id"], "create", user, design, 0, "Salinan desain bawaan")
    return _public_template(doc, include_design=True)


async def save_draft(db, template_id: str, design: dict, expected_revision: int, user: dict) -> dict:
    doc = await _require(db, template_id)
    _check_revision(doc, expected_revision)
    if not isinstance(design, dict):
        raise HTTPException(400, "Desain tidak valid.")
    design = clone_design(design)
    design["document_type"] = doc["document_type"]
    design["schema_version"] = 1
    stamp = now_iso()
    await _commit_if_revision(db, doc["id"], expected_revision, {
        "design": design,
        "has_draft": True,
        "updated_at": stamp,
        "updated_by": actor_of(user),
    })
    await _insert_version(db, doc["id"], "save_draft", user, design, int(doc.get("active_version") or 0), "Simpan draf")
    updated = await _require(db, doc["id"])
    row = _public_template(updated, include_design=True, default_ids=await _default_ids(db))
    row["errors"] = validate_design(design, doc["document_type"])
    return row


async def activate_template(db, template_id: str, expected_revision: int, user: dict) -> dict:
    doc = await _require(db, template_id)
    _check_revision(doc, expected_revision)
    if doc.get("is_builtin"):
        raise HTTPException(400, "Desain bawaan tetap memakai renderer yang sudah ada. Salin template untuk mengaktifkan desain kustom.")
    design = doc.get("design")
    errors = validate_design(design, doc["document_type"])
    if errors:
        raise HTTPException(400, {"message": errors[0]["message"], "errors": errors})
    try:
        rendered = await _render_with_assets(db, design, doc["document_type"], preview=True)
    except DesignRenderError as exc:
        raise HTTPException(400, {"message": str(exc), "errors": exc.errors})
    if not rendered.startswith(b"%PDF"):
        raise HTTPException(400, "Aktivasi dibatalkan karena preview PDF gagal.")
    version = int(doc.get("active_version") or 0) + 1
    await _commit_if_revision(db, doc["id"], expected_revision, {
        "status": "active",
        "active_design": clone_design(design),
        "active_version": version,
        "has_draft": False,
        "legacy_renderer": False,
        "updated_at": now_iso(),
        "updated_by": actor_of(user),
    })
    await _insert_version(db, doc["id"], "activate", user, design, version, "Aktifkan")
    return _public_template(await _require(db, doc["id"]), include_design=True, default_ids=await _default_ids(db))


async def copy_template(db, template_id: str, user: dict, name: str = "") -> dict:
    source = await _require(db, template_id)
    stamp = now_iso()
    design = clone_design(source.get("design") or builtin_design(source["document_type"]))
    doc = {
        "id": str(uuid.uuid4()),
        "name": (name or f"Salinan {source.get('name')}")[:120],
        "document_type": source["document_type"],
        "audience": source["audience"],
        "status": "draft",
        "is_default": False,
        "is_builtin": False,
        "legacy_renderer": False,
        "has_draft": True,
        "design": design,
        "active_design": None,
        "active_version": 0,
        "revision": 1,
        "created_at": stamp,
        "updated_at": stamp,
        "created_by": actor_of(user),
        "updated_by": actor_of(user),
    }
    await db[TEMPLATES].insert_one(doc)
    await _insert_version(db, doc["id"], "copy", user, design, 0, f"Disalin dari {source.get('name')}")
    return _public_template(doc, include_design=True)


async def set_default(db, template_id: str, expected_revision: int, user: dict) -> dict:
    doc = await _require(db, template_id)
    _check_revision(doc, expected_revision)
    if doc.get("status") != "active":
        raise HTTPException(400, "Hanya template aktif yang dapat dijadikan default.")
    owned_revision = int(expected_revision) + 1
    await _commit_if_revision(db, doc["id"], expected_revision, {
        "updated_at": now_iso(),
        "updated_by": actor_of(user),
    })
    try:
        await _publish_default(db, doc, owned_revision)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, "Default cetakan tidak berubah.") from exc
    await _insert_version(db, doc["id"], "set_default", user, None, int(doc.get("active_version") or 0), "Jadikan default")
    return _public_template(await _require(db, doc["id"]), default_ids=await _default_ids(db))


async def archive_template(db, template_id: str, expected_revision: int, user: dict) -> dict:
    doc = await _require(db, template_id)
    _check_revision(doc, expected_revision)
    if doc.get("is_builtin"):
        raise HTTPException(400, "Desain bawaan tidak dapat diarsipkan.")
    if doc.get("status") != "active":
        raise HTTPException(400, "Hanya template aktif yang dapat diarsipkan.")
    if await _is_authoritative_default(db, doc):
        raise HTTPException(400, "Template default tidak dapat diarsipkan sebelum pengganti ditentukan.")
    owned_revision = int(expected_revision) + 1
    await _commit_if_revision(db, doc["id"], expected_revision, {
        "status": "archived",
        "updated_at": now_iso(),
        "updated_by": actor_of(user),
    })
    if await _is_authoritative_default(db, doc):
        if _before_archive_rollback is not None:
            await _before_archive_rollback(doc["id"], owned_revision)
        reverted = await db[TEMPLATES].update_one(
            {"id": doc["id"], "revision": owned_revision, "status": "archived"},
            {"$set": {"status": "active"}},
        )
        if _matched_count(reverted) != 1:
            fresh = await db[TEMPLATES].find_one({"id": doc["id"]}, {"_id": 0, "status": 1})
            if not fresh or fresh.get("status") != "active":
                await _move_pointer_off_archived(db, doc)
        raise HTTPException(409, "Template menjadi default saat diarsipkan. Arsip dibatalkan.")
    await _insert_version(db, doc["id"], "archive", user, None, int(doc.get("active_version") or 0), "Arsipkan")
    return _public_template(await _require(db, doc["id"]), default_ids=await _default_ids(db))


async def list_versions(db, template_id: str) -> List[dict]:
    await _require(db, template_id)
    rows = await db[VERSIONS].find({"template_id": str(template_id)}, {"_id": 0, "design": 0}).to_list(500)
    rows.sort(key=lambda row: row.get("at") or "")
    return rows


async def restore_version(db, template_id: str, version_id: str, expected_revision: int, user: dict) -> dict:
    doc = await _require(db, template_id)
    _check_revision(doc, expected_revision)
    if doc.get("is_builtin"):
        raise HTTPException(400, "Desain bawaan tidak dipulihkan melalui versi. Salin template untuk membuat desain baru.")
    version = await db[VERSIONS].find_one({"id": str(version_id), "template_id": doc["id"]}, {"_id": 0})
    if not version or not version.get("design"):
        raise HTTPException(404, "Versi desain tidak ditemukan.")
    design = clone_design(version["design"])
    errors = validate_design(design, doc["document_type"])
    if errors:
        raise HTTPException(400, {"message": errors[0]["message"], "errors": errors})
    try:
        rendered = await _render_with_assets(db, design, doc["document_type"], preview=True)
    except DesignRenderError as exc:
        raise HTTPException(400, {"message": str(exc), "errors": exc.errors})
    if not rendered.startswith(b"%PDF"):
        raise HTTPException(400, "Pemulihan dibatalkan karena desain tidak dapat dirender.")
    new_version = int(doc.get("active_version") or 0) + 1
    await _commit_if_revision(db, doc["id"], expected_revision, {
        "design": design,
        "active_design": clone_design(design),
        "status": "active",
        "active_version": new_version,
        "has_draft": False,
        "legacy_renderer": False,
        "updated_at": now_iso(),
        "updated_by": actor_of(user),
    })
    await _insert_version(db, doc["id"], "restore", user, design, new_version, f"Pulihkan versi {version.get('version')}")
    return _public_template(await _require(db, doc["id"]), include_design=True, default_ids=await _default_ids(db))


def _asset_ids(design: dict) -> List[str]:
    found: List[str] = []
    for band in ((design or {}).get("bands") or {}).values():
        if not isinstance(band, dict):
            continue
        for element in band.get("elements") or []:
            if not isinstance(element, dict) or element.get("type") not in ("image", "signature"):
                continue
            props = element.get("props") or {}
            if props.get("url") or props.get("path") or props.get("src"):
                raise HTTPException(400, "Gambar dari URL atau path bebas tidak diizinkan.")
            asset_id = str(props.get("asset_id") or "").strip()
            if asset_id and asset_id not in found:
                found.append(asset_id)
    return found


async def _load_assets(db, design: dict) -> Dict[str, bytes]:
    from services import storage as obj_storage

    assets: Dict[str, bytes] = {}
    missing: List[str] = []
    for asset_id in _asset_ids(design):
        stored = await db[ASSETS].find_one({"id": asset_id}, {"_id": 0})
        path = str((stored or {}).get("storage_path") or "")
        data = b""
        if path:
            try:
                data, _content_type = obj_storage.get_object(path)
            except Exception:
                data = b""
        if not data:
            missing.append(asset_id)
            continue
        assets[asset_id] = data
    if missing:
        raise HTTPException(400, {
            "message": "Aset gambar pada desain tidak ditemukan.",
            "asset_ids": missing,
        })
    return assets


def render_for_design(design: dict, document_type: str, *, preview: bool = False, record: Optional[dict] = None, public: bool = False, tracking_token: str = "", assets: Optional[dict] = None) -> bytes:
    if document_type == DOCUMENT_DELIVERY:
        source = record or sample_delivery()
        context = build_delivery_context(source, tracking_token="" if preview else tracking_token, preview=preview)
    else:
        source = record or sample_invoice()
        context = build_invoice_context(source, public=public, tracking_token="" if preview else tracking_token, preview=preview)
    return render_design_pdf(design, context, assets=assets)


async def _render_with_assets(db, design: dict, document_type: str, **kwargs) -> bytes:
    assets = await _load_assets(db, design)
    try:
        return render_for_design(design, document_type, assets=assets, **kwargs)
    except DesignRenderError as exc:
        raise HTTPException(400, {"message": str(exc), "errors": exc.errors}) from exc


async def _is_authoritative_default(db, doc: dict) -> bool:
    pointer = await _ensure_pointer(db, doc["document_type"], doc["audience"])
    return str((pointer or {}).get("template_id") or "") == str(doc.get("id") or "")


async def _write_pointer(db, doc: dict) -> None:
    pointer_id = _pointer_id(doc["document_type"], doc["audience"])
    await db[DEFAULTS].update_one(
        {"_id": pointer_id},
        {"$set": {
            "id": pointer_id,
            "document_type": doc["document_type"],
            "audience": doc["audience"],
            "template_id": doc["id"],
            "updated_at": now_iso(),
        }},
        upsert=True,
    )


async def _fallback_template_id(db, doc: dict, previous_id: str) -> str:
    if previous_id and previous_id != str(doc.get("id") or ""):
        previous = await db[TEMPLATES].find_one({"id": previous_id}, {"_id": 0, "status": 1})
        if previous and previous.get("status") == "active":
            return previous_id
    return BUILTIN_IDS[(doc["document_type"], doc["audience"])]


async def _move_pointer_off_archived(db, doc: dict, previous_id: str = "") -> None:
    """Move the pointer only while it still names this archived template."""
    pointer_id = _pointer_id(doc["document_type"], doc["audience"])
    fallback = await _fallback_template_id(db, doc, previous_id)
    await db[DEFAULTS].update_one(
        {"_id": pointer_id, "template_id": doc["id"]},
        {"$set": {
            "id": pointer_id,
            "document_type": doc["document_type"],
            "audience": doc["audience"],
            "template_id": fallback,
            "updated_at": now_iso(),
        }},
    )


async def _publish_default(db, doc: dict, owned_revision: int) -> None:
    """Publish the pointer, then drop it if this template is no longer an active default."""
    previous = await _ensure_pointer(db, doc["document_type"], doc["audience"])
    previous_id = str((previous or {}).get("template_id") or "")
    if _before_pointer_write is not None:
        await _before_pointer_write(doc["id"])
    await _write_pointer(db, doc)
    after = await db[TEMPLATES].find_one({"id": doc["id"]}, {"_id": 0, "status": 1, "revision": 1})
    if after and after.get("status") == "active":
        return
    await _move_pointer_off_archived(db, doc, previous_id)
    raise _conflict((after or {}).get("revision") or owned_revision)


async def preview_template(db, template_id: str, design: Optional[dict]) -> bytes:
    doc = await _require(db, template_id)
    chosen = design if isinstance(design, dict) else doc.get("design")
    chosen = clone_design(chosen)
    chosen["document_type"] = doc["document_type"]
    return await _render_with_assets(db, chosen, doc["document_type"], preview=True)


async def resolve_template(db, document_type: str, audience: str = "customer", template_id: Optional[str] = None) -> Optional[dict]:
    await ensure_builtins(db)
    if template_id:
        doc = await db[TEMPLATES].find_one({"id": str(template_id)}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "Template cetakan tidak ditemukan.")
        if doc.get("document_type") != document_type:
            raise HTTPException(400, "Template tidak sesuai dengan jenis dokumen.")
        if doc.get("status") != "active":
            raise HTTPException(400, "Template draf atau arsip tidak dapat dipakai untuk cetak.")
        return doc
    return await _resolve_authoritative(db, document_type, audience)


def _legacy_invoice(invoice: dict) -> bytes:
    from services.sales_invoices import generate_sales_invoice_pdf_bytes
    return generate_sales_invoice_pdf_bytes(invoice)


def _legacy_delivery(delivery: dict) -> bytes:
    from services.sales_deliveries import generate_sales_delivery_pdf_bytes
    return generate_sales_delivery_pdf_bytes(delivery, None)


async def render_invoice_pdf(db, invoice: dict, *, audience: str = "customer", template_id: Optional[str] = None, public: bool = False, tracking_token: str = "") -> bytes:
    if public:
        template_id = None
        audience = "customer"
    template = await resolve_template(db, DOCUMENT_INVOICE, audience, template_id)
    if public and template and template.get("audience") != "customer":
        template = await resolve_template(db, DOCUMENT_INVOICE, "customer", None)
    if not template or template.get("legacy_renderer"):
        if public:
            return _legacy_invoice(invoice)
        return _legacy_invoice(invoice)
    design = template.get("active_design") or template.get("design")
    return await _render_with_assets(
        db,
        design,
        DOCUMENT_INVOICE,
        preview=False,
        record=invoice,
        public=public,
        tracking_token=tracking_token,
    )


async def render_delivery_pdf(db, delivery: dict, *, audience: str = "customer", template_id: Optional[str] = None, tracking_token: str = "") -> bytes:
    template = await resolve_template(db, DOCUMENT_DELIVERY, audience, template_id)
    if not template or template.get("legacy_renderer"):
        return _legacy_delivery(delivery)
    design = template.get("active_design") or template.get("design")
    return await _render_with_assets(
        db,
        design,
        DOCUMENT_DELIVERY,
        preview=False,
        record=delivery,
        tracking_token=tracking_token,
    )


async def render_public_invoice_pdf(db, invoice: dict, order: dict, requested_template_id: Optional[str] = None) -> bytes:
    """Live Tracking always uses the active customer default. Template id is ignored."""
    del requested_template_id
    from services.customer_tracking_invoices import customer_invoice_pdf_source

    source = customer_invoice_pdf_source(invoice, order)
    token = str((order or {}).get("tracking_token") or "").strip()
    return await render_invoice_pdf(db, source, audience="customer", template_id=None, public=True, tracking_token=token)


async def active_print_options(db, document_type: str) -> List[dict]:
    await ensure_builtins(db)
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(400, "Jenis dokumen tidak didukung.")
    rows = await db[TEMPLATES].find({"document_type": document_type, "status": "active"}, {"_id": 0}).to_list(200)
    defaults = await _default_ids(db)
    rows.sort(key=lambda row: (0 if row.get("id") in defaults else 1, row.get("audience") or "", row.get("name") or ""))
    return [
        {
            "id": row.get("id"),
            "name": row.get("name"),
            "audience": row.get("audience"),
            "audience_label": AUDIENCE_LABELS.get(row.get("audience"), ""),
            "is_default": row.get("id") in defaults,
            "is_builtin": bool(row.get("is_builtin")),
        }
        for row in rows
    ]


def cache_token_for(template: Optional[dict], document: Optional[dict]) -> str:
    return print_cache_token(template, document)


async def store_asset(db, storage, data: bytes, content_type: str, user: dict) -> dict:
    if content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(400, "Logo harus berupa file PNG atau JPEG.")
    if not data or len(data) > 1_500_000:
        raise HTTPException(400, "Ukuran gambar maksimal 1,5 MB.")
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if image.format not in ("PNG", "JPEG"):
                raise HTTPException(400, "Jenis gambar tidak didukung.")
            if width > 4000 or height > 4000:
                raise HTTPException(400, "Dimensi gambar terlalu besar.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "File gambar tidak dapat dibaca.")
    asset_id = str(uuid.uuid4())
    ext = "png" if content_type == "image/png" else "jpg"
    path = f"eternal-packaging/print-templates/assets/{asset_id}.{ext}"
    stored = storage.put_object(path, data, content_type)
    doc = {
        "id": asset_id,
        "storage_path": stored.get("path") or path,
        "content_type": content_type,
        "size": len(data),
        "width": width,
        "height": height,
        "created_at": now_iso(),
        "created_by": actor_of(user),
    }
    await db[ASSETS].insert_one(doc)
    return {"id": asset_id, "content_type": content_type, "width": width, "height": height}


async def load_asset(db, storage, asset_id: str) -> tuple:
    doc = await db[ASSETS].find_one({"id": str(asset_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Aset tidak ditemukan.")
    try:
        data, content_type = storage.get_object(doc["storage_path"])
    except Exception:
        raise HTTPException(404, "Aset tidak ditemukan.")
    return data, doc.get("content_type") or content_type


def user_can(user: dict, perms: set, *needed: str) -> bool:
    if user.get("role") == "admin" or user.get("is_super_admin"):
        return True
    return any(item in perms for item in needed)


def can_print_document(user: dict, perms: set, document_type: str) -> bool:
    if document_type == DOCUMENT_INVOICE:
        return user_can(user, perms, *INVOICE_PRINT_PERMS)
    if document_type == DOCUMENT_DELIVERY:
        return user_can(user, perms, *DELIVERY_PRINT_PERMS)
    return False
