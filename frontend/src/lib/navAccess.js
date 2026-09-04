/** Sidebar / page visibility helpers over existing `hasPerm` + nav.* keys. */

export const NAV_KEYS = {
  dashboard: "nav.dashboard",
  production: "nav.production",
  schedule: "nav.production_schedule",
  deliverySchedule: "nav.delivery_schedule",
  deliveryReschedule: "nav.delivery_reschedule",
  driverRoute: "nav.driver_route",
  tasks: "nav.tasks",
  tasksDesigner: "nav.tasks.designer",
  tasksKepalaProduksi: "nav.tasks.kepala_produksi",
  tasksScreener: "nav.tasks.screener",
  tasksHelper: "nav.tasks.helper",
  tasksOperator: "nav.tasks.operator",
  tasksPacking: "nav.tasks.packing",
  tasksSuratJalan: "nav.tasks.surat_jalan",
  tasksLoading: "nav.tasks.loading",
  tasksDelivery: "nav.tasks.delivery",
  sales: "nav.sales",
  salesCustomers: "nav.sales.customers",
  salesOrder: "nav.sales.sales_order",
  salesDeliveryOrder: "nav.sales.delivery_order",
  salesInvoice: "nav.sales.sales_invoice",
  salesReceipt: "nav.sales.sales_receipt",
  importPo: "nav.sales.import_po",
  salesActivity: "nav.sales.sales_activity",
  purchasing: "nav.purchasing",
  purchaseRequests: "nav.purchasing.requests",
  purchaseOrders: "nav.purchasing.orders",
  purchaseInvoices: "nav.purchasing.invoices",
  purchasePayments: "nav.purchasing.payments",
  purchaseReceipts: "nav.purchasing.receipts",
  inventory: "nav.inventory",
  masterItems: "nav.inventory.master_items",
  currentStock: "nav.inventory.current_stock",
  reports: "nav.reports",
  analytics: "nav.reports.analytics_output",
  shipmentStatus: "nav.reports.shipment_status",
  statusPo: "nav.reports.status_po",
  gajiOperator: "nav.reports.gaji_operator",
  orderPrediction: "nav.reports.order_prediction",
  employeeAdmin: "nav.employee_administration",
  employeeAdminMine: "nav.employee_administration.mine",
  employeeAdminCreate: "nav.employee_administration.create",
  employeeAdminApprove: "nav.employee_administration.approve",
  employeeAdminArchive: "nav.employee_administration.archive",
  hrPayroll: "nav.hr_payroll",
  hrPayrollEmployees: "nav.hr_payroll.employees",
  hrPayrollEmployment: "nav.hr_payroll.employment",
  hrPayrollPayroll: "nav.hr_payroll.payroll",
  hrPayrollPayslips: "nav.hr_payroll.payslips",
  hrPayrollComponents: "nav.hr_payroll.components",
  hrPayrollHistory: "nav.hr_payroll.history",
  routeOptimizer: "nav.route_optimizer",
  settings: "nav.settings",
};

