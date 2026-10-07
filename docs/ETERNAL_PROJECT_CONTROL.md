# ETERNAL ERP — Project Control Center

> **Control rule:** No evidence = no DONE.  
> This file is a control board, not a guess board. Any status without current evidence must be `NEEDS VERIFY` or `UNKNOWN`.

## Status legend

| Status | Meaning |
|---|---|
| ✅ DONE | Evidence exists: commit/PR/test/deploy/smoke verification |
| 🟡 IN PROGRESS | Active branch/PR/work is in progress |
| 🔴 BLOCKED | Known blocker/error prevents completion |
| 👀 NEEDS VERIFY | Likely/previously completed but current verification is incomplete |
| ⬜ NOT STARTED | No evidence work has started |
| ❓ UNKNOWN | Required evidence is unavailable |

## Evidence policy

A module may only be marked **DONE** when the applicable evidence exists:
1. **CODE** — commit/PR evidence.
2. **TEST** — focused test/CI evidence.
3. **MERGE** — merged PR / merge SHA.
4. **DEPLOY** — exact production SHA or deploy output.
5. **OWNER VERIFY** — production smoke test / owner screenshot where required.

Production status is never inferred from GitHub alone.

---

## Current control snapshot

**Repository:** `prasnda21-tech/eternal-packaging`  
**GitHub main observed:** `55cc5331347ea9dd5665173e309d9d14d8d0b246` — Merge PR #505  
**Production last owner-verified:** `cda987b7a9cedb8ee9ada9a080c55a55047a4529` — PR #495 deployment output  
**Production current exact SHA:** 👀 **NEEDS VERIFY** before claiming it matches current main.

### Important drift

GitHub `main` has moved after the last owner-verified production SHA. Recent merged work includes:
- PR #497 — sales table header filters
- PR #500 — invoice status filter consolidation
- PR #501 — operator payment status
- PR #502 — customer pricing group simplification
- PR #503 — generic invoice header designer
- PR #505 — generic invoice footer designer

**Control consequence:** do not say production contains those newer merges unless production SHA/assets are checked again.

---

## Current blockers / attention

| Priority | Area | Status | Evidence / reason | Next action |
|---|---|---|---|---|
| P1 | HOLDING role → Cabang & Gudang | 🔴 BLOCKED | Owner production screenshot showed `Gagal memuat cabang dan gudang`; menu not visible for HOLDING | Audit exact permission keys and role assignment |
| P1 | HOLDING role → HPP Pembelian | 👀 NEEDS VERIFY | HPP Pembelian absent from HOLDING menu screenshot | Audit nav + view permissions |
| P1 | Production vs current main | 👀 NEEDS VERIFY | Last verified production SHA is older than current GitHub main | Read-only production fingerprint/SHA check |
| P2 | Production smoke test after #495 | 🟡 IN PROGRESS | Stock Saat Ini loads; role-dependent gaps remain | Complete role-aware smoke matrix |

---

## Purchasing / Inventory / HPP control matrix

| Module | Code | Test | Merge | Deploy | Owner verify | Evidence |
|---|---:|---:|---:|---:|---:|---|
| Permintaan | ✅ | ✅ | ✅ | ✅ | 👀 | PR #495 / production #495 package |
| Pesanan Pembelian | ✅ | ✅ | ✅ | ✅ | 👀 | PR #495 |
| Uang Muka | ✅ | ✅ | ✅ | ✅ | 👀 | PR #495 |
| Penerimaan Barang R2 | ✅ | ✅ | ✅ | ✅ | 👀 | PR #495 |
| Retur Pembelian | ✅ | ✅ | ✅ | ✅ | 👀 | PR #495 |
| Faktur Pembelian | ✅ | ✅ | ✅ | ✅ | 👀 | PR #495 |
| Pembayaran Pembelian | ✅ | ✅ | ✅ | ✅ | 👀 | PR #495 |
| HPP Pembelian | ✅ | ✅ | ✅ | ✅ | 🔴 | HOLDING permission/menu requires audit |
| Cabang & Gudang | ✅ | ✅ | ✅ | ✅ | 🔴 | HOLDING role cannot currently load branch/warehouse context |
| R2 physical stock / Stock Saat Ini | ✅ | ✅ | ✅ | ✅ | 🟡 | Production page opens; role/branch loading issue remains |
| Opening Balance / Saldo Awal | ✅ | ✅ | ✅ | ✅ | 👀 | Button visible in production; transactional smoke not yet recorded here |
| Stock Opname | ✅ | ✅ | ✅ | ✅ | 👀 | Entry button visible in Stock Saat Ini; full smoke not yet recorded here |
| Nilai Persediaan | ✅ | ✅ | ✅ | ✅ | 👀 | Menu visible; full production smoke not yet recorded here |
| First-GR bootstrap | ✅ | ✅ | ✅ | ✅ | 👀 | PR #495 package |
| Purchase Return valuation/reversal | ✅ | ✅ | ✅ | ✅ | 👀 | PR #495 package |
| Stock Opname valuation | ✅ | ✅ | ✅ | ✅ | 👀 | PR #495 package |

### #495 evidence

- Approved PR head before merge: `71c8193704c7fcb291a4b54c00fd5c6f2f380ae1`
- Merge commit: `cda987b7a9cedb8ee9ada9a080c55a55047a4529`
- Backend focused CI: PASS
- Frontend tests/build CI: PASS
- Owner deploy evidence: source/frontend activated, backend restarted, service active, public HTTP 200, Mongo operational
- Deploy explicitly reported: no migration, no backfill, no seed, no manual stock mutation

---

## Open PR watch

| PR | Title | State | Control status |
|---|---|---|---|
| #504 | Public catalog configuration | OPEN / draft | 🟡 IN PROGRESS — not deployed |
| #446 | Sales report/print hardening work | OPEN / draft | 👀 NEEDS REVIEW before any merge/deploy |

Do not infer deployment from PR state.

---

## Next action queue

1. **P1 — Audit HOLDING permissions only**
   - identify exact permission keys for Cabang & Gudang, Stock Saat Ini dependencies, and HPP Pembelian;
   - verify whether missing access is role configuration vs application permission guard;
   - no broad refactor.

2. **P1 — Re-verify production SHA**
   - production has been owner-verified at #495 SHA, but GitHub main has moved;
   - read exact production SHA before claiming newer PRs are live.

3. **P1 — Complete production smoke matrix**
   - Admin;
   - HOLDING;
   - Purchasing menu;
   - Stock Saat Ini;
   - Saldo Awal;
   - Stock Opname;
   - Nilai Persediaan;
   - HPP Pembelian.

4. **P2 — Review open PR #504**
   - confirm owner intent;
   - CI;
   - merge readiness;
   - deploy separately only with approval.

---

## Operational rules

- Cursor codes only after scope/design is approved where applicable.
- Manual production deploy only.
- Never auto-deploy from this control board.
- Never mark production **DONE** from GitHub merge alone.
- When `main` moves, compare against the last verified production SHA.
- If evidence conflicts, downgrade status to **NEEDS VERIFY** and investigate.
- Preserve existing production behavior unless the owner explicitly approves a change.
- One bounded audit for the changed module; avoid broad unrelated audits.

---

## Update protocol

When updating this file:
1. Fetch current GitHub `main`.
2. Read relevant PR/CI state.
3. Preserve exact SHAs.
4. For production claims, require production evidence.
5. Update only rows whose evidence changed.
6. Add blockers before marking completion.
7. Never replace `UNKNOWN` with a guess.
