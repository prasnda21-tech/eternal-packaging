# Eternal Packaging — Sistem Produksi (PRD)

## Tech Stack
React + FastAPI + MongoDB. Emergent Object Storage + Emergent LLM key. Accurate Online integration (LIVE OAuth ready, MOCK when creds absent).

## What's implemented (state of 2026-02)
- Mobile-first PO tracking, HoP Dashboard, Surat Jalan, Multi-photo Delivery Proofs, Import PO (Accurate/File/PDF with layout-aware parser, PDF asli attached via Object Storage).
- Granular RBAC + Settings UI (Karyawan / Role & Hak Akses / Audit Log).
- **Phase A workflow enhancements (Feb 2026):**
  - **Req 1**: Approval Design/Payment gated by prior attachment. Re-upload resets approval to Waiting with history entry. Fields: `design_approved_by`, `design_approved_at`, `design_approval_history[]`, same for payment.
  - **Req 2**: HOLD gate at stage 4→5 (Approval Payment → Penjadwalan). `GET /orders/{id}` returns `hold: bool` + `hold_reasons: []`. Advance from stage 4 refused (400 with `hold_reasons`) unless `(design_approved OR design_bypassed) AND (payment_approved OR payment_bypassed)`.
  - **Req 7**: `POST /orders/{id}/rollback` — perm `workflow.rollback`, requires `to_stage` (< current) + `reason`. Adds `rollback_history[]`; resets `design_status`/`payment_status` when rolling back before their approval stage (bypass flags retained by design).
  - **Req 9**: `POST /orders/{id}/helper-prep` — perm `helper.prepare`. Stores `helper_prep = { qty_required, qty_prepared, qty_shortage=max(req-prep,0), shortage_status, notes, updated_by, updated_at }`. Never mutates PO qty.
  - **Req 10**: `POST /orders/{id}/screener-info` — perm `screen.update`. Info-only: `screener_info = { screen_qty, notes, updated_*}`. Never advances stage or changes PO qty.
  - **Req 11**: Two separate permissions `design.bypass_approval` + `payment.bypass_approval`. Endpoints `/orders/{id}/design/bypass` + `/payment/bypass` require reason. Bypass ≠ Approved (separate flags `design_bypassed`, `payment_bypassed`, plus reason/by/at). Bypass banner in UI.

### New permissions (added to catalog)
`design.bypass_approval`, `payment.bypass_approval`, `workflow.rollback`.

### New MongoDB fields (backward-compatible, additive only)
`orders`: `design_approved_by`, `design_approved_by_name`, `design_approved_at`, `design_approval_history[]`, `design_bypassed`, `design_bypassed_by`, `design_bypassed_by_name`, `design_bypassed_at`, `design_bypass_reason`, mirror fields for payment, `helper_prep`, `screener_info`, `rollback_history[]`, `hold` (computed, snapshot in reads), `hold_reasons` (computed).

### Audit-log actions (all with target `po:<po_number>`)
`design.upload`, `design.approve`, `design.approve.revoke`, `design.bypass`, `payment.upload`, `payment.approve`, `payment.approve.revoke`, `payment.bypass`, `workflow.rollback`, `helper.prep.update`, `screener.info.update`.

## Req 5 — DONE (Feb 2026, iteration_15 PASS 13/13)
- Permissions `po.cancel` and `po.delete` in catalog. Default: admin has both; head_production `po.cancel` only.
- Endpoints:
  - `POST /orders/{id}/cancel` — reason wajib. Sets `cancelled=true`, `cancelled_by/at`, `cancel_reason`, `status_label='Cancelled'`. Timeline entry retained. Audit `po.cancel`.
  - `GET  /orders/{id}/delete-impact` — snapshot data yang akan hilang. Perm `po.delete`.
  - `DELETE /orders/{id}` — reason + strong confirm (PO number OR literal `HAPUS`). Hard-deletes order doc, soft-marks files, audit `po.delete` BEFORE delete with impact snapshot.
  - `POST /orders-bulk/cancel` / `POST /orders-bulk/delete` — bulk variants; bulk delete requires `confirm='HAPUS'`.
- `GET /orders?status={aktif|hold|selesai|cancelled}` filter. Aktif default. Response includes `counts:{aktif,selesai,cancelled}`.
- Frontend Dashboard: STATUS_TABS + checkboxes + bulk-bar (`bulk-cancel-button`, `bulk-delete-button`).
- Frontend PODetail: `doCancelPO` + `doDeletePO` with impact preview + double-prompt confirmation.
- **Guard `_ensure_not_cancelled(order)`** — injected into every workflow mutation endpoint (advance, approve-design, approve-payment, bypass_design, bypass_payment, rollback, helper-prep, screener-info, urgent, attachment, note, loading, packed, delivered, assign-operators, operator-progress, delivery-proof, delete_delivery_proof). Read (`GET /orders/{id}`), cancel, delete-impact and delete are exempt so cancelled POs can still be viewed and permanently deleted.
- `_persist_order` sets `status_label='Cancelled'` for cancelled orders (consistent).
- Duplicate protection (`_find_existing_import`) still finds Cancelled POs (blocks re-import). Deleted POs (hard-delete) allow re-import of the same PO number.

## Phase B — B1+B2+B3 DONE (Feb 2026, iteration_19 100% PASS)

### B1 — Role & Super Admin Management
- **Roles collection** (`db.roles`) seeded from built-in list; UI dapat Tambah/Edit/Nonaktifkan/Aktifkan/Hapus role custom.
- Backend endpoints: `POST/PATCH /admin/roles`, `POST /admin/roles/{r}/activate|deactivate`, `DELETE /admin/roles/{r}` (guard user_count>0), `PUT /admin/roles/{r}/permissions`.
- **Super Admin**: field `is_super_admin` di user. SA punya semua permissions runtime. Endpoints: `POST /admin/users/{id}/super-admin/grant|revoke|transfer`. Invariant: minimal 1 SA aktif; last SA tidak bisa direvoke/dinonaktifkan/ditransfer tanpa target aktif.
- Bootstrap: startup mempromosikan `admin@eternalpackaging.com` menjadi SA jika belum ada SA. Drift protection: paksa role='admin' & name='Admin'.
- Audit lengkap: role.create/update/delete/activate/deactivate/permissions.update, super_admin.grant/revoke/transfer.
- **Phase B migration** (`PHASE_B_ROLE_TOP_UPS`): startup idempotent menambah `dashboard.production.view/export` (+ `po.cancel` untuk head_operations) ke override lama.

### B2 — Dashboard Jadwal Produksi (Tabel) + Excel Export
- Permission baru: `dashboard.production.view`, `dashboard.production.export`.
- `GET /api/dashboard/production` → real Mongo rows dengan seluruh kolom Req 6 (deadline/priority/customer/outlet/operator splits/design/payment/HOLD/screener/helper/packing/DN/loading/driver/status).
- `POST /api/dashboard/production/export` → .xlsx 32 kolom, worksheet **Jadwal Produksi** (freeze header, autofilter, tanggal Excel-date, qty numeric, no merged cells) + **Ringkasan** (count aktif/HOLD/urgent/overdue/selesai/cancelled/PO per operator). Payload {ids, include_cancelled, include_completed, scope}. Audit `dashboard.production.export`. Read-only (tidak menulis ke orders).
- Frontend page `/produksi` (ProductionSchedule.jsx): toggle **Kartu/Tabel**, sticky header + horizontal scroll, filters (status, customer, operator, screener, helper, packing, delivery, urgent, HOLD, include cancelled/completed, tanggal from/to, search), tombol Export Terfilter & Export Semua.
- NavLink "Jadwal Produksi" muncul di top-nav bila user punya `dashboard.production.view`.

### B3 — Progress Stage Clickable Drawer
- `StageBar` sekarang menerima `onStageClick` — 16 node clickable dengan `data-testid=stage-node-{i}`, focus-visible, hover animate.
- `StageDetailDrawer` baru: drawer read-only per tahap. Mapping index 0-15 → order/design/payment/schedule/screener/helper/operator/packing/DN/delivery blocks. Berkas & histori tahap ditampilkan.
- **No side-effect**: buka/tutup drawer TIDAK mengubah workflow (verified: current_stage/timeline/status_label identical before & after).

## Import PO Permissions (Feb 2026, DONE)
- 5 permission baru di kelompok "Import PO" pada `PERMISSION_CATALOG`:
  - `po.import.view` — Buka halaman Import PO (gate untuk halaman + list Accurate + status)
  - `accurate.import` — Preview & Import Single PO dari Accurate
  - `accurate.import.bulk` — Preview & Import beberapa Sales Order Accurate sekaligus
  - `po.import.pdf` — Import PO dari file PDF
  - `po.import.spreadsheet` — Import PO dari file XLSX / CSV
- Admin (`ROLE_DEFAULT_PERMISSIONS["admin"] = list(ALL_PERMISSIONS)`) otomatis dapat semua permission baru. Super Admin bypass tetap penuh.
- Migration idempotent (`PHASE_B_ROLE_TOP_UPS["admin"]`) top-up 5 permission ini ke override `db.role_permissions` yang sudah persisted sebelum permission baru dibuat.
- Backend enforcement (bukan hanya frontend hide):
  - `GET /api/accurate/status`, `GET /api/accurate/list`, `GET /api/accurate/import/logs`, `GET /api/accurate/import/available` → `po.import.view`
  - `POST /api/accurate/import/preview`, `.../confirm` → `accurate.import`
  - `POST /api/accurate/import/preview-bulk`, `.../confirm-bulk` → `accurate.import.bulk`
  - `POST /api/import/pdf/preview` → `po.import.pdf`
  - `POST /api/import/file/preview` → `po.import.spreadsheet`
  - `POST /api/import/file/confirm` → `po.import.pdf` bila `source=pdf`, `po.import.spreadsheet` selain itu (dienforce inline via `_require_perm`).
- Frontend:
  - `App.js` — `/admin/import` route pakai `PermGate anyOf=["po.import.view"]` (bukan lagi `RoleGate allowed=["admin"]`).
  - `AppShell.jsx` — NavLink Import PO tampil hanya bila `hasPerm("po.import.view")`.
  - `ImportPO.jsx` — inisial `mode` dipilih dari tab yang boleh diakses. Tab & panel Accurate/File/PDF hanya render bila permission terkait ada. Bulk bar + checkbox column disembunyikan bila tidak ada `accurate.import.bulk`. Banner peringatan bila user tidak punya sumber import manapun.
- UI Pengaturan → Role & Hak Akses otomatis menampilkan kelompok "Import PO" karena `/api/admin/permissions/catalog` grup dari `PERMISSION_CATALOG`.
- Verified 21 Feb 2026 (curl PASS):
  - Screener (no import perms) → 403 di semua 4 endpoint import.
  - Screener + override `{po.import.view:true, accurate.import:true}` → 200 di list & preview single; 403 di bulk & pdf.
  - Admin (super) → 200 di semua endpoint.
  - Settings UI menampilkan "Import PO" module dengan 5 permission berlabel Bahasa Indonesia.
  - Admin role: 38/38 hak akses (jumlah bertambah dari 33 setelah penambahan 5 perm).

## Regression coverage
- iteration_14 (Phase A) 25/25 PASS
- iteration_15 (Req 5 backend) 13/13 PASS
- iteration_16 (Delete PO UI) 6/6 PASS
- iteration_17 (B1 Role & Super Admin) — 2 findings fixed (custom role assignable, perm-first cancel/delete)
- iteration_18 (B2+B3+B1 regression) — 1 HIGH fixed (role_permissions top-up migration)
- iteration_19 (retest) 13/13 PASS

## Regression coverage
- iteration_13 (PDF header extraction).
- iteration_14 (Phase A): 25/25 backend tests PASS.
- iteration_15 (Req 5 Cancel/Delete/Bulk + Phase A regression): 13/13 PASS.

## Minor notes (from iteration_15 review, non-blocking)
- Pydantic returns 422 (not app-level 400) if `reason` field is entirely omitted. When present-but-empty, app returns 400 with Indonesian message.
- `counts.aktif` in `/orders` list currently counts non-cancelled active POs at DB level (`current_stage<15`); the returned orders array for `status=aktif` post-filters HOLD out. Numbers may differ; tabs display list length via `data-testid=order-count`.
- `bulk_cancel` writes one aggregate audit entry (`po.bulk_cancel`); individual `po.cancel` entries per PO are only written by the single-cancel path.

## Trial credentials
`/app/memory/test_credentials.md`. Password `Eternal123!`. Deactivate (never delete) to preserve history.

## Bugfix Targeted Terpadu — Surat Jalan + Tanggal Pengerjaan Operator + Workflow 15 Tahap (21 Feb 2026, DONE)