export const NAV_MENU_FALLBACK = [
  { key: NAV_KEYS.dashboard, label: "Dashboard", parent: null },
  { key: NAV_KEYS.production, label: "Produksi", parent: null },
  { key: NAV_KEYS.schedule, label: "Jadwal Produksi", parent: NAV_KEYS.production },
  { key: NAV_KEYS.deliverySchedule, label: "Jadwal Pengiriman", parent: NAV_KEYS.production },
  { key: NAV_KEYS.deliveryReschedule, label: "Jadwal Pengiriman Ulang", parent: NAV_KEYS.production },
  { key: NAV_KEYS.tasks, label: "Tugas Produksi", parent: NAV_KEYS.production },
  { key: NAV_KEYS.tasksDesigner, label: "Designer", parent: NAV_KEYS.tasks },
  { key: NAV_KEYS.tasksScreener, label: "Screener", parent: NAV_KEYS.tasks },
  { key: NAV_KEYS.tasksKepalaProduksi, label: "Kepala Produksi", parent: NAV_KEYS.tasks },
  { key: NAV_KEYS.tasksHelper, label: "Helper", parent: NAV_KEYS.tasks },
  { key: NAV_KEYS.tasksOperator, label: "Operator", parent: NAV_KEYS.tasks },
  { key: NAV_KEYS.tasksPacking, label: "Packing", parent: NAV_KEYS.tasks },
  { key: NAV_KEYS.tasksSuratJalan, label: "Surat Jalan", parent: NAV_KEYS.tasks },
  { key: NAV_KEYS.tasksLoading, label: "Loading", parent: NAV_KEYS.tasks },
  { key: NAV_KEYS.tasksDelivery, label: "Driver / Terkirim", parent: NAV_KEYS.tasks },
  { key: NAV_KEYS.sales, label: "Penjualan", parent: null },
  { key: NAV_KEYS.salesCustomers, label: "Data Pelanggan", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesOrder, label: "Pesanan Penjualan", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesDeliveryOrder, label: "Pengiriman Pesanan", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesInvoice, label: "Faktur Penjualan", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesReceipt, label: "Penerimaan Penjualan", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.importPo, label: "Import PO", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesActivity, label: "Aktivitas Sales", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.purchasing, label: "Pembelian", parent: null },
  { key: NAV_KEYS.purchaseRequests, label: "Permintaan", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.purchaseOrders, label: "Pesanan Pembelian", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.purchaseInvoices, label: "Faktur Pembelian", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.purchasePayments, label: "Pembayaran Pembelian", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.purchaseReceipts, label: "Penerimaan Barang", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.inventory, label: "Persediaan", parent: null },
  { key: NAV_KEYS.masterItems, label: "Barang/Jasa", parent: NAV_KEYS.inventory },
  { key: NAV_KEYS.currentStock, label: "Stock Saat Ini", parent: NAV_KEYS.inventory },
  { key: NAV_KEYS.reports, label: "Laporan", parent: null },
  { key: NAV_KEYS.analytics, label: "Analitik Output", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.statusPo, label: "Status PO", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.shipmentStatus, label: "Status Pengiriman", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.gajiOperator, label: "Gaji Operator", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.orderPrediction, label: "Prediksi Order", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.employeeAdmin, label: "Administrasi Karyawan", parent: null },
  { key: NAV_KEYS.employeeAdminMine, label: "Pengajuan Saya", parent: NAV_KEYS.employeeAdmin },
  { key: NAV_KEYS.employeeAdminCreate, label: "Buat Pengajuan", parent: NAV_KEYS.employeeAdmin },
  { key: NAV_KEYS.employeeAdminApprove, label: "Persetujuan", parent: NAV_KEYS.employeeAdmin },
  { key: NAV_KEYS.employeeAdminArchive, label: "Arsip Dokumen", parent: NAV_KEYS.employeeAdmin },
  { key: NAV_KEYS.hrPayroll, label: "HR & Payroll", parent: null },
  { key: NAV_KEYS.hrPayrollEmployees, label: "Daftar Karyawan", parent: NAV_KEYS.hrPayroll },
  { key: NAV_KEYS.hrPayrollEmployment, label: "Data Kepegawaian", parent: NAV_KEYS.hrPayroll },
  { key: NAV_KEYS.hrPayrollPayroll, label: "Penggajian", parent: NAV_KEYS.hrPayroll },
  { key: NAV_KEYS.hrPayrollPayslips, label: "Slip Gaji", parent: NAV_KEYS.hrPayroll },
  { key: NAV_KEYS.hrPayrollComponents, label: "Komponen Gaji", parent: NAV_KEYS.hrPayroll },
  { key: NAV_KEYS.hrPayrollHistory, label: "Riwayat Payroll", parent: NAV_KEYS.hrPayroll },
  { key: NAV_KEYS.routeOptimizer, label: "Optimasi Rute", parent: null },
  { key: NAV_KEYS.settings, label: "Pengaturan", parent: null },
];

export function canSeeNav(hasPerm, key) {
  return typeof hasPerm === "function" ? !!hasPerm(key) : false;
}

