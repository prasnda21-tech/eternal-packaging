# Audit gabungan dashboard produksi dan keuangan

Baseline: PR #698, `fe637a9a8b8772a1f7ad43b2c12ab4662c744618`.
Audit statis terhadap katalog, endpoint, service, pemetaan FE, renderer, izin, dan adapter rincian. Pengujian fixture dilaporkan terpisah; ini bukan verifikasi database produksi. Tidak ada migrasi, perubahan workflow, atau aktivasi widget tanpa kontrak sumber.

## Pembaruan penyelesaian setelah commit 0f9b00c

Instruksi Owner: tuntaskan visual dan angka yang belum selaras. Seluruh **32 widget tersedia** memakai tampilan kartu bersama: header, body yang dapat digulir bila padat, footer tetap di dalam border, format angka/satuan dan state akses. **24 widget pending tetap katalog nonaktif**, bukan widget operasional baru. Membangun pencatatan QC, jam mesin, proposal, atribusi cabang, atau histori sesi bukan bagian yang bisa digantikan dengan angka dummy.

Perbaikan kontrak lanjutan:

- **Kas:** HTTP rincian langsung memanggil `_cash_movement(..., include_rows=True)`, fungsi yang sama dengan KPI. Seluruh rekening BANK, jurnal legitimate, transfer internal dan reversal transfer dikecualikan. Pencarian diterapkan sebelum ringkasan/pagination; tanpa pencarian total kartu = total rincian. Saldo berjalan lintas rekening dihapus; rekening per baris ditampilkan. Tidak lagi memilih rekening pertama atau memakai cap mutasi 100.000. Rekening lama/inaktif tetap tercakup bila mempunyai akun BANK seperti kartu. Rincian satu rekening tetap tersedia pada modul Mutasi yang tidak diubah.
- **Histori produksi:** wave2 menerima semua PO non-batal dalam scope. PO selesai tetap masuk aging/ketepatan/target cohort; PO Aktif dan bottleneck tetap hanya pekerjaan aktif. Target yang telah dikunci tidak menghilang setelah pengiriman selesai. Tampilan menjelaskan cakupan aktif + selesai, bukan periode kalender palsu.
- **Sisa kirim:** Status PO memakai aturan qty fisik dan `qty_delivered`, terpisah dari sisa cetak. Unit tunggal dihitung langsung; unit campuran tanpa pengiriman atau sudah seluruhnya terkirim ditampilkan per satuan. Legacy pengiriman parsial campuran yang hanya mempunyai scalar total diberi keterangan eksplisit, tidak dibagi ke line secara asumsi. Qty tidak valid tidak ditampilkan nol.
- **Prioritas:** tanggal kirim efektif kini diteruskan dari board ke kartu; nomor line ditampilkan mulai 1.
- **Keuangan:** Laba kotor memakai `gross_profit` sumber; kartu kas diperbesar menampilkan saldo akhir rekening dari sumber yang sama, dengan penjelasan termasuk transfer internal. Beban menampilkan nominal dan persentase serta tetap memisahkan koreksi negatif. Footer semua kartu konsisten. Legenda donut tidak menyusut menimpa catatan.

Bukti terbaru: **62 tes Python lulus, 1 baseline deselected** (mengimpor server penuh); **88 tes frontend / 9 suite lulus**. Tes tambahan memakai builder keuangan sebenarnya dan route rincian yang diregistrasikan, bukan hanya mock return adapter. Browser fixture: 23 produksi + 9 keuangan tanpa error JS, seluruh footer di dalam border pada 1366/1440/CSS zoom 125%. Konten panjang memakai scroll internal; tidak dipotong. Bundle komponen terisolasi dan `git diff --check` lulus.

**Batas verifikasi:** belum full checkout/build aplikasi, belum menjalankan suite server dengan database, belum mencocokkan angka database operasional. Ini penyelesaian kode untuk widget bersumber yang diaudit, bukan klaim seluruh 56 widget aktif atau seluruh angka produksi telah direkonsiliasi. Tidak ada CI dispatch, merge, deploy, atau database write.

## Cakupan dan keputusan implementasi

