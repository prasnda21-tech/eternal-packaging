/**
 * @jest-environment node
 */
import {
  formatCashAxisLabel,
  formatCashTooltipLabel,
  mapOwnerCashTrend,
  ownerCashGrainLabel,
  pickCashAxisTickIndexes,
} from "./ownerAnalytics";

test("ownerCashGrainLabel maps API enums to Indonesian presentation", () => {
  expect(ownerCashGrainLabel("day")).toBe("Harian");
  expect(ownerCashGrainLabel("month")).toBe("Bulanan");
  expect(ownerCashGrainLabel("Harian")).toBe("Harian");
  expect(ownerCashGrainLabel("")).toBe("");
});

test("formatCashAxisLabel daily and monthly forms", () => {
  expect(formatCashAxisLabel("2026-10-01")).toBe("01 Okt");
  expect(formatCashAxisLabel("2026-10-08")).toBe("08 Okt");
  expect(formatCashAxisLabel("2026-10")).toBe("Okt");
  expect(formatCashAxisLabel("2026-10", { showYear: true })).toBe("Okt 2026");
});

test("formatCashTooltipLabel keeps full date for tooltips", () => {
  expect(formatCashTooltipLabel("2026-10-05")).toBe("05 Okt 2026");
  expect(formatCashTooltipLabel("2026-10")).toBe("Okt 2026");
});

test("pickCashAxisTickIndexes thins labels but keeps first and last", () => {
  const wide = pickCashAxisTickIndexes(31, 900);
  expect(wide[0]).toBe(0);
  expect(wide[wide.length - 1]).toBe(30);
  expect(wide.length).toBeLessThanOrEqual(9);

  const mobile = pickCashAxisTickIndexes(31, 390);
  expect(mobile[0]).toBe(0);
  expect(mobile[mobile.length - 1]).toBe(30);
  expect(mobile.length).toBeLessThanOrEqual(5);
  expect(mobile.length).toBeLessThan(wide.length);
});

test("mapOwnerCashTrend never exposes raw day/month grain labels", () => {
  const empty = mapOwnerCashTrend({
    status: "empty",
    grain: "day",
    points: [],
  });
  expect(empty.grain).toBe("Harian");
  expect(empty.grainKey).toBe("day");

  const mapped = mapOwnerCashTrend({
    status: "complete",
    grain: "day",
    points: [
      { period: "2026-10-01", cash_in: 1000, cash_out: 0 },
      { period: "2026-10-08", cash_in: 2000, cash_out: 500 },
    ],
  });
  expect(mapped.grain).toBe("Harian");
  expect(mapped.points).toHaveLength(2);
  expect(mapped.points[0].label).toBe("01 Okt");
  expect(mapped.points[0].fullLabel).toBe("01 Okt 2026");
  expect(mapped.points[0].cash_in).toBe(1000);
  expect(mapped.points[1].label).toBe("08 Okt");
  expect(mapped.points[1].fullLabel).toBe("08 Okt 2026");
});

test("mapOwnerCashTrend monthly showYear only when span crosses years", () => {
  const singleYear = mapOwnerCashTrend({
    status: "complete",
    grain: "month",
    points: [
      { period: "2026-01", cash_in: 1, cash_out: 0 },
      { period: "2026-02", cash_in: 2, cash_out: 0 },
    ],
  });
  expect(singleYear.grain).toBe("Bulanan");
  expect(singleYear.points[0].label).toBe("Jan");

  const multiYear = mapOwnerCashTrend({
    status: "complete",
    grain: "month",
    points: [
      { period: "2025-12", cash_in: 1, cash_out: 0 },
      { period: "2026-01", cash_in: 2, cash_out: 0 },
    ],
  });
  expect(multiYear.points[0].label).toBe("Des 2025");
  expect(multiYear.points[1].label).toBe("Jan 2026");
});