/** Parent that is not a page itself is hidden when no child is visible. */
export function salesNavVisible(hasPerm) {
  const parent = canSeeNav(hasPerm, NAV_KEYS.sales);
  const salesCustomers = parent && (canSeeNav(hasPerm, NAV_KEYS.salesCustomers) || canSeeNav(hasPerm, "sales.customer.view") || canSeeNav(hasPerm, "sales.customer.manage") || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "sales_order.create") || canSeeNav(hasPerm, "po.view"));
  const salesOrder = parent && (canSeeNav(hasPerm, NAV_KEYS.salesOrder) || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "sales_order.create") || canSeeNav(hasPerm, "po.view"));
  const salesDeliveryOrder = parent && (canSeeNav(hasPerm, NAV_KEYS.salesDeliveryOrder) || canSeeNav(hasPerm, "sales.delivery.view") || canSeeNav(hasPerm, "sales.delivery.create") || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "po.view"));
  const salesInvoice = parent && (canSeeNav(hasPerm, NAV_KEYS.salesInvoice) || canSeeNav(hasPerm, "sales.invoice.view") || canSeeNav(hasPerm, "sales.invoice.create") || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "po.view"));
  const salesReceipt = parent && (canSeeNav(hasPerm, NAV_KEYS.salesReceipt) || canSeeNav(hasPerm, "sales.receipt.view") || canSeeNav(hasPerm, "sales.receipt.create") || canSeeNav(hasPerm, "sales.invoice.view") || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "po.view"));
  const importPo = parent && (canSeeNav(hasPerm, NAV_KEYS.importPo) || canSeeNav(hasPerm, "po.import.view"));
  const salesActivity = parent && (canSeeNav(hasPerm, NAV_KEYS.salesActivity) || canSeeNav(hasPerm, "sales.activity.view") || canSeeNav(hasPerm, "sales.activity.create") || canSeeNav(hasPerm, "sales.outlet.create"));
  return {
    parent: Boolean(salesCustomers || salesOrder || salesDeliveryOrder || salesInvoice || salesReceipt || importPo || salesActivity),
    salesCustomers: Boolean(salesCustomers),
    salesOrder: Boolean(salesOrder),
    salesDeliveryOrder: Boolean(salesDeliveryOrder),
    salesInvoice: Boolean(salesInvoice),
    salesReceipt: Boolean(salesReceipt),
    importPo: Boolean(importPo),
    salesActivity: Boolean(salesActivity),
  };
}

/** Existing Penjualan child routes. */
export const SALES_NAV_ROUTES = {
  [NAV_KEYS.salesCustomers]: "/penjualan/data-pelanggan",
  [NAV_KEYS.salesOrder]: "/penjualan/pesanan-penjualan",
  [NAV_KEYS.salesDeliveryOrder]: "/penjualan/pengiriman-pesanan",
  [NAV_KEYS.salesInvoice]: "/penjualan/faktur-penjualan",
  [NAV_KEYS.salesReceipt]: "/penjualan/penerimaan-penjualan",
  [NAV_KEYS.importPo]: "/admin/import",
  [NAV_KEYS.salesActivity]: "/penjualan/aktivitas-sales",
};

const SALES_VIS_FIELD = {
  [NAV_KEYS.salesCustomers]: "salesCustomers",
  [NAV_KEYS.salesOrder]: "salesOrder",
  [NAV_KEYS.salesDeliveryOrder]: "salesDeliveryOrder",
  [NAV_KEYS.salesInvoice]: "salesInvoice",
  [NAV_KEYS.salesReceipt]: "salesReceipt",
  [NAV_KEYS.importPo]: "importPo",
  [NAV_KEYS.salesActivity]: "salesActivity",
};

/** Stable tile tones keyed by existing nav.* — not randomized per render. */
export const SALES_TILE_TONES = {
  [NAV_KEYS.salesCustomers]: "teal",
  [NAV_KEYS.salesOrder]: "blue",
  [NAV_KEYS.salesDeliveryOrder]: "amber",
  [NAV_KEYS.salesInvoice]: "purple",
  [NAV_KEYS.salesReceipt]: "emerald",
  [NAV_KEYS.importPo]: "green",
  [NAV_KEYS.salesActivity]: "blue",
};

export const SALES_TILE_TESTIDS = {
  [NAV_KEYS.salesCustomers]: "top-nav-sales-customers",
  [NAV_KEYS.salesOrder]: "top-nav-sales-order",
  [NAV_KEYS.salesDeliveryOrder]: "top-nav-sales-delivery-order",
  [NAV_KEYS.salesInvoice]: "top-nav-sales-invoice",
  [NAV_KEYS.salesReceipt]: "top-nav-sales-receipt",
  [NAV_KEYS.importPo]: "top-nav-import",
  [NAV_KEYS.salesActivity]: "top-nav-sales-activity",
};

function menuItemsFromFallback(hasPerm, parentKey, visible, fieldMap, routes, tones, testids, extras = {}) {
  return NAV_MENU_FALLBACK.filter((item) => {
    if (item.parent !== parentKey) return false;
    const field = fieldMap[item.key];
    return !!(field && visible[field] && routes[item.key]);
  }).map((item) => ({
    key: item.key,
    label: item.label,
    to: routes[item.key],
    tone: tones[item.key] || "green",
    testid: testids[item.key] || `top-nav-${item.key}`,
    ...(extras[item.key] || {}),
  }));
}

/** Child tiles from NAV_MENU_FALLBACK, filtered by salesNavVisible + known routes. */
export function salesMenuItems(hasPerm) {
  return menuItemsFromFallback(
    hasPerm,
    NAV_KEYS.sales,
    salesNavVisible(hasPerm),
    SALES_VIS_FIELD,
    SALES_NAV_ROUTES,
    SALES_TILE_TONES,
    SALES_TILE_TESTIDS,
  );
}

