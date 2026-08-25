"""Browser-origin auth: CORS + cookie session for local preview.

Direct API login can be 200 while the UI stays logged out if the session
cookie is not sent on GET /api/auth/me. These checks use the frontend
preview origin without printing credentials.
"""
import os
from pathlib import Path

import pytest
import requests

FRONTEND_ORIGIN = "http://localhost:3002"


def _backend_url() -> str:
    env = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
    if env:
        return env
    return "http://127.0.0.1:8000"


def _trial_password() -> str:
    explicit = os.environ.get("TRIAL_DEFAULT_PASSWORD")
    if explicit:
        return explicit
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("TRIAL_DEFAULT_PASSWORD="):
                return line.split("=", 1)[1].strip()
    return ""


BASE_URL = _backend_url()
PASSWORD = _trial_password()
ADMIN_EMAIL = "admin@eternalpackaging.com"


def _origin_headers():
    return {"Origin": FRONTEND_ORIGIN}


@pytest.fixture(scope="module")
def live():
    try:
        r = requests.get(f"{BASE_URL}/api/system/health", timeout=5)
    except requests.RequestException:
        pytest.skip("local backend not running")
    if r.status_code != 200:
        pytest.skip("local backend health not 200")
    if not PASSWORD:
        pytest.skip("canonical local trial password not configured")
    return True


def test_cors_preflight_allows_localhost_3002(live):
    r = requests.options(
        f"{BASE_URL}/api/auth/login",
        headers={
            **_origin_headers(),
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
        timeout=10,
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == FRONTEND_ORIGIN
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_browser_origin_login_then_me_with_cookie(live):
    session = requests.Session()
    login = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": PASSWORD},
        headers=_origin_headers(),
        timeout=10,
    )
    assert login.status_code == 200, login.text[:200]
    assert login.json().get("role") == "admin"
    assert login.headers.get("access-control-allow-origin") == FRONTEND_ORIGIN
    assert login.headers.get("access-control-allow-credentials") == "true"
    assert "access_token" in (login.headers.get("set-cookie") or "")

    me = session.get(f"{BASE_URL}/api/auth/me", headers=_origin_headers(), timeout=10)
    assert me.status_code == 200, me.text[:200]
    body = me.json()
    assert body.get("email") == ADMIN_EMAIL
    assert body.get("role") == "admin"
    assert body.get("permissions")

    logout = session.post(
        f"{BASE_URL}/api/auth/logout",
        headers=_origin_headers(),
        timeout=10,
    )
    assert logout.status_code == 200
    me_after = session.get(f"{BASE_URL}/api/auth/me", headers=_origin_headers(), timeout=10)
    assert me_after.status_code == 401


def test_me_without_cookie_is_unauthorized(live):
    r = requests.get(f"{BASE_URL}/api/auth/me", headers=_origin_headers(), timeout=10)
    assert r.status_code == 401
