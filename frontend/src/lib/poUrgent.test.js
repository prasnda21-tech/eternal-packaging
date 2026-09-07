import { canManageUrgent } from "./poUrgent";

describe("CRM / urgent.manage visibility", () => {
  test("intended roles with urgent.manage see the control", () => {
    expect(canManageUrgent((p) => p === "urgent.manage")).toBe(true);
    expect(canManageUrgent(() => true)).toBe(true);
  });

  test("default CRM payload without urgent.manage hides the control", () => {
    const crmDefault = new Set(["po.view", "payment.view", "sales.receipt.view"]);
    expect(canManageUrgent((p) => crmDefault.has(p))).toBe(false);
  });

  test("CRM override urgent.manage shows the control", () => {
    const crmGranted = new Set(["po.view", "urgent.manage"]);
    expect(canManageUrgent((p) => crmGranted.has(p))).toBe(true);
  });

  test("unrelated operational role stays hidden", () => {
    const operator = new Set(["po.view", "operator.update"]);
    expect(canManageUrgent((p) => operator.has(p))).toBe(false);
  });
});