export const PURCHASING_NAV_ROUTES = {
  [NAV_KEYS.purchaseRequests]: "/pembelian/permintaan",
  [NAV_KEYS.purchaseOrders]: "/pembelian/pesanan-pembelian",
  [NAV_KEYS.purchaseInvoices]: "/pembelian/faktur-pembelian",
  [NAV_KEYS.purchasePayments]: "/pembelian/pembayaran-pembelian",
  [NAV_KEYS.purchaseReceipts]: "/pembelian/penerimaan-barang",
};

const PURCHASING_VIS_FIELD = {
  [NAV_KEYS.purchaseRequests]: "requests",
  [NAV_KEYS.purchaseOrders]: "orders",
  [NAV_KEYS.purchaseInvoices]: "invoices",
  [NAV_KEYS.purchasePayments]: "payments",
  [NAV_KEYS.purchaseReceipts]: "receipts",
};

export const PURCHASING_TILE_TONES = {
  [NAV_KEYS.purchaseRequests]: "green",
  [NAV_KEYS.purchaseOrders]: "blue",
  [NAV_KEYS.purchaseInvoices]: "purple",
  [NAV_KEYS.purchasePayments]: "emerald",
  [NAV_KEYS.purchaseReceipts]: "amber",
};

export const PURCHASING_TILE_TESTIDS = {
  [NAV_KEYS.purchaseRequests]: "top-nav-permintaan",
  [NAV_KEYS.purchaseOrders]: "top-nav-pesanan-pembelian",
  [NAV_KEYS.purchaseInvoices]: "top-nav-faktur-pembelian",
  [NAV_KEYS.purchasePayments]: "top-nav-pembayaran-pembelian",
  [NAV_KEYS.purchaseReceipts]: "top-nav-penerimaan-barang",
};

export function purchasingMenuItems(hasPerm) {
  return menuItemsFromFallback(
    hasPerm,
    NAV_KEYS.purchasing,
    purchasingNavVisible(hasPerm),
    PURCHASING_VIS_FIELD,
    PURCHASING_NAV_ROUTES,
    PURCHASING_TILE_TONES,
    PURCHASING_TILE_TESTIDS,
  );
}

export const INVENTORY_NAV_ROUTES = {
  [NAV_KEYS.masterItems]: "/persediaan/master-barang-jasa",
  [NAV_KEYS.currentStock]: "/persediaan/stock-saat-ini",
};

const INVENTORY_VIS_FIELD = {
  [NAV_KEYS.masterItems]: "masterItems",
  [NAV_KEYS.currentStock]: "currentStock",
};

export const INVENTORY_TILE_TONES = {
  [NAV_KEYS.masterItems]: "green",
  [NAV_KEYS.currentStock]: "blue",
};

export const INVENTORY_TILE_TESTIDS = {
  [NAV_KEYS.masterItems]: "top-nav-master-barang-jasa",
  [NAV_KEYS.currentStock]: "top-nav-stock-saat-ini",
};

export function inventoryMenuItems(hasPerm) {
  return menuItemsFromFallback(
    hasPerm,
    NAV_KEYS.inventory,
    inventoryNavVisible(hasPerm),
    INVENTORY_VIS_FIELD,
    INVENTORY_NAV_ROUTES,
    INVENTORY_TILE_TONES,
    INVENTORY_TILE_TESTIDS,
  );
}

export const REPORTS_NAV_ROUTES = {
  [NAV_KEYS.analytics]: "/laporan/analitik-output",
  [NAV_KEYS.statusPo]: "/laporan/status-po",
  [NAV_KEYS.shipmentStatus]: "/laporan/status-pengiriman",
  [NAV_KEYS.gajiOperator]: "/laporan/gaji-operator",
  [NAV_KEYS.orderPrediction]: "/laporan/prediksi-order",
};

const REPORTS_VIS_FIELD = {
  [NAV_KEYS.analytics]: "analytics",
  [NAV_KEYS.statusPo]: "statusPo",
  [NAV_KEYS.shipmentStatus]: "shipmentStatus",
  [NAV_KEYS.gajiOperator]: "gajiOperator",
  [NAV_KEYS.orderPrediction]: "orderPrediction",
};

export const REPORTS_TILE_TONES = {
  [NAV_KEYS.analytics]: "green",
  [NAV_KEYS.statusPo]: "blue",
  [NAV_KEYS.shipmentStatus]: "orange",
  [NAV_KEYS.gajiOperator]: "purple",
  [NAV_KEYS.orderPrediction]: "green",
};