Katalog dedicated: **25 produksi (23 tersedia, 2 pending)** dan **31 keuangan (9 tersedia, 22 pending)** = **56 ID**. Status tersedia berarti memiliki jalur implementasi, bukan semua pengguna memiliki izin atau semua data lengkap. Widget Dashboard lama memiliki delapan kartu keuangan tambahan/overlap yang dicatat di bawah; tidak dihitung sebagai ID dedicated baru.

Satu batch menerapkan pola visual kartu yang telah ditinjau, unit PO eksplisit, state data hilang yang tidak berubah menjadi nol, kuantitas lintas satuan tanpa perbandingan batang, grafik nol yang benar, dan koreksi adapter yang sumbernya terbukti. Layout tersimpan, mekanisme drag/resize/nudge, izin, route detail, dan prototype dev dipertahankan. Tidak menambahkan Sablon/QC sebagai tahap workflow.

## Matriks produksi — seluruh 23 widget tersedia

Sumber S = `production_dashboard_snapshot.build_production_snapshot` melalui `/dashboard/produksi/snapshot`; A = `production_analytics` melalui endpoint analytics; W = `production_wave2.build_wave2` di snapshot. P = `dashboard.production.view`; O = `dashboard.production.operator_analytics`; B = `dashboard.production.branch_output`. Scope order berasal dari `order_population_query`, cabang operasional dan sales user, non-cancelled. Izin O/B tidak disamakan dengan P.

| ID / judul | Sumber, grain, waktu | Visual & interaksi | Audit / keputusan |
|---|---|---|---|
| po_aktif · PO Aktif | S; PO unik belum fully delivered, snapshot; P | Angka + PO, caption, rincian Status PO | HOLD/Urgent/Buffer ikut; batal keluar. Selesai cetak belum tentu selesai kirim. Footer dan unit dibuat eksplisit. |
| perlu_produksi · PO Perlu Produksi | S + kepala board; PO unik dengan sisa cetak; P | Angka + PO; rincian per line | Field adapter tidak cocok dengan board asli; diperbaiki tanpa mengubah grain. |
| sisa_qty · Sisa Qty Produksi | S + aggregate_kepala_kpis; qty per unit; P | Daftar angka per unit; rincian line + ringkasan per unit | Batang membandingkan pcs/box tidak bermakna. Hilangkan perbandingan lintas unit; summary seluruh hasil filter sebelum pagination. |
| po_urgent · PO Urgent | S; subset aktif priority=urgent; P | Angka PO; rincian Status PO | Tidak memakai toggle urgent legacy. |
| po_hold · PO HOLD | S; PO aktif HOLD; P | Angka PO; rincian Status PO | Multi-line tidak menggandakan hitungan. |
| deadline · Deadline Pengiriman | S; PO ≤6 jam dan ≤24 jam; P | Dua panel link terpisah | ≤6 jam subset ≤24 jam. Tidak boleh dijumlahkan. |
| approval · Menunggu Approval | S; design/payment pending; P | Dua panel link | Satu PO dapat masuk keduanya; bukan dua populasi saling lepas. |
| prioritas · Prioritas Pekerjaan | S; delapan board rows teratas, total board; P | Tabel PO, line, produk, qty, label, deadline | Rincian tadinya hanya perlu-produksi; disamakan dengan seluruh board yang sudah terurut. |
| output_hari_ini · Output Cetak Hari Ini | A; increment konfirmasi sah hari WIB; P | Angka per unit + waktu konfirmasi | Legacy/tanpa tanggal tidak dihitung. Bukan qty_completed tambahan. |
| tren_output · Tren Output Produksi | A; konfirmasi harian 7/30 hari; P | Seri satu unit terpilih | Zero-fill hari tanpa output; tidak menggabungkan unit. |
| distribusi_tahap · Distribusi Tahap Workflow | A; satu PO pada tahap paling tertinggal; P | Batang tahap, scope produksi/semua | Per-line unavailable. Populasi analytics non-batal mencakup order selesai; bukan selalu sama dengan PO Aktif. |
| output_operator · Performa Operator | A; output operator per unit 7/30 hari; O | Ranking + target jika finalisasi ada | Target/cohort alokasi dan output kalender berbeda. Target bukan kapasitas mesin. |
| beban_operator · Beban Kerja Operator | A; sisa alokasi split per unit, unique PO/line; O | Daftar operator dan kuantitas | Identitas line hilang diberi catatan, bukan line palsu. Missing payload harus incomplete. |
| kesiapan · Kesiapan Produksi & Pengiriman | S; Design/Payment sah dan hasil cetak; P | Dua ringkasan terpisah | Kesiapan tidak otomatis memberi izin jadwal/kirim. |
| target_produksi · Pencapaian Target Produksi | W; target terkunci dan realisasi cohort; P | Target Awal/Revisi dan realisasi per unit | Belum finalisasi = unlocked, bukan nol. Populasi W kini mencakup PO aktif dan selesai non-batal dalam scope. |
| po_terlambat · PO Terlambat Kirim | po_late_tracking summary; PO aktif melewati kirim; P | Total PO + tingkat keterlambatan; route tracking | Buffer dan fully delivered dikecualikan. Bukan definisi terlambat produksi. |
| target_realisasi · Target vs Realisasi | W; cohort tanggal jadwal, target awal/revisi; P | Perbandingan per unit | Bukan output kalender. Populasi aktif dan selesai dijelaskan pada footer; target tetap mengikuti cohort jadwal. |
| aging · Aging Pekerjaan | W; umur produksi/umur PO; P | Kelompok umur dan status kelengkapan | Packing mengakhiri produksi; full kirim mengakhiri total. Input W mencakup PO selesai sehingga histori penyelesaian tetap dihitung. |
| ketepatan · Ketepatan Penyelesaian | W; selesai produksi vs finish target, kirim vs effective date; P | Dua metrik ketepatan | Target/selesai tidak diketahui bukan tepat waktu. Semua PO non-batal dalam scope user; bukan hanya PO aktif. |
| bottleneck · Bottleneck Workflow | W; lewat finish target, PO/line/per-unit; P | PO, line, pengiriman belum selesai | HOLD/pending approval tidak disalahkan sebagai bottleneck produksi. |
| komposisi · Komposisi Produk Diproduksi | A; identity/category snapshot konfirmasi; P | Ranking/kategori satu unit | Unverified/unmapped dan excluded historical qty tetap terpisah; master kini tidak mengubah histori. |
| output_cabang · Output per Cabang | A; branch snapshot aktual atau asal PO; B | Ranking satu unit, total scoped company | Belum Teratribusi tetap masuk total. Cabang asal bukan lokasi pengerjaan. |
| delivery_qty · Qty Rencana Pengiriman | delivery_plan; sisa rencana aktif hari ini/lewat dan 7/30 hari ke depan | Qty per unit + tren rencana; rincian filter unit | Izin salah satu delivery.schedule.view/schedule.view/nav.delivery_schedule. Bukan realisasi. Ringkasan rincian kini per bucket unit, termasuk campuran/tanpa satuan, tidak dijumlah lintas unit. |

