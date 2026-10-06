"""Design Cetakan Phase 5C — copy explicit sparse overrides onto a branch."""
from __future__ import annotations

import copy
import io
import os
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_NAME", "eternal_packaging_design_cetakan_copy_test")

from services import storage as obj_storage  # noqa: E402
from services.print_design_copy import copy_print_design, target_owned_storage_path  # noqa: E402
from services.print_design_history import ACTION_BRANCH_COPY  # noqa: E402
from services.print_design_settings import (  # noqa: E402
    SETTINGS_KEY,
    SLOT_APPROVED,
    SLOT_COMPANY_LOGO,
    SLOT_PREPARED,
    _load_branch_raw,
    get_branch_design_view,
    get_print_design_internal,
    reset_branch_design_settings,
    save_branch_design_settings,
    save_print_design_settings,
    upload_branch_print_design_asset,
    upload_print_design_asset,
)

USER = {"id": "u1", "email": "owner@eternalpackaging.com", "name": "Owner", "role": "admin"}


def _png(color=(200, 20, 20)) -> bytes:
    from PIL import Image as PILImage

    im = PILImage.new("RGB", (32, 16), color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _match(doc, query):
    for key, expected in (query or {}).items():
        if isinstance(expected, dict):
            if "$ne" in expected and doc.get(key) == expected["$ne"]:
                return False
            elif "$ne" not in expected and doc.get(key) != expected:
                return False
        elif doc.get(key) != expected:
            return False
    return True


class MockCollection:
    def __init__(self, initial=None):
        self._docs = copy.deepcopy(initial or [])

    async def find_one(self, query=None, projection=None):
        for doc in self._docs:
            if _match(doc, query):
                res = copy.deepcopy(doc)
                if projection and projection.get("_id") == 0 and "_id" in res:
                    res.pop("_id", None)
                return res
        return None

    async def update_one(self, query, update, upsert=False):
        set_vals = update.get("$set", {})
        for idx, doc in enumerate(self._docs):
            if _match(doc, query):
                self._docs[idx].update(copy.deepcopy(set_vals))
                return
        if upsert:
            new_doc = {k: v for k, v in (query or {}).items() if not str(k).startswith("$")}
            new_doc.update(copy.deepcopy(set_vals))
            self._docs.append(new_doc)

    async def insert_one(self, doc):
        self._docs.append(copy.deepcopy(doc))

    async def delete_one(self, query):
        for idx, doc in enumerate(self._docs):
            if _match(doc, query):
                self._docs.pop(idx)
                return

    async def distinct(self, field):
        return [doc.get(field) for doc in self._docs]


class MockDb:
    def __init__(self, orders=None, app_settings=None):
        self.orders = MockCollection(orders)
        self.app_settings = MockCollection(app_settings)
        self.files = MockCollection()


def _orders():
    return [
        {"id": "o1", "branch_id": "50"},
        {"id": "o2", "branch_id": "77"},
    ]


@pytest.fixture(autouse=True)
def _isolated_local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "objstore"))
    obj_storage._reset_provider_for_tests()
    yield
    obj_storage._reset_provider_for_tests()


async def _seed_global(db, *, logo=True, prepared=False, approved=False):
    await save_print_design_settings(
        db,
        {
            "company_name": "Eternal Global",
            "address_lines": ["Alamat Global"],
            "accent_color": "#112233",
            "invoice": {
                "bank_accounts": [{"bank_label": "BRI", "account_number": "1", "account_name": "Global"}],
                "estimasi_title": "Estimasi Custom",
                "estimasi_lines": ["Line A"],
                "prepared_label": "Disiapkan Global",
                "prepared_footer": "Admin Global",
                "approved_label": "Disetujui Global",
                "approved_footer": "Pengelola Global",
            },
            "po": {"signature_labels": ["A", "B", "C"], "footer_name_label": "Nama"},
            "surat_jalan": {"signature_labels": ["G", "Cek", "Kurir", "Penerima"], "date_label": "Tanggal"},
        },
        user=USER,
    )
    if logo:
        await upload_print_design_asset(
            db, slot=SLOT_COMPANY_LOGO, body=_png((180, 20, 20)), content_type="image/png",
            filename="global-logo.png", user=USER,
        )
    if prepared:
        await upload_print_design_asset(
            db, slot=SLOT_PREPARED, body=_png((20, 180, 20)), content_type="image/png",
            filename="global-prepared.png", user=USER,
        )
    if approved:
        await upload_print_design_asset(
            db, slot=SLOT_APPROVED, body=_png((20, 20, 180)), content_type="image/png",
            filename="global-approved.png", user=USER,
        )