export const REPORTS_TILE_TESTIDS = {
  [NAV_KEYS.analytics]: "top-nav-analitik-output",
  [NAV_KEYS.statusPo]: "top-nav-status-po",
  [NAV_KEYS.shipmentStatus]: "top-nav-status-pengiriman",
  [NAV_KEYS.gajiOperator]: "top-nav-gaji-operator",
  [NAV_KEYS.orderPrediction]: "top-nav-prediksi-order",
};

export function reportsMenuItems(hasPerm) {
  return menuItemsFromFallback(
    hasPerm,
    NAV_KEYS.reports,
    reportsNavVisible(hasPerm),
    REPORTS_VIS_FIELD,
    REPORTS_NAV_ROUTES,
    REPORTS_TILE_TONES,
    REPORTS_TILE_TESTIDS,
  );
}

export const PRODUCTION_NAV_ROUTES = {
  [NAV_KEYS.schedule]: "/produksi",
  [NAV_KEYS.deliverySchedule]: "/produksi/jadwal-pengiriman",
  [NAV_KEYS.deliveryReschedule]: "/produksi/jadwal-pengiriman-ulang",
};

const PRODUCTION_VIS_FIELD = {
  [NAV_KEYS.schedule]: "schedule",
  [NAV_KEYS.deliverySchedule]: "deliverySchedule",
  [NAV_KEYS.deliveryReschedule]: "deliveryReschedule",
};

export const PRODUCTION_TILE_TONES = {
  [NAV_KEYS.schedule]: "green",
  [NAV_KEYS.deliverySchedule]: "blue",
  [NAV_KEYS.deliveryReschedule]: "orange",
};

export const PRODUCTION_TILE_TESTIDS = {
  [NAV_KEYS.schedule]: "top-nav-jadwal-produksi",
  [NAV_KEYS.deliverySchedule]: "top-nav-jadwal-pengiriman",
  [NAV_KEYS.deliveryReschedule]: "top-nav-jadwal-pengiriman-ulang",
};

/** Existing Produksi child that reuses status PO perm — not a new nav key. */
export const PRODUCTION_TODAY_ITEM = {
  key: "deliveryToday",
  label: "Pengiriman Hari Ini",
  to: "/produksi/pengiriman-hari-ini",
  tone: "purple",
  testid: "top-nav-pengiriman-hari-ini",
};

export function productionMenuItems(hasPerm) {
  const visible = productionNavVisible(hasPerm);
  const items = menuItemsFromFallback(
    hasPerm,
    NAV_KEYS.production,
    visible,
    PRODUCTION_VIS_FIELD,
    PRODUCTION_NAV_ROUTES,
    PRODUCTION_TILE_TONES,
    PRODUCTION_TILE_TESTIDS,
    {
      [NAV_KEYS.schedule]: { end: true },
      [NAV_KEYS.deliverySchedule]: { end: true },
    },
  );
  if (visible.deliveryToday && visible.parent) {
    const afterSchedule = items.findIndex((item) => item.key === NAV_KEYS.schedule);
    items.splice(afterSchedule >= 0 ? afterSchedule + 1 : 0, 0, { ...PRODUCTION_TODAY_ITEM });
  }
  return items;
}

export function reportsNavVisible(hasPerm) {
  const parent = canSeeNav(hasPerm, NAV_KEYS.reports);
  const analytics = parent && canSeeNav(hasPerm, NAV_KEYS.analytics);
  const statusPo = parent && canSeeNav(hasPerm, NAV_KEYS.statusPo);
  const shipmentStatus = parent && canSeeNav(hasPerm, NAV_KEYS.shipmentStatus);
  const gajiOperator = parent && canSeeNav(hasPerm, NAV_KEYS.gajiOperator);
  const orderPrediction = parent && canSeeNav(hasPerm, NAV_KEYS.orderPrediction);
  return { parent: !!(analytics || statusPo || shipmentStatus || gajiOperator || orderPrediction), analytics, statusPo, shipmentStatus, gajiOperator, orderPrediction };
}

