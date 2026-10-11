# Aktivasi Edit & Batalkan Penerimaan Penjualan

Kode UI/backend sudah mendukung Edit (reverse→repost) dan Batalkan (soft-cancel→reverse).
**Flag production tetap OFF sampai Owner mengaktifkan secara bertahap.**

## Permission (opt-in — jangan auto-grant)

| Key | Tujuan |
|-----|--------|
| `sales.receipt.edit` | Edit nominal / alokasi / tanggal / Kas-Bank |
| `sales.receipt.cancel` | Soft-cancel bisnis |

Berikan hanya ke **Admin Keuangan** dan **Direktur** lewat Settings → Role & Hak Akses.
Tidak ada grant otomatis ke CRM / Sales Eternal / role lain.

## Feature flags (default OFF)

| Flag | Efek |
|------|------|
| `sales_receipt_edit_revision_enabled` | Posted receipt edit reverse→repost |
| `sales_receipt_business_cancel_enabled` | Soft-cancel + reverse |
| `financial_journal_revision_enabled` | Harus ON (global) |
| Allowlist revisi jurnal memuat `sales_receipt` | Wajib |

## Indeks wajib

- `uniq_sales_receipt_edit_revision_key` pada `sales_receipt_edit_revisions`
- `uniq_sales_business_cancel_key` pada `sales_business_cancellations`
- Primary journal index (sesuai modul posting)

Cek readiness: `GET /api/sales-receipts/lifecycle-capability` → `edit_revision.available` / `business_cancel.available`.

## Rencana bertahap

1. **Staging / disposable DB:** nyalakan flags + grant perm ke akun uji Admin Keuangan & Direktur.
2. Jalankan suite: `test_sales_receipt_edit_revision.py`, `test_sales_business_cancel.py` (receipt cases), `test_sales_lifecycle_capability.py`.
3. Uji UI: Edit posted, Batalkan posted, permission denied, periode terkunci.
4. **Production (Owner SSH only):** dry-run aktivasi → apply flags → grant perm → verify capability READY → smoke read-only.
5. Jangan deploy UI tanpa flags/index/perm siap (tombol tetap fail-closed).

## Rollback

1. Matikan `sales_receipt_edit_revision_enabled` dan/atau `sales_receipt_business_cancel_enabled`.
2. Cabut `sales.receipt.edit` / `sales.receipt.cancel` dari role bila perlu.
3. UI: tombol hilang karena `lifecycleOpAvailable` / opt-in perm fail-closed.
4. Jangan hapus histori revisi/cancel; jangan migrasi balik.

## Yang tidak dilakukan skrip aktivasi umum

- Tidak mengubah transaksi historis.
- Tidak hard-delete.
- Tidak refund fisik otomatis.
- Tidak auto-grant permission.
