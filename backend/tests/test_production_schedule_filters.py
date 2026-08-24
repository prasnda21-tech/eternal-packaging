"""Logical tests mirroring ProductionSchedule.jsx client filters (AND semantics)."""


def status_of_row(r):
    if r.get("cancelled"):
        return "cancelled"
    if r.get("current_stage", 0) >= 14:
        return "selesai"
    if r.get("hold"):
        return "hold"
    return "aktif"


def screener_filled(r):
    v = r.get("screen_qty")
    return v is not None and str(v).strip() != ""


def normalize_delivery_status(r):
    raw = r.get("delivery_status")
    if not raw or raw in ("Belum", "Belum Terkirim"):
        return "Belum Terkirim"
    if raw in ("Sebagian", "Sebagian Terkirim"):
        return "Sebagian Terkirim"
    if raw == "Terkirim":
        return "Terkirim"
    return raw


def apply_filters(rows, filters):
    q = filters.get("q", "").lower()
    for r in rows:
        if filters.get("statusFilter", "all") != "all" and status_of_row(r) != filters["statusFilter"]:
            continue
        if filters.get("urgentOnly") and not r.get("urgent"):
            continue
        if filters.get("holdOnly") and not r.get("hold"):
            continue
        if filters.get("customerFilter", "all") != "all" and r.get("customer") != filters["customerFilter"]:
            continue
        if filters.get("operatorFilter", "all") != "all":
            ops = [o.get("operator_name") for o in (r.get("operators") or [])]
            if filters["operatorFilter"] not in ops:
                continue
        if filters.get("screenerFilter") == "yes" and not screener_filled(r):
            continue
        if filters.get("screenerFilter") == "no" and screener_filled(r):
            continue
        if filters.get("helperFilter") == "ok" and r.get("helper_shortage_status") != "Cukup":
            continue
        if filters.get("helperFilter") == "kurang" and r.get("helper_shortage_status") != "Barang Kurang":
            continue
        if filters.get("helperFilter") == "none":
            req = r.get("helper_required")
            if req not in (None, ""):
                continue
        packing_status = r.get("packing_status") or "Belum"
        if filters.get("packingFilter", "all") != "all" and packing_status != filters["packingFilter"]:
            continue
        delivery_status = normalize_delivery_status(r)
        if filters.get("deliveryFilter", "all") != "all" and delivery_status != filters["deliveryFilter"]:
            continue
        if filters.get("bufferFilter") == "yes" and not r.get("buffer_stock"):
            continue
        if filters.get("bufferFilter") == "no" and r.get("buffer_stock"):
            continue
        if q:
            hay = f"{r.get('po_number','')} {r.get('customer','')} {r.get('outlet','')}".lower()
            if q not in hay:
                continue
        yield r


SAMPLE = [
    {"id": "1", "po_number": "PO-A", "customer": "C1", "outlet": "O1", "buffer_stock": True,
     "packing_status": "Berjalan", "current_stage": 11, "urgent": False, "hold": False,
     "operators": [], "screen_qty": 100, "helper_required": 100, "helper_shortage_status": "Cukup"},
    {"id": "2", "po_number": "PO-B", "customer": "C2", "outlet": "O2", "buffer_stock": False,
     "packing_status": "Belum", "current_stage": 8, "urgent": True, "hold": False,
     "operators": [{"operator_name": "Op1"}], "screen_qty": None, "helper_required": None},
    {"id": "3", "po_number": "PO-C", "customer": "C1", "outlet": "O3", "buffer_stock": True,
     "packing_status": "Sudah", "current_stage": 14, "delivery_status": "Terkirim",
     "urgent": False, "hold": False, "operators": []},
]


def test_buffer_stock_filter():
    yes = list(apply_filters(SAMPLE, {"bufferFilter": "yes"}))
    assert len(yes) == 2
    no = list(apply_filters(SAMPLE, {"bufferFilter": "no"}))
    assert len(no) == 1
    assert no[0]["po_number"] == "PO-B"


def test_packing_berjalan_filter():
    rows = list(apply_filters(SAMPLE, {"packingFilter": "Berjalan"}))
    assert len(rows) == 1 and rows[0]["po_number"] == "PO-A"


def test_buffer_and_status_and():
    rows = list(apply_filters(SAMPLE, {"bufferFilter": "yes", "statusFilter": "aktif"}))
    assert len(rows) == 1 and rows[0]["po_number"] == "PO-A"


def test_search_filter():
    rows = list(apply_filters(SAMPLE, {"q": "PO-B"}))
    assert len(rows) == 1


def test_reset_returns_all():
    rows = list(apply_filters(SAMPLE, {}))
    assert len(rows) == 3


def run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
    print(f"ALL {len(tests)} FILTER TESTS PASS")


if __name__ == "__main__":
    run_all()
