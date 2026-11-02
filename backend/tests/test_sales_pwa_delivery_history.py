"""PWA Sales delivery history: confirmed shipments only, session ownership."""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from permissions import ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.sales_pwa_delivery_history import (
    build_sales_delivery_history,
    confirmed_day_wib,
    filter_history_rows,
    flatten_confirmation_rows,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    app.dependency_overrides.pop(current_user, None)
    yield
    app.dependency_overrides.pop(current_user, None)


def _user(uid, role="sales_eternal"):
    return {
        "id": uid,
        "user_id": uid,
        "email": f"{uid}@example.com",
        "name": uid,
        "role": role,
        "permissions": list(ROLE_DEFAULT_PERMISSIONS.get(role, [])),
    }


def _conf(cid, sid, at, items, **extra):
    row = {
        "id": cid,
        "shipment_id": sid,
        "sj_number": extra.pop("sj_number", "SJ-1"),
        "confirmed_at": at,
        "qty": sum(int(it.get("qty") or 0) for it in items),
        "items": items,
        "proof_ids": extra.pop("proof_ids", ["p1"]),
        "recipient_name": extra.pop("recipient_name", "Budi"),
        "has_recipient_signature": True,
        "has_driver_signature": True,
    }
    row.update(extra)
    return row


def _order(oid, po, owner, customer, shipments, confirmations, **extra):
    doc = {
        "id": oid,
        "po_number": po,
        "customer": customer,
        "customer_name": customer,
        "outlet": extra.pop("outlet", "Outlet A"),
        "sales_user_id": owner,
        "cancelled": False,
        "line_items": extra.pop(
            "line_items",
            [
                {"line_id": "L0", "name": "Cup PET 16oz 11gr", "quantity": 2000, "unit": "pcs"},
                {"line_id": "L1", "name": "Lid Flat 16oz", "quantity": 500, "unit": "pcs"},
            ],
        ),
        "shipments": shipments,
        "delivery_confirmations": confirmations,
        "delivery_proofs": extra.pop(
            "delivery_proofs",
            [{"id": "p1", "storage_path": "proofs/p1.jpg", "content_type": "image/jpeg"}],
        ),
    }
    doc.update(extra)
    return doc


def test_confirmed_day_wib_inclusive_boundary():
    assert confirmed_day_wib("2026-11-02T00:30:00+07:00") == "2026-11-02"
    assert confirmed_day_wib("2026-11-01T17:30:00Z") == "2026-11-02"
    assert confirmed_day_wib("2026-11-01T16:59:00Z") == "2026-11-01"


def test_flatten_excludes_cancelled_superseded_legacy_and_foreign():
    owner = "sales-a"
    active = {
        "id": "sh-a",
        "number": "SJ-A",
        "sales_delivery_id": "sh-a",
        "lines": [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 1000}],
    }
    cancelled = {
        "id": "sh-x",
        "number": "SJ-X",
        "is_cancelled": True,
        "lines": [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 100}],
    }
    superseded = {
        "id": "sh-y",
        "number": "SJ-Y",
        "superseded": True,
        "superseded_by_shipment_id": "sh-a",
        "lines": [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 50}],
    }
    order = _order(
        "po-1",
        "PO-1",
        owner,
        "Kopi Nusantara",
        [active, cancelled, superseded],
        [
            _conf(
                "c1",
                "sh-a",
                "2026-11-02T10:00:00+07:00",
                [
                    {"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 1000},
                    {"line_idx": 1, "line_id": "L1", "name": "Lid Flat 16oz", "qty": 200},
                ],
                sj_number="SJ-A",
            ),
            _conf(
                "c-x",
                "sh-x",
                "2026-11-02T11:00:00+07:00",
                [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 100}],
                sj_number="SJ-X",
            ),
            _conf(
                "c-y",
                "sh-y",
                "2026-11-02T12:00:00+07:00",
                [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 50}],
                sj_number="SJ-Y",
            ),
        ],
    )
    rows = flatten_confirmation_rows(order)
    assert len(rows) == 2
    assert {r["item_name"] for r in rows} == {"Cup PET 16oz 11gr", "Lid Flat 16oz"}
    assert all(r["sj_number"] == "SJ-A" for r in rows)
    assert all(r["unit"] == "pcs" for r in rows)

    # No confirmations → no synthetic legacy rows
    legacy = _order("po-legacy", "PO-L", owner, "Legacy", [active], [])
    legacy["qty_delivered"] = 999
    legacy["delivery_proofs"] = [{"id": "px", "storage_path": "x"}]
    assert flatten_confirmation_rows(legacy) == []