def _slot_path(doc, slot):
    if slot == SLOT_COMPANY_LOGO:
        ref = (doc or {}).get("company_logo") or {}
    else:
        ref = ((doc or {}).get("invoice") or {}).get(slot) or {}
    return str(ref.get("storage_path") or "")


@pytest.mark.anyio
async def test_global_to_branch_freeze_allowlist_and_assets():
    db = MockDb(_orders())
    await _seed_global(db, logo=True, prepared=True, approved=True)
    global_before = await get_print_design_internal(db)
    global_logo = _slot_path(global_before, SLOT_COMPANY_LOGO)
    result = await copy_print_design(
        db,
        {"source_scope": "global", "target_branch_id": "50"},
        user=USER,
    )
    assert result["copied"] is True
    assert result["mode"] == "replace"
    assert result["source_scope"] == "global"
    assert result["source_branch_id"] is None
    keys = result["copied_field_keys"]
    assert "company_name" in keys
    assert "bank_accounts" in keys
    assert "company_logo" in keys
    assert "prepared_signature_image" in keys
    assert "approved_signature_image" in keys
    assert "accent_color" not in keys
    raw = await _load_branch_raw(db, "50")
    assert raw.get("company_name") == "Eternal Global"
    assert "accent_color" not in raw
    assert "po" not in raw
    assert "surat_jalan" not in raw
    assert (raw.get("invoice") or {}).get("estimasi_title") is None
    view = result["branch"]
    assert view["inherited"]["company_name"]["overridden"] is True
    assert view["effective"]["accent_color"] == "#112233"
    target_logo = _slot_path(raw, SLOT_COMPANY_LOGO)
    assert target_logo
    assert target_logo != global_logo
    assert target_owned_storage_path(target_logo, "50")
    assert obj_storage.get_object(global_logo)[0]
    assert obj_storage.get_object(target_logo)[0]

    await save_print_design_settings(db, {"company_name": "Eternal Baru", "accent_color": "#abcdef"}, user=USER)
    after_global = await get_branch_design_view(db, "50")
    assert after_global["effective"]["company_name"] == "Eternal Global"
    assert after_global["effective"]["accent_color"] == "#abcdef"

    no_logo_db = MockDb(_orders())
    await _seed_global(no_logo_db, logo=False)
    copied = await copy_print_design(no_logo_db, {"source_scope": "global", "target_branch_id": "77"}, user=USER)
    raw77 = await _load_branch_raw(no_logo_db, "77")
    assert "company_logo" not in raw77
    assert copied["branch"]["inherited"]["company_logo"]["inherited"] is True


