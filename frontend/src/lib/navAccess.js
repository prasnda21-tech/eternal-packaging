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
  productRequests: "nav.sales.product_requests",
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
  ledger: "nav.ledger",
  ledgerAccounts: "nav.ledger.accounts",
  ledgerGeneralJournal: "nav.ledger.general_journal",
  ledgerOpeningBalance: "nav.ledger.opening_balance",
  ledgerAccountingPeriods: "nav.ledger.accounting_periods",
  ledgerGeneralLedger: "nav.ledger.general_ledger",
  ledgerAccountsReceivable: "nav.ledger.accounts_receivable",
  ledgerAccountsPayable: "nav.ledger.accounts_payable",
  ledgerTrialBalance: "nav.ledger.trial_balance",
  ledgerProfitLoss: "nav.ledger.profit_loss",
  ledgerBalanceSheet: "nav.ledger.balance_sheet",
  cashBank: "nav.cash_bank",
  cashBankAccounts: "nav.cash_bank.accounts",
  cashBankReceipts: "nav.cash_bank.receipts",
  cashBankPayments: "nav.cash_bank.payments",
  cashBankTransfers: "nav.cash_bank.transfers",
  cashBankMutasi: "nav.cash_bank.mutasi",
  cashBankReconciliation: "nav.cash_bank.reconciliation",
  chat: "nav.chat",
  settings: "nav.settings",
};

export const NAV_MENU_FALLBACK = [
  { key: NAV_KEYS.dashboard, label: "Dashboard", parent: null },
  { key: NAV_KEYS.chat, label: "Chat", parent: null },
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
  { key: NAV_KEYS.productRequests, label: "Permintaan Produk", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesOrder, label: "Pesanan Penjualan", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesDeliveryOrder, label: "Pengiriman Pesanan", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesInvoice, label: "Faktur Penjualan", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesReceipt, label: "Penerimaan Penjualan", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.importPo, label: "Import PO", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesActivity, label: "Aktivitas Sales", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.statusPo, label: "Status PO", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.orderPrediction, label: "Prediksi Order", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.purchasing, label: "Pembelian", parent: null },
  { key: NAV_KEYS.purchaseRequests, label: "Permintaan", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.purchaseOrders, label: "Pesanan Pembelian", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.purchaseReceipts, label: "Penerimaan Barang", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.purchaseInvoices, label: "Faktur Pembelian", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.purchasePayments, label: "Pembayaran Pembelian", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.inventory, label: "Persediaan", parent: null },
  { key: NAV_KEYS.masterItems, label: "Barang/Jasa", parent: NAV_KEYS.inventory },
  { key: NAV_KEYS.currentStock, label: "Stock Saat Ini", parent: NAV_KEYS.inventory },
  { key: NAV_KEYS.reports, label: "Laporan", parent: null },
  { key: NAV_KEYS.analytics, label: "Analitik Output", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.gajiOperator, label: "Gaji Operator", parent: NAV_KEYS.reports },
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
  { key: NAV_KEYS.ledger, label: "Buku Besar", parent: null },
  { key: NAV_KEYS.ledgerAccounts, label: "Akun Perkiraan", parent: NAV_KEYS.ledger },
  { key: NAV_KEYS.ledgerGeneralJournal, label: "Jurnal Umum", parent: NAV_KEYS.ledger },
  { key: NAV_KEYS.ledgerOpeningBalance, label: "Saldo Awal", parent: NAV_KEYS.ledger },
  { key: NAV_KEYS.ledgerAccountingPeriods, label: "Periode Akuntansi", parent: NAV_KEYS.ledger },
  { key: NAV_KEYS.ledgerGeneralLedger, label: "Buku Besar", parent: NAV_KEYS.ledger },
  { key: NAV_KEYS.ledgerAccountsReceivable, label: "Piutang Usaha", parent: NAV_KEYS.ledger },
  { key: NAV_KEYS.ledgerAccountsPayable, label: "Hutang Usaha", parent: NAV_KEYS.ledger },
  { key: NAV_KEYS.ledgerTrialBalance, label: "Neraca Saldo", parent: NAV_KEYS.ledger },
  { key: NAV_KEYS.ledgerProfitLoss, label: "Laba/Rugi", parent: NAV_KEYS.ledger },
  { key: NAV_KEYS.ledgerBalanceSheet, label: "Neraca", parent: NAV_KEYS.ledger },
  { key: NAV_KEYS.cashBank, label: "Kas & Bank", parent: null },
  { key: NAV_KEYS.cashBankAccounts, label: "Daftar Kas & Bank", parent: NAV_KEYS.cashBank },
  { key: NAV_KEYS.cashBankReceipts, label: "Penerimaan", parent: NAV_KEYS.cashBank },
  { key: NAV_KEYS.cashBankPayments, label: "Pembayaran", parent: NAV_KEYS.cashBank },
  { key: NAV_KEYS.cashBankTransfers, label: "Transfer", parent: NAV_KEYS.cashBank },
  { key: NAV_KEYS.cashBankMutasi, label: "Mutasi Kas & Bank", parent: NAV_KEYS.cashBank },
  { key: NAV_KEYS.cashBankReconciliation, label: "Rekonsiliasi Bank", parent: NAV_KEYS.cashBank },
  { key: NAV_KEYS.settings, label: "Pengaturan", parent: null },
];

