"""Local filesystem storage + provider selection. No live S3 / Emergent / production."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.providers.local import LocalFilesystemStorageProvider, sanitize_storage_key
from services.providers.s3 import S3StorageProvider
from services.providers.emergent import EmergentStorageProvider
from services import storage as obj_storage


JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xd9"
)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


@pytest.fixture
def local_root(tmp_path):
    return tmp_path / "store"


@pytest.fixture
def provider(local_root):
    p = LocalFilesystemStorageProvider(root=local_root)
    p.init()
    return p


def test_put_and_get_jpeg(provider, local_root):
    meta = provider.put_object("eternal-packaging/delivery-proof/PO-1/a.jpg", JPEG, "image/jpeg")
    assert meta["path"] == "eternal-packaging/delivery-proof/PO-1/a.jpg"
    assert meta["size"] == len(JPEG)
    data, _ct = provider.get_object(meta["path"])
    assert data == JPEG
    assert (local_root / "eternal-packaging" / "delivery-proof" / "PO-1" / "a.jpg").is_file()


def test_put_png(provider):
    meta = provider.put_object("eternal-packaging/delivery-proof/PO-1/b.png", PNG, "image/png")
    data, _ = provider.get_object(meta["path"])
    assert data == PNG


def test_path_traversal_rejected(provider, local_root):
    with pytest.raises(ValueError):
        provider.put_object("../../etc/passwd", JPEG, "image/jpeg")
    with pytest.raises(ValueError):
        sanitize_storage_key("foo/../../secret")
    # Nothing written outside root
    assert list(local_root.rglob("*")) == [] or all(
        str(p).startswith(str(local_root)) for p in local_root.rglob("*")
    )


def test_dotdot_segment_rejected(provider):
    with pytest.raises(ValueError):
        provider.put_object("eternal-packaging/delivery-proof/../escape.jpg", JPEG, "image/jpeg")


def test_missing_object_raises(provider):
    with pytest.raises(FileNotFoundError):
        provider.get_object("eternal-packaging/delivery-proof/missing.jpg")


def test_selects_local_when_provider_local(monkeypatch):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017")
    obj_storage._reset_provider_for_tests()
    p = obj_storage._select_provider()
    assert p.name == "local"
    obj_storage._reset_provider_for_tests()


def test_selects_s3_when_configured(monkeypatch):
    monkeypatch.setenv("STORAGE_PROVIDER", "s3")
    monkeypatch.setenv("S3_BUCKET", "biznet-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "id-not-used")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "secret-not-used")
    monkeypatch.setenv("MONGO_URL", "mongodb://prod.example:27017")
    obj_storage._reset_provider_for_tests()
    p = obj_storage._select_provider()
    assert p.name == "s3"
    assert isinstance(p, S3StorageProvider)
    obj_storage._reset_provider_for_tests()


def test_selects_emergent_when_key_present(monkeypatch):
    monkeypatch.setenv("STORAGE_PROVIDER", "emergent")
    monkeypatch.setenv("EMERGENT_LLM_KEY", "not-a-real-key")
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.setenv("MONGO_URL", "mongodb://prod.example:27017")
    obj_storage._reset_provider_for_tests()
    p = obj_storage._select_provider()
    assert p.name == "emergent"
    assert isinstance(p, EmergentStorageProvider)
    obj_storage._reset_provider_for_tests()


def test_local_mongo_without_remote_creds_uses_filesystem(monkeypatch):
    monkeypatch.setenv("STORAGE_PROVIDER", "emergent")
    monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017")
    obj_storage._reset_provider_for_tests()
    p = obj_storage._select_provider()
    assert p.name == "local"
    obj_storage._reset_provider_for_tests()


def test_s3_is_configured_requires_bucket_and_keys(monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("S3_SECRET_ACCESS_KEY", raising=False)
    assert S3StorageProvider().is_configured() is False
    monkeypatch.setenv("S3_BUCKET", "b")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "a")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "s")
    assert S3StorageProvider().is_configured() is True
