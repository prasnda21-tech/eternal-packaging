# Pengguna Aktif — implementasi lanjutan PR #698

Basis: `8e579f5ea1068036f710c154503a84c06716355a`.
Owner meminta nama pengguna, waktu terakhir aktif dan label Online ketika web terbuka.

## Kontrak

- POST `/api/auth/presence`: authenticated `current_user`, identitas hanya dari sesi.
- Web mengirim heartbeat saat mount, setiap 30 detik, pageshow dan koneksi kembali.
- Online berarti heartbeat kurang dari 90 detik. Tab tertutup/koneksi hilang kedaluwarsa; tidak bergantung pada unload yang tidak andal. Beberapa tab/perangkat memakai `$max` pada user yang sama.
- Interaksi pointer, keyboard, scroll dan touch yang trusted dicatat terpisah. Heartbeat idle tidak mengubah waktu interaksi. Tidak merekam isi input, halaman, alamat IP atau histori event.
- Dua timestamp BSON pada `users.web_presence`: `seen_at`, `active_at`. Clock server; payload hanya umur interaksi (0–90.000 ms), bukan user id atau absolute timestamp. `$max` mencegah timestamp mundur.
- GET `/api/dashboard/active-users`: izin existing `users.view`. Akun status active/legacy tanpa status; proyeksi hanya id, nama, kedua timestamp. Pagination 50, maksimal 100; total eksplisit. Urut interaksi terbaru, heartbeat terbaru, id stabil.
- Widget dipasang melalui **Tambah Widget → Aktivitas → Pengguna Aktif**. Tidak mengubah susunan default/susunan tersimpan. Bukan metrik periode keuangan.
- Tampilan: nama, Online/Offline, waktu relatif + waktu lengkap WIB; data belum tercatat tetap "Belum tercatat". Poll daftar 30 detik; status kedaluwarsa dihitung ulang tiap 5 detik dengan clock server. Saat fetch gagal, data lama tidak ditampilkan sebagai status live.
- AuthProvider memulai tracker hanya saat login; cleanup saat identitas berubah/logout/unmount. Route prototype `/dev/` tidak mengirim heartbeat. Tidak mengubah UI PWA atau workflow bisnis; web authenticated berbagi tracker.

## Batas

Browser dapat menangguhkan timer tab background/perangkat tidur. Online bukan bukti sedang bekerja; tab terbuka tanpa heartbeat yang segar bisa Offline. Penutupan/logout dikenali setelah timeout, tidak seketika. Waktu interaksi dikirim pada heartbeat berikutnya (hingga 30 detik normal). Tidak melakukan backfill aktivitas lama dari waktu login.

Pencatatan dimulai setelah kode diaktifkan dan user membuka web. Tidak ada migration, seed, database produksi atau deploy pada sesi ini. Full app build, browser+Mongo end-to-end dan CI belum dijalankan.

## Verifikasi

- Python: 18 tes lulus (identity, validation, idle, expiry boundary, multi-document monotonic merge, pagination/projection, permission/layout, HTTP ASGI real routes).
- Frontend: 39 tes lulus pada 5 suite terarah (tracker, widget, finance page/layout, shared board regression).
- Browser Chromium lokal, komponen aktual dengan fixture: 1366/1440, footer di dalam border, tanpa horizontal overflow/error JS, pagination dan trusted click heartbeat lulus. Tidak memakai API bisnis.
- Python syntax compile server/service dan diff whitespace check lulus.

Status katalog setelah batch: 25 Produksi (23 tersedia, 2 pending), 31 Keuangan (10 tersedia, 21 pending): **33 tersedia, 23 pending**. Catatan audit gabungan sebelumnya tetap bukti batch 32 widget.
