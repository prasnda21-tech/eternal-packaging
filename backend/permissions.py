"""Permission model for Eternal Packaging.

Concepts:
- Role = job function (admin, head_production, head_operations, designer, screener,
  helper, operator, driver, crm).
- Permission = granular capability key like `helper.prepare` or `packing.update`.
- Role defaults = the permission list a role starts with.
- Per-user override = optional dict `{permission_key: bool}` that adds/removes
  permissions on top of the role defaults.

A user's effective permission set is: role_defaults ∪ override(true) − override(false)
"""

# All permissions the app knows about, grouped by module for the Hak Akses matrix.
PERMISSION_CATALOG = [
    # module, permission_key, label
    ("PO", "po.view", "Lihat PO"),
    ("PO", "po.edit", "Ubah PO"),
    ("PO", "po.cancel", "Batalkan PO"),
    ("PO", "po.delete", "Hapus permanen PO"),
    ("Penjualan", "product_request.create", "Buat Permintaan Produk"),
    ("Penjualan", "product_request.read_own_draft", "Baca draft Permintaan Produk sendiri"),
    ("Penjualan", "product_request.read_all", "Baca semua Permintaan Produk terkirim"),
    ("Penjualan", "product_request.feedback", "Beri feedback Permintaan Produk"),
    ("Penjualan", "product_request.update_status", "Ubah status Permintaan Produk"),
    ("Penjualan", "crm.daily_report.create", "Buat Laporan Harian CRM"),
    ("Penjualan", "crm.daily_report.view_own", "Lihat Laporan Harian CRM sendiri"),
    ("Penjualan", "crm.daily_report.edit_own", "Edit Laporan Harian CRM sendiri (same-day)"),
    ("Penjualan", "crm.daily_report.view_all", "Lihat semua Laporan Harian CRM"),
    ("Penjualan", "sales.customer.view", "Lihat Data Pelanggan"),
    ("Penjualan", "sales.customer.manage", "Kelola Data Pelanggan"),
    ("Penjualan", "sales.customer.edit", "Edit Pelanggan"),
    ("Penjualan", "sales.customer.manage_status", "Kelola Status Pelanggan"),
    ("Penjualan", "pra_po.create", "Buat Pra PO"),
    ("Penjualan", "pra_po.edit_own", "Ubah Pra PO milik sendiri"),
    ("Penjualan", "pra_po.view_own", "Lihat Pra PO milik sendiri"),
    ("Penjualan", "pra_po.view_all", "Lihat semua Pra PO"),
    ("Penjualan", "pra_po.submit", "Ajukan Pra PO"),
    ("Penjualan", "pra_po.verify", "Setujui Pra PO menjadi Pesanan Penjualan"),
    ("Penjualan", "pra_po.return", "Kembalikan Pra PO ke CRM"),
    ("Penjualan", "pra_po.audit.view", "Lihat audit Pra PO"),
    ("Penjualan", "sales_order.view", "Lihat Pesanan Penjualan"),
    ("Penjualan", "sales_order.create", "Buat Pesanan Penjualan"),
    ("Penjualan", "sales.delivery.view", "Lihat Pengiriman Pesanan"),
    ("Penjualan", "sales.delivery.create", "Buat Pengiriman Pesanan"),
    ("Penjualan", "sales.delivery.print", "Cetak Pengiriman Pesanan"),
    ("Penjualan", "sales.invoice.view", "Lihat Faktur Penjualan"),
    ("Penjualan", "proforma_invoice.view", "Lihat Proforma Invoice"),
    ("Penjualan", "sales.invoice_markup.view", "Lihat Faktur Markup"),
    ("Penjualan", "sales.invoice_markup.create", "Buat Faktur Markup"),
    ("Penjualan", "sales.invoice.create", "Buat Faktur Penjualan"),
    ("Penjualan", "sales.invoice.edit", "Ubah Faktur Penjualan"),
    ("Penjualan", "sales.invoice.cancel", "Batalkan Faktur Penjualan (bisnis)"),
    ("Penjualan", "sales.receipt.view", "Lihat Penerimaan Penjualan"),
    ("Penjualan", "sales.receipt.create", "Buat Penerimaan Penjualan"),
    ("Penjualan", "sales.receipt.edit", "Ubah Penerimaan Penjualan"),
    ("Penjualan", "sales.receipt.cancel", "Batalkan Penerimaan Penjualan (bisnis)"),
    ("Penjualan", "sales.delivery.delete", "Hapus Pengiriman Pesanan"),
    ("Penjualan", "sales.invoice.delete", "Hapus Faktur Penjualan"),
    ("Penjualan", "sales.receipt.delete", "Hapus Penerimaan Penjualan"),
    ("Aktivitas Penjualan", "sales.activity.view", "Lihat Aktivitas Sales / Outlet"),
    ("Aktivitas Penjualan", "sales.activity.create", "Catat Aktivitas Sales sendiri"),
    ("Aktivitas Penjualan", "sales.outlet.create", "Buat Outlet / Prospek sendiri"),
    ("Aktivitas Penjualan", "sales.ownership.manage", "Kelola kepemilikan outlet"),
    ("Aktivitas Penjualan", "sales.pool.manage", "Kelola Pool assignment"),
    ("Aktivitas Penjualan", "sales.activity.import", "Import migrasi Sales Activity"),
    ("Aktivitas Penjualan", "sales.activity.activate_migration", "Aktifkan automation data migrasi"),
    ("Pengaturan", "settings.sales_activity.view", "Lihat pengaturan Sales Activity & Ownership"),
    ("Pengaturan", "settings.sales_activity.manage", "Ubah pengaturan Sales Activity & Ownership"),
    ("Pengaturan", "settings.sales_user_mapping.view", "Lihat mapping Sales Accurate"),
    ("Pengaturan", "settings.sales_user_mapping.manage", "Kelola mapping Sales Accurate"),
    ("Pengaturan", "settings.sales_master.view", "Lihat Master Sales"),
    ("Pengaturan", "settings.sales_master.manage", "Kelola Master Sales"),
    ("Pengaturan", "settings.crm_daily_report.view", "Lihat pengaturan Laporan Harian CRM"),
    ("Pengaturan", "settings.crm_daily_report.manage", "Kelola pengaturan Laporan Harian CRM"),
    ("Penjualan", "sales.pwa.view", "Buka PWA Sales terisolasi"),
    ("Penjualan", "sales.pwa.visit.create", "Catat visit milik sendiri dari PWA Sales"),
    ("Optimasi Rute", "route_optimizer.view", "Lihat Optimasi Rute"),
    ("Optimasi Rute", "route_optimizer.create", "Buat Rute Optimasi"),
    ("Optimasi Rute", "route_optimizer.edit_own", "Ubah rute Optimasi sendiri"),
    ("Optimasi Rute", "route_optimizer.optimize", "Jalankan optimasi rute"),
    ("Optimasi Rute", "route_optimizer.manage", "Kelola semua rute Optimasi"),
    ("Pengaturan", "settings.route_optimizer.view", "Lihat pengaturan Optimasi Rute"),
    ("Pengaturan", "settings.route_optimizer.manage", "Ubah pengaturan Optimasi Rute"),
    ("Import PO", "po.import.view", "Buka halaman Import PO"),
    ("Import PO", "accurate.import", "Preview & Import Single PO dari Accurate"),
    ("Import PO", "accurate.import.bulk", "Preview & Import beberapa Sales Order Accurate sekaligus"),
    ("Import PO", "po.import.pdf", "Import PO dari file PDF"),
    ("Import PO", "po.import.spreadsheet", "Import PO dari file XLSX / CSV"),
    ("Persediaan", "master_items.view", "Lihat Barang/Jasa"),
    ("Persediaan", "master_items.sync", "Sinkronkan Barang/Jasa dari Accurate"),
    ("Persediaan", "master_items.manage", "Tambah / kelola Barang/Jasa lokal"),
    ("Persediaan", "org.branches.view", "Lihat Cabang"),
    ("Persediaan", "org.branches.manage", "Kelola Cabang"),
    ("Persediaan", "org.warehouses.view", "Lihat Gudang"),
    ("Persediaan", "org.warehouses.manage", "Kelola Gudang"),
    ("Persediaan", "current_stock.view", "Lihat Stock Saat Ini"),
    ("Persediaan", "current_stock.update", "Ubah Qty Saat Ini / Pembelian Barang"),
    ("Persediaan", "current_stock.import", "Impor & Sesuaikan Stock Saat Ini"),
    ("Persediaan", "current_stock.export", "Export Stock Saat Ini"),
    ("Persediaan", "stock_minimum.manage", "Atur Stok Minimum"),
    ("Persediaan", "inventory_valuation.view", "Lihat Nilai Persediaan"),
    ("Persediaan", "inventory_valuation.export", "Export Nilai Persediaan"),
    ("Persediaan", "inventory_valuation.manage_opening", "Atur HPP Awal Persediaan"),
    ("Pembelian", "purchase_receipts.view", "Lihat Penerimaan Barang"),
    ("Pembelian", "purchase_receipts.sync", "Sinkronkan Penerimaan Barang dari Accurate"),
    ("Pembelian", "purchase_receipts.export", "Export Penerimaan Barang"),
    ("Pembelian", "purchase_receipts.receive", "Catat penerimaan terhadap Pesanan Pembelian"),
    ("Pembelian", "purchase_requests.view", "Lihat Permintaan"),
    ("Pembelian", "purchase_requests.create", "Buat Permintaan"),
    ("Pembelian", "purchase_orders.view", "Lihat Pesanan Pembelian"),
    ("Pembelian", "purchase_orders.create", "Buat Pesanan Pembelian"),
    ("Pembelian", "purchase_orders.edit_branch", "Lengkapi Cabang Pembelian pada PO lama"),
    ("Pembelian", "purchase_invoices.view", "Lihat Faktur Pembelian"),
    ("Pembelian", "purchase_invoices.create", "Buat Faktur Pembelian"),
    ("Pembelian", "purchase_invoices.edit", "Ubah termin / jatuh tempo Faktur Pembelian"),
    ("Pembelian", "purchase_payments.view", "Lihat Pembayaran Pembelian"),
    ("Pembelian", "purchase_payments.create", "Buat Pembayaran Pembelian"),
    ("Pembelian", "purchase_hpp.view", "Lihat HPP Pembelian"),
    ("Pengaturan", "settings.hpp.view", "Lihat Pengaturan HPP"),
    ("Pengaturan", "settings.hpp.manage", "Ubah Pengaturan HPP / hitung ulang"),
    ("Pengaturan", "settings.sales_accounting.view", "Lihat Pengaturan Akuntansi Penjualan"),
    ("Pengaturan", "settings.sales_accounting.manage", "Ubah Pengaturan Akuntansi Penjualan"),
    ("Pengaturan", "settings.purchase_accounting.view", "Lihat Pengaturan Akuntansi Pembelian"),
    ("Pengaturan", "settings.purchase_accounting.manage", "Ubah Pengaturan Akuntansi Pembelian"),
    ("Penjualan", "sales_accounting.post", "Post Faktur Penjualan ke Accounting"),
    ("Penjualan", "sales_accounting.reverse", "Reversal Faktur Penjualan dari Accounting"),
    ("Penjualan", "sales.receipt.accounting.post", "Post Penerimaan Penjualan ke Accounting"),
    ("Penjualan", "sales.receipt.accounting.reverse", "Reversal Penerimaan Penjualan dari Accounting"),
    ("Pembelian", "purchase_accounting.post", "Post Faktur Pembelian ke Accounting"),
    ("Pembelian", "purchase_accounting.reverse", "Reversal Faktur Pembelian dari Accounting"),
    ("Pembelian", "purchase.payment.accounting.post", "Post Pembayaran Pembelian ke Accounting"),
    ("Pembelian", "purchase.payment.accounting.reverse", "Reversal Pembayaran Pembelian dari Accounting"),
    ("Pembelian", "purchase.receipt.accounting.post", "Post Penerimaan Barang ke Accounting"),
    ("Pembelian", "purchase.receipt.accounting.reverse", "Reversal Penerimaan Barang dari Accounting"),
    ("Buku Besar", "inventory_issue.view", "Lihat HPP / Pengeluaran Persediaan"),
    ("Buku Besar", "inventory_issue.post", "Post HPP / Pengeluaran Persediaan"),
    ("Buku Besar", "inventory_reconciliation.view", "Lihat Rekonsiliasi Persediaan / HPP"),
    ("Pengaturan", "payroll_accounting.view", "Lihat kesiapan akuntansi payroll"),
    ("Pengaturan", "payroll_accounting.manage", "Kelola pemetaan akuntansi payroll"),
    ("Pengaturan", "payroll_accounting.post", "Post Jurnal Akrual Payroll ke Accounting"),
    ("Pengaturan", "settings.customer_pricing.view", "Lihat Harga Customer"),
    ("Pengaturan", "settings.customer_pricing.manage", "Kelola Draft Harga Customer"),
    ("Pengaturan", "settings.customer_pricing.publish", "Publish Harga Customer"),
    ("Pengaturan", "settings.public_catalog.view", "Lihat Katalog Publik"),
    ("Pengaturan", "settings.public_catalog.manage", "Kelola Katalog Publik"),
    ("Design", "design.view", "Lihat desain"),
    ("Design", "design.upload", "Upload desain"),
    ("Design", "design.approve", "Approve desain"),
    ("Design", "design.bypass_approval", "Bypass approval desain"),
    ("Payment", "payment.view", "Lihat pembayaran"),
    ("Payment", "payment.upload", "Upload bukti pembayaran"),
    ("Payment", "payment.approve", "Approve pembayaran"),
    ("Payment", "payment.bypass_approval", "Bypass approval pembayaran"),
    ("Jadwal", "schedule.view", "Lihat jadwal produksi"),
    ("Jadwal", "schedule.edit", "Atur jadwal produksi"),
    ("Jadwal", "schedule.work_date.view", "Lihat Tanggal Pengerjaan"),
    ("Jadwal", "schedule.work_date.edit", "Ubah Tanggal Pengerjaan"),
    ("Workflow", "workflow.rollback", "Kembalikan tahap workflow"),
    ("Screener", "screen.update", "Update tahap Screener"),
    ("Persiapan Barang", "helper.prepare", "Persiapan barang"),
    ("Operator", "operator.update", "Update progres Operator"),
    ("Operator", "operator.sample_upload", "Upload sample cetak"),
    ("Operator", "operator.sample_approve", "Approve sample cetak"),
    ("Produksi", "work_panel.view", "Lihat Form Pekerjaan Divisi"),
    ("Pengaturan", "settings.operational_calendar.manage", "Kelola Kalender Libur Operasional"),
    ("Pengaturan", "settings.work_panel.view", "Lihat pengaturan Form Pekerjaan Divisi"),
    ("Pengaturan", "settings.work_panel.manage", "Ubah pengaturan Form Pekerjaan Divisi"),
    ("Pengaturan", "settings.design_cetakan.view", "Lihat Design Cetakan"),
    ("Pengaturan", "settings.design_cetakan.manage", "Ubah Design Cetakan"),
    ("Laporan", "reports.gaji_operator.view", "Lihat Gaji Operator"),
    ("Laporan", "reports.gaji_operator.repair_work_date", "Koreksi tanggal kerja output Gaji Operator"),
    ("Laporan", "reports.gaji_operator.repair_rate", "Koreksi snapshot tarif output Gaji Operator"),
    ("Laporan", "reports.sales.view", "Lihat Laporan Penjualan"),
    ("Laporan", "reports.sales.export", "Ekspor Laporan Penjualan"),
    ("Laporan", "reports.purchase.view", "Lihat Laporan Pembelian"),
    ("Laporan", "reports.purchase.export", "Ekspor Laporan Pembelian"),
    ("Laporan", "reports.order_prediction.view", "Lihat Prediksi Order"),
    ("Laporan", "reports.order_prediction.import", "Impor Histori Prediksi Order"),
    ("Pengaturan", "settings.operator_wage.view", "Lihat Upah Operator"),
    ("Pengaturan", "settings.operator_wage.manage", "Ubah Upah Operator"),
    ("Packing", "packing.update", "Packing"),
    ("Surat Jalan", "delivery_note.view", "Lihat Surat Jalan"),
    ("Surat Jalan", "delivery_note.view_file", "Lihat File Surat Jalan"),
    ("Surat Jalan", "delivery_note.manage", "Kelola Surat Jalan"),
    ("Surat Jalan", "delivery_note.delete_file", "Hapus File Surat Jalan"),
    ("Loading", "loading.update", "Konfirmasi Loading"),
    ("Loading", "loading.delete", "Hapus Line Loading"),
    ("Loading", "loading.view", "Lihat Loading"),
    ("Loading", "loading.confirm_match", "Konfirmasi Barang Sesuai SJ"),
    ("Loading", "loading.report_discrepancy", "Laporkan Selisih Qty Loading"),
    ("Loading", "loading.view_discrepancy_history", "Lihat Riwayat Selisih Loading"),
    ("Loading", "loading.resolve_discrepancy", "Selesaikan Selisih Loading"),
    ("Loading", "loading.granular_configured", "Hak akses Loading granular sudah dikonfigurasi"),
    ("Pengiriman", "delivery.update", "Update status kirim"),
    ("Pengiriman", "delivery.reschedule", "Ajukan penjadwalan ulang pengiriman"),
    ("Pengiriman", "delivery.reschedule.set_date", "Tetapkan tanggal penjadwalan ulang"),
    ("Pengiriman", "delivery.schedule.view", "Lihat Jadwal Pengiriman"),
    ("Pengiriman", "delivery.location.manage", "Atur Lokasi Outlet Pengiriman"),
    ("Pengiriman", "delivery.route.optimize", "Optimize Rute Pengiriman (legacy)"),
    ("Pengiriman", "delivery.route.edit", "Ubah draf rute pengiriman (legacy)"),
    ("Pengiriman", "delivery.route.confirm", "Konfirmasi Jadwal Rute (legacy)"),
    ("Pengiriman", "delivery.vehicle.manage", "Kelola Master Kendaraan"),
    ("Pengaturan", "settings.depot.view", "Lihat Gudang / Depot (legacy)"),
    ("Pengaturan", "settings.depot.manage", "Ubah Gudang / Depot (legacy)"),
    ("Pengiriman", "delivery.deadline.edit", "Edit Deadline Pengiriman"),
    ("Pengiriman", "delivery.deadline.settings", "Atur Perhitungan Deadline Pengiriman"),
    ("Produksi", "production.route.edit", "Atur Proses Cetak / Non-Cetak per Line Item"),
    ("Sentralisasi Cat", "paint.view", "Lihat Sentralisasi Cat"),
    ("Sentralisasi Cat", "paint.stock.view", "Lihat Stok Cat"),
    ("Sentralisasi Cat", "paint.stock.opening", "Input Stok Awal Cat"),
    ("Sentralisasi Cat", "paint.stock.add", "Tambah Stok Cat"),
    ("Sentralisasi Cat", "paint.transaction.create", "Catat Ambil/Kembali Cat"),
    ("Sentralisasi Cat", "paint.transaction.correct", "Koreksi / Adjust Stok Cat"),
    ("Sentralisasi Cat", "paint.report.view", "Lihat Laporan Penggunaan Cat"),
    ("Sentralisasi Cat", "paint.report.export", "Ekspor Laporan Penggunaan Cat"),
    ("Sentralisasi Cat", "paint.settings.manage", "Kelola Master Cat"),
    ("Catatan Produksi", "production_note.view", "Lihat Catatan Produksi"),
    ("Catatan Produksi", "production_note.create", "Tambah Catatan Produksi"),
    ("Catatan Produksi", "production_note.edit", "Edit Catatan Produksi"),
    ("Prioritas", "urgent.manage", "Set/hapus urgent (legacy — tidak lagi mengubah prioritas)"),
    ("Prioritas", "urgent.request", "Ajukan Urgent PO"),
    ("Prioritas", "urgent.review", "Review Pengajuan Urgent"),
    ("Prioritas", "urgent.approve", "Setujui / Tolak Pengajuan Urgent"),
    ("Prioritas", "urgent.notify", "Terima notifikasi pengajuan Urgent (aplikasi)"),
    ("Prioritas", "urgent.email_notify", "Terima email pengajuan Urgent (Phase B)"),
    ("PO", "buffer_stock.manage", "Set/hapus Buffer Stock"),
    ("User & Karyawan", "users.view", "Lihat data karyawan"),
    ("User & Karyawan", "users.manage", "Kelola karyawan"),
    ("Role & Permission", "roles.manage", "Ubah role user"),
    ("Role & Permission", "permissions.manage", "Ubah default permission role"),
    ("Role & Permission", "super_admin.manage", "Kelola Super Admin"),
    ("Dashboard Produksi", "dashboard.production.view", "Lihat Dashboard Produksi"),
    ("Dashboard Produksi", "dashboard.production.operator_analytics", "Lihat analitik operator Dashboard Produksi"),
    ("Dashboard Produksi", "dashboard.production.branch_output", "Lihat output produksi per cabang"),
    (
        "Dashboard Produksi",
        "dashboard.production.urgent_requests.view",
        "Lihat Pengajuan Urgent Dashboard",
    ),
    ("Dashboard Keuangan", "dashboard.finance.view", "Lihat Dashboard Keuangan"),
    ("Dashboard Penjualan", "dashboard.sales.view", "Lihat Dashboard Penjualan"),
    ("Dashboard Owner", "dashboard.owner.view", "Lihat Dashboard Owner (read-only)"),
    ("Dashboard Produksi", "dashboard.production.input", "Input data dari Dashboard"),
    ("Dashboard Produksi", "dashboard.production.edit", "Edit data dari Dashboard"),
    ("Dashboard Produksi", "dashboard.production.export", "Export Excel Dashboard Jadwal Produksi"),
    ("Audit", "audit.view", "Lihat audit log"),
    ("Administrasi Karyawan", "employee_admin.view_own", "Lihat pengajuan sendiri"),
    ("Administrasi Karyawan", "employee_admin.submit", "Buat pengajuan karyawan"),
    ("Administrasi Karyawan", "employee_admin.approve", "Setujui pengajuan karyawan"),
    ("Administrasi Karyawan", "employee_admin.view_all", "Lihat semua pengajuan karyawan"),
    ("Administrasi Karyawan", "employee_admin.settings", "Atur Administrasi Karyawan"),
    ("Administrasi Karyawan", "employee_admin.document_view", "Lihat dokumen resmi pengajuan"),
    ("HR & Payroll", "hr.employee.view", "Lihat daftar karyawan HR"),
    ("HR & Payroll", "hr.employee.manage", "Kelola data karyawan & kepegawaian"),
    ("HR & Payroll", "payroll.view", "Lihat penggajian"),
    ("HR & Payroll", "payroll.calculate", "Hitung / kelola Draft penggajian"),
    ("HR & Payroll", "payroll.review", "Review penggajian"),
    ("HR & Payroll", "payroll.approve", "Approve penggajian"),
    ("HR & Payroll", "payroll.finalize", "Finalisasi penggajian"),
    ("HR & Payroll", "payroll.reopen", "Buka ulang penggajian Final"),
    ("HR & Payroll", "payroll.settings", "Atur HR & Payroll"),
    ("HR & Payroll", "payroll.payslip.view_own", "Lihat slip gaji sendiri"),
    ("HR & Payroll", "payroll.payslip.view_all", "Lihat semua slip gaji"),
    ("HR & Payroll", "payroll.adjustment", "Penyesuaian manual penggajian"),
    ("HR & Payroll", "payroll.piece_holding.edit", "Isi periode, bonus, dan kasbon upah borongan"),
    ("HR & Payroll", "payroll_payment.view", "Lihat Pembayaran Payroll"),
    ("HR & Payroll", "payroll_payment.create", "Buat Pembayaran Payroll"),
    ("HR & Payroll", "payroll_payment.approve", "Setujui Pembayaran Payroll"),
    ("HR & Payroll", "payroll_payment.post", "Post Pembayaran Payroll ke Accounting"),
    ("HR & Payroll", "payroll_payment.reverse", "Reversal Pembayaran Payroll dari Accounting"),
    ("HR & Payroll", "payroll_payment.cancel", "Batalkan Pembayaran Payroll"),
    ("Buku Besar", "ledger.accounts.view", "Lihat Akun Perkiraan"),
    ("Buku Besar", "ledger.accounts.manage", "Kelola Akun Perkiraan"),
    ("Buku Besar", "general_journal.view", "Lihat Jurnal Umum"),
    ("Buku Besar", "general_journal.manage", "Kelola Jurnal Umum"),
    ("Buku Besar", "general_journal.post", "Posting Jurnal Umum"),
    ("Buku Besar", "general_journal.reverse", "Reversal Jurnal Umum"),
    ("Buku Besar", "opening_balance.view", "Lihat Saldo Awal"),
    ("Buku Besar", "opening_balance.manage", "Kelola Saldo Awal"),
    ("Buku Besar", "opening_balance.post", "Posting Saldo Awal"),
    ("Buku Besar", "opening_balance.reverse", "Reversal Saldo Awal"),
    ("Buku Besar", "general_ledger.view", "Lihat Buku Besar"),
    ("Buku Besar", "accounts_receivable.view", "Lihat Piutang Usaha"),
    ("Buku Besar", "accounts_payable.view", "Lihat Hutang Usaha"),
    ("Buku Besar", "trial_balance.view", "Lihat Neraca Saldo"),
    ("Buku Besar", "profit_loss.view", "Lihat Laba/Rugi"),
    ("Buku Besar", "balance_sheet.view", "Lihat Neraca"),
    ("Buku Besar", "accounting.period.view", "Lihat Periode Akuntansi"),
    ("Buku Besar", "accounting.period.close", "Tutup Periode Akuntansi"),
    ("Buku Besar", "accounting.period.reopen", "Buka Ulang Periode Akuntansi"),
    ("Kas & Bank", "cash_bank.view", "Lihat Kas & Bank"),
    ("Kas & Bank", "cash_bank.manage", "Kelola Kas & Bank"),
    ("Kas & Bank", "cash_bank.post", "Posting Kas & Bank"),
    ("Kas & Bank", "cash_bank.reverse", "Reversal Kas & Bank"),
    ("Kas & Bank", "cash_bank.reconcile", "Rekonsiliasi Kas & Bank"),
    (
        "Kas & Bank",
        "cash_bank.reconcile.purge_unmatched",
        "Hapus mutasi rekening koran belum cocok",
    ),
    ("Chat", "chat.use", "Gunakan Chat Internal"),
    ("Chat", "chat.group.manage", "Kelola Grup Chat"),
    ("Chat", "chat.moderate", "Moderasi Chat"),
    ("SPK", "spk.create", "Buat SPK Antar Divisi"),
    ("SPK", "spk.view_own", "Lihat SPK sendiri"),
    ("SPK", "spk.view_division", "Lihat SPK divisi"),
    ("SPK", "spk.view_all", "Lihat semua SPK"),
    ("SPK", "spk.assign", "Tugaskan SPK"),
    ("SPK", "spk.update_progress", "Perbarui progress SPK"),
    ("SPK", "spk.comment", "Beri catatan pada SPK"),
    ("SPK", "spk.correct", "Koreksi administratif nilai SPK"),
    ("SPK", "spk.verify", "Verifikasi hasil SPK"),
    ("SPK", "spk.cancel", "Batalkan SPK"),
    ("SPK", "spk.manage", "Kelola SPK"),
    # Customer Approval (design)
    ("Customer Approval", "customer_approval.view", "Lihat Customer Approval"),
    ("Customer Approval", "customer_approval.manage", "Kelola Link Customer Approval"),
    ("Customer Approval", "customer_approval.settings", "Atur Customer Approval"),
    ("Notifikasi", "notifications.settings.view", "Lihat pengaturan notifikasi"),
    ("Notifikasi", "notifications.settings.manage", "Kelola pengaturan notifikasi"),
    # Sidebar / menu visibility (additional layer; does not replace API RBAC)
    ("Akses Menu", "nav.dashboard", "Menu Dashboard"),
    ("Akses Menu", "nav.dashboard.sales", "Menu Dashboard Penjualan"),
    ("Akses Menu", "nav.dashboard.finance", "Menu Dashboard Keuangan"),
    ("Akses Menu", "nav.production", "Menu Produksi"),
    ("Akses Menu", "nav.production_schedule", "Menu Jadwal Produksi"),
    ("Akses Menu", "nav.production.paint", "Menu Sentralisasi Cat"),
    ("Akses Menu", "nav.delivery_schedule", "Menu Jadwal Pengiriman"),
    ("Akses Menu", "nav.delivery_reschedule", "Menu Jadwal Pengiriman Ulang"),
    ("Akses Menu", "nav.driver_route", "Menu Rute Saya (legacy)"),
    ("Akses Menu", "nav.tasks", "Menu Tugas Produksi"),
    ("Akses Menu", "nav.tasks.designer", "Menu Tugas Designer"),
    ("Akses Menu", "nav.tasks.screener", "Menu Tugas Screener"),
    ("Akses Menu", "nav.tasks.kepala_produksi", "Menu Tugas Kepala Produksi"),
    ("Akses Menu", "nav.tasks.helper", "Menu Tugas Helper"),
    ("Akses Menu", "nav.tasks.operator", "Menu Tugas Operator"),
    ("Akses Menu", "nav.tasks.packing", "Menu Tugas Packing"),
    ("Akses Menu", "nav.tasks.surat_jalan", "Menu Tugas Surat Jalan"),
    ("Akses Menu", "nav.tasks.loading", "Menu Tugas Loading"),
    ("Akses Menu", "nav.tasks.delivery", "Menu Tugas Driver / Terkirim"),
    ("Akses Menu", "nav.sales", "Menu Penjualan"),
    ("Akses Menu", "nav.sales.customers", "Menu Data Pelanggan"),
    ("Akses Menu", "nav.sales.product_requests", "Menu Permintaan Produk"),
    ("Akses Menu", "nav.sales.pra_po", "Menu Pra PO"),
    ("Akses Menu", "nav.sales.sales_order", "Menu Pesanan Penjualan"),
    ("Akses Menu", "nav.sales.delivery_order", "Menu Pengiriman Pesanan"),
    ("Akses Menu", "nav.sales.sales_invoice", "Menu Faktur Penjualan"),
    ("Akses Menu", "nav.sales.sales_receipt", "Menu Penerimaan Penjualan"),
    ("Akses Menu", "nav.sales.import_po", "Menu Import PO"),
    ("Akses Menu", "nav.sales.sales_activity", "Menu Aktivitas Sales"),
    ("Akses Menu", "nav.sales.crm_daily_report", "Menu Laporan Harian CRM"),
    ("Akses Menu", "nav.purchasing", "Menu Pembelian"),
    ("Akses Menu", "nav.purchasing.requests", "Menu Permintaan"),
    ("Akses Menu", "nav.purchasing.orders", "Menu Pesanan Pembelian"),
    ("Akses Menu", "nav.purchasing.advances", "Menu Uang Muka Pembelian"),
    ("Akses Menu", "nav.purchasing.invoices", "Menu Faktur Pembelian"),
    ("Akses Menu", "nav.purchasing.payments", "Menu Pembayaran Pembelian"),
    ("Akses Menu", "nav.purchasing.receipts", "Menu Penerimaan Barang"),
    ("Akses Menu", "nav.purchasing.returns", "Menu Retur Pembelian"),
    ("Akses Menu", "nav.purchasing.hpp", "Menu HPP Pembelian"),
    ("Akses Menu", "nav.dashboard.owner", "Menu Dashboard Owner"),
    ("Akses Menu", "nav.cash_bank.reconciliation_owner", "Menu Rekonsiliasi Owner"),
    ("Akses Menu", "nav.inventory", "Menu Persediaan"),
    ("Akses Menu", "nav.inventory.master_items", "Menu Barang/Jasa"),
    ("Akses Menu", "nav.inventory.branches_warehouses", "Menu Cabang & Gudang"),
    ("Akses Menu", "nav.inventory.current_stock", "Menu Stock Saat Ini"),
    ("Akses Menu", "nav.inventory.inventory_value", "Menu Nilai Persediaan"),
    ("Akses Menu", "nav.reports", "Menu Laporan"),
    ("Akses Menu", "nav.reports.analytics_output", "Menu Analitik Output"),
    ("Akses Menu", "nav.reports.shipment_status", "Menu Status Pengiriman"),
    ("Akses Menu", "nav.reports.status_po", "Menu Status PO"),
    ("Akses Menu", "nav.reports.gaji_operator", "Menu Gaji Operator"),
    ("Akses Menu", "nav.reports.order_prediction", "Menu Prediksi Order"),
    ("Akses Menu", "nav.settings", "Menu Pengaturan"),
    ("Akses Menu", "nav.employee_administration", "Menu Administrasi Karyawan"),
    ("Akses Menu", "nav.employee_administration.mine", "Menu Pengajuan Saya"),
    ("Akses Menu", "nav.employee_administration.create", "Menu Buat Pengajuan"),
    ("Akses Menu", "nav.employee_administration.approve", "Menu Persetujuan"),
    ("Akses Menu", "nav.employee_administration.archive", "Menu Arsip Dokumen"),
    ("Akses Menu", "nav.hr_payroll", "Menu HR & Payroll"),
    ("Akses Menu", "nav.hr_payroll.employees", "Menu Daftar Karyawan"),
    ("Akses Menu", "nav.hr_payroll.employment", "Menu Data Kepegawaian"),
    ("Akses Menu", "nav.hr_payroll.payroll", "Menu Penggajian"),
    ("Akses Menu", "nav.hr_payroll.payslips", "Menu Slip Gaji"),
    ("Akses Menu", "nav.hr_payroll.components", "Menu Komponen Gaji"),
    ("Akses Menu", "nav.hr_payroll.history", "Menu Riwayat Penggajian"),
    ("Akses Menu", "nav.hr_payroll.reconciliation", "Menu Rekonsiliasi Payroll"),
    ("Akses Menu", "nav.route_optimizer", "Menu Optimasi Rute"),
    ("Akses Menu", "nav.ledger", "Menu Buku Besar"),
    ("Akses Menu", "nav.ledger.accounts", "Menu Akun Perkiraan"),
    ("Akses Menu", "nav.ledger.general_journal", "Menu Jurnal Umum"),
    ("Akses Menu", "nav.ledger.opening_balance", "Menu Saldo Awal"),
    ("Akses Menu", "nav.ledger.general_ledger", "Menu Buku Besar"),
    ("Akses Menu", "nav.ledger.accounts_receivable", "Menu Piutang Usaha"),
    ("Akses Menu", "nav.ledger.accounts_payable", "Menu Hutang Usaha"),
    ("Akses Menu", "nav.ledger.trial_balance", "Menu Neraca Saldo"),
    ("Akses Menu", "nav.ledger.profit_loss", "Menu Laba/Rugi"),
    ("Akses Menu", "nav.ledger.balance_sheet", "Menu Neraca"),
    ("Akses Menu", "nav.ledger.accounting_periods", "Menu Periode Akuntansi"),
    ("Akses Menu", "nav.cash_bank", "Menu Kas & Bank"),
    ("Akses Menu", "nav.cash_bank.accounts", "Menu Daftar Kas & Bank"),
    ("Akses Menu", "nav.cash_bank.receipts", "Menu Penerimaan Kas & Bank"),
    ("Akses Menu", "nav.cash_bank.payments", "Menu Pembayaran Kas & Bank"),
    ("Akses Menu", "nav.cash_bank.transfers", "Menu Transfer Kas & Bank"),
    ("Akses Menu", "nav.cash_bank.mutasi", "Menu Mutasi Kas & Bank"),
    ("Akses Menu", "nav.cash_bank.reconciliation", "Menu Rekonsiliasi Bank"),
    ("Akses Menu", "nav.chat", "Menu Chat"),
    ("Akses Menu", "nav.spk", "Menu SPK Antar Divisi"),
    ("Akses Menu", "nav.configured", "Akses menu sudah dikonfigurasi"),
]


