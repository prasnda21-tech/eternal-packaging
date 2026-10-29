"""HTTP contract for PO late tracking routes (registration + auth gate)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from server import app


def test_po_late_tracking_routes_registered():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/delivery/po-late-tracking" in paths
    assert "/api/delivery/po-late-tracking/summary" in paths


def test_po_late_tracking_requires_auth():
    client = TestClient(app)
    summary = client.get("/api/delivery/po-late-tracking/summary")
    listing = client.get("/api/delivery/po-late-tracking")
    assert summary.status_code in (401, 403)
    assert listing.status_code in (401, 403)
