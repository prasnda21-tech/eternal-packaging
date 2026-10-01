import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.customer_identity import (
    build_identity_context,
    customer_identity_key,
    is_legacy_composite_customer_id,
    resolve_customer_identity,
)


def test_legacy_composite_classification():
    assert is_legacy_composite_customer_id("KEDAI TUMAN|Yogyakarta")
    assert is_legacy_composite_customer_id("Kopi Nusantara|Kemang", customer="Kopi Nusantara", outlet="Kemang")
    assert not is_legacy_composite_customer_id("550e8400-e29b-41d4-a716-446655440000")
    assert not is_legacy_composite_customer_id("CUSTOMER-A")
    assert not is_legacy_composite_customer_id("")


def test_outlet_never_becomes_identity():
    resolved = resolve_customer_identity({"outlet": "Yogyakarta", "customer": "", "customer_name": ""})
    assert resolved.key == ""
    assert customer_identity_key({"outlet": "Yogyakarta"}) == ""


def test_unique_name_fallback_uses_single_stable_id():
    live = {"customer": "KEDAI TUMAN", "customer_id": "uuid-1", "outlet": "Yogyakarta"}
    hist = {"customer": "KEDAI TUMAN", "customer_id": "KEDAI TUMAN|Yogyakarta", "outlet": "Yogyakarta"}
    ctx = build_identity_context(
        [{"id": "uuid-1", "name": "KEDAI TUMAN"}],
        [live],
        [hist],
    )
    assert resolve_customer_identity(hist, ctx).key == "id:uuid-1"
    assert resolve_customer_identity(live, ctx).key == "id:uuid-1"


def test_pwa_strict_identity_does_not_promote_a_unique_name():
    live = {"customer": "KEDAI TUMAN", "customer_id": "uuid-1", "outlet": "Yogyakarta"}
    hist = {"customer": "KEDAI TUMAN", "customer_id": "KEDAI TUMAN|Yogyakarta", "outlet": "Yogyakarta"}
    ctx = build_identity_context(
        [{"id": "uuid-1", "name": "KEDAI TUMAN"}],
        [live],
        [hist],
    )
    strict = resolve_customer_identity(hist, ctx, allow_name_fallback=False)
    assert strict.key == "name:kedai tuman"
    assert strict.stable_id is None
    assert strict.kind == "name"
    assert resolve_customer_identity(live, ctx, allow_name_fallback=False).key == "id:uuid-1"


def test_ambiguous_name_does_not_collapse():
    a = {"customer": "TOKO MAKMUR", "customer_id": "CUSTOMER-A"}
    b = {"customer": "TOKO MAKMUR", "customer_id": "CUSTOMER-B"}
    hist = {"customer": "TOKO MAKMUR", "customer_id": "TOKO MAKMUR|Solo", "outlet": "Solo"}
    ctx = build_identity_context([], [a, b], [hist])
    assert resolve_customer_identity(a, ctx).key == "id:CUSTOMER-A"
    assert resolve_customer_identity(b, ctx).key == "id:CUSTOMER-B"
    assert resolve_customer_identity(hist, ctx).key == "ambiguous:toko makmur"
    assert resolve_customer_identity(hist, ctx).ambiguous is True
