# PWA Sales — lanjutan audit (2026-09-30)

Laporan ini melanjutkan `docs/audit/PWA_SALES_MOBILE_UX_2026-09-30.md`. Berkas dengan nama ini belum ada di `main`. Audit tidak diulang untuk seluruh aplikasi. Lingkup lanjutan: link approval desain pada kartu lapangan dan kartu PO PWA Sales.

## Perbandingan dengan production

Diperiksa read-only pada `https://produksi.eternalpackaging.id` (GET). Indeks terakhir diubah server pada 2026-10-02 02:00:20 GMT. Bundle `static/js/main.597995bb.js`. Kartu lapangan ada di chunk `static/js/2353.b47158ce.chunk.js`.

Chunk itu memuat kartu LAPANGAN / Butuh aksi, teks "Lihat PO Approval Design", dan "Prioritas hari ini". Tidak ada "Buka Approval Desain", "Link approval belum tersedia", atau field `design_approvals`.

SHA commit yang sedang berjalan di production tidak tertulis di bundle, jadi commit production tidak bisa dipastikan sama dengan `main` lokal (`efe9628`). Perilaku kartu lapangan di bundle itu sama dengan `main` sebelum revisi ini: link approval tidak ditampilkan.

Tidak ada deploy, merge, atau penulisan ke data production.

## Alur yang ditelusuri

1. Pembuatan link: `POST /api/orders/{id}/customer-approval/generate` di `backend/customer_approval.py`. Token plaintext (`secrets.token_urlsafe`) dikembalikan sekali. Yang disimpan di `customer_approvals` hanya `token_hash`. Snapshot `order.customer_approval` tidak berisi URL atau token.
2. Relasi: satu record approval terikat `order_id` dan `design_version` (id file di `order.design_files`). Generate baru mencabut link aktif sebelumnya. File yang dibuka customer adalah file yang terikat, lewat `bound_design_file`.
3. Pembuka resmi yang tetap ada setelah refresh: token live tracking pada order. `public_design_approval` menyusun `/customer/design-approval/{tracking_token}`. `_load_by_token` memakai token itu untuk membuka approval yang ditunjuk snapshot, tanpa membuat token approval baru.
4. API PWA Sales: `serialize_list_row` di `backend/services/sales_pwa.py` memproyeksikan `customer_approval` lalu membuangnya. Respons daftar/detail tidak punya URL.
5. Kartu: bagian LAPANGAN di `SalesPwaDashboardPage.jsx` hanya merender tautan "Lihat PO Approval Design". Kartu PO di `SalesPwaOrdersPage.jsx` juga tidak membaca approval.

## Penyebab

Kartu lapangan tidak menerima URL karena serializer PWA Sales menjatuhkan snapshot approval, dan URL plaintext memang tidak disimpan. Tidak ada field URL lain pada snapshot. Mengarang path dari id order, atau membuat token approval baru, akan mencabut link yang sudah dikirim ke customer.

## Revisi

PWA Sales, untuk order milik `sales_user_id` sesi, mengembalikan `design_approvals`:

- status dari record `customer_approvals` (atau snapshot bila record belum terbaca), dengan perhitungan kedaluwarsa yang sama seperti halaman publik
- nama file dari `design_version` yang terikat, bukan file desain lain
- `path` hanya untuk approval snapshot yang masih `active`, memakai `public_design_approval`
- bila approval aktif dan order belum punya `tracking_token`, token live tracking yang sudah ada dibuat lewat `ensure_tracking_token`. Ini tidak menulis token approval dan tidak mencabut link yang sudah dibuat
- versi desain lain pada order yang sama tetap tampil dengan statusnya sendiri dan tanpa path itu

Kartu lapangan, kartu PO (HP), baris tabel PO, dan detail PO menampilkan "Buka Approval Desain" hanya bila path lolos pemeriksaan `approvalActionPath`. Tanpa path, teksnya "Link approval belum tersedia" dan tidak ada tombol. Order di luar tahap Approval Design yang tidak punya record approval tidak menambah baris kosong. Data dimuat ulang lewat `useEffect` yang sudah ada saat halaman dipasang kembali.

Role `sales_eternal` tetap hanya lewat `/api/sales-pwa`. Sales lain mendapat 404 untuk order orang lain dan tidak menerima path-nya.

## File