Drill Status PO pada FE memerlukan `nav.reports.status_po` DAN `schedule.view`, tetapi endpoint baseline menerima salah satunya. Batch ini menegakkan kedua syarat pada endpoint. Drill kepala tetap memerlukan salah satu `nav.tasks.kepala_produksi`/`schedule.edit`. Kartu yang bisa dibaca tidak otomatis membuka daftar. Klik body/keyboard dan link memakai path yang sama. State loading/error/forbidden/incomplete tidak boleh menampilkan angka operasional palsu.

## Matriks keuangan — seluruh 9 widget tersedia

| ID / judul | Sumber & grain | Waktu / izin | Visual & hasil audit |
|---|---|---|---|
| cash · Arus Kas | financial_dashboard._cash_movement; journal lines akun BANK, transfer internal dikeluarkan | Periode; dashboard.finance.view | Kas masuk/keluar/neto. Rincian menggunakan builder jurnal yang sama, seluruh rekening, dan total sebelum pagination. Search mengubah subset secara eksplisit. |
| expense · Beban Perusahaan | financial_dashboard._performance/_composition, report_group expense | Periode; dashboard.finance.view; drill profit_loss.view/nav.ledger.profit_loss | Komposisi positif; negatif tetap negatif. Adapter rincian salah memakai pass, memasukkan grup lain; diperbaiki. Summary nominal beban hasil filter. |
| profit · Laba/Rugi Tahun Ini | widget_profit_loss/get_profit_loss; akun/jurnal sah | Periode + YTD terpisah; profit_loss.view | Komponen laba rugi dan net profit. Rincian preserve nol serta kelompok report_group. Tidak memasukkan asumsi HPP prototype. |
| purchases · Pembelian | purchase_period_summary; invoice_date, active grand_total | Periode; purchase_invoices.view | Total dan grand total faktur lunas/belum lunas. DP belum lunas; bukan remaining debt/cash payment. Adapter total tidak boleh memakai fallback ketika grand_total=0. |
| recon · Rekonsiliasi Bank | widget_dashboard_http; active pairs dan legacy docs | Snapshot; cash_bank.reconcile | Dua daftar terpisah. Batas baca 2.000 legacy/pairs ditandai incomplete. Finalized tidak berarti selisih nol. |
| sales · Penjualan | sales_dashboard; finalized non-cancelled after_discount | Periode & scope branch/sales; dashboard.sales.view | Angka, perbandingan periode, spark. Zero bar tidak dibuat positif. |
| products · Penjualan Barang | sales_dashboard._build_products; line_subtotal mapped identity | Periode; dashboard.sales.view | Ranking barang; jumlah ranking bukan omzet after_discount. Unmapped dan partial tetap ditandai. |
| sellers · Performa Penjual | sales_dashboard._build_sales; omzet per identitas sales | Periode; dashboard.sales.view | Ranking, Belum ditetapkan tetap terlihat. Tidak membuat drill per-sales yang API-nya belum mendukung. |
| trend · Tren Penjualan | sales_dashboard._build_omzet_and_trend; grain ditentukan service | Periode; dashboard.sales.view | Semua bucket ditampilkan/di-scroll, bukan diam-diam 31 terakhir. Nol tidak memiliki tinggi batang. Detail mengikuti rentang kartu. |

