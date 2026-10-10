# Phase 3 — Accounting Activation Operator Runbook

Eternal Packaging ERP. Owner-operated. No automatic production activation.

## 1. Preflight (read-only)

Confirm:

- Deployed code includes Phase 3 capability contract + invoice edit fail-closed intent index check.
- Production Mongo access is read-only for this step.
- No feature flags are enabled yet.

Do **not** create indexes, grant permissions, or flip flags until preflight passes.

## 2. Required index inventory

| Name / equivalent | Collection | Unique | Key | Partial |
|---|---|---|---|---|
| `uniq_primary_source_journal` | `journal_entries` | yes | `source_type`, `source_id` | `{status:"posted", reversal_of:null}` |
| `uniq_reversal_of` | `journal_entries` | yes | `reversal_of` | `{reversal_of:{$type:"string"}}` |
| partial unique `idempotency_key` | `sales_invoices` | yes | `idempotency_key` | `{idempotency_key:{$type:"string"}}` |
| `auto_intent_idempotency_unique` (or equiv.) | `sales_invoice_auto_create_intents` | yes | `idempotency_key` | — |
| partial unique `idempotency_key` | `sales_receipts` | yes | `idempotency_key` | `{idempotency_key:{$type:"string"}}` |
| `auto_receipt_intent_idempotency_unique` (or equiv.) | `sales_receipt_auto_create_intents` | yes | `idempotency_key` | — |
| `uniq_sales_invoice_edit_revision_key` | `sales_invoice_edit_revisions` | yes | `invoice_id`, `idempotency_key` | — |
| `uniq_sales_receipt_edit_revision_key` | `sales_receipt_edit_revisions` | yes | `receipt_id`, `idempotency_key` | — |
| `uniq_sales_business_cancel_key` | `sales_business_cancellations` | yes | `document_type`, `document_id`, `idempotency_key` | — |

Incompatible same-name indexes are **not** READY.

## 3. Duplicate detection before index creation

Before creating `uniq_primary_source_journal`, detect duplicate posted primaries for `sales_invoice` / `sales_receipt`. If any groups exist, **stop** and remediate historically. Do not force-create the unique index over duplicates.

## 4. Owner-approved index DDL

Prepare DDL offline from the inventory above. Execute only with Owner approval in a controlled window. Application code must not create these indexes on write paths (invoice edit revision is check-only).

## 5. Opt-in permission inventory

Explicit grants only (never admin-default):

- `sales.invoice.cancel`
- `sales.receipt.cancel`
- `sales.receipt.edit`

Inventory Role & Hak Akses before activation. Do not grant via this runbook automatically.

## 6. Flag / allowlist dependency order

1. Indexes verified/created.
2. Opt-in permissions granted to named operators.
3. `financial_journal_revision_enabled` = true (allowlist still empty is safe).
4. Allowlist `sales_invoice`.
5. Enable `sales_invoice_edit_revision_enabled` and/or `sales_invoice_business_cancel_enabled` as needed.
6. Enable `auto_sales_invoice_lifecycle_enabled` after invoice idempotency + intent indexes ready.
7. Allowlist `sales_receipt`.
8. Enable receipt edit/cancel flags.
9. Optionally enable `receipt_create_requires_invoice_journal_posted`.
10. Enable `auto_sales_receipt_lifecycle_enabled`.

## 7. Invoice-first canary

- Enable invoice path only.
- Validate create → Simpan (`financial_complete`), Edit → Simpan, Hapus → verified cancel on **non-production** or synthetic data.
- Confirm capability endpoint reports `legacy_manual_actions_required: false` for authorized users.
- Confirm Finalisasi / Post / Reverse hidden in UI for those users.

## 8. Receipt canary (after Invoice)

- Expand allowlist / flags for receipt.
- Validate create/edit/cancel + sibling AR + credit blockers on synthetic data.
- Confirm receipt capability and UI hide of Post/Reverse.

## 9. Accounting / AR reconciliation checks

Run approved read-only reconciliation scripts (AR vs GL, receipt journals) after canary. Stop on unexplained imbalance.

## 10. Stop conditions

- Missing/incompatible unique index.
- Duplicate posted primaries.
- Capability reports unavailable while flags appear ON.
- Incomplete revision/cancel intents stuck without recovery.
- UI hides legacy controls while `legacy_manual_actions_required` remains true (bug).

## 11. Rollback

1. Set lifecycle / edit / cancel flags `enabled: false`.
2. Set allowlist `source_types: []`.
3. Legacy Finalisasi / Post / Reverse reappear (`legacy_manual_actions_required: true`).
4. Do not drop indexes during rollback unless Owner explicitly requires.

## 12. Recovery for incomplete revision / cancellation

- Retry the **same** idempotency key / intent.
- Do not start a second reverse/repost with a new key while an incomplete fence exists.
- Use existing recover-auto endpoints only for auto-lifecycle Pending/Failed documents.

## 13. Conditions for hiding legacy UI buttons

Hide Finalisasi / Post Accounting / Reverse only when backend `lifecycle_capability.legacy_manual_actions_required === false` (auto create/post available for the user). Frontend must not guess flags. Recover/Edit/Hapus remain governed by their own capability and permissions.