export function canSeeNav(hasPerm, key) {
  return typeof hasPerm === "function" ? !!hasPerm(key) : false;
}

/** Parent that is not a page itself is hidden when no child is visible. */
export function salesNavVisible(hasPerm) {
  const parent = canSeeNav(hasPerm, NAV_KEYS.sales);
  const salesCustomers = parent && (canSeeNav(hasPerm, NAV_KEYS.salesCustomers) || canSeeNav(hasPerm, "sales.customer.view") || canSeeNav(hasPerm, "sales.customer.manage") || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "sales_order.create") || canSeeNav(hasPerm, "po.view"));
  const productRequests = parent && (canSeeNav(hasPerm, NAV_KEYS.productRequests) || canSeeNav(hasPerm, "product_request.create") || canSeeNav(hasPerm, "product_request.read_own_draft") || canSeeNav(hasPerm, "product_request.read_all"));
  const salesOrder = parent && (canSeeNav(hasPerm, NAV_KEYS.salesOrder) || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "sales_order.create") || canSeeNav(hasPerm, "po.view"));
  const salesDeliveryOrder = parent && (canSeeNav(hasPerm, NAV_KEYS.salesDeliveryOrder) || canSeeNav(hasPerm, "sales.delivery.view") || canSeeNav(hasPerm, "sales.delivery.create") || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "po.view"));
  const salesInvoice = parent && (canSeeNav(hasPerm, NAV_KEYS.salesInvoice) || canSeeNav(hasPerm, "sales.invoice.view") || canSeeNav(hasPerm, "sales.invoice.create") || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "po.view"));
  const salesReceipt = parent && (canSeeNav(hasPerm, NAV_KEYS.salesReceipt) || canSeeNav(hasPerm, "sales.receipt.view") || canSeeNav(hasPerm, "sales.receipt.create") || canSeeNav(hasPerm, "sales.invoice.view") || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "po.view"));
  const importPo = parent && (canSeeNav(hasPerm, NAV_KEYS.importPo) || canSeeNav(hasPerm, "po.import.view"));
  const salesActivity = parent && (canSeeNav(hasPerm, NAV_KEYS.salesActivity) || canSeeNav(hasPerm, "sales.activity.view") || canSeeNav(hasPerm, "sales.activity.create") || canSeeNav(hasPerm, "sales.outlet.create"));
  // Status PO and Prediksi Order remain authorized by their existing reports keys.
  // Only their navigation grouping moves to Penjualan.
  const reportsParent = canSeeNav(hasPerm, NAV_KEYS.reports);
  const statusPo = reportsParent && canSeeNav(hasPerm, NAV_KEYS.statusPo);
  const orderPrediction = reportsParent && canSeeNav(hasPerm, NAV_KEYS.orderPrediction);
  return {
    parent: Boolean(salesCustomers || productRequests || salesOrder || salesDeliveryOrder || salesInvoice || salesReceipt || importPo || salesActivity || statusPo || orderPrediction),
    salesCustomers: Boolean(salesCustomers),
    productRequests: Boolean(productRequests),
    salesOrder: Boolean(salesOrder),
    salesDeliveryOrder: Boolean(salesDeliveryOrder),
    salesInvoice: Boolean(salesInvoice),
    salesReceipt: Boolean(salesReceipt),
    importPo: Boolean(importPo),
    salesActivity: Boolean(salesActivity),
    statusPo: Boolean(statusPo),
    orderPrediction: Boolean(orderPrediction),
  };
}

/** Existing Penjualan child routes. */
export const PRODUCT_REQUEST_LIST_PATH = "/penjualan/permintaan-produk";
export const PRODUCT_REQUEST_NEW_PATH = "/penjualan/permintaan-produk/baru";

export function canCreateProductRequest(hasPerm) {
  return canSeeNav(hasPerm, "product_request.create");
}

export function canReadAllProductRequests(hasPerm) {
  return canSeeNav(hasPerm, "product_request.read_all");
}

export function canReadOwnProductRequestDraft(hasPerm) {
  return canCreateProductRequest(hasPerm) || canSeeNav(hasPerm, "product_request.read_own_draft");
}

