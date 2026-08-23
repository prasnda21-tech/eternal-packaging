"""Emergent → S3 migration DRY-RUN utility.

USAGE (DRY-RUN default; no writes, no deletes):
    python -m scripts.migrate_storage_dryrun

WHAT IT DOES
- Enumerates every object referenced by MongoDB (`files.storage_path` +
  `customer_approvals.signature_storage_path`).
- For each object:
    * Determines source key (Emergent) and target key (S3, using S3_PREFIX).
    * Reads source metadata via Emergent provider (HEAD-equivalent = GET).
    * Reports missing objects, duplicate paths, and total bytes/count.
    * Computes SHA-256 of source bytes (integrity plan).
- If `S3_PROVIDER_READY=1` and `--check-target` supplied, ALSO probes S3
  head_object to check whether target already exists (idempotency safeguard).

SAFETY GUARANTEES
- **DRY-RUN ONLY** by default. No PUT/DELETE ever performed.
- To enable actual copy, `--live` flag is required AND `STORAGE_MIGRATE_LIVE=1`
  env must be set. Copy path is intentionally left NOT-IMPLEMENTED for this
  task; it will be added and tested in the next migration step.
- No credential is printed. Provider names and counts only.

INTEGRITY
- SHA-256 hash of each source object is computed on read. After a real copy is
  performed (future step), the same hash MUST be computed on the target
  fetched bytes; migration is only marked success when hashes match. ETag is
  captured additionally for S3 (may differ from SHA-256 due to multipart).

EXIT CODES
    0  dry-run completed with no missing objects / no collisions
    2  missing source object(s) found
    3  target collision detected (target key already exists)
    9  configuration error (provider unavailable)
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

# Ensure backend package on path when running as script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.providers.emergent import EmergentStorageProvider  # noqa: E402
from services.providers.s3 import S3StorageProvider  # noqa: E402

logger = logging.getLogger("migrate_storage_dryrun")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class ObjectRef:
    source_path: str  # canonical source key (Emergent)
    origin: str       # "files" | "customer_approvals"
    ref_id: str       # MongoDB document id (for traceability)
    mime_hint: Optional[str] = None
    # Populated by probes:
    source_present: Optional[bool] = None
    source_size: Optional[int] = None
    source_content_type: Optional[str] = None
    source_sha256: Optional[str] = None
    target_key: Optional[str] = None
    target_present: Optional[bool] = None
    notes: List[str] = field(default_factory=list)


async def enumerate_refs(db) -> List[ObjectRef]:
    refs: List[ObjectRef] = []
    async for f in db.files.find({}, {"_id": 0, "id": 1, "storage_path": 1, "mime_type": 1}):
        p = (f.get("storage_path") or "").strip()
        if p:
            refs.append(ObjectRef(source_path=p, origin="files", ref_id=str(f.get("id") or "?"), mime_hint=f.get("mime_type")))
    async for a in db.customer_approvals.find({}, {"_id": 0, "id": 1, "signature_storage_path": 1}):
        p = (a.get("signature_storage_path") or "").strip()
        if p:
            refs.append(ObjectRef(source_path=p, origin="customer_approvals", ref_id=str(a.get("id") or "?")))
    return refs


def _target_key_for(source_path: str) -> str:
    """Preserve object key semantics. Optionally prepend `S3_PREFIX` (handled by
    S3 provider itself when calling put_object with the same path).

    Returns the canonical target key as the caller will pass to the provider —
    i.e. the same string as source_path. Prefix application happens inside the
    S3 provider so MongoDB records don't need updating.
    """
    return source_path


def probe_source(refs: Iterable[ObjectRef], src: EmergentStorageProvider) -> None:
    for r in refs:
        try:
            data, ct = src.get_object(r.source_path)
            r.source_present = True
            r.source_size = len(data)
            r.source_content_type = ct
            r.source_sha256 = hashlib.sha256(data).hexdigest()
        except Exception as e:  # noqa: BLE001
            r.source_present = False
            r.notes.append(f"source-error: {type(e).__name__}")


def probe_target(refs: Iterable[ObjectRef], tgt: S3StorageProvider) -> None:
    for r in refs:
        r.target_key = _target_key_for(r.source_path)
        try:
            meta = tgt.head_object(r.target_key)
            r.target_present = True
            r.notes.append(f"target-exists size={meta.get('size')} ct={meta.get('content_type')}")
        except Exception as e:  # noqa: BLE001
            # Missing target is the expected state for a fresh migration.
            r.target_present = False
            code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
            if code not in ("404", "NoSuchKey", "NotFound"):
                r.notes.append(f"target-probe-error: {code}")


def summarize(refs: List[ObjectRef]) -> dict:
    total = len(refs)
    present = sum(1 for r in refs if r.source_present)
    missing = sum(1 for r in refs if r.source_present is False)
    total_bytes = sum((r.source_size or 0) for r in refs if r.source_present)
    seen_paths = {}
    duplicates = 0
    for r in refs:
        seen_paths[r.source_path] = seen_paths.get(r.source_path, 0) + 1
    for p, n in seen_paths.items():
        if n > 1:
            duplicates += 1
    target_collisions = sum(1 for r in refs if r.target_present)
    return {
        "total_refs": total,
        "source_present": present,
        "source_missing": missing,
        "duplicate_source_paths": duplicates,
        "target_collisions": target_collisions,
        "total_bytes_present": total_bytes,
    }


def print_report(refs: List[ObjectRef], summary: dict, verbose: bool) -> None:
    print("=" * 60)
    print(" STORAGE MIGRATION DRY-RUN REPORT ")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:26} : {v}")
    if verbose:
        print()
        print(" Per-object detail:")
        for r in refs:
            status = "OK" if r.source_present else "MISS"
            tgt = ("EXISTS" if r.target_present else "-") if r.target_present is not None else "n/a"
            print(f"  [{status}] target={tgt:6} sha={ (r.source_sha256 or '-')[:12] } size={r.source_size} origin={r.origin} path={r.source_path}")
            for n in r.notes:
                print(f"        · {n}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true", help="Per-object detail lines")
    parser.add_argument("--check-target", action="store_true", help="Also probe S3 for existing target keys (requires S3 env configured)")
    parser.add_argument("--live", action="store_true", help="RESERVED — real copy path is intentionally not implemented in this task")
    args = parser.parse_args()

    if args.live and os.environ.get("STORAGE_MIGRATE_LIVE") != "1":
        logger.error("--live requires env STORAGE_MIGRATE_LIVE=1 (safety guard). Aborting.")
        return 9
    if args.live:
        logger.error("Live copy path is NOT implemented in this task (dry-run only). Aborting.")
        return 9

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        logger.error("MONGO_URL / DB_NAME not configured.")
        return 9
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    refs = await enumerate_refs(db)
    logger.info("Enumerated %d object refs from MongoDB.", len(refs))

    src = EmergentStorageProvider()
    if not src.is_configured():
        logger.error("Emergent provider not configured (EMERGENT_LLM_KEY missing).")
        return 9
    logger.info("Probing source (Emergent)…")
    probe_source(refs, src)

    if args.check_target:
        tgt = S3StorageProvider()
        if not tgt.is_configured():
            logger.warning("S3 provider not configured — skipping target probe. Set S3_BUCKET/keys to enable.")
        else:
            logger.info("Probing target (S3) heads…")
            probe_target(refs, tgt)

    summary = summarize(refs)
    print_report(refs, summary, verbose=args.verbose)

    if summary["source_missing"] > 0:
        return 2
    if summary["target_collisions"] > 0:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
