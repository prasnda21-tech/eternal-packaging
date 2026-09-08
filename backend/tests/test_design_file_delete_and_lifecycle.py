"""Targeted tests for Design file deletion and Approval Design link lifecycle."""
import base64
import copy
import logging
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("STORAGE_PROVIDER", "local")


import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


from customer_approval import (

    bound_design_file,
    build_router,
    current_design_pdf_file,
    is_pdf_design,
)
import server

TINY_PDF = b"%PDF-1.4 mock pdf data"
TINY_PDF_B64 = "data:application/pdf;base64," + base64.b64encode(TINY_PDF).decode("ascii")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_file(file_id, name="design.pdf", is_pdf=True, storage_path=None):
    ct = "application/pdf" if is_pdf else "image/png"
    data = TINY_PDF_B64 if is_pdf else "data:image/png;base64,iVBORw0KGgo="
    f = {
        "id": file_id,
        "name": name,
        "content_type": ct,
        "data_url": data,
        "uploaded_by": "designer@example.com",
        "uploaded_by_role": "designer",
        "uploaded_by_name": "Designer One",
        "uploaded_at": "2026-09-08T10:00:00+00:00",
    }
    if storage_path:
        f["storage_path"] = storage_path
    return f


@pytest.mark.anyio
async def test_delete_design_file_authorized_exact():
    """1, 6, 7, 8, 9, 10: Authorized internal user deletes exact file, preserving order & history."""
    f1 = _make_file("file-1", "rev1.pdf")
    f2 = _make_file("file-2", "rev2.pdf")
    mock_order = {
        "id": "po-123",
        "po_number": "PO-123",
        "cancelled": False,
        "design_status": "Waiting",
        "design_files": [f1, f2],
        "timeline": [],
    }

    mock_user = {"email": "designer@example.com", "name": "Designer", "role": "designer"}
    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(mock_order))

    with patch.object(server, "db", mock_db), \
         patch.object(server, "_require_perm", AsyncMock()) as mock_perm, \
         patch.object(server, "_persist_order", AsyncMock()) as mock_persist, \
         patch.object(server, "_audit_po", AsyncMock()) as mock_audit, \
         patch.object(server._ca_router, "_invalidate_on_design_file_delete", AsyncMock()) as mock_ca_inval:

        res = await server.delete_design_file("po-123", "file-1", user=mock_user)

        mock_perm.assert_awaited_once_with(mock_user, "design.upload")
        updated_files = res["order"]["design_files"]
        assert len(updated_files) == 1
        assert updated_files[0]["id"] == "file-2"
        mock_persist.assert_awaited_once()
        mock_audit.assert_awaited_once()
        mock_ca_inval.assert_awaited_once()


@pytest.mark.anyio
async def test_delete_design_file_unauthorized():
    """2: Unauthorized user without design.upload is rejected."""
    mock_user = {"email": "operator@example.com", "name": "Operator", "role": "operator"}
    with patch.object(server, "_require_perm", AsyncMock(side_effect=HTTPException(403, "Permission denied"))):
        with pytest.raises(HTTPException) as exc:
            await server.delete_design_file("po-123", "file-1", user=mock_user)
        assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_delete_design_file_nonexistent_and_cross_order():
    """3, 4: Nonexistent file or file belonging to another order rejected 404."""
    f1 = _make_file("file-1", "order1.pdf")
    mock_order = {
        "id": "po-1",
        "po_number": "PO-1",
        "cancelled": False,
        "design_files": [f1],
    }
    mock_user = {"email": "designer@example.com", "role": "designer"}
    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(mock_order))

    with patch.object(server, "db", mock_db), \
         patch.object(server, "_require_perm", AsyncMock()):

        # Nonexistent file
        with pytest.raises(HTTPException) as exc:
            await server.delete_design_file("po-1", "file-999", user=mock_user)
        assert exc.value.status_code == 404

        # Cross-order: file-2 exists on another order, not on po-1
        with pytest.raises(HTTPException) as exc2:
            await server.delete_design_file("po-1", "file-2", user=mock_user)
        assert exc2.value.status_code == 404


