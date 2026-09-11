"""Customer Approval Design must complete Approval Design / StageFlow.

Reproduces the production split:
  customer_approval.status = approved  (panel: Approved by Customer)
  design_status = Waiting              (badge: Menunggu Approval Design)

Customer Approval is an alternate path for the same Approval Design gate,
not a second mandatory internal gate.
"""
from __future__ import annotations

import copy
import inspect
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("STORAGE_PROVIDER", "local")

from customer_approval import build_router
from services.workflow_stage_status import resolve_stage_chip
from workflow_gates import customer_design_approved, design_ok, payment_ok, sync_workflow_stage
import server


STAGE_APPROVAL_DESIGN = 2
STAGE_PAYMENT = 3
STAGE_APPROVAL_PAYMENT = 4


def _future_iso(hours=4):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _screenshot_order(**extra):
    order = {
        "id": "po-ca-stageflow",
        "po_number": "PO-CA-SF-1",
        "customer": "Acme",
        "cancelled": False,
        "current_stage": 2,
        "status_label": "Approval Design",
        "design_status": "Waiting",
        "design_bypassed": False,
        "design_files": [{
            "id": "df-1",
            "name": "art.pdf",
            "content_type": "application/pdf",
            "data_url": "data:application/pdf;base64,eA==",
            "uploaded_at": "2026-09-11T01:00:00+00:00",
        }],
        "payment_status": "Waiting",
        "payment_bypassed": False,
        "payment_files": [],
        "customer_approval": {
            "approval_id": "ca-1",
            "status": "approved",
            "design_version": "df-1",
            "signer_name": "Budi",
            "responded_at": "2026-09-11T02:00:00+00:00",
        },
        "customer_approval_history": [],
        "design_approval_history": [],
        "timeline": [],
        "line_items": [{"name": "Kaos", "quantity": 10, "production_process": "print"}],
    }
    order.update(extra)
    return order


def test_screenshot_split_state_completes_approval_design_not_payment():
    order = _screenshot_order()
    assert customer_design_approved(order)
    assert design_ok(order)
    assert not payment_ok(order)

    ad = resolve_stage_chip(STAGE_APPROVAL_DESIGN, order, 2)
    assert ad["state"] == "done"
    assert ad["label"] == "Selesai"

    pay = resolve_stage_chip(STAGE_PAYMENT, order, 2)
    assert pay["state"] == "current"
    assert pay["label"] == "Belum dikerjakan"

    ap = resolve_stage_chip(STAGE_APPROVAL_PAYMENT, order, 2)
    assert ap["state"] != "done"


def test_reload_same_document_stays_complete():
    order = _screenshot_order()
    first = resolve_stage_chip(STAGE_APPROVAL_DESIGN, order, order["current_stage"])
    reloaded = copy.deepcopy(order)
    second = resolve_stage_chip(STAGE_APPROVAL_DESIGN, reloaded, reloaded["current_stage"])
    assert first == second
    assert second["state"] == "done"


def test_customer_reject_does_not_complete_approval_design():
    order = _screenshot_order(
        customer_approval={"approval_id": "ca-1", "status": "rejected", "reject_reason": "revisi warna"},
        design_status="Waiting",
    )
    assert not customer_design_approved(order)
    assert not design_ok(order)
    chip = resolve_stage_chip(STAGE_APPROVAL_DESIGN, order, 2)
    assert chip["state"] != "done"
    assert chip["label"] == "Menunggu approval"


def test_internal_approve_still_completes_gate():
    order = _screenshot_order(
        customer_approval={"approval_id": "ca-1", "status": "active"},
        design_status="Approved",
        current_stage=3,
    )
    assert design_ok(order)
    assert resolve_stage_chip(STAGE_APPROVAL_DESIGN, order, 3)["state"] == "done"
    assert resolve_stage_chip(STAGE_PAYMENT, order, 3)["state"] == "current"
    assert resolve_stage_chip(STAGE_PAYMENT, order, 3)["label"] == "Belum dikerjakan"