def test_partial_multi_po_sj_and_text_filters():
    owner = "sales-a"
    o1 = _order(
        "po-1",
        "PO-1024",
        owner,
        "Kopi Nusantara",
        [{"id": "sh1", "number": "SJ-2108", "lines": []}],
        [
            _conf(
                "c1",
                "sh1",
                "2026-11-02T08:00:00+07:00",
                [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 2000}],
                sj_number="SJ-2108",
            ),
            _conf(
                "c2",
                "sh1",
                "2026-11-03T09:00:00+07:00",
                [{"line_idx": 1, "line_id": "L1", "name": "Lid Flat 16oz", "qty": 500}],
                sj_number="SJ-2108",
            ),
        ],
    )
    o2 = _order(
        "po-2",
        "PO-2048",
        owner,
        "Es Teh Makmur",
        [{"id": "sh2", "number": "SJ-2201", "lines": []}],
        [
            _conf(
                "c3",
                "sh2",
                "2026-11-04T10:00:00+07:00",
                [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 800}],
                sj_number="SJ-2201",
            ),
        ],
        line_items=[{"line_id": "L0", "name": "Cup PET 16oz 11gr", "quantity": 800, "unit": "pcs"}],
    )
    foreign = _order(
        "po-x",
        "PO-X",
        "sales-b",
        "Foreign",
        [{"id": "shx", "number": "SJ-X", "lines": []}],
        [
            _conf(
                "cx",
                "shx",
                "2026-11-02T08:00:00+07:00",
                [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 10}],
                sj_number="SJ-X",
            ),
        ],
    )
    payload = build_sales_delivery_history(
        [o1, o2, foreign],
        _user(owner),
        item_q="pet 16",
        date_from="2026-11-01",
        date_to="2026-11-30",
        limit=50,
    )
    assert payload["total"] == 2
    assert {r["po_number"] for r in payload["rows"]} == {"PO-1024", "PO-2048"}
    assert all("PET" in r["item_name"].upper() for r in payload["rows"])
    assert payload["rows"][0]["confirmed_at"] >= payload["rows"][1]["confirmed_at"]

    page = build_sales_delivery_history(
        [o1, o2],
        _user(owner),
        skip=0,
        limit=1,
    )
    assert page["total"] == 3
    assert len(page["rows"]) == 1

    qty_only = filter_history_rows(
        flatten_confirmation_rows(o1) + flatten_confirmation_rows(o2),
        qty_min=1000,
        qty_max=2500,
    )
    assert len(qty_only) == 1
    assert qty_only[0]["qty"] == 2000


@pytest.mark.anyio
async def test_history_http_ownership_spoof_and_pagination():
    import server

    owner_a = f"sales-a-{uuid.uuid4().hex[:8]}"
    owner_b = f"sales-b-{uuid.uuid4().hex[:8]}"
    oid_a = f"po-a-{uuid.uuid4().hex[:8]}"
    oid_b = f"po-b-{uuid.uuid4().hex[:8]}"
    await server.db.orders.delete_many({"id": {"$in": [oid_a, oid_b]}})
    await server.db.orders.insert_many([
        _order(
            oid_a,
            "PO-A",
            owner_a,
            "Customer A",
            [{"id": "sha", "number": "SJ-A", "lines": []}],
            [
                _conf(
                    "ca1",
                    "sha",
                    "2026-11-02T08:00:00+07:00",
                    [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 100}],
                    sj_number="SJ-A",
                ),
                _conf(
                    "ca2",
                    "sha",
                    "2026-11-03T08:00:00+07:00",
                    [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 50}],
                    sj_number="SJ-A",
                ),
            ],
        ),
        _order(
            oid_b,
            "PO-B",
            owner_b,
            "Customer B",
            [{"id": "shb", "number": "SJ-B", "lines": []}],
            [
                _conf(
                    "cb1",
                    "shb",
                    "2026-11-02T08:00:00+07:00",
                    [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 999}],
                    sj_number="SJ-B",
                ),
            ],
        ),
    ])

    app.dependency_overrides[current_user] = lambda: _user(owner_a)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        own = await client.get(
            "/api/sales-pwa/deliveries/history",
            params={"sales_user_id": owner_b, "limit": 10},
        )
        assert own.status_code == 200
        body = own.json()
        assert body["sales_user_id"] == owner_a
        assert body["total"] == 2
        assert all(r["po_number"] == "PO-A" for r in body["rows"])

        page1 = await client.get(
            "/api/sales-pwa/deliveries/history",
            params={"skip": 0, "limit": 1, "item_q": "pet 16"},
        )
        page2 = await client.get(
            "/api/sales-pwa/deliveries/history",
            params={"skip": 1, "limit": 1, "item_q": "pet 16"},
        )
        assert page1.status_code == 200 and page2.status_code == 200
        assert page1.json()["total"] == 2
        assert page2.json()["total"] == 2
        assert page1.json()["rows"][0]["id"] != page2.json()["rows"][0]["id"]

        blocked = await client.post("/api/sales-pwa/deliveries/history", json={})
        assert blocked.status_code in (405, 404, 422)

        erp = await client.get("/api/sales-deliveries")
        assert erp.status_code == 403

    app.dependency_overrides[current_user] = lambda: _user(owner_b)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        other = await client.get("/api/sales-pwa/deliveries/history")
        assert other.status_code == 200
        assert other.json()["total"] == 1
        assert other.json()["rows"][0]["po_number"] == "PO-B"

    await server.db.orders.delete_many({"id": {"$in": [oid_a, oid_b]}})


