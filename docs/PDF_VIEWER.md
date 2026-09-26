# Viewer PDF di dalam aplikasi

Fitur yang menampilkan PDF kepada pengguna memakai viewer internal `pdfjs-dist` lewat komponen `frontend/src/components/InAppDocumentViewer.jsx`. Worker yang dipakai adalah `frontend/public/pdf.worker.min.mjs` (disalin dari `pdfjs-dist` 4.8.69). Jangan menambah library PDF lain.

Berlaku untuk bukti pembayaran di Approval Payment, Penerimaan Penjualan, dan Live Tracking, preview desain PDF di Live Tracking, serta aksi Lihat PDF Surat Jalan di Live Tracking. Unduh Surat Jalan tetap aksi terpisah dengan izin akses yang sudah ada. Faktur pelanggan mempertahankan viewer yang sudah berjalan.

Pola tampilannya mengikuti Surat Jalan di PWA (`MobileSuratJalanViewerPage` dan `SalesPwaPdfViewer`): semua halaman tersusun ke bawah, lebar mengikuti ruang yang ada, dan header tidak ikut tergulung.

## Aturan

- Buka PDF di modal pada halaman yang sama. Jangan memakai `window.open`, `target=_blank`, viewer bawaan browser, atau layanan viewer eksternal untuk aksi Buka/Lihat.
- Render semua halaman PDF berurutan ke bawah di dalam area yang bergulir. Jangan memakai tombol Sebelumnya, Berikutnya, Zoom, Sesuaikan Lebar, atau indikator Halaman X/Y di toolbar.
- Lebar setiap halaman mengikuti lebar area viewer dan tidak melebihi 920px, supaya tetap nyaman dibaca di desktop dan tidak membuat scroll horizontal.
- Header tetap di luar area gulir. Isinya hanya judul, Unduh bila diizinkan, dan Tutup.
- JPG dan PNG memakai viewer gambar di komponen yang sama, juga di dalam area gulir itu.
- Loading, gagal muat dengan Coba lagi, akses ditolak, dan format tidak didukung ditampilkan sebagai keadaan yang berbeda.
- Mengganti atau menutup dokumen membatalkan request/render, melepaskan object URL, dan tidak meninggalkan halaman dokumen sebelumnya.
- Jangan menyimpan token atau isi dokumen di `localStorage`.
- Pemeriksaan akses desain dan kebijakan publikasi yang sudah ada tetap berlaku. Endpoint baca tidak menulis dokumen bisnis.

Fitur PDF berikutnya yang masuk cakupan ini mengikuti standar ini. Jangan mengganti viewer di halaman yang berada di luar cakupan di atas hanya untuk menyamakan tampilan.
