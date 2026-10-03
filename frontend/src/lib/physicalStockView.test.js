import {
  movementLabel,
  mutationQtyColumns,
  openingReady,
  preselectedWarehouseId,
  stockEmptyMessage,
  warehouseChoices,
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
    expect(stockEmptyMessage({ masterItemCount: 3, warehouseCount: 0, branchId: "smg" })).toBe(
      "Cabang ini belum memiliki gudang.",
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
    expect(movementLabel("SALES_DELIVERY")).toBe("Pengiriman");
  });
});