### Root causes exact
- **Part A (Tanggal Pengerjaan Operator hilang di UI Penjadwalan)** — Backend endpoint `PATCH /orders/{id}/operator-splits/{sid}/work-date` sudah lama ada tapi `OperatorSplitPanel.jsx` tidak pernah render kolom `op.work_date` — file component sama sekali TIDAK menyebut kata "work_date". Bug frontend-only.
- **Part B (Workflow 16 tahap termasuk Driver)** — `STAGES` array 16 items (`[..., "Driver", "Terkirim"]`) + `STAGE_EDIT_ROLES` sampai index 15 di backend & frontend. Driver secara semantik hanya metadata pengantar, bukan stage produksi.
- **Part C (SJ otomatis selesai + actor Packing = actor SJ)** — `advance_stage` di old code: `if new_stage == 12 and not delivery_note_number: order["delivery_note_number"] = "SJ-..."` — auto-generate nomor SJ saat masuk stage 12, otomatis menjadikan SJ "selesai" tanpa aksi eksplisit user berwenang. Tidak ada endpoint `POST /surat-jalan` dan tidak ada `delivery_note_created_by/at` sebagai source of truth completion.

### Strategi backward-compatibility Driver stage removal
Migration IDEMPOTENT + BOUNDED (bukan destructive mass renumber) di startup event:
- `current_stage == 15` (old Terkirim) → **14** (new Terkirim)
- `current_stage == 14` + `delivery_status == "Terkirim"` (old Driver yg sudah delivered) → **14** (Terkirim)
- `current_stage == 14` + belum delivered (old Driver in-transit) → **13** (Loading — masih pre-delivery)
- `current_stage < 14` → tidak disentuh
Timeline/history stage=14 Driver lama TIDAK dihapus. Driver assignment/role/delivery_proof TIDAK dihapus. Semua log migration count. Verified: DB post-migration TIDAK ada current_stage=15.

### Mapping workflow final 15 tahap
```
0 Order Masuk       8  Operator
1 Design            9  Konfirmasi Sample
2 Approval Design   10 Cetak
3 Payment           11 Packing
4 Approval Payment  12 Surat Jalan
5 Penjadwalan       13 Loading
6 Screener          14 Terkirim
7 Persiapan Helper  (Driver dihapus sebagai stage — hanya metadata)
```

### Permission Work Date + Surat Jalan
- Work Date: `schedule.work_date.view` (baca), `schedule.work_date.edit` (edit) — existing.
- Surat Jalan: `delivery_note.manage` — existing (admin + head_production + head_operations default).

### Source of truth Surat Jalan
SJ dianggap SELESAI hanya jika **ketiga** field terisi eksplisit oleh user berwenang: `delivery_note_number` + `delivery_note_created_by` + `delivery_note_created_at`. Auto-gen delivery_note_number dihapus dari advance & mark_packed. Endpoint `POST /orders/{id}/surat-jalan` adalah satu-satunya jalur create.

### Progression final Packing → SJ → Loading → Terkirim
```
11 Packing —POST /packed→  packing_status=Sudah, advance→12, actor Helper
12 Surat Jalan —POST /surat-jalan→  delivery_note_created_by/at diisi, actor SJ, TIDAK auto-advance
12 Surat Jalan —advance→ requires surat_jalan_created → 13 Loading
13 Loading — (audit detail task berikutnya, dibiarkan existing)
14 Terkirim — via mark_delivered / advance
```

### Endpoint/file yang berubah
Backend `server.py`:
- STAGES 15 items (Driver dihapus). STAGE_EDIT_ROLES 0-14. LEGACY_STAGE_MAX const.
- Global replace: `>= 15` → `>= 14`, `< 15` → `< 14`, `$lt: 15/$gte: 15` → 14. `new_stage == 15` → 14.
- Helper baru `_surat_jalan_created`, `_prereq_reasons_for_loading`.
- `advance_stage`: side-effect `new_stage==14` (bukan 15) untuk delivery_status. Gate `cur==12` pakai `_prereq_reasons_for_loading`. Auto delivery_note_number DIHAPUS. Timeline 11→12 log "Packing selesai — menuju Surat Jalan" actor Packing (bukan SJ actor).
- `mark_packed`: auto delivery_note_number DIHAPUS.
- `mark_delivered`: current_stage=14.
- New `POST /orders/{id}/surat-jalan` require `delivery_note.manage`, gate stage>=12, mengisi delivery_note_created_by/name/at, TIDAK auto-advance.
- `_persist_order` + `get_order`: expose `surat_jalan_created`, `loading_prereq_reasons`, `loading_ready`.
- Startup migration idempotent bounded (14/15 saja).

Frontend `constants.js`: STAGES 15, STAGE_EDIT_ROLES 0-14 (14 hanya admin+head_production, driver dihapus dari stage 14 karena stage 14 = Terkirim final, driver hanya metadata di stage 13 & mark_delivered).
Frontend `api.js`: `updateOperatorWorkDate`, `updateOrderWorkDate`, `createSuratJalan`.
Frontend `OperatorSplitPanel.jsx`: kolom Tanggal Pengerjaan per split (testid `op-work-date-{email}` + `op-work-date-input-{email}` bila punya edit perm; `op-work-date-value-{email}` bila view-only).
Frontend `components/SuratJalanPanel.jsx` (new): panel SJ create/update, badge state, testids `surat-jalan-panel`, `-semantic-badge`, `-form`, `-number-input`, `-notes-input`, `create-surat-jalan-button`, `-done-note`, `-readonly-note`, `-last-update`.
Frontend `PODetail.jsx`: import + render `SuratJalanPanel` setelah PackingPanel, `canManageSuratJalan=hasPerm("delivery_note.manage")`.
Frontend `components/Timeline.jsx`: STAGE_SURAT_JALAN=12 resolver semantic (SELESAI hanya jika `suratJalanCreated && currentStage>idx`).
Frontend `ProductionSchedule.jsx`: `>= 15` → `>= 14`. Header kolom "Driver" tetap ada sebagai metadata pengantar (bukan stage produksi).

### Verified — Testing Agent iteration_27 (12/12 backend + 5/5 UI PASS)
- PATCH work_date per split independent + persist ✅
- Migration idempotent (post-migration zero cs=15) ✅
- Dashboard stage_breakdown 15 items, no "Driver" ✅
- filter aktif cs<14 / selesai cs>=14 ✅
- POST /surat-jalan operator 403, admin OK, no auto-advance ✅
- advance 11→12 tidak auto-gen delivery_note_number ✅
- advance 12→13 gated by surat_jalan_created ✅
- get_order expose surat_jalan_created/loading_ready ✅
- UI work_date column: admin edit, screener hidden, operator view-only ✅
- UI SJ panel: admin form+create+persist, operator readonly ✅

## Bugfix Targeted — Tahap 12 Packing + Status Akhir Konsisten (21 Feb 2026, DONE)

### Root cause exact
- **Bug 1** (backend `advance_stage` line ~897 sebelum fix): baris `if new_stage == 11: order["packing_status"] = "Sudah"` men-set `packing_status="Sudah"` otomatis saat PO memasuki stage 11 — bukan saat Packing benar-benar selesai. Akibat: PO yang di-rollback dari 11 ke 10 masih menyisakan `packing_status="Sudah"` stale, menghasilkan tampilan **"Packing Sudah tetapi Status Akhir Cetak"** di Jadwal Produksi.
- **Bug 2** (backend `advance_stage` sebelum fix): `add_timeline(order, new_stage, user, ...)` selalu tag entry dengan `new_stage`. Ketika Operator advance dari Cetak (10) → Packing (11), timeline mencatat entry `stage=11 by_role=operator` seolah-olah **Operator mengerjakan Packing**.

### Rule final Cetak → Packing → Surat Jalan
```
10 Cetak (Operator input qty_completed>0)
  ↓ [advance] — timeline stage=10 note "Cetak selesai — menuju Packing", actor=Operator
11 Packing (Helper input Qty Packing via /packing-info)
  ↓ [save qty_packed]   — timeline stage=11 note "Qty Packing: N pcs", actor=Helper (save-only, TIDAK auto-advance)
11 Packing (Helper klik "Selesai Packing")
  ↓ [mark_packed / advance] — sets packing_status="Sudah", timeline stage=11 note "Packing: N pcs", actor=Helper
12 Surat Jalan (Status Akhir="Surat Jalan", Packing="Sudah")
```

### Field / Endpoint / Permission
- **Field baru additive** `order.packing_info` — nullable: `{qty_packed:int, notes:str, updated_by, updated_by_name, updated_at}`. Nilai `null` = belum diinput.
- **Derived response**: `packing_status_semantic` ∈ {"Belum", "Berjalan", "Sudah"} berbasis `current_stage` semata (semantic override untuk PO existing dengan stale persisted value).
- **Endpoint baru** `POST /orders/{id}/packing-info` — perm `packing.update`, save-only, cap `qty_packed <= total_quantity`, reject `qty_packed <= 0`.
- **Endpoint refactor** `POST /orders/{id}/packed` (mark_packed) — dari `require("admin","head_production","helper")` role → `_require_perm("packing.update")`. Wajib `qty_packed>0`. Sets `packing_status="Sudah"` AND advances `current_stage=12` (Surat Jalan). Timeline entry stage=11 dengan actor sebenarnya.
- **Advance gate** cur=11 → butuh `packing_info.qty_packed > 0`. Reason: "Qty Packing harus diisi terlebih dahulu."

### Endpoint/file yang berubah
- `backend/server.py`: helper `_packing_qty_input`, `_packing_status_semantic`, `_prereq_reasons_for_surat_jalan`. `advance_stage`: gate cur=11 + timeline stage=cur untuk 10→11 + auto packing_status="Sudah" pindah ke 11→12 (bukan 10→11). New `save_packing_info`. `mark_packed` perm-based + wajib qty + advance ke 12 + actor-benar. `_persist_order` strip semantic. `get_order` expose semantic. `production_dashboard` schedule row `packing_status = _packing_status_semantic(o)`.
- `frontend/src/components/PackingPanel.jsx` (new).
- `frontend/src/pages/PODetail.jsx`: render PackingPanel, mark-packed-button visible hanya di cur=11 + disable saat qty_packed<=0, `packed()` handler pre-check qty.
- `frontend/src/lib/api.js`: `savePackingInfo`.
- `frontend/src/components/Timeline.jsx`: STAGE_PACKING resolver semantic.
- `frontend/src/pages/ProductionSchedule.jsx`: kolom Packing 3-state Sudah/Berjalan/Belum via `prod-packing-{po_number}` testid.

### Verified — Testing Agent iteration_26 (13/14 backend + 5/5 UI PASS) + validation gap fix
Testing agent menemukan 1 minor: `qty_packed=0` diterima (validation `<0` bukan `<=0`) — **fixed di server.py line 1699 → `<= 0`**. Verified via curl: `POST /packing-info {qty_packed:0}` → HTTP 400 "Qty Packing tidak valid."
- Bug 1 semantic override: schedule row `packing_status='Belum'` untuk PO stage=10 stale ✅
- Bug 2 actor: Operator advance 10→11 → timeline stage=10 note "Cetak selesai" ✅
- Auto-set packing_status='Sudah' dihapus dari 10→11 ✅
- /packing-info perm gate (operator 403, admin 200) ✅
- Save qty tidak mengubah stage ✅
- Advance 11→12 wajib qty_packed ✅
- /packed perm-based + wajib qty + advance ke 12 + timeline actor Helper/Admin (bukan Operator) ✅
- Existing PO stage=12+ backward-safe ✅
- UI PackingPanel semua state (empty/save/complete/readonly) ✅
- UI Jadwal Produksi 3-state ✅

## Bugfix Targeted Terpadu — Workflow 06-12 Tidak Boleh Lompat Tahap (21 Feb 2026, DONE)
Fokus: progression, prerequisite backend, timeline semantik, upload/approval sampel cetak, gate Cetak→Packing pada stage indices 5..11 (Penjadwalan → Screener → Persiapan Helper → Operator → Konfirmasi Sampel → Cetak → Packing).

### Root cause exact
- **Bug A (Assign operator melewati Screener/Helper)** — `server.py` `assign_operators` line ~1310 punya baris `if order["current_stage"] < 8 and new_splits: order["current_stage"] = 8` yang otomatis mem-fast-forward stage ke Operator saat assignment disimpan. **Backend bug.**
- **Bug B (Tombol Packing shortcut)** — `server.py` `mark_packed` line ~1436 langsung set `current_stage = 11` + `packing_status = "Sudah"` **tanpa validator prerequisite apa pun**. Endpoint terpisah dari `advance` sehingga tidak share gate. **Backend bug.**
- **Bug C (Sampel Cetak tidak ada approval)** — Tidak ada field `sample_approved`, tidak ada endpoint `/approve-sample`, tidak ada permission `operator.sample_approve`. Gate lama di `advance_stage` line ~762 cuma cek `sample_files` exists tapi di **stage yang salah** (cur==10 = Cetak→Packing) sehingga sample bisa lewat Konfirmasi Sampel tanpa review. **Backend gap.**
- **Bug D (Blank page Cetak→Packing)** — Ketika backend return 400 dengan `detail: {message, prereq_reasons: [...]}` (object), frontend `run` handler `PODetail.jsx` melakukan `setErr(e.response?.data?.detail || "Aksi gagal")` → `err` menjadi object → React render `<div>{err}</div>` throw "Objects are not valid as React child" → seluruh PODetail unmount → **blank page**. **Frontend bug.**