@pytest.mark.anyio
async def test_branch_to_branch_replace_sparse_and_duplicate_assets():
    db = MockDb(_orders())
    await _seed_global(db, logo=True)
    await save_branch_design_settings(db, "50", {"company_name": "Eternal Jogja"}, user=USER)
    await upload_branch_print_design_asset(
        db, branch_id="50", slot=SLOT_PREPARED, body=_png((10, 200, 10)),
        content_type="image/png", filename="jogja-prepared.png", user=USER,
    )
    await save_branch_design_settings(
        db, "77",
        {"invoice": {"bank_accounts": [{"bank_label": "BCA", "account_number": "9", "account_name": "Tujuan"}]}},
        user=USER,
    )
    await upload_branch_print_design_asset(
        db, branch_id="77", slot=SLOT_COMPANY_LOGO, body=_png((10, 10, 200)),
        content_type="image/png", filename="tujuan-logo.png", user=USER,
    )
    source_raw = await _load_branch_raw(db, "50")
    source_sig = _slot_path(source_raw, SLOT_PREPARED)
    old_target = await _load_branch_raw(db, "77")
    old_logo = _slot_path(old_target, SLOT_COMPANY_LOGO)
    assert old_logo

    result = await copy_print_design(
        db,
        {"source_scope": "branch", "source_branch_id": "50", "target_branch_id": "77"},
        user=USER,
    )
    keys = set(result["copied_field_keys"])
    assert keys == {"company_name", "prepared_signature_image"}
    target = await _load_branch_raw(db, "77")
    assert target.get("company_name") == "Eternal Jogja"
    assert not (target.get("invoice") or {}).get("bank_accounts")
    assert "company_logo" not in target
    new_sig = _slot_path(target, SLOT_PREPARED)
    assert new_sig and new_sig != source_sig
    assert target_owned_storage_path(new_sig, "77")
    assert obj_storage.get_object(source_sig)[0]
    assert obj_storage.get_object(new_sig)[0]
    with pytest.raises(FileNotFoundError):
        obj_storage.get_object(old_logo)
    source_after = await _load_branch_raw(db, "50")
    assert source_after.get("company_name") == "Eternal Jogja"
    assert _slot_path(source_after, SLOT_PREPARED) == source_sig
    global_after = await get_print_design_internal(db)
    assert global_after.get("company_name") == "Eternal Global"
    assert "accent_color" not in target
    view = result["branch"]
    assert view["inherited"]["bank_accounts"]["inherited"] is True
    assert view["inherited"]["company_name"]["overridden"] is True

    await reset_branch_design_settings(db, "77", user=USER)
    with pytest.raises(FileNotFoundError):
        obj_storage.get_object(new_sig)
    assert obj_storage.get_object(source_sig)[0]
    assert obj_storage.get_object(_slot_path(global_after, SLOT_COMPANY_LOGO))[0]
    reset_view = await get_branch_design_view(db, "77")
    assert reset_view["inherited"]["company_name"]["inherited"] is True
    source_reset = await _load_branch_raw(db, "50")
    assert _slot_path(source_reset, SLOT_PREPARED) == source_sig


@pytest.mark.anyio
async def test_copy_validation_and_forbidden_payload():
    db = MockDb(_orders())
    await _seed_global(db, logo=False)

    with pytest.raises(HTTPException) as nondigit_src:
        await copy_print_design(db, {"source_scope": "branch", "source_branch_id": "50a", "target_branch_id": "77"})
    assert nondigit_src.value.status_code == 400
    with pytest.raises(HTTPException) as unknown_src:
        await copy_print_design(db, {"source_scope": "branch", "source_branch_id": "99", "target_branch_id": "77"})
    assert unknown_src.value.status_code == 400
    with pytest.raises(HTTPException) as nondigit_tgt:
        await copy_print_design(db, {"source_scope": "global", "target_branch_id": "77a"})
    assert nondigit_tgt.value.status_code == 400
    with pytest.raises(HTTPException) as unknown_tgt:
        await copy_print_design(db, {"source_scope": "global", "target_branch_id": "99"})
    assert unknown_tgt.value.status_code == 400
    with pytest.raises(HTTPException) as same:
        await copy_print_design(db, {"source_scope": "branch", "source_branch_id": "50", "target_branch_id": "50"})
    assert same.value.status_code == 400
    with pytest.raises(HTTPException) as empty:
        await copy_print_design(db, {"source_scope": "branch", "source_branch_id": "50", "target_branch_id": "77"})
    assert empty.value.status_code == 400
    assert "pengaturan khusus" in empty.value.detail
    with pytest.raises(HTTPException) as to_global:
        await copy_print_design(db, {"source_scope": "branch", "source_branch_id": "50", "target_branch_id": "77", "target_scope": "global"})
    assert to_global.value.status_code == 400
    with pytest.raises(HTTPException) as bag:
        await copy_print_design(db, {
            "source_scope": "global",
            "target_branch_id": "77",
            "settings": {"company_name": "Hacked"},
            "override": {"accent_color": "#000"},
        })
    assert bag.value.status_code == 400
    with pytest.raises(HTTPException) as mode:
        await copy_print_design(db, {"source_scope": "global", "target_branch_id": "77", "mode": "merge"})
    assert mode.value.status_code == 400
    with pytest.raises(HTTPException) as global_src_bid:
        await copy_print_design(db, {"source_scope": "global", "source_branch_id": "50", "target_branch_id": "77"})
    assert global_src_bid.value.status_code == 400


