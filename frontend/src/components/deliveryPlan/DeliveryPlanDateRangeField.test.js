/**
 * @jest-environment node
 */
import {
  formatRangeLabel,
  formatYmdLocal,
  normalizeRangeYmd,
  parseYmdLocal,
} from "./deliveryPlanDateRange";

test("parse and format stay date-only without UTC shift", () => {
  const d = parseYmdLocal("2026-12-15");
  expect(d.getFullYear()).toBe(2026);
  expect(d.getMonth()).toBe(11);
  expect(d.getDate()).toBe(15);
  expect(formatYmdLocal(d)).toBe("2026-12-15");
});

test("normalize reverse selection to ascending range", () => {
  expect(normalizeRangeYmd("2026-12-15", "2026-12-12")).toEqual({
    from: "2026-12-12",
    to: "2026-12-15",
  });
  expect(normalizeRangeYmd("2026-12-12", "2026-12-15")).toEqual({
    from: "2026-12-12",
    to: "2026-12-15",
  });
});

test("range label uses DD MMM YYYY display", () => {
  expect(formatRangeLabel("2026-12-12", "2026-12-15")).toBe("12 Des 2026 – 15 Des 2026");
  expect(formatRangeLabel("", "")).toBe("Semua tanggal");
});

test("cross month and year labels", () => {
  expect(formatRangeLabel("2026-12-28", "2027-01-03")).toBe("28 Des 2026 – 03 Jan 2027");
});
