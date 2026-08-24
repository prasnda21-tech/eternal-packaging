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
    ("Packing", "packing.update", "Packing"),
    ("Surat Jalan", "delivery_note.view", "Lihat Surat Jalan"),
    ("Surat Jalan", "delivery_note.manage", "Kelola Surat Jalan"),
    ("Loading", "loading.update", "Loading kendaraan"),
    ("Pengiriman", "delivery.update", "Update status kirim"),
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
]

ALL_PERMISSIONS = tuple(p for _mod, p, _lbl in PERMISSION_CATALOG)

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
        "delivery_note.view", "delivery_note.manage",
        "loading.update", "delivery.update", "urgent.manage",
        "buffer_stock.manage",
        "users.view", "audit.view",
        "dashboard.production.view", "dashboard.production.input", "dashboard.production.edit",
        "dashboard.production.export",
        "customer_approval.view", "customer_approval.manage", "customer_approval.settings",
        "delivery.deadline.edit", "delivery.deadline.settings",
        "production.route.edit",
    ],
    "head_operations": [
        "po.view", "po.edit", "po.cancel",
        "schedule.view", "schedule.edit",
        "schedule.work_date.view", "schedule.work_date.edit",
        "delivery_note.view", "delivery_note.manage",
        "loading.update", "delivery.update", "urgent.manage",
        "buffer_stock.manage",
        "users.view", "audit.view",
        "dashboard.production.view", "dashboard.production.input", "dashboard.production.edit",
        "dashboard.production.export",
        "delivery.deadline.edit", "delivery.deadline.settings",
    ],
    "designer": [
        "po.view",
        "design.view", "design.upload", "design.approve",
        "schedule.view", "schedule.work_date.view",
        "customer_approval.view", "customer_approval.manage",
    ],
    "screener": [
        "po.view", "schedule.view", "schedule.work_date.view", "screen.update",
    ],
    "helper": [
        "po.view", "schedule.view", "schedule.work_date.view", "helper.prepare", "packing.update",
    ],
    "operator": [
        "po.view", "schedule.view", "schedule.work_date.view",
        "operator.update", "operator.sample_upload",
    ],
    "driver": [
        "po.view", "schedule.view", "schedule.work_date.view",
        "delivery_note.view", "loading.update", "delivery.update",
    ],
    "crm": [
        "po.view", "design.view", "payment.view",
        "schedule.view", "schedule.work_date.view", "delivery_note.view",
        "customer_approval.view",
        "buffer_stock.manage",
    ],
}

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
    13: "loading.update",        # Loading -> Driver
    14: "delivery.update",       # Driver -> Terkirim
}


def effective_permissions(role: str, override: dict | None, role_defaults_override: dict | None = None) -> set:
    """Compute effective permissions for a user.

    role_defaults_override lets admin change the role-defaults at runtime via
    the Roles UI; if None, uses the built-in `ROLE_DEFAULT_PERMISSIONS`.
    """
    if role_defaults_override and role in role_defaults_override:
        base = set(role_defaults_override[role] or [])
    else:
        base = set(ROLE_DEFAULT_PERMISSIONS.get(role, []))
    for perm, allowed in (override or {}).items():
        if allowed:
            base.add(perm)
        else:
            base.discard(perm)
    return base


def has_permission(user_perms: set, perm: str) -> bool:
    return perm in user_perms