@pytest.mark.anyio
async def test_asset_failure_compensation_and_old_target_kept(monkeypatch):
    db = MockDb(_orders())
    await _seed_global(db, logo=True, prepared=True)
    await save_branch_design_settings(db, "77", {"company_name": "Keep Me"}, user=USER)
    await upload_branch_print_design_asset(
        db, branch_id="77", slot=SLOT_COMPANY_LOGO, body=_png((9, 9, 9)),
        content_type="image/png", filename="keep.png", user=USER,
    )
    old_target = await _load_branch_raw(db, "77")
    old_logo = _slot_path(old_target, SLOT_COMPANY_LOGO)
    created_paths = []
    real_put = obj_storage.put_object

    def tracking_put(path, data, content_type):
        created_paths.append(path)
        if len(created_paths) >= 2:
            raise RuntimeError("second put failed")
        return real_put(path, data, content_type)

    monkeypatch.setattr(obj_storage, "put_object", tracking_put)
    with pytest.raises(HTTPException) as failed:
        await copy_print_design(db, {"source_scope": "global", "target_branch_id": "77"}, user=USER)
    assert failed.value.status_code == 502
    kept = await _load_branch_raw(db, "77")
    assert kept.get("company_name") == "Keep Me"
    assert _slot_path(kept, SLOT_COMPANY_LOGO) == old_logo
    assert obj_storage.get_object(old_logo)[0]
    for path in created_paths:
        with pytest.raises(FileNotFoundError):
            obj_storage.get_object(path)

    def missing_get(_path):
        raise FileNotFoundError("gone")

    monkeypatch.setattr(obj_storage, "get_object", missing_get)
    with pytest.raises(HTTPException):
        await copy_print_design(db, {"source_scope": "global", "target_branch_id": "77"}, user=USER)
    still = await _load_branch_raw(db, "77")
    assert still.get("company_name") == "Keep Me"


@pytest.mark.anyio
async def test_settings_replace_failure_cleans_new_keeps_old(monkeypatch):
    db = MockDb(_orders())
    await _seed_global(db, logo=True)
    await save_branch_design_settings(db, "50", {"company_name": "Old Target"}, user=USER)
    old = await _load_branch_raw(db, "50")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("replace failed")

    created = []
    real_dup = None
    from services import print_design_copy as copy_mod

    real_dup = copy_mod._duplicate_asset

    async def wrapping_dup(*args, **kwargs):
        ref = await real_dup(*args, **kwargs)
        created.append(ref)
        return ref

    monkeypatch.setattr(copy_mod, "_duplicate_asset", wrapping_dup)
    monkeypatch.setattr(copy_mod, "_replace_branch_document", boom)
    with pytest.raises(RuntimeError):
        await copy_print_design(db, {"source_scope": "global", "target_branch_id": "50"}, user=USER)
    assert await _load_branch_raw(db, "50") == old
    assert created
    for ref in created:
        with pytest.raises(FileNotFoundError):
            obj_storage.get_object(ref["storage_path"])


