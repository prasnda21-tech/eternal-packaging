# PHASE 3 — SECURITY REVIEW (HARDENED)

**Waktu:** 31 Oct 2026 ~04:30 WIB (Asia/Jakarta)  
**Branch:** `cursor/harden-ro-inventory-gate-1f94`  
**Pinned production SHA (expect):** `27df94962dd67c1e63ef42e33dd34640c0de109c`  
**Mode:** Local + synthetic UAT only · **not executed against production** · no ERP/DB mutation · no deploy/merge/PR

## Artifacts

| Item | Path |
|------|------|
| Hardened audit | `scripts/ro_full_inventory_gate/ro_inventory_audit.py` |
| VPS wrapper | `scripts/ro_full_inventory_gate/VPS_RO_FULL_INVENTORY_GATE_HARDENED.sh` |
| UAT harness | `scripts/ro_full_inventory_gate/uat_synthetic_harness.py` |
| Evidence dir | `/opt/cursor/artifacts/RO_INVENTORY_GATE_HARDENED_20261031/` |
| UAT results | `.../UAT_RESULTS.json` (25/25 PASS) |

### Hashes
```
26ae0bc65380a2477f92a6620d95a81e7c0074a7cda9c6af12508318ca7f4c4d  ro_inventory_audit.py
41d8ad1b5c0a39a1848a1fa0e243458a063e1b8ddb52d3b4bdb6cda3ed952d03  VPS_RO_FULL_INVENTORY_GATE_HARDENED.sh
```

## Controls vs prior FAIL

| Prior FAIL | Hardened control | Evidence |
|------------|------------------|----------|
| App RW `MONGO_URL` | Require `ETERNAL_RO_MONGO_URL` + `ETERNAL_RO_DB_NAME`; refuse missing; refuse URI equal to `MONGO_URL` unless explicit allow | UAT `fail_closed_missing_ro_uri`, `bash_fail_closed` |
| No RO proof | `verify_read_only`: `connectionStatus` + insert/update/delete probes must be **DENIED**; else STOP | `RO_AUTH_EVIDENCE.json`; UAT `read_only_permission_enforcement` (RW rejected) |
| 30k orders in RAM | Paginated `_id` ascending batches; discard pages; aggregates only | `resource_*` UAT; `aggregates_omit_raw_orders` |
| Active-only branches | All branches + warehouses (incl. inactive) | `inactive_branch_included`, `multi_warehouse_listed` |
| Idle SKU skip | Every inventory master × branch in dossier (incl. zero) | `zero_stock_sku_in_dossier` |
| Owners 5k cap | Full master scan rebuild of unique Accurate owners | catalog phase |
| FULL while truncated | `coverage=FULL` only when scanned==expected, skipped=0, incomplete empty | `full_when_clean`, `coverage_label_honest` |
| No resume | `CHECKPOINT.json` + ObjectId restore | `resume_*` UAT PASS |
| Secrets in report | Masked URI; PII keys redacted; sanitization proof | `OUTPUT_SANITIZATION_PROOF.json` pass; secret scan clean |

### Mutation surface (runtime audit)

Only write attempts are **RO probes that must fail**. No `$out`/`$merge`, no ledger/order/SOP writes, no deploy/restart. Outputs only under explicit non-production `ETERNAL_RO_OUT_DIR` (forbidden under `/opt/eternal-packaging*`).

## UAT matrix (synthetic `eternal_ro_uat` @ 127.0.0.1:27019)

All **25/25 PASS** — see `UAT_RESULTS.json`.

Cases: SERVICE without parent, orphan print, ambiguous splits, partial shipments, reversal, multi-WH, missing proof path, zero-stock SKU, pagination boundaries, SSH/interrupt resume, RO enforcement, missing URI fail-closed, resource batch/timeouts.

## Verdict

| Field | Value |
|-------|--------|
| **SCRIPT_SAFETY** | **PASS** |
| **QUERY_LOAD_RISK** | **LOW** (defaults batch=100, query_max_ms=30000, paginated; raise to MEDIUM if Owner raises batch/timeout aggressively on huge prod) |
| **COVERAGE_EXPECTED** | **FULL** (when scanned==expected and incomplete empty; otherwise honest PARTIAL/INCOMPLETE) |
| **READ_ONLY_ROLE_VERIFIED** | **YES** (synthetic RO user; production requires Owner-provided RO URI — not created by agent) |
| **RESUME_TEST** | **PASS** |
| **RESOURCE_LIMIT_TEST** | **PASS** |

## EXACT_OWNER_COMMAND

Owner must supply a **dedicated read-only** Mongo URI (agent did not create production credentials). On the VPS, after placing this script tree:

```bash
ETERNAL_RO_EXPECT_SHA=27df94962dd67c1e63ef42e33dd34640c0de109c \
ETERNAL_RO_MONGO_URL='mongodb://<RO_USER>:<RO_PASS>@<HOST>:27017/<DB>?authSource=<AUTH_DB>&readPreference=secondaryPreferred' \
ETERNAL_RO_DB_NAME='<DB>' \
ETERNAL_RO_OUT_DIR="/tmp/eternal_ro_inv_audit_$(date -u +%Y%m%dT%H%M%SZ)" \
ETERNAL_RO_BATCH_SIZE=100 \
ETERNAL_RO_QUERY_MAX_MS=30000 \
ETERNAL_RO_BACKEND=/opt/eternal-packaging-runtime/current/backend \
bash /path/to/scripts/ro_full_inventory_gate/VPS_RO_FULL_INVENTORY_GATE_HARDENED.sh
```

If the RO URI string happens to equal app `MONGO_URL`, also set `ETERNAL_RO_ALLOW_SAME_AS_APP_URI=1` **only** when that URI is truly read-only (probes still must deny writes).

## STOP_CONDITIONS

1. `ETERNAL_RO_MONGO_URL` / `ETERNAL_RO_DB_NAME` missing  
2. RO write probes succeed → `STOP_RO_NOT_VERIFIED`  
3. `ACTIVE_SHA != EXPECT_SHA` → INCOMPLETE  
4. Output dir under `/opt/eternal-packaging*` or other forbidden paths  
5. Any skip/truncation/count mismatch → never claim FULL  
6. Do not run against production from this agent session  
7. Do not invent Belum=0 / mutate SOP / merge / deploy from audit outputs  
8. Peak-hour: keep batch≤100 and query_max_ms≤30000 unless Owner accepts MEDIUM load  

## Production execution status

**NOT RUN** (per task). Local synthetic UAT only.