export function purchasingNavVisible(hasPerm) {
  const parent = canSeeNav(hasPerm, NAV_KEYS.purchasing);
  const requests = parent && (canSeeNav(hasPerm, NAV_KEYS.purchaseRequests) || canSeeNav(hasPerm, "purchase_requests.view") || canSeeNav(hasPerm, "purchase_requests.create"));
  const orders = parent && (canSeeNav(hasPerm, NAV_KEYS.purchaseOrders) || canSeeNav(hasPerm, "purchase_orders.view") || canSeeNav(hasPerm, "purchase_orders.create"));
  const invoices = parent && (canSeeNav(hasPerm, NAV_KEYS.purchaseInvoices) || canSeeNav(hasPerm, "purchase_invoices.view") || canSeeNav(hasPerm, "purchase_invoices.create"));
  const payments = parent && (canSeeNav(hasPerm, NAV_KEYS.purchasePayments) || canSeeNav(hasPerm, "purchase_payments.view") || canSeeNav(hasPerm, "purchase_payments.create"));
  const receipts = parent && canSeeNav(hasPerm, NAV_KEYS.purchaseReceipts);
  return {
    parent: !!(requests || orders || invoices || payments || receipts),
    requests: !!requests,
    orders: !!orders,
    invoices: !!invoices,
    payments: !!payments,
    receipts: !!receipts,
  };
}

export function inventoryNavVisible(hasPerm) {
  const parent = canSeeNav(hasPerm, NAV_KEYS.inventory);
  const masterItems = parent && canSeeNav(hasPerm, NAV_KEYS.masterItems);
  const currentStock = parent && canSeeNav(hasPerm, NAV_KEYS.currentStock);
  return { parent: !!(masterItems || currentStock), masterItems, currentStock };
}

export const TASK_NAV_ORDER = [
  "designer",
  "screener",
  "kepala_produksi",
  "helper",
  "operator",
  "packing",
  "surat_jalan",
  "loading",
  "terkirim",
];

const TASK_CHILDREN = [
  ["designer", NAV_KEYS.tasksDesigner],
  ["screener", NAV_KEYS.tasksScreener],
  ["kepala_produksi", NAV_KEYS.tasksKepalaProduksi],
  ["helper", NAV_KEYS.tasksHelper],
  ["operator", NAV_KEYS.tasksOperator],
  ["packing", NAV_KEYS.tasksPacking],
  ["surat_jalan", NAV_KEYS.tasksSuratJalan],
  ["loading", NAV_KEYS.tasksLoading],
  ["terkirim", NAV_KEYS.tasksDelivery],
];

/** Same submit perms used by derived_nav_permissions — not a second RBAC system. */
export const TASK_SUBMIT_PERMS = {
  designer: "design.upload",
  kepala_produksi: "schedule.edit",
  screener: "screen.update",
  helper: "helper.prepare",
  operator: "operator.update",
  packing: "packing.update",
  surat_jalan: "delivery_note.manage",
  loading: "loading.update",
  terkirim: "delivery.update",
};

export const TASK_NAV_PATHS = {
  designer: "/tasks/designer",
  kepala_produksi: "/tasks/kepala-produksi",
  screener: "/tasks/screener",
  helper: "/tasks/helper",
  operator: "/tasks/operator",
  packing: "/tasks/packing",
  surat_jalan: "/tasks/surat-jalan",
  loading: "/tasks/loading",
  terkirim: "/tasks/delivery",
};

export const TASK_NAV_LABELS = {
  designer: "Designer",
  kepala_produksi: "Kepala Produksi",
  screener: "Screener",
  helper: "Helper",
  operator: "Operator",
  packing: "Packing",
  surat_jalan: "Surat Jalan",
  loading: "Loading",
  terkirim: "Driver / Terkirim",
};

/** Parent hidden unless at least one child is granted via nav.* or matching submit perm. */
export function tasksNavVisible(hasPerm) {
  const children = {};
  let any = false;
  for (const [id, key] of TASK_CHILDREN) {
    const on = canSeeNav(hasPerm, key) || canSeeNav(hasPerm, TASK_SUBMIT_PERMS[id]);
    children[id] = on;
    if (on) any = true;
  }
  return { parent: any, ...children };
}

export const TASK_TILE_TONES = {
  designer: "green",
  screener: "blue",
  kepala_produksi: "orange",
  helper: "purple",
  operator: "green",
  packing: "blue",
  surat_jalan: "orange",
  loading: "purple",
  terkirim: "green",
};

/** Existing Tugas Produksi children only — TASK_NAV_ORDER.map keeps current order. */
export function taskMenuItems(hasPerm) {
  const visible = tasksNavVisible(hasPerm);
  return TASK_NAV_ORDER.filter((id) => visible[id]).map((id) => ({
    key: id,
    label: TASK_NAV_LABELS[id],
    to: TASK_NAV_PATHS[id],
    tone: TASK_TILE_TONES[id] || "green",
    testid: `top-nav-tasks-${id}`,
  }));
}

