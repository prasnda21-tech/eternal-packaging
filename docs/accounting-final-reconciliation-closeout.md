# Final Accounting Reconciliation & Disaster Recovery — Closeout

Source reviewed: `origin/main` @ merge tip including accounting automation Phase 1–3.
Agent environment: no production Mongo URI; production inventory uses Owner-confirmed baseline + code contracts.
**No production writes, migrations, backfills, flag changes, permission grants, backup deletes, or restores into production.**

## ACCOUNTING_SOURCE_OF_TRUTH

| Concern | Source of truth | Notes |
|---|---|---|
| Primary posted journal | `journal_entries` where `reversal_of` is null, `status=posted`, `source_type` ∈ {`sales_invoice`,`sales_receipt`} | Unique index `uniq_primary_source_journal` (partial) |
| Reversal journal | `journal_entries` with `reversal_of` string; `source_type` ∈ {`sales_invoice_reversal`,`sales_receipt_reversal`} | Unique `uniq_reversal_of` |
| Edit revision reverse/repost | Same journal core; prior primary → `reversed`; new posted primary is current effect | Allowlist must include source type |
| Business cancellation | Soft-cancel on document; posted docs reverse first; unposted cancel does not fabricate reversal | `sales_business_cancellations` intents |
| Settlement-effective | `receipt_counts_toward_outstanding`: cancelled never; explicit `False` never; **absent field = legacy counts**; `True` counts | Not the same as “has journal” |
| Legacy historical receipts | Field `settlement_effective` absent → still settlement-effective operationally | Explains journals ≫ `settlement_effective=true` |
| Customer credit | `sales_customer_credit_application` (+ reversal) journals + applications collection | Separate AR source types |
| Piutang accounting | `accounts_receivable`: `journal_entries` + `journal_lines` for sales AR sources | **Not** `sales_invoices.outstanding_amount` |
| API `accounting_posted` / `accounting_status` | Projection attached at read time from journals | **Not** Mongo SoT |

## POSTING_FLAG_ANALYSIS

Owner baseline `live_accounting_posted_flag=0` on finalized invoices and non-cancelled receipts is **expected**, not a projection bug:

- `accounting_posted` / `accounting_status` are set in `attach_*` / list enrichers from `journal_entries`.
- Posting paths persist `accounting_posted_at` (receipt heal) but do **not** persist `accounting_posted: true` as SoT.
- Counting Mongo docs with `accounting_posted: true` therefore returns 0 while journals show posted primaries.

**Do not backfill `accounting_posted` flags.** Use journals.

## INVOICE_RECONCILIATION (baseline interpretation)

Owner baseline: finalized=171, cancelled=7, posted primary `sales_invoice` journals=157.

Code-contract interpretation (not automatic error):

- Gap finalized − posted primary ≈ 14 → classify via Owner script categories:
  - `legacy_finalized_never_posted` (pre-automation / intentional)
  - `review_finalized_accounting_reversed` (reversed, still finalized)
  - incomplete auto lifecycle (Owner prior incomplete counts = 0)
- Cancelled with history expected after reverse+soft-cancel.
- Duplicate posted primaries: Owner reported 0 (matches hard gate).

Run `scripts/accounting_final_reconciliation_audit.py` on production for exact category counts (PII-free).

## RECEIPT_RECONCILIATION (baseline interpretation)

Owner baseline: settlement_effective non-cancelled=22, cancelled=2, posted primary `sales_receipt` journals=135.

Code-contract interpretation:

- Prior audit counted only `settlement_effective: true`.
- Legacy receipts with **absent** field still count toward settlement and may hold journals.
- Therefore journals (135) ≫ true-flag (22) is **explained by legacy semantics**, not automatic missing/duplicate financial effect.
- Confirm only via per-document category `legacy_settlement_absent_active_posted` (never journal-vs-true count deltas).

## AR_GL_RECONCILIATION