@pytest.mark.anyio
async def test_delete_same_filename_different_id():
    """5: Same filename, different IDs remain completely isolated."""
    f1 = _make_file("file-A", "design.pdf")
    f2 = _make_file("file-B", "design.pdf")
    mock_order = {
        "id": "po-iso",
        "po_number": "PO-ISO",
        "cancelled": False,
        "design_files": [f1, f2],
        "timeline": [],
    }
    mock_user = {"email": "designer@example.com", "role": "designer"}
    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(mock_order))

    with patch.object(server, "db", mock_db), \
         patch.object(server, "_require_perm", AsyncMock()), \
         patch.object(server, "_persist_order", AsyncMock()), \
         patch.object(server, "_audit_po", AsyncMock()), \
         patch.object(server._ca_router, "_invalidate_on_design_file_delete", AsyncMock()):

        res = await server.delete_design_file("po-iso", "file-A", user=mock_user)
        kept = res["order"]["design_files"]
        assert len(kept) == 1
        assert kept[0]["id"] == "file-B"
        assert kept[0]["name"] == "design.pdf"


@pytest.mark.anyio
async def test_exact_storage_delete_and_no_wildcards():
    """11, 12: Exact storage delete invoked only for exact path, no wildcards."""
    f1 = _make_file("file-s1", "design.pdf", storage_path="eternal-packaging/designs/po-1/file-s1.pdf")
    mock_order = {
        "id": "po-1",
        "po_number": "PO-1",
        "cancelled": False,
        "design_files": [f1],
        "timeline": [],
    }
    mock_user = {"email": "designer@example.com", "role": "designer"}
    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(mock_order))
    mock_db.files.update_one = AsyncMock()

    with patch.object(server, "db", mock_db), \
         patch.object(server, "_require_perm", AsyncMock()), \
         patch.object(server, "_persist_order", AsyncMock()), \
         patch.object(server, "_audit_po", AsyncMock()), \
         patch.object(server.obj_storage, "delete_object", MagicMock(return_value=True)) as mock_del_obj, \
         patch.object(server._ca_router, "_invalidate_on_design_file_delete", AsyncMock()):

        await server.delete_design_file("po-1", "file-s1", user=mock_user)
        mock_del_obj.assert_called_once_with("eternal-packaging/designs/po-1/file-s1.pdf")
        # Ensure no wildcards in path
        called_path = mock_del_obj.call_args[0][0]
        assert "*" not in called_path
        mock_db.files.update_one.assert_awaited_once()


@pytest.mark.anyio
async def test_storage_delete_failure_aborts_and_preserves_metadata():
    """Storage deletion failure aborts before modifying metadata; order & files remain intact."""
    f1 = _make_file("file-s1", "design.pdf", storage_path="eternal-packaging/designs/po-1/file-s1.pdf")
    mock_order = {
        "id": "po-1",
        "po_number": "PO-1",
        "cancelled": False,
        "design_files": [f1],
        "timeline": [],
    }
    mock_user = {"email": "designer@example.com", "role": "designer"}
    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(mock_order))
    mock_db.files.update_one = AsyncMock()

    with patch.object(server, "db", mock_db), \
         patch.object(server, "_require_perm", AsyncMock()), \
         patch.object(server, "_persist_order", AsyncMock()) as mock_persist, \
         patch.object(server, "_audit_po", AsyncMock()) as mock_audit, \
         patch.object(server.obj_storage, "delete_object", MagicMock(side_effect=RuntimeError("Storage disk offline"))) as mock_del_obj, \
         patch.object(server._ca_router, "_invalidate_on_design_file_delete", AsyncMock()) as mock_ca_inval:

        with pytest.raises(HTTPException) as exc:
            await server.delete_design_file("po-1", "file-s1", user=mock_user)
        assert exc.value.status_code == 500
        assert "Gagal menghapus file design dari storage" in exc.value.detail

        # Verify nothing was mutated or persisted
        mock_del_obj.assert_called_once()
        mock_persist.assert_not_called()
        mock_audit.assert_not_called()
        mock_ca_inval.assert_not_called()
        mock_db.files.update_one.assert_not_called()