Layout finance menyimpan period_mode per widget. Global range tidak boleh menimpa custom range. Request stale dibatasi request ID. Akses detail berbeda dengan akses KPI; state forbidden tidak sama dengan empty. Kartu keuangan negatif dan data tidak lengkap tetap memiliki arti asli.

## Seluruh 24 widget pending

Tetap di katalog sebagai belum tersedia; tidak dibuat angka, chart, drill, atau metrik baru dalam batch ini.

| Dashboard | ID | Judul | Sumber/kontrak yang belum selesai |
|---|---|---|---|
| Produksi | qc_reject | QC, Reject & Cetak Ulang | Pencatatan sah QC/reject/reprint |
| Produksi | utilisasi_mesin | Utilisasi Mesin | Jam kerja mesin dan denominator kapasitas |
| Keuangan | assets | Aset saat ini | Katalog dedicated belum punya adapter; WidgetDashboard terpisah sudah punya total_assets, bukan otomatis aset lancar |
| Keuangan | overdue_invoices | Faktur Terlewat | Adapter faktur piutang akuntansi |
| Keuangan | cash_available | Ketersediaan Kas | Saldo saat ini terpisah dari arus periode |
| Keuangan | branch_profit | Laba Cabang | Atribusi biaya cabang |
| Keuangan | customers_owing | Pelanggan Berutang | Kontrak agregat/status; jangan menyamakan dengan pelanggan menunggak di halaman lain |
| Keuangan | quote_win | Keberhasilan Penawaran | Dokumen penawaran dan denominator |
| Keuangan | quote_stale | Penawaran Terabaikan | Dokumen penawaran/status stale |
| Keuangan | sales_category | Penjualan Per Kategori | Kontrak kategori historis |
| Keuangan | sales_brand | Penjualan Per Merek | Atribut merek |
| Keuangan | sales_daily | Penjualan per Hari | Kontrak dedicated lintas rentang; trend existing sudah memilih grain |
| Keuangan | customer_growth | Pertumbuhan Pelanggan | Seri historis terverifikasi |
| Keuangan | sales_orders | Pesanan Penjualan | Kontrak status pesanan, bukan pendapatan |
| Keuangan | min_stock | Barang Stok Minimum | Ambang minimum per barang |
| Keuangan | hpp | Monitoring HPP | Pilihan sumber dan scope HPP |
| Keuangan | item_requests | Permintaan Barang | Agregasi daftar dan batas baca |
| Keuangan | purchase_orders | Pesanan Pembelian | Adapter widget PO pembelian |
| Keuangan | my_activity | Aktivitas Terakhir Anda | Jejak sesi |
| Keuangan | upcoming | Kegiatan Mendatang | Kontrak follow-up |
| Keuangan | missed | Kegiatan Terlewat | Kontrak follow-up overdue |
| Keuangan | active_users | Pengguna Aktif | Definisi sesi aktif |
| Keuangan | approvals | Persetujuan | Antrean terpisah dari tugas produksi |
| Keuangan | approvals_done | Persetujuan Terproses | Riwayat persetujuan selesai |

