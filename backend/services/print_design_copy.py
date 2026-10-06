"""Design Cetakan Phase 5C — copy explicit sparse overrides onto a branch."""
from __future__ import annotations

import copy
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services import storage as obj_storage
from services.print_design_history import FIELD_LABELS
from services.print_design_settings import (
    ASSET_SLOTS,
    FILE_KIND,
    SLOT_APPROVED,
    SLOT_COMPANY_LOGO,
    SLOT_PREPARED,
    _FILENAME_SAFE,
    _best_effort_delete_asset,
    _delete_branch_only_asset,
    _get_slot_ref,
    _load_branch_raw,
    _replace_branch_document,
    _sanitize_asset_ref,
    _set_slot_ref,
    _slot_max_bytes,
    _sparse_from_doc,
    _storage_key,
    assert_known_branch_id,
    canonical_branch_id,
    get_branch_design_view,
    get_print_design_internal,
    now_iso,
    public_sparse_override,
    validate_design_image,
)

logger = logging.getLogger(__name__)

MODE_REPLACE = "replace"
SCOPE_GLOBAL = "global"
SCOPE_BRANCH = "branch"
ALLOWED_SCOPES = frozenset({SCOPE_GLOBAL, SCOPE_BRANCH})
ALLOWED_PAYLOAD_KEYS = frozenset({
    "source_scope",
    "source_branch_id",
    "target_branch_id",
    "mode",
    "target_scope",
})
def field_label(key: str) -> str:
    return FIELD_LABELS.get(key) or key


def copied_field_items(keys: List[str]) -> List[Dict[str, str]]:
    items = []
    for key in keys:
        label = field_label(key)
        if label:
            items.append({"key": key, "label": label})
    return items


def sparse_copyable_keys(sparse: Optional[dict]) -> List[str]:
    src = sparse if isinstance(sparse, dict) else {}
    inv = src.get("invoice") if isinstance(src.get("invoice"), dict) else {}
    keys: List[str] = []
    if "company_name" in src:
        keys.append("company_name")
    if "address_lines" in src:
        keys.append("address_lines")
    if "company_logo" in src:
        keys.append("company_logo")
    if "bank_accounts" in inv:
        keys.append("bank_accounts")
    for field in ("prepared_label", "prepared_footer", "approved_label", "approved_footer"):
        if field in inv:
            keys.append(field)
    for field in (SLOT_PREPARED, SLOT_APPROVED):
        if field in inv:
            keys.append(field)
    return keys


def _text_sparse(sparse: dict) -> dict:
    out: Dict[str, Any] = {}
    if "company_name" in sparse:
        out["company_name"] = sparse["company_name"]
    if "address_lines" in sparse:
        out["address_lines"] = list(sparse["address_lines"])
    inv = sparse.get("invoice") if isinstance(sparse.get("invoice"), dict) else {}
    inv_out: Dict[str, Any] = {}
    if "bank_accounts" in inv:
        inv_out["bank_accounts"] = copy.deepcopy(inv["bank_accounts"])
    for field in ("prepared_label", "prepared_footer", "approved_label", "approved_footer"):
        if field in inv:
            inv_out[field] = inv[field]
    if inv_out:
        out["invoice"] = inv_out
    return out


def _source_asset_slots(sparse: dict) -> List[str]:
    slots: List[str] = []
    if _sanitize_asset_ref(sparse.get("company_logo")):
        slots.append(SLOT_COMPANY_LOGO)
    inv = sparse.get("invoice") if isinstance(sparse.get("invoice"), dict) else {}
    if _sanitize_asset_ref(inv.get(SLOT_PREPARED)):
        slots.append(SLOT_PREPARED)
    if _sanitize_asset_ref(inv.get(SLOT_APPROVED)):
        slots.append(SLOT_APPROVED)
    return slots


def target_owned_storage_path(path: str, branch_id: str) -> bool:
    safe_branch = _FILENAME_SAFE.sub("_", str(branch_id or "")) or "branch"
    prefix = f"{obj_storage.APP_NAME}/print-design/branch-{safe_branch}/"
    raw = str(path or "").strip()
    return bool(raw) and raw.startswith(prefix) and ".." not in raw and not raw.startswith("/")


