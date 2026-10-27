import {
  bookPhysicalDisplay,
  stockPhysicalPrimaryDisplay,
  goodsReceiptWarehouseChoice,
  isOperationalWarehouse,
  movementLabel,
  operationalOpeningWarehouses,
  mutationQtyColumns,
  openingReady,
  openingFieldErrors,
  opnameLocationLabel,
  opnameWarehouseChoices,
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

  test("opname warehouse choices include UAT-YGY by branch id, not YGY hardcode", () => {
    const branches = [
      { id: "br-uat", code: "UAT-YGY", name: "Cabang UAT Adjust", active: true },
    ];
    const warehouses = [
      { id: "wh-a", branch_id: "br-uat", code: "GUD-A", name: "Gudang A", active: true, is_operational: true },
      { id: "wh-b", branch_id: "br-uat", code: "GUD-B", name: "Gudang B", active: true, is_operational: true },
      { id: "wh-x", branch_id: "br-uat", code: "X", name: "Non-ops", active: true, is_operational: false },
      { id: "wh-old", branch_id: "gone", code: "OLD", active: true, is_operational: true },
    ];
    expect(operationalOpeningWarehouses(branches, warehouses)).toEqual([]);
    expect(opnameWarehouseChoices(branches, warehouses).map((row) => row.code)).toEqual(["GUD-A", "GUD-B"]);
    expect(preselectedWarehouseId(opnameWarehouseChoices(branches, warehouses))).toBe("");
    expect(warehouseLocationLabel(warehouses[0], branches)).toMatch(/UAT-YGY/);
    expect(warehouseLocationLabel(warehouses[0], branches)).toMatch(/GUD-A/);
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

  test("openingFieldErrors surfaces clear per-field messages", () => {
    expect(openingFieldErrors({ itemId: "", branchId: "", warehouseId: "", qty: "" })).toEqual({
      itemId: "Pilih barang.",
      branchId: "Pilih cabang.",
      warehouseId: "Pilih gudang.",
      qty: "Isi qty stok fisik (≥ 0).",
    });
    expect(openingFieldErrors({ itemId: "i", branchId: "b", warehouseId: "w", qty: "-2" }).qty)
      .toMatch(/tidak boleh negatif/i);
    expect(openingFieldErrors({ itemId: "i", branchId: "b", warehouseId: "w", qty: "1.5" })).toEqual({});
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

  test("goods receipt warehouse choice matches UAT branch by id, not YGY code", () => {
    const branches = [{ id: "uat-ygy", code: "UAT-YGY", name: "Cabang UAT Adjust", active: true }];
    const many = goodsReceiptWarehouseChoice(branches, [
      { id: "a", branch_id: "uat-ygy", code: "GUD-A", name: "Gudang A", active: true, is_operational: true },
      { id: "b", branch_id: "uat-ygy", code: "GUD-B", name: "Gudang B", active: true, is_operational: true },
    ]);
    expect(many.blocked).toBe(false);
    expect(many.explicit).toBe(true);
    expect(many.warehouseId).toBe("");
    expect(many.rows.map((r) => r.code)).toEqual(["GUD-A", "GUD-B"]);
  });

  test("bookPhysicalDisplay never treats missing balance as proven zero", () => {
    const absent = bookPhysicalDisplay({
      physical_qty: 0,
      warehouse_balances: [{
        warehouse_id: "wh-b",
        physical_qty: null,
        balance_present: false,
      }],
    }, { warehouseId: "wh-b" });
    expect(absent).toEqual({ present: false, qty: null, text: "Belum saldo" });

    const zeroBook = bookPhysicalDisplay({
      physical_qty: 0,
      warehouse_balances: [{
        warehouse_id: "wh-a",
        physical_qty: 0,
        balance_present: true,
      }],
    }, { warehouseId: "wh-a" });
    expect(zeroBook).toEqual({ present: true, qty: 0, text: null });

    const book20 = bookPhysicalDisplay({
      physical_qty: 20,
      warehouse_balances: [{
        warehouse_id: "wh-a",
        physical_qty: 20,
        balance_present: true,
      }],
    }, { warehouseId: "wh-a" });
    expect(book20).toEqual({ present: true, qty: 20, text: null });
  });

  test("stockPhysicalPrimaryDisplay is a single status — never Belum saldo + Belum lengkap", () => {
    const partial = {
      physical_qty: 200,
      baseline_status: "unreconciled",
      baseline_label: "Saldo awal belum direkonsiliasi",
      warehouse_balances: [
        { warehouse_id: "wh-a", balance_present: false, baseline_status: "unreconciled" },
        { warehouse_id: "wh-b", balance_present: true, physical_qty: 200 },
      ],
    };
    const aggregate = stockPhysicalPrimaryDisplay(partial, { branchAggregate: true });
    expect(aggregate.text).toBe("Belum lengkap");
    expect(aggregate.flag).toBeNull();
    expect(aggregate.present).toBe(false);
    expect(aggregate.qty).toBeNull();

    const allMissing = stockPhysicalPrimaryDisplay({
      physical_qty: 0,
      baseline_status: "unreconciled",
      warehouse_balances: [
        { warehouse_id: "wh-a", balance_present: false, baseline_status: "unreconciled" },
        { warehouse_id: "wh-b", balance_present: false, baseline_status: "unreconciled" },
      ],
    }, { branchAggregate: true });
    expect(allMissing.text).toBe("Belum lengkap");
    expect(allMissing.flag).toBeNull();
    expect(`${allMissing.text || ""}${allMissing.flag || ""}`).not.toMatch(/saldo.*lengkap|lengkap.*saldo/i);

    const selectedMissing = stockPhysicalPrimaryDisplay(partial, { warehouseId: "wh-a" });
    expect(selectedMissing.text).toBe("Belum saldo");
    expect(selectedMissing.flag).toBeNull();
    expect(selectedMissing.text).not.toMatch(/terverifikasi/i);

    const valid = stockPhysicalPrimaryDisplay({
      physical_qty: 1500,
      baseline_status: "reconciled",
      warehouse_balances: [
        { warehouse_id: "wh-a", balance_present: true, physical_qty: 1000 },
        { warehouse_id: "wh-b", balance_present: true, physical_qty: 500 },
      ],
    }, { branchAggregate: true });
    expect(valid.present).toBe(true);
    expect(valid.qty).toBe(1500);
    expect(valid.text).toBeNull();
    expect(valid.flag).toBeNull();

    const deficit = stockPhysicalPrimaryDisplay({
      physical_qty: 75,
      saldo_buku_negatif: false,
      baseline_status: "reconciled",
      warehouse_balances: [
        { warehouse_id: "wh-a", balance_present: true, physical_qty: -25, saldo_buku_negatif: true },
        { warehouse_id: "wh-b", balance_present: true, physical_qty: 100 },
      ],
    }, { branchAggregate: true });
    expect(deficit.present).toBe(true);
    expect(deficit.qty).toBe(75);
    expect(deficit.flag).toBe("Defisit");

    const selectedNeg = stockPhysicalPrimaryDisplay({
      physical_qty: -25,
      saldo_buku_negatif: true,
      warehouse_balances: [
        { warehouse_id: "wh-a", balance_present: true, physical_qty: -25, saldo_buku_negatif: true },
      ],
    }, { warehouseId: "wh-a" });
    expect(selectedNeg.qty).toBe(-25);
    expect(selectedNeg.flag).toBe("Defisit");
    expect(selectedNeg.negative).toBe(true);
  });
});