PERMISSION_DESCRIPTIONS = {
    "loading.view": "Akses lihat tahap Loading tanpa mengubah data.",
    "loading.confirm_match": "Izinkan konfirmasi satu klik bahwa barang fisik sesuai Surat Jalan.",
    "loading.report_discrepancy": "Izinkan melaporkan selisih qty fisik terhadap Surat Jalan.",
    "loading.view_discrepancy_history": "Izinkan melihat riwayat laporan selisih Loading.",
    "loading.resolve_discrepancy": "Izinkan penanganan administratif selisih (tidak membuka Pengiriman sendiri).",
    "spk.view_own": "Melihat SPK yang ditugaskan kepada pengguna atau yang ia buat.",
    "spk.view_division": "Melihat SPK pada divisi pengguna, tanpa mengubah nilai progres PIC lain.",
    "spk.view_all": "Melihat seluruh SPK antar divisi, tanpa mengubah nilai progres PIC lain.",
    "spk.create": "Membuat, menyimpan draft, dan menerbitkan SPK antar divisi.",
    "spk.update_progress": "Menandai bahwa role ini biasa mengerjakan SPK. Nilai progres tetap hanya dapat diubah oleh PIC yang ditugaskan.",
    "spk.comment": "Menambah catatan pada SPK yang berhak dilihat, tanpa mengubah nilai progres.",
    "spk.correct": "Koreksi nilai SPK yang tercatat, dengan alasan wajib. Bukan update progres biasa.",
    "spk.verify": "Menerima hasil atau meminta revisi. PIC tidak dapat memverifikasi SPK yang ditugaskan kepadanya.",
    "spk.cancel": "Membatalkan SPK yang belum selesai, dengan alasan wajib.",
    "spk.assign": "Memilih PIC saat menyusun SPK. Tidak memberi hak mengubah progres PIC lain.",
    "spk.manage": "Kelola administratif SPK, termasuk koreksi tercatat. Tidak memakai endpoint progres biasa.",
    "sales.invoice.cancel": (
        "Batalkan Faktur Penjualan secara bisnis (soft-cancel). Tidak diwariskan dari create/edit/delete. "
        "Tidak memberi wewenang reverse jurnal accounting secara umum."
    ),
    "sales.receipt.edit": (
        "Ubah Penerimaan Penjualan (koreksi field / revisi keuangan). Tidak diwariskan dari create/cancel/delete. "
        "Tidak memberi wewenang post/reverse accounting secara umum. Opt-in eksplisit, termasuk admin."
    ),
    "sales.receipt.cancel": (
        "Batalkan Penerimaan Penjualan secara bisnis (soft-cancel). Tidak diwariskan dari create/edit/delete. "
        "Tidak memberi wewenang reverse jurnal accounting secara umum."
    ),
}