- `backend/services/sales_pwa.py`
- `backend/tests/test_sales_pwa_design_approval.py`
- `frontend/src/sales/SalesPwaDesignApproval.jsx`
- `frontend/src/sales/SalesPwaDashboardPage.jsx`
- `frontend/src/sales/SalesPwaOrdersPage.jsx`
- `frontend/src/sales/SalesPwaOrderDetailPage.jsx`
- `frontend/src/sales/salesPwa.css`
- tes frontend dashboard, daftar PO, dan detail PO

## Tes

- `backend/tests/test_sales_pwa_design_approval.py`: 3 lulus. Generate lewat endpoint aplikasi, path muncul pada PO yang benar, halaman publik membuka file desain yang terikat, desain lama tidak memakai path yang sama, order tanpa link tidak punya path, muat ulang mengembalikan path yang sama, sales lain tidak melihat order atau path. Approval kedaluwarsa tidak dibuatkan tracking token.
- `tests/test_sales_pwa_scope.py`, `test_sales_pwa_list_filters.py`, `test_status_po_dispatch_contract.py`, `test_buffer_stock_late_exclusion.py`: 41 lulus.
- Jest `SalesPwaDashboardPage`, `SalesPwaOrdersPage`, `SalesPwaOrderDetailPage`: 29 lulus.

## Temuan audit UX 30 Sep yang masih terbuka

Rekomendasi di `PWA_SALES_MOBILE_UX_2026-09-30.md` sudah ada di kode `main` saat lanjutan ini dimulai: filter dan scroll di session, kartu di layar sempit, bottom sheet filter, chip satu baris, debounce pencarian, dan skeleton. Tidak ada butir dari daftar itu yang masih belum dikerjakan.

Di luar laporan UX itu, yang masih berlaku setelah lanjutan batas 4 PO:

- URL plaintext dari tombol generate tetap tidak bisa ditampilkan ulang. Kartu memakai pembuka live tracking. Keduanya membuka approval yang sama; string token generate tidak dikembalikan ke PWA. Mekanisme generate tidak diubah.
- SHA persis bundle production tetap tidak bisa dicocokkan ke commit git. Lihat bagian lanjutan di bawah.

## Lanjutan: batas 4 PO per tahap (2026-10-02)

### Sinkronisasi source