def test_bypass_still_marks_bypass_not_payment_done():
    order = _screenshot_order(
        customer_approval=None,
        design_status="Waiting",
        design_bypassed=True,
        current_stage=3,
    )
    assert design_ok(order)
    chip = resolve_stage_chip(STAGE_APPROVAL_DESIGN, order, 3)
    assert chip["state"] == "bypass"
    assert resolve_stage_chip(STAGE_PAYMENT, order, 3)["state"] == "current"


def test_pending_customer_link_does_not_complete():
    order = _screenshot_order(customer_approval={"approval_id": "ca-1", "status": "active"})
    assert not design_ok(order)
    assert resolve_stage_chip(STAGE_APPROVAL_DESIGN, order, 2)["state"] == "current"


def test_get_order_is_read_only_no_persist():
    src = inspect.getsource(server.get_order)
    assert "persist_order" not in src
    assert "update_one" not in src
    assert "_sync_workflow_stage" not in src


def test_customer_submit_source_writes_canonical_design_status_then_notifies():
    src = inspect.getsource(sys.modules["customer_approval"])
    start = src.index("async def public_submit")
    submit_src = src[start:]
    assert 'order["design_status"] = "Approved"' in submit_src
    assert submit_src.index('order["design_status"] = "Approved"') < submit_src.index("await persist_order(order)")
    assert submit_src.index("await persist_order(order)") < submit_src.index("emit_customer_design_approval_notifications")


class _UpdateOk:
    modified_count = 1


class _UpdateNone:
    modified_count = 0


