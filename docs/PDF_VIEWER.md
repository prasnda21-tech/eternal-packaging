# Viewer PDF di dalam aplikasi

Fitur yang menampilkan PDF kepada pengguna memakai viewer internal `pdfjs-dist` lewat komponen `frontend/src/components/InAppDocumentViewer.jsx`. Worker yang dipakai adalah `frontend/public/pdf.worker.min.mjs` (disalin dari `pdfjs-dist` 4.8.69). Jangan menambah library PDF lain.

Berlaku untuk bukti pembayaran di Approval Payment, Penerimaan Penjualan, dan Live Tracking, serta preview desain PDF di Live Tracking. Faktur pelanggan dan Surat Jalan mempertahankan viewer yang sudah berjalan.

## Aturan

- Buka PDF di modal pada halaman yang sama. Jangan memakai `window.open`, `target=_blank`, viewer bawaan browser, atau layanan viewer eksternal untuk aksi Buka/Lihat.
- JPG dan PNG memakai viewer gambar di komponen yang sama.
- Toolbar menyediakan Tutup, halaman sebelumnya/berikutnya, indikator halaman, zoom, Sesuaikan Lebar, dan Unduh terpisah bila unduhan diizinkan.
- Satu halaman membuat tombol navigasi nonaktif.
- Layar sempit (lebar maksimal 768px) memulai Sesuaikan Lebar. Toolbar tetap di luar area gulir dokumen.
- Loading, gagal muat dengan Coba lagi, akses ditolak, dan format tidak didukung ditampilkan sebagai keadaan yang berbeda.
- Mengganti dokumen tidak boleh menampilkan canvas dokumen sebelumnya. Menutup viewer membatalkan request/render dan melepaskan object URL serta dokumen PDF.
- Jangan menyimpan token atau isi dokumen di `localStorage`.
- Pemeriksaan akses desain dan kebijakan publikasi yang sudah ada tetap berlaku. Endpoint baca tidak menulis dokumen bisnis.

Fitur PDF berikutnya mengikuti standar ini. Jangan mengganti viewer di halaman yang berada di luar cakupan di atas hanya untuk menyamakan tampilan.
