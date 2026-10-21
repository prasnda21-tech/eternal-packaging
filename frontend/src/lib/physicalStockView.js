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

/**
 * Goods receipt location choices: same ID-scoped rule as stock opname.
 * Uses branch/warehouse ids from access-scoped API lists — no YGY name/code hardcode
 * (UAT branches like UAT-YGY must appear when the API returns them).
 */
export function goodsReceiptWarehouseChoice(branches, warehouses) {
  const rows = opnameWarehouseChoices(branches, warehouses);
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

/**
 * Book stock cell for Stock Saat Ini.
 * Missing balance (balance_present=false) is NOT a proven zero — do not render 0.
 * Only show numeric 0 when a balance document is present.
 */
export function bookPhysicalDisplay(item, { warehouseId = "" } = {}) {
  const balances = Array.isArray(item?.warehouse_balances) ? item.warehouse_balances : [];
  const wid = String(warehouseId || "").trim();
  if (wid) {
    const wb = balances.find((row) => String(row?.warehouse_id || "") === wid)
      || (balances.length === 1 ? balances[0] : null);
    if (wb && wb.balance_present === false) {
      return { present: false, qty: null, text: "Belum tersedia" };
    }
    if (wb && wb.balance_present === true) {
      const qty = wb.physical_qty;
      return { present: true, qty, text: null };
    }
  }
  if (balances.length > 0 && balances.every((row) => row?.balance_present === false)) {
    return { present: false, qty: null, text: "Belum tersedia" };
  }
  if (item?.balance_present === false) {
    return { present: false, qty: null, text: "Belum tersedia" };
  }
  return { present: true, qty: item?.physical_qty, text: null };
}

/**
 * Single primary Stock Fisik cell for the catalog table.
 *
 * OWNER rules (no dual badges):
 * - Semua Gudang + any missing/unreconciled WH → "Belum lengkap" only
 * - Selected WH + no balance → "Belum tersedia" only
 * - Valid book → numeric qty
 * - Negative book (or WH deficit in aggregate) → numeric qty + compact "Defisit"
 * Never invent 0 for unknown balances.
 */
export function stockPhysicalPrimaryDisplay(item, { warehouseId = "", branchAggregate = false } = {}) {
  const wid = String(warehouseId || "").trim();
  const book = bookPhysicalDisplay(item, { warehouseId: wid });
  const balances = Array.isArray(item?.warehouse_balances) ? item.warehouse_balances : [];
  const hasWhMissing = balances.some((wb) => wb?.balance_present === false);
  const hasWhUnreconciled = balances.some((wb) => wb?.baseline_status === "unreconciled");
  const hasWhDeficit = balances.some((wb) => wb?.saldo_buku_negatif);
  const rowBaselineUnreconciled = String(item?.baseline_status || "") === "unreconciled";
  const aggregateIncomplete = Boolean(
    branchAggregate && (hasWhMissing || hasWhUnreconciled || rowBaselineUnreconciled),
  );

  if (wid) {
    // Selected warehouse — never show "Belum lengkap" (that is aggregate-only).
    if (!book.present) {
      return {
        present: false,
        qty: null,
        text: "Belum tersedia",
        flag: null,
        negative: false,
        hints: [item?.baseline_label, "Saldo gudang belum ada"].filter(Boolean),
      };
    }
    const qty = book.qty;
    const negative = Boolean(item?.saldo_buku_negatif) || (qty != null && Number(qty) < 0);
    return {
      present: true,
      qty,
      text: null,
      flag: negative ? "Defisit" : null,
      negative,
      hints: [
        item?.baseline_label,
        negative ? "Saldo negatif" : null,
      ].filter(Boolean),
    };
  }

  if (aggregateIncomplete) {
    // One primary status — never "Belum tersedia" + "Belum lengkap", never qty + "Belum lengkap".
    return {
      present: false,
      qty: null,
      text: "Belum lengkap",
      flag: null,
      negative: false,
      hints: [
        item?.baseline_label,
        hasWhMissing ? "Ada gudang belum saldo" : null,
        hasWhUnreconciled || rowBaselineUnreconciled ? "Ada gudang belum valid" : null,
        hasWhDeficit ? "Ada gudang defisit" : null,
      ].filter(Boolean),
    };
  }

  if (!book.present) {
    return {
      present: false,
      qty: null,
      text: "Belum tersedia",
      flag: null,
      negative: false,
      hints: [item?.baseline_label].filter(Boolean),
    };
  }

  const qty = book.qty;
  const rowNegative = Boolean(item?.saldo_buku_negatif) || (qty != null && Number(qty) < 0);
  const showDeficit = rowNegative || hasWhDeficit;
  return {
    present: true,
    qty,
    text: null,
    flag: showDeficit ? "Defisit" : null,
    negative: showDeficit,
    hints: [
      rowNegative ? "Saldo negatif" : null,
      hasWhDeficit ? "Ada gudang defisit" : null,
    ].filter(Boolean),
  };
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