def test_sort_desc_and_stable_tie_breaker_pagination():
    owner = "sales-a"
    same = "2026-11-05T10:00:00+07:00"
    order = _order(
        "po-sort",
        "PO-SORT",
        owner,
        "Sort Co",
        [
            {"id": "sh1", "number": "SJ-1", "lines": []},
            {"id": "sh2", "number": "SJ-2", "lines": []},
        ],
        [
            _conf(
                "c-old",
                "sh1",
                "2026-11-01T08:00:00+07:00",
                [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 10}],
                sj_number="SJ-1",
            ),
            _conf(
                "c-b",
                "sh2",
                same,
                [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 20}],
                sj_number="SJ-2",
            ),
            _conf(
                "c-a",
                "sh1",
                same,
                [
                    {"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 30},
                    {"line_idx": 1, "line_id": "L1", "name": "Lid Flat 16oz", "qty": 5},
                ],
                sj_number="SJ-1",
            ),
            _conf(
                "c-new",
                "sh2",
                "2026-11-10T12:00:00+07:00",
                [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 40}],
                sj_number="SJ-2",
            ),
        ],
    )
    page1 = build_sales_delivery_history([order], _user(owner), skip=0, limit=2)
    page2 = build_sales_delivery_history([order], _user(owner), skip=2, limit=2)
    page3 = build_sales_delivery_history([order], _user(owner), skip=4, limit=2)
    assert page1["total"] == 5  # c-a expands to two item rows
    ids = [r["id"] for r in page1["rows"] + page2["rows"] + page3["rows"]]
    assert len(ids) == 5
    assert len(ids) == len(set(ids))
    assert page1["rows"][0]["confirmed_at"] == "2026-11-10T12:00:00+07:00"
    assert page1["rows"][0]["qty"] == 40
    # Same timestamp: stable by po/sj/confirmation/line/id — no overlap across pages.
    assert {r["id"] for r in page1["rows"]}.isdisjoint({r["id"] for r in page2["rows"]})
    all_rows = page1["rows"] + page2["rows"] + page3["rows"]
    times = [r["confirmed_at"] for r in all_rows]
    assert times == sorted(times, reverse=True)


def test_unit_ambiguous_when_line_id_conflicts():
    order = _order(
        "po-u",
        "PO-U",
        "sales-a",
        "Cust",
        [{"id": "shu", "number": "SJ-U", "lines": []}],
        [
            _conf(
                "cu",
                "shu",
                "2026-11-02T08:00:00+07:00",
                [{"line_idx": 0, "line_id": "MISSING", "name": "Cup PET 16oz 11gr", "qty": 10}],
                sj_number="SJ-U",
            ),
        ],
    )
    rows = flatten_confirmation_rows(order)
    assert len(rows) == 1
    assert rows[0]["unit"] is None
    assert rows[0]["unit_ambiguous"] is True


