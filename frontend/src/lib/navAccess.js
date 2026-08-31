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
  salesOrder: "nav.sales.sales_order",
  salesDeliveryOrder: "nav.sales.delivery_order",
  salesInvoice: "nav.sales.sales_invoice",
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
  orderPrediction: "nav.reports.order_prediction",
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
  { key: NAV_KEYS.salesOrder, label: "Pesanan Penjualan", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesDeliveryOrder, label: "Pengiriman Pesanan", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.salesInvoice, label: "Faktur Penjualan", parent: NAV_KEYS.sales },
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
  { key: NAV_KEYS.orderPrediction, label: "Prediksi Order", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.settings, label: "Pengaturan", parent: null },
];

export function canSeeNav(hasPerm, key) {
  return typeof hasPerm === "function" ? !!hasPerm(key) : false;
}

/** Parent that is not a page itself is hidden when no child is visible. */
export function salesNavVisible(hasPerm) {
  const parent = canSeeNav(hasPerm, NAV_KEYS.sales);
  const salesOrder = parent && (canSeeNav(hasPerm, NAV_KEYS.salesOrder) || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "sales_order.create") || canSeeNav(hasPerm, "po.view"));
  const salesDeliveryOrder = parent && (canSeeNav(hasPerm, NAV_KEYS.salesDeliveryOrder) || canSeeNav(hasPerm, "sales.delivery.view") || canSeeNav(hasPerm, "sales.delivery.create") || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "po.view"));
  const salesInvoice = parent && (canSeeNav(hasPerm, NAV_KEYS.salesInvoice) || canSeeNav(hasPerm, "sales.invoice.view") || canSeeNav(hasPerm, "sales.invoice.create") || canSeeNav(hasPerm, "sales_order.view") || canSeeNav(hasPerm, "po.view"));
  const importPo = parent && (canSeeNav(hasPerm, NAV_KEYS.importPo) || canSeeNav(hasPerm, "po.import.view"));
  return {
    parent: Boolean(salesOrder || salesDeliveryOrder || salesInvoice || importPo),
    salesOrder: Boolean(salesOrder),
    salesDeliveryOrder: Boolean(salesDeliveryOrder),
    salesInvoice: Boolean(salesInvoice),
    importPo: Boolean(importPo),
  };
}

/** Existing Penjualan child routes. */
export const SALES_NAV_ROUTES = {
  [NAV_KEYS.salesOrder]: "/penjualan/pesanan-penjualan",
  [NAV_KEYS.salesDeliveryOrder]: "/penjualan/pengiriman-pesanan",
  [NAV_KEYS.salesInvoice]: "/penjualan/faktur-penjualan",
  [NAV_KEYS.importPo]: "/admin/import",
};

const SALES_VIS_FIELD = {
  [NAV_KEYS.salesOrder]: "salesOrder",
  [NAV_KEYS.salesDeliveryOrder]: "salesDeliveryOrder",
  [NAV_KEYS.salesInvoice]: "salesInvoice",
  [NAV_KEYS.importPo]: "importPo",
};

/** Stable tile tones keyed by existing nav.* — not randomized per render. */
export const SALES_TILE_TONES = {
  [NAV_KEYS.salesOrder]: "blue",
  [NAV_KEYS.salesDeliveryOrder]: "amber",
  [NAV_KEYS.salesInvoice]: "purple",
  [NAV_KEYS.importPo]: "green",
};

export const SALES_TILE_TESTIDS = {
  [NAV_KEYS.salesOrder]: "top-nav-sales-order",
  [NAV_KEYS.salesDeliveryOrder]: "top-nav-sales-delivery-order",
  [NAV_KEYS.salesInvoice]: "top-nav-sales-invoice",
  [NAV_KEYS.importPo]: "top-nav-import",
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
  [NAV_KEYS.purchaseReceipts]: "/pembelian/penerimaan-barang",
};

const PURCHASING_VIS_FIELD = {
  [NAV_KEYS.purchaseReceipts]: "receipts",
};

export const PURCHASING_TILE_TONES = {
  [NAV_KEYS.purchaseReceipts]: "blue",
};

export const PURCHASING_TILE_TESTIDS = {
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
