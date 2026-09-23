"""PWA Sales serves the signed Surat Jalan rendition without leaking storage paths."""
import copy
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.sales_pwa_http import register_sales_pwa_routes

SALES = {
    "id": "sales-1",
    "user_id": "sales-1",
    "role": "sales_eternal",
    "email": "sales@example.test",
    "name": "Sales Satu",
}

UNSIGNED = b"%PDF-1.4 unsigned-original"
SIGNED_ORIGINAL = b"%PDF-1.4 signed-row-original"
SIGNED = b"%PDF-1.4 SIGNED-RENDITION"
SIGNED_ONLY = b"%PDF-1.4 SIGNED-ONLY"
FALLBACK_ORIGINAL = b"%PDF-1.4 fallback-original"

PATH_UNSIGNED = "secret-store/unsigned.pdf"
PATH_SIGNED_ORIGINAL = "secret-store/signed-original.pdf"
PATH_SIGNED = "secret-store/signed-rendition.pdf"
PATH_SIGNED_ONLY = "secret-store/signed-only.pdf"
PATH_FALLBACK_SIGNED = "secret-store/fallback-signed-missing.pdf"
PATH_FALLBACK_ORIGINAL = "secret-store/fallback-original.pdf"

ORDER = {
    "id": "po-1",
    "po_number": "PO-AUDIT-1",
    "customer": "PT Audit",
    "sales_user_id": "sales-1",
    "cancelled": False,
    "qty_delivered": 100,
    "delivery_status": "Terkirim",
    "total_quantity": 100,
    "line_items": [{"name": "Cup", "quantity": 100}],
    "delivery_proofs": [],
    "surat_jalan_files": [
        {
            "id": "del-unsigned",
            "shipment_id": "del-unsigned",
            "sj_number": "SJ-UNSIGNED",
            "storage_path": PATH_UNSIGNED,
        },
        {
            "id": "del-signed",
            "shipment_id": "del-signed",
            "sj_number": "SJ-SIGNED",
            "storage_path": PATH_SIGNED_ORIGINAL,
            "signed_storage_path": PATH_SIGNED,
            "signed_at": "2026-09-22T10:00:00+00:00",
            "signed_recipient_name": "Penerima",
        },
        {
            "id": "del-signed-only",
            "shipment_id": "del-signed-only",
            "sj_number": "SJ-SIGNED-ONLY",
            "storage_path": "",
            "signed_storage_path": PATH_SIGNED_ONLY,
            "signed_at": "2026-09-22T11:00:00+00:00",
            "signed_recipient_name": "Penerima Dua",
        },
        {
            "id": "del-fallback",
            "shipment_id": "del-fallback",
            "sj_number": "SJ-FALLBACK",
            "storage_path": PATH_FALLBACK_ORIGINAL,
            "signed_storage_path": PATH_FALLBACK_SIGNED,
            "signed_at": "2026-09-22T12:00:00+00:00",
            "signed_recipient_name": "Penerima Tiga",
        },
        {
            "id": "del-data-url",
            "shipment_id": "del-data-url",
            "sj_number": "SJ-DATA",
            "data_url": "data:application/pdf;base64,QQ==",
        },
        {
            "id": "del-cancelled",
            "shipment_id": "del-cancelled",
            "sj_number": "SJ-CANCEL",
            "storage_path": PATH_UNSIGNED,
            "signed_storage_path": PATH_SIGNED,
        },
    ],
}
OTHER_ORDER = {
    "id": "po-2",
    "po_number": "PO-OTHER",
    "customer": "PT Lain",
    "sales_user_id": "sales-2",
    "cancelled": False,
    "qty_delivered": 5,
    "delivery_status": "Belum Terkirim",
    "surat_jalan_files": [],
    "delivery_proofs": [],
}
DELIVERIES = [
    {"id": "del-unsigned", "po_id": "po-1", "sj_number": "SJ-UNSIGNED", "shipment_index": 1, "delivery_date": "2026-09-20", "status": "SELESAI", "total_qty": 10, "is_cancelled": False},
    {"id": "del-signed", "po_id": "po-1", "sj_number": "SJ-SIGNED", "shipment_index": 2, "delivery_date": "2026-09-21", "status": "SELESAI", "total_qty": 20, "is_cancelled": False},
    {"id": "del-signed-only", "po_id": "po-1", "sj_number": "SJ-SIGNED-ONLY", "shipment_index": 3, "delivery_date": "2026-09-22", "status": "SELESAI", "total_qty": 5, "is_cancelled": False},
    {"id": "del-fallback", "po_id": "po-1", "sj_number": "SJ-FALLBACK", "shipment_index": 4, "delivery_date": "2026-09-22", "status": "SEBAGIAN", "total_qty": 3, "is_cancelled": False},
    {"id": "del-data-url", "po_id": "po-1", "sj_number": "SJ-DATA", "shipment_index": 5, "delivery_date": "2026-09-18", "status": "SEBAGIAN", "total_qty": 2, "is_cancelled": False},
    {"id": "del-cancelled", "po_id": "po-1", "sj_number": "SJ-CANCEL", "shipment_index": 6, "delivery_date": "2026-09-17", "status": "SELESAI", "total_qty": 1, "is_cancelled": True},
]