@pytest.mark.anyio
async def test_deleting_file_bound_to_active_link_invalidates_link():
    """13, 14: Deleting a file bound to active link invalidates that link and preserves history."""
    mock_db = MagicMock()
    mock_order = {
        "id": "ord-ca",
        "po_number": "PO-CA",
        "customer_approval": {"approval_id": "ca-active", "status": "active"},
        "customer_approval_history": [],
    }
    active_rec = {
        "id": "ca-active",
        "order_id": "ord-ca",
        "design_version": "f-to-delete",
        "status": "active",
    }
    mock_db.customer_approvals.find_one = AsyncMock(return_value=copy.deepcopy(active_rec))
    mock_db.customer_approvals.update_one = AsyncMock()

    deps = {
        "db": mock_db,
        "current_user": lambda: {"email": "test@test.com"},
        "require_perm": AsyncMock(),
        "persist_order": AsyncMock(),
        "audit_po": AsyncMock(),
        "add_timeline": AsyncMock(),
        "obj_storage": None,
        "logger": logging.getLogger("test"),
    }
    router = build_router(deps)
    actor = {"email": "user@example.com", "name": "User One"}

    res = await router._invalidate_on_design_file_delete(mock_order, "f-to-delete", actor)
    assert res is not None
    assert res["status"] == "invalidated"
    assert mock_order["customer_approval"]["status"] == "invalidated"
    assert len(mock_order["customer_approval_history"]) == 1
    assert mock_order["customer_approval_history"][0]["action"] == "invalidated-on-file-delete"
    assert mock_order["customer_approval_history"][0]["design_version"] == "f-to-delete"


def test_current_design_pdf_resolution_and_validation():
    """15, 21: Canonical current PDF resolution, rejects non-PDF or empty."""
    # Empty order
    assert current_design_pdf_file({}) is None

    # Order with only image
    img = _make_file("img-1", "photo.png", is_pdf=False)
    order_img = {"design_files": [img]}
    assert current_design_pdf_file(order_img) is None

    # Order with old PDF then newer image -> current revision is the image, so no active PDF!
    pdf1 = _make_file("pdf-1", "old.pdf", is_pdf=True)
    order_pdf_then_img = {"design_files": [pdf1, img]}
    assert current_design_pdf_file(order_pdf_then_img) is None

    # Order with newer PDF -> resolves to the newer PDF
    pdf2 = _make_file("pdf-2", "new.pdf", is_pdf=True)
    order_revised = {"design_files": [pdf1, pdf2]}
    assert current_design_pdf_file(order_revised)["id"] == "pdf-2"


@pytest.mark.anyio
async def test_generate_link_requires_active_pdf():
    """21: Generating link without active PDF fails safely with 400."""
    mock_db = MagicMock()
    mock_order = {"id": "ord-nopdf", "cancelled": False, "design_files": [_make_file("img", "x.png", is_pdf=False)]}
    mock_db.orders.find_one = AsyncMock(return_value=mock_order)
    mock_db.app_settings.find_one = AsyncMock(return_value=None)

    deps = {
        "db": mock_db,
        "current_user": lambda: {"email": "test@test.com"},
        "require_perm": AsyncMock(),
        "persist_order": AsyncMock(),
        "audit_po": AsyncMock(),
        "add_timeline": AsyncMock(),
        "obj_storage": None,
        "logger": logging.getLogger("test"),
    }
    app = FastAPI()
    router = build_router(deps)
    app.include_router(router)
    client = TestClient(app)

    res = client.post("/api/orders/ord-nopdf/customer-approval/generate", json={})
    assert res.status_code == 400
    assert "File Design PDF aktif belum tersedia" in res.json()["detail"]


