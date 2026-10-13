export function isOperationalWarehouse(row) {
  return row?.is_operational !== false;
}

export function yogyakartaWarehouses(branches, warehouses) {
  const branch = (Array.isArray(branches) ? branches : []).find((row) => {
    const code = String(row?.code || "").trim().toUpperCase();
    const name = String(row?.name || "").trim().toLowerCase();
    return code === "YGY" || name.includes("yogyakarta");
  });
  if (!branch) return [];
  return (Array.isArray(warehouses) ? warehouses : []).filter(
    (row) => row.branch_id === branch.id && row.active !== false,
  );
}

export function operationalOpeningWarehouses(branches, warehouses) {
  return yogyakartaWarehouses(branches, warehouses).filter(isOperationalWarehouse);
}

/**
 * Stock Opname location choices: active + operational warehouses whose
 * branch_id matches an active branch returned by the API (already access-scoped).
 * Does not hardcode YGY — isolated UAT branches like UAT-YGY must appear.
 */
export function opnameWarehouseChoices(branches, warehouses) {
  const activeBranchIds = new Set(
    (Array.isArray(branches) ? branches : [])
      .filter((row) => row?.id && row.active !== false)
      .map((row) => row.id),
  );
  return (Array.isArray(warehouses) ? warehouses : []).filter((row) => (
    Boolean(row?.id)
    && row.active !== false
    && isOperationalWarehouse(row)
    && activeBranchIds.has(row.branch_id)
  ));
}

export function goodsReceiptWarehouseChoice(branches, warehouses) {
  const rows = yogyakartaWarehouses(branches, warehouses);
  if (rows.length === 1) return { rows, warehouseId: rows[0].id, blocked: false, explicit: false };
  if (!rows.length) return { rows, warehouseId: "", blocked: true, explicit: false };
  return { rows, warehouseId: "", blocked: false, explicit: true };
}

export function preselectedWarehouseId(rows) {
  const list = Array.isArray(rows) ? rows : [];
  return list.length === 1 ? list[0].id : "";
}

export function warehouseChoices(warehouses, branchId) {
  const rows = Array.isArray(warehouses) ? warehouses : [];
  if (!branchId) return rows;
  return rows.filter((row) => row.branch_id === branchId);
}

/** Cabang + kode/nama gudang when location choice is ambiguous. Uses IDs via branch map. */
export function warehouseLocationLabel(warehouse, branches) {
  if (!warehouse) return "";
  const whCode = String(warehouse.code || "").trim();
  const whName = String(warehouse.name || "").trim();
  const whPart = whCode && whName && whCode !== whName
    ? `${whCode} — ${whName}`
    : (whName || whCode || String(warehouse.id || "").trim());
  const branchRows = Array.isArray(branches) ? branches : [];
  const branch = branchRows.find((row) => row?.id === warehouse.branch_id)
    || (warehouse.branch_code || warehouse.branch_name
      ? { code: warehouse.branch_code, name: warehouse.branch_name }
      : null);
  if (!branch) return whPart;
  const brCode = String(branch.code || "").trim();
  const brName = String(branch.name || "").trim();
  const brPart = brCode || brName;
  if (!brPart) return whPart;
  if (brName && brCode && brName !== brCode) return `${brCode} (${brName}) · ${whPart}`;
  return `${brPart} · ${whPart}`;
}

/** Session/history rows already carry branch_* and warehouse_* denormalized fields. */
export function opnameLocationLabel(row) {
  if (!row) return "";
  return warehouseLocationLabel(
    {
      id: row.warehouse_id,
      code: row.warehouse_code,
      name: row.warehouse_name,
      branch_id: row.branch_id,
      branch_code: row.branch_code,
      branch_name: row.branch_name,
    },
    [],
  );
}

export function mutationQtyColumns(qtyDelta) {
  const qty = Number(qtyDelta);
  if (!Number.isFinite(qty) || qty === 0) return { masuk: null, keluar: null };
  if (qty > 0) return { masuk: qty, keluar: null };
  return { masuk: null, keluar: Math.abs(qty) };
}

export function stockEmptyMessage({ masterItemCount = 0, warehouseCount = 0, branchId = "", readMode = "" } = {}) {
  if (!masterItemCount) return "Belum ada Barang/Jasa pada environment preview.";
  if (readMode === "r2" && branchId && !warehouseCount) return "Cabang ini belum memiliki gudang.";
  if (readMode === "r2" && !warehouseCount) return "Tidak ada stok fisik untuk filter ini.";
  return "Tidak ada baris stok untuk filter ini.";
}

export function openingReady({ itemId, branchId, warehouseId, qty }) {
  if (!itemId || !branchId || !warehouseId) return false;
  if (qty === "" || qty === null || qty === undefined) return false;
  const parsed = Number(String(qty).replace(",", "."));
  return Number.isFinite(parsed) && parsed >= 0;
}

export const PHYSICAL_MOVEMENT_LABELS = {
  OPENING_BALANCE: "Saldo Awal",
  PURCHASE_RECEIPT: "Penerimaan Pembelian",
  PURCHASE_RETURN: "Retur Pembelian",
  SALES_DELIVERY: "Pengiriman",
  STOCK_OPNAME_ADJUSTMENT: "Penyesuaian Stock Opname",
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
