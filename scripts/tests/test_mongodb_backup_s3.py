"""Focused mocked tests for MongoDB backup S3 retention hardening.

No real Biznet S3. No real MongoDB.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from lib.mongodb_backup_s3 import (  # noqa: E402
    DEFAULT_EXACT_PREFIX,
    DEFAULT_PROTECTED_NAME,
    BackupConfig,
    BackupS3Error,
    abort_incomplete_multiparts,
    build_retention_plan,
    enrich_checksum_metadata,
    list_backup_objects,
    pre_upload_eligible_keys,
    retain_local_backups,
    run_s3_backup_phase,
    sha256_file,
)
from tests.fake_s3 import FakeS3, _Multipart  # noqa: E402

BUCKET = "eternal-packaging-prod"
PREFIX = DEFAULT_EXACT_PREFIX
PROTECTED = DEFAULT_PROTECTED_NAME


def _cfg(**kwargs) -> BackupConfig:
    base = dict(
        bucket=BUCKET,
        exact_prefix=PREFIX,
        keep=3,
        protected_name=PROTECTED,
        safety_reserve_bytes=0,
        assumed_capacity_bytes=None,
        abort_incomplete_multipart=False,
    )
    base.update(kwargs)
    return BackupConfig(**base)


def _seed(
    s3: FakeS3,
    items: list[tuple[str, bytes, datetime, dict | None]],
) -> None:
    for name, body, modified, meta in items:
        s3.put_bytes(
            BUCKET,
            f"{PREFIX}{name}",
            body,
            metadata=meta,
            last_modified=modified,
        )


def _write_archive(path: Path, payload: bytes = b"backup-bytes") -> Path:
    # Minimal gzip-like content not required for S3 helper; shell tests gzip separately.
    path.write_bytes(payload)
    return path


@pytest.fixture
def tmp_archive(tmp_path: Path) -> Path:
    return _write_archive(tmp_path / "eternal_packaging_2026-10-08_02-30-00.archive.gz", b"NEW-BACKUP-DATA")


def test_01_successful_daily_backup_and_retention(tmp_archive: Path):
    s3 = FakeS3()
    _seed(
        s3,
        [
            (
                "eternal_packaging_2026-10-01_02-30-00.archive.gz",
                b"d1",
                datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-02_02-30-00.archive.gz",
                b"d2",
                datetime(2026, 10, 2, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-03_02-30-00.archive.gz",
                b"d3",
                datetime(2026, 10, 3, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-04_02-30-00.archive.gz",
                b"d4",
                datetime(2026, 10, 4, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                PROTECTED,
                b"checkpoint",
                datetime(2026, 9, 30, 8, 11, 33, tzinfo=timezone.utc),
                None,
            ),
        ],
    )
    # Also seed operational object outside prefix
    s3.put_bytes(BUCKET, "production/customers/file.pdf", b"ops")

    summary = run_s3_backup_phase(
        s3,
        local_path=tmp_archive,
        backup_name=tmp_archive.name,
        config=_cfg(),
    )
    assert summary["status"] == "SUCCESS"
    keys = set(s3._bucket(BUCKET).keys())
    assert f"{PREFIX}{tmp_archive.name}" in keys
    assert f"{PREFIX}{PROTECTED}" in keys
    assert "production/customers/file.pdf" in keys
    # Keep 3 daily + protected
    daily = [k for k in keys if k.startswith(PREFIX) and not k.endswith(PROTECTED)]
    assert len(daily) == 3
    assert f"{PREFIX}eternal_packaging_2026-10-01_02-30-00.archive.gz" not in keys


def test_02_quota_exceeded_no_safe_prune(tmp_archive: Path):
    s3 = FakeS3()
    s3.quota_bytes = 10
    _seed(
        s3,
        [
            (
                "eternal_packaging_2026-10-07_02-30-00.archive.gz",
                b"1234567890",  # fills quota
                datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc),
                None,
            ),
        ],
    )
    with pytest.raises(BackupS3Error, match="QuotaExceeded|no safe"):
        run_s3_backup_phase(
            s3,
            local_path=tmp_archive,
            backup_name=tmp_archive.name,
            config=_cfg(),
        )
    # Newest sole backup must remain
    assert f"{PREFIX}eternal_packaging_2026-10-07_02-30-00.archive.gz" in s3._bucket(BUCKET)


def test_03_newest_remote_protected_pre_upload():
    s3 = FakeS3()
    _seed(
        s3,
        [
            (
                "eternal_packaging_2026-10-05_02-30-00.archive.gz",
                b"old",
                datetime(2026, 10, 5, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-06_02-30-00.archive.gz",
                b"mid",
                datetime(2026, 10, 6, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-07_02-30-00.archive.gz",
                b"new",
                datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-04_02-30-00.archive.gz",
                b"older",
                datetime(2026, 10, 4, 2, 30, tzinfo=timezone.utc),
                None,
            ),
        ],
    )
    objs = list_backup_objects(s3, BUCKET, PREFIX)
    eligible = pre_upload_eligible_keys(
        objs, exact_prefix=PREFIX, keep=3, protected_name=PROTECTED
    )
    newest = f"{PREFIX}eternal_packaging_2026-10-07_02-30-00.archive.gz"
    assert newest not in eligible
    # With keep=3, preserve keep-1=2 newest → eligible starts after that
    assert f"{PREFIX}eternal_packaging_2026-10-04_02-30-00.archive.gz" in eligible


def test_04_historical_checkpoint_protected(tmp_archive: Path):
    s3 = FakeS3()
    _seed(
        s3,
        [
            (
                PROTECTED,
                b"checkpoint",
                datetime(2026, 9, 30, 8, 11, 33, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-01_02-30-00.archive.gz",
                b"a",
                datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-02_02-30-00.archive.gz",
                b"b",
                datetime(2026, 10, 2, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-03_02-30-00.archive.gz",
                b"c",
                datetime(2026, 10, 3, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-04_02-30-00.archive.gz",
                b"d",
                datetime(2026, 10, 4, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-05_02-30-00.archive.gz",
                b"e",
                datetime(2026, 10, 5, 2, 30, tzinfo=timezone.utc),
                None,
            ),
        ],
    )
    run_s3_backup_phase(
        s3,
        local_path=tmp_archive,
        backup_name=tmp_archive.name,
        config=_cfg(),
    )
    assert f"{PREFIX}{PROTECTED}" in s3._bucket(BUCKET)
    assert f"{PREFIX}{PROTECTED}" not in s3.deleted_keys


def test_05_only_three_daily_retained(tmp_archive: Path):
    s3 = FakeS3()
    for day in range(1, 8):
        _seed(
            s3,
            [
                (
                    f"eternal_packaging_2026-10-0{day}_02-30-00.archive.gz"
                    if day < 10
                    else f"eternal_packaging_2026-10-{day}_02-30-00.archive.gz",
                    f"d{day}".encode(),
                    datetime(2026, 10, day, 2, 30, tzinfo=timezone.utc),
                    None,
                )
            ],
        )
    run_s3_backup_phase(
        s3,
        local_path=tmp_archive,
        backup_name=tmp_archive.name,
        config=_cfg(keep=3),
    )
    daily = [
        k
        for k in s3._bucket(BUCKET)
        if k.startswith(PREFIX) and PROTECTED not in k
    ]
    assert len(daily) == 3


def test_06_failed_upload_after_safe_pre_pruning(tmp_path: Path):
    local = _write_archive(tmp_path / "eternal_packaging_2026-10-08_03-00-00.archive.gz", b"X" * 50)
    s3 = FakeS3()
    # Assumed capacity forces pre-prune; upload still fails
    _seed(
        s3,
        [
            (
                "eternal_packaging_2026-10-01_02-30-00.archive.gz",
                b"Y" * 40,
                datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-05_02-30-00.archive.gz",
                b"A" * 10,
                datetime(2026, 10, 5, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-06_02-30-00.archive.gz",
                b"B" * 10,
                datetime(2026, 10, 6, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-07_02-30-00.archive.gz",
                b"C" * 10,
                datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc),
                None,
            ),
        ],
    )
    s3.fail_upload_always = True
    with pytest.raises(BackupS3Error):
        run_s3_backup_phase(
            s3,
            local_path=local,
            backup_name=local.name,
            config=_cfg(assumed_capacity_bytes=80, safety_reserve_bytes=0),
        )
    # Newest must still exist
    assert f"{PREFIX}eternal_packaging_2026-10-07_02-30-00.archive.gz" in s3._bucket(BUCKET)
    # Local archive preserved by caller; helper does not delete local
    assert local.is_file()


def test_07_remote_checksum_mismatch(tmp_archive: Path):
    class CorruptAfterUpload(FakeS3):
        def upload_file(self, Filename, Bucket, Key, ExtraArgs=None, **kwargs):
            super().upload_file(Filename, Bucket, Key, ExtraArgs=ExtraArgs, **kwargs)
            # Corrupt stored metadata so verify fails
            self._bucket(Bucket)[Key].metadata["sha256"] = "deadbeef" * 8

    s3 = CorruptAfterUpload()
    with pytest.raises(BackupS3Error, match="sha256"):
        run_s3_backup_phase(
            s3,
            local_path=tmp_archive,
            backup_name=tmp_archive.name,
            config=_cfg(),
        )


def test_08_existing_object_different_content(tmp_archive: Path):
    s3 = FakeS3()
    digest = sha256_file(tmp_archive)
    s3.put_bytes(
        BUCKET,
        f"{PREFIX}{tmp_archive.name}",
        b"OTHER-CONTENT-DIFFERENT",
        metadata={"sha256": "a" * 64},
    )
    with pytest.raises(BackupS3Error, match="different content"):
        run_s3_backup_phase(
            s3,
            local_path=tmp_archive,
            backup_name=tmp_archive.name,
            config=_cfg(),
            local_sha256=digest,
        )


def test_09_interrupted_retried_upload_idempotent(tmp_archive: Path):
    s3 = FakeS3()
    digest = sha256_file(tmp_archive)
    # First run succeeds
    run_s3_backup_phase(
        s3,
        local_path=tmp_archive,
        backup_name=tmp_archive.name,
        config=_cfg(),
        local_sha256=digest,
    )
    calls_after_first = s3.upload_calls
    # Retry same file/name — idempotent hit, no clobber error
    summary = run_s3_backup_phase(
        s3,
        local_path=tmp_archive,
        backup_name=tmp_archive.name,
        config=_cfg(),
        local_sha256=digest,
    )
    assert summary["status"] == "SUCCESS"
    assert summary["idempotent"] is True
    assert s3.upload_calls == calls_after_first


def test_10_failed_object_listing(tmp_archive: Path):
    s3 = FakeS3()
    s3.fail_list = True
    with pytest.raises(BackupS3Error, match="listing failed"):
        run_s3_backup_phase(
            s3,
            local_path=tmp_archive,
            backup_name=tmp_archive.name,
            config=_cfg(),
        )


def test_11_local_backup_failure_empty_file(tmp_path: Path):
    empty = tmp_path / "eternal_packaging_2026-10-08_02-30-00.archive.gz"
    empty.write_bytes(b"")
    s3 = FakeS3()
    with pytest.raises(BackupS3Error, match="empty"):
        run_s3_backup_phase(
            s3,
            local_path=empty,
            backup_name=empty.name,
            config=_cfg(),
        )


def test_12_local_retention_preserves_valid_copies(tmp_path: Path):
    for stamp in [
        "2026-10-01_01-00-00",
        "2026-10-02_01-00-00",
        "2026-10-03_01-00-00",
        "2026-10-04_01-00-00",
        "2026-10-05_01-00-00",
    ]:
        (tmp_path / f"eternal_packaging_{stamp}.archive.gz").write_bytes(b"ok")
    result = retain_local_backups(tmp_path, keep=3)
    assert len(result["kept"]) == 3
    assert result["kept"][0].startswith("eternal_packaging_2026-10-05")
    assert len(result["deleted"]) == 2
    assert len(list(tmp_path.glob("eternal_packaging_*.archive.gz"))) == 3


def test_13_concurrent_backup_lock(tmp_path: Path):
    lock = tmp_path / "backup.lock"
    lock.touch()
    acquired = []

    def holder():
        import fcntl

        with open(lock, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            acquired.append("held")
            time.sleep(0.4)

    t = threading.Thread(target=holder)
    t.start()
    time.sleep(0.05)
    import fcntl

    with open(lock, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got_lock = True
        except BlockingIOError:
            got_lock = False
    t.join()
    assert acquired == ["held"]
    assert got_lock is False


def test_14_operational_prefixes_untouched(tmp_archive: Path):
    s3 = FakeS3()
    s3.put_bytes(BUCKET, "production/delivery/doc.pdf", b"delivery")
    s3.put_bytes(BUCKET, "production/customers/x.bin", b"customer")
    s3.put_bytes(BUCKET, "production/backups/other/not-mongo.gz", b"other")
    run_s3_backup_phase(
        s3,
        local_path=tmp_archive,
        backup_name=tmp_archive.name,
        config=_cfg(),
    )
    assert "production/delivery/doc.pdf" in s3._bucket(BUCKET)
    assert "production/customers/x.bin" in s3._bucket(BUCKET)
    assert "production/backups/other/not-mongo.gz" in s3._bucket(BUCKET)
    assert "production/delivery/doc.pdf" not in s3.deleted_keys


def test_15_legacy_without_checksum_not_auto_invalid(tmp_archive: Path):
    s3 = FakeS3()
    _seed(
        s3,
        [
            (
                "eternal_packaging_2026-10-07_02-30-00.archive.gz",
                b"legacy",
                datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc),
                None,  # no sha256 metadata
            ),
            (
                PROTECTED,
                b"checkpoint",
                datetime(2026, 9, 30, 8, 11, 33, tzinfo=timezone.utc),
                None,
            ),
        ],
    )
    objs = enrich_checksum_metadata(
        s3, BUCKET, list_backup_objects(s3, BUCKET, PREFIX), PREFIX
    )
    legacy = [o for o in objs if o.sha256 is None]
    assert any(o.name == "eternal_packaging_2026-10-07_02-30-00.archive.gz" for o in legacy)
    plan = build_retention_plan(
        objs, exact_prefix=PREFIX, keep=3, protected_name=PROTECTED
    )
    # Legacy sole/newest daily must not be in eligible deletes when only 1 daily
    assert f"{PREFIX}eternal_packaging_2026-10-07_02-30-00.archive.gz" not in plan.eligible_delete_keys
    summary = run_s3_backup_phase(
        s3,
        local_path=tmp_archive,
        backup_name=tmp_archive.name,
        config=_cfg(),
    )
    assert summary["status"] == "SUCCESS"
    # Legacy may remain if within keep window
    assert f"{PREFIX}eternal_packaging_2026-10-07_02-30-00.archive.gz" in s3._bucket(BUCKET)


def test_16_incomplete_multipart_cleanup_disabled_by_default(tmp_archive: Path):
    s3 = FakeS3()
    s3.multiparts.append(
        _Multipart(
            key=f"{PREFIX}orphan.archive.gz",
            upload_id="up1",
            initiated=datetime.now(timezone.utc) - timedelta(days=2),
        )
    )
    run_s3_backup_phase(
        s3,
        local_path=tmp_archive,
        backup_name=tmp_archive.name,
        config=_cfg(abort_incomplete_multipart=False),
    )
    assert s3.aborted_multiparts == []
    # Explicit enable works
    aborted = abort_incomplete_multiparts(
        s3,
        bucket=BUCKET,
        exact_prefix=PREFIX,
        max_age_seconds=3600,
    )
    assert aborted == [f"{PREFIX}orphan.archive.gz"]


def test_assumed_capacity_pre_prune_then_success(tmp_path: Path):
    local = _write_archive(tmp_path / "eternal_packaging_2026-10-08_04-00-00.archive.gz", b"N" * 30)
    s3 = FakeS3()
    _seed(
        s3,
        [
            (
                "eternal_packaging_2026-09-01_02-30-00.archive.gz",
                b"O" * 40,
                datetime(2026, 9, 1, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-06_02-30-00.archive.gz",
                b"A" * 10,
                datetime(2026, 10, 6, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-07_02-30-00.archive.gz",
                b"B" * 10,
                datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                PROTECTED,
                b"C" * 5,
                datetime(2026, 9, 30, 8, 11, 33, tzinfo=timezone.utc),
                None,
            ),
        ],
    )
    # used=65, local=30, capacity=80 → shortfall before prune; eligible old Sept dump
    summary = run_s3_backup_phase(
        s3,
        local_path=local,
        backup_name=local.name,
        config=_cfg(assumed_capacity_bytes=80),
    )
    assert summary["status"] == "SUCCESS"
    assert f"{PREFIX}eternal_packaging_2026-09-01_02-30-00.archive.gz" not in s3._bucket(BUCKET)
    assert f"{PREFIX}{PROTECTED}" in s3._bucket(BUCKET)


def test_etag_not_used_as_md5(tmp_archive: Path):
    """HEAD returns multipart-looking ETag; verification streams content, ignores ETag."""
    s3 = FakeS3()
    summary = run_s3_backup_phase(
        s3,
        local_path=tmp_archive,
        backup_name=tmp_archive.name,
        config=_cfg(),
    )
    assert summary["status"] == "SUCCESS"
    head = s3.head_object(Bucket=BUCKET, Key=f"{PREFIX}{tmp_archive.name}")
    assert "multipart" in head["ETag"]
    assert head["Metadata"]["sha256"] == sha256_file(tmp_archive)


def test_content_corrupt_same_metadata_fails(tmp_archive: Path):
    s3 = FakeS3()
    s3.corrupt_body_after_upload_for = f"{PREFIX}{tmp_archive.name}"
    with pytest.raises(BackupS3Error, match="content sha256 mismatch"):
        run_s3_backup_phase(
            s3,
            local_path=tmp_archive,
            backup_name=tmp_archive.name,
            config=_cfg(),
        )
    # Unverified upload removed fail-closed
    assert f"{PREFIX}{tmp_archive.name}" not in s3._bucket(BUCKET)


def test_same_size_different_bytes_fails(tmp_path: Path):
    local = _write_archive(
        tmp_path / "eternal_packaging_2026-10-08_05-00-00.archive.gz",
        b"AAAA",
    )
    s3 = FakeS3()
    digest = sha256_file(local)
    # Pre-seed same size different bytes with matching false metadata
    s3.put_bytes(
        BUCKET,
        f"{PREFIX}{local.name}",
        b"BBBB",
        metadata={"sha256": digest},  # lying metadata
    )
    with pytest.raises(BackupS3Error, match="content sha256 mismatch|different content"):
        run_s3_backup_phase(
            s3,
            local_path=local,
            backup_name=local.name,
            config=_cfg(),
            local_sha256=digest,
        )


def test_valid_remote_content_pass(tmp_archive: Path):
    s3 = FakeS3()
    summary = run_s3_backup_phase(
        s3,
        local_path=tmp_archive,
        backup_name=tmp_archive.name,
        config=_cfg(),
    )
    assert summary["status"] == "SUCCESS"
    assert summary["local_sha256"] == sha256_file(tmp_archive)


def test_four_plus_checkpoint_then_new_upload(tmp_archive: Path):
    """S3 has 3 daily + checkpoint; new upload keeps 3 daily + checkpoint."""
    s3 = FakeS3()
    _seed(
        s3,
        [
            (PROTECTED, b"ckpt", datetime(2026, 9, 30, 8, 11, 33, tzinfo=timezone.utc), None),
            (
                "eternal_packaging_2026-10-05_02-30-00.archive.gz",
                b"a",
                datetime(2026, 10, 5, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-06_02-30-00.archive.gz",
                b"b",
                datetime(2026, 10, 6, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-07_02-30-00.archive.gz",
                b"c",
                datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc),
                None,
            ),
        ],
    )
    run_s3_backup_phase(
        s3, local_path=tmp_archive, backup_name=tmp_archive.name, config=_cfg()
    )
    keys = set(s3._bucket(BUCKET))
    assert f"{PREFIX}{PROTECTED}" in keys
    daily = [k for k in keys if k.startswith(PREFIX) and not k.endswith(PROTECTED)]
    assert len(daily) == 3
    assert f"{PREFIX}{tmp_archive.name}" in keys
    assert f"{PREFIX}eternal_packaging_2026-10-05_02-30-00.archive.gz" not in keys


def test_quota_one_eligible_older_daily(tmp_path: Path):
    local = _write_archive(
        tmp_path / "eternal_packaging_2026-10-08_06-00-00.archive.gz", b"N" * 20
    )
    s3 = FakeS3()
    s3.quota_bytes = 55  # used 50 until oldest (20) pruned → room for 20
    _seed(
        s3,
        [
            (
                "eternal_packaging_2026-10-01_02-30-00.archive.gz",
                b"O" * 20,
                datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-06_02-30-00.archive.gz",
                b"A" * 10,
                datetime(2026, 10, 6, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-07_02-30-00.archive.gz",
                b"B" * 10,
                datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (PROTECTED, b"C" * 10, datetime(2026, 9, 30, 8, 11, 33, tzinfo=timezone.utc), None),
        ],
    )
    summary = run_s3_backup_phase(
        s3, local_path=local, backup_name=local.name, config=_cfg()
    )
    assert summary["status"] == "SUCCESS"
    assert f"{PREFIX}{PROTECTED}" in s3._bucket(BUCKET)
    assert f"{PREFIX}eternal_packaging_2026-10-07_02-30-00.archive.gz" in s3._bucket(BUCKET)


def test_quota_no_eligible_fail_closed(tmp_archive: Path):
    s3 = FakeS3()
    s3.quota_bytes = 5
    _seed(
        s3,
        [
            (
                "eternal_packaging_2026-10-07_02-30-00.archive.gz",
                b"12345",
                datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (PROTECTED, b"ckpt", datetime(2026, 9, 30, 8, 11, 33, tzinfo=timezone.utc), None),
        ],
    )
    with pytest.raises(BackupS3Error, match="QuotaExceeded|no safe"):
        run_s3_backup_phase(
            s3, local_path=tmp_archive, backup_name=tmp_archive.name, config=_cfg()
        )
    assert f"{PREFIX}{PROTECTED}" in s3._bucket(BUCKET)
    assert f"{PREFIX}eternal_packaging_2026-10-07_02-30-00.archive.gz" in s3._bucket(BUCKET)


def test_upload_fails_after_old_deleted_preserves_newest(tmp_path: Path):
    local = _write_archive(
        tmp_path / "eternal_packaging_2026-10-08_07-00-00.archive.gz", b"X" * 30
    )
    s3 = FakeS3()
    _seed(
        s3,
        [
            (
                "eternal_packaging_2026-10-01_02-30-00.archive.gz",
                b"Y" * 40,
                datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-06_02-30-00.archive.gz",
                b"A" * 10,
                datetime(2026, 10, 6, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-07_02-30-00.archive.gz",
                b"B" * 10,
                datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (PROTECTED, b"ckpt", datetime(2026, 9, 30, 8, 11, 33, tzinfo=timezone.utc), None),
        ],
    )
    s3.fail_upload_always = True
    with pytest.raises(BackupS3Error):
        run_s3_backup_phase(
            s3,
            local_path=local,
            backup_name=local.name,
            config=_cfg(assumed_capacity_bytes=70),
        )
    assert f"{PREFIX}{PROTECTED}" in s3._bucket(BUCKET)
    assert f"{PREFIX}eternal_packaging_2026-10-07_02-30-00.archive.gz" in s3._bucket(BUCKET)
    assert local.is_file()


def test_sole_verified_external_never_deleted(tmp_archive: Path):
    s3 = FakeS3()
    sole = "eternal_packaging_2026-10-07_02-30-00.archive.gz"
    _seed(
        s3,
        [(sole, b"only", datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc), None)],
    )
    elig = pre_upload_eligible_keys(
        list_backup_objects(s3, BUCKET, PREFIX),
        exact_prefix=PREFIX,
        keep=3,
        protected_name=PROTECTED,
    )
    assert f"{PREFIX}{sole}" not in elig
    # Force quota failure path
    s3.quota_bytes = 1
    with pytest.raises(BackupS3Error):
        run_s3_backup_phase(
            s3, local_path=tmp_archive, backup_name=tmp_archive.name, config=_cfg()
        )
    assert f"{PREFIX}{sole}" in s3._bucket(BUCKET)


def test_protected_checkpoint_newest_or_oldest(tmp_archive: Path):
    s3 = FakeS3()
    # Checkpoint is oldest
    _seed(
        s3,
        [
            (PROTECTED, b"oldckpt", datetime(2026, 9, 30, 8, 11, 33, tzinfo=timezone.utc), None),
            (
                "eternal_packaging_2026-10-07_02-30-00.archive.gz",
                b"d",
                datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc),
                None,
            ),
        ],
    )
    run_s3_backup_phase(
        s3, local_path=tmp_archive, backup_name=tmp_archive.name, config=_cfg()
    )
    assert f"{PREFIX}{PROTECTED}" in s3._bucket(BUCKET)

    # Checkpoint artificially newest by LastModified — still protected
    s3b = FakeS3()
    _seed(
        s3b,
        [
            (
                "eternal_packaging_2026-10-01_02-30-00.archive.gz",
                b"a",
                datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (PROTECTED, b"newckpt", datetime(2026, 10, 9, 0, 0, 0, tzinfo=timezone.utc), None),
        ],
    )
    local2 = _write_archive(
        tmp_archive.parent / "eternal_packaging_2026-10-08_08-00-00.archive.gz", b"Z"
    )
    run_s3_backup_phase(s3b, local_path=local2, backup_name=local2.name, config=_cfg())
    assert f"{PREFIX}{PROTECTED}" in s3b._bucket(BUCKET)
    assert f"{PREFIX}{PROTECTED}" not in s3b.deleted_keys


def test_listing_changes_during_execution(tmp_archive: Path):
    s3 = FakeS3()
    victim = f"{PREFIX}eternal_packaging_2026-10-01_02-30-00.archive.gz"
    _seed(
        s3,
        [
            (
                "eternal_packaging_2026-10-01_02-30-00.archive.gz",
                b"gone",
                datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (
                "eternal_packaging_2026-10-07_02-30-00.archive.gz",
                b"stay",
                datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc),
                None,
            ),
            (PROTECTED, b"ckpt", datetime(2026, 9, 30, 8, 11, 33, tzinfo=timezone.utc), None),
        ],
    )
    s3.list_mutate_remove_key = victim
    summary = run_s3_backup_phase(
        s3, local_path=tmp_archive, backup_name=tmp_archive.name, config=_cfg()
    )
    assert summary["status"] == "SUCCESS"
    assert f"{PREFIX}{PROTECTED}" in s3._bucket(BUCKET)


def test_retry_after_upload_timeout_object_landed(tmp_archive: Path):
    s3 = FakeS3()
    s3.fail_upload_timeout_once = True
    s3.upload_timeout_but_store = True
    summary = run_s3_backup_phase(
        s3, local_path=tmp_archive, backup_name=tmp_archive.name, config=_cfg()
    )
    assert summary["status"] == "SUCCESS"
    assert f"{PREFIX}{tmp_archive.name}" in s3._bucket(BUCKET)


def test_retry_after_upload_timeout_then_retry_upload(tmp_archive: Path):
    s3 = FakeS3()
    s3.fail_upload_timeout_once = True
    s3.upload_timeout_but_store = False
    summary = run_s3_backup_phase(
        s3, local_path=tmp_archive, backup_name=tmp_archive.name, config=_cfg()
    )
    assert summary["status"] == "SUCCESS"
    assert s3.upload_calls >= 2


def test_local_retention_never_deletes_sole_valid(tmp_path: Path):
    only = tmp_path / "eternal_packaging_2026-10-08_01-00-00.archive.gz"
    only.write_bytes(b"solo")
    (tmp_path / "notes.txt").write_text("not a backup")
    result = retain_local_backups(tmp_path, keep=3)
    assert result["kept"] == [only.name]
    assert result["deleted"] == []
    assert only.is_file()


def test_local_retention_ignores_non_mongodb_names(tmp_path: Path):
    (tmp_path / "eternal_packaging_2026-10-08_01-00-00.archive.gz").write_bytes(b"a")
    (tmp_path / "random.gz").write_bytes(b"nope")
    (tmp_path / "production_customers.bin").write_bytes(b"ops")
    retain_local_backups(tmp_path, keep=3)
    assert (tmp_path / "random.gz").is_file()
    assert (tmp_path / "production_customers.bin").is_file()