ALL_PERMISSIONS = tuple(p for _mod, p, _lbl in PERMISSION_CATALOG)

# Opt-in only: never part of role defaults (including admin) or automatic
# super-admin/admin bypass grants. Must be explicitly assigned via Hak Akses
# or per-user override. Create/edit/delete must never imply these.
OPT_IN_ONLY_PERMISSIONS = frozenset({
    "sales.invoice.cancel",
    "sales.receipt.cancel",
    "sales.receipt.edit",
    # Stock minimum thresholds — never implied by current_stock.update.
    "stock_minimum.manage",
    # Destructive unmatched statement purge — never in admin/operator defaults.
    "cash_bank.reconcile.purge_unmatched",
    # Sentralisasi Cat V1 — never auto-granted; assign via Hak Akses & Role.
    "paint.view",
    "paint.stock.view",
    "paint.stock.opening",
    "paint.stock.add",
    "paint.transaction.create",
    "paint.transaction.correct",
    "paint.report.view",
    "paint.report.export",
    "paint.settings.manage",
    "nav.production.paint",
    # Report Center pembelian Wave 1 — default OFF; assign via Hak Akses & Role.
    # Never implied by purchase_invoices.view.
    "reports.purchase.view",
    "reports.purchase.export",
    # Urgent PO approval workflow — default OFF; never implied by urgent.manage.
    # Performa dan beban operator. Tidak ikut dashboard.production.view atau schedule.view.
    # Output per cabang. Tidak ikut dashboard.production.view.
    "dashboard.production.operator_analytics",
    "dashboard.production.branch_output",
    "settings.operational_calendar.manage",
    "urgent.request",
    "urgent.review",
    "urgent.approve",
    "urgent.notify",
    "urgent.email_notify",
})