### Rule final progression (semua endpoint pakai validator SAMA):
```
5 Penjadwalan → 6 Screener      : min 1 operator_split assigned
6 Screener    → 7 Persiapan Helper : screener_info.screen_qty != null
7 Helper      → 8 Operator      : helper_prep.qty_prepared != null
8 Operator    → 9 Konfirmasi Sampel : min 1 sample_files uploaded
9 Konfirmasi Sampel → 10 Cetak  : sample_files ada AND sample_approved==true
10 Cetak      → 11 Packing      : min 1 operator_splits[].qty_completed > 0
```

### Field / Permission baru (additive & backward-safe)
- **Permission** `operator.sample_approve` (module Operator, label "Approve sample cetak"). Ditambah ke default admin + head_production via `PHASE_B_ROLE_TOP_UPS` (idempotent, tidak menghapus override existing).
- **Fields order (nullable)**: `sample_approved` (bool), `sample_approved_by` (email), `sample_approved_by_name`, `sample_approved_at` (iso), `sample_approval_history` (list of {action, by_email, by_name, at, note}).
- **Derived fields di response**: `packing_prereq_reasons` (list str), `packing_ready` (bool). Tidak dipersist ke MongoDB — dihitung tiap response via helper.

### Endpoint / file yang berubah
Backend `/app/backend/server.py`:
- Central helper: `_stage_penjadwalan_done`, `_stage_screener_done`, `_stage_helper_done`, `_sample_uploaded`, `_sample_ok`, `_print_qty_input`, `_prereq_reasons_for_advance(order, from_stage)`, `_prereq_reasons_for_packing(order)`.
- `advance_stage`: integrasi validator untuk cur ∈ [5..10]. HTTP 400 dengan `{message, prereq_reasons, from_stage, to_stage}`.
- `mark_packed`: pakai `_prereq_reasons_for_packing` — semua jalur masuk Packing sama gate.
- `assign_operators`: HAPUS auto-jump `current_stage = 8`. Assignment = planning data, bukan advance.
- `attachment` (kind=sample): jika `sample_approved` sebelumnya true, reset ke false + append history entry action='reset-on-new-upload'. History TIDAK dihapus.
- **New endpoint** `POST /orders/{id}/approve-sample` — butuh permission `operator.sample_approve`, prereq: min 1 sample_files exists. Approve/revoke append to `sample_approval_history` + audit log.
- `_persist_order`: strip derived fields sebelum DB write, attach ulang untuk response.
- `get_order`: expose derived fields.

Backend `/app/backend/permissions.py`: `operator.sample_approve` di catalog + admin default.

Frontend `/app/frontend/src/lib/api.js`: method `approveSample(id, approved, note)`.

Frontend `/app/frontend/src/components/Timeline.jsx`: semantic resolver stages 5..10 pakai predikat data nyata (bukan `idx < currentStage`). Labels Bahasa: "Menunggu jadwal / input / upload sampel / approval sampel / qty cetak".

Frontend `/app/frontend/src/pages/PODetail.jsx`:
- `run` handler format `detail.prereq_reasons` multi-line.
- `useEffect` load handler guard object-detail (fix ROOT CAUSE blank page).
- `err` render pakai `whiteSpace: pre-line`.
- `mark-packed-button` disabled ketika `!order.packing_ready` + tooltip dengan reasons + `data-packing-ready` attribute.
- `packed()` handler: pre-check prereq → confirm dialog → mutate.
- `sampleSlot` di AttachmentsPanel: badge status ("Menunggu upload sampel" / "Menunggu approval sampel" / "Sampel disetujui — {name}"), tombol Approve Sampel Cetak (butuh `operator.sample_approve`), tombol Batalkan approval sampel, readonly note.
- `canApproveSample = hasPerm("operator.sample_approve")`, `canUploadSample = hasPerm("operator.sample_upload")`.

### Verified — Testing Agent iteration_25 (25/25 backend + 4/4 UI PASS)
- Assign no-jump (Bug A) ✅
- Advance 5→6, 6→7, 7→8, 8→9, 9→10, 10→11 semua GATED dengan pesan Bahasa ✅
- mark_packed shortcut BLOCKED di stage 5,6,7,8,9,10 + MongoDB `current_stage` tidak berubah ✅
- Sample approve permission gate: operator 403, admin 200 ✅
- Re-upload sample RESET approval + preserve history ✅
- Blank page Cetak→Packing FIXED — advance-error multi-line, PODetail tetap render ✅
- Packing button state (disabled + tooltip + data-packing-ready) ✅
- Sample UI (admin sees button, operator sees readonly note) ✅
- Timeline semantic (Cetak tidak "Selesai" kalau qty_completed=0) ✅
- Existing PO backward-safe — packing_prereq_reasons/packing_ready fields muncul tanpa break data lama ✅

### Bug lokasi
| Bug | Layer | File |
|---|---|---|
| A (assign auto-jump) | Backend | server.py `assign_operators` |
| B (packing shortcut) | Backend | server.py `mark_packed` + missing central validator |
| C (sample approval) | Backend + Frontend | server.py (new endpoint + perm) + PODetail.jsx (UI) + api.js |
| D (blank page) | Frontend | PODetail.jsx (setErr(object) crash) |

### Existing data safety
- Tidak ada MongoDB reset/migration destructive.
- Field baru semua nullable/optional dengan default aman.
- PO lama current_stage=11 tanpa sample_approved → timeline sekarang menampilkan status data sebenarnya ("Menunggu approval sampel") di Konfirmasi Sampel tapi tidak auto-rollback current_stage.
- File `test_iteration25_workflow_06_12.py` di `/app/backend/tests/` untuk regression.

## Audit RBAC Frontend — Konversi role-hardcode → Effective Permission (21 Feb 2026, DONE)
Full sweep frontend authorization/gating dari role name → `hasPerm(perm_key)`. Backend enforcement tidak diubah sesuai instruksi. Frontend visibility sekarang seragam mengikuti Effective Permission = Role Default + User Override.

### Mapping yang diubah
| File | Sebelum (hardcoded role) | Sesudah (Effective Permission) |
|---|---|---|
| `HoPDashboard.jsx:128` | `user?.role === "admin"` (Import PO banner link) | `hasPerm("po.import.view")` |
| `PODetail.jsx:127` | `canScheduleEdit = ["admin","head_production"].includes(role)` | Dipecah: `canManageUrgent = hasPerm("urgent.manage")`, `canEditSchedule = hasPerm("schedule.edit")` |
| `PODetail.jsx:320` | `["admin","head_production","helper"].includes(role)` (mark-packed) | `hasPerm("packing.update")` |
| `PODetail.jsx:325` | `["admin","head_production","driver"].includes(role)` (mark-delivered) | `hasPerm("delivery.update")` |
| `SuratJalan.jsx:47` | `["admin","head_production","driver","helper"].includes(user.role)` (canEditChecklist) | `hasPerm("loading.update")` |
| `SuratJalan.jsx:48` | `["admin","head_production","driver"].includes(user.role)` (canMarkDelivered) | `hasPerm("delivery.update")` |
| `DeliveryProofPanel.jsx:26-27` | `["driver","admin","head_production"].includes(role)` (canUpload/canDelete) | `hasPerm("delivery.update")` |
| `OperatorSplitPanel.jsx:118` | `role admin/head_production OR (operator && isSelf)` | `hasPerm("operator.update") && (isSelf \|\| hasPerm("schedule.edit"))` — mempertahankan business rule isSelf |
| `App.js:54` | `<RoleGate allowed=["admin","head_production"]>` untuk `/analytics/operators` | `<PermGate anyOf=["dashboard.production.view"]>` |
| `App.js:31-35` | `RoleGate` component | Dihapus (tidak dipakai lagi setelah semua route pindah ke PermGate) |

### Yang tidak diubah (bukan authorization/security):
| File | Line | Alasan tetap dipertahankan |
|---|---|---|
| `PODetail.jsx:54` | `isCrm = role === "crm"` | Display flag untuk read-only hint saja (bukan gating). Kondisi lengkap: `isCrm && !hasAnyActionPerm`. |
| `SuratJalan.jsx:35` | `user?.role === "driver"` untuk auto-fill driver name | UX pre-fill, bukan authorization. |
| `constants.js STAGE_EDIT_ROLES` + `canAdvance()` | matriks role → stage yang mirror backend `STAGE_EDIT_ROLES` | Workflow rule identik backend (backend hardcode role). |
| `Settings.jsx` `role === slug`, `r.role === "admin"` (badge terkunci) | Filter/badge display (bukan gating) | Role admin selalu terkunci sebagai super-role, hanya untuk label. |

### Verified 21 Feb 2026 (smoke test langsung)
- CRM (role=crm, override role: design.view/design.approve/payment.view/payment.upload/payment.approve; TIDAK ada urgent.manage/packing.update/delivery.update):
  - ✅ approve-design-button visible
  - ✅ upload-design-button hidden
  - ✅ toggle-urgent-button hidden
  - ✅ mark-packed-button hidden
  - ✅ mark-delivered-button hidden
  - ✅ crm-readonly-note hidden (karena punya design/payment action perms)
- Admin HoPDashboard: import-link-banner visible ✅.
- Backend endpoint level enforcement TIDAK DIUBAH — backend tetap menjadi source of truth via role/permission decorator masing-masing.

### Catatan Trade-off
Beberapa endpoint backend masih menggunakan `require("role1","role2")` (mis. `mark_packed`, `mark_delivered`, `update_loading`, `set_urgent`, `analytics_operators`). Frontend sekarang membuka visibility button berdasarkan permission — jika seseorang mendapatkan permission via User Override tetapi role-nya tidak termasuk whitelist backend, tombol akan tampil tapi API akan 403. Ini disepakati (permission-first UI, backend tetap authoritative). Backlog: sinkronkan backend menggunakan `_require_perm` sepenuhnya di iterasi mendatang.

### Files changed
- `frontend/src/App.js` (hapus RoleGate, konversi route analytics)
- `frontend/src/pages/HoPDashboard.jsx` (banner link perm-based)
- `frontend/src/pages/PODetail.jsx` (semua button gates → perm-based)
- `frontend/src/pages/SuratJalan.jsx` (checklist + delivered gates)
- `frontend/src/components/DeliveryProofPanel.jsx` (upload/delete gates)
- `frontend/src/components/OperatorSplitPanel.jsx` (canEditQty perm+isSelf)

## Bugfix Targeted — CRM design.approve tombol tidak muncul (21 Feb 2026, DONE)
- **Root cause exact** (frontend saja, backend sudah benar):
  - `/app/frontend/src/pages/PODetail.jsx` line 123: `canApproveDesign = ["designer","admin","head_production"].includes(role)` — HARDCODED role whitelist, mengabaikan Effective Permission `design.approve`. Bandingkan dengan `canApprovePayment = hasPerm("payment.approve")` yang sudah benar.
  - Baris 324 menampilkan **"Mode CRM read-only"** setiap kali `role === "crm"` tanpa memperhatikan Effective Permission.
  - `AttachmentsPanel.jsx` line 13-14: `canUploadDesign` / `canUploadSample` juga hardcoded role check, mengabaikan `design.upload` / `operator.sample_upload`.
  - Backend `/api/orders/{id}/approve-design` **sudah** enforce via `_require_perm(user, "design.approve")` — permission-based, correct.
- **Fix** (Effective Permission = Role Default + User Override, bukan role name):
  - `PODetail.jsx`: `canApproveDesign = hasPerm("design.approve")` + `canUploadDesign = hasPerm("design.upload")` + `hasAnyActionPerm` untuk menentukan tampilnya label "Mode CRM read-only". Label kini hanya muncul bila `isCrm && !hasAnyActionPerm`. Testid: `data-testid=crm-readonly-note`.
  - `AttachmentsPanel.jsx`: `canUploadDesign = perms.includes("design.upload")`, `canUploadSample = perms.includes("operator.sample_upload")`.
- **Rule confirmed**: `design.view` / `design.upload` / `design.approve` / `design.bypass_approval` sepenuhnya independen. CRM dengan `design.approve=true` bisa Approve tanpa punya `design.upload`.
- **Verified 21 Feb 2026 (backend curl + frontend smoke UI)**:
  1. CRM (design.approve=true, design.upload=false, file design tersedia) → tombol Approve Design **muncul** (visible di screenshot), tombol Upload Design **tidak muncul**, "Mode CRM read-only" **tidak muncul**.
  2. Backend `POST /orders/{id}/approve-design` sebagai CRM → **HTTP 200**, design_status='Approved', design_approved_by='crm@eternalpackaging.com'.
  3. Backend `POST /orders/{id}/attachment` (kind=design) sebagai CRM → **HTTP 403** "Anda tidak punya permission 'design.upload'".
  4. User override `design.approve=Deny` → `/auth/me` effective `design.approve=false`, approve-design endpoint **HTTP 403**.
  5. Reset override → effective kembali mengikuti role default.
  6. Payment upload/approve pola sudah `hasPerm("payment.*")` — tidak terpengaruh.
  7. Regression: role designer/admin/head_production tetap punya design.approve di default → tombol tetap muncul (tidak ada regresi).
- **Files changed**: `frontend/src/pages/PODetail.jsx`, `frontend/src/components/AttachmentsPanel.jsx`. Backend tidak diubah — sudah benar.

