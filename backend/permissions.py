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
    ("Import PO", "po.import.view", "Buka halaman Import PO"),
    ("Import PO", "accurate.import", "Preview & Import Single PO dari Accurate"),
    ("Import PO", "accurate.import.bulk", "Preview & Import beberapa Sales Order Accurate sekaligus"),
    ("Import PO", "po.import.pdf", "Import PO dari file PDF"),
    ("Import PO", "po.import.spreadsheet", "Import PO dari file XLSX / CSV"),
    ("Persediaan", "master_items.view", "Lihat Barang/Jasa"),
    ("Persediaan", "master_items.sync", "Sinkronkan Barang/Jasa dari Accurate"),
    ("Persediaan", "current_stock.view", "Lihat Stock Saat Ini"),
    ("Persediaan", "current_stock.update", "Ubah Qty Saat Ini / Pembelian Barang"),
    ("Persediaan", "current_stock.export", "Export Stock Saat Ini"),
    ("Pembelian", "purchase_receipts.view", "Lihat Penerimaan Barang"),
    ("Pembelian", "purchase_receipts.sync", "Sinkronkan Penerimaan Barang dari Accurate"),
    ("Pembelian", "purchase_receipts.export", "Export Penerimaan Barang"),
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
    ("Pengaturan", "settings.work_panel.view", "Lihat pengaturan Form Pekerjaan Divisi"),
    ("Pengaturan", "settings.work_panel.manage", "Ubah pengaturan Form Pekerjaan Divisi"),
    ("Laporan", "reports.gaji_operator.view", "Lihat Gaji Operator"),
    ("Pengaturan", "settings.operator_wage.view", "Lihat Upah Operator"),
    ("Pengaturan", "settings.operator_wage.manage", "Ubah Upah Operator"),
    ("Packing", "packing.update", "Packing"),
    ("Surat Jalan", "delivery_note.view", "Lihat Surat Jalan"),
    ("Surat Jalan", "delivery_note.view_file", "Lihat File Surat Jalan"),
    ("Surat Jalan", "delivery_note.manage", "Kelola Surat Jalan"),
    ("Surat Jalan", "delivery_note.delete_file", "Hapus File Surat Jalan"),
    ("Loading", "loading.update", "Konfirmasi Loading"),
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
    ("Prioritas", "urgent.manage", "Set/hapus urgent"),
    ("PO", "buffer_stock.manage", "Set/hapus Buffer Stock"),
    ("User & Karyawan", "users.view", "Lihat data karyawan"),
    ("User & Karyawan", "users.manage", "Kelola karyawan"),
    ("Role & Permission", "roles.manage", "Ubah role user"),
    ("Role & Permission", "permissions.manage", "Ubah default permission role"),
    ("Role & Permission", "super_admin.manage", "Kelola Super Admin"),
    ("Dashboard Produksi", "dashboard.production.view", "Lihat Dashboard Produksi"),
    ("Dashboard Produksi", "dashboard.production.input", "Input data dari Dashboard"),
    ("Dashboard Produksi", "dashboard.production.edit", "Edit data dari Dashboard"),
    ("Dashboard Produksi", "dashboard.production.export", "Export Excel Dashboard Jadwal Produksi"),
    ("Audit", "audit.view", "Lihat audit log"),
    # Customer Approval (design)
    ("Customer Approval", "customer_approval.view", "Lihat Customer Approval"),
    ("Customer Approval", "customer_approval.manage", "Kelola Link Customer Approval"),
    ("Customer Approval", "customer_approval.settings", "Atur Customer Approval"),
    # Sidebar / menu visibility (additional layer; does not replace API RBAC)
    ("Akses Menu", "nav.dashboard", "Menu Dashboard"),
    ("Akses Menu", "nav.production", "Menu Produksi"),
    ("Akses Menu", "nav.production_schedule", "Menu Jadwal Produksi"),
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
    ("Akses Menu", "nav.sales.import_po", "Menu Import PO"),
    ("Akses Menu", "nav.purchasing", "Menu Pembelian"),
    ("Akses Menu", "nav.purchasing.receipts", "Menu Penerimaan Barang"),
    ("Akses Menu", "nav.inventory", "Menu Persediaan"),
    ("Akses Menu", "nav.inventory.master_items", "Menu Barang/Jasa"),
    ("Akses Menu", "nav.inventory.current_stock", "Menu Stock Saat Ini"),
    ("Akses Menu", "nav.reports", "Menu Laporan"),
    ("Akses Menu", "nav.reports.analytics_output", "Menu Analitik Output"),
    ("Akses Menu", "nav.reports.shipment_status", "Menu Status Pengiriman"),
    ("Akses Menu", "nav.reports.status_po", "Menu Status PO"),
    ("Akses Menu", "nav.reports.gaji_operator", "Menu Gaji Operator"),
    ("Akses Menu", "nav.settings", "Menu Pengaturan"),
    ("Akses Menu", "nav.configured", "Akses menu sudah dikonfigurasi"),
]

