import {
  CATEGORY_OPTIONS,
  categoryClass,
  formatIdDate,
  mapPoLateSummary,
  poLateDrill,
  poLateTrackingPath,
} from "./poLateTracking";
import { isPoLateVisualFixtureEnabled } from "./poLateTrackingVisualFixture";

test("tracking path keeps late filter for refresh/back", () => {
  expect(poLateTrackingPath({ late: true })).toBe("/produksi/tracking-po-terlambat?late=1");
  expect(poLateTrackingPath({ late: true, category: "kritis", q: "PO-1" })).toContain("category=kritis");
  expect(poLateTrackingPath({ late: true, category: "kritis", q: "PO-1" })).toContain("q=PO-1");
  expect(poLateDrill().path).toContain("late=1");
  expect(poLateDrill().any_of).toContain("dashboard.production.view");
});

test("summary mapper preserves counts and empty state", () => {
  expect(mapPoLateSummary(null).status).toBe("unavailable");
  const empty = mapPoLateSummary({ status: "empty", total: 0, counts: {} });
  expect(empty.status).toBe("empty");
  expect(empty.overdue).toBe(0);
  const full = mapPoLateSummary({
    status: "complete",
    total: 4,
    counts: { lewat_1_hari: 1, terlambat: 2, kritis: 1 },
    note: "PO aktif melewati tanggal pengiriman.",
  });
  expect(full.status).toBe("complete");
  expect(full.overdue).toBe(4);
  expect(full.counts.kritis).toBe(1);
  expect(full.drill.path).toContain("tracking-po-terlambat");
  expect(full.drill.path).not.toContain("visualFixture");
});

test("visualFixture drill flag never sticks from payload when NODE_ENV is production", () => {
  const prev = process.env.NODE_ENV;
  process.env.NODE_ENV = "production";
  try {
    const mapped = mapPoLateSummary({
      status: "complete",
      total: 1,
      counts: { lewat_1_hari: 1, terlambat: 0, kritis: 0 },
      visual_fixture: true,
    });
    expect(mapped.drill.path).not.toContain("visualFixture");
    expect(isPoLateVisualFixtureEnabled(new URLSearchParams("visualFixture=1"))).toBe(false);
  } finally {
    process.env.NODE_ENV = prev;
  }
});

test("category helpers and date format", () => {
  expect(categoryClass("kritis")).toBe("is-critical");
  expect(categoryClass("terlambat")).toBe("is-late");
  expect(categoryClass("lewat_1_hari")).toBe("is-warn");
  expect(formatIdDate("2026-10-29")).toBe("29/10/2026");
  expect(formatIdDate("")).toBe("—");
  expect(CATEGORY_OPTIONS.some((row) => row.value === "kritis")).toBe(true);
});