## Halaman WidgetDashboard lain — delapan kartu keuangan

`widgetDashboardCatalog.js` mendefinisikan F1 purchases (invoice grand_total periode), F2 utang/AP akuntansi (saldo sekarang), F3 cash (arus periode), F4 expense, F5 profit, F6 assets (summary.total_assets neraca sekarang), F7 overdue (saldo piutang positif lewat jatuh tempo menurut customer_key), F8 recon. ID/arti di halaman ini tidak disamakan dengan pending katalog dedicated. Shared model/adapter harus tetap kompatibel. Batch sales/settlement/date di PR tetap dipertahankan.

## Temuan audit awal dan status lanjutannya

1. **Cash total vs drill**: card membaca legitimate BANK journal lines lintas akun; mutasi membaca satu rekening dengan batas 100.000 dan aturan status berbeda. **Ditutup pada pembaruan:** rincian sekarang menggunakan builder kas kartu; mutasi rekening terpisah tidak diubah.
2. **Wave2 aktif vs histori**: snapshot memberi `active` ke build_wave2. **Ditutup pada pembaruan yang diotorisasi Owner:** seluruh PO non-batal digunakan untuk histori/cohort; backlog tetap aktif.
3. **Sisa kirim per PO**: status_po_row tidak memasok qty_remaining. Tidak mengarang qty lintas line/unit atau menamai sisa cetak sebagai sisa kirim. Deadline dapat diambil dari raw order yang sudah di-enrich. **Diperbaiki:** sisa kirim dihitung dengan aturan kuantitas resmi; ambiguitas legacy partial lintas unit tetap dijelaskan.
4. **Soft delete**: jalur snapshot membuktikan filter cancelled, belum membuktikan semantik deleted lain.
5. **Unit legacy**: kepala board default pcs dan integer; analytics memakai normalisasi/conversion snapshot. Tidak mengubah histori/konversi dalam batch visual.
6. **Operator targets**: cohort alokasi seluruh snapshot dan output kalender 7/30 hari berbeda; jangan mengambil persentase output kalender dibagi target cohort.
7. **Populasi workflow**: analytics menerima non-cancelled termasuk fully delivered; label seluruh workflow bukan sinonim PO aktif.
8. **Read limits**: rekonsiliasi sudah jujur truncated; modul mutasi lama 100.000 belum mempunyai sinyal truncation; rincian dashboard kas kini tidak memakai modul/cap itu. Tidak ada klaim skala produksi dari fixture kecil.

## Bukti kode utama

- Katalog/layout FE: `frontend/src/components/{productionDashboard,financeDashboard}/*Catalog.js`, `*Layout.js`; layout backend `production_widget_layout.py`, `finance_widget_layout.py`.
- Pemetaan/request: `frontend/src/pages/ProductionDashboard.jsx`, `FinanceDashboard.jsx`, `frontend/src/lib/widgetDashboardModel.js`.
- Renderer/interaksi: `ProductionWidgetBoard.jsx`, `FinanceWidgetBoard.jsx`, `dashboardDrillNav.js`, `DashboardRincianPage.jsx`, `dashboardRincianPresentation.js`.
- Produksi: `production_dashboard_http.py`, `production_dashboard_snapshot.py`, `kepala_produksi_board.py`, `division_tasks.py`, `quantity_rules.py`, `production_analytics.py`, `production_wave2.py`, `delivery_plan.py`, `po_late_tracking.py`.
- Keuangan: `financial_dashboard.py`, `profit_loss.py`, `widget_profit_loss.py`, `purchase_period_summary.py`, `widget_dashboard_http.py`, `sales_dashboard.py`, `cash_bank_mutasi.py`.
- Rincian/izin/scope: `dashboard_rincian.py`, `dashboard_rincian_http.py`, `status_po.py`.

## Batch implementasi