ALL_PERMISSIONS = tuple(p for _mod, p, _lbl in PERMISSION_CATALOG)

# Sidebar visibility keys (excludes the internal "already configured" sentinel).
NAV_PERMISSION_KEYS = tuple(
    p for _mod, p, _lbl in PERMISSION_CATALOG
    if p.startswith("nav.") and p != "nav.configured"
)
NAV_CONFIGURED = "nav.configured"

# Menus that were previously always visible in the sidebar. Used only to derive
# defaults for roles that have not stored any nav.* keys yet.
NAV_ALWAYS_VISIBLE = (
    "nav.sales",
    "nav.sales.import_po",
    "nav.purchasing",
    "nav.inventory",
    "nav.inventory.master_items",
    "nav.reports",
    "nav.reports.analytics_output",
    "nav.settings",
)

NAV_MENU_ITEMS = (
    ("nav.dashboard", "Dashboard", None),
    ("nav.production", "Produksi", None),
    ("nav.production_schedule", "Jadwal Produksi", "nav.production"),
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
    ("nav.sales.import_po", "Import PO", "nav.sales"),
    ("nav.purchasing", "Pembelian", None),
    ("nav.purchasing.receipts", "Penerimaan Barang", "nav.purchasing"),
    ("nav.inventory", "Persediaan", None),
    ("nav.inventory.master_items", "Barang/Jasa", "nav.inventory"),
    ("nav.inventory.current_stock", "Stock Saat Ini", "nav.inventory"),
    ("nav.reports", "Laporan", None),
    ("nav.reports.analytics_output", "Analitik Output", "nav.reports"),
    ("nav.reports.status_po", "Status PO", "nav.reports"),
    ("nav.reports.shipment_status", "Status Pengiriman", "nav.reports"),
    ("nav.reports.gaji_operator", "Gaji Operator", "nav.reports"),
    ("nav.settings", "Pengaturan", None),
)


def derived_nav_permissions(existing: set) -> set:
    """Default sidebar visibility from existing page-access permissions.

    Matches pre-nav sidebar behavior so old roles do not lose menus on deploy.
    """
    nav = set(NAV_ALWAYS_VISIBLE)
    if "dashboard.production.view" in existing:
        nav.add("nav.dashboard")
    if "schedule.view" in existing:
        nav.add("nav.production")
        nav.add("nav.production_schedule")
        nav.add("nav.delivery_schedule")
        nav.add("nav.reports")
        nav.add("nav.reports.shipment_status")
        nav.add("nav.reports.status_po")
        nav.add("nav.reports.gaji_operator")
    if "delivery.schedule.view" in existing:
        nav.add("nav.production")
        nav.add("nav.delivery_schedule")
    if "delivery.reschedule" in existing or "delivery.reschedule.set_date" in existing:
        nav.add("nav.production")
        nav.add("nav.delivery_reschedule")
    if "current_stock.view" in existing:
        nav.add("nav.inventory")
        nav.add("nav.inventory.current_stock")
    if "purchase_receipts.view" in existing:
        nav.add("nav.purchasing")
        nav.add("nav.purchasing.receipts")
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
    if "loading.update" in existing:
        nav.add("nav.production")
        nav.add("nav.tasks")
        nav.add("nav.tasks.loading")
    if "delivery.update" in existing:
        nav.add("nav.production")
        nav.add("nav.tasks")
        nav.add("nav.tasks.delivery")
    return nav


def nav_is_configured(perms: set) -> bool:
    return NAV_CONFIGURED in perms or any(k in perms for k in NAV_PERMISSION_KEYS)