def _build_submit_client(order, rec, persist_calls, notif_calls=None):
    approvals = [copy.deepcopy(rec)]
    order_store = {"doc": copy.deepcopy(order)}

    async def find_order(q, *args, **kwargs):
        if q.get("id") == order_store["doc"]["id"]:
            return copy.deepcopy(order_store["doc"])
        return None

    async def find_approval(q, *args, **kwargs):
        if q.get("token_hash"):
            for a in approvals:
                if a.get("token_hash") == q["token_hash"]:
                    return copy.deepcopy(a)
            return None
        if q.get("id"):
            for a in approvals:
                if a.get("id") == q["id"]:
                    return copy.deepcopy(a)
            return None
        if q.get("order_id"):
            matches = [a for a in approvals if a.get("order_id") == q["order_id"]]
            if q.get("status"):
                matches = [a for a in matches if a.get("status") == q["status"]]
            sort = kwargs.get("sort")
            if sort:
                matches = sorted(matches, key=lambda a: a.get("generated_at") or "", reverse=True)
            return copy.deepcopy(matches[0]) if matches else None
        return None

    async def update_approval(q, patch):
        for a in approvals:
            if a.get("id") == q.get("id") and (not q.get("status") or a.get("status") == q.get("status")):
                a.update(patch.get("$set") or {})
                return _UpdateOk()
        return _UpdateNone()

    async def persist(order_doc, **kwargs):
        persist_calls.append(copy.deepcopy(order_doc))
        order_store["doc"] = copy.deepcopy(order_doc)
        return order_doc

    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(side_effect=find_order)
    mock_db.customer_approvals.find_one = AsyncMock(side_effect=find_approval)
    mock_db.customer_approvals.update_one = AsyncMock(side_effect=update_approval)
    mock_db.app_settings.find_one = AsyncMock(return_value={
        "key": "customer_approval",
        "expiry_hours": 4,
        "allow_approve": True,
        "allow_reject": True,
        "signature_enabled": False,
        "signature_required_approve": False,
        "signature_required_reject": False,
        "reject_reason_required": True,
    })
    mock_db.files.insert_one = AsyncMock()

    async def _emit(*args, **kwargs):
        if notif_calls is not None:
            notif_calls.append({"decision": kwargs.get("decision"), "approval": kwargs.get("approval")})
        return []

    deps = {
        "db": mock_db,
        "current_user": lambda: {"email": "staff@example.com", "name": "Staff"},
        "require_perm": AsyncMock(),
        "persist_order": persist,
        "audit_po": AsyncMock(),
        "add_timeline": server.add_timeline,
        "sync_workflow_stage": sync_workflow_stage,
        "obj_storage": SimpleNamespace(APP_NAME="eternal", ext_for=lambda *_: "png", put_object=lambda *a, **k: {"path": "x", "size": 1}),
        "logger": logging.getLogger("test_ca_stageflow"),
    }
    app = FastAPI()
    app.include_router(build_router(deps))
    client = TestClient(app)
    return client, approvals, persist, _emit


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_public_submit_approve_writes_canonical_and_advances_stageflow(monkeypatch):
    persist_calls = []
    notif_calls = []
    rec = {
        "id": "ca-1",
        "order_id": "po-ca-stageflow",
        "po_number": "PO-CA-SF-1",
        "design_version": "df-1",
        "token_hash": "hash-active",
        "status": "active",
        "generated_at": "2026-09-11T01:00:00+00:00",
        "expires_at": _future_iso(),
        "expiry_hours": 4,
    }
    order = _screenshot_order(customer_approval={"approval_id": "ca-1", "status": "active"})
    client, _approvals, _persist, emit = _build_submit_client(order, rec, persist_calls, notif_calls)
    import customer_approval as ca
    monkeypatch.setattr(ca, "_hash_token", lambda token: "hash-active")
    monkeypatch.setattr(
        "services.notifications.emit_customer_design_approval_notifications",
        emit,
    )

    res = client.post(
        "/api/public/customer-approval/tok-active/submit",
        json={"action": "approve", "signer_name": "Budi"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["view"]["status"] == "approved"
    assert persist_calls, "submit must persist the order"
    persisted = persist_calls[0]
    assert persisted["design_status"] == "Approved"
    assert persisted["customer_approval"]["status"] == "approved"
    assert persisted["current_stage"] >= 3
    assert design_ok(persisted)
    assert not payment_ok(persisted)
    assert resolve_stage_chip(STAGE_APPROVAL_DESIGN, persisted, persisted["current_stage"])["state"] == "done"
    assert resolve_stage_chip(STAGE_PAYMENT, persisted, persisted["current_stage"])["state"] == "current"
    assert resolve_stage_chip(STAGE_PAYMENT, persisted, persisted["current_stage"])["label"] == "Belum dikerjakan"
    assert len(notif_calls) == 1
    assert notif_calls[0]["decision"] == "approved"

    # Duplicate submit: no second persist / notification
    res2 = client.post(
        "/api/public/customer-approval/tok-active/submit",
        json={"action": "approve", "signer_name": "Budi"},
    )
    assert res2.status_code == 400
    assert persist_calls[0]["design_status"] == "Approved"
    assert len(persist_calls) == 1
    assert len(notif_calls) == 1


def test_public_submit_reject_does_not_advance(monkeypatch):
    persist_calls = []
    notif_calls = []
    rec = {
        "id": "ca-rej",
        "order_id": "po-ca-stageflow",
        "po_number": "PO-CA-SF-1",
        "design_version": "df-1",
        "token_hash": "hash-rej",
        "status": "active",
        "generated_at": "2026-09-11T01:00:00+00:00",
        "expires_at": _future_iso(),
        "expiry_hours": 4,
    }
    order = _screenshot_order(customer_approval={"approval_id": "ca-rej", "status": "active"})
    client, _approvals, _persist, emit = _build_submit_client(order, rec, persist_calls, notif_calls)
    import customer_approval as ca
    monkeypatch.setattr(ca, "_hash_token", lambda token: "hash-rej")
    monkeypatch.setattr("services.notifications.emit_customer_design_approval_notifications", emit)

    res = client.post(
        "/api/public/customer-approval/tok-rej/submit",
        json={"action": "reject", "signer_name": "Budi", "note": "revisi warna"},
    )
    assert res.status_code == 200, res.text
    persisted = persist_calls[0]
    assert persisted["design_status"] == "Waiting"
    assert persisted["customer_approval"]["status"] == "rejected"
    assert persisted["customer_approval"]["reject_reason"] == "revisi warna"
    assert persisted["current_stage"] == 2
    assert not design_ok(persisted)
    assert resolve_stage_chip(STAGE_APPROVAL_DESIGN, persisted, 2)["state"] != "done"
    assert len(notif_calls) == 1
    assert notif_calls[0]["decision"] == "rejected"
