"""Offline integration for sop_belum_unverified_enumerator (no live API)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "repair" / "sop_belum_unverified_enumerator.py"


def _load():
    # Ensure sibling imports (b31_live_validator, propose_*) resolve.
    repair = str(ROOT / "scripts" / "repair")
    if repair not in sys.path:
        sys.path.insert(0, repair)
    spec = importlib.util.spec_from_file_location("sop_belum_unverified_enumerator", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _line(code: str, status: str, *, mid: str | None = None):
    return {
        "item_code": code,
        "master_item_id": mid or f"mid-{code}",
        "qty_pesanan": 1,
        "qty_belum_terkirim": None if status == "BELUM_TERVERIFIKASI" else 0,
        "belum_terkirim_status": status,
    }


def test_login_uses_body_keyword_not_token_positional(monkeypatch):
    m = _load()
    calls = []

    def fake_http(method, url, token=None, body=None):
        calls.append({"method": method, "url": url, "token": token, "body": body})
        return 200, {"access_token": "tok-xyz"}

    monkeypatch.setattr(m, "_http_json", fake_http)
    tok = m._login("http://127.0.0.1:8000", "a@b.c", "secret")
    assert tok == "tok-xyz"
    assert calls[0]["body"] == {"email": "a@b.c", "password": "secret"}
    assert calls[0]["token"] is None


def test_resolve_api_base_prefers_env_then_optional_parser(monkeypatch):
    m = _load()
    assert m._resolve_api_base({"ETERNAL_B31_API_BASE": "http://127.0.0.1:9/"}) == "http://127.0.0.1:9"
    monkeypatch.setattr(m, "_parse_backend_api_base_optional", lambda: "http://127.0.0.1:77")
    assert m._resolve_api_base({}) == "http://127.0.0.1:77"
    monkeypatch.setattr(m, "_parse_backend_api_base_optional", lambda: None)
    with pytest.raises(SystemExit) as exc:
        m._resolve_api_base({})
    assert exc.value.code == 1


def test_schema_fail_closed_missing_status_and_empty_lines():
    m = _load()
    with pytest.raises(ValueError, match="SESSION_LINES_EMPTY"):
        m.validate_session_schema({
            "opname_number": "SOP-X", "id": "s1", "lines": [], "item_count": 0,
        })
    with pytest.raises(ValueError, match="SESSION_LINE_KEYS_MISSING"):
        m.validate_session_schema({
            "opname_number": "SOP-X",
            "id": "s1",
            "lines": [{"item_code": "1", "master_item_id": "m"}],  # no status
            "item_count": 1,
        })
    with pytest.raises(ValueError, match="SESSION_LINE_COUNT_UNEXPECTED"):
        m.validate_session_schema(
            {
                "opname_number": "SOP-20261031-001",
                "id": "s1",
                "lines": [_line("100001", "READY")],
                "item_count": 1,
            },
            expect_lines=401,
        )


def test_401_line_fixture_full_coverage_and_known_residuals(tmp_path):
    m = _load()
    residual = {
        "100031": "BELUM_TERVERIFIKASI",
        "100051": "BELUM_TERVERIFIKASI",
        "100147": "BELUM_TERVERIFIKASI",
        "100388": "BELUM_TERVERIFIKASI",
        "100004": "BELUM_TERVERIFIKASI",
    }
    lines = [_line(code, status) for code, status in residual.items()]
    n = 0
    while len(lines) < 401:
        code = f"9{n:05d}"
        if code not in residual:
            lines.append(_line(code, "READY"))
        n += 1
    assert len(lines) == 401
    assert sum(1 for ln in lines if ln["belum_terkirim_status"] == "BELUM_TERVERIFIKASI") == 5
    session = {
        "id": "sess-1",
        "opname_number": "SOP-20261031-001",
        "mode": "operational",
        "status": "COUNTING",
        "warehouse_id": "wh",
        "branch_id": "br",
        "item_count": 401,
        "lines": lines,
    }
    path = tmp_path / "session.json"
    path.write_text(json.dumps(session), encoding="utf-8")
    out = tmp_path / "out.json"
    rc = m.main([
        "--fixture-session", str(path),
        "--expect-lines", "401",
        "--session", "SOP-20261031-001",
        "--out", str(out),
    ])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["total_skus_examined"] == 401
    assert payload["unverified_sku_count"] == 5
    codes = {r["item_code"] for r in payload["unverified"]}
    assert codes == set(residual)
    assert payload["sku_100004"]["reason_code"] == "NEEDS_LIVE_ORDER_DRILLDOWN"
    assert payload["sku_100004"]["classification"] == "G"
    by = {r["item_code"]: r for r in payload["unverified"]}
    assert by["100031"]["fix_batch"] == "B32"
    assert by["100147"]["fix_batch"] == "B32"
    assert len(payload["c_no_print_hold_order_ids"]) == 2


def test_main_fail_closed_on_bad_schema_fixture(tmp_path):
    m = _load()
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"opname_number": "SOP-X", "lines": []}), encoding="utf-8")
    assert m.main(["--fixture-session", str(path)]) == 2
