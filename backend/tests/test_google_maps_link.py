"""Google Maps URL parser + SSRF-safe short-link resolver."""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import google_maps_link as maps_link
from services.google_maps_link import (
    GoogleMapsLinkError,
    inspect_google_maps_url,
    parse_google_maps_coordinates,
    resolve_google_maps_short_link,
)


@pytest.fixture(autouse=True)
def _skip_live_dns(monkeypatch):
    monkeypatch.setattr(maps_link, "resolved_addresses_are_public", lambda host: True)


def test_google_com_maps_at_coords():
    result = inspect_google_maps_url(
        "https://www.google.com/maps/place/Monas/@-6.175392,106.827153,17z"
    )
    assert result.status == "OK"
    assert result.lat == -6.175392
    assert result.lng == 106.827153


def test_maps_google_com_q_coords():
    coords = parse_google_maps_coordinates("https://maps.google.com/maps?q=-6.2,106.8")
    assert coords == {"lat": -6.2, "lng": 106.8}


def test_query_coords():
    coords = parse_google_maps_coordinates(
        "https://www.google.com/maps/search/?api=1&query=-6.175,106.827"
    )
    assert coords == {"lat": -6.175, "lng": 106.827}


def test_whitespace_trimmed():
    coords = parse_google_maps_coordinates("  https://www.google.com/maps/@-6.2,106.8,17z \n")
    assert coords == {"lat": -6.2, "lng": 106.8}


def test_malformed_url_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("not a url")
    assert exc.value.code == "INVALID_LINK"


def test_non_google_host_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("https://example.com/maps/@-6.2,106.8")
    assert exc.value.code == "UNSUPPORTED_HOST"


def test_http_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("http://www.google.com/maps/@-6.2,106.8,17z")
    assert exc.value.code == "INVALID_LINK"


def test_latitude_under_minus_90_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("https://www.google.com/maps/@-91,106.8,17z")
    assert exc.value.code == "INVALID_LINK"


def test_latitude_over_90_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("https://www.google.com/maps/@91,106.8,17z")
    assert exc.value.code == "INVALID_LINK"
    assert parse_google_maps_coordinates("https://www.google.com/maps/@91,106.8,17z") is None


def test_longitude_over_180_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("https://maps.google.com/?q=-6.2,181")
    assert exc.value.code == "INVALID_LINK"


def test_longitude_under_minus_180_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("https://maps.google.com/?q=-6.2,-181")
    assert exc.value.code == "INVALID_LINK"


def test_ipv6_loopback_and_ula_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("https://[::1]/maps/@-6.2,106.8")
    assert exc.value.code == "UNSUPPORTED_HOST"
    with pytest.raises(GoogleMapsLinkError) as exc2:
        inspect_google_maps_url("https://[fd00::1]/maps/@-6.2,106.8")
    assert exc2.value.code == "UNSUPPORTED_HOST"
    with pytest.raises(GoogleMapsLinkError) as exc3:
        inspect_google_maps_url("https://[fe80::1]/maps/@-6.2,106.8")
    assert exc3.value.code == "UNSUPPORTED_HOST"


def test_no_coordinates_does_not_invent_zero():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("https://www.google.com/maps/place/Monas")
    assert exc.value.code == "NO_COORDINATES"
    assert parse_google_maps_coordinates("https://www.google.com/maps/place/Monas") is None


def test_localhost_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("https://localhost/maps/@-6.2,106.8")
    assert exc.value.code == "UNSUPPORTED_HOST"


def test_loopback_ip_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("https://127.0.0.1/maps/@-6.2,106.8")
    assert exc.value.code == "UNSUPPORTED_HOST"


def test_private_ip_destination_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("https://10.0.0.8/maps/@-6.2,106.8")
    assert exc.value.code == "UNSUPPORTED_HOST"


def test_credentials_in_url_rejected():
    with pytest.raises(GoogleMapsLinkError) as exc:
        inspect_google_maps_url("https://user:pass@www.google.com/maps/@-6.2,106.8,17z")
    assert exc.value.code == "INVALID_LINK"


class _RedirectTransport(httpx.BaseTransport):
    def __init__(self, mapping, delays=None):
        self.mapping = mapping
        self.delays = delays or {}
        self.calls = []

    def handle_request(self, request):
        self.calls.append(str(request.url))
        if str(request.url) in self.delays:
            raise httpx.TimeoutException("timed out")
        target = self.mapping.get(str(request.url))
        if target is None:
            return httpx.Response(404)
        if target.get("location"):
            return httpx.Response(302, headers={"location": target["location"]})
        return httpx.Response(target.get("status", 200), content=b"")


