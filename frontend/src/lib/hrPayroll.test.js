import { canTransition, moneyFormat, nextStatuses, statusLabel } from "./hrPayroll";

describe("HR Payroll helpers", () => {
  test("labels workflow statuses", () => {
    expect(statusLabel("DRAFT")).toBe("Draft");
    expect(statusLabel("APPROVAL")).toBe("Persetujuan");
    expect(statusLabel("FINAL")).toBe("Final");
    expect(statusLabel("UNKNOWN")).toBe("UNKNOWN");
  });

  test("uses the backend transition graph", () => {
    expect(canTransition("DRAFT", "REVIEW")).toBe(true);
    expect(canTransition("DRAFT", "APPROVAL")).toBe(false);
    expect(canTransition("REVIEW", "DRAFT")).toBe(true);
    expect(canTransition("APPROVAL", "FINAL")).toBe(true);
    expect(nextStatuses("FINAL")).toEqual([]);
    expect(nextStatuses("REVIEW")).toEqual(["APPROVAL", "DRAFT"]);
  });

  test("formats Indonesian rupiah safely", () => {
    expect(moneyFormat(1250000)).toMatch(/Rp\s?1\.250\.000/);
    expect(moneyFormat("1250.5")).toMatch(/1\.250,5/);
    expect(moneyFormat("invalid")).toBe("Rp0");
  });
});
