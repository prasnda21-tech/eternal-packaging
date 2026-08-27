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
  importPo: "nav.sales.import_po",
  purchasing: "nav.purchasing",
  purchaseReceipts: "nav.purchasing.receipts",
  inventory: "nav.inventory",
  masterItems: "nav.inventory.master_items",
  currentStock: "nav.inventory.current_stock",
  reports: "nav.reports",
  analytics: "nav.reports.analytics_output",
  shipmentStatus: "nav.reports.shipment_status",
  statusPo: "nav.reports.status_po",
  gajiOperator: "nav.reports.gaji_operator",
  settings: "nav.settings",
};

export const NAV_MENU_FALLBACK = [
  { key: NAV_KEYS.dashboard, label: "Dashboard", parent: null },
  { key: NAV_KEYS.production, label: "Produksi", parent: null },
  { key: NAV_KEYS.schedule, label: "Jadwal Produksi", parent: NAV_KEYS.production },
  { key: NAV_KEYS.deliverySchedule, label: "Jadwal Pengiriman", parent: NAV_KEYS.production },
  { key: NAV_KEYS.deliveryReschedule, label: "Jadwal Pengiriman Ulang", parent: NAV_KEYS.production },
  { key: NAV_KEYS.driverRoute, label: "Rute Saya", parent: NAV_KEYS.production },
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
  { key: NAV_KEYS.importPo, label: "Import PO", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.purchasing, label: "Pembelian", parent: null },
  { key: NAV_KEYS.purchaseReceipts, label: "Penerimaan Barang", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.inventory, label: "Persediaan", parent: null },
  { key: NAV_KEYS.masterItems, label: "Barang/Jasa", parent: NAV_KEYS.inventory },
  { key: NAV_KEYS.currentStock, label: "Stock Saat Ini", parent: NAV_KEYS.inventory },
  { key: NAV_KEYS.reports, label: "Laporan", parent: null },
  { key: NAV_KEYS.analytics, label: "Analitik Output", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.statusPo, label: "Status PO", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.shipmentStatus, label: "Status Pengiriman", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.gajiOperator, label: "Gaji Operator", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.settings, label: "Pengaturan", parent: null },
];

export function canSeeNav(hasPerm, key) {
  return typeof hasPerm === "function" ? !!hasPerm(key) : false;
}

/** Parent that is not a page itself is hidden when no child is visible. */
export function salesNavVisible(hasPerm) {
  const parent = canSeeNav(hasPerm, NAV_KEYS.sales);
  const importPo = parent && canSeeNav(hasPerm, NAV_KEYS.importPo);
  return { parent: importPo, importPo };
}

export function reportsNavVisible(hasPerm) {
  const parent = canSeeNav(hasPerm, NAV_KEYS.reports);
  const analytics = parent && canSeeNav(hasPerm, NAV_KEYS.analytics);
  const statusPo = parent && canSeeNav(hasPerm, NAV_KEYS.statusPo);
  const shipmentStatus = parent && canSeeNav(hasPerm, NAV_KEYS.shipmentStatus);
  const gajiOperator = parent && canSeeNav(hasPerm, NAV_KEYS.gajiOperator);
  return { parent: !!(analytics || statusPo || shipmentStatus || gajiOperator), analytics, statusPo, shipmentStatus, gajiOperator };
}

export function purchasingNavVisible(hasPerm) {
  const parent = canSeeNav(hasPerm, NAV_KEYS.purchasing);
  const receipts = parent && canSeeNav(hasPerm, NAV_KEYS.purchaseReceipts);
  return { parent: !!receipts, receipts };
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

export function productionNavVisible(hasPerm) {
  const schedule = canSeeNav(hasPerm, NAV_KEYS.schedule);
  const deliverySchedule = canSeeNav(hasPerm, NAV_KEYS.deliverySchedule);
  const deliveryReschedule = canSeeNav(hasPerm, NAV_KEYS.deliveryReschedule);
  const driverRoute = canSeeNav(hasPerm, NAV_KEYS.driverRoute);
  const tasks = tasksNavVisible(hasPerm);
  return {
    parent: !!(schedule || deliverySchedule || deliveryReschedule || driverRoute || tasks.parent),
    schedule,
    deliverySchedule,
    deliveryReschedule,
    driverRoute,
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
  };
  return map[actionKey] || "/tasks";
}
