import pathlib
import requests

BASE_URL = next(
    line.split("=", 1)[1].strip()
    for line in pathlib.Path("/app/frontend/.env").read_text().splitlines()
    if line.startswith("REACT_APP_BACKEND_URL=")
).rstrip("/")


def login(email):
    session = requests.Session()
    response = session.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": "Eternal123!"})
    assert response.status_code == 200
    assert response.cookies.get("access_token") is not None
    return session, response.json()


def test_auth_me_and_orders():
    session, user = login("production@eternalpackaging.com")
    assert user["role"] == "head_production"
    assert session.get(f"{BASE_URL}/api/auth/me").json()["role"] == "head_production"
    payload = session.get(f"{BASE_URL}/api/orders").json()
    assert payload["demo_data"] is True and len(payload["orders"]) == 4


def test_unauthenticated_order_mutation_is_forbidden():
    response = requests.patch(f"{BASE_URL}/api/orders/EP-1048", json={"action": "advance"})
    assert response.status_code == 401


def test_accurate_sync_is_demo_labeled_for_admin():
    session, _ = login("admin@eternalpackaging.com")
    response = session.post(f"{BASE_URL}/api/accurate/sync")
    assert response.status_code == 200
    assert response.json()["status"] == "demo"