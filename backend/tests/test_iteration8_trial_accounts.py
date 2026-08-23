"""Iteration 8: Verify trial accounts (12 emails), role mapping, bcrypt storage,
   no user enumeration, permission unchanged, and no plaintext password in frontend."""
import os
import re
import pathlib
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fallback to reading frontend .env
    envp = pathlib.Path("/app/frontend/.env")
    for line in envp.read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")

TRIAL_PWD = "Eternal123!"

TRIAL_ACCOUNTS = [
    ("admin@eternalpackaging.com", "admin"),
    ("production@eternalpackaging.com", "head_production"),
    ("operasional@eternalpackaging.com", "head_production"),
    ("designer@eternalpackaging.com", "designer"),
    ("screener@eternalpackaging.com", "screener"),
    ("helper@eternalpackaging.com", "helper"),
    ("helper.persiapan@eternalpackaging.com", "helper"),
    ("helper.packing@eternalpackaging.com", "helper"),
    ("operator@eternalpackaging.com", "operator"),
    ("operator2@eternalpackaging.com", "operator"),
    ("driver@eternalpackaging.com", "driver"),
    ("crm@eternalpackaging.com", "crm"),
]


def login(email, password=TRIAL_PWD):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    return r, s


# --- Login + role mapping ---
@pytest.mark.parametrize("email,expected_role", TRIAL_ACCOUNTS)
def test_login_and_role(email, expected_role):
    r, s = login(email)
    assert r.status_code == 200, f"login {email} -> {r.status_code} {r.text}"
    me = s.get(f"{BASE_URL}/api/auth/me")
    assert me.status_code == 200
    data = me.json()
    assert data["email"] == email
    assert data["role"] == expected_role, f"{email} role={data['role']} exp={expected_role}"
    assert data.get("name"), "name should be non-empty"


# --- Wrong password + non-existent user return same 401 ---
def test_wrong_password_returns_401():
    r, _ = login("admin@eternalpackaging.com", "wrongpass!!")
    assert r.status_code == 401


def test_nonexistent_user_returns_401_same_message():
    r_wrong, _ = login("admin@eternalpackaging.com", "wrongpass!!")
    r_none, _ = login("nobody-xyz@eternalpackaging.com", TRIAL_PWD)
    assert r_none.status_code == 401
    # generic message parity (no enumeration)
    assert r_none.json().get("detail") == r_wrong.json().get("detail")


# --- BCrypt storage in DB ---
def test_bcrypt_password_hash_in_db():
    async def run():
        env = {}
        for line in pathlib.Path("/app/backend/.env").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
        mongo_url = os.environ.get("MONGO_URL") or env.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME") or env.get("DB_NAME")
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        try:
            for email, _ in TRIAL_ACCOUNTS:
                u = await db.users.find_one({"email": email})
                assert u is not None, f"user {email} missing in db"
                ph = u.get("password_hash", "")
                assert isinstance(ph, str) and len(ph) >= 60, f"{email} hash too short"
                assert ph.startswith("$2a$") or ph.startswith("$2b$") or ph.startswith("$2y$"), \
                    f"{email} hash not bcrypt: {ph[:6]}"
                # No plaintext stored anywhere
                assert TRIAL_PWD not in ph
                assert u.get("password") in (None, "")  # no plaintext key
        finally:
            client.close()
    asyncio.get_event_loop().run_until_complete(run())


# --- No plaintext password in frontend source or build ---
def test_no_plaintext_password_in_frontend():
    roots = [pathlib.Path("/app/frontend/src")]
    build = pathlib.Path("/app/frontend/build")
    if build.exists():
        roots.append(build)
    exts = {".js", ".jsx", ".ts", ".tsx", ".json", ".html", ".css"}
    hits = []
    for root in roots:
        for p in root.rglob("*"):
            if p.suffix.lower() in exts and p.is_file():
                try:
                    txt = p.read_text(errors="ignore")
                except Exception:
                    continue
                if TRIAL_PWD in txt:
                    hits.append(str(p))
    assert not hits, f"Plaintext password leaked in: {hits}"


def test_login_jsx_no_prefill():
    txt = pathlib.Path("/app/frontend/src/pages/Login.jsx").read_text()
    # useState("") for both email and password
    assert re.search(r'useState\(\s*"\s*"\s*\)', txt), "expected empty useState defaults"
    # no autofill value attr containing @
    assert "@eternalpackaging.com" not in txt


# --- Permission unchanged ---
def test_operasional_can_access_dashboard_and_reminders():
    r, s = login("operasional@eternalpackaging.com")
    assert r.status_code == 200
    d = s.get(f"{BASE_URL}/api/dashboard/summary")
    assert d.status_code == 200, d.text
    rem = s.get(f"{BASE_URL}/api/reminders")
    assert rem.status_code == 200, rem.text


def _get_order_at_stage(session, stage):
    r = session.get(f"{BASE_URL}/api/orders")
    assert r.status_code == 200
    body = r.json()
    items = body.get("orders", body) if isinstance(body, dict) else body
    for o in items:
        if o.get("current_stage") == stage:
            return o
    return None


def test_helper_packing_cannot_advance_screener_stage_6():
    # login as admin to find/prep an order at stage 6 if any
    _, admin_s = login("admin@eternalpackaging.com")
    assert _.status_code == 200
    order = _get_order_at_stage(admin_s, 6)
    if not order:
        pytest.skip("no order currently at stage 6 (screener)")
    _, hp = login("helper.packing@eternalpackaging.com")
    r = hp.post(f"{BASE_URL}/api/orders/{order['id']}/advance", json={"note": "try"})
    assert r.status_code == 403, f"expected 403, got {r.status_code} {r.text}"


def test_helper_persiapan_role_is_helper():
    r, s = login("helper.persiapan@eternalpackaging.com")
    assert r.status_code == 200
    assert s.get(f"{BASE_URL}/api/auth/me").json()["role"] == "helper"