def test_large_dataset_pagination_25_50_100_no_truncation():
    """Isolated >100-row fixture: full total, page sizes, no dup/missing across pages."""
    owner = "sales-page-owner"
    foreign = "sales-other"
    items_catalog = [
        ("L0", "Cup PET 16oz 11gr"),
        ("L1", "Lid Flat 16oz"),
        ("L2", "Sedotan Paper Kraft"),
    ]
    orders = []
    expected_ids = []
    # 40 POs × 3 items = 120 rows inside Bulan Ini WIB Nov 2026
    for i in range(40):
        day = 1 + (i % 28)
        hour = 8 + (i % 10)
        at = f"2026-11-{day:02d}T{hour:02d}:{(i % 60):02d}:00+07:00"
        po = f"PO-PAGE-{i:03d}"
        sj = f"SJ-PAGE-{i:03d}"
        sid = f"sh-page-{i:03d}"
        items = [
            {"line_idx": li, "line_id": lid, "name": name, "qty": 100 + i + li}
            for li, (lid, name) in enumerate(items_catalog)
        ]
        conf = _conf(f"c-page-{i:03d}", sid, at, items, sj_number=sj)
        orders.append(
            _order(
                f"po-page-{i:03d}",
                po,
                owner,
                f"Customer {i % 7}",
                [{"id": sid, "number": sj, "lines": []}],
                [conf],
                outlet=f"Outlet {i % 5}",
                line_items=[
                    {"line_id": lid, "name": name, "quantity": 1000, "unit": "pcs"}
                    for lid, name in items_catalog
                ],
            )
        )
        for li, (_, name) in enumerate(items_catalog):
            expected_ids.append(f"{orders[-1]['id']}:{conf['id']}:{li}")

    # Foreign owner rows must never appear
    orders.append(
        _order(
            "po-foreign",
            "PO-FOREIGN",
            foreign,
            "Foreign Co",
            [{"id": "sh-f", "number": "SJ-F", "lines": []}],
            [
                _conf(
                    "c-f",
                    "sh-f",
                    "2026-11-15T12:00:00+07:00",
                    [{"line_idx": 0, "line_id": "L0", "name": "Cup PET 16oz 11gr", "qty": 9}],
                    sj_number="SJ-F",
                )
            ],
        )
    )

    user = _user(owner)
    full = build_sales_delivery_history(
        orders, user, date_from="2026-11-01", date_to="2026-11-30", skip=0, limit=100
    )
    assert full["total"] == 120
    assert len(full["rows"]) == 100
    assert all(r["po_number"] != "PO-FOREIGN" for r in full["rows"])

    page25 = build_sales_delivery_history(
        orders, user, date_from="2026-11-01", date_to="2026-11-30", skip=0, limit=25
    )
    page25b = build_sales_delivery_history(
        orders, user, date_from="2026-11-01", date_to="2026-11-30", skip=25, limit=25
    )
    assert page25["total"] == 120
    assert len(page25["rows"]) == 25
    assert len(page25b["rows"]) == 25
    assert {r["id"] for r in page25["rows"]}.isdisjoint({r["id"] for r in page25b["rows"]})

    page50 = build_sales_delivery_history(
        orders, user, date_from="2026-11-01", date_to="2026-11-30", skip=0, limit=50
    )
    assert page50["limit"] == 50
    assert len(page50["rows"]) == 50

    # Walk all pages at limit=25 — no dups, no missing, newest→oldest
    seen = []
    for skip in range(0, 120, 25):
        chunk = build_sales_delivery_history(
            orders, user, date_from="2026-11-01", date_to="2026-11-30", skip=skip, limit=25
        )
        assert chunk["total"] == 120
        seen.extend(r["id"] for r in chunk["rows"])
    assert len(seen) == 120
    assert len(set(seen)) == 120
    assert set(seen) == set(expected_ids)

    all_pages = []
    for skip in range(0, 120, 25):
        all_pages.extend(
            build_sales_delivery_history(
                orders, user, date_from="2026-11-01", date_to="2026-11-30", skip=skip, limit=25
            )["rows"]
        )
    times = [r["confirmed_at"] for r in all_pages]
    assert times == sorted(times, reverse=True)

    # Live search spans full dataset (not just first page)
    searched = build_sales_delivery_history(
        orders, user, date_from="2026-11-01", date_to="2026-11-30", item_q="Sedotan", skip=0, limit=25
    )
    assert searched["total"] == 40  # one sedotan line per PO
    assert len(searched["rows"]) == 25
    assert all("Sedotan" in (r["item_name"] or "") for r in searched["rows"])

    # limit capped at HISTORY_PAGE_SIZE_MAX (100)
    capped = build_sales_delivery_history(
        orders, user, date_from="2026-11-01", date_to="2026-11-30", skip=0, limit=500
    )
    assert capped["limit"] == 100
    assert capped["total"] == 120
    assert len(capped["rows"]) == 100