def test_short_link_allowed_host_resolved():
    transport = _RedirectTransport({
        "https://maps.app.goo.gl/abc": {
            "location": "https://www.google.com/maps/@-6.175,106.827,17z",
        }
    })
    result = resolve_google_maps_short_link(
        "https://maps.app.goo.gl/abc",
        client=httpx.Client(transport=transport, follow_redirects=False),
    )
    assert result.status == "OK"
    assert result.lat == -6.175
    assert result.lng == 106.827


def test_unsupported_external_host_rejected_on_redirect():
    transport = _RedirectTransport({
        "https://maps.app.goo.gl/evil": {"location": "https://evil.example/maps/@-6.2,106.8"},
    })
    with pytest.raises(GoogleMapsLinkError) as exc:
        resolve_google_maps_short_link(
            "https://maps.app.goo.gl/evil",
            client=httpx.Client(transport=transport, follow_redirects=False),
        )
    assert exc.value.code == "UNSUPPORTED_HOST"


def test_localhost_redirect_rejected():
    transport = _RedirectTransport({
        "https://maps.app.goo.gl/local": {"location": "https://localhost/secret"},
    })
    with pytest.raises(GoogleMapsLinkError) as exc:
        resolve_google_maps_short_link(
            "https://maps.app.goo.gl/local",
            client=httpx.Client(transport=transport, follow_redirects=False),
        )
    assert exc.value.code == "UNSUPPORTED_HOST"


def test_loopback_redirect_rejected():
    transport = _RedirectTransport({
        "https://maps.app.goo.gl/loop": {"location": "https://127.0.0.1/secret"},
    })
    with pytest.raises(GoogleMapsLinkError) as exc:
        resolve_google_maps_short_link(
            "https://maps.app.goo.gl/loop",
            client=httpx.Client(transport=transport, follow_redirects=False),
        )
    assert exc.value.code == "UNSUPPORTED_HOST"


def test_private_ip_redirect_rejected():
    transport = _RedirectTransport({
        "https://maps.app.goo.gl/priv": {"location": "https://192.168.1.10/maps"},
    })
    with pytest.raises(GoogleMapsLinkError) as exc:
        resolve_google_maps_short_link(
            "https://maps.app.goo.gl/priv",
            client=httpx.Client(transport=transport, follow_redirects=False),
        )
    assert exc.value.code == "UNSUPPORTED_HOST"


def test_http_redirect_rejected():
    transport = _RedirectTransport({
        "https://maps.app.goo.gl/http": {"location": "http://www.google.com/maps/@-6.2,106.8,17z"},
    })
    with pytest.raises(GoogleMapsLinkError) as exc:
        resolve_google_maps_short_link(
            "https://maps.app.goo.gl/http",
            client=httpx.Client(transport=transport, follow_redirects=False),
        )
    assert exc.value.code == "INVALID_LINK"


def test_excessive_redirects_rejected():
    mapping = {
        f"https://maps.app.goo.gl/r{i}": {"location": f"https://maps.app.goo.gl/r{i + 1}"}
        for i in range(0, 8)
    }
    mapping["https://maps.app.goo.gl/r0"] = {"location": "https://maps.app.goo.gl/r1"}
    transport = _RedirectTransport(mapping)
    with pytest.raises(GoogleMapsLinkError) as exc:
        resolve_google_maps_short_link(
            "https://maps.app.goo.gl/r0",
            client=httpx.Client(transport=transport, follow_redirects=False),
        )
    assert exc.value.code == "RESOLVE_FAILURE"


def test_timeout_handled():
    transport = _RedirectTransport(
        {},
        delays={"https://maps.app.goo.gl/slow": True},
    )
    with pytest.raises(GoogleMapsLinkError) as exc:
        resolve_google_maps_short_link(
            "https://maps.app.goo.gl/slow",
            client=httpx.Client(transport=transport, follow_redirects=False),
        )
    assert exc.value.code == "RESOLVE_FAILURE"


def test_allowlisted_host_resolving_to_private_ip_rejected(monkeypatch):
    monkeypatch.setattr(maps_link, "resolved_addresses_are_public", lambda host: False)
    transport = _RedirectTransport({
        "https://maps.app.goo.gl/privdns": {
            "location": "https://www.google.com/maps/@-6.2,106.8,17z",
        }
    })
    with pytest.raises(GoogleMapsLinkError) as exc:
        resolve_google_maps_short_link(
            "https://maps.app.goo.gl/privdns",
            client=httpx.Client(transport=transport, follow_redirects=False),
        )
    assert exc.value.code == "UNSUPPORTED_HOST"


def test_malformed_redirect_rejected():
    transport = _RedirectTransport({
        "https://maps.app.goo.gl/bad": {"location": "notaurl"},
    })
    with pytest.raises(GoogleMapsLinkError) as exc:
        resolve_google_maps_short_link(
            "https://maps.app.goo.gl/bad",
            client=httpx.Client(transport=transport, follow_redirects=False),
        )
    assert exc.value.code in {"INVALID_LINK", "RESOLVE_FAILURE"}
