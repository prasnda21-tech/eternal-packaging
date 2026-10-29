import {
  buildPoLateVisualFixturePayload,
  buildPoLateVisualSummary,
  isPoLateVisualFixtureEnabled,
} from "./poLateTrackingVisualFixture";

test("visual fixture disabled in production and without flag", () => {
  const params = new URLSearchParams("late=1");
  expect(isPoLateVisualFixtureEnabled(params)).toBe(false);
  expect(isPoLateVisualFixtureEnabled(new URLSearchParams("visualFixture=1"))).toBe(
    process.env.NODE_ENV !== "production",
  );
});

test("fixture helpers hard-disable when NODE_ENV is production", () => {
  const prev = process.env.NODE_ENV;
  process.env.NODE_ENV = "production";
  try {
    expect(isPoLateVisualFixtureEnabled(new URLSearchParams("visualFixture=1"))).toBe(false);
    expect(isPoLateVisualFixtureEnabled(new URLSearchParams("late=1&visualFixture=1"))).toBe(false);
  } finally {
    process.env.NODE_ENV = prev;
  }
});

test("fixture module and consumers keep production guards in source", () => {
  const fs = require("fs");
  const path = require("path");
  const fixtureSrc = fs.readFileSync(path.join(__dirname, "poLateTrackingVisualFixture.js"), "utf8");
  const mapperSrc = fs.readFileSync(path.join(__dirname, "poLateTracking.js"), "utf8");
  const dashSrc = fs.readFileSync(path.join(__dirname, "../pages/ProductionDashboard.jsx"), "utf8");
  expect(fixtureSrc).toContain('process.env.NODE_ENV === "production"');
  expect(mapperSrc).toContain('process.env.NODE_ENV !== "production"');
  expect(dashSrc).toContain("isPoLateVisualFixtureEnabled");
  expect(fixtureSrc).not.toMatch(/db\.|insertOne|updateOne|collection\(/);
});

test("fixture payload keeps active total separate from filtered total", () => {
  const all = buildPoLateVisualFixturePayload({ page: 1, pageSize: 10 });
  expect(all.active_late_total).toBeGreaterThan(10);
  expect(all.rows).toHaveLength(10);
  expect(all.visual_fixture).toBe(true);
  expect(all.branch_options.length).toBeGreaterThan(1);
  expect(all.branch_options.every((row) => row.id && row.name)).toBe(true);
  const krit = buildPoLateVisualFixturePayload({ page: 1, pageSize: 50, category: "kritis" });
  expect(krit.filtered_total).toBeLessThan(krit.active_late_total);
  expect(krit.rows.every((row) => row.category === "kritis")).toBe(true);
  const byBranch = buildPoLateVisualFixturePayload({
    page: 1,
    pageSize: 50,
    branchId: all.branch_options[0].id,
  });
  expect(byBranch.rows.every((row) => row.branch_id === all.branch_options[0].id)).toBe(true);
  const summary = buildPoLateVisualSummary();
  expect(summary.total).toBe(all.active_late_total);
  expect(summary.counts.kritis + summary.counts.terlambat + summary.counts.lewat_1_hari).toBe(summary.total);
});