`origin/main` saat digabung adalah `12477ea` (Merge PR #440, compact tahap 1–5 di detail PO). Perubahan itu hanya `App.css`, `StageBar.jsx`, `PODetail.jsx`, dan tes delivery detail. Digabung ke branch ini tanpa menghapus `7f3ba00`.

### Production, read-only

GET `https://produksi.eternalpackaging.id` pada 2026-10-02:

- Indeks `Last-Modified: Fri, 02 Oct 2026 04:04:43 GMT`. Bundle utama sekarang `/static/js/main.b8332a37.js` (sebelumnya `main.597995bb.js`, indeks 02:00:20 GMT).
- Chunk lapangan tetap `static/js/2353.b47158ce.chunk.js`. Isinya masih "LAPANGAN", "Butuh aksi", "Lihat PO Approval Design", dan `limit:4`. Tidak ada "Buka Approval Desain", "Link approval belum tersedia", atau `design_approvals`.
- "Lihat semua" yang ada di bundle production berada di chunk lain (`3381.faf46bfd.chunk.js`) untuk kartu kerja hari ini, bukan daftar lapangan PWA Sales.
- Source map `main.b8332a37.js.map` memuat `file: static/js/main.866a3970.js`, tidak sama dengan nama file yang dilayani. Tidak ada SHA git di map atau di chunk.
- API deployment GitHub menolak akses (403).

SHA yang sedang berjalan di production tidak terbukti. Kecocokan teks chunk tidak dipakai sebagai bukti bahwa bundle production sama dengan `main`.

Tidak ada deploy, merge, atau penulisan ke data production.

### Penyebab batas 4 PO

Batas ada di frontend, di `SalesPwaDashboardPage.jsx`: kedua permintaan tahap memakai `limit: 4`. API `list_sales_pwa_orders` sudah menerima `skip`/`limit` (maksimum 200) dan `total` adalah jumlah seluruh order milik sales yang lolos filter, bukan 4.

Daftar PO (`SalesPwaOrdersPage.jsx`) meminta `limit: 120` sekali. Jika `total` lebih besar, teks sisa tampil, tetapi tidak ada halaman berikutnya. Pencarian dan filter sudah dikirim ke API, jadi berlaku untuk seluruh order milik sales, tetapi order di luar respons pertama tidak bisa dibuka.

Tahap dihitung di Python (`effective_workflow_status`). HOLD tidak masuk jarum "Approval Design" / "Approval Payment". Tidak ada batas 4 di API.

### Perubahan

- Kartu lapangan tetap menampilkan 4 PO pertama per tahap. Di atas kartu ada jumlah total tahap itu untuk sales yang login, misalnya "45 PO Approval Design". Bila total lebih dari yang tampil, "Lihat semua" membuka `/sales/po` dengan `salesPwaPoContext.stage` persis "Approval Design" atau "Approval Payment", status `semua`, dan filter sesi sebelumnya diganti.
- Daftar PO memuat 40 baris, lalu "Muat lagi" meminta `skip` halaman server berikutnya. Baris digabung menurut id. `total` dan chip tetap dari respons terfilter, bukan dari 4 kartu. Halaman berikutnya memakai pencarian dan tahap yang sama.
- Pengiriman, faktur, dan lampiran approval hanya dimuat untuk halaman yang diminta. Urutan sama dengan sortir baris yang sudah diserialkan. Seluruh order milik sales tetap dipindai untuk menentukan tahap, karena tahap tidak tersimpan sebagai filter database.
- Alur approval desain dari `7f3ba00` tidak diubah: path hanya untuk approval aktif yang terikat, tidak ada token approval baru, dan order tanpa path tetap "Link approval belum tersedia".

### Tes lanjutan

- `backend/tests/test_sales_pwa_stage_page.py`: 6 PO Approval Design milik satu sales, `limit` 4 lalu `skip` 4, total 6, tidak ada id ganda atau yang terlewat, pencarian menemukan PO di luar 4 pertama, sales lain tidak melihat PO itu. Dokumen hanya dimuat untuk id pada halaman.
- `test_sales_pwa_design_approval.py` bersama regresi scope, filter daftar, status PO, dan buffer stock: 46 lulus, termasuk 2 tes halaman tahap.
- Jest dashboard, daftar PO, state filter, dan detail: lulus, termasuk "Lihat semua" membawa tahap yang sama dan "Muat lagi" tidak menduplikasi id. Ekspektasi navigasi di `src/lib/salesPwa.test.js` kemudian disesuaikan dengan bar yang berlaku (Dashboard, PO Saya, Visit, Lainnya). Navigasi tidak diubah.
- `npm run build` dengan `CI=false` dan `DISABLE_ESLINT_PLUGIN=true`, sama seperti workflow frontend, selesai tanpa error.
- Pratinjau lokal `http://localhost:3002` dimuat ulang. Di lebar 390px: total tahap, 4 kartu, "Lihat semua", daftar dengan filter tahap yang sama, lalu "Muat lagi" sampai 45 kartu unik. Sales kedua tidak menerima PO sales pertama.

## Lanjutan tampilan (2026-10-02)

Bukan audit ulang. Hanya catatan pada daftar PO, kartu lapangan, dan navigasi.

- Daftar PO menampilkan `Tahap: …` selama filter tahap aktif. Menghapus tahap meminta ulang dari `skip` 0 dan memakai total respons baru. Angka pada tombol Filter tidak lagi menghitung tahap itu.
- Kartu lapangan memakai "Lihat PO". Status yang sama dengan judul tahap tidak diulang di dalam kartu. Bila path approval ada, "Buka Approval Desain" menjadi aksi utama dan "Lihat PO" menjadi aksi sekunder.
- `src/lib/salesPwa.test.js` mengharapkan bar lama (PO Saya, Pelanggan, Prediksi Order, Profil) dan menganggap `/sales` sebagai daftar PO. Bar yang berlaku, dan yang sudah diuji `SalesPwaLayout.test.js`, adalah Dashboard, PO Saya, Visit, dan Lainnya. Prediksi Order ada di menu Lainnya. Ekspektasi tes disesuaikan. Isi `SALES_PWA_NAV` tidak diubah.
- "Muat lagi" dikunci selama permintaan berjalan. Gagal memuat mempertahankan baris yang sudah ada dan mengganti tombol menjadi "Coba lagi". Setelah semua baris termuat, teksnya `{jumlah} dari {total} PO` dan tombol disembunyikan. Pergantian filter menaikkan generasi permintaan sehingga respons halaman lama diabaikan.