## Bugfix Terpadu — Semantic Queues + HoP Dashboard Clickable Cards + Permission Gate (21 Feb 2026, DONE)
- **Backend `/api/dashboard/summary` refactor** (`_apply_hold_snapshot` per-order, exclude cancelled globally):
  - Response schema baru: `kpi.hold` + list `active[]`, `urgent[]`, `hold[]`, `completed[]` (selain existing `due_today`, `overdue`, `waiting_design_approval`, `waiting_payment_approval`).
  - Predikat masing-masing list INDEPENDEN dari `current_stage` — satu PO boleh masuk 2-3 list sekaligus (design + payment + hold).
  - Cancelled PO dikecualikan dari SEMUA count, list, dan stage_breakdown.
  - `slim()` sekarang emit: `design_bypassed`, `payment_bypassed`, `hold`, `work_date`.
- **Frontend `HoPDashboard.jsx` rewritten**:
  - `KpiCard` menjadi `<button>` clickable + focus-visible outline.
  - 8 card clickable: `kpi-active`, `kpi-urgent`, `kpi-today`, `kpi-overdue`, `kpi-waiting-design`, `kpi-waiting-payment`, `kpi-hold`, `kpi-completed`.
  - Panel filter dinamis (`hop-filter-panel`) muncul TEPAT di bawah KPI grid dengan judul "Menampilkan: <label>" + count + tombol "Hapus Filter" (`hop-filter-clear`).
  - Klik card yang sama lagi = toggle (panel hilang). Empty state: `hop-filter-empty`.
  - Row klikable ke `/po/{id}` (existing PODetail). Row testid: `hop-row-<filter>-<po_number>`.
  - Statis dual-column panel (overdue/today/design/payment) DIHAPUS untuk hindari duplikasi — satu source truth via filter panel.
- **App.css**: `.kpi-card-clickable`, `.kpi-card-active` (inset outline + subtle lift on hover).
- **Guarantee `count === list rows`**: KPI vector diambil langsung dari `len(list)` di backend; UI hanya render list dari response yang sama → guaranteed konsisten.
- **No mutation on click**: pure client-side state; tidak ada API call selain GET `/api/dashboard/summary` yang read-only.
- **Testing agent iteration_24 — 16/16 PASS (8 backend + 8 frontend)**:
  - Permission gate: admin OK, screener 403 di API + nav hidden.
  - 8 card × count == list rows: `(active=1, urgent=0, today=0, overdue=0, design=1, payment=1, hold=1, completed=0)` all matched.
  - Toggle behavior, Hapus Filter, row navigation ke PODetail.
  - Semantic PO stage=7 dengan design_status=Waiting tetap masuk `waiting_design_approval`.
  - Satu PO dengan design+payment pending muncul di kedua list.
  - Cancelled PO tereksklusi 100%.
  - HOLD PO tetap muncul di list waiting_design + hold sekaligus.
  - 5× GET summary → 0 mutation pada current_stage/timeline/updated_at.
- **Files changed**: `backend/server.py` (dashboard_summary refactor + slim), `frontend/src/pages/HoPDashboard.jsx` (rewrite), `frontend/src/App.css` (+5 lines styling).
- **Test file baru**: `/app/backend/tests/test_iteration24_hop_semantic_queues.py`.


- **Root cause exact**: `Timeline.jsx` menentukan state 'Selesai' untuk SEMUA stage semata-mata dari `idx < currentStage`. Ketika `approve_payment` memajukan `current_stage` ke 4, stage 2 (Approval Design) otomatis marked 'Selesai' meskipun `design_status` masih Waiting. Bug UI Timeline murni; backend endpoint `approve_design`/`approve_payment` sudah independen (approve_payment tidak menyentuh design_* field, dan sebaliknya).
- **Fix**:
  1. `Timeline.jsx`: `resolveStageState()` resolver — stage 2 & 4 dievaluasi dari flag masing-masing (`design_status==='Approved'` → Selesai; `design_bypassed===true` → BYPASS; jika current_stage sudah lewat tapi approval belum → 'Menunggu approval' dengan state 'current'). Stage lain tetap pakai progression.
  2. `PODetail.jsx`: pass `order` prop ke Timeline.
  3. `AttachmentsPanel.jsx`: terima `designSlot`/`sampleSlot` prop untuk inline actions.
  4. `PODetail.jsx`: `designSlot` berisi `DesignStatusBadge` + tombol inline `approve-design-inline-button` / `revoke-design-inline-button` / `bypass-design-inline-button` di panel Design (mirror pattern Payment yang sudah existing).
  5. `PODetail.jsx`: null-guard `order.item_count`, `order.total_quantity`, `order.line_items`, `li.quantity` (latent crash risk yang ditemukan testing agent).
- **Backend independensi**: verified — `approve_design` (line 720+) hanya mutate `design_*`; `approve_payment` (line 760+) hanya mutate `payment_*`. Re-upload attachment (line 1268+): reset own approval + entri history `reset-on-new-upload`, tidak menyentuh field yang lain. HOLD gate (`_apply_hold_snapshot`) tetap enforce dua-kondisi.
- **Testing agent iteration_23 — 13/13 PASS**:
  - Backend (7/7 pytest): approve_payment tidak ubah design; approve_design tidak ubah payment; HOLD gate ganda; re-upload independen (kedua arah); no design_files → 400; bypass ≠ approve.
  - Frontend (6/6 live UI): tombol Approve Design berada di panel Attachments (design-actions slot), tombol Approve Payment di panel Payment (existing); Timeline chip 'Menunggu approval' bukan 'Selesai' setelah approve_payment saja; refresh persist.
- **Files changed**: `Timeline.jsx`, `PODetail.jsx`, `AttachmentsPanel.jsx`. Tidak ada perubahan backend selain test file baru di `/app/backend/tests/test_iteration23_approval_independence.py`.

## Bugfix — Stage shortcut badge count vs list mismatch (21 Feb 2026, DONE)
- **Root cause exact**: `list_orders` default `status="aktif"` memfilter keluar orders yang HOLD (`o.hold`) dan stage>=15. Sedangkan `stage_counts` (dari MongoDB aggregation `{cancelled:{$ne:True}}` grouped by `current_stage`) menghitung SEMUA non-cancelled per stage. Kedua predicate berbeda → badge count (canonical) tidak sama dengan list (aktif-only). Contoh: PO baru di stage 0 selalu HOLD (belum ada design/payment) → count=1 tapi aktif list=0.
- **Fix (canonical resolver)**: Backend `list_orders` menambah status baru `stage_view` yang return semua non-cancelled TANPA HOLD/aktif filter. Frontend `Dashboard.jsx` merge `useEffect` — bila `?stage=N` aktif → `load("stage_view")`; bila tidak → `load(statusTab)`. Predicate count sekarang === predicate list.
- **HOLD indicator**: PO HOLD tetap ditampilkan di list stage terkait dengan hold-badge di POCard (tidak disembunyikan tanpa penjelasan).
- **Testing agent iteration_22 — 16/16 PASS**:
  - Seed 16 test PO (1 per stage 0-15) + 1 cancelled. Untuk setiap stage: badge count === chip count === list entries. Match=true untuk 16/16.
  - Cancelled PO excluded dari semua stage.
  - Hapus filter, back nav preserve ?stage=, no mutation on click, regression tabs OK.
  - Cleanup semua test PO setelahnya.
- **Files changed**: `/app/backend/server.py` (list_orders + stage_view branch), `/app/frontend/src/pages/Dashboard.jsx` (unified useEffect untuk stage/tab load).

## Bugfix — Role custom tidak muncul di Karyawan dropdown (21 Feb 2026, DONE)
- **Root cause**: Dropdown Role di `EmployeeForm` (Settings.jsx) di-render dari array hardcoded `ROLE_KEYS = Object.keys(ROLE_LABELS)` — hanya 9 slug built-in. Role custom (mis. `direktur_utama`) tersimpan benar di backend tapi tidak pernah muncul di UI Tambah/Edit Karyawan.
- **Fix**: `EmployeesTab.load()` sekarang fetch `api.adminListRoles()` paralel dengan `adminListUsers()`; state `roles` di-pass ke `EmployeeForm` sebagai prop. Dropdown Add/Edit render dari backend response (built-in + custom). Filter role toolbar + role badge di tabel juga pakai source of truth ini (`roleLabelOf()`).
- **Aturan yang di-enforce**:
  - Role status `active` → tampil di dropdown Create/Edit.
  - Role status `inactive` → di-hide dari Create dropdown; DIPRESERVE di Edit dropdown bila user existing masih memakai role tersebut (dengan suffix "(nonaktif)") → historis tetap terbaca.
  - Filter role toolbar menampilkan SEMUA role (termasuk yang inactive) dengan suffix "(nonaktif)".
  - Backend `/api/admin/users` (POST/PUT) sudah validasi slug via `_role_slug_is_valid` — accept custom slugs otomatis.
- **Verified 21 Feb 2026 (testing_agent_v3_fork iteration_21) — 8/8 PASS**:
  - T1 Add dropdown contains custom + all builtin
  - T2 Edit modal shows custom role
  - T3 Create user dengan role custom → tersimpan slug benar, badge label human
  - T4 Edit user baru → dropdown terpilih otomatis
  - T5 Filter toolbar menampilkan custom + inactive dengan suffix
  - T6 Login user baru → `/api/auth/me` return 25 permissions role default
  - T7 Inactive role hidden di Create, visible di Edit untuk user existing
  - T8 Regression: 9 built-in slug tetap ada

## Bugfix — Role custom tidak muncul (21 Feb 2026) — CLOSED

## Requirements 13-16 (Feb 2026, DONE)

### Req 15 — Effective Permission Model
- Effective = Role Default + User Override(Allow) − User Override(Deny). Super Admin bypass tetap.
- Model existing sudah support Inherit/Allow/Deny; enhancement: **surface breakdown ke UI**.
- New backend endpoints:
  - `GET /admin/users/{id}/permissions/effective` → `{default_from_role, override, effective}`.
  - `POST /admin/users/{id}/permissions/reset-override` → clear override, audit `user.permissions.reset_override`.
- UI Pengaturan → Karyawan → Hak Akses menampilkan per permission 3 pill: **Default** (dari role) · **Override** (Ikut role/Allow/Deny) · **Efektif** (Diizinkan/Tidak Diizinkan). Tombol **"Reset Semua Override"** di header.
- Role change tidak menghapus override (existing PUT behavior) — override tetap dipertahankan; admin bisa reset manual jika diperlukan.
- `/auth/me` return effective permissions (existing, via `user_permissions()`).

### Req 13 — Tanggal Pengerjaan (work_date)
- Field baru: `orders.work_date` (global, YYYY-MM-DD) + `operator_splits[].work_date` (per operator).
- Permissions baru: `schedule.work_date.view`, `schedule.work_date.edit`.
- Endpoints:
  - `PATCH /orders/{id}/work-date` (require `schedule.work_date.edit`).
  - `PATCH /orders/{id}/operator-splits/{split_id}/work-date` (require `schedule.work_date.edit`).
  - Keduanya audited: `schedule.work_date.update` (before/after work_date + timeline entry).
- **Tidak mengubah** `deadline`/`estimated_delivery`/qty.
- `/api/dashboard/production` response `_production_row` diperluas dengan `work_date` + `operator_work_dates[]`.
- Frontend:
  - `ProductionSchedule.jsx`: kolom "Pengerjaan" (mono date), fallback per-operator display, filter `Semua/Sudah/Belum Dijadwalkan`, range filter, sort `Deadline/Pengerjaan asc/desc`.
  - `HoPDashboard.jsx`: setiap PO card menampilkan "Pengerjaan: YYYY-MM-DD" atau "Belum dijadwalkan" (data-testid `hop-po-workdate-<id>`).
- Verified backend: PATCH global 200, screener tanpa perm 403.

### Req 14 — Sample Print Gate before Packing
- `POST /orders/{id}/advance` menolak (400 `{"message":"Upload sampel cetak terlebih dahulu...", "requires":"sample_upload"}`) bila `current_stage == 10 (Cetak)` dan `sample_files` kosong.
- Upload sample: `POST /orders/{id}/attachment` dengan `kind=sample` sekarang memerlukan permission `operator.sample_upload` (sebelumnya hardcode role check).
- Sample history preserved — upload baru menambah entri baru ke `sample_files[]`, tidak menghapus lama.
- Verified backend: no-sample advance → 400 dengan pesan tepat; with sample → 200 stage naik 10→11.

### Req 16 — Dashboard Produksi Permissions
- Permissions baru: `dashboard.production.input`, `dashboard.production.edit` (ditampilkan di Settings → Role & Hak Akses grup "Dashboard Produksi").
- `dashboard.production.view` (existing) sekarang menggate BOTH `/dashboard` (HoP Dashboard control tower) dan `/produksi` (Jadwal Produksi).
- App.js: `/dashboard` route berganti dari `RoleGate` ke `PermGate anyOf=["dashboard.production.view"]`.
- Frontend HoP Dashboard existing tidak di-redesign (sesuai instruksi). Card klik + Detail PO clickable sudah existing.
- Permission bisnis untuk input/edit tetap wajib per modul (schedule.work_date.edit, screen.update, dll). Dashboard perm bukan super-access.

