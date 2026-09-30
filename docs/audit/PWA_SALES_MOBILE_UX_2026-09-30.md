# PWA Sales — audit UX mobile (2026-09-30)

Lingkup: daftar PO (`/sales/po`) saja. Workflow bisnis, kontrak API, database, finance, purchasing, dan production tidak diubah.

## Masalah UI sekarang

1. Filter PO hidup di `useState` pada `SalesPwaOrdersPage`. Halaman dilepas dari DOM saat membuka detail (`/sales/po/:id`) atau viewer dokumen. Saat kembali, search, status, tahap, tanggal, buffer, dan progres kembali kosong.
2. Posisi scroll tidak disimpan. Kembali dari detail selalu ke atas daftar.
3. Daftar memakai tabel 5 kolom, termasuk di layar sempit. Di bawah 430px font tabel mengecil menjadi 10,5px. Filter kolom (pelanggan, tahap, dua tanggal, progres, buffer) menempel di header dan memenuhi layar.
4. Chip status sudah satu baris dan bisa digeser, tetapi `flex-wrap` tidak dinyatakan, jadi mudah terbungkus jika layout induk berubah. Angka chip mengikuti filter aktif hanya selama halaman tidak di-reset.
5. Pencarian mengirim permintaan pada setiap ketukan, tanpa tombol hapus, dan tanpa status loading khusus di kotak cari.
6. Saat filter berganti, daftar lama disembunyikan dan diganti teks "Memuat...". Tidak ada skeleton.

## Rekomendasi

- Simpan filter di `sessionStorage` kunci `sales_pwa_po_filter` dan scroll di `sales_pwa_po_scroll` selama sesi tab. Logout menghapus kedua kunci.
- Simpan `scrollY` jendela bersama filter. Pulihkan setelah daftar yang sesuai filter tampil. Jangan menimpa posisi itu saat unmount, karena navigasi sering sudah mengembalikan scroll ke 0.
- Layar `max-width: 767px`: kartu (nama customer, nomor PO, tahap, tanggal, progres, jumlah dokumen, buffer, tombol Detail). Layar lebih lebar tetap memakai tabel desktop beserta filter kolom.
- Tombol "Filter" membuka bottom sheet untuk tahap, tanggal, buffer, dan progres. Chip status dan kotak cari tetap di luar sheet.
- Chip: `flex-wrap: nowrap` dan scroll horizontal. Angka tetap dari `counts` respons daftar yang sudah difilter.
- Cari: debounce 300ms, tombol hapus, dan skeleton segera setelah teks atau filter berubah — data lama tidak tetap tampil.
- Permintaan API tetap `q`, `status`, `stage`, `date_from`, `date_to`, `progress_min`, `buffer`, `sort`, `direction`, `limit: 120`. `customer_q` dan `po_q` tidak dikirim.

## File yang diubah

- `frontend/src/lib/salesPwaPoListState.js` — baca/tulis/hapus session state
- `frontend/src/lib/salesPwaPoListState.test.js`
- `frontend/src/sales/SalesPwaOrdersPage.jsx`
- `frontend/src/sales/SalesPwaOrdersPage.test.js`
- `frontend/src/sales/salesPwa.css`
- `frontend/src/lib/auth.jsx` — hapus state daftar PO pada logout

Tidak diubah: `backend/services/sales_pwa.py`, detail PO, dokumen, dashboard, visit, prediksi, finance, purchasing, production, skema database.
