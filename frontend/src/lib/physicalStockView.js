export function warehouseChoices(warehouses, branchId) {
  const rows = Array.isArray(warehouses) ? warehouses : [];
  if (!branchId) return rows;
  return rows.filter((row) => row.branch_id === branchId);
}

export function mutationQtyColumns(qtyDelta) {
  const qty = Number(qtyDelta);
  if (!Number.isFinite(qty) || qty === 0) return { masuk: null, keluar: null };
  if (qty > 0) return { masuk: qty, keluar: null };
  return { masuk: null, keluar: Math.abs(qty) };
}

export function stockEmptyMessage({ masterItemCount = 0, warehouseCount = 0, branchId = "" } = {}) {
  if (!masterItemCount) return "Belum ada Barang/Jasa pada environment preview.";
  if (branchId && !warehouseCount) return "Cabang ini belum memiliki gudang.";
  return "Tidak ada stok fisik untuk filter ini.";
}

export function openingReady({ itemId, branchId, warehouseId, qty }) {
  if (!itemId || !branchId || !warehouseId) return false;
  if (qty === "" || qty === null || qty === undefined) return false;
  const parsed = Number(String(qty).replace(",", "."));
  return Number.isFinite(parsed) && parsed >= 0;
}

export const PHYSICAL_MOVEMENT_LABELS = {
  OPENING_BALANCE: "Saldo Awal",
  PURCHASE_RECEIPT: "Penerimaan",
  SALES_DELIVERY: "Pengiriman",
  STOCK_OPNAME_ADJUSTMENT: "Penyesuaian Opname",
  MANUAL_ADJUSTMENT: "Penyesuaian",
  WAREHOUSE_TRANSFER_OUT: "Transfer Keluar",
  WAREHOUSE_TRANSFER_IN: "Transfer Masuk",
  REVERSAL: "Reversal",
  PRODUCTION_CONSUMPTION: "Pemakaian Produksi",
  PRODUCTION_OUTPUT: "Hasil Produksi",
};

export function movementLabel(movementType) {
  return PHYSICAL_MOVEMENT_LABELS[movementType] || movementType || "—";
}
