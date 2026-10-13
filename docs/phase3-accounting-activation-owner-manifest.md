# Phase 3 — Owner Activation Decision Package

Source of truth reviewed: `origin/main` @ `460cd9d` (2026-10-13).
Local preview only for index DDL proof. **No production mutation performed.**

## Verdict precondition

Code + focused tests + local capability contract are ready.
Production remains **not fully complete** until Owner-approved activation window finishes non-destructive verification.

## A. Required unique indexes (DDL — Owner approval required)

Do **not** create these until duplicate preflight passes on production (read-only).

| Collection | Name | Unique | Key | Partial |
|---|---|---|---|---|
| `journal_entries` | `uniq_primary_source_journal` | yes | `source_type`, `source_id` | `{status:"posted", reversal_of:null}` |
| `journal_entries` | `uniq_reversal_of` | yes | `reversal_of` | `{reversal_of:{$type:"string"}}` |
| `sales_invoices` | `idempotency_key_partial_unique` (or equiv.) | yes | `idempotency_key` | `{idempotency_key:{$type:"string"}}` |
| `sales_invoice_auto_create_intents` | `auto_intent_idempotency_unique` (or equiv.) | yes | `idempotency_key` | — |
| `sales_receipts` | `idempotency_key_partial_unique` (or equiv.) | yes | `idempotency_key` | — same partial |
| `sales_receipt_auto_create_intents` | `auto_receipt_intent_idempotency_unique` (or equiv.) | yes | `idempotency_key` | — |
| `sales_invoice_edit_revisions` | `uniq_sales_invoice_edit_revision_key` | yes | `invoice_id`, `idempotency_key` | — |
| `sales_receipt_edit_revisions` | `uniq_sales_receipt_edit_revision_key` | yes | `receipt_id`, `idempotency_key` | — |
| `sales_business_cancellations` | `uniq_sales_business_cancel_key` | yes | `document_type`, `document_id`, `idempotency_key` | — |

Compatibility rule: name alone is insufficient — key order, `unique`, and `partialFilterExpression` must match.

### Preflight (production, read-only)

1. Inventory each index above; mark READY / MISSING / INCOMPATIBLE.
2. Detect duplicate posted primaries:

```js
db.journal_entries.aggregate([
  { $match: {
      status: "posted",
      reversal_of: null,
      source_type: { $in: ["sales_invoice", "sales_receipt"] }
  }},
  { $group: { _id: { st: "$source_type", sid: "$source_id" }, n: { $sum: 1 } } },
  { $match: { n: { $gt: 1 } } }
])
```

If any group exists → **STOP**. Do not force unique index creation.

### Local proof (non-production)

On `eternal_packaging_preview` (2026-10-13): after creating the missing indexes above, readiness helpers all returned `true`. Duplicate posted primaries: **0**.

## B. Feature flags / allowlist (production state: UNKNOWN from this agent)

Do not assume ON/OFF from code defaults. Confirm with approved production read-only query on `app_settings`.

Ordered enablement (after indexes READY):

1. `financial_journal_revision_enabled` = true (allowlist may stay empty)
2. Allowlist `sales_invoice` in `financial_journal_revision_source_allowlist`
3. `sales_invoice_edit_revision_enabled` and/or `sales_invoice_business_cancel_enabled` as needed
4. `auto_sales_invoice_lifecycle_enabled` = true
5. Invoice canary + AR reconciliation (synthetic / approved non-prod or Owner-approved canary docs)
6. Allowlist `sales_receipt`
7. Receipt edit/cancel flags as needed
8. Optional: `receipt_create_requires_invoice_journal_posted` = true
9. `auto_sales_receipt_lifecycle_enabled` = true
10. Receipt canary + reconciliation

**Never enable receipt automation before invoice journal prerequisites are valid.**

## C. Opt-in permissions (never auto-grant to all admin)

| Permission | Purpose |
|---|---|
| `sales.invoice.cancel` | Business soft-cancel Faktur |
| `sales.receipt.cancel` | Business soft-cancel Penerimaan |
| `sales.receipt.edit` | Edit / posted revision Penerimaan |

### Proposed named operators (Owner confirm before grant)

Based on Role & Hak Akses catalog + who already operates Penjualan finance flows:

| Candidate | Suggested grants | Rationale |
|---|---|---|
| Named finance / accounting operators (Owner list) | all three | Primary operators of Faktur/Penerimaan finance |
| `admin` (named accounts only, explicit override) | only if they perform cancel/edit in production | Admin role defaults **exclude** these OPT_IN_ONLY keys |
| `head_operations` / `head_production` | only if they currently cancel/edit receipts/invoices | Have create/view invoice+receipt defaults; cancel/edit are still opt-in |

**Do not** bulk-grant via role wildcard. Prefer per-user `permissions_override` or explicit Role & Hak Akses rows for named people.

## D. Activation order (single window)

A. Backup + rollback readiness (`scripts/backup-mongodb.sh` / approved VPS backup path).
B. Production index inventory + duplicate preflight → Owner-approved DDL.
C. Grant confirmed opt-in permissions.
D. Journal revision flag + invoice allowlist.
E. Invoice edit/cancel flags as required.
F. Invoice auto lifecycle flag → capability `legacy_manual_actions_required: false` for authorized users.
G. Invoice canary (create Simpan → financial_complete; edit; Hapus) + AR/GL read-only recon.
H. Receipt allowlist + edit/cancel + optional receipt-create journal gate.
I. Receipt auto lifecycle → receipt canary + recon.
J. Final functional verification; stop on capability/UI mismatch.

## E. Backup / rollback

1. Take Mongo backup before DDL and before flag flips.
2. Rollback flags: set lifecycle/edit/cancel/`financial_journal_revision_enabled` to `enabled: false`; clear allowlist `source_types` / `value` to `[]`.
3. UI restores Finalisasi/Post/Reverse (`legacy_manual_actions_required: true`).
4. Do **not** drop unique indexes on rollback unless Owner explicitly requires.
5. Incomplete revision/cancel: retry same idempotency key; use recover-auto only for auto-lifecycle Pending/Failed.

## F. Bounded verification after activation

- Capability endpoints report expected availability for authorized users only.
- UI hides Finalisasi/Post/Reverse **only** when `legacy_manual_actions_required === false`.
- Duplicate Simpan does not double-post journals.
- Partial failure shows Pending/Failed + recover, never false financial completion.
- Hapus is soft-cancel with reversal when posted; history retained.

## G. Explicitly NOT authorized by this package until Owner signs

- Production Mongo DDL
- Production feature flag / allowlist writes
- Production permission grants
- Production deploy of new source (none required for this closeout — code already on main)
- Production financial canary writes
- Destructive duplicate remediation

## Owner decision checklist

- [ ] Approve production read-only index inventory + duplicate preflight results
- [ ] Approve DDL list above (or amended)
- [ ] Confirm named operators for the three OPT_IN permissions
- [ ] Approve single activation window following order D
- [ ] Approve backup owner + rollback contact
