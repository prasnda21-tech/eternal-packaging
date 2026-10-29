"""Pra-PO verification links or creates one Master Customer. No profile overwrite."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services import pra_po
from services.sales_customers import list_sales_customers
from services.sales_invoices import get_po_invoice_preview
from services.sales_reports import NO_CUSTOMER_ID, _customer_rows, _stable_customer_id
from tests.test_pra_po_and_crm_progress_notifications import (
    ADMIN,
    ADMIN_PERMS,
    CRM_A,
    CRM_PERMS,
    FakeDb,
    SUBMIT_READY,
)


def _line():
    return {
        "name": "Box",
        "quantity": 2,
        "unit": "pcs",
        "master_item_id": "m1",
        "unit_price": 1500,
    }


def _install_unique_customer_name(db: FakeDb) -> None:
    original = db.sales_customers.insert_one

    async def insert_one(doc):
        norm = str(doc.get("normalized_name") or "").strip()
        if norm and any(str(row.get("normalized_name") or "") == norm for row in db.sales_customers.docs):
            raise DuplicateKeyError("normalized_name")
        return await original(doc)

    db.sales_customers.insert_one = insert_one


async def _submit(db, payload):
    created = await pra_po.create_draft(db, payload, CRM_A)
    return await pra_po.submit_pra_po(db, created["id"], CRM_A, CRM_PERMS)


def _stub_materialize(monkeypatch):
    async def require_master(db, *, master_item_id, accurate_item_id, line_no):
        return {"item_type": ""}, {
            "master_item_id": master_item_id,
            "accurate_item_id": accurate_item_id,
            "item_code": "BOX",
            "unit": "pcs",
        }

    async def production_process(db, **kwargs):
        return "print"

    async def availability(db, lines):
        return []

    async def snapshot(db, order):
        return None

    async def ownership(db, order):
        return None

    async def sales_write(db, **kwargs):
        label = str(kwargs.get("sales_label") or "").strip() or None
        return kwargs.get("sales_master_id"), label, None

    async def next_po(db):
        return "PO-PRA-1"

    monkeypatch.setattr(
        "services.line_product_replacement.require_po_create_line_master",
        require_master,
    )
    monkeypatch.setattr(
        "services.master_items.resolve_line_production_process",
        production_process,
    )
    monkeypatch.setattr(
        "services.stock_engine_v2.validate_sales_order_availability",
        availability,
    )
    monkeypatch.setattr(
        "services.physical_stock_engine.attach_order_stock_snapshot",
        snapshot,
    )
    monkeypatch.setattr(
        "services.sales_user_mapping.attach_sales_ownership",
        ownership,
    )
    monkeypatch.setattr("services.sales_master.resolve_for_po_write", sales_write)
    monkeypatch.setattr(pra_po, "_next_po_number", next_po)


@pytest.mark.anyio
async def test_draft_and_submit_do_not_create_master():
    db = FakeDb()
    before = await db.sales_customers.count_documents({})
    submitted = await _submit(db, {
        **SUBMIT_READY,
        "customer_pic": "Ani",
        "customer_email": "ani@toko.test",
        "customer_city": "Jogja",
    })
    assert not str(submitted.get("customer_id") or "").strip()
    assert submitted["customer_phone"] == "08123456789"
    assert submitted["address"] == "Jl. Melati 1"
    assert submitted["customer_pic"] == "Ani"
    assert await db.sales_customers.count_documents({}) == before
    assert await db.orders.count_documents({}) == 0


@pytest.mark.anyio
async def test_verify_existing_customer_does_not_create_or_overwrite(monkeypatch):
    db = FakeDb()
    _stub_materialize(monkeypatch)
    master = await db.sales_customers.find_one({"id": "cust-1"})
    submitted = await _submit(db, {
        "customer": "Nama Di Pra PO",
        "customer_id": "cust-1",
        "customer_phone": "08999",
        "address": "Alamat transaksi",
        "line_items": [_line()],
    })
    result = await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    assert result["pra_po"]["customer_id"] == "cust-1"
    assert result["order"]["customer_id"] == "cust-1"
    assert result["order"]["customer"] == "Nama Di Pra PO"
    assert result["order"]["customer_phone"] == "08999"
    assert result["order"]["address"] == "Alamat transaksi"
    stored = await db.sales_customers.find_one({"id": "cust-1"})
    assert stored["name"] == master["name"]
    assert stored["phone"] == master["phone"]
    assert stored["address"] == master["address"]
    assert await db.sales_customers.count_documents({}) == 1


@pytest.mark.anyio
async def test_verify_creates_one_master_and_links_pra_po_and_order(monkeypatch):
    db = FakeDb()
    _install_unique_customer_name(db)
    _stub_materialize(monkeypatch)
    submitted = await _submit(db, {
        **SUBMIT_READY,
        "customer": "Toko Baru",
        "customer_pic": "Ani",
        "customer_email": "ani@toko.test",
        "customer_city": "Jogja",
    })
    result = await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    cid = result["pra_po"]["customer_id"]
    assert cid
    assert result["order"]["customer_id"] == cid
    assert result["order"]["customer"] == "Toko Baru"
    assert result["order"]["customer_phone"] == SUBMIT_READY["customer_phone"]
    assert result["order"]["address"] == SUBMIT_READY["address"]
    masters = [row for row in db.sales_customers.docs if row.get("id") != "cust-1"]
    assert len(masters) == 1
    assert masters[0]["id"] == cid
    assert masters[0]["name"] == "Toko Baru"
    assert masters[0]["phone"] == SUBMIT_READY["customer_phone"]
    assert masters[0]["address"] == SUBMIT_READY["address"]
    assert masters[0]["pic"] == "Ani"
    assert masters[0]["city"] == "Jogja"
    assert masters[0]["normalized_name"] == "toko baru"

    again = await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    assert again["idempotent"] is True
    assert again["order"]["id"] == result["order"]["id"]
    assert len([row for row in db.sales_customers.docs if row.get("id") != "cust-1"]) == 1

    catalog = await list_sales_customers(db, q="Toko Baru")
    assert any(row.get("id") == cid for row in catalog)

    preview = await get_po_invoice_preview(db, result["order"])
    assert preview["customer"] == "Toko Baru"
    assert _stable_customer_id({"customer": preview["customer"]}, result["order"]) == cid
    rows = _customer_rows([
        {
            "customer_id": cid,
            "customer": preview["customer"],
            "invoice_id": "inv-1",
            "penjualan": Decimal("1500.00"),
            "quantity": Decimal("2"),
            "qty_verified": True,
            "bpp": None,
        },
        {
            "customer_id": None,
            "customer": "Toko Baru",
            "invoice_id": "inv-legacy",
            "penjualan": Decimal("99.00"),
            "quantity": Decimal("1"),
            "qty_verified": True,
            "bpp": None,
        },
    ])
    linked = next(row for row in rows if row["customer_id"] == cid)
    unlinked = next(row for row in rows if row["customer_id"] is None)
    assert linked["penjualan"] == Decimal("1500.00")
    assert linked["customer"] == "Toko Baru"
    assert unlinked["customer_id"] is None
    assert unlinked["customer"] != NO_CUSTOMER_ID


@pytest.mark.anyio
async def test_same_normalized_name_reuses_master_without_overwrite(monkeypatch):
    db = FakeDb()
    _install_unique_customer_name(db)
    _stub_materialize(monkeypatch)
    await db.sales_customers.insert_one({
        "id": "cust-sama",
        "name": "Toko Sama",
        "normalized_name": "toko sama",
        "phone": "081-lama",
        "address": "Alamat master",
        "pic": "PIC lama",
        "email": "lama@test",
        "city": "Lama",
    })
    submitted = await _submit(db, {
        "customer": "  TOKO   SAMA ",
        "customer_phone": "089-baru",
        "address": "Alamat pra-po",
        "customer_pic": "PIC baru",
        "customer_email": "baru@test",
        "customer_city": "Baru",
        "line_items": [_line()],
    })
    result = await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    assert result["pra_po"]["customer_id"] == "cust-sama"
    assert result["order"]["customer_id"] == "cust-sama"
    assert result["order"]["customer"] == "TOKO   SAMA"
    assert result["order"]["customer_phone"] == "089-baru"
    assert result["order"]["address"] == "Alamat pra-po"
    stored = await db.sales_customers.find_one({"id": "cust-sama"})
    assert stored["phone"] == "081-lama"
    assert stored["address"] == "Alamat master"
    assert stored["pic"] == "PIC lama"
    assert stored["name"] == "Toko Sama"
    assert await db.sales_customers.count_documents({"normalized_name": "toko sama"}) == 1


@pytest.mark.anyio
async def test_legacy_master_without_normalized_name_is_reused(monkeypatch):
    db = FakeDb()
    _install_unique_customer_name(db)
    _stub_materialize(monkeypatch)
    await db.sales_customers.insert_one({
        "id": "legacy-1",
        "name": "  Toko   Lama ",
        "phone": "081-lama",
        "address": "Alamat lama",
        "pic": "PIC lama",
    })
    before = [dict(row) for row in db.sales_customers.docs if row.get("id") == "legacy-1"]
    submitted = await _submit(db, {
        "customer": "toko lama",
        "customer_phone": "089-baru",
        "address": "Alamat pra-po",
        "customer_pic": "PIC baru",
        "line_items": [_line()],
    })
    result = await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    assert result["pra_po"]["customer_id"] == "legacy-1"
    assert result["order"]["customer_id"] == "legacy-1"
    assert result["order"]["customer_phone"] == "089-baru"
    assert result["order"]["address"] == "Alamat pra-po"
    stored = await db.sales_customers.find_one({"id": "legacy-1"})
    assert stored["phone"] == "081-lama"
    assert stored["address"] == "Alamat lama"
    assert stored["pic"] == "PIC lama"
    assert "normalized_name" not in stored
    assert stored["name"] == before[0]["name"]
    assert len([
        row for row in db.sales_customers.docs
        if row.get("normalized_name") == "toko lama" or row.get("id") == "legacy-1"
    ]) == 1


@pytest.mark.anyio
async def test_several_legacy_names_are_not_merged_or_duplicated(monkeypatch):
    db = FakeDb()
    _stub_materialize(monkeypatch)
    await db.sales_customers.insert_one({"id": "leg-a", "name": "Toko Ganda", "phone": "1", "address": "A"})
    await db.sales_customers.insert_one({"id": "leg-b", "name": "TOKO GANDA", "phone": "2", "address": "B"})
    before = await db.sales_customers.count_documents({})
    submitted = await _submit(db, {
        "customer": "toko ganda",
        "customer_phone": "089",
        "address": "Jl. Baru",
        "line_items": [_line()],
    })
    with pytest.raises(HTTPException) as exc:
        await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    assert exc.value.status_code == 409
    pra = await db.pra_pos.find_one({"id": submitted["id"]})
    assert pra["status"] == pra_po.STATUS_SUBMITTED
    assert not str(pra.get("customer_id") or "").strip()
    assert await db.orders.count_documents({}) == 0
    assert await db.sales_customers.count_documents({}) == before
    assert "normalized_name" not in (await db.sales_customers.find_one({"id": "leg-a"}))


@pytest.mark.anyio
async def test_customer_id_survives_until_po_insert(monkeypatch):
    db = FakeDb()
    _install_unique_customer_name(db)
    _stub_materialize(monkeypatch)
    submitted = await _submit(db, {**SUBMIT_READY, "customer": "Toko Tahan"})
    original = pra_po._materialize_sales_order
    calls = {"n": 0}

    async def fail_before_insert(db_, pra, user):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HTTPException(400, "Line item gagal")
        return await original(db_, pra, user)

    monkeypatch.setattr(pra_po, "_materialize_sales_order", fail_before_insert)
    with pytest.raises(HTTPException):
        await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    created = [row for row in db.sales_customers.docs if row.get("normalized_name") == "toko tahan"]
    assert len(created) == 1
    pra = await db.pra_pos.find_one({"id": submitted["id"]})
    assert pra["status"] == pra_po.STATUS_SUBMITTED
    assert pra["customer_id"] == created[0]["id"]
    assert await db.orders.count_documents({}) == 0
    phone_before = created[0]["phone"]

    done = await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    assert done["order"]["customer_id"] == created[0]["id"]
    assert done["pra_po"]["customer_id"] == created[0]["id"]
    assert await db.orders.count_documents({"pra_po_id": submitted["id"]}) == 1
    assert len([row for row in db.sales_customers.docs if row.get("normalized_name") == "toko tahan"]) == 1
    stored = await db.sales_customers.find_one({"id": created[0]["id"]})
    assert stored["phone"] == phone_before


@pytest.mark.anyio
async def test_same_phone_different_name_creates_another_master(monkeypatch):
    db = FakeDb()
    _install_unique_customer_name(db)
    _stub_materialize(monkeypatch)
    await db.sales_customers.insert_one({
        "id": "cust-alpha",
        "name": "Alpha",
        "normalized_name": "alpha",
        "phone": "08123",
        "address": "Jl. Alpha",
    })
    submitted = await _submit(db, {
        "customer": "Beta",
        "customer_phone": "08123",
        "address": "Jl. Beta",
        "line_items": [_line()],
    })
    result = await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    assert result["order"]["customer_id"] != "cust-alpha"
    beta = await db.sales_customers.find_one({"id": result["order"]["customer_id"]})
    alpha = await db.sales_customers.find_one({"id": "cust-alpha"})
    assert beta["name"] == "Beta"
    assert beta["phone"] == "08123"
    assert alpha["name"] == "Alpha"
    assert alpha["address"] == "Jl. Alpha"


@pytest.mark.anyio
async def test_invalid_customer_id_is_rejected_without_a_new_master():
    db = FakeDb()
    before = await db.sales_customers.count_documents({})
    submitted = await _submit(db, {
        "customer": "Hantu",
        "customer_phone": "081",
        "address": "Jl. X",
        "line_items": [_line()],
    })
    # Submit already rejects a missing master. Force the stored id after submit
    # so verify itself is the gate under test.
    await db.pra_pos.update_one(
        {"id": submitted["id"]},
        {"$set": {"customer_id": "missing-cust", "status": pra_po.STATUS_SUBMITTED}},
    )
    with pytest.raises(HTTPException) as exc:
        await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    assert exc.value.status_code == 400
    assert "Customer master" in str(exc.value.detail)
    pra = await db.pra_pos.find_one({"id": submitted["id"]})
    assert pra["status"] == pra_po.STATUS_SUBMITTED
    assert await db.orders.count_documents({}) == 0
    assert await db.sales_customers.count_documents({}) == before


@pytest.mark.anyio
async def test_retry_after_po_insert_failure_keeps_one_customer_and_one_po(monkeypatch):
    db = FakeDb()
    _install_unique_customer_name(db)
    _stub_materialize(monkeypatch)
    submitted = await _submit(db, {**SUBMIT_READY, "customer": "Toko Retry"})
    original = pra_po._materialize_sales_order
    calls = {"n": 0}

    async def fail_once(db_, pra, user):
        calls["n"] += 1
        order = await original(db_, pra, user)
        if calls["n"] == 1:
            raise RuntimeError("timeout after insert")
        return order

    monkeypatch.setattr(pra_po, "_materialize_sales_order", fail_once)
    with pytest.raises(RuntimeError):
        await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    created = [row for row in db.sales_customers.docs if row.get("name") == "Toko Retry"]
    assert len(created) == 1
    assert await db.orders.count_documents({"pra_po_id": submitted["id"]}) == 1
    pra = await db.pra_pos.find_one({"id": submitted["id"]})
    assert pra["status"] == pra_po.STATUS_SUBMITTED
    assert pra["customer_id"] == created[0]["id"]

    recovered = await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    assert recovered["idempotent"] is True
    assert recovered["pra_po"]["customer_id"] == created[0]["id"]
    assert recovered["order"]["customer_id"] == created[0]["id"]
    assert await db.orders.count_documents({"pra_po_id": submitted["id"]}) == 1
    assert len([row for row in db.sales_customers.docs if row.get("name") == "Toko Retry"]) == 1


@pytest.mark.anyio
async def test_two_pra_pos_with_the_same_new_name_share_one_master(monkeypatch):
    db = FakeDb()
    _install_unique_customer_name(db)
    _stub_materialize(monkeypatch)
    numbers = iter(["PO-A", "PO-B", "PO-C"])

    async def next_po(db_):
        return next(numbers)

    monkeypatch.setattr(pra_po, "_next_po_number", next_po)
    first = await _submit(db, {**SUBMIT_READY, "customer": "Toko Kembar"})
    second = await _submit(db, {**SUBMIT_READY, "customer": "toko kembar"})
    results = await asyncio.gather(
        pra_po.verify_pra_po(db, first["id"], ADMIN, ADMIN_PERMS),
        pra_po.verify_pra_po(db, second["id"], ADMIN, ADMIN_PERMS),
    )
    ids = {row["pra_po"]["customer_id"] for row in results}
    assert len(ids) == 1
    assert results[0]["order"]["customer_id"] == results[1]["order"]["customer_id"]
    assert results[0]["order"]["id"] != results[1]["order"]["id"]
    assert await db.orders.count_documents({}) == 2
    assert len([row for row in db.sales_customers.docs if row.get("normalized_name") == "toko kembar"]) == 1
    stored = await db.sales_customers.find_one({"normalized_name": "toko kembar"})
    assert stored["phone"] in (SUBMIT_READY["customer_phone"],)


@pytest.mark.anyio
async def test_unique_name_conflict_adopts_winner_without_a_second_insert(monkeypatch):
    db = FakeDb()
    winner = {
        "id": "winner-1",
        "name": "Toko Race",
        "normalized_name": "toko race",
        "phone": "081-lama",
        "address": "Alamat master",
    }
    seen = {"n": 0}

    async def find_name(db_, name):
        seen["n"] += 1
        if seen["n"] == 1:
            return None
        return dict(winner)

    async def insert_conflict(doc):
        raise DuplicateKeyError("normalized_name")

    monkeypatch.setattr("services.sales_customers.find_customer_by_normalized_name", find_name)
    db.sales_customers.insert_one = insert_conflict
    cid = await pra_po._resolve_customer_for_verify(
        db,
        {"customer": "Toko Race", "customer_phone": "089-baru", "address": "Alamat pra"},
        ADMIN,
    )
    assert cid == "winner-1"
    assert winner["phone"] == "081-lama"


@pytest.mark.anyio
async def test_verified_pra_po_retry_does_not_create_another_customer(monkeypatch):
    db = FakeDb()
    _stub_materialize(monkeypatch)
    submitted = await _submit(db, {**SUBMIT_READY, "customer_id": "cust-1", "customer": "Toko Master"})
    first = await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    before = await db.sales_customers.count_documents({})
    second = await pra_po.verify_pra_po(db, submitted["id"], ADMIN, ADMIN_PERMS)
    assert second["idempotent"] is True
    assert second["order"]["id"] == first["order"]["id"]
    assert await db.sales_customers.count_documents({}) == before
