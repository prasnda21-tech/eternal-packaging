# SOP Operasional Penjualan — Phase 4

Dokumen ringkas untuk Admin, CRM, Sales, dan Direktur.  
Hak akses mengikuti RBAC aktual; jangan mengasumsikan semua role punya izin yang sama.

## 1. Cara Buat PO (Pesanan Penjualan)

1. Buka **Penjualan → Pesanan Penjualan**.
2. Klik **Buat Pesanan**.
3. Isi pelanggan, outlet (opsional), nomor PO / Auto, tanggal PO, rencana kirim (DD/MM/YYYY).
4. Tambah barang, qty, dan harga jual.
5. Lengkapi pengiriman bila perlu, lalu **Simpan Pesanan**.
6. PO terbuka di detail `/po/{id}` untuk lanjut tahapan operasional.

## 2. Cara Buat Faktur dari PO

1. Buka **Penjualan → Faktur Penjualan** → **Buat Faktur**, atau dari detail PO bila tersedia.
2. Pilih PO. Sistem menampilkan sisa qty yang masih bisa difakturkan.
3. Isi tanggal faktur (DD/MM/YYYY), termin, diskon/PPN bila perlu.
4. Sesuaikan qty faktur per baris (parsial / multi-invoice diperbolehkan selama tidak melebihi sisa).
5. **Simpan Faktur**. Lifecycle otomatis (Phase 3) membuat/mem-posting sesuai capability backend.

## 3. Cara Edit dan Batalkan Faktur

### Edit
1. Buka detail faktur dari daftar (klik baris / Lihat).
2. Nilai awal diisi dari server; ubah tanggal, qty, harga, atau ketentuan.
3. **Simpan** — faktur finalized memakai jalur revisi/reversal yang sudah ada (bukan update langsung yang melewati lifecycle).
4. Perhatikan pesan sukses/error dan status jurnal.

### Batalkan (business cancel)
1. Tombol batalkan hanya muncul jika user punya permission cancel yang relevan **dan** dokumen eligible.
2. Konfirmasi menampilkan nomor faktur dan dampak pembatalan.
3. Cancel membatalkan bisnis + reversal sesuai Phase 3; **bukan** hard-delete jurnal/histori.

## 4. Cara Catat Penerimaan

1. Buka **Penjualan → Penerimaan Penjualan** → **Catat Penerimaan**, atau dari detail faktur (**Catat Penerimaan**) bila masih ada sisa tagihan.
2. Pilih faktur yang masih outstanding (AR live).
3. Isi jumlah diterima, alokasi, tanggal (DD/MM/YYYY), Kas/Bank, bukti bila wajib.
4. **Simpan Penerimaan**.
5. Cek status pembayaran faktur (Sudah Diterima / Sisa Tagihan AR) setelah simpan.

### Edit / Hapus Penerimaan
**DINONAKTIFKAN** pada Phase 3/4.  
Jika nominal salah: catat penerimaan koreksi baru atau eskalasi ke Owner/Admin keuangan. Jangan hard-delete.

## 5. Cara Memeriksa Piutang

1. Dari detail Faktur: lihat **Sudah Diterima (AR)**, **Sisa Tagihan (AR)**, **Status Pembayaran (AR)**.
2. Telusuri riwayat penerimaan di detail faktur (tautan ke nomor penerimaan).
3. Dari detail penerimaan: tautan ke faktur dan PO.
4. Menu Piutang Usaha / dashboard keuangan (bila role punya akses) untuk aging agregat.

## 6. Batas Hak Akses (ringkas)

| Aksi | Catatan |
|------|---------|
| Buat PO | `sales_order.create` / `po.edit` / admin |
| Buat Faktur | `sales.invoice.create` (+ fallback role yang sudah ada) |
| Edit Faktur | `sales.invoice.edit` (+ fallback); finalized → revisi lifecycle |
| Batalkan Faktur | Opt-in `sales.invoice.cancel` + eligible |
| Catat Penerimaan | `sales.receipt.create` (+ fallback) |
| Edit/Hapus Penerimaan | **UI OFF** (freeze Phase 4) |
| Post/Reverse jurnal | Permission accounting masing-masing dokumen |

CRM / Sales / Direktur: gunakan permission matrix di Settings; uji dengan akun role tersebut sebelum mengasumsikan akses.

## 7. Penanganan Kesalahan & Eskalasi

| Gejala | Tindakan pertama | Eskalasi |
|--------|------------------|----------|
| Validasi qty melebihi sisa | Turunkan qty faktur | Ops / CRM |
| Simpan faktur gagal financial | Catat pesan error + nomor dokumen | Admin keuangan |
| Penerimaan pending settlement | Jangan dihapus; pantau status AR | Admin keuangan |
| Faktur legacy warning | Pisahkan dari alur baru; jangan mass-post | Owner |
| Butuh koreksi penerimaan | Buat penerimaan baru / minta Owner | Owner |

**Eskalasi Owner** bila menyentuh data production, migrasi historis, atau perubahan lifecycle keuangan.
