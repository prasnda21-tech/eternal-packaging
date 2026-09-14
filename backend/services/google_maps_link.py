"""Parse and safely resolve Google Maps location URLs.

The resolver exists only for recognized Google Maps hosts (short share links).
It is not a generic URL fetch / proxy.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx

EXACT_HOSTS = frozenset({"maps.app.goo.gl", "goo.gl"})
MAX_REDIRECTS = 5
RESOLVE_TIMEOUT = 5.0
ALLOWED_PORT = {None, "", "443"}

COORD_PAIR = re.compile(r"^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$")
AT_COORDS = re.compile(r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")
BANG_COORDS = re.compile(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)")
PATH_COORDS = re.compile(r"(?:^|/)(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)(?:/|$)")
QUERY_KEYS = ("q", "query", "ll", "destination", "daddr", "sll")

INVALID_LINK = "INVALID_LINK"
UNSUPPORTED_HOST = "UNSUPPORTED_HOST"
NO_COORDINATES = "NO_COORDINATES"
RESOLVE_FAILURE = "RESOLVE_FAILURE"

USER_MESSAGES = {
    INVALID_LINK: "Link Google Maps tidak valid.",
    UNSUPPORTED_HOST: "Gunakan link lokasi dari Google Maps.",
    NO_COORDINATES: "Titik lokasi tidak ditemukan dari link ini.",
    RESOLVE_FAILURE: "Link Google Maps tidak dapat diproses. Coba salin ulang link dari Google Maps.",
}


class GoogleMapsLinkError(ValueError):
    def __init__(self, code: str, message: Optional[str] = None):
        self.code = code
        self.message = message or USER_MESSAGES.get(code, USER_MESSAGES[RESOLVE_FAILURE])
        super().__init__(self.message)


@dataclass(frozen=True)
class MapsLinkResult:
    status: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    url: Optional[str] = None


def is_valid_maps_coordinate(lat: object, lng: object) -> bool:
    try:
        la = float(lat)
        ln = float(lng)
    except (TypeError, ValueError):
        return False
    if la != la or ln != ln:  # NaN
        return False
    return -90.0 <= la <= 90.0 and -180.0 <= ln <= 180.0


def _host_allowed(hostname: str) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    if host in EXACT_HOSTS:
        return True
    return host == "google.com" or host.endswith(".google.com")


def _is_short_host(hostname: str) -> bool:
    return (hostname or "").strip().lower() in {"maps.app.goo.gl", "goo.gl"}


def _ip_is_public(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return bool(ip.is_global)


def resolved_addresses_are_public(hostname: str) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return _ip_is_public(host)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise GoogleMapsLinkError(RESOLVE_FAILURE) from exc
    if not infos:
        raise GoogleMapsLinkError(RESOLVE_FAILURE)
    for info in infos:
        addr = info[4][0]
        if not _ip_is_public(addr):
            return False
    return True


def _coord_pair(text: str) -> Optional[MapsLinkResult]:
    match = COORD_PAIR.match((text or "").strip())
    if not match:
        return None
    lat = float(match.group(1))
    lng = float(match.group(2))
    if not is_valid_maps_coordinate(lat, lng):
        return MapsLinkResult(status=INVALID_LINK)
    return MapsLinkResult(status="OK", lat=lat, lng=lng)


def _extract_from_href(href: str) -> Optional[MapsLinkResult]:
    bang = BANG_COORDS.search(href or "")
    if bang:
        lat = float(bang.group(1))
        lng = float(bang.group(2))
        if not is_valid_maps_coordinate(lat, lng):
            return MapsLinkResult(status=INVALID_LINK)
        return MapsLinkResult(status="OK", lat=lat, lng=lng)
    at = AT_COORDS.search(href or "")
    if at:
        lat = float(at.group(1))
        lng = float(at.group(2))
        if not is_valid_maps_coordinate(lat, lng):
            return MapsLinkResult(status=INVALID_LINK)
        return MapsLinkResult(status="OK", lat=lat, lng=lng)
    return None


def assert_allowed_maps_url(raw: str) -> str:
    trimmed = (raw or "").strip()
    if not trimmed:
        raise GoogleMapsLinkError(INVALID_LINK)
    parsed = urlparse(trimmed)
    if parsed.scheme != "https":
        raise GoogleMapsLinkError(INVALID_LINK)
    if parsed.username or parsed.password:
        raise GoogleMapsLinkError(INVALID_LINK)
    if parsed.port not in ALLOWED_PORT and str(parsed.port) not in {"443"}:
        raise GoogleMapsLinkError(UNSUPPORTED_HOST)
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise GoogleMapsLinkError(UNSUPPORTED_HOST)
    try:
        if parsed.hostname and not _ip_is_public(str(ipaddress.ip_address(parsed.hostname))):
            raise GoogleMapsLinkError(UNSUPPORTED_HOST)
    except ValueError:
        pass
    if not _host_allowed(host):
        raise GoogleMapsLinkError(UNSUPPORTED_HOST)
    if host == "goo.gl" and not (parsed.path or "").lower().startswith("/maps"):
        raise GoogleMapsLinkError(UNSUPPORTED_HOST)
    return trimmed


def inspect_google_maps_url(raw: str) -> MapsLinkResult:
    trimmed = assert_allowed_maps_url(raw)
    parsed = urlparse(trimmed)

    from_href = _extract_from_href(trimmed)
    if from_href is not None:
        if from_href.status != "OK":
            raise GoogleMapsLinkError(INVALID_LINK)
        return MapsLinkResult(status="OK", lat=from_href.lat, lng=from_href.lng, url=trimmed)

    query = parse_qs(parsed.query or "")
    saw_invalid = False
    for key in QUERY_KEYS:
        for value in query.get(key) or []:
            pair = _coord_pair(unquote(value))
            if pair is None:
                continue
            if pair.status != "OK":
                saw_invalid = True
                continue
            return MapsLinkResult(status="OK", lat=pair.lat, lng=pair.lng, url=trimmed)
    if saw_invalid:
        raise GoogleMapsLinkError(INVALID_LINK)

    path_match = PATH_COORDS.search(unquote(parsed.path or ""))
    if path_match:
        lat = float(path_match.group(1))
        lng = float(path_match.group(2))
        if not is_valid_maps_coordinate(lat, lng):
            raise GoogleMapsLinkError(INVALID_LINK)
        return MapsLinkResult(status="OK", lat=lat, lng=lng, url=trimmed)

    if _is_short_host(parsed.hostname or ""):
        return MapsLinkResult(status="NEEDS_RESOLVE", url=trimmed)
    raise GoogleMapsLinkError(NO_COORDINATES)


def parse_google_maps_coordinates(raw: str) -> Optional[dict]:
    try:
        result = inspect_google_maps_url(raw)
    except GoogleMapsLinkError:
        return None
    if result.status != "OK" or result.lat is None or result.lng is None:
        return None
    return {"lat": result.lat, "lng": result.lng}


def _validate_hop(url: str) -> str:
    current = assert_allowed_maps_url(url)
    host = urlparse(current).hostname or ""
    if not resolved_addresses_are_public(host):
        raise GoogleMapsLinkError(UNSUPPORTED_HOST)
    return current


def resolve_google_maps_short_link(
    raw: str,
    *,
    client: Optional[httpx.Client] = None,
    transport=None,
) -> MapsLinkResult:
    """Follow allowlisted HTTPS redirects only. Never download an arbitrary body."""
    current = (raw or "").strip()
    inspected = inspect_google_maps_url(current)
    if inspected.status == "OK":
        return inspected

    own_client = client is None
    http = client or httpx.Client(
        transport=transport,
        follow_redirects=False,
        timeout=RESOLVE_TIMEOUT,
        headers={"User-Agent": "EternalPackaging-MapsLinkResolver/1.0"},
    )
    try:
        hops = 0
        while hops < MAX_REDIRECTS:
            _validate_hop(current)
            try:
                with http.stream("GET", current) as response:
                    location = response.headers.get("location")
                    status = response.status_code
            except httpx.TimeoutException as exc:
                raise GoogleMapsLinkError(RESOLVE_FAILURE) from exc
            except httpx.RequestError as exc:
                raise GoogleMapsLinkError(RESOLVE_FAILURE) from exc

            if status in {301, 302, 303, 307, 308}:
                if not location:
                    raise GoogleMapsLinkError(RESOLVE_FAILURE)
                nxt = urlparse(location)
                if not nxt.scheme:
                    if not location.startswith("/"):
                        raise GoogleMapsLinkError(INVALID_LINK)
                    base = urlparse(current)
                    location = f"{base.scheme}://{base.netloc}{location}"
                try:
                    hop = inspect_google_maps_url(location)
                except GoogleMapsLinkError:
                    raise
                if hop.status == "OK":
                    return hop
                current = location
                hops += 1
                continue

            if 200 <= status < 300:
                hop = inspect_google_maps_url(current)
                if hop.status == "OK":
                    return hop
                raise GoogleMapsLinkError(NO_COORDINATES)
            raise GoogleMapsLinkError(RESOLVE_FAILURE)
        raise GoogleMapsLinkError(RESOLVE_FAILURE)
    finally:
        if own_client:
            http.close()