@pytest.mark.anyio
async def test_new_approval_link_binds_revised_pdf_and_revokes_old_link():
    """16, 17, 18, 19, 20: New link serves revised PDF; old link is revoked, cannot view/approve."""
    mock_db = MagicMock()
    pdf_old = _make_file("pdf-old", "rev1.pdf", is_pdf=True)
    pdf_new = _make_file("pdf-new", "rev2.pdf", is_pdf=True)

    order_doc = {
        "id": "ord-1",
        "po_number": "PO-REV",
        "cancelled": False,
        "design_files": [pdf_old, pdf_new],
        "customer_approval": None,
        "customer_approval_history": [],
    }

    prior_rec = {
        "id": "ca-old",
        "order_id": "ord-1",
        "design_version": "pdf-old",
        "token_hash": "hash_old",
        "status": "active",
        "generated_at": "2026-09-08T01:00:00+00:00",
    }

    # Setup memory store for customer_approvals
    approvals = [prior_rec]

    async def mock_insert_one(doc):
        approvals.append(doc)

    async def mock_find_one(q, *args, **kwargs):
        sort = kwargs.get("sort")
        if q.get("order_id") == "ord-1" and sort:
            # return latest
            sorted_apps = sorted(approvals, key=lambda a: a.get("generated_at", ""), reverse=True)
            return copy.deepcopy(sorted_apps[0]) if sorted_apps else None
        if q.get("order_id") == "ord-1" and q.get("status") == "active":
            for a in approvals:
                if a.get("order_id") == "ord-1" and a.get("status") == "active":
                    return copy.deepcopy(a)
            return None
        if q.get("token_hash"):
            for a in approvals:
                if a.get("token_hash") == q["token_hash"]:
                    return copy.deepcopy(a)
            return None
        return None

    async def mock_update_many(q, patch):
        for a in approvals:
            if a.get("order_id") == q.get("order_id") and a.get("id") != q.get("id", {}).get("$ne"):
                if a.get("status") == q.get("status"):
                    a.update(patch.get("$set", {}))

    async def mock_update_one(q, patch):
        for a in approvals:
            if a.get("id") == q.get("id"):
                a.update(patch.get("$set", {}))

    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(order_doc))
    mock_db.customer_approvals.insert_one = AsyncMock(side_effect=mock_insert_one)
    mock_db.customer_approvals.find_one = AsyncMock(side_effect=mock_find_one)
    mock_db.customer_approvals.update_many = AsyncMock(side_effect=mock_update_many)
    mock_db.customer_approvals.update_one = AsyncMock(side_effect=mock_update_one)
    mock_db.app_settings.find_one = AsyncMock(return_value=None)

    deps = {
        "db": mock_db,
        "current_user": lambda: {"email": "designer@example.com", "name": "Designer"},
        "require_perm": AsyncMock(),
        "persist_order": AsyncMock(),
        "audit_po": AsyncMock(),
        "add_timeline": AsyncMock(),
        "obj_storage": None,
        "logger": logging.getLogger("test"),
    }
    app = FastAPI()
    router = build_router(deps)
    app.include_router(router)
    client = TestClient(app)

    # Generate new link
    gen_res = client.post("/api/orders/ord-1/customer-approval/generate", json={})
    assert gen_res.status_code == 200
    new_token = gen_res.json()["token"]
    assert gen_res.json()["approval"]["design_version"] == "pdf-new"

    # Prior record must now be revoked
    old_rec = next(a for a in approvals if a["id"] == "ca-old")
    assert old_rec["status"] == "revoked"

    # Try accessing old link design file using old token_hash
    with patch("customer_approval._hash_token", return_value="hash_old"):
        res_old_design = client.get("/api/public/customer-approval/token_old/design")
        assert res_old_design.status_code == 404

        # Try submitting on old token
        res_old_submit = client.post("/api/public/customer-approval/token_old/submit", json={"action": "approve", "signer_name": "Cust"})
        assert res_old_submit.status_code in (400, 404)

    # New link serves revised PDF
    res_new_view = client.get(f"/api/public/customer-approval/{new_token}")
    assert res_new_view.status_code == 200
    assert res_new_view.json()["view"]["design_file"]["id"] == "pdf-new"

    res_new_design = client.get(f"/api/public/customer-approval/{new_token}/design")
    assert res_new_design.status_code == 200