### Migration
- `PHASE_B_ROLE_TOP_UPS` idempotent untuk `admin` + `head_production` + `head_operations`: top-up perm baru `schedule.work_date.view/edit` + `dashboard.production.input/edit`.
- Role default (`ROLE_DEFAULT_PERMISSIONS`) untuk role viewer (designer/screener/helper/operator/driver/crm) ditambah `schedule.work_date.view`.
- Admin: 42/42 hak akses setelah migrasi (naik dari 38).

### Files changed
- `backend/permissions.py`: 4 permission baru (Import PO 5 sudah ada), role defaults extended.
- `backend/server.py`: sample gate, `_require_perm("operator.sample_upload")`, endpoint effective/reset, endpoint work-date (global + per-split), `_production_row` extended, migration extended.
- `frontend/src/App.js`: `/dashboard` PermGate.
- `frontend/src/components/AppShell.jsx`: Import PO nav via hasPerm (existing).
- `frontend/src/pages/Settings.jsx`: triad pills + reset override button + effective breakdown fetch.
- `frontend/src/pages/HoPDashboard.jsx`: work_date display per PO card.
- `frontend/src/pages/ProductionSchedule.jsx`: work_date column + filter/sort UI + sorted rendering.
- `frontend/src/lib/api.js`: 4 method baru.
- `frontend/src/App.css`: `.pill-muted` + `.pill.xs`.

### Test summary (21 Feb 2026)
- **Req 13 — PASS** (curl + smoke)
- **Req 14 — PASS** (curl gate verification)
- **Req 15 — PASS** (effective breakdown + reset override)
- **Req 16 — PASS** (403 for missing perm, dashboard shows for admin)
- **Regression PASS**: Accurate list (5 rows, 120 total), PO list, Import PO nav, existing permissions preserved.

## Mobile Workflow Stages Clickable (Feb 2026, DONE)
- Setiap dari 16 tahap di section "Cakupan alur kerja" (Dashboard mobile, `/`) sekarang berupa `<Link>` full-row (min-height 48px untuk tap area nyaman) dengan hover/pressed state + chevron.
- Route: `/?stage=N` — Dashboard membaca query param `stage` via `useSearchParams`, filter list ke PO dengan `current_stage === N`. Stage 15 auto-switch ke `statusTab=selesai`, lainnya ke `aktif`.
- Filter chip "Menampilkan PO — <label>" + count + tombol "Hapus filter" di atas list (`data-testid: stage-filter-chip`).
- Backend `/api/orders` response menambah `stage_counts` map (aggregation, exclude cancelled). Digunakan sebagai badge count per stage.
- RBAC: Link hanya melakukan navigasi + filter — RBAC existing di `/api/orders` tetap enforce; user tidak dapat melihat PO yang bukan haknya. Klik shortcut TIDAK menulis DB, TIDAK mengubah workflow, TIDAK advance stage.
- Back navigation: standar react-router history — user tekan Back kembali ke halaman sebelumnya (mis. Detail PO → Dashboard dengan filter tetap terisi).
- Verified 21 Feb 2026 (smoke mobile 390×900): 16 link render dengan count real dari DB (Order Masuk=4, Approval Payment=1, Persiapan Helper=1, Konfirmasi Sample=1); klik Packing → URL `/?stage=11` + chip visible + tabel filtered; klik "Hapus filter" → chip hilang + URL bersih; stage 15 (Terkirim) chip "Menampilkan PO — Terkirim".

## Known limitations
- Persisted `role_permissions` customization (mis. Screener "disesuaikan") tidak otomatis di-top-up dengan perm baru non-admin — sesuai design (customization dihormati). Admin bisa edit role manually di Settings.
- `server.py` >3100 lines — split into routers pending (tracked backlog, ditunda hingga fitur stabil).
- Rollback keeps bypass flags intact (intentional: bypass is a conscious admin override). If user needs bypass to reset on rollback, add explicit `also_clear_bypass:bool` flag.
- Helper-prep and Screener-info write timeline entries regardless of stage (kept for full audit trail).
- HOLD status hidden in `GET` once `current_stage >= 5`; advance guardrails still enforce.
- Accurate Online `sales-order/list.do` central endpoint sesekali return `{s:false, d:[...]}` dari server pusat Accurate — sudah ditangani gracefully oleh `_accurate_client.list_recent_sales_orders` (60s cache + stale fallback) dan endpoint `/api/accurate/list` (return 200 dengan `error:true` payload). Frontend `ImportPO.jsx` menampilkan pesan ramah tanpa membuat halaman blank.

## Accurate LIVE — Native API Token + HMAC Signature (Feb 2026, DONE)
- Auth mode `api_token`: `ACCURATE_API_TOKEN` + `ACCURATE_API_SIGNATURE_SECRET` + `ACCURATE_DB_ID` di `backend/.env`.
- HMAC-SHA256 timestamp signature (`X-Api-Timestamp`, `X-Api-Signature`) via `_signed_headers`.
- READ-ONLY 100% — hanya GET (`open-db.do`, `sales-order/list.do`, `sales-order/detail.do`).

### Accurate List Fix (21 Feb 2026) — ROOT CAUSE + FIX
- **Root cause**: parameter `fields=id,number,transDate` DAN `sp.sort=transDate desc` (dengan spasi) menyebabkan tenant Accurate mengembalikan `{s:false, d:["Upsss..."]}`. Endpoint `detail.do` tidak terpengaruh karena tidak menggunakan sort/fields.
- **Fix**: `list_recent_sales_orders` tidak lagi mengirim `fields`. Sort menggunakan pipe syntax `transDate|desc`. Dukung `page`, `pageSize` (1..100), `keyword` (Accurate `keywords` filter).
- Verified LIVE: `total=120`, halaman 1–6, sortasi transDate desc benar, `keyword=KOPI` mengembalikan 4 baris match.
- Diagnostic: `_last_list_error` + collection `db.accurate_errors` mencatat tenant error message secara aman (tanpa token/signature).

### Endpoints
  - `GET /api/accurate/status` — mode + message (tidak bocorkan credential).
  - `GET /api/accurate/list?limit=20&page=1&keyword=&refresh=false` — 1 halaman SO (READ-ONLY), 60s in-memory cache per (page,limit,keyword). Setiap baris di-enrich dengan `import_status` (`imported`/`none`), `existing_po_id`, `existing_po_number`, `existing_stage_label` via match ke MongoDB `orders`.
  - `POST /api/accurate/import/preview` — 1 SO (via detail.do), TIDAK menulis Mongo.
  - `POST /api/accurate/import/preview-bulk` — hingga 25 SO (parallel fetch, sem=4), TIDAK menulis Mongo. Return per-SO {ok, duplicate, preview, has_verify} + summary agregat.
  - `POST /api/accurate/import/confirm` — write 1 PO (duplicate protection).
  - `POST /api/accurate/import/confirm-bulk` — write hingga 25 PO sekuensial. Return per-SO {status: success|duplicate|failed}.

### Color detection (per line item, from Accurate free text)
- `_detect_colors(text)` regex word-boundary untuk: PUTIH, HITAM, MERAH, BIRU, HIJAU, KUNING, ORANGE, UNGU, COKLAT, PINK, GOLD, SILVER, BENING, ABU-ABU, KREM, MAROON.
- Prioritas: `item.color` field → item description/detailNotes/name → SO-level notes/memo.
- 1 warna jelas → `line.color`. Multiple candidates atau SO-notes multi-item → `line.color_candidates[]` + `line.color_needs_verify=True` (UI menampilkan "Perlu verifikasi (…kandidat…)").
- Admin masih bisa mengoreksi warna di flow PDF (existing edit).

### Frontend `/admin/import` Tab Accurate
- Auto-fetch daftar 20 SO saat tab dibuka; default page size 20, pilihan 20/50/100.
- Search bar (nomor SO / customer) dengan `keyword` submit.
- Kolom tabel: `checkbox` · Nomor SO · Tanggal · Customer · Total · Status Accurate · Status Import · Aksi.
- Baris SO yang **Sudah Diimport**: pill hijau + tombol "Lihat PO", checkbox disabled (tidak bisa dipilih untuk import).
- Pagination Previous / Next + indikator "Halaman X dari Y".
- Multi-select: bulk-bar muncul saat ≥1 dipilih, tombol "Preview N Terpilih".
- Modal Preview Bulk: ringkasan (total, siap, duplikat, gagal, total qty, jumlah item), warning "N PO memiliki item warna perlu verifikasi", tabel per SO dengan customer/qty/warna/status, konfirmasi eksplisit "Import N PO".
- Modal Result Bulk: per-SO success/duplicate/failed dengan link Lihat PO.
- Preview single SO menampilkan warna per line item + "Perlu verifikasi (kandidat)".
- Graceful error banner `recent-so-error` saat list.do gagal (fallback sekarang selalu berhasil setelah fix).
- Tidak ada auto-import, tidak ada write-back ke Accurate, duplicate protection selalu aktif.

### Verified 21 Feb 2026 (self-test PASS)
- Bulk confirm 3 SO (PO.2026.07.00012/13/11) → 3 imported ke Mongo.
- Re-confirm 2 SO sama → 2 duplicate (ditolak).
- List refresh setelah import → 3 SO menampilkan `import_status: imported`.
- Preview single ke SO imported → `duplicate: True` + link ke existing PO.
- Color extraction: PO 00012 → HITAM, PO 00013 → PUTIH, PO 00011 → candidates=[BIRU] verify=True.
- UI smoke test: seluruh data-testid tersedia (search, select-all, pagination, bulk-bar, bulk-preview-modal dengan summary + items + verify warning).


## Surat Jalan — inline action error UX (Feb 2026)
- **Root cause**: `SuratJalan.jsx` menggabungkan `err` untuk load-error awal & action-error. `if (err) return <div className="error-page">…` meng-unmount seluruh halaman saat action (Simpan Checklist / Tandai Terkirim) gagal — user kehilangan konteks.
- **Fix**: state di-split menjadi `loadErr` (hanya untuk fetch awal `getOrder`, boleh replace halaman) dan `actionErr` (untuk semua aksi mutasi, tampil sebagai inline banner di bawah `sj-actions`, tidak meng-unmount halaman). Banner dilengkapi tombol "Tutup" (`sj-action-error-dismiss`) dan otomatis di-clear saat action berikutnya sukses.
- **Files changed**: `/app/frontend/src/pages/SuratJalan.jsx`.
- **Verified 22 Feb 2026 (screenshot smoke test PASS)**:
  - Initial load sukses → dokumen `surat-jalan-doc` render normal, tanpa banner error.
  - Klik "Tandai Terkirim" saat prerequisite belum lengkap → halaman tetap ter-render lengkap, banner `sj-action-error` muncul multi-line ("Belum bisa Terkirim — prerequisite belum lengkap.\n• Surat Jalan belum dibuat.\n• Checklist Loading belum lengkap (items_loaded & seal_ok).").
  - Klik "Tutup" → banner hilang, halaman tetap utuh.
  - `loadErr` (via `data-testid="sj-load-error"`) hanya trigger pada 404/network error dari `getOrder`.
- **Regression**: tidak ada perubahan pada RBAC, workflow, API contract, stage mapping, Loading/Terkirim logic, atau backend.

## Customer Design Approval — secure link + signature + hour-based expiry (22 Feb 2026)

### Scope (only)
Link approval design yang dikirim staff manual via WhatsApp ke customer.
Customer buka link (tanpa login) → lihat PO/Design → isi nama + tanda tangan →
Approve / Reject. Hasil otomatis nyambung ke Approval Design internal (source
of truth tetap `design_status`/`design_approval_history`). **Tidak menyentuh**
KPI, refactor server, WhatsApp API, email automation, atau Customer PO Tracking.

### Schema
- Collection **`app_settings`** (single doc `{key:"customer_approval",...}`):
  `expiry_hours` (default **4 jam**), `allow_approve`, `allow_reject`,
  `signature_enabled`, `signature_required_approve` (default true),
  `signature_required_reject` (default false), `reject_reason_required`
  (default true) + `updated_by/at`.
- Collection **`customer_approvals`** (unique index `id`, unique index
  `token_hash`, index `order_id`, index `status`):
  `id, order_id, po_number, design_version, token_hash, status, generated_by,
  generated_by_name, generated_at, expires_at, expiry_hours, response,
  signer_name, signature_storage_path, customer_note, reject_reason,
  responded_at, revoked_by, revoked_by_name, revoked_at, revoke_reason`.
- Order (additive & backward-compatible): `customer_approval` snapshot
  pointer + `customer_approval_history[]` (append-only).

### Expiry
`expires_at = generated_at + expiry_hours` (timezone-aware UTC). Pilihan preset
1/2/4/6/12/24 jam + Custom (1-168 jam). Derived expired dicek server-side saat
public view / submit. Frontend hanya cosmetic countdown.