def with_nav_defaults(perms: set) -> set:
    """If a role has never stored nav.* keys, fill them from existing access."""
    base = set(perms or [])
    if nav_is_configured(base):
        return base
    return base | derived_nav_permissions(base)

# Default permission set per role. Admin gets everything.
ROLE_DEFAULT_PERMISSIONS = {
    "admin": list(ALL_PERMISSIONS),
    "head_production": [
        "po.view", "po.edit",
        "design.view", "design.approve", "design.bypass_approval",
        "payment.view", "payment.approve", "payment.bypass_approval",
        "schedule.view", "schedule.edit",
        "schedule.work_date.view", "schedule.work_date.edit",
        "workflow.rollback",
        "screen.update", "helper.prepare",
        "operator.update", "operator.sample_upload", "operator.sample_approve",
        "packing.update",
        "delivery_note.view", "delivery_note.view_file", "delivery_note.manage",
        "loading.update", "delivery.update", "urgent.manage",
        "buffer_stock.manage",
        "users.view", "audit.view",
        "dashboard.production.view", "dashboard.production.input", "dashboard.production.edit",
        "dashboard.production.export",
        "work_panel.view", "settings.work_panel.view", "settings.work_panel.manage",
        "customer_approval.view", "customer_approval.manage", "customer_approval.settings",
        "delivery.deadline.edit", "delivery.deadline.settings",
        "delivery.reschedule", "delivery.reschedule.set_date", "delivery.schedule.view",
        "delivery.location.manage",
        "delivery.vehicle.manage",
        "production.route.edit",
        "current_stock.view", "current_stock.update", "current_stock.export",
        "purchase_receipts.view", "purchase_receipts.sync", "purchase_receipts.export",
        "reports.gaji_operator.view",
        "settings.operator_wage.view", "settings.operator_wage.manage",
    ],
    "head_operations": [
        "po.view", "po.edit", "po.cancel",
        "schedule.view", "schedule.edit",
        "schedule.work_date.view", "schedule.work_date.edit",
        "delivery_note.view", "delivery_note.view_file", "delivery_note.manage",
        "loading.update", "delivery.update", "urgent.manage",
        "buffer_stock.manage",
        "users.view", "audit.view",
        "dashboard.production.view", "dashboard.production.input", "dashboard.production.edit",
        "dashboard.production.export",
        "work_panel.view", "settings.work_panel.view", "settings.work_panel.manage",
        "delivery.deadline.edit", "delivery.deadline.settings",
        "delivery.reschedule", "delivery.reschedule.set_date", "delivery.schedule.view",
        "delivery.location.manage",
        "delivery.vehicle.manage",
        "current_stock.view", "current_stock.update", "current_stock.export",
        "purchase_receipts.view", "purchase_receipts.sync", "purchase_receipts.export",
        "reports.gaji_operator.view",
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
        "po.view", "design.view", "payment.view",
        "schedule.view", "schedule.work_date.view", "delivery_note.view", "delivery_note.manage",
        "customer_approval.view",
        "buffer_stock.manage",
    ],
}

# Builtin roles get derived nav.* defaults so first-load matches current sidebar.
for _role, _keys in list(ROLE_DEFAULT_PERMISSIONS.items()):
    if _role == "admin":
        continue
    ROLE_DEFAULT_PERMISSIONS[_role] = sorted(set(_keys) | derived_nav_permissions(set(_keys)))

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
}

# Stage index → permission required to advance from that stage.
STAGE_PERMISSION = {
    6: "screen.update",          # Screener -> Persiapan Helper
    7: "helper.prepare",         # Persiapan Helper -> Operator
    8: "operator.update",        # Operator -> Konfirmasi Sample
    9: "operator.update",        # Konfirmasi Sample -> Cetak
    10: "operator.update",       # Cetak -> Packing
    11: "packing.update",        # Packing -> Surat Jalan
    14: "delivery.update",       # Surat Jalan / Driver -> Terkirim
}


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
    base = with_nav_defaults(base)
    for perm, allowed in (override or {}).items():
        if allowed:
            base.add(perm)
        else:
            base.discard(perm)
    return base


def has_permission(user_perms: set, perm: str) -> bool:
    return perm in user_perms