@pytest.mark.anyio
async def test_failed_new_link_creation_does_not_revoke_prior_link():
    """22: If new link insertion fails, prior valid link is NOT revoked."""
    mock_db = MagicMock()
    pdf1 = _make_file("pdf-1", "active.pdf", is_pdf=True)
    order_doc = {
        "id": "ord-fail",
        "po_number": "PO-FAIL",
        "cancelled": False,
        "design_files": [pdf1],
    }
    prior_rec = {
        "id": "ca-prior",
        "order_id": "ord-fail",
        "status": "active",
        "design_version": "pdf-1",
        "generated_at": "2026-09-08T01:00:00+00:00",
    }
    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(order_doc))
    mock_db.customer_approvals.find_one = AsyncMock(return_value=copy.deepcopy(prior_rec))
    mock_db.app_settings.find_one = AsyncMock(return_value=None)
    # Simulate DB insert failure
    mock_db.customer_approvals.insert_one = AsyncMock(side_effect=RuntimeError("Mongo write error"))
    mock_db.customer_approvals.update_many = AsyncMock()

    deps = {
        "db": mock_db,
        "current_user": lambda: {"email": "designer@example.com"},
        "require_perm": AsyncMock(),
        "persist_order": AsyncMock(),
        "audit_po": AsyncMock(),
        "add_timeline": AsyncMock(),
        "obj_storage": None,
        "logger": logging.getLogger("test"),
    }
    app = FastAPI()
    router = build_router(deps)
    app.include_router(router)
    client = TestClient(app)

    with pytest.raises(RuntimeError):
        client.post("/api/orders/ord-fail/customer-approval/generate", json={})

    # update_many (which revokes prior link) MUST NOT have been called!
    mock_db.customer_approvals.update_many.assert_not_called()