- Empat kartu hitungan PO memakai unit PO, meta snapshot, dan footer link/akses terbatas di dalam kartu. Semua tone produksi memakai kartu putih dengan aksen atas; layout tersimpan tidak direset.
- Sisa qty berupa daftar unit, bukan batang yang membandingkan unit berbeda. Rincian sisa/delivery menyajikan ringkasan per unit sebelum pagination.
- Kepala adapter memakai order_number, item, qty_production_remaining, effective_delivery_date, priority_display. Baris line memiliki id order:line_idx; order_id/link detail tetap PO. Prioritas memakai board lengkap, bukan subset perlu-produksi.
- Status PO mengambil deadline dari raw order yang sudah di-enrich; qty tidak dikarang ketika status_po_row tidak menyediakannya.
- Missing/null snapshot menjadi incomplete, bukan zero; workflow/workload tanpa status juga incomplete. Label cakupan wave2 menjelaskan PO aktif.
- Rincian Beban menyaring report_group expense, mempertahankan negatif/nol, dan menghitung nominal seluruh hasil filter. Rincian profit menampilkan net profit periode dengan catatan cakupan pencarian.
- Pembelian mempertahankan grand_total=0; amount tidak valid tidak menjadi 0. Mutasi memakai journal_line_id/transaction_number dan mempertahankan running_balance=0; rekening serta perbedaan dengan kartu kas diperlihatkan.
- Izin detail sales/purchases/profit menerima izin KPI dedicated masing-masing tanpa menghilangkan syarat dataset. Status PO memerlukan kedua izin seperti FE.
- Grafik finance menampilkan seluruh bucket, nol tanpa tinggi minimum, serta nilai negatif di bawah baseline. Ranking produksi tidak membuat nol sepanjang 4%.
- Donut beban diberi label Beban positif dan semua legenda; negatif tetap terpisah. Lihat Semua rekonsiliasi membuka seluruh baris yang dikembalikan service, tidak berhenti di 12.
- Tombol/input di dalam body drill tidak memicu navigasi parent ketika Enter/Space ditekan. Menu, resize, drag, nudge, dan layout saving tetap diuji.

## Verifikasi batch awal (sebelum pembaruan penyelesaian)

| Pemeriksaan | Hasil dan batas |
|---|---|
| Python focused | **48 passed, 1 deselected**. `test_dashboard_combined_contracts.py`, `test_dashboard_rincian.py`, `test_kepala_produksi_board.py`; satu tes yang mengimpor server penuh tidak tersedia pada mirror parsial dan dikecualikan secara eksplisit. |
| Frontend focused | **62 passed / 8 suites**: halaman Produksi/Keuangan, combined contracts, layout keduanya, format produksi, presentation rincian, drill navigation. Jest 29 + React 19 pada harness lokal; bukan klaim `yarn build`. |
| Browser fixture | 23 widget produksi menggunakan page/renderer asli dan output builder Python lokal; 9 widget keuangan memakai renderer asli dengan model fixture. Tidak ada error JavaScript. |
| Ukuran | 1366, 1440, dan CSS zoom 125%. Empat count card berukuran 132 CSS px; footer di dalam border dan body tidak perlu scroll untuk fixture ini. Ini tidak menggantikan pengujian shell ERP penuh atau zoom browser native. |
| Chart | Delapan hari bernilai nol pada fixture finance memiliki tinggi 0%; test 40 bucket memastikan tidak dipotong menjadi 31. |
| Bundle terisolasi | esbuild sukses untuk komponen/page terkait; auth/API di-stub pada harness review. Tidak ada API bisnis dipanggil. |
| Whitespace | `git diff --check` bersih. |

Perintah backend yang dijalankan:

```sh
PYTHONPATH=backend python -m pytest -q backend/tests/test_dashboard_combined_contracts.py backend/tests/test_dashboard_rincian.py backend/tests/test_kepala_produksi_board.py -k 'not global_deadline_helper_untouched_by_kp_classifier'
```

Validasi sebelum merge tetap memerlukan checkout lengkap, full frontend build, dan tes server/database dummy. Clone terminal tidak terautentikasi pada sesi ini; sumber dibaca melalui connector pada SHA baseline. Tidak ada CI manual, pemeriksaan database produksi, merge, atau deploy. Commit batch menggunakan `[skip ci]` dan PR tetap draft.
