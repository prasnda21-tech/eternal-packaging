# Ketersediaan Kas

Basis PR #698: `eb121e107eabbae4163dfa9242ab42ee77f101aa`.

Owner memilih saldo seluruh kas/bank saja: tidak ada proyeksi, rencana pembayaran, atau ketergantungan rekonsiliasi.

## Sumber dan perilaku

- GET `/api/dashboard/widget/cash-available`, izin `cash_bank.view`, no-store. Tidak menerima filter periode; cutoff tanggal bisnis hari ini Asia/Jakarta.
- Semua COA bertipe BANK (tipe Kas/Bank pada ERP), termasuk akun tidak aktif yang masih mempunyai saldo. Nama dari master kas/bank jika terhubung, fallback nama COA.
- `load_statement_accounts` dan `sum_journal_lines_by_account` existing: seluruh batch jurnal, sama dengan neraca; debit dikurangi kredit kumulatif. Saldo awal, transfer dan pembalikan ikut sesuai aturan buku besar. Draft/future tidak masuk; jurnal legacy tanpa status mengikuti aturan existing. Tidak mengubah aturan jurnal.
- Satu akun buku besar dihitung sekali, walau lebih dari satu master menunjuk akun yang sama. Akun nol dan negatif tetap ditampilkan.
- Master tanpa tautan BANK valid tetap muncul sebagai "Belum terhubung"; total penuh null/incomplete dan subtotal rekening terhubung diberi label. Tanpa akun BANK unavailable, bukan nol. Ada akun tanpa transaksi = saldo nol.
- Tidak membaca koleksi rekonsiliasi, mutasi statement bank, piutang, utang, atau rencana pembayaran. Bukan saldo bank eksternal yang disinkronkan; nominal mengikuti unit pembukuan laporan existing, tidak mengonversi valas atau memakai currency metadata statement sebagai kurs.
- Kartu tersedia di Tambah Widget, kategori Keuangan; tidak menimpa layout/default pengguna. Total dan seluruh rekening ada pada kartu (body dapat digulir), waktu pembaruan WIB dan tombol Muat ulang saldo. Scope perusahaan mengikuti izin kas/bank existing, tanpa filter cabang baru.

## Verifikasi

- 8 tes backend lulus: sumber helper nyata dengan database fixture read-only, kumulatif, transfer, negatif, nol, reversal, legacy, cutoff WIB, draft/future, master duplikat/unmapped, seluruh 1.001 jurnal, tidak membaca rekonsiliasi, permission route/layout.
- 38 tes frontend / 4 suite lulus: halaman Finance termasuk load/refresh saldo tanpa argumen periode atau panggilan rekonsiliasi, incomplete/permission, layout dan regresi board/presence.
- Browser Chromium komponen aktual + fixture: 1366/1440 dan CSS zoom125%, footer dalam kartu, tanpa horizontal overflow/error JS, tombol refresh berfungsi.
- Full app build, Mongo end-to-end, pencocokan angka database operasional dan CI belum dijalankan.

Tidak mengubah saldo, data operasional, dependency atau CI workflow. Tidak merge/deploy. Katalog kini **34 tersedia, 22 pending** (Produksi 23/25, Keuangan 11/31).

## CI note
Validation re-triggered on HEAD without [skip ci] for PR merge review.

