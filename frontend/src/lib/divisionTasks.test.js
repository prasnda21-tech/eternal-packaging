import { formatCurrentStock, formatIdQty, taskMatchesQuery, taskWorkHref } from "./divisionTasks";

describe("division task helpers", () => {
  test("kerja href is focused work panel not full PO", () => {
    expect(taskWorkHref({ order_id: "po1", division: "operator" })).toBe("/po/po1/kerja?divisi=operator");
    expect(taskWorkHref({ order_id: "po1", division: "packing" })).not.toMatch(/^\/po\/po1$/);
    expect(taskWorkHref({ order_id: "po1", division: "designer" })).toBe("/po/po1/kerja?divisi=designer");
    expect(taskWorkHref({ order_id: "po1", division: "kepala_produksi", line_idx: 0 })).toBe("/po/po1/kerja?divisi=kepala_produksi&line=0");
  });

  test("search matches po customer item", () => {
    const t = { order_number: "PO-001", customer_name: "ABC", item: "Cup A" };
    expect(taskMatchesQuery(t, "cup")).toBe(true);
    expect(taskMatchesQuery(t, "zzz")).toBe(false);
  });

  test("qty format", () => {
    expect(formatIdQty(4000)).toBe("4.000");
  });

  test("current stock format preserves zero and dashes unresolved", () => {
    expect(formatCurrentStock(3250)).toBe("3.250");
    expect(formatCurrentStock(0)).toBe("0");
    expect(formatCurrentStock(null)).toBe("—");
    expect(formatCurrentStock(undefined)).toBe("—");
  });
});
