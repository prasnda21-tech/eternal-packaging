import {
  canManageUrgent,
  canRequestUrgent,
  canApproveUrgent,
  canShowUrgentRequestButton,
} from "./poUrgent";

describe("Urgent PO permission helpers", () => {
  test("request/approve follow dedicated keys", () => {
    expect(canRequestUrgent((p) => p === "urgent.request")).toBe(true);
    expect(canApproveUrgent((p) => p === "urgent.approve")).toBe(true);
    expect(canRequestUrgent((p) => p === "urgent.manage")).toBe(false);
    expect(canApproveUrgent((p) => p === "urgent.manage")).toBe(false);
  });

  test("legacy canManageUrgent still detects urgent.manage only", () => {
    expect(canManageUrgent((p) => p === "urgent.manage")).toBe(true);
    expect(canManageUrgent((p) => p === "urgent.request")).toBe(false);
  });

  test("show request button only for Normal eligible PO", () => {
    const has = (p) => p === "urgent.request";
    expect(canShowUrgentRequestButton({
      hasPerm: has,
      order: { priority: "normal", cancelled: false },
      request: null,
    })).toBe(true);
    expect(canShowUrgentRequestButton({
      hasPerm: has,
      order: { priority: "urgent", cancelled: false },
      request: null,
    })).toBe(false);
    expect(canShowUrgentRequestButton({
      hasPerm: has,
      order: { priority: "normal", cancelled: false },
      request: { status: "pending" },
    })).toBe(false);
  });
});