- Accounting AR = posted journal movements on AREC accounts for sales AR source types.
- Operational outstanding on invoices is a separate projection; inequality ≠ GL bug by itself.
- Hard anomalies to escalate: duplicate active primaries, cancelled+still-active-posted, settlement-effective with zero journal when counts=true, orphan posted primaries (capped scan).

## LEGACY_DATA_FINDINGS

- Invoice: finalized without journals expected for pre-auto-lifecycle history unless category proves otherwise.
- Receipt: absent `settlement_effective` is intentional legacy compatibility in `receipt_counts_toward_outstanding`.
- No speculative backfill recommended from count gaps alone.

## CANCEL_REVERSAL_SAFETY

Code (`sales_business_cancel`, posting reverse, edit revision):

- Posted → reverse journal then soft-cancel / revision repost.
- Unposted → soft-cancel without fabricating reversal.
- Receipt cancel forces `settlement_effective=false`.
- Unique indexes + claims fence concurrency.
- Owner: incomplete lifecycle counts 0; cancel/edit flags ON; allowlist includes invoice+receipt.

## BACKUP_STATUS / BACKUP_TIMER / BACKUP_INTEGRITY

| Item | Evidence |
|---|---|
| Script | `scripts/backup-mongodb.sh` — mongodump gzip, local `gzip -t`, SHA-256, S3 helper, local keep=3, protected S3 name |
| S3 prefix lock | `production/backups/mongodb/` (fail-closed unless override) |
| Local retention | Keep latest N valid gzip; refuse deletes if keep set empty |
| Owner baseline | Local archives=3, status=SUCCESS, timer ENABLED |
| Agent local units | systemd unit files not present in this cloud workspace (runtime on VPS) |
| Synthetic local safety | `scripts/tests/test_backup_mongodb_local_safety.sh` |

## ISOLATED_RESTORE

| Item | Result |
|---|---|
| Production restore | **NOT EXECUTED** (forbidden) |
| Isolated synthetic restore | `scripts/tests/test_isolated_backup_restore.sh` — localhost mongod, dump → restore to **different** DB name, count verify, teardown |
| Production archive restore | **BLOCKED until Owner** provides an isolated non-prod host + copy of archive (never restore into production DB name / URI) |

Owner DR steps (stop before prod mutation):

1. Copy one local archive to an isolated host (not production Mongo).
2. `gzip -t` + compare SHA-256 to backup log.
3. Start disposable mongod (or dedicated restore VM).
4. `mongorestore --archive=... --gzip --nsFrom='<prod_db>.*' --nsTo='eternal_packaging_restore_verify.*'`
5. Compare collection counts only; drop restore DB; do not point app at it.

## CONFIRMED_ANOMALIES

From code + Owner baseline (no new prod query in this agent):

- **None proven** as financial corruption.
- Count gaps and `live_accounting_posted_flag=0` are **explained by contracts**.
- Exact anomaly category counts require Owner run of the new RO script.

## RECOMMENDED_CORRECTIONS

1. Owner: run `accounting_final_reconciliation_audit.py` once; retain stdout (counts only).
2. Owner: perform isolated restore of one production archive on non-prod host (procedure above).
3. **No** journal backfill, **no** `accounting_posted` field backfill, **no** settlement_effective mass update without category proof.
4. If script reports `anomaly_*` > 0, investigate those IDs offline (do not paste PII into chat).

## REMAINING_BLOCKERS

1. Production RO reconciliation category totals not yet re-run with this script (Owner action).
2. Production backup **restore** not verified on an isolated host (Owner action).
3. Agent cannot access production Mongo / backup archives / systemd timer runtime from this environment.

## VERDICT

**READY_FOR_OWNER_FINAL_VERIFICATION**

Capability/automation/indexes/flags/permissions/backup-SUCCESS are Owner-confirmed; remaining work is Owner execution of (1) precise RO category audit and (2) isolated restore of a real archive — not new product features.