export function productionNavVisible(hasPerm) {
  const schedule = canSeeNav(hasPerm, NAV_KEYS.schedule);
  const deliverySchedule = canSeeNav(hasPerm, NAV_KEYS.deliverySchedule);
  const deliveryReschedule = canSeeNav(hasPerm, NAV_KEYS.deliveryReschedule);
  const deliveryToday = canSeeNav(hasPerm, NAV_KEYS.statusPo);
  const tasks = tasksNavVisible(hasPerm);
  return {
    parent: !!(schedule || deliverySchedule || deliveryReschedule || tasks.parent),
    schedule,
    deliverySchedule,
    deliveryReschedule,
    deliveryToday,
    tasks,
  };
}

export function firstTaskNavPath(hasPerm) {
  const v = tasksNavVisible(hasPerm);
  for (const [id] of TASK_CHILDREN) {
    if (v[id]) return TASK_NAV_PATHS[id];
  }
  return "/";
}

export function actionKeyToTaskPath(actionKey) {
  const map = {
    design_upload: TASK_NAV_PATHS.designer,
    penjadwalan: TASK_NAV_PATHS.kepala_produksi,
    screener: TASK_NAV_PATHS.screener,
    helper: TASK_NAV_PATHS.helper,
    operator_work: TASK_NAV_PATHS.operator,
    cetak_qty: TASK_NAV_PATHS.operator,
    packing: TASK_NAV_PATHS.packing,
    surat_jalan: TASK_NAV_PATHS.surat_jalan,
    loading: TASK_NAV_PATHS.loading,
    terkirim: TASK_NAV_PATHS.terkirim,
    design_approve: null,
    payment_approve: null,
    sample_upload: null,
    sample_approve: null,
  };
  if (Object.prototype.hasOwnProperty.call(map, actionKey)) return map[actionKey];
  return "/tasks";
}

export const EMPLOYEE_ADMIN_NAV_ROUTES = {
  [NAV_KEYS.employeeAdminMine]: "/administrasi-karyawan/pengajuan-saya",
  [NAV_KEYS.employeeAdminCreate]: "/administrasi-karyawan/buat",
  [NAV_KEYS.employeeAdminApprove]: "/administrasi-karyawan/persetujuan",
  [NAV_KEYS.employeeAdminArchive]: "/administrasi-karyawan/arsip",
};

const EMPLOYEE_ADMIN_VIS_FIELD = {
  [NAV_KEYS.employeeAdminMine]: "mine",
  [NAV_KEYS.employeeAdminCreate]: "create",
  [NAV_KEYS.employeeAdminApprove]: "approve",
  [NAV_KEYS.employeeAdminArchive]: "archive",
};

export const EMPLOYEE_ADMIN_TILE_TONES = {
  [NAV_KEYS.employeeAdminMine]: "blue",
  [NAV_KEYS.employeeAdminCreate]: "green",
  [NAV_KEYS.employeeAdminApprove]: "amber",
  [NAV_KEYS.employeeAdminArchive]: "purple",
};

export const EMPLOYEE_ADMIN_TILE_TESTIDS = {
  [NAV_KEYS.employeeAdminMine]: "top-nav-ea-mine",
  [NAV_KEYS.employeeAdminCreate]: "top-nav-ea-create",
  [NAV_KEYS.employeeAdminApprove]: "top-nav-ea-approve",
  [NAV_KEYS.employeeAdminArchive]: "top-nav-ea-archive",
};

export function employeeAdminNavVisible(hasPerm) {
  const mine = canSeeNav(hasPerm, NAV_KEYS.employeeAdminMine) || canSeeNav(hasPerm, "employee_admin.view_own") || canSeeNav(hasPerm, "employee_admin.submit");
  const create = canSeeNav(hasPerm, NAV_KEYS.employeeAdminCreate) || canSeeNav(hasPerm, "employee_admin.submit");
  const approve = canSeeNav(hasPerm, NAV_KEYS.employeeAdminApprove) || canSeeNav(hasPerm, "employee_admin.approve");
  const archive = canSeeNav(hasPerm, NAV_KEYS.employeeAdminArchive) || canSeeNav(hasPerm, "employee_admin.document_view") || canSeeNav(hasPerm, "employee_admin.view_all");
  return {
    parent: Boolean(mine || create || approve || archive || canSeeNav(hasPerm, NAV_KEYS.employeeAdmin)),
    mine: Boolean(mine),
    create: Boolean(create),
    approve: Boolean(approve),
    archive: Boolean(archive),
  };
}

export function employeeAdminMenuItems(hasPerm) {
  return menuItemsFromFallback(
    hasPerm,
    NAV_KEYS.employeeAdmin,
    employeeAdminNavVisible(hasPerm),
    EMPLOYEE_ADMIN_VIS_FIELD,
    EMPLOYEE_ADMIN_NAV_ROUTES,
    EMPLOYEE_ADMIN_TILE_TONES,
    EMPLOYEE_ADMIN_TILE_TESTIDS,
  );
}