### Secure token
`secrets.token_urlsafe(32)` (43 char). Hanya **SHA-256(token)** disimpan (field
`token_hash`, unique index). Plaintext token hanya dikembalikan **sekali** ke
staff internal saat generate. Tidak pernah masuk audit log. URL publik:
`/customer/design-approval/{token}` (route di luar AppShell, tanpa auth).
Endpoint publik: `GET|POST /api/public/customer-approval/{token}[/submit]`.

### Design version binding
`design_version = order.design_files[-1].id`. Saat public view/submit, backend
membandingkan `design_files[-1].id` order sekarang dengan `design_version`
record → beda → status derived jadi `invalidated`, submit ditolak.
Endpoint `POST /orders/{id}/attachment` (kind=design) memanggil
`_ca_router._invalidate_on_design_reupload(order, user)` yang set status link
aktif jadi `invalidated` + history entry (best-effort, tidak menggagalkan
upload).

### Endpoints (semua di `/api`)
Internal (auth + permission):
- `GET /customer-approval/settings` — `customer_approval.view`
- `PUT /customer-approval/settings` — `customer_approval.settings`
- `POST /orders/{id}/customer-approval/generate` — `customer_approval.manage`.
  Kalau ada link `active`, otomatis revoke sebagai regenerate; kalau
  expired/rejected/invalidated → resend membuat link baru + design_version
  refreshed ke terbaru. Menerima optional `expiry_hours` override.
- `POST /orders/{id}/customer-approval/revoke` — `customer_approval.manage`.

Public (no auth):
- `GET /public/customer-approval/{token}` — mengembalikan safe view + derived
  status (expired/invalidated re-computed).
- `POST /public/customer-approval/{token}/submit` — atomic guard update
  `status:"active"` → `approved|rejected` (mencegah double-submit / race).
  Signature disimpan ke Emergent Object Storage via `obj_storage.put_object`
  (`customer-signature/{po}/{approval_id}.png`), record `db.files` dibuat agar
  `/api/files/{path}` bisa serve (auth-gated).

### Behavior detail
- **Approve**: validasi (allow_approve, signer, signature-if-required),
  atomic → `design_status = "Approved"`, `design_approved_by="customer:{name}"`,
  push `design_approval_history[]` (`action:"customer-approve"`), advance stage
  ke 3 kalau <3, timeline entry.
- **Reject**: validasi (allow_reject, signer, reject_reason-if-required),
  atomic → design tetap Waiting (kalau sudah Approved sebelumnya, di-reset),
  history entry `"customer-reject"` + reject_reason muncul di internal PODetail.
- **Completed link** (`approved`/`rejected`): re-open menampilkan halaman
  read-only "Approval sudah dikirim" + hasil. Form aktif tidak muncul lagi.
- **Expired/Revoked/Invalidated**: public halaman read-only, tidak ada tombol
  aksi. Backend juga tolak submit dengan HTTP 400.
- **Regenerate** (active → active baru): confirm prompt "Link lama akan
  dinonaktifkan. Buat link approval baru?", token lama di-revoke, hanya 1 link
  aktif per PO.
- **Resend** (expired/rejected/invalidated → new active): 1-klik "Buat Link
  Baru / Kirim Ulang", `expires_at` dihitung ulang dari waktu generate baru,
  token lama tetap invalid.
- **Revoke**: token tidak bisa submit, public halaman read-only "revoked".
  `revoked_by/at/reason` disimpan. `design_status` internal TIDAK di-reset.

### Permissions (baru)
`customer_approval.view`, `customer_approval.manage`, `customer_approval.settings`.
Role defaults (baseline `permissions.py` + top-up runtime idempotent di seed):
- **admin**: view + manage + settings (super admin bypass tetap).
- **head_production**: view + manage + settings.
- **designer**: view + manage.
- **crm**: view (read-only).
Semua UI gating via `hasPerm(...)` (tidak ada `role === ...`).

### Frontend
- `/app/frontend/src/pages/PublicCustomerApproval.jsx` — halaman publik
  mobile-first (`data-testid=public-approval-*`). Menampilkan hanya data safe.
- `/app/frontend/src/components/SignaturePad.jsx` — canvas custom (Pointer
  Events), berfungsi di touch/tablet/mouse, `getDataUrl()` = PNG base64,
  tombol Hapus Tanda Tangan.
- `/app/frontend/src/components/CustomerApprovalPanel.jsx` — panel internal di
  PODetail (di bawah AttachmentsPanel). Data-testid: `customer-approval-panel`,
  `ca-status-chip`, `ca-actions`, `ca-generate-button`, `ca-regenerate-button`,
  `ca-revoke-button`, `ca-resend-button`, `ca-copy-link-button`,
  `ca-copy-wa-button`, `ca-fresh-token`, `ca-signature-preview`.
- `/app/frontend/src/pages/Settings.jsx` — tab **Customer Approval**
  (`settings-tab-customer-approval` / `ca-settings-tab`), preset 1/2/4/6/12/24
  jam + Custom + 6 toggle. Save disabled kalau tidak punya
  `customer_approval.settings`.
- `/app/frontend/src/App.js` — public route `/customer/design-approval/:token`
  di luar `Protected`. `SETTINGS_PERMS` diperluas.

### Integration & data safety
- `design_approval_history` **append-only**: setiap customer approve/reject
  menambah entry (`customer-approve`/`customer-reject`) tanpa menghapus history
  internal existing.
- Re-upload design tetap reset approval seperti sebelumnya + otomatis
  invalidate customer link.
- Workflow 15 tahap TIDAK diubah. Accurate/Packing/Surat Jalan/Loading/Terkirim
  tidak disentuh.
- Semua field/schema baru additive & nullable. PO lama tetap render normal
  (`customer_approval` undefined → panel tampil "Belum Dibuat").
- Public view TIDAK mengekspos: audit log, MongoDB `_id`, permissions,
  operator internal, notes internal, payment, Accurate credentials, token
  hash, storage path lengkap signature.

### Targeted tests — 22 Feb 2026 (self-test PASS)
Backend (curl):
- Settings default = 4 jam + choices [1,2,4,6,12,24]; PUT ke 6 jam → OK.
- Generate → token 43 char, `design_version = design_files[-1].id`,
  `expires_at = generated_at + 4h`.
- Design re-upload → link lama otomatis `invalidated`; submit ditolak (400).
- Submit approve tanpa signer_name → 400. Tanpa signature (required=true)
  → 400. Dengan valid → `status=approved`, `design_status=Approved`,
  `design_approved_by=customer:<name>`, signature terpersist ke storage.
- Double-submit → 400 (atomic guard `updateOne{status:active}`).
- Submit reject tanpa reason (required=true) → 400. Dengan reason → status
  `rejected`, `design_status` tetap Waiting, `reject_reason` disimpan.
- Expiry: backdate → derived `expired`, submit 400. Resend membuat token baru
  dengan `expires_at` baru; token lama tetap invalid.
- Revoke → token 2 jadi `revoked`, submit 400.
- CRM (view only): PUT settings 403, generate 403, GET settings 200.
- Regression: internal approve-design endpoint masih menghasilkan
  `design_status=Approved` normal.

Frontend (screenshot smoke):
- PODetail panel render di bawah section Design, chip status/expired/approved
  benar, Buat Link Baru / Regenerate / Revoke bekerja.
- Settings tab "Customer Approval" render dengan preset jam & toggle
  (checkbox states sesuai default).
- Public page mobile (420×900): No PO, Customer, item, file design, signer
  input, signature pad canvas, tombol Approve/Reject muncul saat active;
  countdown "~3 jam 59 menit lagi" muncul.
- Client-side validation: submit tanpa signature (required) → banner
  "Tanda tangan wajib diisi" muncul, halaman tetap.
- Setelah customer approve, PODetail chip = "Approved oleh Customer",
  attachment section berubah jadi "Design Approved", signature preview
  ter-render via `/api/files/{path}`.


## Batch 1 Bugfix — Helper per-line + Deadline Delivery (22 Feb 2026)

### Root causes & fixes
- **Helper preparation total-only**: `helper_prep` hanya `qty_required/qty_prepared`. Diperluas menjadi `helper_prep.items[]` per `line_idx` (name, qty_required, qty_prepared, qty_shortage). Legacy total fields di-preserve → backward compatible. Endpoint baru `POST /orders/{id}/helper-prep/items`. Qty Dibutuhkan = read-only, source of truth = `line_items[i].quantity`. Qty Order **tidak berubah**.
- **Delivery deadline belum ada**: schema, settings, calc, manual override, warning tidak eksis. Diimplementasi full stack.
- **Operator "Lanjut" label ambigu**: di stage Cetak (10) sekarang label eksplisit "**Lanjut Packing**" (`operator-advance-<email>`), tetap di dalam area input Qty; prerequisite backend gate tidak diubah.
- **Terkirim sync**: audit menemukan `mark_delivered` sudah set `current_stage=14` + timeline entry, dan `get_order` attach `terkirim_confirmed`. Diperbaiki: `list_orders` sekarang JUGA attach `terkirim_confirmed` + `delivery_deadline_effective` → Jadwal Produksi & Dashboard konsisten dengan Detail PO.
- **Loading permission**: audit mengonfirmasi `SuratJalan.jsx` sudah pakai `hasPerm("loading.update")` / `hasPerm("delivery.update")` (tidak hardcode role). Tidak ada perubahan diperlukan.
- **Work date per operator**: audit mengonfirmasi `OperatorSplitPanel` sudah render `work_date` per split dengan gate `schedule.work_date.view/edit`. Tidak ada perubahan diperlukan.

### Schema baru
- `app_settings.delivery_deadline` = `{lead_time_days: 1|3|5}` (default **3**, calendar days).
- Order (additive, nullable):
  - `delivery_deadline_manual` (ISO), `delivery_deadline_manual_by(_name)`,
    `delivery_deadline_manual_at`, `delivery_deadline_manual_reason`.
  - `delivery_deadline_history[]` (append-only: `manual.set`, `manual.reset`).
- Order (derived, non-persisted, dihitung oleh `_attach_deadline_snapshot`):
  - `delivery_deadline_calculated`, `delivery_deadline_effective`,
    `delivery_deadline_source` (`automatic|manual|none`),
    `delivery_deadline_lead_time_days`, `delivery_deadline_status`
    (`normal|h1|h6|overdue|terkirim|unknown`).

### Formula
- `latest_approval_at = max(design_approved_at || design_bypassed_at, payment_approved_at || payment_bypassed_at)`.
- Kalau salah satu kosong → `calculated = null` (tidak muncul warning).
- **Precedence approval timestamp**: `approved_at > bypassed_at`. Kalau approved_at tidak ada tapi bypassed_at ada → pakai bypassed_at.
- `calculated_delivery_deadline = latest_approval_at + lead_time_days`
  (satuan **calendar days**).
- **Precedence effective**: `manual ?? calculated`. Manual disimpan permanent
  dan **tidak overwrite** oleh calculation background.

### Warning predicate (server-derived, konsisten Dashboard/PODetail/Schedule)
Prioritas display: **overdue > h6 > h1 > normal**.
- `terkirim`: `delivery_status="Terkirim"` atau `current_stage>=14` (warning aktif hilang).
- `overdue`: `now > effective`.
- `h6`: `0 < effective-now ≤ 6h`.
- `h1`: `6h < effective-now ≤ 24h`.
- `normal`: `>24h`.

### Permissions (baru)
- `delivery.deadline.edit` — Edit Deadline Pengiriman (manual set/reset per PO).
- `delivery.deadline.settings` — Atur Perhitungan Deadline (lead_time_days global).
Baseline `permissions.py` role defaults + top-up seed (idempotent):
- admin, head_production, head_operations: `edit` + `settings`.
- Semua UI gating via `hasPerm(...)`.

### Endpoints
- `GET /api/delivery-deadline/settings`.
- `PUT /api/delivery-deadline/settings` — `delivery.deadline.settings`.
- `POST /api/orders/{id}/delivery-deadline` — `delivery.deadline.edit`.
  Body `{deadline_at, reason?}`. Deadline_at ISO 8601; tanpa tz → assume WIB (+07:00) konsisten dengan area operasional. Persist di UTC.
- `POST /api/orders/{id}/delivery-deadline/reset` — `delivery.deadline.edit`.

### Files changed
- **Backend**: `permissions.py` (2 perm baru), `server.py` (helper + endpoints + attach snapshot di GET/list/persist + `HelperPrepItemsPayload` + endpoint helper items + top-up seed admin/head_prod/head_ops).
- **Frontend baru**: `components/DeliveryDeadlinePanel.jsx`, `components/HelperPrepPanel.jsx`.
- **Frontend edited**: `pages/PODetail.jsx` (replace helper section + selipkan deadline panel + `canEditDeliveryDeadline`), `pages/Settings.jsx` (tab baru "Deadline Pengiriman"), `components/OperatorSplitPanel.jsx` (label "Lanjut Packing" saat stage=10), `lib/api.js` (5 method baru), `App.js` (SETTINGS_PERMS diperluas).

