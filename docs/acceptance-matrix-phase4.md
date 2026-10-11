# Acceptance Matrix — Phase 4 (Tahap 4 + 5 + 6)

Baseline main: `72553b2` (PR #552 merged, not deployed).  
Keputusan deploy: **HOLD**.

## A. Fitur × Status

| ID | Fitur / Alur | Bukti | Status |
|----|--------------|-------|--------|
| F01 | Buat PO + buka kembali detail `/po/{id}` | FE create workspace tests + route nav | PASS* |
| F02 | Buat Faktur dari PO | FE Faktur create + remaining qty | PASS* |
| F03 | Faktur parsial / multi-invoice (sisa qty) | Validasi sisa qty UI + existing tests | PASS* |
| F04 | Edit Faktur finalized via revisi lifecycle | `updateSalesInvoice` + interpret lifecycle | PASS* |
| F05 | Business cancel Faktur (bukan hard delete) | Cancel UI + Phase 3 wiring | PASS* |
| F06 | Catat Penerimaan + AR paid/outstanding | Create modal + detail AR labels | PASS* |
| F07 | Pembayaran parsial / lunas (display AR) | Detail outstanding/paid testids | PASS* |
| F08 | Receipt pending settlement (tidak dihapus) | Hapus/Edit UI frozen | PASS |
| F09 | Search / 5-tab / pagination daftar | Existing five-tab FE | PASS* |
| F10 | Navigasi PO → Faktur → Penerimaan | Deep link `invoice_id` / `receipt_id` + links | PASS |
| F11 | Validasi input / error / retry idempotency | Existing idempotency helpers | PASS* |
| F12 | Audit trail tidak dihapus UI | Tidak ada hard-delete path baru | PASS |
| F13 | Edit Penerimaan OFF | `RECEIPT_MUTATION_UI_ENABLED=false` | PASS |
| F14 | Hapus/Batalkan Penerimaan OFF | showDeleteControls gated | PASS |
| F15 | Responsive desktop 1366/1440 | Preview screenshots | PASS** |
| F16 | Responsive mobile 390/360 | Preview screenshots | PASS** |
| F17 | DD/MM/YYYY date display | SalesInvoiceDateField + detail dates | PASS |
| F18 | SOP operasional | `docs/sop-penjualan-phase4.md` | PASS |

\* Regression guard via focused FE tests; full role E2E di test DB memerlukan akun role — lihat matriks role.  
\*\* Screenshot aktual real-shell; lihat artefak PR.

## B. Role × Hak (harus diuji dengan RBAC aktual)

| Aksi | Admin | CRM | Sales | Direktur |
|------|-------|-----|-------|----------|
| Lihat daftar PO/Faktur/Penerimaan | tergantung permission | tergantung permission | tergantung permission | tergantung permission |
| Buat PO | biasanya YA | cek `sales_order.create`/`po.edit` | cek permission | cek permission |
| Buat Faktur | biasanya YA | cek `sales.invoice.create` | cek permission | cek permission |
| Edit / Batalkan Faktur | YA bila perm | cek `edit`/`cancel` | cek permission | cek permission |
| Catat Penerimaan | biasanya YA | cek `sales.receipt.create` | cek permission | cek permission |
| Edit/Hapus Penerimaan | **UI OFF** | **UI OFF** | **UI OFF** | **UI OFF** |

Catatan: sel “tergantung permission” = jangan asumsikan; verifikasi di Settings → role sebelum Owner Acceptance.

## C. Blocker / Non-blocker / Residual

### Blocker
- Tidak ada blocker frontend dalam scope yang menghalangi Owner Acceptance UI Phase 4.
- Jika ditemukan kegagalan backend finansial di test DB saat Owner Acceptance → **BLOCKED** untuk deploy (bukan di-patch diam-diam di PR ini).

### Non-blocker
- Detail faktur lines masih scroll horizontal internal di mobile sempit (indikator overflow container); tidak overflow viewport halaman.
- Widget notifikasi tetap ada; footer detail/create memakai padding aman FAB.
- Hapus/Edit Penerimaan tetap OFF — koreksi via penerimaan baru / eskalasi.

### Risiko residual
- AR list projection vs detail live: detail memakai payload `getSalesInvoice`/`getSalesReceipt`; list mengandalkan field API list. Refresh setelah mutasi tetap diperlukan.
- Role CRM/Sales/Direktur belum dijalankan login penuh di lingkungan Cloud Agent ini tanpa kredensial role terisolasi → Owner Acceptance wajib uji role.

### Fitur tetap dinonaktifkan
- Edit Penerimaan
- Hapus / Business Cancel Penerimaan (UI)
- Migrasi historis / mass-post Faktur legacy
- Deploy production (HOLD)

## D. Keputusan

**READY_FOR_OWNER_ACCEPTANCE**

Syarat Owner sebelum deploy tunggal:
1. Review PR gabungan ini + PR #552 (sudah di main, belum deploy).
2. Uji role Admin/CRM/Sales/Direktur di test DB.
3. Setelah diterima: satu deploy target commit final merged dengan prosedur backup/lock/build/health/rollback Eternal.