def _match(doc, query):
    for key, expected in (query or {}).items():
        if isinstance(expected, dict) and "$ne" in expected:
            if doc.get(key) == expected["$ne"]:
                return False
            continue
        if isinstance(expected, dict) and "$in" in expected:
            if doc.get(key) not in expected["$in"]:
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


def _project(doc, projection):
    if not projection:
        return dict(doc)
    include = [key for key, flag in projection.items() if key != "_id" and flag]
    if include:
        return {key: doc[key] for key in include if key in doc}
    excluded = {key for key, flag in projection.items() if not flag}
    return {key: value for key, value in doc.items() if key not in excluded}


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._i]
        self._i += 1
        return row


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if _match(doc, query):
                return _project(doc, projection)
        return None

    def find(self, query, projection=None):
        return _Cursor(_project(doc, projection) for doc in self.docs if _match(doc, query))


class _DB:
    def __init__(self, order, other, deliveries):
        self.orders = _Coll([order, other])
        self.sales_deliveries = _Coll(deliveries)
        self.sales_invoices = _Coll([])


class _Store:
    def __init__(self):
        self.blobs = {
            PATH_UNSIGNED: UNSIGNED,
            PATH_SIGNED_ORIGINAL: SIGNED_ORIGINAL,
            PATH_SIGNED: SIGNED,
            PATH_SIGNED_ONLY: SIGNED_ONLY,
            PATH_FALLBACK_ORIGINAL: FALLBACK_ORIGINAL,
        }
        self.reads = []

    def get_object(self, path):
        self.reads.append(path)
        if path not in self.blobs:
            raise FileNotFoundError(path)
        return self.blobs[path], "application/pdf"


async def _allow(_user):
    return ["sales.pwa.view"]


async def _deny(_user):
    return []


def _app(db, store, permissions=_allow):
    app = FastAPI()
    register_sales_pwa_routes(
        app,
        get_db=lambda: db,
        current_user=lambda: SALES,
        Depends=Depends,
        obj_storage=store,
        user_permissions=permissions,
    )
    return app


def _rows(body):
    return {row["id"]: row for row in body.get("sj") or []}


@pytest.fixture
def world():
    order = copy.deepcopy(ORDER)
    other = copy.deepcopy(OTHER_ORDER)
    deliveries = copy.deepcopy(DELIVERIES)
    before_order = copy.deepcopy(order)
    before_deliveries = copy.deepcopy(deliveries)
    db = _DB(order, other, deliveries)
    store = _Store()
    client = TestClient(_app(db, store))
    return {
        "db": db,
        "store": store,
        "client": client,
        "before_order": before_order,
        "before_deliveries": before_deliveries,
    }


def test_unsigned_original_stays_available(world):
    docs = world["client"].get("/sales-pwa/orders/po-1/documents")
    assert docs.status_code == 200, docs.text
    row = _rows(docs.json())["del-unsigned"]
    assert row["available"] is True
    assert row["has_signed"] is False
    assert row["status"] == "SELESAI"
    assert "signed_at" not in row
    assert "recipient_name" not in row
    world["store"].reads.clear()
    opened = world["client"].get("/sales-pwa/orders/po-1/files/sj/del-unsigned")
    assert opened.status_code == 200
    assert opened.content == UNSIGNED
    assert world["store"].reads == [PATH_UNSIGNED]
    assert opened.headers["cache-control"] == "private, no-store"


