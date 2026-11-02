import { operationalSalesSnapshotLabel } from "./operationalSalesSnapshot";

describe("operationalSalesSnapshotLabel", () => {
  test("prefers sales_name then sales snapshot fields", () => {
    expect(operationalSalesSnapshotLabel({ sales_name: "Vira", sales: "Old" })).toBe("Vira");
    expect(operationalSalesSnapshotLabel({ sales: "  Alfi  " })).toBe("Alfi");
    expect(operationalSalesSnapshotLabel({ seller: "Euis" })).toBe("Euis");
    expect(operationalSalesSnapshotLabel({ sales_user: "Legacy" })).toBe("Legacy");
  });

  test("does not invent identity from sales_master_id alone", () => {
    expect(operationalSalesSnapshotLabel({ sales_master_id: "sm-1" })).toBe("");
    expect(
      operationalSalesSnapshotLabel({ sales_master_id: "sm-1", sales: "Vira" })
    ).toBe("Vira");
  });

  test("handles empty and non-object input", () => {
    expect(operationalSalesSnapshotLabel(null)).toBe("");
    expect(operationalSalesSnapshotLabel(undefined)).toBe("");
    expect(operationalSalesSnapshotLabel({})).toBe("");
    expect(operationalSalesSnapshotLabel("Vira")).toBe("");
  });
});