@pytest.mark.anyio
async def test_delete_design_file_storage_success_db_failure_full_rollback():
    """Sections 4, 5, 6, 7: Storage delete succeeds, but DB persist fails.
    Verifies full rollback:
    - active approval link restored to active
    - db.files restored to not deleted
    - order document restored to snapshot (A remained attached, no success timeline)
    - no false success audit log written
    """
    f1 = _make_file("file-s1", "design.pdf", storage_path="eternal-packaging/designs/po-fail-db/file-s1.pdf")
    f2 = _make_file("file-s2", "other.pdf", storage_path="eternal-packaging/designs/po-fail-db/file-s2.pdf")
    mock_order = {
        "id": "po-fail-db",
        "po_number": "PO-FAIL-DB",
        "cancelled": False,
        "design_files": [f1, f2],
        "timeline": [{"stage": 1, "note": "Initial upload"}],
    }
    mock_ca = {
        "id": "ca-active-link",
        "order_id": "po-fail-db",
        "design_version": "file-s1",
        "status": "active",
        "revoke_reason": None,
    }
    mock_file_doc = {
        "id": "file-s1",
        "order_id": "po-fail-db",
        "is_deleted": False,
    }

    mock_user = {"email": "designer@example.com", "role": "designer"}
    mock_db = MagicMock()

    async def mock_find_one(q, *args, **kwargs):
        if q.get("id") == "po-fail-db":
            return copy.deepcopy(mock_order)
        if q.get("design_version") == "file-s1":
            return copy.deepcopy(mock_ca)
        if q.get("id") == "file-s1":
            return copy.deepcopy(mock_file_doc)
        return None

    mock_db.orders.find_one = AsyncMock(side_effect=mock_find_one)
    mock_db.customer_approvals.find_one = AsyncMock(side_effect=mock_find_one)
    mock_db.files.find_one = AsyncMock(side_effect=mock_find_one)

    mock_ca_updates = []
    async def mock_ca_update_one(q, update):
        mock_ca_updates.append((q, update))
    mock_db.customer_approvals.update_one = AsyncMock(side_effect=mock_ca_update_one)

    mock_file_updates = []
    async def mock_file_update_one(q, update):
        mock_file_updates.append((q, update))
    mock_db.files.update_one = AsyncMock(side_effect=mock_file_update_one)

    mock_order_updates = []
    async def mock_order_update_one(q, update):
        mock_order_updates.append((q, update))
    mock_db.orders.update_one = AsyncMock(side_effect=mock_order_update_one)

    mock_audit = AsyncMock()
    mock_storage_bytes = b"%PDF-1.4 test bytes"

    with patch.object(server, "db", mock_db), \
         patch.object(server, "_require_perm", AsyncMock()), \
         patch.object(server, "_persist_order", AsyncMock(side_effect=RuntimeError("Mongo DB connection dropped"))), \
         patch.object(server, "_audit_po", mock_audit), \
         patch.object(server.obj_storage, "get_object", MagicMock(return_value=(mock_storage_bytes, "application/pdf"))), \
         patch.object(server.obj_storage, "delete_object", MagicMock(return_value=True)) as mock_del_obj, \
         patch.object(server.obj_storage, "put_object", MagicMock(return_value={"path": "..."})) as mock_put_obj:

        with pytest.raises(HTTPException) as exc:
            await server.delete_design_file("po-fail-db", "file-s1", user=mock_user)

        assert exc.value.status_code == 500
        assert "Gagal memperbarui data order di database" in exc.value.detail

        # Storage object was deleted, then restored via compensation
        mock_del_obj.assert_called_once_with("eternal-packaging/designs/po-fail-db/file-s1.pdf")
        mock_put_obj.assert_called_once_with("eternal-packaging/designs/po-fail-db/file-s1.pdf", mock_storage_bytes, "application/pdf")

        # 4. Approval link was restored to active
        assert len(mock_ca_updates) >= 1
        last_ca_update = mock_ca_updates[-1][1]["$set"]
        assert last_ca_update["status"] == "active"

        # 5. File record was restored to is_deleted=False
        assert len(mock_file_updates) >= 2
        last_file_update = mock_file_updates[-1][1]["$set"]
        assert last_file_update["is_deleted"] is False

        # 6 & 7. Order was restored to snapshot, so A remains attached and no false timeline/audit
        assert len(mock_order_updates) >= 1
        restored_order = mock_order_updates[-1][1]["$set"]
        assert any(f["id"] == "file-s1" for f in restored_order["design_files"])
        assert len(restored_order["timeline"]) == 1
        mock_audit.assert_not_called()


@pytest.mark.anyio
async def test_delete_design_file_compensation_failure_reports_critical_error():
    """Section 8: If storage compensation also fails, reports CRITICAL hard failure."""
    f1 = _make_file("file-s1", "design.pdf", storage_path="eternal-packaging/designs/po-crit/file-s1.pdf")
    mock_order = {
        "id": "po-crit",
        "po_number": "PO-CRIT",
        "cancelled": False,
        "design_files": [f1],
        "timeline": [],
    }
    mock_user = {"email": "designer@example.com", "role": "designer"}
    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(mock_order))
    mock_db.customer_approvals.find_one = AsyncMock(return_value=None)
    mock_db.files.find_one = AsyncMock(return_value=None)
    mock_db.orders.update_one = AsyncMock()

    mock_storage_bytes = b"%PDF-1.4 test bytes"

    with patch.object(server, "db", mock_db), \
         patch.object(server, "_require_perm", AsyncMock()), \
         patch.object(server, "_persist_order", AsyncMock(side_effect=RuntimeError("Mongo DB failure"))), \
         patch.object(server.obj_storage, "get_object", MagicMock(return_value=(mock_storage_bytes, "application/pdf"))), \
         patch.object(server.obj_storage, "delete_object", MagicMock(return_value=True)), \
         patch.object(server.obj_storage, "put_object", MagicMock(side_effect=RuntimeError("Storage disk completely dead"))):

        with pytest.raises(HTTPException) as exc:
            await server.delete_design_file("po-crit", "file-s1", user=mock_user)

        assert exc.value.status_code == 500
        assert "CRITICAL" in exc.value.detail
        assert "gagal memulihkan file storage" in exc.value.detail