### Targeted tests — 22 Feb 2026 (PASS)
Backend (curl):
- Settings GET default `{lead_time_days:3}` + `allowed_lead_time_days:[1,3,5]` + `unit:"calendar_days"`.
- Settings PUT 1/3/5 OK; PUT 2 → 400 dengan pesan validasi.
- `_calculated`: design 07:45 (design_approved_at), payment 07:45 (payment_approved_at) → latest = 07:45, calc = +3d = 25 Aug 07:45. Precedence design > payment kalau design lebih akhir.
- Manual set 20h dari now → `status=h1`. Manual set 4h → `h6`. Manual set -2h → `overdue`. Reset → source=`automatic`.
- CRM tanpa perm: `PUT settings 403`, `POST manual 403`.
- Helper `POST /helper-prep/items` dengan `qty_prepared=800` (line qty=1000) → `qty_shortage=200`, `shortage_status="Barang Kurang"`. Full 1000 → `Cukup`. PO `total_quantity`/`line_items[0].quantity` tidak berubah.
- `list_orders` payload include `delivery_deadline_effective/status/source` + `terkirim_confirmed` (sekarang konsisten dengan `get_order`).

Frontend (screenshot smoke — admin):
- PODetail: `helper-prep-panel` render tabel per-line (Barang / Qty Dibutuhkan 1.000 read-only / Qty Disiapkan input / Qty Kurang colored), row Total, badge "Cukup".
- `delivery-deadline-panel` render "Otomatis" · Effective "25 Agu 2026, 07.45" · Formula "Approval terakhir + 3 hari" · tombol `dd-edit-button` visible.
- Settings tab `delivery-deadline` render 3 tombol (1/3/5 hari) dengan preset aktif = "3 hari" highlighted.
- OperatorSplitPanel label "Lanjut" berubah jadi "Lanjut Packing" saat stage=10 (verify via code path).

**Regression tidak dijalankan penuh** sesuai instruksi (targeted only). Customer Design Approval tidak disentuh — tetap PASS.

### Not implemented in Batch 1 (deferred)
- Dashboard-level warning card/count untuk H-1/H-6/Overdue (data sudah tersedia di `list_orders`; UI badge belum ditambahkan).
- Display deadline effective di halaman Jadwal Produksi (data sudah ada, kolom belum ditambahkan).
Kedua item ini bisa jadi Next Action Item Batch 2 karena melampaui area file yang disentuh Batch 1.

### Data safety
- Semua field baru additive & nullable. Backward-compatible.
- Manual override permanent — tidak di-overwrite background calc.
- Workflow 15 tahap tidak diubah. Driver tetap bukan stage.
- Customer Design Approval tidak disentuh.
- Refresh setelah mutation: `PODetail` memakai `setOrder(r.order)` pada semua panel Batch 1 → UI langsung update tanpa polling.

### Aman untuk Re-publish
✅ YA. Perubahan terisolasi di file baru + additive di server.py. Migration idempotent. Tidak ada breaking change ke endpoint/schema existing.


## Batch 1 — Sisa: Dashboard warning + Jadwal deadline + Verifikasi Operator UX (22 Feb 2026)

### Yang ternyata sudah ada — HANYA DIVERIFIKASI (tidak diubah)
- **Operator Qty Target/Selesai/Sisa**: `OperatorSplitPanel.jsx` line 150-155 sudah menampilkan `{quantity} pcs`, `✓ {qty_completed}`, `sisa {remaining}` + progress bar. `remaining = max(quantity - qty_completed, 0)`. Partial progress ditampilkan sebagai bar %, bukan "selesai penuh". ✅
- **Tombol "Lanjut Packing" placement**: `.split-actions-inline` (line 175-206) memuat input Qty + tombol "Simpan qty" + tombol Lanjut Packing dalam **container yang sama** — tidak sekadar rename label. ✅
- **Refresh setelah mutation**: semua panel (Helper, Operator qty, Work Date, Deadline, Terkirim) sudah pakai pattern `onUpdated(res.order)` → parent `setOrder(...)`. UI langsung update tanpa browser refresh. Tidak ada polling. ✅
- **Customer name di PO row**: `PORow` sudah render `o.customer` (line 48 HoPDashboard, line 273 ProductionSchedule). ✅

### Yang benar-benar diubah

**Backend (`server.py`)**:
- `dashboard_summary`: attach `_attach_deadline_snapshot` pada setiap active order. Predicate `overdue/h1/h6` sekarang memakai `delivery_deadline_status` (source of truth). Tambah `kpi.deadline_h1`, `kpi.deadline_h6` + list `deadline_h1`, `deadline_h6`. `slim()` expose `delivery_deadline_effective/status/source`. `due_today` sekarang hanya untuk PO yang belum lewat + jatuh tempo hari ini.
- `production_dashboard`: attach snapshot yang sama. `_production_row()` expose 3 field baru → Jadwal.

**Frontend**:
- `pages/HoPDashboard.jsx`: 2 KPI card baru `kpi-deadline-h1` & `kpi-deadline-h6` (dengan tone warn/danger). FILTERS extended (`h1`, `h6`). `PORow` render `<DeadlineBadge>` sesuai `o.delivery_deadline_status` (H-1 amber, ≤6 JAM merah, OVERDUE merah tebal). `formatDeadline` pakai `delivery_deadline_effective ?? deadline`.
- `pages/ProductionSchedule.jsx`: kolom baru "Deadline Pengiriman" render `formatDate(delivery_deadline_effective)` + badge sumber (Manual/Otomatis) + badge status (H-1/≤6 JAM/OVERDUE). Kalau `null` (belum semua approved) → "Menunggu approval".

### Test yang dijalankan (targeted, PASS)

Backend (curl):
- Manual +20h → `kpi.deadline_h1=1`, `list=1`, PO tampil di list dengan `status=h1`, `source=manual`.
- Manual -1h → `kpi.overdue=1`, `list=1` (count = list length).
- Reset → semua kembali 0.
- `/dashboard/production` row berisi `delivery_deadline_effective/status/source` sesuai `/orders/{id}` (source of truth sama antara Dashboard, Schedule, PODetail).

Frontend (screenshot):
- Dashboard HoP: KPI card baru "H-1 Pengiriman: 1", "Deadline ≤ 6 Jam: 0", "Overdue: 0". Klik kartu H-1 → filter list muncul `(1 PO)` dengan badge H-1 amber.
- Jadwal Produksi: kolom "Deadline Pengiriman" tampil isi `23 Agu 2026, 04.37` + badge `Manual` + badge `H-1`.

### Files changed
- `backend/server.py` (dashboard_summary + production_dashboard + slim + _production_row).
- `frontend/src/pages/HoPDashboard.jsx` (DeadlineBadge component, 2 KPI card baru, FILTERS extended, PORow).
- `frontend/src/pages/ProductionSchedule.jsx` (kolom Deadline Pengiriman + badges).

### Aman untuk Re-publish
✅ YA. Perubahan additive: KPI cards baru, kolom baru, badge derived dari field yang sudah dikirim backend. Tidak ada breaking change ke schema atau API contract existing. Predicate H-1/H-6/Overdue sekarang **konsisten** untuk Dashboard, Jadwal Produksi, dan PODetail (semua dari `delivery_deadline_status`).


## Batch 2 — Attachments + Timeline PIC + Customer Name + Auto Refresh (22 Feb 2026)

### Attachment infrastructure
- **Reused existing** `POST /api/orders/{id}/attachment` (data URL storage inline di `design_files/payment_files/sample_files` per PO doc) — tidak membuat sistem file kedua.
- **MIME validation ditambah** di backend: allow-list `{image/jpeg,jpg,png,webp,gif, application/pdf}`. Reject `text/javascript`, `application/x-msdownload`, dsb → HTTP 400 dengan pesan "Tipe file tidak didukung ({ct}). Gunakan JPG, PNG, WEBP, GIF, atau PDF."
- **Max size** dinaikkan dari ~4MB ke ~6MB agar PDF muat.
- `content_type` disimpan pada attachment entry (derived dari data URL header) → dipakai frontend untuk memilih viewer.
- **Permissions view/upload**: mengikuti existing granular perms `design.upload`/`design.view`, `payment.upload`/`payment.view`, `operator.sample_upload`. Tidak ada permission baru. Backend authoritative.

### Design PDF di Customer Approval (secure)
- Public view `_public_view` mengekspos `design_file.content_type` (fallback derive dari data URL header untuk legacy file). `data_url` hanya dikembalikan kalau status `active/approved/rejected`.
- Frontend `PublicCustomerApproval.jsx` sekarang mendeteksi content-type:
  - image → `<img>`.
  - PDF → `<object type="application/pdf">` embedded viewer + link "Buka Design PDF di tab baru" (`data-testid=public-approval-design-pdf-open`).
- **Security**: file access customer tetap terikat pada secure token → `_load_by_token` (sha256 hash), + design version binding (`design_version = design_files[-1].id`). Re-upload design → `_invalidate_on_design_reupload` set link lama jadi `invalidated`. Data URL bukan storage path — tidak expose object storage. Token PO A tidak dapat meminta Design PO B karena token ↔ order_id ↔ design_version terikat 1-1.

### Timeline PIC (Penanggung Jawab)
- Helper `_picForStage(idx, order)` di `Timeline.jsx` — priority: **actor aktual > role generic**. **Tidak** memakai actor tahap sebelumnya.
- Source of truth (existing field, tidak ada duplikat):
  - Design (1): `design_files[-1].uploaded_by_name` → fallback "Designer".
  - Approval Design (2): `design_approved_by_name` atau `design_bypassed_by_name`.
  - Approval Payment (3): `payment_approved_by_name` atau `payment_bypassed_by_name`.
  - Screener (6): `screener_info.updated_by_name`.
  - Helper (7): `helper_prep.updated_by_name`.
  - Assign Operator (8): join `operator_splits[].operator_name`.
  - Sample Cetak (9): `sample_files[-1].uploaded_by_name`.
  - Approval Sampel (10): `sample_approved_by_name`.
  - Cetak (11): join operator names.
  - Packing (12): `packing_info.updated_by_name`.
  - Surat Jalan (13): `delivery_note.created_by_name`.
  - Loading/Terkirim (14): `delivered_by_name` kalau `terkirim_confirmed`, else `loading_checklist.updated_by_name`.
- Tag "PJ: {name} ({role})" render di kanan setiap timeline row (`data-testid=timeline-stage-{idx}-pic`).

### Customer Name
- Source of truth existing: `order.customer` (dipopulasi dari Accurate import/PDF/XLSX mapping). Tidak diubah, tidak invent.
- Ditampilkan pada: PODetail header (`po-customer-line`), HoPDashboard PORow (`o.customer`), ProductionSchedule kolom Customer.
- Fallback: **"Customer belum tersedia"** ditampilkan pada legacy PO tanpa nama customer.

### Auto Refresh
- Hook `useAutoRefresh(fn, {intervalMs, enabled, paused, onData, onError})` di `lib/useAutoRefresh.js`:
  - **Visibility-guarded** (`document.visibilityState === "visible"`) — polling pause saat tab background.
  - **Cleanup on unmount** (clearInterval + removeEventListener).
  - **Read-only** — hook tidak melakukan mutation.
  - **Ref-based callbacks** — versi terbaru `fn/onData` tetap dipanggil tanpa restart interval.
  - **Concurrent guard** — request in-flight tidak overlap.
- Terpasang di:
  - `PODetail.jsx` → 30s poll `api.getOrder(id)` → `setOrder(d.order)`.
  - `HoPDashboard.jsx` → 30s poll `api.dashboardSummary()` → `setData(d)`.
  - `ProductionSchedule.jsx` → 30s poll `api.productionDashboard(...)` → `setRows/setTotal`.

### Proteksi unsaved form
- Panel-panel Batch 1 (`HelperPrepPanel`, `DeliveryDeadlinePanel`, `CustomerApprovalPanel`) memakai local state (`draft`, `editing`, `signerName`, dst) yang **tidak** direset saat `order` prop berubah — hanya `useEffect([order.id, x.updated_at])` yang me-reset draft ketika **server-side timestamp** berubah (yang berarti server-side memang di-update setelah save, bukan sekadar polling).
- **Test cross-session verified**: Session A ketik "456" di Helper input → Session B update deadline manual via API → 32s berlalu (1× interval) → Session A Helper input tetap "456", tetapi `Deadline Pengiriman` panel + banner "H-6" + `Terakhir diperbarui` timestamp semua ter-update.
- Tombol **Refresh manual** ditambahkan: `po-refresh-button`, `hop-refresh-button`, `prod-refresh-button`.
- Label **"Terakhir diperbarui: HH:MM"** ditampilkan di ketiga halaman.

### Files changed
- **Backend**: `server.py` (MIME allow-list + content_type stored di entry attachment). `customer_approval.py` (public view fallback derive content_type dari data URL header).
- **Frontend baru**: `lib/useAutoRefresh.js`.
- **Frontend edited**: `pages/PublicCustomerApproval.jsx` (PDF `<object>` viewer + link buka tab baru). `components/Timeline.jsx` (helper `_picForStage` + tag PJ per row). `pages/PODetail.jsx` (auto-refresh, customer line dengan fallback, refresh button + last updated). `pages/HoPDashboard.jsx` (auto-refresh + last updated). `pages/ProductionSchedule.jsx` (auto-refresh + refresh button + last updated).

