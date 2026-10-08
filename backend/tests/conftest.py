"""Shared pytest isolation for parallel Mongo-backed tests."""

import os

# Fail-closed package lock: explicit local APP_ENV required for admin package access.
# Individual tests may override APP_ENV (production / unset) via monkeypatch.
os.environ.setdefault("APP_ENV", "test")


def pytest_configure(config):
    """Give each xdist worker its own database before test modules import."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return
    base_name = os.environ.get("DB_NAME", "eternal_packaging_test")
    if not base_name.endswith(f"_{worker}"):
        os.environ["DB_NAME"] = f"{base_name}_{worker}"