export function productRequestEntryPath(hasPerm) {
  if (canReadAllProductRequests(hasPerm)) return PRODUCT_REQUEST_LIST_PATH;
  if (canCreateProductRequest(hasPerm)) return PRODUCT_REQUEST_NEW_PATH;
  return PRODUCT_REQUEST_LIST_PATH;
}

export const SALES_NAV_ROUTES = {
  [NAV_KEYS.salesCustomers]: "/penjualan/data-pelanggan",
  [NAV_KEYS.productRequests]: PRODUCT_REQUEST_LIST_PATH,
  [NAV_KEYS.salesOrder]: "/penjualan/pesanan-penjualan",
  [NAV_KEYS.salesDeliveryOrder]: "/penjualan/pengiriman-pesanan",
  [NAV_KEYS.salesInvoice]: "/penjualan/faktur-penjualan",
  [NAV_KEYS.salesReceipt]: "/penjualan/penerimaan-penjualan",
  [NAV_KEYS.importPo]: "/admin/import",
  [NAV_KEYS.salesActivity]: "/penjualan/aktivitas-sales",
  // Keep legacy report URLs stable for bookmarks and direct links.
  [NAV_KEYS.statusPo]: "/laporan/status-po",
  [NAV_KEYS.orderPrediction]: "/laporan/prediksi-order",
};

const SALES_VIS_FIELD = {
  [NAV_KEYS.salesCustomers]: "salesCustomers",
  [NAV_KEYS.productRequests]: "productRequests",
  [NAV_KEYS.salesOrder]: "salesOrder",
  [NAV_KEYS.salesDeliveryOrder]: "salesDeliveryOrder",
  [NAV_KEYS.salesInvoice]: "salesInvoice",
  [NAV_KEYS.salesReceipt]: "salesReceipt",
  [NAV_KEYS.importPo]: "importPo",
  [NAV_KEYS.salesActivity]: "salesActivity",
  [NAV_KEYS.statusPo]: "statusPo",
  [NAV_KEYS.orderPrediction]: "orderPrediction",
};

/** Stable tile tones keyed by existing nav.* — not randomized per render. */
export const SALES_TILE_TONES = {
  [NAV_KEYS.salesCustomers]: "teal",
  [NAV_KEYS.productRequests]: "green",
  [NAV_KEYS.salesOrder]: "blue",
  [NAV_KEYS.salesDeliveryOrder]: "amber",
  [NAV_KEYS.salesInvoice]: "purple",
  [NAV_KEYS.salesReceipt]: "emerald",
  [NAV_KEYS.importPo]: "green",
  [NAV_KEYS.salesActivity]: "blue",
  [NAV_KEYS.statusPo]: "blue",
  [NAV_KEYS.orderPrediction]: "green",
};

export const SALES_TILE_TESTIDS = {
  [NAV_KEYS.salesCustomers]: "top-nav-sales-customers",
  [NAV_KEYS.productRequests]: "top-nav-product-requests",
  [NAV_KEYS.salesOrder]: "top-nav-sales-order",
  [NAV_KEYS.salesDeliveryOrder]: "top-nav-sales-delivery-order",
  [NAV_KEYS.salesInvoice]: "top-nav-sales-invoice",
  [NAV_KEYS.salesReceipt]: "top-nav-sales-receipt",
  [NAV_KEYS.importPo]: "top-nav-import",
  [NAV_KEYS.salesActivity]: "top-nav-sales-activity",
  [NAV_KEYS.statusPo]: "top-nav-status-po",
  [NAV_KEYS.orderPrediction]: "top-nav-prediksi-order",
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
  ).map((item) => (
    item.key === NAV_KEYS.productRequests
      ? { ...item, to: productRequestEntryPath(hasPerm) }
      : item
  ));
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
  // Status Pengiriman consolidated into Jadwal Pengiriman — no standalone menu route.
  [NAV_KEYS.gajiOperator]: "/laporan/gaji-operator",
  [NAV_KEYS.orderPrediction]: "/laporan/prediksi-order",
};

const REPORTS_VIS_FIELD = {
  [NAV_KEYS.analytics]: "analytics",
  [NAV_KEYS.statusPo]: "statusPo",
  [NAV_KEYS.gajiOperator]: "gajiOperator",
  [NAV_KEYS.orderPrediction]: "orderPrediction",
};

export const REPORTS_TILE_TONES = {
  [NAV_KEYS.analytics]: "green",
  [NAV_KEYS.statusPo]: "blue",
  [NAV_KEYS.gajiOperator]: "purple",
  [NAV_KEYS.orderPrediction]: "green",
};

