import {
  pickYgyBranchFromMaster,
  purchaseBranchLabel,
  purchaseInvoiceWarehouseLabel,
  purchaseWarehouseLabel,
} from "./purchaseUi";

describe("PUR-LOC-01 LOC-C1 purchase location labels", () => {
  test("pickYgyBranchFromMaster matches code YGY without hardcoding id", () => {
    const ygy = pickYgyBranchFromMaster([
      { id: "branch-smg", code: "SMG", name: "Semarang", active: true },
      { id: "branch-ygy", code: "YGY", name: "Yogyakarta", active: true },
    ]);
    expect(ygy?.code).toBe("YGY");
    expect(ygy?.name).toBe("Yogyakarta");
    expect(purchaseBranchLabel(ygy)).toBe("Yogyakarta (YGY)");
  });

  test("legacy empty branch shows em dash", () => {
    expect(purchaseBranchLabel({})).toBe("—");
    expect(purchaseBranchLabel({ branch_id: "branch-ygy" })).toBe("—");
    expect(purchaseBranchLabel(null)).toBe("—");
  });

  test("invoice PO preview: branch from PO, warehouse em dash before GR", () => {
    const preview = {
      invoice_source: "purchase_order",
      branch_id: "branch-ygy",
      branch_code: "YGY",
      branch_name: "Yogyakarta",
    };
    expect(purchaseBranchLabel(preview)).toBe("Yogyakarta (YGY)");
    expect(purchaseInvoiceWarehouseLabel(preview, "purchase_order")).toBe("—");
  });

  test("invoice GR preview: branch and warehouse from GR snapshot", () => {
    const preview = {
      invoice_source: "receipt",
      branch_code: "YGY",
      branch_name: "Yogyakarta",
      warehouse_code: "YGY-A",
      warehouse_name: "Gudang YGY A",
    };
    expect(purchaseBranchLabel(preview)).toBe("Yogyakarta (YGY)");
    expect(purchaseInvoiceWarehouseLabel(preview, "receipt")).toBe("Gudang YGY A (YGY-A)");
    expect(purchaseWarehouseLabel(preview)).toBe("Gudang YGY A (YGY-A)");
  });

  test("invoice detail snapshot and legacy null location", () => {
    const snap = {
      invoice_source: "receipt",
      branch_code: "YGY",
      branch_name: "Yogyakarta",
      warehouse_code: "YGY-A",
      warehouse_name: "Gudang YGY A",
    };
    expect(purchaseBranchLabel(snap)).toBe("Yogyakarta (YGY)");
    expect(purchaseInvoiceWarehouseLabel(snap, "receipt")).toBe("Gudang YGY A (YGY-A)");
    expect(purchaseBranchLabel({ invoice_number: "FPB-LEGACY" })).toBe("—");
    expect(purchaseInvoiceWarehouseLabel({ invoice_source: "purchase_order" }, "purchase_order")).toBe("—");
  });
});