# Opt-in even for Direktur full-access /auth/me — must be explicit override or
# role_permissions / builtin ROLE_DEFAULT re-add (see merge_director_must_opt_in).
# API already uses _require_opt_in_perm for these.
# Sales cancel/edit must match write-path / lifecycle capability: never implied by
# Direktur full-access alone. urgent.request/notify/email stay OFF until granted.
# urgent.review/approve stay in this set so per-user False overrides still work,
# while Direktur/Holding builtin ROLE_DEFAULT re-add supplies Owner initial grants.
DIRECTOR_MUST_OPT_IN_PERMISSIONS = frozenset({
    "sales.invoice.cancel",
    "sales.receipt.cancel",
    "sales.receipt.edit",
    "dashboard.production.operator_analytics",
    "dashboard.production.branch_output",
    "settings.operational_calendar.manage",
    "urgent.request",
    "urgent.review",
    "urgent.approve",
    "urgent.notify",
    "urgent.email_notify",
})

DEFAULT_GRANT_PERMISSIONS = tuple(
    p for p in ALL_PERMISSIONS if p not in OPT_IN_ONLY_PERMISSIONS
)

# Sidebar visibility keys (excludes the internal "already configured" sentinel).
NAV_PERMISSION_KEYS = tuple(
    p for _mod, p, _lbl in PERMISSION_CATALOG
    if p.startswith("nav.") and p != "nav.configured"
)
NAV_CONFIGURED = "nav.configured"
LOADING_GRANULAR_CONFIGURED = "loading.granular_configured"
LOADING_DEFAULTS_FROM_UPDATE = (
    "loading.view",
    "loading.confirm_match",
    "loading.report_discrepancy",
    "loading.view_discrepancy_history",
)

