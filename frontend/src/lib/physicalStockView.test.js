import {
  goodsReceiptWarehouseChoice,
  isOperationalWarehouse,
  movementLabel,
  operationalOpeningWarehouses,
  mutationQtyColumns,
  openingReady,
  opnameLocationLabel,
  preselectedWarehouseId,
  stockEmptyMessage,
  warehouseChoices,
  warehouseLocationLabel,
  yogyakartaWarehouses,
} from "./physicalStockView";

describe("physical stock view helpers", () => {
  test("one Yogyakarta warehouse is preselected and several warehouses are not guessed", () => {
    const branches = [
      { id: "ygy", code: "YGY", name: "Yogyakarta" },
      { id: "smg", code: "SMG", name: "Semarang" },
    ];
    const warehouses = [
      { id: "main", branch_id: "ygy", code: "MAIN", active: true },
      { id: "smg-wh", branch_id: "smg", code: "ALT", active: true },
    ];
    const only = yogyakartaWarehouses(branches, warehouses);
    expect(only.map((row) => row.id)).toEqual(["main"]);
    expect(preselectedWarehouseId(only)).toBe("main");
    const many = yogyakartaWarehouses(branches, [
      ...warehouses,
      { id: "whb", branch_id: "ygy", code: "WHB", active: true },
    ]);
    expect(preselectedWarehouseId(many)).toBe("");
    expect(many.map((row) => row.id)).toEqual(["main", "whb"]);
  });

  test("opening balance warehouses hide a non-operational warehouse and keep a missing flag operational", () => {
    const branches = [{ id: "ygy", code: "YGY", name: "Yogyakarta" }];
    const warehouses = [
      { id: "main", branch_id: "ygy", code: "MAIN", active: true },
      { id: "ops", branch_id: "ygy", code: "OPS", active: true, is_operational: true },
      { id: "sopuat", branch_id: "ygy", code: "SOPUAT", active: true, is_operational: false },
    ];
    expect(isOperationalWarehouse(warehouses[0])).toBe(true);
    expect(isOperationalWarehouse(warehouses[2])).toBe(false);
    expect(operationalOpeningWarehouses(branches, warehouses).map((row) => row.id)).toEqual(["main", "ops"]);
    expect(yogyakartaWarehouses(branches, warehouses).map((row) => row.id)).toEqual(["main", "ops", "sopuat"]);
  });

  test("warehouse choices follow the selected branch and allow an empty branch", () => {
    const warehouses = [
      { id: "w1", branch_id: "ygy", code: "MAIN" },
      { id: "w2", branch_id: "smg", code: "ALT" },
    ];
    expect(warehouseChoices(warehouses, "ygy").map((row) => row.id)).toEqual(["w1"]);
    expect(warehouseChoices(warehouses, "smg")).toHaveLength(1);
    expect(warehouseChoices(warehouses, "empty")).toEqual([]);
    expect(warehouseChoices(warehouses, "").map((row) => row.id)).toEqual(["w1", "w2"]);
  });

  test("mutation qty maps positive to masuk and negative to keluar", () => {
    expect(mutationQtyColumns(12)).toEqual({ masuk: 12, keluar: null });
    expect(mutationQtyColumns(-4)).toEqual({ masuk: null, keluar: 4 });
    expect(mutationQtyColumns(0)).toEqual({ masuk: null, keluar: null });
  });

  test("empty master items and a branch without warehouses have distinct messages", () => {
    expect(stockEmptyMessage({ masterItemCount: 0, warehouseCount: 1 })).toBe(
      "Belum ada Barang/Jasa pada environment preview.",
    );
    expect(stockEmptyMessage({
      masterItemCount: 3,
      warehouseCount: 0,
      branchId: "smg",
      readMode: "r2",
    })).toBe(
      "Cabang ini belum memiliki gudang.",
    );
    expect(stockEmptyMessage({ masterItemCount: 3, warehouseCount: 0, readMode: "compatibility" })).toBe(
      "Tidak ada baris stok untuk filter ini.",
    );
  });

  test("saldo awal requires item, branch, warehouse, and a non-negative qty", () => {
    expect(openingReady({ itemId: "i", branchId: "b", warehouseId: "", qty: "1" })).toBe(false);
    expect(openingReady({ itemId: "i", branchId: "b", warehouseId: "w", qty: "" })).toBe(false);
    expect(openingReady({ itemId: "i", branchId: "b", warehouseId: "w", qty: "-1" })).toBe(false);
    expect(openingReady({ itemId: "i", branchId: "b", warehouseId: "w", qty: "0" })).toBe(true);
  });

  test("movement labels stay on physical types", () => {
    expect(movementLabel("OPENING_BALANCE")).toBe("Saldo Awal");
    expect(movementLabel("PURCHASE_RECEIPT")).toBe("Penerimaan Pembelian");
    expect(movementLabel("PURCHASE_RETURN")).toBe("Retur Pembelian");
    expect(movementLabel("SALES_DELIVERY")).toBe("Pengiriman");
    expect(movementLabel("MANUAL_ADJUSTMENT")).toBe("Penyesuaian");
    expect(movementLabel("REVERSAL")).toBe("Reversal");
  });

  test("warehouse location label shows cabang + gudang, not name-only", () => {
    const branches = [
      { id: "ygy", code: "YGY", name: "Yogyakarta" },
      { id: "smg", code: "SMG", name: "Semarang" },
    ];
    expect(warehouseLocationLabel(
      { id: "main", branch_id: "ygy", code: "MAIN", name: "Gudang Utama" },
      branches,
    )).toBe("YGY (Yogyakarta) · MAIN — Gudang Utama");
    expect(warehouseLocationLabel(
      { id: "alt", branch_id: "smg", code: "ALT", name: "ALT" },
      branches,
    )).toBe("SMG (Semarang) · ALT");
    expect(opnameLocationLabel({
      warehouse_code: "MAIN",
      warehouse_name: "Gudang Utama",
      branch_code: "YGY",
      branch_name: "Yogyakarta",
    })).toBe("YGY (Yogyakarta) · MAIN — Gudang Utama");
  });

  test("goods receipt warehouse choice never guesses among several warehouses", () => {
    const branches = [{ id: "ygy", code: "YGY", name: "Yogyakarta" }];
    const one = goodsReceiptWarehouseChoice(branches, [
      { id: "main", branch_id: "ygy", code: "MAIN", name: "Gudang Yogyakarta", active: true },
    ]);
    expect(one).toMatchObject({ warehouseId: "main", blocked: false, explicit: false });
    const many = goodsReceiptWarehouseChoice(branches, [
      { id: "main", branch_id: "ygy", code: "MAIN", name: "Gudang Yogyakarta", active: true },
      { id: "whb", branch_id: "ygy", code: "WHB", name: "Gudang B", active: true },
    ]);
    expect(many.warehouseId).toBe("");
    expect(many.explicit).toBe(true);
    expect(many.blocked).toBe(false);
    const none = goodsReceiptWarehouseChoice(branches, []);
    expect(none.blocked).toBe(true);
    expect(none.warehouseId).toBe("");
  });
});