export const REPORTS_TILE_TESTIDS = {
  [NAV_KEYS.analytics]: "top-nav-analitik-output",
  [NAV_KEYS.statusPo]: "top-nav-status-po",
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

/** Existing Produksi child tiles — Pengiriman Hari Ini menu removed; use Jadwal Pengiriman filter Hari Ini. */
export const PRODUCTION_TODAY_ITEM = null;

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
  return items;
}

export function reportsNavVisible(hasPerm) {
  const parent = canSeeNav(hasPerm, NAV_KEYS.reports);
  const analytics = parent && canSeeNav(hasPerm, NAV_KEYS.analytics);
  const statusPo = parent && canSeeNav(hasPerm, NAV_KEYS.statusPo);
  // Standalone Status Pengiriman menu removed — consolidated into Jadwal Pengiriman.
  const shipmentStatus = false;
  const gajiOperator = parent && canSeeNav(hasPerm, NAV_KEYS.gajiOperator);
  const orderPrediction = parent && canSeeNav(hasPerm, NAV_KEYS.orderPrediction);
  // Status PO and Prediksi Order are still exposed for route/index decisions,
  // but no longer make the Laporan sidebar group visible.
  return { parent: !!(analytics || gajiOperator), analytics, statusPo, shipmentStatus, gajiOperator, orderPrediction };
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
function hasLoadingNavAccess(hasPerm) {
  if (typeof hasPerm !== "function") return false;
  return (
    canSeeNav(hasPerm, TASK_SUBMIT_PERMS.loading)
    || hasPerm("loading.view")
    || hasPerm("loading.confirm_match")
    || hasPerm("loading.report_discrepancy")
    || hasPerm("loading.view_discrepancy_history")
    || hasPerm("loading.resolve_discrepancy")
  );
}

export function tasksNavVisible(hasPerm) {
  const children = {};
  let any = false;
  for (const [id, key] of TASK_CHILDREN) {
    const on = id === "loading"
      ? (canSeeNav(hasPerm, key) || hasLoadingNavAccess(hasPerm))
      : (canSeeNav(hasPerm, key) || canSeeNav(hasPerm, TASK_SUBMIT_PERMS[id]));
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
  const deliveryToday = false; // Pengiriman Hari Ini menu removed — use Jadwal Pengiriman filter.
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

export const LEDGER_NAV_ROUTES = {
  [NAV_KEYS.ledgerAccounts]: "/buku-besar/akun-perkiraan",
  [NAV_KEYS.ledgerGeneralJournal]: "/buku-besar/jurnal-umum",
  [NAV_KEYS.ledgerOpeningBalance]: "/buku-besar/saldo-awal",
  [NAV_KEYS.ledgerAccountingPeriods]: "/buku-besar/periode-akuntansi",
  [NAV_KEYS.ledgerGeneralLedger]: "/buku-besar/buku-besar",
  [NAV_KEYS.ledgerAccountsReceivable]: "/buku-besar/piutang-usaha",
  [NAV_KEYS.ledgerAccountsPayable]: "/buku-besar/hutang-usaha",
  [NAV_KEYS.ledgerTrialBalance]: "/buku-besar/neraca-saldo",
  [NAV_KEYS.ledgerProfitLoss]: "/buku-besar/laba-rugi",
  [NAV_KEYS.ledgerBalanceSheet]: "/buku-besar/neraca",
};

const LEDGER_VIS_FIELD = {
  [NAV_KEYS.ledgerAccounts]: "accounts",
  [NAV_KEYS.ledgerGeneralJournal]: "generalJournal",
  [NAV_KEYS.ledgerOpeningBalance]: "openingBalance",
  [NAV_KEYS.ledgerAccountingPeriods]: "accountingPeriods",
  [NAV_KEYS.ledgerGeneralLedger]: "generalLedger",
  [NAV_KEYS.ledgerAccountsReceivable]: "accountsReceivable",
  [NAV_KEYS.ledgerAccountsPayable]: "accountsPayable",
  [NAV_KEYS.ledgerTrialBalance]: "trialBalance",
  [NAV_KEYS.ledgerProfitLoss]: "profitLoss",
  [NAV_KEYS.ledgerBalanceSheet]: "balanceSheet",
};

export const LEDGER_TILE_TONES = {
  [NAV_KEYS.ledgerAccounts]: "emerald",
  [NAV_KEYS.ledgerGeneralJournal]: "blue",
  [NAV_KEYS.ledgerOpeningBalance]: "amber",
  [NAV_KEYS.ledgerAccountingPeriods]: "rose",
  [NAV_KEYS.ledgerGeneralLedger]: "violet",
  [NAV_KEYS.ledgerAccountsReceivable]: "slate",
  [NAV_KEYS.ledgerAccountsPayable]: "rose",
  [NAV_KEYS.ledgerTrialBalance]: "slate",
  [NAV_KEYS.ledgerProfitLoss]: "emerald",
  [NAV_KEYS.ledgerBalanceSheet]: "slate",
};

export const LEDGER_TILE_TESTIDS = {
  [NAV_KEYS.ledgerAccounts]: "top-nav-akun-perkiraan",
  [NAV_KEYS.ledgerGeneralJournal]: "top-nav-jurnal-umum",
  [NAV_KEYS.ledgerOpeningBalance]: "top-nav-saldo-awal",
  [NAV_KEYS.ledgerAccountingPeriods]: "top-nav-periode-akuntansi",
  [NAV_KEYS.ledgerGeneralLedger]: "top-nav-buku-besar-ledger",
  [NAV_KEYS.ledgerAccountsReceivable]: "top-nav-piutang-usaha",
  [NAV_KEYS.ledgerAccountsPayable]: "top-nav-hutang-usaha",
  [NAV_KEYS.ledgerTrialBalance]: "top-nav-neraca-saldo",
  [NAV_KEYS.ledgerProfitLoss]: "top-nav-laba-rugi",
  [NAV_KEYS.ledgerBalanceSheet]: "top-nav-neraca",
};

export function ledgerNavVisible(hasPerm) {
  const accounts = canSeeNav(hasPerm, NAV_KEYS.ledgerAccounts)
    || canSeeNav(hasPerm, "ledger.accounts.view")
    || canSeeNav(hasPerm, "ledger.accounts.manage");
  const generalJournal = canSeeNav(hasPerm, NAV_KEYS.ledgerGeneralJournal)
    || canSeeNav(hasPerm, "general_journal.view")
    || canSeeNav(hasPerm, "general_journal.manage")
    || canSeeNav(hasPerm, "general_journal.post")
    || canSeeNav(hasPerm, "general_journal.reverse");
  const openingBalance = canSeeNav(hasPerm, NAV_KEYS.ledgerOpeningBalance)
    || canSeeNav(hasPerm, "opening_balance.view")
    || canSeeNav(hasPerm, "opening_balance.manage")
    || canSeeNav(hasPerm, "opening_balance.post")
    || canSeeNav(hasPerm, "opening_balance.reverse");
  const accountingPeriods = canSeeNav(hasPerm, NAV_KEYS.ledgerAccountingPeriods)
    || canSeeNav(hasPerm, "accounting.period.view")
    || canSeeNav(hasPerm, "accounting.period.close")
    || canSeeNav(hasPerm, "accounting.period.reopen");
  const generalLedger = canSeeNav(hasPerm, NAV_KEYS.ledgerGeneralLedger)
    || canSeeNav(hasPerm, "general_ledger.view");
  const accountsReceivable = canSeeNav(hasPerm, NAV_KEYS.ledgerAccountsReceivable)
    || canSeeNav(hasPerm, "accounts_receivable.view");
  const accountsPayable = canSeeNav(hasPerm, NAV_KEYS.ledgerAccountsPayable)
    || canSeeNav(hasPerm, "accounts_payable.view");
  const trialBalance = canSeeNav(hasPerm, NAV_KEYS.ledgerTrialBalance)
    || canSeeNav(hasPerm, "trial_balance.view");
  const profitLoss = canSeeNav(hasPerm, NAV_KEYS.ledgerProfitLoss)
    || canSeeNav(hasPerm, "profit_loss.view");
  const balanceSheet = canSeeNav(hasPerm, NAV_KEYS.ledgerBalanceSheet)
    || canSeeNav(hasPerm, "balance_sheet.view");
  return {
    parent: Boolean(accounts || generalJournal || openingBalance || accountingPeriods || generalLedger || accountsReceivable || accountsPayable || trialBalance || profitLoss || balanceSheet),
    accounts: Boolean(accounts),
    generalJournal: Boolean(generalJournal),
    openingBalance: Boolean(openingBalance),
    accountingPeriods: Boolean(accountingPeriods),
    generalLedger: Boolean(generalLedger),
    accountsReceivable: Boolean(accountsReceivable),
    accountsPayable: Boolean(accountsPayable),
    trialBalance: Boolean(trialBalance),
    profitLoss: Boolean(profitLoss),
    balanceSheet: Boolean(balanceSheet),
  };
}

export function ledgerMenuItems(hasPerm) {
  return menuItemsFromFallback(
    hasPerm,
    NAV_KEYS.ledger,
    ledgerNavVisible(hasPerm),
    LEDGER_VIS_FIELD,
    LEDGER_NAV_ROUTES,
    LEDGER_TILE_TONES,
    LEDGER_TILE_TESTIDS,
  );
}

export const CASH_BANK_NAV_ROUTES = {
  [NAV_KEYS.cashBankAccounts]: "/kas-bank/daftar",
  [NAV_KEYS.cashBankReceipts]: "/kas-bank/penerimaan",
  [NAV_KEYS.cashBankPayments]: "/kas-bank/pembayaran",
  [NAV_KEYS.cashBankTransfers]: "/kas-bank/transfer",
  [NAV_KEYS.cashBankMutasi]: "/kas-bank/mutasi",
  [NAV_KEYS.cashBankReconciliation]: "/kas-bank/rekonsiliasi",
};

const CASH_BANK_VIS_FIELD = {
  [NAV_KEYS.cashBankAccounts]: "accounts",
  [NAV_KEYS.cashBankReceipts]: "receipts",
  [NAV_KEYS.cashBankPayments]: "payments",
  [NAV_KEYS.cashBankTransfers]: "transfers",
  [NAV_KEYS.cashBankMutasi]: "mutasi",
  [NAV_KEYS.cashBankReconciliation]: "reconciliation",
};

export const CASH_BANK_TILE_TONES = {
  [NAV_KEYS.cashBankAccounts]: "emerald",
  [NAV_KEYS.cashBankReceipts]: "green",
  [NAV_KEYS.cashBankPayments]: "amber",
  [NAV_KEYS.cashBankTransfers]: "blue",
  [NAV_KEYS.cashBankMutasi]: "cyan",
  [NAV_KEYS.cashBankReconciliation]: "slate",
};

export const CASH_BANK_TILE_TESTIDS = {
  [NAV_KEYS.cashBankAccounts]: "top-nav-kas-bank-daftar",
  [NAV_KEYS.cashBankReceipts]: "top-nav-kas-bank-penerimaan",
  [NAV_KEYS.cashBankPayments]: "top-nav-kas-bank-pembayaran",
  [NAV_KEYS.cashBankTransfers]: "top-nav-kas-bank-transfer",
  [NAV_KEYS.cashBankMutasi]: "top-nav-kas-bank-mutasi",
  [NAV_KEYS.cashBankReconciliation]: "top-nav-kas-bank-rekonsiliasi",
};

export function cashBankNavVisible(hasPerm) {
  const pagePerm =
    canSeeNav(hasPerm, "cash_bank.view")
    || canSeeNav(hasPerm, "cash_bank.manage")
    || canSeeNav(hasPerm, "cash_bank.post")
    || canSeeNav(hasPerm, "cash_bank.reverse")
    || canSeeNav(hasPerm, "cash_bank.reconcile");
  const accounts = canSeeNav(hasPerm, NAV_KEYS.cashBankAccounts) || pagePerm;
  const receipts = canSeeNav(hasPerm, NAV_KEYS.cashBankReceipts) || pagePerm;
  const payments = canSeeNav(hasPerm, NAV_KEYS.cashBankPayments) || pagePerm;
  const transfers = canSeeNav(hasPerm, NAV_KEYS.cashBankTransfers) || pagePerm;
  const mutasi = canSeeNav(hasPerm, NAV_KEYS.cashBankMutasi)
    || canSeeNav(hasPerm, "cash_bank.view")
    || pagePerm;
  const reconciliation = canSeeNav(hasPerm, NAV_KEYS.cashBankReconciliation)
    || canSeeNav(hasPerm, "cash_bank.reconcile")
    || canSeeNav(hasPerm, "cash_bank.view")
    || pagePerm;
  const parent = canSeeNav(hasPerm, NAV_KEYS.cashBank)
    || accounts
    || receipts
    || payments
    || transfers
    || mutasi
    || reconciliation;
  return {
    parent: Boolean(parent && (accounts || receipts || payments || transfers || mutasi || reconciliation)),
    accounts: Boolean(parent && accounts),
    receipts: Boolean(parent && receipts),
    payments: Boolean(parent && payments),
    transfers: Boolean(parent && transfers),
    mutasi: Boolean(parent && mutasi),
    reconciliation: Boolean(parent && reconciliation),
  };
}

export function cashBankMenuItems(hasPerm) {
  return menuItemsFromFallback(
    hasPerm,
    NAV_KEYS.cashBank,
    cashBankNavVisible(hasPerm),
    CASH_BANK_VIS_FIELD,
    CASH_BANK_NAV_ROUTES,
    CASH_BANK_TILE_TONES,
    CASH_BANK_TILE_TESTIDS,
  );
}

/** Existing Settings page sections — same ids as Settings.jsx SETTINGS_NAV_ITEMS. */
export const SETTINGS_TAB_IDS = [
  "employees",
  "roles",
  "sidebar-access",
  "operator-wage",
  "hpp",
  "physical-stock",
  "sales-accounting",
  "purchase-accounting",
  "payroll-accounting",
  "customer-pricing",
  "customer-approval",
  "notifications",
  "delivery-deadline",
  "operator-target",
  "work-panel",
  "employee-admin",
  "hr-payroll",
  "sales-activity",
  "route-optimizer",
  "accurate-creators",
  "sales-user-mapping",
  "audit",
];

const SETTINGS_TAB_ID_SET = new Set(SETTINGS_TAB_IDS);

export const SETTINGS_NAV_ROUTES = Object.fromEntries(
  SETTINGS_TAB_IDS.map((id) => [id, `/settings/${id}`]),
);

export const SETTINGS_TILE_TONES = {
  employees: "teal",
  roles: "purple",
  "sidebar-access": "blue",
  "operator-wage": "green",
  hpp: "orange",
  "physical-stock": "emerald",
  "sales-accounting": "purple",
  "purchase-accounting": "rose",
  "payroll-accounting": "blue",
  "customer-pricing": "green",
  "customer-approval": "teal",
  "notifications": "cyan",
  "delivery-deadline": "orange",
  "operator-target": "blue",
  "work-panel": "purple",
  "employee-admin": "orange",
  "hr-payroll": "blue",
  "sales-activity": "green",
  "route-optimizer": "blue",
  "accurate-creators": "purple",
  "sales-user-mapping": "teal",
  audit: "orange",
};

export const SETTINGS_TILE_TESTIDS = Object.fromEntries(
  SETTINGS_TAB_IDS.map((id) => [id, `top-nav-settings-${id}`]),
);

export const SETTINGS_TILE_LABELS = {
  employees: "Karyawan",
  roles: "Role & Hak Akses",
  "sidebar-access": "Akses Menu Sidebar",
  "operator-wage": "Sistem Upah Operator",
  hpp: "Pengaturan HPP",
  "physical-stock": "Stok Fisik",
  "sales-accounting": "Akuntansi Penjualan",
  "purchase-accounting": "Akuntansi Pembelian",
  "payroll-accounting": "Akuntansi Payroll",
  "customer-pricing": "Harga Customer",
  "customer-approval": "Customer Approval",
  "notifications": "Notifikasi",
  "delivery-deadline": "Deadline Pengiriman",
  "operator-target": "Target Output Operator",
  "work-panel": "Form Pekerjaan Divisi",
  "employee-admin": "Administrasi Karyawan",
  "hr-payroll": "HR & Payroll",
  "sales-activity": "Sales Activity & Ownership",
  "route-optimizer": "Optimasi Rute",
  "accurate-creators": "Pembuat PO Accurate",
  "sales-user-mapping": "Mapping Sales Accurate",
  audit: "Audit Log",
};

/** Resolve an existing Settings section from path or legacy ?tab=. */
export function settingsTabFromPath(pathname, search = "") {
  const path = String(pathname || "").split("?")[0].replace(/\/$/, "") || "/";
  const child = path.startsWith("/settings/") ? path.slice("/settings/".length) : "";
  if (child && !child.includes("/") && SETTINGS_TAB_ID_SET.has(child)) return child;
  const raw = String(search || "");
  const query = raw.startsWith("?") ? raw.slice(1) : raw;
  const tab = new URLSearchParams(query).get("tab");
  if (tab && SETTINGS_TAB_ID_SET.has(tab)) return tab;
  return null;
}

/** Same permission gates as Settings.jsx category cards. Requires nav.settings for the rail. */
export function settingsNavVisible(hasPerm) {
  const parentGranted = canSeeNav(hasPerm, NAV_KEYS.settings);
  const employees = canSeeNav(hasPerm, "users.view") || canSeeNav(hasPerm, "users.manage");
  const roles = canSeeNav(hasPerm, "permissions.manage");
  const sidebarAccess = canSeeNav(hasPerm, "permissions.manage");
  const operatorWage = canSeeNav(hasPerm, "settings.operator_wage.view")
    || canSeeNav(hasPerm, "settings.operator_wage.manage")
    || canSeeNav(hasPerm, "reports.gaji_operator.view");
  const hpp = canSeeNav(hasPerm, "settings.hpp.view")
    || canSeeNav(hasPerm, "settings.hpp.manage")
    || canSeeNav(hasPerm, "permissions.manage");
  const physicalStock = canSeeNav(hasPerm, "current_stock.view")
    || canSeeNav(hasPerm, "current_stock.update")
    || canSeeNav(hasPerm, "permissions.manage");
  const salesAccounting = canSeeNav(hasPerm, "settings.sales_accounting.view")
    || canSeeNav(hasPerm, "settings.sales_accounting.manage")
    || canSeeNav(hasPerm, "permissions.manage");
  const purchaseAccounting = canSeeNav(hasPerm, "settings.purchase_accounting.view")
    || canSeeNav(hasPerm, "settings.purchase_accounting.manage")
    || canSeeNav(hasPerm, "permissions.manage");
  const payrollAccounting = canSeeNav(hasPerm, "payroll_accounting.view")
    || canSeeNav(hasPerm, "payroll_accounting.manage")
    || canSeeNav(hasPerm, "permissions.manage");
  const customerPricing = canSeeNav(hasPerm, "settings.customer_pricing.view")
    || canSeeNav(hasPerm, "settings.customer_pricing.manage")
    || canSeeNav(hasPerm, "settings.customer_pricing.publish")
    || canSeeNav(hasPerm, "permissions.manage");
  const customerApproval = canSeeNav(hasPerm, "customer_approval.view")
    || canSeeNav(hasPerm, "customer_approval.settings");
  const deliveryDeadline = canSeeNav(hasPerm, "delivery.deadline.settings")
    || canSeeNav(hasPerm, "delivery.deadline.edit")
    || canSeeNav(hasPerm, "dashboard.production.view");
  const operatorTarget = canSeeNav(hasPerm, "schedule.edit") || canSeeNav(hasPerm, "users.manage");
  const workPanel = canSeeNav(hasPerm, "settings.work_panel.view")
    || canSeeNav(hasPerm, "settings.work_panel.manage")
    || canSeeNav(hasPerm, "permissions.manage");
  const employeeAdmin = canSeeNav(hasPerm, "employee_admin.settings");
  const hrPayroll = canSeeNav(hasPerm, "payroll.settings");
  const salesActivity = canSeeNav(hasPerm, "settings.sales_activity.view")
    || canSeeNav(hasPerm, "settings.sales_activity.manage")
    || canSeeNav(hasPerm, "permissions.manage");
  const routeOptimizer = canSeeNav(hasPerm, "settings.route_optimizer.view")
    || canSeeNav(hasPerm, "settings.route_optimizer.manage")
    || canSeeNav(hasPerm, "permissions.manage");
  const accurateCreators = employees
    || canSeeNav(hasPerm, "accurate.import")
    || canSeeNav(hasPerm, "permissions.manage");
  const salesUserMapping = canSeeNav(hasPerm, "settings.sales_user_mapping.view")
    || canSeeNav(hasPerm, "settings.sales_user_mapping.manage")
    || canSeeNav(hasPerm, "permissions.manage");
  const audit = canSeeNav(hasPerm, "audit.view");
  const children = {
    employees: Boolean(parentGranted && employees),
    roles: Boolean(parentGranted && roles),
    "sidebar-access": Boolean(parentGranted && sidebarAccess),
    "operator-wage": Boolean(parentGranted && operatorWage),
    hpp: Boolean(parentGranted && hpp),
    "physical-stock": Boolean(parentGranted && physicalStock),
    "sales-accounting": Boolean(parentGranted && salesAccounting),
    "purchase-accounting": Boolean(parentGranted && purchaseAccounting),
    "payroll-accounting": Boolean(parentGranted && payrollAccounting),
    "customer-pricing": Boolean(parentGranted && customerPricing),
    "customer-approval": Boolean(parentGranted && customerApproval),
    "notifications": Boolean(parentGranted && customerApproval),
    "delivery-deadline": Boolean(parentGranted && deliveryDeadline),
    "operator-target": Boolean(parentGranted && operatorTarget),
    "work-panel": Boolean(parentGranted && workPanel),
    "employee-admin": Boolean(parentGranted && employeeAdmin),
    "hr-payroll": Boolean(parentGranted && hrPayroll),
    "sales-activity": Boolean(parentGranted && salesActivity),
    "route-optimizer": Boolean(parentGranted && routeOptimizer),
    "accurate-creators": Boolean(parentGranted && accurateCreators),
    "sales-user-mapping": Boolean(parentGranted && salesUserMapping),
    audit: Boolean(parentGranted && audit),
  };
  return {
    parent: Boolean(parentGranted && SETTINGS_TAB_IDS.some((id) => children[id])),
    ...children,
  };
}

export function settingsMenuItems(hasPerm) {
  const visible = settingsNavVisible(hasPerm);
  return SETTINGS_TAB_IDS.filter((id) => visible[id]).map((id) => ({
    key: id,
    label: SETTINGS_TILE_LABELS[id],
    to: SETTINGS_NAV_ROUTES[id],
    tone: SETTINGS_TILE_TONES[id] || "green",
    testid: SETTINGS_TILE_TESTIDS[id],
  }));
}