# Menus that were previously always visible in the sidebar. Used only to derive
# defaults for roles that have not stored any nav.* keys yet.
# Pesanan Penjualan is omitted: that leaf follows the list endpoint gate.
NAV_ALWAYS_VISIBLE = (
    "nav.sales",
    "nav.sales.product_requests",
    "nav.sales.pra_po",
    "nav.sales.delivery_order",
    "nav.sales.sales_invoice",
    "nav.sales.import_po",
    "nav.purchasing",
    "nav.inventory",
    "nav.inventory.master_items",
    "nav.reports",
    "nav.reports.analytics_output",
    "nav.settings",
)

# Keys that actually open GET /sales-orders. Options allows a wider set;
# those extras still leave the list at 403, so they do not show this menu.
SALES_ORDER_PAGE_KEYS = (
    "sales_order.view",
    "sales_order.create",
    "po.view",
)

NAV_MENU_ITEMS = (
    ("nav.dashboard", "Dashboard", None),
    ("nav.dashboard.owner", "Dashboard Owner", "nav.dashboard"),
    ("nav.dashboard.sales", "Dashboard Penjualan", "nav.dashboard"),
    ("nav.dashboard.finance", "Dashboard Keuangan", "nav.dashboard"),
    ("nav.spk", "SPK Antar Divisi", None),
    ("nav.production", "Produksi", None),
    ("nav.production_schedule", "Jadwal Produksi", "nav.production"),
    ("nav.production.paint", "Sentralisasi Cat", "nav.production"),
    ("nav.delivery_schedule", "Jadwal Pengiriman", "nav.production"),
    ("nav.delivery_reschedule", "Jadwal Pengiriman Ulang", "nav.production"),
    ("nav.tasks", "Tugas Produksi", "nav.production"),
    ("nav.tasks.designer", "Designer", "nav.tasks"),
    ("nav.tasks.screener", "Screener", "nav.tasks"),
    ("nav.tasks.kepala_produksi", "Kepala Produksi", "nav.tasks"),
    ("nav.tasks.helper", "Helper", "nav.tasks"),
    ("nav.tasks.operator", "Operator", "nav.tasks"),
    ("nav.tasks.packing", "Packing", "nav.tasks"),
    ("nav.tasks.surat_jalan", "Surat Jalan", "nav.tasks"),
    ("nav.tasks.loading", "Loading", "nav.tasks"),
    ("nav.tasks.delivery", "Driver / Terkirim", "nav.tasks"),
    ("nav.sales", "Penjualan", None),
    ("nav.sales.customers", "Data Pelanggan", "nav.sales"),
    ("nav.sales.product_requests", "Permintaan Produk", "nav.sales"),
    ("nav.sales.pra_po", "Pra PO", "nav.sales"),
    ("nav.sales.sales_order", "Pesanan Penjualan", "nav.sales"),
    ("nav.sales.delivery_order", "Pengiriman Pesanan", "nav.sales"),
    ("nav.sales.sales_invoice", "Faktur Penjualan", "nav.sales"),
    ("nav.sales.sales_receipt", "Penerimaan Penjualan", "nav.sales"),
    ("nav.sales.import_po", "Import PO", "nav.sales"),
    ("nav.sales.sales_activity", "Aktivitas Sales", "nav.sales"),
    ("nav.sales.crm_daily_report", "Laporan Harian CRM", "nav.sales"),
    ("nav.purchasing", "Pembelian", None),
    ("nav.purchasing.requests", "Permintaan", "nav.purchasing"),
    ("nav.purchasing.orders", "Pesanan Pembelian", "nav.purchasing"),
    ("nav.purchasing.advances", "Uang Muka", "nav.purchasing"),
    ("nav.purchasing.invoices", "Faktur Pembelian", "nav.purchasing"),
    ("nav.purchasing.payments", "Pembayaran Pembelian", "nav.purchasing"),
    ("nav.purchasing.receipts", "Penerimaan Barang", "nav.purchasing"),
    ("nav.purchasing.returns", "Retur Pembelian", "nav.purchasing"),
    ("nav.purchasing.hpp", "HPP Pembelian", "nav.purchasing"),
    ("nav.inventory", "Persediaan", None),
    ("nav.inventory.master_items", "Barang/Jasa", "nav.inventory"),
    ("nav.inventory.branches_warehouses", "Cabang & Gudang", "nav.inventory"),
    ("nav.inventory.current_stock", "Stock Saat Ini", "nav.inventory"),
    ("nav.inventory.inventory_value", "Nilai Persediaan", "nav.inventory"),
    ("nav.reports", "Laporan", None),
    ("nav.reports.analytics_output", "Analitik Output", "nav.reports"),
    ("nav.reports.status_po", "Status PO", "nav.reports"),
    ("nav.reports.shipment_status", "Status Pengiriman", "nav.reports"),
    ("nav.reports.gaji_operator", "Gaji Operator", "nav.reports"),
    ("nav.reports.order_prediction", "Prediksi Order", "nav.reports"),
    ("nav.reports.sales", "Laporan Penjualan", "nav.reports"),
    ("nav.employee_administration", "Administrasi Karyawan", None),
    ("nav.employee_administration.mine", "Pengajuan Saya", "nav.employee_administration"),
    ("nav.employee_administration.create", "Buat Pengajuan", "nav.employee_administration"),
    ("nav.employee_administration.approve", "Persetujuan", "nav.employee_administration"),
    ("nav.employee_administration.archive", "Arsip Dokumen", "nav.employee_administration"),
    ("nav.hr_payroll", "HR & Payroll", None),
    ("nav.hr_payroll.employees", "Daftar Karyawan", "nav.hr_payroll"),
    ("nav.hr_payroll.employment", "Data Kepegawaian", "nav.hr_payroll"),
    ("nav.hr_payroll.payroll", "Penggajian", "nav.hr_payroll"),
    ("nav.hr_payroll.payslips", "Slip Gaji", "nav.hr_payroll"),
    ("nav.hr_payroll.components", "Komponen Gaji", "nav.hr_payroll"),
    ("nav.hr_payroll.history", "Riwayat Penggajian", "nav.hr_payroll"),
    ("nav.hr_payroll.reconciliation", "Rekonsiliasi Payroll", "nav.hr_payroll"),
    ("nav.route_optimizer", "Optimasi Rute", None),
    ("nav.ledger", "Buku Besar", None),
    ("nav.ledger.accounts", "Akun Perkiraan", "nav.ledger"),
    ("nav.ledger.general_journal", "Jurnal Umum", "nav.ledger"),
    ("nav.ledger.opening_balance", "Saldo Awal", "nav.ledger"),
    ("nav.ledger.accounting_periods", "Periode Akuntansi", "nav.ledger"),
    ("nav.ledger.general_ledger", "Buku Besar", "nav.ledger"),
    ("nav.ledger.accounts_receivable", "Piutang Usaha", "nav.ledger"),
    ("nav.ledger.accounts_payable", "Hutang Usaha", "nav.ledger"),
    ("nav.ledger.trial_balance", "Neraca Saldo", "nav.ledger"),
    ("nav.ledger.profit_loss", "Laba/Rugi", "nav.ledger"),
    ("nav.ledger.balance_sheet", "Neraca", "nav.ledger"),
    ("nav.cash_bank", "Kas & Bank", None),
    ("nav.cash_bank.accounts", "Daftar Kas & Bank", "nav.cash_bank"),
    ("nav.cash_bank.receipts", "Penerimaan", "nav.cash_bank"),
    ("nav.cash_bank.payments", "Pembayaran", "nav.cash_bank"),
    ("nav.cash_bank.transfers", "Transfer", "nav.cash_bank"),
    ("nav.cash_bank.mutasi", "Mutasi Kas & Bank", "nav.cash_bank"),
    ("nav.cash_bank.reconciliation", "Rekonsiliasi Bank", "nav.cash_bank"),
    ("nav.cash_bank.reconciliation_owner", "Rekonsiliasi Owner", "nav.cash_bank"),
    ("nav.chat", "Chat", None),
    ("nav.settings", "Pengaturan", None),
)