export const HR_PAYROLL_NAV_ROUTES = {
  [NAV_KEYS.hrPayrollEmployees]: "/hr-payroll/daftar-karyawan",
  [NAV_KEYS.hrPayrollEmployment]: "/hr-payroll/data-kepegawaian",
  [NAV_KEYS.hrPayrollPayroll]: "/hr-payroll/penggajian",
  [NAV_KEYS.hrPayrollPayslips]: "/hr-payroll/slip-gaji",
  [NAV_KEYS.hrPayrollComponents]: "/hr-payroll/komponen-gaji",
  [NAV_KEYS.hrPayrollHistory]: "/hr-payroll/riwayat",
};

const HR_PAYROLL_VIS_FIELD = {
  [NAV_KEYS.hrPayrollEmployees]: "employees",
  [NAV_KEYS.hrPayrollEmployment]: "employment",
  [NAV_KEYS.hrPayrollPayroll]: "payroll",
  [NAV_KEYS.hrPayrollPayslips]: "payslips",
  [NAV_KEYS.hrPayrollComponents]: "components",
  [NAV_KEYS.hrPayrollHistory]: "history",
};

export const HR_PAYROLL_TILE_TONES = {
  [NAV_KEYS.hrPayrollEmployees]: "blue",
  [NAV_KEYS.hrPayrollEmployment]: "green",
  [NAV_KEYS.hrPayrollPayroll]: "amber",
  [NAV_KEYS.hrPayrollPayslips]: "emerald",
  [NAV_KEYS.hrPayrollComponents]: "purple",
  [NAV_KEYS.hrPayrollHistory]: "orange",
};

export const HR_PAYROLL_TILE_TESTIDS = {
  [NAV_KEYS.hrPayrollEmployees]: "top-nav-hr-employees",
  [NAV_KEYS.hrPayrollEmployment]: "top-nav-hr-employment",
  [NAV_KEYS.hrPayrollPayroll]: "top-nav-hr-payroll",
  [NAV_KEYS.hrPayrollPayslips]: "top-nav-hr-payslips",
  [NAV_KEYS.hrPayrollComponents]: "top-nav-hr-components",
  [NAV_KEYS.hrPayrollHistory]: "top-nav-hr-history",
};

export function hrPayrollNavVisible(hasPerm) {
  const employees = canSeeNav(hasPerm, NAV_KEYS.hrPayrollEmployees)
    || canSeeNav(hasPerm, "hr.employee.view") || canSeeNav(hasPerm, "hr.employee.manage");
  const employment = canSeeNav(hasPerm, NAV_KEYS.hrPayrollEmployment)
    || canSeeNav(hasPerm, "hr.employee.view") || canSeeNav(hasPerm, "hr.employee.manage");
  const payroll = canSeeNav(hasPerm, NAV_KEYS.hrPayrollPayroll)
    || ["payroll.view", "payroll.calculate", "payroll.review", "payroll.approve", "payroll.finalize"].some((p) => canSeeNav(hasPerm, p));
  const payslips = canSeeNav(hasPerm, NAV_KEYS.hrPayrollPayslips)
    || canSeeNav(hasPerm, "payroll.payslip.view_own") || canSeeNav(hasPerm, "payroll.payslip.view_all");
  const components = canSeeNav(hasPerm, NAV_KEYS.hrPayrollComponents)
    || canSeeNav(hasPerm, "payroll.settings") || canSeeNav(hasPerm, "payroll.view");
  const history = canSeeNav(hasPerm, NAV_KEYS.hrPayrollHistory)
    || canSeeNav(hasPerm, "payroll.view") || canSeeNav(hasPerm, "payroll.payslip.view_all");
  return {
    parent: Boolean(employees || employment || payroll || payslips || components || history || canSeeNav(hasPerm, NAV_KEYS.hrPayroll)),
    employees: Boolean(employees),
    employment: Boolean(employment),
    payroll: Boolean(payroll),
    payslips: Boolean(payslips),
    components: Boolean(components),
    history: Boolean(history),
  };
}

export function hrPayrollMenuItems(hasPerm) {
  return menuItemsFromFallback(
    hasPerm,
    NAV_KEYS.hrPayroll,
    hrPayrollNavVisible(hasPerm),
    HR_PAYROLL_VIS_FIELD,
    HR_PAYROLL_NAV_ROUTES,
    HR_PAYROLL_TILE_TONES,
    HR_PAYROLL_TILE_TESTIDS,
  );
}
