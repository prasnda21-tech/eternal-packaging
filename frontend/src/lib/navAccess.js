/** Sidebar / page visibility helpers over existing `hasPerm` + nav.* keys. */

export const NAV_KEYS = {
  dashboard: "nav.dashboard",
  sales: "nav.sales",
  importPo: "nav.sales.import_po",
  schedule: "nav.production_schedule",
  purchasing: "nav.purchasing",
  purchaseReceipts: "nav.purchasing.receipts",
  inventory: "nav.inventory",
  masterItems: "nav.inventory.master_items",
  currentStock: "nav.inventory.current_stock",
  reports: "nav.reports",
  analytics: "nav.reports.analytics_output",
  shipmentStatus: "nav.reports.shipment_status",
  statusPo: "nav.reports.status_po",
  settings: "nav.settings",
};

export const NAV_MENU_FALLBACK = [
  { key: NAV_KEYS.dashboard, label: "Dashboard", parent: null },
  { key: NAV_KEYS.sales, label: "Penjualan", parent: null },
  { key: NAV_KEYS.importPo, label: "Import PO", parent: NAV_KEYS.sales },
  { key: NAV_KEYS.schedule, label: "Jadwal Produksi", parent: null },
  { key: NAV_KEYS.purchasing, label: "Pembelian", parent: null },
  { key: NAV_KEYS.purchaseReceipts, label: "Penerimaan Barang", parent: NAV_KEYS.purchasing },
  { key: NAV_KEYS.inventory, label: "Persediaan", parent: null },
  { key: NAV_KEYS.masterItems, label: "Master Barang/Jasa", parent: NAV_KEYS.inventory },
  { key: NAV_KEYS.currentStock, label: "Stock Saat Ini", parent: NAV_KEYS.inventory },
  { key: NAV_KEYS.reports, label: "Laporan", parent: null },
  { key: NAV_KEYS.analytics, label: "Analitik Output", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.statusPo, label: "Status PO", parent: NAV_KEYS.reports },
  { key: NAV_KEYS.shipmentStatus, label: "Status Pengiriman", parent: NAV_KEYS.reports },
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
  return { parent: !!(analytics || statusPo || shipmentStatus), analytics, statusPo, shipmentStatus };
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