def derived_nav_permissions(existing: set) -> set:
    """Default sidebar visibility from existing page-access permissions.

    Matches pre-nav sidebar behavior so old roles do not lose menus on deploy.
    """
    nav = set(NAV_ALWAYS_VISIBLE)
    if "dashboard.production.view" in existing:
        nav.add("nav.dashboard")
    if "dashboard.owner.view" in existing:
        nav.add("nav.dashboard")
        nav.add("nav.dashboard.owner")
    if "dashboard.sales.view" in existing:
        nav.add("nav.dashboard.sales")
    if "dashboard.finance.view" in existing:
        nav.add("nav.dashboard.finance")
    # Penjualan pages: a data-view grant is sufficient for menu visibility.
    # This does not grant any create/edit/delete or data permission.
    sales_pages = (
        ("nav.sales.sales_order", SALES_ORDER_PAGE_KEYS),
        ("nav.sales.delivery_order", ("sales.delivery.view",)),
        ("nav.sales.sales_invoice", ("sales.invoice.view",)),
        ("nav.sales.sales_receipt", ("sales.receipt.view",)),
        ("nav.sales.customers", ("sales.customer.view", "sales_order.view")),
    )
    for menu_key, capability_keys in sales_pages:
        if any(key in existing for key in capability_keys):
            nav.add("nav.sales")
            nav.add(menu_key)
    if "schedule.view" in existing:
        nav.add("nav.production")
        nav.add("nav.production_schedule")
        nav.add("nav.delivery_schedule")
        nav.add("nav.reports")
        nav.add("nav.reports.shipment_status")
        nav.add("nav.reports.status_po")
        nav.add("nav.reports.gaji_operator")
    if "reports.order_prediction.view" in existing:
        nav.add("nav.reports")
        nav.add("nav.reports.order_prediction")
    if "reports.sales.view" in existing:
        nav.add("nav.reports")
        nav.add("nav.reports.sales")
    if "delivery.schedule.view" in existing:
        nav.add("nav.production")
        nav.add("nav.delivery_schedule")
    if "delivery.reschedule" in existing or "delivery.reschedule.set_date" in existing:
        nav.add("nav.production")
        nav.add("nav.delivery_reschedule")
    if "org.branches.view" in existing or "org.warehouses.view" in existing:
        nav.add("nav.inventory")
        nav.add("nav.inventory.branches_warehouses")
    if "current_stock.view" in existing:
        nav.add("nav.inventory")
        nav.add("nav.inventory.current_stock")
    if "inventory_valuation.view" in existing:
        nav.add("nav.inventory")
        nav.add("nav.inventory.inventory_value")
    if "purchase_receipts.view" in existing:
        nav.add("nav.purchasing")
        nav.add("nav.purchasing.receipts")
    if "purchase_requests.view" in existing or "purchase_requests.create" in existing:
        nav.add("nav.purchasing")
        nav.add("nav.purchasing.requests")
    if "purchase_orders.view" in existing or "purchase_orders.create" in existing:
        nav.add("nav.purchasing")
        nav.add("nav.purchasing.orders")
    if "purchase_invoices.view" in existing or "purchase_invoices.create" in existing:
        nav.add("nav.purchasing")
        nav.add("nav.purchasing.invoices")
    if "purchase_payments.view" in existing or "purchase_payments.create" in existing:
        nav.add("nav.purchasing")
        nav.add("nav.purchasing.payments")
    if "purchase_hpp.view" in existing:
        nav.add("nav.purchasing")
        nav.add("nav.purchasing.hpp")
    if "design.upload" in existing:
        nav.add("nav.production")
        nav.add("nav.tasks")
        nav.add("nav.tasks.designer")
    if "schedule.edit" in existing:
        nav.add("nav.production")
        nav.add("nav.tasks")
        nav.add("nav.tasks.kepala_produksi")
    if "screen.update" in existing:
        nav.add("nav.production")
        nav.add("nav.tasks")
        nav.add("nav.tasks.screener")
    if "helper.prepare" in existing:
        nav.add("nav.production")
        nav.add("nav.tasks")
        nav.add("nav.tasks.helper")
    if "operator.update" in existing:
        nav.add("nav.production")
        nav.add("nav.tasks")
        nav.add("nav.tasks.operator")
    if "packing.update" in existing:
        nav.add("nav.production")
        nav.add("nav.tasks")
        nav.add("nav.tasks.packing")
    if "delivery_note.manage" in existing:
        nav.add("nav.production")
        nav.add("nav.tasks")
        nav.add("nav.tasks.surat_jalan")
    if (
        "loading.update" in existing
        or "loading.view" in existing
        or "loading.confirm_match" in existing
        or "loading.report_discrepancy" in existing
    ):
        nav.add("nav.production")
        nav.add("nav.tasks")
        nav.add("nav.tasks.loading")
    if "delivery.update" in existing:
        nav.add("nav.production")
        nav.add("nav.tasks")
        nav.add("nav.tasks.delivery")
    if "employee_admin.view_own" in existing or "employee_admin.submit" in existing or "employee_admin.view_all" in existing:
        nav.add("nav.employee_administration")
        nav.add("nav.employee_administration.mine")
    if "employee_admin.submit" in existing:
        nav.add("nav.employee_administration")
        nav.add("nav.employee_administration.create")
    if "employee_admin.approve" in existing:
        nav.add("nav.employee_administration")
        nav.add("nav.employee_administration.approve")
    if "employee_admin.document_view" in existing or "employee_admin.view_all" in existing:
        nav.add("nav.employee_administration")
        nav.add("nav.employee_administration.archive")
    if "employee_admin.settings" in existing:
        nav.add("nav.employee_administration")
    if "hr.employee.view" in existing or "hr.employee.manage" in existing:
        nav.add("nav.hr_payroll")
        nav.add("nav.hr_payroll.employees")
        nav.add("nav.hr_payroll.employment")
    if "payroll.view" in existing or "payroll.calculate" in existing or "payroll.review" in existing or "payroll.approve" in existing or "payroll.finalize" in existing or "payroll.piece_holding.edit" in existing:
        nav.add("nav.hr_payroll")
        nav.add("nav.hr_payroll.payroll")
    if "payroll.payslip.view_own" in existing or "payroll.payslip.view_all" in existing:
        nav.add("nav.hr_payroll")
        nav.add("nav.hr_payroll.payslips")
    if "payroll.settings" in existing or "payroll.view" in existing:
        nav.add("nav.hr_payroll")
        nav.add("nav.hr_payroll.components")
    if "payroll.view" in existing or "payroll.payslip.view_all" in existing:
        nav.add("nav.hr_payroll")
        nav.add("nav.hr_payroll.history")
    if "payroll.view" in existing and "payroll_payment.view" in existing:
        nav.add("nav.hr_payroll")
        nav.add("nav.hr_payroll.reconciliation")
    if "payroll.settings" in existing:
        nav.add("nav.hr_payroll")
    if (
        "product_request.create" in existing
        or "product_request.read_own_draft" in existing
        or "product_request.read_all" in existing
    ):
        nav.add("nav.sales")
        nav.add("nav.sales.product_requests")
    if (
        "pra_po.create" in existing
        or "pra_po.view_own" in existing
        or "pra_po.view_all" in existing
        or "pra_po.verify" in existing
    ):
        nav.add("nav.sales")
        nav.add("nav.sales.pra_po")
    if (
        "sales.activity.view" in existing
        or "sales.activity.create" in existing
        or "sales.outlet.create" in existing
        or "sales.ownership.manage" in existing
    ):
        nav.add("nav.sales")
        nav.add("nav.sales.sales_activity")
    if (
        "crm.daily_report.create" in existing
        or "crm.daily_report.view_own" in existing
        or "crm.daily_report.edit_own" in existing
        or "crm.daily_report.view_all" in existing
    ):
        nav.add("nav.sales")
        nav.add("nav.sales.crm_daily_report")
    if "ledger.accounts.view" in existing or "ledger.accounts.manage" in existing:
        nav.add("nav.ledger")
        nav.add("nav.ledger.accounts")
    if (
        "general_journal.view" in existing
        or "general_journal.manage" in existing
        or "general_journal.post" in existing
        or "general_journal.reverse" in existing
    ):
        nav.add("nav.ledger")
        nav.add("nav.ledger.general_journal")
    if (
        "opening_balance.view" in existing
        or "opening_balance.manage" in existing
        or "opening_balance.post" in existing
        or "opening_balance.reverse" in existing
    ):
        nav.add("nav.ledger")
        nav.add("nav.ledger.opening_balance")
    if "general_ledger.view" in existing:
        nav.add("nav.ledger")
        nav.add("nav.ledger.general_ledger")
    if "accounts_receivable.view" in existing:
        nav.add("nav.ledger")
        nav.add("nav.ledger.accounts_receivable")
    if "accounts_payable.view" in existing:
        nav.add("nav.ledger")
        nav.add("nav.ledger.accounts_payable")
    if "trial_balance.view" in existing:
        nav.add("nav.ledger")
        nav.add("nav.ledger.trial_balance")
    if "profit_loss.view" in existing:
        nav.add("nav.ledger")
        nav.add("nav.ledger.profit_loss")
    if "balance_sheet.view" in existing:
        nav.add("nav.ledger")
        nav.add("nav.ledger.balance_sheet")
    if (
        "accounting.period.view" in existing
        or "accounting.period.close" in existing
        or "accounting.period.reopen" in existing
    ):
        nav.add("nav.ledger")
        nav.add("nav.ledger.accounting_periods")
    if (
        "cash_bank.view" in existing
        or "cash_bank.manage" in existing
        or "cash_bank.post" in existing
        or "cash_bank.reverse" in existing
        or "cash_bank.reconcile" in existing
    ):
        nav.add("nav.cash_bank")
        nav.add("nav.cash_bank.accounts")
        nav.add("nav.cash_bank.receipts")
        nav.add("nav.cash_bank.payments")
        nav.add("nav.cash_bank.transfers")
        nav.add("nav.cash_bank.mutasi")
        # Rekonsiliasi: only when view or reconcile (not auto from post/reverse alone
        # beyond the shared parent — mutasi is read-only via view).
        nav.add("nav.cash_bank.reconciliation")
    if any(str(k).startswith("spk.") for k in existing):
        nav.add("nav.spk")
    return nav


def nav_is_configured(perms: set) -> bool:
    return NAV_CONFIGURED in perms or any(k in perms for k in NAV_PERMISSION_KEYS)


def with_nav_defaults(perms: set) -> set:
    """If a role has never stored nav.* keys, fill them from existing access.

    Permintaan Produk list/detail is open to every authenticated user, so the
    child menu key stays visible even for custom roles that already stamped
    nav.*. This must not grant parent ``nav.sales`` (or other sales children),
    nor create / feedback / update_status.
    """
    base = set(perms or [])
    # Business capabilities always generate matching menu visibility. Legacy
    # nav.configured remains stored for compatibility but cannot hide a page
    # whose functional permission was explicitly granted.
    base |= derived_nav_permissions(base)
    base.add("nav.sales.product_requests")
    if (
        "pra_po.create" in base
        or "pra_po.view_own" in base
        or "pra_po.view_all" in base
        or "pra_po.verify" in base
    ):
        base.add("nav.sales.pra_po")
    # A stored nav.sales.sales_order must not outlive the list gate. This does
    # not remove nav.sales or any other Penjualan child, and it does not grant
    # a data permission so the menu can stay open.
    if not any(key in base for key in SALES_ORDER_PAGE_KEYS):
        base.discard("nav.sales.sales_order")
    return base

