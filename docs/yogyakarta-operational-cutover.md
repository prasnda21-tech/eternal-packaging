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
- Izin modul tetap wajib. Aturan editor: centang, simpan, langsung berlaku;
  hapus centang, simpan, akses dicabut. Tidak ada dialog persetujuan kedua.
  Grant package yang tersimpan tetapi tidak efektif pada evaluator produksi
  saat ini tidak boleh menyala saat kode baru diaktifkan. Prosedur transisi
  ada di bagian berikut. Holding dan Direktur mempertahankan grant package
  yang sudah efektif. Cakupan cabang global tidak menambah izin modul.
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

## Transisi izin produksi

Evaluator yang sekarang hidup di produksi mengabaikan
`package_production_confirmed_keys` dan menghapus seluruh kunci package untuk
semua role selain `holding_team` dan `direktur_utama`. Evaluator baru, bila
diaktifkan lebih dulu, menghormati stempel itu pada dokumen yang belum
dinormalisasi. Grant package yang tersimpan tetapi tidak efektif akan menyala.
Karena itu cutover izin dijalankan saat evaluator lama masih yang hidup,
baru kemudian kode baru diaktifkan.

Evaluator lama juga mengabaikan marker `manual_grant_normalized`. Menghapus
kunci package yang tidak efektif dan menulis marker tidak mengubah akses
selama kode lama masih hidup. Kunci opt-in yang memang sudah tersimpan pada
dokumen role, atau override pengguna bernilai benar, tetap efektif dan tidak
dihapus. Stempel konfirmasi hanya dilaporkan, tidak dijadikan dasar grant.
Tidak ada daftar persetujuan baru.

Admin dan Super Admin tidak diubah. Field cabang tidak ditulis. Backfill
Yogyakarta (`backend/scripts/migrate_ygy_operational.py`) hanya mengisi
assignment cabang, melewati Direktur dan Holding, dan tidak menyentuh izin.
Jangan menulis ulang atau menjalankan ulang backfill itu bila marker
`ygy_operational_v1` sudah ada. Kedua prosedur boleh berada dalam jendela
yang sama karena field-nya berbeda. Cutover izin harus selesai sebelum kode
izin baru aktif.

Urutan:

1. Dry-run, tanpa menulis:
   `python3 scripts/dry_run_manual_grant_cutover.py`
2. Review selisih: kunci package yang dihapus, yang dipertahankan, kunci
   opt-in yang tetap, dan stempel konfirmasi yang diabaikan.
3. Pastikan backup MongoDB penuh dapat dipulihkan. Skrip apply juga menulis
   `manual_grant_cutover_backups` dan menghitungnya sebelum update.
4. Hentikan perubahan izin. Pertahankan jeda ini sampai kode baru aktif.
   Penyimpanan editor lama dapat memasukkan kembali kunci package ke dokumen
   yang sudah ditandai normalized.
5. Apply saat evaluator lama masih hidup. Jangan mengubah `APP_ENV`.
   `python3 scripts/dry_run_manual_grant_cutover.py --apply --database "$DB_NAME"`
   Nama database harus sama dengan `DB_NAME`. Dokumen yang berubah sejak
   perencanaan tidak ditimpa; proses berhenti.
6. Verifikasi akses efektif tidak berubah: kunci package yang tadinya tidak
   efektif tetap mati, Holding/Direktur dan opt-in eksplisit tetap hidup.
   Apply ulang melewati dokumen yang sudah normalized, jadi grant manual
   yang diberikan sesudahnya tidak terhapus.
7. Aktifkan kode izin baru. Setelah kode itu hidup, rollback skrip di bawah
   tidak aman karena mengembalikan kunci package yang evaluator baru akan
   anggap efektif. Pulihkan dari backup MongoDB penuh.

Rollback sebelum kode baru aktif, default dry-run:

`python3 scripts/rollback_manual_grant_cutover.py`

`--apply --database "$DB_NAME"` memulihkan grant hanya bila dokumen masih
sama dengan hasil migrasi. Perubahan sesudah migrasi ditolak.

Prosedur ini siap dijalankan pada produksi. Jangan menjalankannya sebagai
bagian dari deploy, dan jangan mengarahkannya ke produksi dari lingkungan
pengembangan.

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
- Izin editor: centang izin biasa, bekas opt-in, atau bekas package lalu simpan
  membuatnya efektif tanpa dialog tambahan. Hapus centang lalu simpan mencabut
  akses, dengan override yang memang berlaku tetap dihormati. Draft bertahan
  pada 409. Pilih semua mencakup izin yang terlihat. Cutover grant lama
  dijalankan manual saat evaluator lama masih hidup, bukan saat deploy.
- Backfill `users` melewati `direktur_utama` dan `holding_team`: `branch_ids`,
  `primary_branch_id`, dan `branch_id` mereka tidak diubah. Ringkasan mencantumkan
  `users_global_skipped`. Karyawan lain dipetakan ke Yogyakarta.
- Checkbox Pembelian/Persediaan/HPP yang tidak efektif pada baseline produksi
  tetap tidak efektif sesudah cutover. Stempel konfirmasi tidak membuatnya
  hidup. Holding dan Direktur mempertahankan grant package yang sudah ada.
  Editor tidak menambah dialog persetujuan dan tidak mengunci package
  berdasarkan nama role.

## Batas validasi saat penyusunan

Tes terarah lokal menggunakan fungsi autentikasi asli yang diekstrak AST,
handler permission, service cabang, dan database dummy. Ini belum menggantikan
smoke seluruh aplikasi dengan MongoDB terisolasi maupun smoke akun produksi.
Perlu CI dan uji penerimaan di atas sebelum merge/deploy. Pembatasan seluruh
modul ERP (misalnya payroll dan seluruh analitik historis) tidak diklaim oleh patch ini.
