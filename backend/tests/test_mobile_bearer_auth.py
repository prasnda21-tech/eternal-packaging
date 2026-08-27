"""Mobile auth: login returns JWT for Bearer use; cookie web path unchanged."""
import os
from pathlib import Path

import pytest
import requests


def _backend_url() -> str:
    env = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
    if env:
        return env
    return "http://localhost:8000"


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
API = f"{BASE_URL.rstrip('/')}/api"
PASSWORD = _trial_password()
ADMIN_EMAIL = "admin@eternalpackaging.com"


@pytest.fixture(scope="module")
def live():
    try:
        r = requests.get(f"{API}/system/health", timeout=5)
    except requests.RequestException:
        pytest.skip("local backend not running")
    if r.status_code != 200:
        pytest.skip("local backend health not 200")
    if not PASSWORD:
        pytest.skip("canonical local trial password not configured")
    return True


@pytest.fixture(scope="module")
def login_body(live):
    r = requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, r.text[:200]
    return r


def test_login_still_sets_httpOnly_cookie(login_body):
    cookie = login_body.headers.get("set-cookie") or ""
    assert "access_token=" in cookie


def test_login_returns_access_token_for_mobile(login_body):
    data = login_body.json()
    assert data.get("email") == ADMIN_EMAIL
    assert data.get("role")
    token = data.get("access_token")
    assert token and isinstance(token, str) and len(token) > 20


def test_bearer_token_reaches_auth_me(login_body):
    token = login_body.json()["access_token"]
    # No cookies — mobile Authorization header only.
    r = requests.get(
        f"{API}/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text[:200]
    me = r.json()
    assert me.get("email") == ADMIN_EMAIL
    assert isinstance(me.get("permissions"), list)
    assert me.get("permissions")


def test_invalid_bearer_token_rejected(live):
    r = requests.get(
        f"{API}/auth/me",
        headers={"Authorization": "Bearer not-a-valid-jwt"},
        timeout=15,
    )
    assert r.status_code == 401


def test_login_body_does_not_include_password(login_body):
    data = login_body.json()
    assert "password" not in data
    assert "password_hash" not in data