# Default permission set per role. Admin gets everything except OPT_IN_ONLY.
ROLE_DEFAULT_PERMISSIONS = {
    "admin": list(DEFAULT_GRANT_PERMISSIONS),
    "head_production": [
        "po.view", "po.edit", "sales_order.view", "sales_order.create",
        "pra_po.view_all", "pra_po.verify", "pra_po.return", "pra_po.audit.view",
        "sales.delivery.view", "sales.delivery.create", "sales.delivery.print",
        "sales.invoice.view", "sales.invoice.create", "sales.invoice.edit",
        "sales.receipt.view", "sales.receipt.create",
        "design.view", "design.approve", "design.bypass_approval",
        "payment.view", "payment.approve", "payment.bypass_approval",
        "schedule.view", "schedule.edit",
        "schedule.work_date.view", "schedule.work_date.edit",
        "workflow.rollback",
        "screen.update", "helper.prepare",
        "operator.update", "operator.sample_upload", "operator.sample_approve",
        "packing.update",
        "delivery_note.view", "delivery_note.view_file", "delivery_note.manage",
        "loading.update", "loading.delete", "delivery.update", "urgent.manage",
        "buffer_stock.manage",
        "users.view", "audit.view",
        "dashboard.production.view", "dashboard.production.input", "dashboard.production.edit",
        "dashboard.production.export",
        "dashboard.production.urgent_requests.view",
        "work_panel.view", "settings.work_panel.view", "settings.work_panel.manage",
        "customer_approval.view", "customer_approval.manage", "customer_approval.settings",
        "delivery.deadline.edit", "delivery.deadline.settings",
        "delivery.reschedule", "delivery.reschedule.set_date", "delivery.schedule.view",
        "delivery.location.manage",
        "delivery.vehicle.manage",
        "production.route.edit",
        "current_stock.view", "current_stock.update", "current_stock.import", "current_stock.export",
        "purchase_receipts.view", "purchase_receipts.sync", "purchase_receipts.export",
        "purchase_receipts.receive",
        "purchase_requests.view", "purchase_requests.create",
        "purchase_orders.view", "purchase_orders.create", "purchase_orders.edit_branch",
        "purchase_invoices.view", "purchase_invoices.create", "purchase_invoices.edit",
        "purchase_payments.view", "purchase_payments.create",
        "purchase_hpp.view",
        "settings.hpp.view", "settings.hpp.manage",
        "reports.gaji_operator.view",
        "reports.gaji_operator.repair_work_date",
        "reports.gaji_operator.repair_rate",
        "reports.order_prediction.view", "reports.order_prediction.import",
        "settings.operator_wage.view", "settings.operator_wage.manage",
    ],
    "head_operations": [
        "po.view", "po.edit", "po.cancel", "sales_order.view", "sales_order.create",
        "pra_po.view_all", "pra_po.verify", "pra_po.return", "pra_po.audit.view",
        "sales.customer.view", "sales.customer.manage", "sales.customer.edit",
        "sales.customer.manage_status",
        "sales.delivery.view", "sales.delivery.create", "sales.delivery.print",
        "sales.invoice.view", "sales.invoice.create", "sales.invoice.edit",
        "sales.receipt.view", "sales.receipt.create",
        "schedule.view", "schedule.edit",
        "schedule.work_date.view", "schedule.work_date.edit",
        "delivery_note.view", "delivery_note.view_file", "delivery_note.manage",
        "loading.update", "loading.delete", "delivery.update", "urgent.manage",
        "buffer_stock.manage",
        "users.view", "audit.view",
        "dashboard.production.view", "dashboard.production.input", "dashboard.production.edit",
        "dashboard.production.export",
        "work_panel.view", "settings.work_panel.view", "settings.work_panel.manage",
        "delivery.deadline.edit", "delivery.deadline.settings",
        "delivery.reschedule", "delivery.reschedule.set_date", "delivery.schedule.view",
        "delivery.location.manage",
        "delivery.vehicle.manage",
        "current_stock.view", "current_stock.update", "current_stock.import", "current_stock.export",
        "purchase_receipts.view", "purchase_receipts.sync", "purchase_receipts.export",
        "purchase_receipts.receive",
        "purchase_requests.view", "purchase_requests.create",
        "purchase_orders.view", "purchase_orders.create", "purchase_orders.edit_branch",
        "purchase_invoices.view", "purchase_invoices.create", "purchase_invoices.edit",
        "purchase_payments.view", "purchase_payments.create",
        "purchase_hpp.view",
        "settings.hpp.view", "settings.hpp.manage",
        "reports.gaji_operator.view",
        "reports.gaji_operator.repair_work_date",
        "reports.gaji_operator.repair_rate",
        "reports.order_prediction.view", "reports.order_prediction.import",
        "settings.operator_wage.view", "settings.operator_wage.manage",
    ],
    "designer": [
        "po.view",
        "design.view", "design.upload", "design.approve",
        "schedule.view", "schedule.work_date.view",
        "customer_approval.view", "customer_approval.manage",
    ],
    "screener": [
        "po.view", "schedule.view", "schedule.work_date.view", "screen.update", "work_panel.view",
    ],
    "helper": [
        "po.view", "schedule.view", "schedule.work_date.view", "helper.prepare", "packing.update", "work_panel.view",
    ],
    "operator": [
        "po.view", "schedule.view", "schedule.work_date.view",
        "operator.update", "operator.sample_upload", "work_panel.view",
    ],
    "driver": [
        "po.view", "schedule.view", "schedule.work_date.view",
        "delivery_note.view", "delivery_note.view_file", "loading.update", "delivery.update", "work_panel.view",
        "delivery.reschedule",
    ],
    "crm": [
        "po.view", "sales_order.view",
        "pra_po.create", "pra_po.edit_own", "pra_po.view_own", "pra_po.submit", "pra_po.audit.view",
        "sales.delivery.view", "sales.delivery.create", "sales.delivery.print",
        "sales.invoice.view", "sales.invoice.create", "sales.invoice.edit",
        "sales.receipt.view", "sales.receipt.create",
        "design.view", "payment.view",
        "schedule.view", "schedule.work_date.view", "delivery_note.view", "delivery_note.manage",
        "customer_approval.view",
        "buffer_stock.manage",
        "reports.order_prediction.view", "reports.order_prediction.import",
        "product_request.create", "product_request.read_own_draft",
        "crm.daily_report.create", "crm.daily_report.view_own", "crm.daily_report.edit_own",
        "nav.sales.pra_po",
    ],
    "holding_team": [
        "product_request.read_all",
        "product_request.feedback",
        "product_request.update_status",
        "crm.daily_report.view_all",
        "reports.sales.view", "reports.sales.export",
        "dashboard.owner.view",
        # Recovered Purchasing + Inventory + HPP package (production-only role).
        # Local admin still uses ALL_PERMISSIONS; production non-holding roles
        # lose these keys via services.purchasing_inventory_access filter.
        "purchase_requests.view",
        "purchase_requests.create",
        "purchase_orders.view",
        "purchase_orders.create",
        "purchase_orders.edit_branch",
        "purchase_invoices.view",
        "purchase_invoices.create",
        "purchase_invoices.edit",
        "purchase_payments.view",
        "purchase_payments.create",
        "purchase_receipts.view",
        "purchase_receipts.sync",
        "purchase_receipts.export",
        "purchase_receipts.receive",
        "purchase_hpp.view",
        "master_items.view",
        "master_items.manage",
        "org.branches.view",
        "org.branches.manage",
        "org.warehouses.view",
        "org.warehouses.manage",
        "current_stock.view",
        "current_stock.update",
        "current_stock.import",
        "current_stock.export",
        "inventory_valuation.view",
        "inventory_valuation.export",
        "inventory_valuation.manage_opening",
        "payroll.piece_holding.edit",
    ],
    "direktur_utama": [
        "product_request.read_all",
        "product_request.feedback",
        "product_request.update_status",
        "dashboard.owner.view",
        "reports.gaji_operator.view",
        "reports.gaji_operator.repair_work_date",
        "reports.gaji_operator.repair_rate",
    ],
    "sales_eternal": [
        # PWA Sales is a dedicated scoped surface. PO/financial data remains
        # read-only; the only mutation is recording a visit for an outlet owned
        # by the authenticated sales user.
        "sales.pwa.view",
        "sales.pwa.visit.create",
    ],
}

# Builtin defaults only. A role saved in Role & Hak Akses (role_permissions)
# replaces this list entirely, so production customizations do not gain SPK
# access until an administrator grants the new keys.
_SPK_STAFF = ["spk.view_own", "spk.update_progress", "spk.comment"]
_SPK_HEAD = ["spk.view_division", "spk.assign", "spk.update_progress", "spk.comment", "spk.verify"]
_SPK_DIREKTUR = ["spk.view_all", "spk.create", "spk.assign", "spk.comment", "spk.verify", "spk.cancel"]
_SPK_ROLE_GRANTS = {
    "direktur_utama": _SPK_DIREKTUR,
    "head_production": _SPK_HEAD,
    "head_operations": _SPK_HEAD,
    "designer": _SPK_STAFF,
    "screener": _SPK_STAFF,
    "helper": _SPK_STAFF,
    "operator": _SPK_STAFF,
    "driver": _SPK_STAFF,
    "crm": _SPK_STAFF,
    "sales_eternal": _SPK_STAFF,
}
for _spk_role, _spk_keys in _SPK_ROLE_GRANTS.items():
    ROLE_DEFAULT_PERMISSIONS[_spk_role] = list(ROLE_DEFAULT_PERMISSIONS.get(_spk_role, [])) + list(_spk_keys)

# Builtin roles get derived nav.* defaults so first-load matches current sidebar.
for _role, _keys in list(ROLE_DEFAULT_PERMISSIONS.items()):
    if _role == "admin":
        continue
    ROLE_DEFAULT_PERMISSIONS[_role] = sorted(set(_keys) | derived_nav_permissions(set(_keys)))