def test_signed_rendition_is_preferred_once(world):
    docs = world["client"].get("/sales-pwa/orders/po-1/documents")
    body = docs.json()
    ids = [row["id"] for row in body["sj"]]
    assert ids.count("del-signed") == 1
    assert ids.count("del-unsigned") == 1
    row = _rows(body)["del-signed"]
    assert row["available"] is True
    assert row["has_signed"] is True
    assert row["signed_at"] == "2026-09-22T10:00:00+00:00"
    assert row["recipient_name"] == "Penerima"
    assert row["qty"] == 20
    assert row["number"] == "SJ-SIGNED"
    world["store"].reads.clear()
    opened = world["client"].get("/sales-pwa/orders/po-1/files/sj/del-signed")
    assert opened.status_code == 200
    assert opened.content == SIGNED
    assert opened.content != SIGNED_ORIGINAL
    assert world["store"].reads == [PATH_SIGNED]
    assert opened.headers["cache-control"] == "private, no-store"
    sibling = _rows(body)["del-unsigned"]
    assert sibling["number"] == "SJ-UNSIGNED"
    assert sibling["qty"] == 10
    assert sibling["has_signed"] is False


def test_signed_only_file_is_available(world):
    docs = world["client"].get("/sales-pwa/orders/po-1/documents")
    row = _rows(docs.json())["del-signed-only"]
    assert row["available"] is True
    assert row["has_signed"] is True
    assert row["recipient_name"] == "Penerima Dua"
    opened = world["client"].get("/sales-pwa/orders/po-1/files/sj/del-signed-only")
    assert opened.status_code == 200
    assert opened.content == SIGNED_ONLY
    assert opened.headers["cache-control"] == "private, no-store"


def test_missing_signed_object_does_not_open_original(world):
    docs = world["client"].get("/sales-pwa/orders/po-1/documents")
    row = _rows(docs.json())["del-fallback"]
    assert row["available"] is True
    assert row["has_signed"] is True
    assert row["signed_at"] == "2026-09-22T12:00:00+00:00"
    assert row["recipient_name"] == "Penerima Tiga"
    world["store"].reads.clear()
    opened = world["client"].get("/sales-pwa/orders/po-1/files/sj/del-fallback")
    assert opened.status_code == 404
    detail = opened.json()["detail"]
    assert detail == "PDF Surat Jalan bertanda tangan tidak ditemukan"
    assert opened.content != FALLBACK_ORIGINAL
    assert world["store"].reads == [PATH_FALLBACK_SIGNED]
    assert "secret-store/" not in opened.text
    assert PATH_FALLBACK_ORIGINAL not in opened.text
    assert PATH_FALLBACK_SIGNED not in detail


def test_documents_hide_storage_paths_cancelled_and_other_sales(world):
    docs = world["client"].get("/sales-pwa/orders/po-1/documents")
    assert docs.status_code == 200, docs.text
    text = docs.text
    assert "secret-store/" not in text
    assert "storage_path" not in text
    assert "signed_storage_path" not in text
    assert "data_url" not in text
    rows = _rows(docs.json())
    assert "del-cancelled" not in rows
    assert rows["del-data-url"]["available"] is False
    assert rows["del-data-url"]["has_signed"] is False
    assert [row["id"] for row in docs.json()["sj"]] == [
        "del-unsigned",
        "del-signed",
        "del-signed-only",
        "del-fallback",
        "del-data-url",
    ]
    cancelled = world["client"].get("/sales-pwa/orders/po-1/files/sj/del-cancelled")
    assert cancelled.status_code == 404
    missing = world["client"].get("/sales-pwa/orders/po-1/files/sj/del-data-url")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "File Surat Jalan belum tersedia"
    assert "secret-store/" not in missing.text
    foreign_docs = world["client"].get("/sales-pwa/orders/po-2/documents")
    assert foreign_docs.status_code == 404
    foreign_file = world["client"].get("/sales-pwa/orders/po-2/files/sj/del-signed")
    assert foreign_file.status_code == 404
    assert world["db"].orders.docs[0] == world["before_order"]
    assert world["db"].sales_deliveries.docs == world["before_deliveries"]
    assert world["db"].orders.docs[0]["qty_delivered"] == 100
    assert world["db"].orders.docs[0]["delivery_status"] == "Terkirim"


def test_sales_pwa_permission_still_required():
    db = _DB(copy.deepcopy(ORDER), copy.deepcopy(OTHER_ORDER), copy.deepcopy(DELIVERIES))
    client = TestClient(_app(db, _Store(), permissions=_deny))
    denied = client.get("/sales-pwa/orders/po-1/documents")
    assert denied.status_code == 403
    denied_file = client.get("/sales-pwa/orders/po-1/files/sj/del-signed")
    assert denied_file.status_code == 403
