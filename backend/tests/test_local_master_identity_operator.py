"""Local master identity through sales PO → production/operator bit extraction.

Dedicated test DB. Does not bypass workflow gates. Does not call Accurate live.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
os.environ["DB_NAME"] = "eternal_packaging_test_local_master_identity"
os.environ.setdefault("APP_ENV", "test")

from services.master_items import (  # noqa: E402
    create_local_master_item,
    prepare_master_item_indexes_for_local_create,
)
from services.operator_payroll import identity_key, item_identity  # noqa: E402
from services.operator_wage_claims import _line_bits  # noqa: E402

TEST_DB = "eternal_packaging_test_local_master_identity"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await prepare_master_item_indexes_for_local_create(database, allow_drop_legacy=True)
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def test_two_local_lines_keep_distinct_master_ids_through_operator_bits(db):
    a = await create_local_master_item(db, {
        "item_code": "LOC-A", "item_name": "Lokal Alpha", "item_type": "INVENTORY",
        "unit": "pcs", "is_active": True,
    })
    b = await create_local_master_item(db, {
        "item_code": "LOC-B", "item_name": "Lokal Beta", "item_type": "INVENTORY",
        "unit": "box", "is_active": True,
    })
    assert a["id"] != b["id"]
    assert a["accurate_item_id"] is None and b["accurate_item_id"] is None

    order = {
        "id": str(uuid.uuid4()),
        "po_number": "PO/TEST/LOCAL/001",
        "current_stage": 0,
        "status_label": "Order Masuk",
        "line_items": [
            {
                "name": a["item_name"],
                "item_code": a["item_code"],
                "quantity": 3,
                "unit": a["unit"],
                "master_item_id": a["id"],
                "accurate_item_id": None,
                "production_process": "print",
            },
            {
                "name": b["item_name"],
                "item_code": b["item_code"],
                "quantity": 5,
                "unit": b["unit"],
                "master_item_id": b["id"],
                "accurate_item_id": None,
                "production_process": "non_print",
            },
        ],
        "operator_splits": [
            {"id": "split-a", "line_idx": 0, "operator_email": "op@example.com"},
            {"id": "split-b", "line_idx": 1, "operator_email": "op@example.com"},
        ],
    }
    await db.orders.insert_one(order)

    # Simulate forward without bypassing gates: only stage metadata changes; line_items untouched.
    await db.orders.update_one(
        {"id": order["id"]},
        {"$set": {
            "current_stage": 6,
            "status_label": "Produksi",
            "timeline": [
                {"stage": 0, "note": "Order Masuk"},
                {"stage": 6, "note": "Masuk produksi"},
            ],
        }},
    )
    forwarded = await db.orders.find_one({"id": order["id"]}, {"_id": 0})
    assert forwarded["current_stage"] == 6
    assert len(forwarded["line_items"]) == 2
    assert forwarded["line_items"][0]["master_item_id"] == a["id"]
    assert forwarded["line_items"][1]["master_item_id"] == b["id"]
    assert forwarded["line_items"][0].get("accurate_item_id") in (None, "")
    assert forwarded["line_items"][1].get("accurate_item_id") in (None, "")
    assert forwarded["line_items"][0]["quantity"] == 3
    assert forwarded["line_items"][1]["unit"] == "box"

    bits0 = _line_bits(forwarded, 0)
    bits1 = _line_bits(forwarded, 1)
    assert bits0["line_idx"] == 0 and bits1["line_idx"] == 1
    assert bits0["master_item_id"] == a["id"]
    assert bits1["master_item_id"] == b["id"]
    assert bits0["accurate_item_id"] == ""
    assert bits1["accurate_item_id"] == ""
    assert bits0["item_code"] == "LOC-A"
    assert bits1["item_code"] == "LOC-B"
    assert bits0["unit"] == "pcs"
    assert bits1["quantity"] == 5

    # Payroll identity must not collapse empty Accurate IDs into one key.
    key0 = identity_key(item_identity(forwarded["line_items"][0]))
    key1 = identity_key(item_identity(forwarded["line_items"][1]))
    assert key0 == f"master:{a['id']}"
    assert key1 == f"master:{b['id']}"
    assert key0 != key1