def _clip_payload(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise HTTPException(400, "Payload salin tidak valid")
    extra = set(raw) - ALLOWED_PAYLOAD_KEYS
    if extra:
        raise HTTPException(400, "Payload salin tidak valid")
    mode = str(raw.get("mode") or MODE_REPLACE).strip().lower() or MODE_REPLACE
    if mode != MODE_REPLACE:
        raise HTTPException(400, "mode tidak didukung")
    target_scope = raw.get("target_scope")
    if target_scope not in (None, "", SCOPE_BRANCH):
        raise HTTPException(400, "Salin ke Global tidak didukung")
    scope = str(raw.get("source_scope") or "").strip().lower()
    if scope not in ALLOWED_SCOPES:
        raise HTTPException(400, "source_scope tidak valid")
    return raw


async def _cleanup_created(db, created: List[dict]) -> None:
    for ref in created:
        try:
            await _best_effort_delete_asset(db, ref)
        except Exception:
            logger.warning("copy cleanup of new print_design object failed", exc_info=True)


async def _restore_previous_target(db, target_id: str, previous: Optional[dict]) -> None:
    """Put the previous branch doc back if replace deleted it and then failed to insert."""
    prior = previous if isinstance(previous, dict) else {}
    if not prior.get("key"):
        return
    current = await _load_branch_raw(db, target_id)
    if current:
        return
    try:
        await db.app_settings.insert_one(copy.deepcopy(prior))
    except Exception:
        logger.warning("copy failed to restore previous target branch doc id=%s", target_id, exc_info=True)


async def _duplicate_asset(db, *, source_ref: dict, slot: str, target_branch_id: str, user: Optional[dict]) -> dict:
    if slot not in ASSET_SLOTS:
        raise HTTPException(400, "Slot asset tidak valid.")
    clean = _sanitize_asset_ref(source_ref)
    if not clean:
        raise HTTPException(400, "Asset sumber Design Cetakan tidak valid.")
    try:
        data, ct = obj_storage.get_object(clean["storage_path"])
    except FileNotFoundError as exc:
        raise HTTPException(502, "Gagal membaca asset sumber Design Cetakan.") from exc
    except Exception as exc:
        logger.exception("print_design copy get_object failed slot=%s", slot)
        raise HTTPException(502, "Gagal membaca asset sumber Design Cetakan.") from exc
    media = validate_design_image(
        data,
        clean.get("content_type") or ct,
        max_bytes=_slot_max_bytes(slot),
    )
    file_id = str(uuid.uuid4())
    ext = obj_storage.ext_for(media, "bin")
    path = _storage_key(slot, file_id, ext, branch_id=target_branch_id)
    stamp = now_iso()
    safe_name = _FILENAME_SAFE.sub("_", str(clean.get("original_filename") or f"{slot}.{ext}")) or f"{slot}.{ext}"
    try:
        stored = obj_storage.put_object(path, data, media)
    except Exception as exc:
        logger.exception("print_design copy put_object failed slot=%s branch=%s", slot, target_branch_id)
        raise HTTPException(502, "Gagal menyimpan asset Design Cetakan.") from exc
    storage_path = stored.get("path") or path
    new_ref = {
        "file_id": file_id,
        "storage_path": storage_path,
        "content_type": media,
        "size": stored.get("size") or len(data),
        "original_filename": safe_name[:180],
        "updated_at": stamp,
    }
    try:
        await db.files.insert_one({
            "id": file_id,
            "storage_path": storage_path,
            "original_filename": safe_name[:180],
            "content_type": media,
            "size": new_ref["size"],
            "kind": FILE_KIND,
            "slot": slot,
            "branch_id": target_branch_id,
            "uploaded_at": stamp,
            "uploaded_by": (user or {}).get("email") or (user or {}).get("name"),
            "uploaded_by_name": (user or {}).get("name"),
            "uploaded_by_role": (user or {}).get("role"),
            "is_deleted": False,
        })
    except Exception:
        logger.warning("print_design copy files metadata insert failed id=%s", file_id, exc_info=True)
    return new_ref


async def _cleanup_old_target_assets(
    db,
    *,
    old_refs: Dict[str, Optional[dict]],
    new_paths: set,
    target_branch_id: str,
    global_design: dict,
) -> None:
    for slot, ref in old_refs.items():
        clean = _sanitize_asset_ref(ref)
        if not clean:
            continue
        path = str(clean.get("storage_path") or "")
        if path in new_paths:
            continue
        if not target_owned_storage_path(path, target_branch_id):
            continue
        try:
            await _delete_branch_only_asset(db, clean, global_design, slot)
        except Exception:
            logger.warning(
                "copy old target asset cleanup failed slot=%s branch=%s",
                slot,
                target_branch_id,
                exc_info=True,
            )


async def copy_print_design(db, payload: Any, user: Optional[dict] = None) -> Dict[str, Any]:
    """Replace target branch sparse override with source copyable sparse fields."""
    src_payload = _clip_payload(payload)
    source_scope = str(src_payload.get("source_scope") or "").strip().lower()
    target_id = await assert_known_branch_id(db, src_payload.get("target_branch_id"))

    source_branch_id = None
    raw_source_bid = src_payload.get("source_branch_id")
    if source_scope == SCOPE_GLOBAL:
        if raw_source_bid not in (None, ""):
            raise HTTPException(400, "source_branch_id tidak valid untuk sumber Global")
        source_sparse = _sparse_from_doc(await get_print_design_internal(db))
    else:
        source_branch_id = await assert_known_branch_id(db, raw_source_bid)
        if source_branch_id == target_id:
            raise HTTPException(400, "Sumber dan tujuan tidak boleh sama")
        source_sparse = _sparse_from_doc(await _load_branch_raw(db, source_branch_id))

    copied_keys = sparse_copyable_keys(source_sparse)
    if not copied_keys:
        raise HTTPException(400, "Sumber tidak punya pengaturan khusus")

    target_raw = await _load_branch_raw(db, target_id)
    old_refs = {
        SLOT_COMPANY_LOGO: _get_slot_ref(target_raw, SLOT_COMPANY_LOGO),
        SLOT_PREPARED: _get_slot_ref(target_raw, SLOT_PREPARED),
        SLOT_APPROVED: _get_slot_ref(target_raw, SLOT_APPROVED),
    }
    before_override = public_sparse_override(target_raw)
    next_sparse = _text_sparse(source_sparse)
    created: List[dict] = []
    try:
        for slot in _source_asset_slots(source_sparse):
            source_ref = _get_slot_ref(source_sparse, slot)
            new_ref = await _duplicate_asset(
                db,
                source_ref=source_ref,
                slot=slot,
                target_branch_id=target_id,
                user=user,
            )
            created.append(new_ref)
            next_sparse = _set_slot_ref(next_sparse, slot, new_ref)
        next_sparse = _sparse_from_doc(next_sparse)
        await _replace_branch_document(db, target_id, next_sparse, user)
    except Exception:
        await _restore_previous_target(db, target_id, target_raw)
        await _cleanup_created(db, created)
        raise

    global_design = await get_print_design_internal(db)
    new_paths = {str(ref.get("storage_path") or "") for ref in created}
    await _cleanup_old_target_assets(
        db,
        old_refs=old_refs,
        new_paths=new_paths,
        target_branch_id=target_id,
        global_design=global_design,
    )
    view = await get_branch_design_view(db, target_id)
    return {
        "copied": True,
        "copied_fields": copied_field_items(copied_keys),
        "copied_field_keys": copied_keys,
        "source_scope": source_scope,
        "source_branch_id": source_branch_id,
        "target_branch_id": target_id,
        "mode": MODE_REPLACE,
        "before_override": before_override,
        "branch": view,
        "message": "Pengaturan khusus Design Cetakan berhasil disalin",
    }
