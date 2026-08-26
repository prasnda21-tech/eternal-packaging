import { formatIdQty, taskMatchesQuery, taskWorkHref } from "./divisionTasks";

describe("division task helpers", () => {
  test("kerja href is focused work panel not full PO", () => {
    expect(taskWorkHref({ order_id: "po1", division: "operator" })).toBe("/po/po1/kerja?divisi=operator");
    expect(taskWorkHref({ order_id: "po1", division: "packing" })).not.toMatch(/^\/po\/po1$/);
  });

  test("search matches po customer item", () => {
    const t = { order_number: "PO-001", customer_name: "ABC", item: "Cup A" };
    expect(taskMatchesQuery(t, "cup")).toBe(true);
    expect(taskMatchesQuery(t, "zzz")).toBe(false);
  });

  test("qty format", () => {
    expect(formatIdQty(4000)).toBe("4.000");
  });
});
