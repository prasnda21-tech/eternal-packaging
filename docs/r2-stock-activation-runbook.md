# Stock R2 Activation Runbook (Eternal Packaging)

**Status:** DRAFT — do **not** execute on production from this document alone.  
**Related code:** Draft PR #591 (compatibility + Add On `parent_line_id` + R2 Adjustment/Opname).

## Production conditions (as of OWNER brief)

| Item | State |
|---|---|
| MongoDB | 8 **standalone** (no multi-document transactions) |
| Auth | Enabled; app user `readWrite` only |
| `r2_authority_active` | **OFF** |
| Delivery cutover | **Not** initialized |
| Owner downtime tolerance | ≤ 15 minutes (claim **not** proven without rehearsal) |

## Absolute boundaries

- NO production write from this PR merge alone.
- NO production DB conversion / migration / restart / deploy / R2 activation without separate OWNER approval and rehearsal evidence.
- Activation remains **BLOCKED** until every prerequisite below is green.

## Why activation is BLOCKED today

1. Standalone MongoDB → `require_transaction_support()` rejects Adjustment, Opname cutoff/confirm, delivery confirm R2, opening posts.
2. App credential is `readWrite` only → cannot initiate replica-set reconfiguration without a separate admin principal.
3. Cutover not initialized; R2 authority OFF (correct until RS + rehearsal succeed).
4. ≤15 minute downtime is **not** guaranteed without timed rehearsal + restore drill evidence.

## Schema / data contract (no forced historical rewrite)

| Field | Where | Meaning |
|---|---|---|
| `line_id` | `orders.line_items[]` | Stable line identity (auto-filled when setting Add On parent) |
| `parent_line_id` | Add On line → parent `line_id` | Explicit process→physical link |
| `parent_line_idx` | Optional fallback | Integer parent index |
| `item_type=SERVICE` | `master_items` | Process work — excluded from physical stock demand |

Historical POs without `parent_line_id` stay **fail-closed** (`BELUM_TERVERIFIKASI`) for siap kirim when SERVICE process lines are present. Do **not** backfill by name matching.

API (opt-in): `PATCH /api/orders/{order_id}/line-items/addon-parent`

## Prerequisites checklist (OWNER)

1. **Backup** — latest consistent dump/snapshot; store off-box; record checksum + timestamp.
2. **Restore rehearsal** — restore backup to isolated host; measure wall-clock; document RTO. If RTO + RS convert > 15 minutes, downtime claim is rejected.
3. **Admin Mongo access** — named admin principal (not app `readWrite`) with authority to convert to single-node replica set; credential vaulted.
4. **Maintenance window** — OWNER-approved; communications plan; freeze writes.
5. **Single-node Replica Set convert** (admin only) — initiate RS on existing data; verify `hello.setName`, `isWritablePrimary`, app connectivity.
6. **Transaction smoke** — from app: Adjustment dry-run or lab opname confirm succeeds; standalone rejection path no longer applies.
7. **Rollback plan** — documented reverse steps if RS convert fails; tested on rehearsal clone.
8. **Delivery cutover init** — `initialize_delivery_cutover_baseline` on production only after RS green; require `blocked=false`, `ambiguous_count=0`.
9. **R2 authority ON** — OWNER toggle only after cutover success; monitor Stock Saat Ini + delivery confirm.
10. **First Saldo Awal / Opname** — per warehouse; Qty Tersedia valid only when baseline reconciled **and** Belum Terkirim verified.

## Suggested activation order

```
backup → restore drill → admin RS convert (window) → txn smoke
  → cutover init → r2_authority_active=true → opening/opname → monitor
```

## Code readiness vs production readiness

| Layer | Status after PR #591 (local) |
|---|---|
| Compatibility Qty Pesanan / Belum / Add On SERVICE | Ready (tests) |
| Explicit `parent_line_id` completion | Ready (tests); historical without link fail-closed |
| Adjustment + Opname txn + idempotency | Ready on RS (existing + new tests) |
| Standalone mutation rejection | Ready (fail-closed) |
| **Production activation** | **BLOCKED** until RS + rehearsal + OWNER steps above |

## Evidence required before claiming ≤15 minute downtime

- Timed restore drill log
- Timed RS convert on production-like clone
- Documented rollback duration
- Sign-off that sum of critical path ≤ window

Without that evidence, state: **downtime ≤15 minutes NOT proven**.
