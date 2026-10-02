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

Di luar laporan UX itu, yang masih berlaku:

- URL plaintext dari tombol generate tetap tidak bisa ditampilkan ulang. Kartu memakai pembuka live tracking. Keduanya membuka approval yang sama; string token generate tidak dikembalikan ke PWA.
- Daftar aksi lapangan tetap mengambil paling banyak 4 PO per tahap (`limit: 4` di dashboard). Bukan bagian revisi ini.
- SHA persis bundle production tidak bisa dicocokkan ke commit git.
