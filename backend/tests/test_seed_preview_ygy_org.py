"""Safety guards for the manual YGY preview org seed helper."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_preview_ygy_org.py"


def _load():
    spec = importlib.util.spec_from_file_location("seed_preview_ygy_org", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_refuses_non_preview_db(monkeypatch):
    mod = _load()
    monkeypatch.setenv("DB_NAME", "eternal_packaging")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(SystemExit) as exc:
        mod._assert_local_preview()
    assert "eternal_packaging_preview" in str(exc.value)


def test_refuses_production_app_env(monkeypatch):
    mod = _load()
    monkeypatch.setenv("DB_NAME", "eternal_packaging_preview")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(SystemExit) as exc:
        mod._assert_local_preview()
    assert "APP_ENV" in str(exc.value)


def test_accepts_preview_local(monkeypatch):
    mod = _load()
    monkeypatch.setenv("DB_NAME", "eternal_packaging_preview")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
    monkeypatch.delenv("APP_ENV", raising=False)
    url, db = mod._assert_local_preview()
    assert db == "eternal_packaging_preview"
    assert "127.0.0.1" in url
