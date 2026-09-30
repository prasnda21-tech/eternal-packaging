"""PWA Sales list filters: pelanggan/PO search and status chip counts."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.sales_pwa import _matches_customer_po_filter, select_sales_pwa_order_rows


def _order(oid, po_number, customer, **extra):
    row = {
        "id": oid,
        "po_number": po_number,
        "customer": customer,
        "customer_name": customer,
        "order_date": "2026-09-01",
        "cancelled": False,
        "hold": False,
        "buffer_stock": False,
        "current_stage": 3,
    }
    row.update(extra)
    return row


def _ids(rows, **filters):
    matched, counts = select_sales_pwa_order_rows(rows, **filters)
    return [row["id"] for row in matched], counts


def test_search_keyword_matches_customer_or_po():
    rows = [
        _order("1", "PO-001", "Kopi ABC", customer_code="KOPI-9"),
        _order("2", "PO-777", "Teh Manis", customer_id="CUST-2"),
    ]
    assert _ids(rows, q="Kopi")[0] == ["1"]
    assert _ids(rows, q="PO-001")[0] == ["1"]
    assert _ids(rows, q="KOPI-9")[0] == ["1"]
    assert _ids(rows, q="CUST-2")[0] == ["2"]
    assert _ids(rows, q="XYZ")[0] == []
    # The global keyword is not customer_q AND po_q.
    assert _ids(rows, customer_q="PO-001")[0] == []
    assert _ids(rows, po_q="PO-001")[0] == ["1"]


def test_distinct_customer_and_po_filters_stay_and():
    row = _order("1", "PO-100", "Alpha")
    assert _matches_customer_po_filter(row, "alpha", "po-100") is True
    assert _matches_customer_po_filter(row, "alpha", "po-999") is False
    assert _matches_customer_po_filter(row, "alpha", "alpha") is False


def test_status_chips_follow_the_active_search():
    rows = [
        _order("1", "PO-100", "Alpha"),
        _order("2", "PO-200", "Beta", hold=True, order_date="2026-08-01", buffer_stock=True),
    ]
    matched, counts = _ids(rows, q="Alpha", status="semua")
    assert matched == ["1"]
    assert counts == {"semua": 1, "berjalan": 1, "hold": 0, "parsial": 0, "selesai": 0}

    hold_rows, hold_counts = _ids(rows, status="hold")
    assert hold_rows == ["2"]
    assert hold_counts["semua"] == 2
    assert hold_counts["hold"] == 1
    assert hold_counts["berjalan"] == 1

    _, dated = _ids(rows, date_from="2026-09-01", date_to="2026-09-30")
    assert dated["semua"] == 1
    assert dated["hold"] == 0

    _, buffered = _ids(rows, buffer="ya")
    assert buffered["semua"] == 1
    assert buffered["hold"] == 1
    assert buffered["berjalan"] == 0

    _, early = _ids(rows, progress_min=100)
    assert early["semua"] == 0

    _, staged = _ids(rows, stage="HOLD")
    assert staged["semua"] == 1
    assert staged["hold"] == 1
    assert staged["berjalan"] == 0