### Targeted tests — 22 Feb 2026 (PASS)
- Backend curl:
  - PNG accept 200 ✓ · PDF accept 200 ✓ · JavaScript reject 400 ✓ · x-msdownload reject 400 ✓
  - Customer approval public view mengembalikan `content_type: application/pdf` untuk PDF design.
- Frontend screenshot:
  - **Timeline PIC**: "PJ: Admin (Designer)", "PJ: Cici Helper (Helper)" (actor aktual), "PJ: Driver" (generic karena belum ada aktor) — konsisten dengan aturan prioritas.
  - **Customer name**: "Customer: **ZAA COFFEE & TEA**" ditampilkan; fallback string tersedia untuk PO tanpa customer.
  - **Refresh manual**: klik tombol → timestamp berubah dari "-" → "08.50".
  - **Public PDF viewer**: `pdf_embed=1`, `pdf_link=1`, mobile 420px card render.
  - **Cross-session auto-refresh**: Session A helper input "456" **preserved** setelah 32s; deadline effective + banner H-6 + "Terakhir diperbarui: 08.52" **ter-update**.

### Aman untuk Re-publish
✅ YA. Perubahan additive:
- MIME allow-list backend hanya menambahkan validasi (files existing pass tanpa `content_type` — legacy fallback derive dari data URL header).
- Polling read-only, tidak melakukan mutation.
- Semua data-testid tetap konsisten.
- Batch 1 (Delivery Deadline) + Customer Design Approval tidak diubah — tetap PASS.
- Workflow 15 tahap tidak diubah. Accurate integration tidak disentuh.
- Storage existing tidak dipindah/dihapus.


## Batch 3 — Production Routing (Cetak/Non-Cetak) + Dashboard Action Center (22 Feb 2026)

### Model/classification
- Field **additive** pada `line_items[].production_process` ∈ `{"print","non_print"}`.
- **Legacy fallback**: line item tanpa `production_process` = **dianggap `print`** (backward-compatible). PO lama tidak tiba-tiba melewati tahap.
- Derived helper `_requires_printing(order)` di server.py: **ANY** line item dengan `production_process=="print"` → wajib print-specific. Kalau ada minimal 1 line yang belum diklasifikasi & tidak ada `print` → default anggap print (safe fallback).
- Attach `requires_printing` di derived (non-persisted) via `_persist_order` dan `get_order`.

### Rule per skenario
- **PO semua Cetak (default legacy)**: workflow existing tidak berubah — semua print-specific gate berlaku.
- **PO semua Non-Cetak**: `_prereq_reasons_for_advance` & `_prereq_reasons_for_packing` **skip** Sample Upload, Sample Approve, Cetak Qty. Tahap Penjadwalan/Screener/Helper/Packing/Surat Jalan/Loading/Terkirim tetap wajib. Timeline print-specific stage tampil **"TIDAK DIPERLUKAN"** (bukan Selesai palsu, bukan Menunggu).
- **PO campuran (min 1 print)**: `_requires_printing==True` → print-specific tetap wajib untuk semua bagian. Tidak invent partial-shipment.

### Qty routing
- Qty Order line item **tidak diubah** oleh classification. Customer, Accurate mapping, line count tidak berubah.
- Helper preparation tetap all-lines (fisik barang).
- Operator target: tetap `operator_splits[].quantity` (business belum meminta re-split — dan qty aktual sudah di-track terpisah).

### Endpoint & Permission
- Baru: `POST /api/orders/{id}/line-items/route` body `{items:[{line_idx, production_process}]}` — permission `production.route.edit` (baru, di-seed admin + head_production top-up).
- Audit log: `production.route.update` dengan sebelum/sesudah routes.

### Dashboard Action Center — SOURCE OF TRUTH
- Endpoint canonical **baru**: `GET /api/dashboard/action-items`.
- Effective permission dihitung server-side (admin bypass; else union role + user overrides).
- 13 action + 1 operator special:
  - `design_upload` → `design.upload` + `!design_files`
  - `design_approve` → `design.approve` + design uploaded & belum approved/bypass
  - `payment_approve` → `payment.approve` + payment uploaded & belum approved/bypass
  - `penjadwalan` → `schedule.edit` + penjadwalan belum done
  - `screener` → `screen.update` + qty_screen kosong
  - `helper` → `helper.prepare` + qty_prepared<qty_required
  - `sample_upload` → `operator.sample_upload` + **requires_printing** + belum upload
  - `sample_approve` → `operator.sample_approve` + **requires_printing** + uploaded belum approved
  - `cetak_qty` → `operator.update` + **requires_printing** + sample approved + qty cetak kosong
  - `packing` → `packing.update` + prereq packing OK & stage<12
  - `surat_jalan` → `delivery_note.manage` + stage>=11 & belum SJ & <13
  - `loading` → `loading.update` + SJ dibuat & checklist belum lengkap & belum terkirim
  - `terkirim` → `delivery.update` + checklist done & belum terkirim
  - `operator_work` (spesial): `operator.update` + operator_splits dengan remaining>0. Scope `"team"` kalau user punya `schedule.edit` (supervisor) — jika tidak, filter split ke `operator_email==user.email`.
- **Count == list**: setiap bucket kembalikan `count = len(items)`. Frontend tidak menghitung ulang.
- Approval Design & Payment **independen** — satu PO bisa muncul di dua bucket sekaligus.
- Sorting: overdue > h6 > h1 > normal > unknown > terkirim.

### Frontend
- **Baru**: `components/ProductionRoutePanel.jsx` — tabel per line + tombol Cetak/Tidak Cetak, badge "Perlu Cetak" / "Semua Non-Cetak". `data-testid=production-route-panel`, `route-print-{idx}`, `route-nonprint-{idx}`, `requires-printing-badge`.
- **Baru**: `components/ActionCenter.jsx` — panel "Tugas Saya (& Tim)" di HoPDashboard. Cards `ac-card-{key}` + count `ac-count-{key}`. Klik card → list rows `ac-row-{key}-{po_number}` dengan customer, deadline effective, badge H-1/H-6/OVERDUE. "Hapus Filter" (`ac-clear-filter`). Reuse `useAutoRefresh` 30s → tidak polling kedua.
- `pages/PODetail.jsx`: wire `ProductionRoutePanel` (visible untuk permission `production.route.edit`).
- `components/Timeline.jsx`: state `skipped` untuk stage 9/10/11 saat `requires_printing==false`. CSS chip warna netral.
- `pages/HoPDashboard.jsx`: mount `<ActionCenter />` di bawah `ReminderList`.
- `lib/api.js`: 2 method baru (`setLineItemRoute`, `dashboardActionItems`).

### Security / RBAC
- Dashboard tidak memberi akses baru. Setiap action card hanya muncul kalau `req_perm ∈ effective_permissions(user)`. Backend `_require_perm` tetap dijalankan pada endpoint action-nya sendiri.
- Override Grant (Effective Permission) → task muncul. Override Deny → task hilang (auto-refresh 30s).
- Non-Cetak PO **tidak** menghasilkan task Sample/Cetak → aksi terkait hilang dari Action Center.

### Targeted tests — 22 Feb 2026 (PASS)
Backend curl:
- Legacy PO tanpa `production_process` → `requires_printing=True`, task Sample/Cetak muncul.
- Set line 0 → `non_print` → `requires_printing=False`; `sample_upload` count turun (PO exit); `cetak_qty=0`; qty line 0 (1000) **tidak berubah**.
- Set line kembali `print` → semua state kembali. Idempotent.
- CRM `POST /line-items/route` → **403** `production.route.edit`.
- Helper login → hanya `helper` bucket (`count=1`) — permission filter benar.
- Admin action items 14 bucket, `count==len(items)` untuk semua bucket ✓.

Frontend screenshot:
- HoPDashboard "Tugas Saya & Tim" render 5 cards: Approval Design 1, Penjadwalan 2, Isi Qty Screen 2, Persiapan Helper 1, Upload Sampel Cetak 1.
- Klik "Penjadwalan (2)" → list "Menampilkan: Penjadwalan — 2 PO" dengan 2 rows (customer + deadline). `COUNT==LIST=2` ✓. Link ke `/po/{id}` (Link, bukan mutation).
- PODetail route panel: badge **"Semua Non-Cetak"**, tabel line dengan tombol Cetak / **Tidak Cetak** active.
- Timeline non-print: stage 9 Operator / 10 Konfirmasi Sample / 11 Cetak chip = **"TIDAK DIPERLUKAN"** dengan style skipped (bukan Selesai/Menunggu).
- Timeline PIC tetap render normal untuk stage lain.

### Regression minimal
- Workflow Cetak existing tetap PASS (semua PO existing default `print`).
- Customer Design Approval tetap PASS.
- Deadline Batch 1 tetap PASS.
- Helper per-line tetap PASS.
- Attachment/PDF tetap PASS.
- 15-stage mapping tetap PASS. Accurate tidak disentuh.

### Aman untuk Re-publish
✅ YA. Semua field additive & nullable. Legacy default preserving print behavior. Predicate action canonical di backend (single source of truth). Auto-refresh reuse existing hook. Tidak ada mutation endpoint baru di Dashboard.



---

## REVISI ROUTING — LINE ITEM + OPERATOR + HELPER + PACKING (Feb 2026)

Revisi business rule Batch 3 Production Routing. Sekarang scope 3 qty berbeda:
- **Qty PO** = sum(all line_items.quantity)
- **print_qty_total** = sum(line.quantity where production_process=="print") — legacy line dianggap print
- **packing_required_total** = sum(all line_items.quantity)

### Backend (`server.py`) — perubahan
- Helpers baru: `_is_print_line`, `_print_line_indices`, `_print_qty_total`, `_packing_required_total`.
- `_stage_penjadwalan_done` / `_stage_screener_done` / `_stage_helper_done` → short-circuit `True` bila `requires_printing==False` (full non-print PO tidak butuh 05/06/07/08 print-preparation).
- `OperatorSplitInput` menerima `line_idx: Optional[int]` (additive, backward-compat). Legacy split tanpa `line_idx` tetap di-render (label "Barang: (legacy)").
- `POST /assign-operators` sekarang enforce:
  - `sum(qty) ≤ print_qty_total` (BUKAN total_quantity)
  - Setiap `line_idx` wajib line **print** (tidak boleh `non_print`)
  - Per-line: `sum(qty untuk line X) ≤ line X quantity`
- `POST /helper-prep/items` sekarang hanya menghitung line **print** untuk `qty_required`/`qty_prepared`/`qty_shortage`; non-print line di-drop dari `items[]` & totals.
- `POST /packing-info` cap qty ≤ `packing_required_total`.
- `POST /packed` sekarang wajib `packing_info.qty_packed ≥ packing_required_total` (belum dukung partial shipment).
- `GET /orders/{id}` expose derived `print_qty_total`, `packing_required_total`.
- Action items: `_po_needs_helper` & `_po_needs_screener` return `False` bila `!requires_printing`. `_po_needs_penjadwalan` otomatis mengikuti karena `_stage_penjadwalan_done` short-circuit.

### Frontend — perubahan
- `Timeline.jsx`: STAGE_PENJADWALAN/SCREENER/HELPER show "Tidak Diperlukan" saat `!requires_printing`.
- `HelperPrepPanel.jsx`: hanya menampilkan line print. Full non-print → panel menampilkan badge "Tidak Diperlukan" tanpa input.
- `OperatorSplitPanel.jsx`: setiap row edit wajib pilih line (print). Existing splits menampilkan label "Barang: Item X · Warna" (legacy → "(legacy)"). Full non-print → panel "Tidak Diperlukan".
- `PackingPanel.jsx`: table breakdown semua line (print + non-print), target = `packing_required_total`. Tombol Selesai Packing disabled sampai `qty_packed ≥ target`. Menampilkan progress `qty/target` + shortage.
- `PODetail.jsx` line-items panel: pill scope "Total PO / Perlu Cetak / Tidak Cetak"; setiap line item pill "Cetak"/"Non-Cetak".

### Targeted test — PASS (2026-08)
- **Mixed 3000+3000**: Operator max=3000 (assign 4000 REJECT ✓). Assign ke line non-print REJECT ✓. Helper scope=3000, shortage 300 pada Item A prepared 2700 ✓. Packing target=6000, save 5500 accepted tapi `/packed` REJECT (kurang 500) ✓, save 6000 → `/packed` OK → stage 12 ✓.
- **Full print 2000+1000**: helper items=2, total=3000 ✓; operator per-line OK; packing target=3000.
- **Full non-print 500+500**: `packing_prereq_reasons=[]` (Penjadwalan/Screener/Helper skipped) ✓, packing target=1000, `/packed` gate sama.

### Legacy handling
- Line item tanpa `production_process` → dianggap `"print"`.
- Operator split tanpa `line_idx` → tetap dirender ("Barang: (legacy)"). Backend TIDAK melakukan mass-migration.

### Aman untuk Re-publish
✅ YA. Semua field additive & backward-compatible. Tidak ada perubahan pada Customer Approval, Deadline, Dashboard Action Center, Surat Jalan, Loading, Terkirim, Accurate. Legacy PO tetap perilaku print. Assign lama tanpa line_idx tetap bekerja read-side.