for _role, _keys in list(ROLE_DEFAULT_PERMISSIONS.items()):
    ROLE_DEFAULT_PERMISSIONS[_role] = sorted(set(_keys) | {"chat.use", "nav.chat"})

# Fail-closed: opt-in cancellation must never appear in any role default.
for _role, _keys in list(ROLE_DEFAULT_PERMISSIONS.items()):
    ROLE_DEFAULT_PERMISSIONS[_role] = sorted(
        p for p in _keys if p not in OPT_IN_ONLY_PERMISSIONS
    )

# Holding team is an explicit Owner-approved grantee for unmatched purge.
# Re-added after the OPT_IN strip so admin/operator defaults stay clean.
# Direktur receives purge via director_full_permission_set() (ALL_PERMISSIONS).
ROLE_DEFAULT_PERMISSIONS["holding_team"] = sorted(
    set(ROLE_DEFAULT_PERMISSIONS.get("holding_team", []))
    | {"cash_bank.reconcile.purge_unmatched"}
)

# Owner policy: Direktur + Holding start with Urgent dashboard monitor + review/approve.
# Re-added after OPT_IN strip. Does not rewrite stored role_permissions documents;
# customized roles keep their saved lists until an admin toggles Hak Akses.
# Kepala Produksi keeps monitor only (no review/approve) — never re-added here.
# Note: urgent.review unlocks legacy GET /urgent-requests (unscoped list) — intentional
# for org-wide leadership, not for production branch operators.
_URGENT_LEADERSHIP_DEFAULTS = frozenset({
    "dashboard.production.urgent_requests.view",
    "urgent.review",
    "urgent.approve",
})
ROLE_DEFAULT_PERMISSIONS["holding_team"] = sorted(
    set(ROLE_DEFAULT_PERMISSIONS.get("holding_team", [])) | _URGENT_LEADERSHIP_DEFAULTS
)
ROLE_DEFAULT_PERMISSIONS["direktur_utama"] = sorted(
    set(ROLE_DEFAULT_PERMISSIONS.get("direktur_utama", [])) | _URGENT_LEADERSHIP_DEFAULTS
)

ROLE_LABELS = {
  "admin": "Admin",
  "head_production": "Kepala Produksi",
  "head_operations": "Kepala Operasional",
  "designer": "Designer",
  "screener": "Screener",
  "helper": "Helper",
  "operator": "Operator",
  "driver": "Driver",
  "crm": "CRM",
  "holding_team": "Holding",
  "direktur_utama": "Direktur",
  "sales_eternal": "Sales Eternal",
}

# Full-application access role (Owner policy). Runtime ignores shortfall
# role_permissions / deny overrides; Mongo docs are left intact.
DIRECTOR_FULL_ACCESS_ROLE = "direktur_utama"


def director_full_permission_set() -> set:
    """Every registered permission except keys that remain true opt-in for Direktur.

    Phase A ``urgent.*`` keys stay out of the raw full set. ``urgent.review`` /
    ``urgent.approve`` can still land via ``merge_director_must_opt_in`` when the
    builtin ROLE_DEFAULT (or stored role_permissions) lists them.
    """
    return set(ALL_PERMISSIONS) - DIRECTOR_MUST_OPT_IN_PERMISSIONS


def merge_director_must_opt_in(
    base: set,
    role: str,
    override: dict | None,
    role_defaults_override: dict | None = None,
) -> set:
    """Add DIRECTOR_MUST_OPT_IN keys when explicitly granted or builtin-defaulted.

    Priority: per-user False deny → per-user True → stored role_permissions →
    builtin ROLE_DEFAULT_PERMISSIONS (post OPT_IN re-add). Does not invent grants
    for roles whose builtin list omits the key.
    """
    out = set(base or [])
    override = override or {}
    builtin = set(ROLE_DEFAULT_PERMISSIONS.get(role) or [])
    stored = set((role_defaults_override or {}).get(role) or []) if role_defaults_override else set()
    for perm in DIRECTOR_MUST_OPT_IN_PERMISSIONS:
        if override.get(perm) is False:
            out.discard(perm)
            continue
        if override.get(perm) is True:
            out.add(perm)
            continue
        if role_defaults_override is not None and role in role_defaults_override:
            # Customized role doc replaces builtins entirely for that role.
            if perm in stored:
                out.add(perm)
            else:
                out.discard(perm)
            continue
        if perm in builtin:
            out.add(perm)
    return out


def is_director_full_access_role(role: str | None) -> bool:
    return str(role or "").strip().lower() == DIRECTOR_FULL_ACCESS_ROLE

# Stage index → permission required to advance from that stage.
STAGE_PERMISSION = {
    6: "screen.update",          # Screener -> Persiapan Helper
    7: "helper.prepare",         # Persiapan Helper -> Operator
    8: "operator.update",        # Operator -> Konfirmasi Sample
    9: "operator.update",        # Konfirmasi Sample -> Cetak
    10: "operator.update",       # Cetak -> Packing
    11: "packing.update",        # Packing -> Surat Jalan
    # Loading (13) → Terkirim (14) is not completed via /advance; confirm-delivered
    # is authoritative. This key still gates any attempt to advance from 13.
    13: "delivery.update",
}


def loading_granular_is_configured(perms: set) -> bool:
    """True once Role & Hak Akses has stamped explicit Loading action keys."""
    return LOADING_GRANULAR_CONFIGURED in (perms or set())


def with_loading_permission_defaults(perms: set) -> set:
    """Compat expand Loading action keys from legacy loading.update — only until configured.

    Before loading.granular_configured is stamped, roles that already had loading.update
    keep confirm_match / report_discrepancy / view / history without a migration.

    After an administrator saves Role & Hak Akses (stamp present), loading.update alone
    no longer re-grants those actions — explicit disables are honored and revocable.
    """
    base = set(perms or [])
    if not loading_granular_is_configured(base) and "loading.update" in base:
        for key in LOADING_DEFAULTS_FROM_UPDATE:
            base.add(key)
        # resolve_discrepancy stays admin/explicit — not auto-granted from update
    if "loading.confirm_match" in base or "loading.report_discrepancy" in base:
        base.add("loading.view")
    if "loading.view_discrepancy_history" in base:
        base.add("loading.view")
    return base


def effective_permissions(role: str, override: dict | None, role_defaults_override: dict | None = None) -> set:
    """Compute effective permissions for a user.

    role_defaults_override lets admin change the role-defaults at runtime via
    the Roles UI; if None, uses the built-in `ROLE_DEFAULT_PERMISSIONS`.
    Navigation keys that have never been stored for a role are derived from
    existing page-access permissions so old roles keep their current menus.
    """
    if role_defaults_override and role in role_defaults_override:
        base = set(role_defaults_override[role] or [])
    else:
        base = set(ROLE_DEFAULT_PERMISSIONS.get(role, []))
    # Capture role-level generated navigation to revoke it when the user denies
    # the business capability. Legacy stored nav.* must not defeat an explicit deny.
    inherited_auto_nav = derived_nav_permissions(base)
    base = with_loading_permission_defaults(base)
    for perm, allowed in (override or {}).items():
        if allowed:
            base.add(perm)
        else:
            base.discard(perm)
    # Re-apply loading defaults after overrides only while granular keys are not configured,
    # so legacy loading.update still expands; explicit False on a child stays discarded.
    # Once loading.granular_configured is set, do not re-expand — revocation must stick.
    if not loading_granular_is_configured(base):
        if (override or {}).get("loading.update") is True or "loading.update" in base:
            denied = {k for k, v in (override or {}).items() if v is False}
            expanded = with_loading_permission_defaults(base | {"loading.update"})
            for k in expanded:
                if k not in denied:
                    base.add(k)
    # Remove nav keys no longer justified after user-specific denials, even if
    # they were saved as legacy role-level nav grants.
    current_auto_nav = derived_nav_permissions(base)
    denied_business = any(v is False and not k.startswith("nav.") for k, v in (override or {}).items())
    if denied_business:
        for nav_key in inherited_auto_nav - current_auto_nav:
            base.discard(nav_key)
    # Do not let old role-level nav settings resurrect a denied business leaf.
    # Keep parent navigation when other authorized children still need it.
    denied_leaf = {
        "sales.delivery.view": "nav.sales.delivery_order",
        "sales.invoice.view": "nav.sales.sales_invoice",
        "sales.receipt.view": "nav.sales.sales_receipt",
        "dashboard.production.view": "nav.dashboard",
        "schedule.view": "nav.production_schedule",
    }
    for capability, nav_key in denied_leaf.items():
        if (override or {}).get(capability) is False:
            base.discard(nav_key)
    base = with_nav_defaults(base)
    # NAV_ALWAYS_VISIBLE contains legacy sales leaves. An account-level deny
    # must win even when those leaves are reintroduced by nav derivation.
    for capability, nav_key in denied_leaf.items():
        if (override or {}).get(capability) is False:
            base.discard(nav_key)
    # Respect explicit per-user nav denies, including always-visible defaults.
    for nav_key, allowed in (override or {}).items():
        if nav_key.startswith("nav.") and allowed is False:
            base.discard(nav_key)
    return base


def has_permission(user_perms: set, perm: str) -> bool:
    return perm in user_perms


def opt_in_permission_granted(
    role: str,
    override: dict | None,
    perm: str,
    role_defaults_override: dict | None = None,
) -> bool:
    """True only when an OPT_IN_ONLY permission is explicitly granted.

    Role defaults never include opt-in keys. Admin / wildcard role membership
    alone is insufficient — the key must appear via an explicit True override
    or an explicit role_permissions document entry that already lists it.

    ``role_defaults_override`` is the same Hak Akses map used by
    ``require_cancel_permission`` / ``server.user_permissions`` (role → permissions).
    When omitted, only built-in ROLE_DEFAULT is consulted (which never grants opt-in).
    """
    if perm not in OPT_IN_ONLY_PERMISSIONS:
        return False
    override = override or {}
    if override.get(perm) is False:
        return False
    if override.get(perm) is True:
        return True
    # Explicit role default override documents may list the key after an
    # audited Hak Akses save. Built-in ROLE_DEFAULT never includes it.
    return perm in effective_permissions(role, override, role_defaults_override)


def strip_opt_in_permissions(perms) -> set:
    """Remove OPT_IN_ONLY keys from an automatic wildcard grant set."""
    return {p for p in (perms or []) if p not in OPT_IN_ONLY_PERMISSIONS}
