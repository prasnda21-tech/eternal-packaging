# Cabang operasional Yogyakarta: penerapan dan verifikasi

Basis implementasi: 652c38c1a8b80b28d22d6b465a7df4fe7bb53bf5.
Perubahan ini memerlukan pemetaan metadata sebelum pembatasan cabang baru diaktifkan.
Jangan jalankan skrip sebagai bagian otomatis startup atau deploy.

## Model dan otoritas

- `operational_branch_id` adalah ID internal master Cabang untuk PO, Pra PO,
  faktur, pengiriman, penerimaan pembayaran dan shipment PO.
- `branch_id` pada transaksi tetap ID sumber Accurate/legacy. Jangan menggantinya
  dengan UUID internal: desain cetak dan integrasi masih membacanya.
- Karyawan memiliki `branch_ids` dan `primary_branch_id`. Alias
  `users.branch_id` mengikuti cabang utama. Cabang utama hanya default transaksi.
  `direktur_utama` dan `holding_team` tetap cakupan global, termasuk cabang baru,
  tanpa assignment satu per satu. Cakupan global tidak memberi izin modul.
- Penetapan cabang dan pembuatan akun mengikuti izin yang sudah ada:
  `users.manage` untuk karyawan, `org.branches.manage` untuk master cabang.
  Bukan nama role. Pemegang izin tanpa cakupan global hanya boleh menetapkan
  cabang yang ia sendiri pegang. Pengguna tanpa izin ditolak, termasuk lewat API.
- Izin modul tetap wajib. Grant Pembelian/Persediaan/HPP yang belum disetujui
  tetap tidak efektif. Persetujuan eksplisit (`confirm_package_keys`) membuat
  hanya daftar yang ditinjau menjadi efektif, untuk role apa pun. Holding dan
  Direktur mempertahankan grant package yang sudah efektif. Simpan biasa tidak
  menumbuhkan daftar persetujuan.
- Cabang kosong tidak memberikan akses global di produksi. Respons 403 meminta
  pengguna yang berwenang mengatur karyawan.
- Outlet pelanggan berbeda dari cabang perusahaan.

## Pemetaan satu kali

Pemilik menyatakan seluruh data saat ini merupakan Yogyakarta. Skrip
`backend/scripts/migrate_ygy_operational.py` memetakan koleksi
`users`, `orders`, `pra_pos`, `sales_invoices`, `sales_deliveries`,
dan `sales_receipts`. Ini bukan migrasi universal seluruh koleksi ERP.
Stok, jurnal, saldo, nominal, kuantitas dan ID Accurate tidak ditulis.

1. Verifikasi SHA, CI, backup MongoDB yang dapat dipulihkan, master cabang,
   jumlah dokumen dan kondisi VPS aktual. Hentikan penulisan operasional
   termasuk impor/background worker selama apply dan aktivasi.
2. Gunakan runtime Python backend dan konfigurasi MONGO_URL/DB_NAME yang sudah
   diverifikasi, tanpa mencetak rahasia. Jalankan skrip tanpa argumen:
   mode default DRY_RUN, tidak menulis DB.
3. Review jumlah tiap koleksi. Bila master YGY tidak ada, skrip mengusulkan ID
   deterministik; master duplikat/nonaktif membuat skrip berhenti.
4. Setelah review dan persetujuan penerapan data, jalankan dengan
   `--apply --all-existing-ygy --backup <file-baru>`.
   File backup metadata dibuat eksklusif dengan mode 0600. Setiap record
   dicatat dan fsync sebelum update bersyarat. Konflik membuat proses berhenti.
   Jika terputus, simpan seluruh jurnal sebelum mencoba melanjutkan.
5. Setelah sukses, marker `migration_status/ygy_operational_v1` mencegah
   apply ulang yang dapat menimpa data cabang baru. Jangan hapus marker
   untuk memaksakan migrasi.
6. Aktifkan backend/frontend baru dengan SOP deploy yang berlaku, lalu buka
   penulisan hanya setelah verifikasi. Kode lama kompatibel dengan field tambahan.

## Rollback metadata

`backend/scripts/rollback_ygy_operational.py <file-backup>` default dry-run;
`--apply` memulihkan field yang tercatat hanya jika nilainya masih sama
dengan hasil migrasi. Konflik berhenti dan perlu review. Master YGY dan marker
migrasi dipertahankan. Rollback kode tidak membatalkan transaksi baru.
Gunakan backup MongoDB penuh untuk pemulihan bencana, bukan jurnal metadata saja.

## Uji penerimaan sebelum selesai

- Login nyata CRM, Kepala Produksi, Admin: izin view dan cabang YGY menghasilkan
  PO YGY pada daftar/detail/dashboard yang diizinkan.
- Pengguna dengan `users.manage` mengubah cabang karyawan dalam cakupan yang ia
  pegang. Pengguna tanpa izin ditolak. Request dengan token lama membaca
  assignment DB terbaru. Pengguna tanpa assignment mendapat pesan 403.
- Dummy cabang kedua: list, detail URL, ekspor produksi, repeat order, Pra PO
  dan dokumen penjualan menolak/menyaring data cabang lain.
- PO manual dan impor: pilih cabang; default memakai cabang utama. ID Accurate
  tetap utuh; dokumen turunan mewarisi cabang operasional.
- Izin khusus: memilih satu menampilkan konfirmasi daftar key saat Simpan;
  menolak konfirmasi mempertahankan draft. Pilih semua tidak mengaktifkan
  izin opt-in. PATCH tanpa konfirmasi exact ditolak; stale snapshot tetap 409.
- Backfill `users` melewati `direktur_utama` dan `holding_team`: `branch_ids`,
  `primary_branch_id`, dan `branch_id` mereka tidak diubah. Ringkasan mencantumkan
  `users_global_skipped`. Karyawan lain dipetakan ke Yogyakarta.
- Checkbox Pembelian/Persediaan/HPP yang tersimpan tetapi belum disetujui tidak
  menjadi efektif. Editor tidak lagi mengunci package berdasarkan nama role.

## Batas validasi saat penyusunan

Tes terarah lokal menggunakan fungsi autentikasi asli yang diekstrak AST,
handler permission, service cabang, dan database dummy. Ini belum menggantikan
smoke seluruh aplikasi dengan MongoDB terisolasi maupun smoke akun produksi.
Perlu CI dan uji penerimaan di atas sebelum merge/deploy. Pembatasan seluruh
modul ERP (misalnya payroll dan seluruh analitik historis) tidak diklaim oleh patch ini.