@pytest.mark.anyio
async def test_cleanup_failure_does_not_undo_copy_or_delete_source(monkeypatch):
    db = MockDb(_orders())
    await _seed_global(db, logo=True)
    global_logo = _slot_path(await get_print_design_internal(db), SLOT_COMPANY_LOGO)
    await save_branch_design_settings(db, "50", {"company_name": "Jogja"}, user=USER)
    await upload_branch_print_design_asset(
        db, branch_id="50", slot=SLOT_COMPANY_LOGO, body=_png((1, 2, 3)),
        content_type="image/png", filename="src.png", user=USER,
    )
    source_logo = _slot_path(await _load_branch_raw(db, "50"), SLOT_COMPANY_LOGO)

    async def noisy_delete(*_a, **_k):
        raise RuntimeError("cleanup noise")

    from services import print_design_copy as copy_mod
    monkeypatch.setattr(copy_mod, "_delete_branch_only_asset", noisy_delete)
    result = await copy_print_design(
        db,
        {"source_scope": "branch", "source_branch_id": "50", "target_branch_id": "77"},
        user=USER,
    )
    assert result["copied"] is True
    target = await _load_branch_raw(db, "77")
    assert target.get("company_name") == "Jogja"
    assert obj_storage.get_object(source_logo)[0]
    assert obj_storage.get_object(global_logo)[0]
    blob = str(result)
    assert "storage_path" not in blob
    assert "file_id" not in blob

    again = await copy_print_design(
        db,
        {"source_scope": "branch", "source_branch_id": "50", "target_branch_id": "77"},
        user=USER,
    )
    assert again["copied"] is True
    assert (await _load_branch_raw(db, "50")).get("company_name") == "Jogja"


@pytest.mark.anyio
async def test_replace_insert_failure_restores_previous_target_settings():
    db = MockDb(_orders())
    await _seed_global(db, logo=True)
    await save_branch_design_settings(db, "50", {"company_name": "Old Target"}, user=USER)
    old = await _load_branch_raw(db, "50")
    real_insert = db.app_settings.insert_one
    state = {"failed": False}

    async def fail_first_settings_insert(doc):
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("insert failed")
        return await real_insert(doc)

    db.app_settings.insert_one = fail_first_settings_insert
    with pytest.raises(RuntimeError):
        await copy_print_design(db, {"source_scope": "global", "target_branch_id": "50"}, user=USER)
    restored = await _load_branch_raw(db, "50")
    assert restored.get("company_name") == "Old Target"
    assert restored.get("key") == old.get("key")
    assert "accent_color" not in restored


@pytest.mark.anyio
async def test_asset_validation_failure_leaves_target(monkeypatch):
    db = MockDb(_orders())
    await _seed_global(db, logo=True)
    await save_branch_design_settings(db, "77", {"company_name": "Stay"}, user=USER)

    def junk_get(_path):
        return b"not-an-image-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "image/png"

    monkeypatch.setattr(obj_storage, "get_object", junk_get)
    with pytest.raises(HTTPException) as bad:
        await copy_print_design(db, {"source_scope": "global", "target_branch_id": "77"}, user=USER)
    assert bad.value.status_code == 400
    assert (await _load_branch_raw(db, "77")).get("company_name") == "Stay"


def test_copy_route_permission_source():
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    idx = server.index("async def design_cetakan_copy")
    block = server[idx:server.find("\nasync def ", idx + 10)]
    assert "settings.design_cetakan.manage" in block
    assert "_design_cetakan_view_perms()" not in block
    assert "audit.view" not in block
    assert block.count("_write_audit") == 1
    assert ACTION_BRANCH_COPY in block
    assert "copy_print_design" in block
    preview = server[server.index("async def design_cetakan_preview"):server.index("async def design_cetakan_preview") + 500]
    assert "_write_audit" not in preview
