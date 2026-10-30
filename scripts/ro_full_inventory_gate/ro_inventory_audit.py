#!/usr/bin/env python3
"""ETERNAL — hardened READ-ONLY full inventory reconciliation audit.

Fail-closed without dedicated RO Mongo URI.
Paginated scans, resumable checkpoints, never labels FULL when skipped/truncated.
Does not mutate ERP data. Writes only under the explicit output directory.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote_plus, urlparse, urlunparse

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, MongoClient
from pymongo.errors import OperationFailure, PyMongoError


def restore_id(value: Any) -> Any:
    """Rehydrate checkpoint _id values (ObjectId was JSON-stringified)."""
    if value is None or isinstance(value, ObjectId):
        return value
    if isinstance(value, str):
        try:
            return ObjectId(value)
        except (InvalidId, TypeError):
            return value
    return value

# ---------------------------------------------------------------------------
# Backend helpers (production classification parity)
# ---------------------------------------------------------------------------
_BACKEND = Path(os.environ.get("ETERNAL_RO_BACKEND") or "").resolve()
if not _BACKEND.is_dir():
    # Prefer sibling backend when run from repo checkout
    cand = Path(__file__).resolve().parents[2] / "backend"
    if cand.is_dir():
        _BACKEND = cand
if _BACKEND.is_dir():
    sys.path.insert(0, str(_BACKEND))

from quantity_rules import is_print_line  # noqa: E402
from services.inventory_accounting import ITEM_TYPE_SERVICE  # noqa: E402
from services.mode_b_legacy_demand import (  # noqa: E402
    get_mode_b_legacy_demand_settings,
    mongo_order_branch_clauses,
)
from services.order_line_addon import resolve_parent_idx  # noqa: E402
from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    _line_master_id,
    _order_cancelled,
    _orders_for_branch,
    _qty_num,
    aggregate_operational_orders,
    operational_availability,
)
from services.r2_go_live import is_r2_authority_active  # noqa: E402
from services.stock_compatibility_read import (  # noqa: E402
    BELUM_STATUS_READY,
    BELUM_STATUS_UNVERIFIED,
    aggregate_legacy_delivered_qty,
)

SCRIPT_VERSION = "hardened-1.0.0"
CHECKPOINT_NAME = "CHECKPOINT.json"
FORBIDDEN_OUT_PREFIXES = (
    "/opt/eternal-packaging/",
    "/opt/eternal-packaging-runtime/",
    "/var/lib/mongodb/",
    "/data/db/",
)
SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|mongo(_)?url|uri)",
    re.I,
)
CUSTOMER_KEYS = frozenset({
    "customer_name", "customer_phone", "customer_address", "phone", "address",
    "email", "npwp", "ktp", "shipping_address", "billing_address",
    "contact_name", "contact_phone", "recipient_name", "recipient_phone",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_uri(uri: str) -> str:
    if not uri:
        return ""
    try:
        p = urlparse(uri)
        netloc = p.netloc
        if "@" in netloc:
            creds, host = netloc.rsplit("@", 1)
            if ":" in creds:
                user = creds.split(":", 1)[0]
                netloc = f"{user}:***@{host}"
            else:
                netloc = f"***@{host}"
        return urlunparse((p.scheme, netloc, p.path, "", p.query, ""))
    except Exception:
        return "<unparseable-uri>"


def refuse_dangerous_out_dir(out: Path) -> None:
    resolved = str(out.resolve())
    for prefix in FORBIDDEN_OUT_PREFIXES:
        if resolved.startswith(prefix.rstrip("/")) or resolved.startswith(prefix):
            raise SystemExit(f"STOP_OUT_DIR_FORBIDDEN {resolved}")
    if resolved in ("/", "/tmp", "/var", "/opt", "/home"):
        raise SystemExit(f"STOP_OUT_DIR_TOO_BROAD {resolved}")


def redact_mapping(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            if SENSITIVE_KEY_RE.search(ks) or ks.lower() in CUSTOMER_KEYS:
                out[ks] = "<redacted>"
            else:
                out[ks] = redact_mapping(v)
        return out
    if isinstance(obj, list):
        return [redact_mapping(x) for x in obj[:50]]
    if isinstance(obj, str) and ("mongodb://" in obj.lower() or "mongodb+srv://" in obj.lower()):
        return mask_uri(obj)
    return obj


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(redact_mapping(payload), indent=2, ensure_ascii=False, default=str) + "\n")


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Read-only authorization evidence
# ---------------------------------------------------------------------------
def verify_read_only(client: MongoClient, db_name: str) -> Dict[str, Any]:
    evidence: Dict[str, Any] = {
        "checked_at_utc": utc_now(),
        "db_name": db_name,
        "read_only_verified": False,
        "probes": {},
    }
    try:
        status = client.admin.command("connectionStatus", showPrivileges=True)
        auth = status.get("authInfo") or {}
        evidence["authenticated_users"] = auth.get("authenticatedUsers") or []
        evidence["authenticated_user_roles"] = auth.get("authenticatedUserRoles") or []
        # Privileges may be large; keep role names only
        privs = auth.get("authenticatedUserPrivileges") or []
        evidence["privilege_actions_sample"] = sorted({
            a for p in privs for a in (p.get("actions") or [])
        })[:80]
    except OperationFailure as exc:
        evidence["connectionStatus_error"] = {"code": exc.code, "codeName": getattr(exc, "codeName", "")}

    probe_coll = client[db_name]["_eternal_ro_write_probe_must_fail"]
    write_allowed = False
    for name, fn in (
        ("insert_one", lambda: probe_coll.insert_one({"_probe": True, "ts": utc_now()})),
        ("update_one", lambda: probe_coll.update_one({"_probe": True}, {"$set": {"x": 1}}, upsert=True)),
        ("delete_one", lambda: probe_coll.delete_one({"_probe": True})),
    ):
        try:
            fn()
            evidence["probes"][name] = {"result": "UNEXPECTED_WRITE_ALLOWED"}
            write_allowed = True
        except OperationFailure as exc:
            evidence["probes"][name] = {
                "result": "DENIED",
                "code": exc.code,
                "codeName": getattr(exc, "codeName", "") or "",
            }
        except PyMongoError as exc:
            evidence["probes"][name] = {"result": "DENIED", "error": type(exc).__name__, "msg": str(exc)[:200]}

    # Prefer evidence that insert was denied (code 13 Unauthorized typical)
    insert_meta = evidence["probes"].get("insert_one") or {}
    evidence["read_only_verified"] = (not write_allowed) and insert_meta.get("result") == "DENIED"
    if write_allowed:
        evidence["failure"] = "WRITE_PROBES_SUCCEEDED_CREDENTIALS_NOT_READ_ONLY"
    return evidence


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
def count_docs(coll, query: dict) -> int:
    return int(coll.count_documents(query))


def iter_pages(
    coll,
    query: dict,
    projection: dict,
    *,
    batch_size: int,
    max_time_ms: int,
    resume_after_id: Any = None,
):
    """Stable _id ascending pagination. Yields (docs, last_id, truncated_flag_unused)."""
    q = dict(query)
    if resume_after_id is not None:
        q = {"$and": [q, {"_id": {"$gt": resume_after_id}}]}
    last_id = resume_after_id
    while True:
        cursor = (
            coll.find(q if resume_after_id is None and last_id is None else (
                {"$and": [query, {"_id": {"$gt": last_id}}]} if last_id is not None else query
            ), projection)
            .sort([("_id", ASCENDING)])
            .limit(batch_size)
            .max_time_ms(max_time_ms)
        )
        docs = list(cursor)
        if not docs:
            break
        last_id = docs[-1]["_id"]
        yield docs, last_id
        if len(docs) < batch_size:
            break


# ---------------------------------------------------------------------------
# Gate classification (same semantics as prior gate script)
# ---------------------------------------------------------------------------
def classify_po_gates(order: dict, owners: Dict[str, str], service_ids: Set[str]) -> Dict[str, Any]:
    if _order_cancelled(order):
        return {"cancelled": True}
    lines = list(order.get("line_items") or [])
    master_ids = [_line_master_id(line, owners) for line in lines]
    active_idxs = [
        i for i, line in enumerate(lines)
        if isinstance(line, dict) and not (line.get("cancelled") or line.get("is_cancelled"))
    ]
    print_candidates = [i for i in active_idxs if is_print_line(lines[i])]
    has_orphan_print = any(not master_ids[i] for i in print_candidates)
    unlinked_service = False
    unlinked_examples: List[dict] = []
    for i in active_idxs:
        mid = master_ids[i]
        if not mid or mid not in service_ids:
            continue
        if not is_print_line(lines[i]):
            continue
        parent = resolve_parent_idx(lines[i], lines)
        if parent is None:
            unlinked_service = True
            unlinked_examples.append({
                "line_idx": i,
                "service_master_id": mid,
                "item_code": str(lines[i].get("item_code") or ""),
            })
    unassigned = []
    for split in order.get("operator_splits") or []:
        if not isinstance(split, dict):
            continue
        raw = split.get("line_idx")
        if raw is None or str(raw).strip() == "":
            unassigned.append({"has_qty": split.get("qty_completed") is not None})
    unassigned_ambiguous = bool(unassigned) and not (
        len(print_candidates) == 1 and master_ids[print_candidates[0]]
        and master_ids[print_candidates[0]] not in service_ids
    )
    physical_idxs = [
        i for i in active_idxs
        if master_ids[i] and master_ids[i] not in service_ids
    ]
    return {
        "cancelled": False,
        "order_id": str(order.get("id") or ""),
        "has_orphan_print": has_orphan_print,
        "unlinked_service": unlinked_service,
        "unlinked_examples": unlinked_examples[:5],
        "unassigned_ambiguous": unassigned_ambiguous,
        "physical_master_ids": sorted({master_ids[i] for i in physical_idxs}),
        "process_gate_unproven": bool(unlinked_service or has_orphan_print or unassigned_ambiguous),
    }


def merge_progress(dst: Dict[str, dict], src: Dict[str, dict]) -> None:
    for mid, slot in src.items():
        cur = dst.setdefault(
            mid,
            {
                "ordered_qty": 0.0,
                "completed_qty": 0.0,
                "completion_unproven": False,
            },
        )
        cur["ordered_qty"] = _qty_num(cur["ordered_qty"]) + _qty_num(slot.get("ordered_qty"))
        cur["completed_qty"] = _qty_num(cur["completed_qty"]) + _qty_num(slot.get("completed_qty"))
        if slot.get("completion_unproven"):
            cur["completion_unproven"] = True


def merge_delivered(dst: Dict[str, float], src: Dict[str, float]) -> None:
    for mid, qty in src.items():
        dst[mid] = _qty_num(dst.get(mid, 0)) + _qty_num(qty)


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------
class Checkpoint:
    def __init__(self, path: Path):
        self.path = path
        self.data = load_json(path, {
            "version": 1,
            "script_version": SCRIPT_VERSION,
            "phase": "init",
            "branch_index": 0,
            "last_order_id": None,
            "last_balance_id": None,
            "last_ledger_id": None,
            "last_master_id": None,
            "aggregates_path": "AGGREGATES.json",
            "updated_at_utc": utc_now(),
        })

    def save(self) -> None:
        self.data["updated_at_utc"] = utc_now()
        self.data["script_version"] = SCRIPT_VERSION
        write_json(self.path, self.data)


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------
class AuditRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.out = Path(args.out_dir).resolve()
        refuse_dangerous_out_dir(self.out)
        self.out.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.out, 0o700)
        except OSError:
            pass
        self.batch = max(1, int(args.batch_size))
        self.max_time_ms = max(1000, int(args.query_max_ms))
        self.mongo_uri = args.mongo_url
        self.db_name = args.db_name
        self.expect_sha = args.expect_sha
        self.active_sha = args.active_sha or ""
        self.skipped: List[dict] = []
        self.incomplete: List[str] = []
        self.counts: Dict[str, Any] = {
            "expected": {},
            "scanned": {},
            "duplicates": {},
            "skipped": 0,
        }
        self.ckpt = Checkpoint(self.out / CHECKPOINT_NAME)
        self.agg_path = self.out / "AGGREGATES.json"
        self.aggregates = load_json(self.agg_path, {
            "owners": {},
            "service_ids": [],
            "masters_by_id": {},
            "branches": [],
            "warehouses": [],
            "per_branch": {},
        })

    def connect(self) -> Tuple[MongoClient, Any]:
        client = MongoClient(
            self.mongo_uri,
            readPreference="secondaryPreferred",
            serverSelectionTimeoutMS=10000,
            maxPoolSize=4,
        )
        client.admin.command("ping")
        return client, client[self.db_name]

    def run(self) -> int:
        started = time.time()
        meta = {
            "script_version": SCRIPT_VERSION,
            "started_at_utc": utc_now(),
            "expect_sha": self.expect_sha,
            "active_sha": self.active_sha,
            "mongo_uri_masked": mask_uri(self.mongo_uri),
            "db_name": self.db_name,
            "batch_size": self.batch,
            "query_max_ms": self.max_time_ms,
            "out_dir": str(self.out),
        }
        write_json(self.out / "RUN_META.json", meta)

        if self.expect_sha and self.active_sha and self.active_sha != self.expect_sha:
            self.incomplete.append(f"SHA_MISMATCH active={self.active_sha} expect={self.expect_sha}")
            write_json(self.out / "STATUS.json", {
                "status": "INCOMPLETE",
                "coverage": "PARTIAL",
                "reasons": self.incomplete,
            })
            (self.out / "STATUS.txt").write_text("INCOMPLETE\n")
            return 2

        client, db = self.connect()
        try:
            ro = verify_read_only(client, self.db_name)
            write_json(self.out / "RO_AUTH_EVIDENCE.json", ro)
            if not ro.get("read_only_verified"):
                write_json(self.out / "STATUS.json", {
                    "status": "STOP",
                    "coverage": "PARTIAL",
                    "reasons": ["READ_ONLY_ROLE_NOT_VERIFIED"],
                    "evidence": ro.get("failure") or ro.get("probes"),
                })
                (self.out / "STATUS.txt").write_text("STOP_RO_NOT_VERIFIED\n")
                return 1

            # Sync helpers that expect Motor-like db still work with pymongo Database
            # (find_one / find are sync on pymongo; async helpers need adapters).
            r2_on = self._r2_active(db)
            legacy = self._legacy_settings(db)
            self.aggregates["r2_authority_active"] = bool(r2_on)
            self.aggregates["legacy_settings_present"] = bool(legacy)

            self._phase_catalog(db)
            self._phase_branches_warehouses(db)
            self._phase_orders_and_demand(db, legacy, r2_on)
            self._phase_finalize(r2_on)

            elapsed = round(time.time() - started, 3)
            coverage = self._coverage_label()
            status = "FULL" if coverage == "FULL" else ("INCOMPLETE" if self.incomplete else "PARTIAL")
            # Never label FULL when skipped/truncated
            if self.skipped or self.incomplete or self.counts["skipped"]:
                coverage = "PARTIAL"
                if status == "FULL":
                    status = "PARTIAL"

            summary = {
                "status": status,
                "coverage": coverage,
                "counts": self.counts,
                "incomplete_reasons": self.incomplete,
                "skipped": self.skipped[:200],
                "skipped_count": len(self.skipped),
                "elapsed_seconds": elapsed,
                "read_only_verified": True,
                "script_version": SCRIPT_VERSION,
                "finished_at_utc": utc_now(),
            }
            write_json(self.out / "COVERAGE_SUMMARY.json", summary)
            write_json(self.out / "STATUS.json", {"status": status, "coverage": coverage})
            (self.out / "STATUS.txt").write_text(f"{status}\n{coverage}\n")
            self._proof_no_secrets()
            self.ckpt.data["phase"] = "done"
            self.ckpt.save()
            print("STATUS", status)
            print("COVERAGE", coverage)
            print("OUT_DIR", str(self.out))
            print("COUNTS", json.dumps(self.counts, default=str))
            return 0 if coverage == "FULL" and status == "FULL" else 2
        finally:
            client.close()

    def _coverage_label(self) -> str:
        if self.incomplete or self.skipped or self.counts.get("skipped"):
            return "PARTIAL"
        exp = self.counts.get("expected") or {}
        scanned = self.counts.get("scanned") or {}
        for key in ("masters", "orders", "branches", "warehouses", "balances"):
            if key in exp and scanned.get(key, -1) != exp.get(key):
                return "PARTIAL"
        return "FULL"

    def _r2_active(self, db) -> bool:
        doc = db.app_settings.find_one({"key": "r2_go_live"}, {"_id": 0}) or {}
        # Match is_r2_authority_active shape loosely
        if "r2_authority_active" in doc:
            return bool(doc.get("r2_authority_active"))
        settings = doc.get("value") if isinstance(doc.get("value"), dict) else doc
        return bool((settings or {}).get("r2_authority_active"))

    def _legacy_settings(self, db) -> Optional[dict]:
        # Use sync find; sanitize via helper if async wrapper not needed
        try:
            # get_mode_b_legacy_demand_settings is async in some builds — call sanitize path
            from services.mode_b_legacy_demand import SETTINGS_KEY, sanitize_settings
            doc = db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
            return sanitize_settings(doc)
        except Exception as exc:
            self.skipped.append({"phase": "legacy_settings", "reason": type(exc).__name__, "detail": str(exc)[:200]})
            self.counts["skipped"] += 1
            return None

    def _phase_catalog(self, db) -> None:
        if self.ckpt.data.get("phase") not in ("init", "catalog", "done") and self.aggregates.get("masters_by_id"):
            # resume past catalog if aggregates already filled and phase advanced
            if self.ckpt.data.get("phase") in ("branches", "orders", "finalize", "done"):
                return
        coll = db[MASTER_ITEMS_COLLECTION]
        expected = count_docs(coll, {})
        self.counts["expected"]["masters"] = expected
        scanned = 0
        seen_ids: Set[str] = set()
        dup = 0
        masters_by_id = dict(self.aggregates.get("masters_by_id") or {})
        service_ids: Set[str] = set(self.aggregates.get("service_ids") or [])
        owners: Dict[str, str] = dict(self.aggregates.get("owners") or {})
        owner_counts: Dict[str, int] = defaultdict(int)
        resume_id = restore_id(self.ckpt.data.get("last_master_id"))
        for docs, last_id in iter_pages(
            coll,
            {},
            {"id": 1, "item_code": 1, "item_name": 1, "item_type": 1, "is_active": 1, "accurate_item_id": 1},
            batch_size=self.batch,
            max_time_ms=self.max_time_ms,
            resume_after_id=resume_id,
        ):
            for doc in docs:
                scanned += 1
                mid = str(doc.get("id") or "").strip()
                if not mid:
                    self.skipped.append({"phase": "masters", "reason": "MISSING_ID", "oid": str(doc.get("_id"))})
                    self.counts["skipped"] += 1
                    continue
                if mid in seen_ids or mid in masters_by_id:
                    dup += 1
                    continue
                seen_ids.add(mid)
                masters_by_id[mid] = {
                    "id": mid,
                    "item_code": doc.get("item_code") or "",
                    "item_name": doc.get("item_name") or "",
                    "item_type": doc.get("item_type") or "",
                    "is_active": doc.get("is_active"),
                    "accurate_item_id": str(doc.get("accurate_item_id") or "").strip(),
                }
                if str(doc.get("item_type") or "").strip().upper() == ITEM_TYPE_SERVICE:
                    service_ids.add(mid)
            self.ckpt.data["phase"] = "catalog"
            self.ckpt.data["last_master_id"] = last_id
            self.aggregates["masters_by_id"] = masters_by_id
            self.aggregates["service_ids"] = sorted(service_ids)
            write_json(self.agg_path, self.aggregates)
            self.ckpt.save()
            if self.args.interrupt_after_masters and scanned >= int(self.args.interrupt_after_masters):
                raise SystemExit("STOP_UAT_INTERRUPT_MASTERS")

        # Rebuild unique Accurate owners from full catalog (no 5000 hard-cap)
        owner_counts = defaultdict(int)
        owners_tmp: Dict[str, str] = {}
        for mid, m in masters_by_id.items():
            acc = str(m.get("accurate_item_id") or "").strip()
            if not acc:
                continue
            owner_counts[acc] += 1
            owners_tmp[acc] = mid
        self.aggregates["owners"] = {
            a: m for a, m in owners_tmp.items() if owner_counts[a] == 1
        }
        self.counts["scanned"]["masters"] = len(masters_by_id)
        self.counts["duplicates"]["masters"] = dup
        if len(masters_by_id) != expected:
            self.incomplete.append(
                f"MASTERS_COUNT_MISMATCH scanned={len(masters_by_id)} expected={expected}"
            )
        self.aggregates["masters_by_id"] = masters_by_id
        self.aggregates["service_ids"] = sorted(service_ids)
        write_json(self.agg_path, self.aggregates)

    def _phase_branches_warehouses(self, db) -> None:
        if self.ckpt.data.get("phase") in ("orders", "finalize", "done") and self.aggregates.get("branches"):
            self.counts["expected"].setdefault("branches", len(self.aggregates["branches"]))
            self.counts["scanned"].setdefault("branches", len(self.aggregates["branches"]))
            self.counts["expected"].setdefault("warehouses", len(self.aggregates["warehouses"]))
            self.counts["scanned"].setdefault("warehouses", len(self.aggregates["warehouses"]))
            return
        # ALL branches / warehouses (including inactive)
        for kind, coll_name in (("branches", "branches"), ("warehouses", "warehouses")):
            coll = db[coll_name]
            expected = count_docs(coll, {})
            self.counts["expected"][kind] = expected
            rows = []
            seen = set()
            dup = 0
            proj = {"id": 1, "code": 1, "name": 1, "active": 1, "branch_id": 1, "is_operational": 1}
            scanned = 0
            for docs, _last in iter_pages(coll, {}, proj, batch_size=self.batch, max_time_ms=self.max_time_ms):
                for doc in docs:
                    scanned += 1
                    rid = str(doc.get("id") or "").strip()
                    if not rid:
                        self.skipped.append({"phase": kind, "reason": "MISSING_ID", "oid": str(doc.get("_id"))})
                        self.counts["skipped"] += 1
                        continue
                    if rid in seen:
                        dup += 1
                        continue
                    seen.add(rid)
                    rows.append({k: doc.get(k) for k in proj if k in doc or True})
            self.counts["scanned"][kind] = scanned
            self.counts["duplicates"][kind] = dup
            if scanned != expected:
                self.incomplete.append(f"{kind.upper()}_COUNT_MISMATCH scanned={scanned} expected={expected}")
            self.aggregates[kind] = rows
        self.ckpt.data["phase"] = "branches"
        self.ckpt.data["last_master_id"] = None
        write_json(self.agg_path, self.aggregates)
        self.ckpt.save()

    def _phase_orders_and_demand(self, db, legacy, r2_on: bool) -> None:
        branches = self.aggregates.get("branches") or []
        service_ids = set(self.aggregates.get("service_ids") or [])
        owners = dict(self.aggregates.get("owners") or {})
        by_id = {
            mid: m for mid, m in (self.aggregates.get("masters_by_id") or {}).items()
            if mid not in service_ids
        }
        start_index = int(self.ckpt.data.get("branch_index") or 0)
        total_orders_expected = 0
        total_orders_scanned = int((self.counts.get("scanned") or {}).get("orders") or 0)

        # Global expected orders
        self.counts["expected"]["orders"] = count_docs(db.orders, {})

        for b_index, branch in enumerate(branches):
            if b_index < start_index:
                continue
            bid = str(branch.get("id") or "").strip()
            bcode = str(branch.get("code") or "").strip().upper() or bid
            if not bid:
                self.skipped.append({"phase": "branches", "reason": "MISSING_BRANCH_ID", "row": branch})
                self.counts["skipped"] += 1
                continue

            per = (self.aggregates.get("per_branch") or {}).get(bid) or {
                "branch_id": bid,
                "branch_code": bcode,
                "progress": {},
                "delivered": {},
                "provenance": {},
                "tainted": {},
                "balances": {},
                "orders_scanned": 0,
                "orders_matched_branch": 0,
                "po_gate_unproven": 0,
            }
            # Resume order cursor only for current branch
            resume_order = restore_id(self.ckpt.data.get("last_order_id")) if b_index == start_index else None

            clauses = mongo_order_branch_clauses(bid, legacy) if legacy is not None else [
                {"branch_id": bid}, {"cabang_id": bid}
            ]
            query = {"$or": clauses} if clauses else {"_id": {"$exists": False}}
            # Projection: operational fields only — no customer PII fields
            proj = {
                "_id": 1, "id": 1, "branch_id": 1, "cabang_id": 1,
                "cancelled": 1, "is_cancelled": 1, "status": 1, "status_label": 1,
                "qty_delivered": 1, "line_items": 1, "operator_splits": 1,
                # shipments: only ids/qty for legacy path — strip customer nested later
                "shipments.id": 1, "shipments.qty": 1, "shipments.quantity": 1,
                "shipments.line_idx": 1, "shipments.items": 1, "shipments.delivered_qty": 1,
                "created_at": 1,
            }
            progress: Dict[str, dict] = {
                k: dict(v) for k, v in (per.get("progress") or {}).items()
            }
            tainted: Dict[str, List[dict]] = {
                k: list(v) for k, v in (per.get("tainted") or {}).items()
            }
            scanned_here = int(per.get("orders_scanned") or 0)
            matched = int(per.get("orders_matched_branch") or 0)

            for docs, last_id in iter_pages(
                db.orders, query, proj,
                batch_size=self.batch,
                max_time_ms=self.max_time_ms,
                resume_after_id=resume_order,
            ):
                # Filter to branch (Mode B aware)
                filtered = _orders_for_branch(docs, bid, legacy_settings=legacy)
                matched += len(filtered)
                scanned_here += len(docs)
                total_orders_scanned += len(docs)
                batch_progress = aggregate_operational_orders(
                    filtered, owners, exclude_master_ids=service_ids,
                )
                merge_progress(progress, batch_progress)
                if not r2_on:
                    delivered_legacy, prov = aggregate_legacy_delivered_qty(
                        filtered, [], owners, process_master_ids=service_ids,
                    )
                    merge_delivered(per.setdefault("delivered", {}), delivered_legacy)
                    for mid, p in (prov or {}).items():
                        per.setdefault("provenance", {})[mid] = p
                for order in filtered:
                    g = classify_po_gates(order, owners, service_ids)
                    if g.get("cancelled") or not g.get("process_gate_unproven"):
                        continue
                    per["po_gate_unproven"] = int(per.get("po_gate_unproven") or 0) + 1
                    sample = {
                        "order_id": g.get("order_id"),
                        "has_orphan_print": g.get("has_orphan_print"),
                        "unlinked_service": g.get("unlinked_service"),
                        "unassigned_ambiguous": g.get("unassigned_ambiguous"),
                        "unlinked_examples": g.get("unlinked_examples"),
                    }
                    for mid in g.get("physical_master_ids") or []:
                        bucket = tainted.setdefault(mid, [])
                        if len(bucket) < 5:
                            bucket.append(sample)

                per["progress"] = progress
                per["tainted"] = tainted
                per["orders_scanned"] = scanned_here
                per["orders_matched_branch"] = matched
                self.aggregates.setdefault("per_branch", {})[bid] = per
                self.ckpt.data["phase"] = "orders"
                self.ckpt.data["branch_index"] = b_index
                self.ckpt.data["last_order_id"] = last_id
                write_json(self.agg_path, self.aggregates)
                self.ckpt.save()
                resume_order = None  # only first page uses resume
                if self.args.interrupt_after_orders and total_orders_scanned >= int(self.args.interrupt_after_orders):
                    raise SystemExit("STOP_UAT_INTERRUPT_ORDERS")

            # Balances for this branch warehouses (all WH linked to branch)
            wh_ids = [
                str(w.get("id") or "")
                for w in (self.aggregates.get("warehouses") or [])
                if str(w.get("branch_id") or "") == bid and w.get("id")
            ]
            bal_by_mid: Dict[str, float] = {
                k: float(v) for k, v in (per.get("balances") or {}).items()
            }
            bal_scanned = 0
            if wh_ids:
                b_expected = count_docs(db[BALANCE_COLLECTION], {"warehouse_id": {"$in": wh_ids}})
                self.counts["expected"][f"balances_{bcode}"] = b_expected
                resume_bal = restore_id(self.ckpt.data.get("last_balance_id")) if (
                    b_index == int(self.ckpt.data.get("branch_index") or 0)
                    and self.ckpt.data.get("phase") == "balances"
                ) else None
                for docs, last_id in iter_pages(
                    db[BALANCE_COLLECTION],
                    {"warehouse_id": {"$in": wh_ids}},
                    {"master_item_id": 1, "warehouse_id": 1, "physical_qty": 1},
                    batch_size=self.batch,
                    max_time_ms=self.max_time_ms,
                    resume_after_id=resume_bal,
                ):
                    for doc in docs:
                        bal_scanned += 1
                        mid = str(doc.get("master_item_id") or "")
                        if mid:
                            bal_by_mid[mid] = _qty_num(bal_by_mid.get(mid, 0)) + _qty_num(doc.get("physical_qty"))
                    per["balances"] = bal_by_mid
                    self.aggregates["per_branch"][bid] = per
                    self.ckpt.data["phase"] = "balances"
                    self.ckpt.data["branch_index"] = b_index
                    self.ckpt.data["last_balance_id"] = last_id
                    write_json(self.agg_path, self.aggregates)
                    self.ckpt.save()
                self.counts["scanned"][f"balances_{bcode}"] = bal_scanned
                if bal_scanned != b_expected:
                    self.incomplete.append(
                        f"BALANCES_COUNT_MISMATCH_{bcode} scanned={bal_scanned} expected={b_expected}"
                    )

            # R2 ledger delivered — paginated
            if r2_on:
                delivered: Dict[str, float] = {
                    k: float(v) for k, v in (per.get("delivered") or {}).items()
                }
                # Two-pass: collect SALES_DELIVERY then subtract REVERSAL refs
                mov_ids: List[str] = list(per.get("delivery_movement_ids") or [])
                q_del = {
                    "movement_type": MOVEMENT_SALES_DELIVERY,
                    "branch_id": bid,
                }
                for docs, last_id in iter_pages(
                    db[LEDGER_COLLECTION],
                    q_del,
                    {"id": 1, "master_item_id": 1, "qty_delta": 1},
                    batch_size=self.batch,
                    max_time_ms=self.max_time_ms,
                    resume_after_id=(
                        None if self.ckpt.data.get("phase") != "ledger"
                        else restore_id(self.ckpt.data.get("last_ledger_id"))
                    ),
                ):
                    for row in docs:
                        mid = str(row.get("master_item_id") or "")
                        delta = _qty_num(row.get("qty_delta"))
                        rid = str(row.get("id") or "")
                        if rid:
                            mov_ids.append(rid)
                        if mid and delta < 0:
                            delivered[mid] = _qty_num(delivered.get(mid, 0)) + abs(delta)
                    per["delivered"] = delivered
                    per["delivery_movement_ids"] = mov_ids[-50000:]  # bound checkpoint size
                    self.aggregates["per_branch"][bid] = per
                    self.ckpt.data["phase"] = "ledger"
                    self.ckpt.data["last_ledger_id"] = last_id
                    write_json(self.agg_path, self.aggregates)
                    self.ckpt.save()
                # Reversals
                if mov_ids:
                    # chunk $in
                    rev_set = set()
                    for i in range(0, len(mov_ids), self.batch):
                        chunk = mov_ids[i:i + self.batch]
                        for docs, _ in iter_pages(
                            db[LEDGER_COLLECTION],
                            {"movement_type": MOVEMENT_REVERSAL, "reference_id": {"$in": chunk}},
                            {"reference_id": 1},
                            batch_size=self.batch,
                            max_time_ms=self.max_time_ms,
                        ):
                            for row in docs:
                                rev_set.add(str(row.get("reference_id") or ""))
                    if rev_set:
                        # Recompute delivered excluding reversed — reload deliveries once more bounded
                        delivered = {}
                        for docs, _ in iter_pages(
                            db[LEDGER_COLLECTION],
                            q_del,
                            {"id": 1, "master_item_id": 1, "qty_delta": 1},
                            batch_size=self.batch,
                            max_time_ms=self.max_time_ms,
                        ):
                            for row in docs:
                                if str(row.get("id") or "") in rev_set:
                                    continue
                                mid = str(row.get("master_item_id") or "")
                                delta = _qty_num(row.get("qty_delta"))
                                if mid and delta < 0:
                                    delivered[mid] = _qty_num(delivered.get(mid, 0)) + abs(delta)
                        per["delivered"] = delivered
                        for mid in delivered:
                            per.setdefault("provenance", {})[mid] = "r2_ledger"

            # Clear per-branch resume cursors when branch completes
            self.ckpt.data["last_order_id"] = None
            self.ckpt.data["last_balance_id"] = None
            self.ckpt.data["last_ledger_id"] = None
            self.ckpt.data["branch_index"] = b_index + 1
            self.aggregates.setdefault("per_branch", {})[bid] = per
            write_json(self.agg_path, self.aggregates)
            self.ckpt.save()

        self.counts["scanned"]["orders"] = total_orders_scanned
        # Note: per-branch $or queries can double-count across branches; track matched separately
        self.counts["expected"]["orders_global"] = self.counts["expected"]["orders"]
        if total_orders_scanned == 0 and self.counts["expected"]["orders"] > 0 and not branches:
            self.incomplete.append("NO_BRANCHES_TO_SCAN_ORDERS")

    def _phase_finalize(self, r2_on: bool) -> None:
        service_ids = set(self.aggregates.get("service_ids") or [])
        masters = {
            mid: m for mid, m in (self.aggregates.get("masters_by_id") or {}).items()
            if mid not in service_ids
        }
        dossier_rows: List[dict] = []
        class_counts: Dict[str, int] = defaultdict(int)
        for branch in self.aggregates.get("branches") or []:
            bid = str(branch.get("id") or "").strip()
            bcode = str(branch.get("code") or "").strip().upper()
            per = (self.aggregates.get("per_branch") or {}).get(bid) or {}
            progress = per.get("progress") or {}
            delivered = {k: _qty_num(v) for k, v in (per.get("delivered") or {}).items()}
            provenance = per.get("provenance") or {}
            tainted = per.get("tainted") or {}
            bal_by_mid = {k: _qty_num(v) for k, v in (per.get("balances") or {}).items()}
            # ALL inventory masters (including zero-balance / no demand)
            for mid, master in sorted(masters.items(), key=lambda kv: kv[0]):
                demand = progress.get(mid) or {
                    "ordered_qty": 0.0,
                    "completed_qty": 0.0,
                    "completion_unproven": False,
                }
                completed = _qty_num(demand.get("completed_qty"))
                ordered = _qty_num(demand.get("ordered_qty"))
                completion_unproven = bool(demand.get("completion_unproven"))
                prov = str(provenance.get(mid) or "")
                delivery_unproven = prov == "unproven"
                buckets = operational_availability(
                    physical_qty=bal_by_mid.get(mid, 0.0),
                    ordered_qty=ordered,
                    completed_qty=completed,
                    r2_delivered_qty=delivered.get(mid, 0),
                )
                belum_unverified = bool(
                    (completion_unproven and completed > 0)
                    or (delivery_unproven and completed > 0)
                )
                gates = tainted.get(mid) or []
                if belum_unverified:
                    belum_status = BELUM_STATUS_UNVERIFIED
                    belum_qty = None
                    problems = []
                    klass = "D"
                    if completion_unproven:
                        problems.append("COMPLETION_UNPROVEN")
                    if delivery_unproven:
                        problems.append("DELIVERY_UNPROVEN")
                    if any(g.get("unlinked_service") for g in gates):
                        problems.append("SERVICE_WITHOUT_PARENT")
                    if any(g.get("has_orphan_print") for g in gates):
                        problems.append("ORPHAN_PRINT")
                    if any(g.get("unassigned_ambiguous") for g in gates):
                        problems.append("AMBIGUOUS_UNASSIGNED_SPLITS")
                    recommendation = "Owner RO review; do not invent delivery"
                    repair_eligible = "NO_AUTO"
                else:
                    belum_status = BELUM_STATUS_READY
                    belum_qty = buckets["qty_belum_terkirim"]
                    problems = []
                    klass = "A"
                    recommendation = "No change"
                    repair_eligible = "N/A"
                class_counts[klass] += 1
                dossier_rows.append({
                    "branch_code": bcode,
                    "branch_id": bid,
                    "warehouse_scope": "BRANCH_DEMAND",
                    "item_code": master.get("item_code") or "",
                    "item_name": master.get("item_name") or "",
                    "master_item_id": mid,
                    "is_active": master.get("is_active"),
                    "problem": "|".join(problems) if problems else "VALID",
                    "class": klass,
                    "ordered_qty": ordered,
                    "completed_qty": completed,
                    "delivered_qty": delivered.get(mid, 0),
                    "qty_pesanan": buckets["qty_pesanan"],
                    "qty_belum_terkirim": belum_qty,
                    "belum_terkirim_status": belum_status,
                    "physical_qty_branch_wh_sum": bal_by_mid.get(mid, 0.0),
                    "completion_unproven": completion_unproven,
                    "delivery_provenance": prov or ("none" if mid not in delivered else "r2_ledger"),
                    "tainted_po_count": len(gates),
                    "tainted_po_sample": json.dumps(gates[:3], ensure_ascii=False),
                    "recommendation": recommendation,
                    "repair_eligible": repair_eligible,
                    "zero_stock_included": bal_by_mid.get(mid, 0.0) == 0 and ordered == 0,
                })

        # Warehouse inventory listing (coverage evidence)
        wh_rows = []
        for w in self.aggregates.get("warehouses") or []:
            wh_rows.append({
                "id": w.get("id"),
                "code": w.get("code"),
                "name": w.get("name"),
                "branch_id": w.get("branch_id"),
                "active": w.get("active"),
                "is_operational": w.get("is_operational"),
            })
        write_json(self.out / "WAREHOUSES.json", wh_rows)
        write_json(self.out / "BRANCHES.json", self.aggregates.get("branches") or [])

        with (self.out / "FULL_RECONCILIATION_DOSSIER.tsv").open("w", newline="") as f:
            fields = list(dossier_rows[0].keys()) if dossier_rows else [
                "branch_code", "item_code", "problem", "class"
            ]
            w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
            w.writeheader()
            for row in dossier_rows:
                w.writerow(row)

        owner_decisions = [
            r for r in dossier_rows
            if r["belum_terkirim_status"] == BELUM_STATUS_UNVERIFIED
        ]
        with (self.out / "OWNER_DECISION_LIST.tsv").open("w", newline="") as f:
            if owner_decisions:
                fields = list(owner_decisions[0].keys())
                w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
                w.writeheader()
                for row in owner_decisions:
                    w.writerow(row)
            else:
                f.write("item_code\tnote\n\tNONE_UNVERIFIED\n")

        write_json(self.out / "CLASS_COUNTS.json", dict(class_counts))
        self.counts["dossier_rows"] = len(dossier_rows)
        self.counts["inventory_masters"] = len(masters)
        self.ckpt.data["phase"] = "finalize"
        self.ckpt.save()

    def _proof_no_secrets(self) -> None:
        findings = []
        for path in self.out.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix in (".tsv", ".json", ".txt", ".md"):
                text = path.read_text(errors="replace")
                if re.search(r"mongodb(\+srv)?://[^:\s]+:[^@\s]+@", text, re.I):
                    findings.append({"file": path.name, "issue": "URI_WITH_CREDENTIALS"})
                for key in ("uat_ro_local_only", "uat_admin_local_only", "password=", "MONGO_URL="):
                    # allow masked forms only
                    if key in text and "***" not in text[max(0, text.find(key) - 5): text.find(key) + 40]:
                        # passwords in evidence must not appear
                        if "local_only" in key or key.startswith("password"):
                            findings.append({"file": path.name, "issue": f"SECRET_LITERAL:{key}"})
                for ck in ("customer_phone", "customer_address", "recipient_phone"):
                    if ck in text:
                        findings.append({"file": path.name, "issue": f"CUSTOMER_FIELD:{ck}"})
        proof = {
            "checked_at_utc": utc_now(),
            "files_checked": sum(1 for p in self.out.rglob("*") if p.is_file()),
            "findings": findings,
            "pass": len(findings) == 0,
        }
        # Write proof without re-scanning itself for passwords we didn't put in
        (self.out / "OUTPUT_SANITIZATION_PROOF.json").write_text(
            json.dumps(proof, indent=2) + "\n"
        )
        if findings:
            self.incomplete.append("OUTPUT_SANITIZATION_FINDINGS")
            self.skipped.append({"phase": "sanitization", "reason": "FINDINGS", "n": len(findings)})


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hardened RO full inventory audit")
    p.add_argument("--mongo-url", default=os.environ.get("ETERNAL_RO_MONGO_URL") or "")
    p.add_argument("--db-name", default=os.environ.get("ETERNAL_RO_DB_NAME") or "")
    p.add_argument("--out-dir", default=os.environ.get("ETERNAL_RO_OUT_DIR") or "")
    p.add_argument("--expect-sha", default=os.environ.get("ETERNAL_RO_EXPECT_SHA") or "")
    p.add_argument("--active-sha", default=os.environ.get("ETERNAL_RO_ACTIVE_SHA") or "")
    p.add_argument("--batch-size", type=int, default=int(os.environ.get("ETERNAL_RO_BATCH_SIZE") or 100))
    p.add_argument("--query-max-ms", type=int, default=int(os.environ.get("ETERNAL_RO_QUERY_MAX_MS") or 30000))
    p.add_argument("--interrupt-after-orders", type=int, default=int(os.environ.get("ETERNAL_RO_UAT_INTERRUPT_ORDERS") or 0))
    p.add_argument("--interrupt-after-masters", type=int, default=int(os.environ.get("ETERNAL_RO_UAT_INTERRUPT_MASTERS") or 0))
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not args.mongo_url:
        print("STOP_MISSING_ETERNAL_RO_MONGO_URL")
        return 1
    if not args.db_name:
        print("STOP_MISSING_ETERNAL_RO_DB_NAME")
        return 1
    if not args.out_dir:
        print("STOP_MISSING_ETERNAL_RO_OUT_DIR")
        return 1
    # Refuse using bare MONGO_URL env as silent fallback (already not read)
    if os.environ.get("MONGO_URL") and os.environ.get("MONGO_URL") == args.mongo_url:
        if os.environ.get("ETERNAL_RO_ALLOW_SAME_AS_APP_URI") != "1":
            # Still OK if Owner explicitly exported the RO URI into both — require explicit allow
            # when the string equals MONGO_URL to prevent accidental app RW URI reuse.
            print("STOP_URI_EQUALS_APP_MONGO_URL_SET_ETERNAL_RO_ALLOW_SAME_AS_APP_URI=1_IF_INTENTIONAL")
            return 1
    try:
        return AuditRunner(args).run()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        msg = str(exc) if not isinstance(exc.code, int) else ""
        if msg:
            print(msg)
        return code if isinstance(code, int) else 1
    except Exception as exc:
        print("STOP_EXCEPTION", type(exc).__name__, str(exc)[:300])
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
