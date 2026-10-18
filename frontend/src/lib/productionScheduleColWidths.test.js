import {
  PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS,
  clampProductionScheduleColWidth,
  clearProductionScheduleColWidths,
  normalizeProductionScheduleColWidths,
  productionScheduleColWidthsStorageKey,
  productionScheduleResizeLogicalDelta,
  productionScheduleResizeNextWidth,
  productionScheduleStickyCssVars,
  readProductionScheduleColWidths,
  writeProductionScheduleColWidths,
} from "./productionScheduleColWidths";

describe("productionScheduleColWidths", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  test("namespaces storage by user id", () => {
    expect(productionScheduleColWidthsStorageKey("u-a")).toContain("u-a");
    expect(productionScheduleColWidthsStorageKey("u-a")).not.toEqual(
      productionScheduleColWidthsStorageKey("u-b"),
    );
  });

  test("clamps per-column min/max", () => {
    const poMin = clampProductionScheduleColWidth("po", 10);
    expect(poMin).toBeGreaterThanOrEqual(48);
    expect(clampProductionScheduleColWidth("qty", 9999)).toBeLessThanOrEqual(220);
  });

  test("persists and isolates widths per user", () => {
    writeProductionScheduleColWidths({ ...PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS, po: 140 }, "alice");
    writeProductionScheduleColWidths({ ...PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS, po: 160 }, "bob");
    expect(readProductionScheduleColWidths("alice").po).toBe(140);
    expect(readProductionScheduleColWidths("bob").po).toBe(160);
    clearProductionScheduleColWidths("alice");
    expect(readProductionScheduleColWidths("alice").po).toBe(PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS.po);
    expect(readProductionScheduleColWidths("bob").po).toBe(160);
  });

  test("invalid storage falls back to defaults", () => {
    window.localStorage.setItem(productionScheduleColWidthsStorageKey("x"), "{bad");
    expect(normalizeProductionScheduleColWidths(null)).toEqual(PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS);
    expect(readProductionScheduleColWidths("x")).toEqual(PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS);
  });

  test("sticky css vars follow resized identity widths", () => {
    const vars = productionScheduleStickyCssVars({ po: 130, customer: 200 });
    expect(vars["--prod-sticky-po-w"]).toContain("130px");
    expect(vars["--prod-sticky-customer-w"]).toContain("200px");
  });

  test("resize delta converts screen pixels through sheet zoom", () => {
    expect(productionScheduleResizeLogicalDelta(100, 140, 1)).toBe(40);
    expect(productionScheduleResizeLogicalDelta(100, 140, 1.25)).toBeCloseTo(32, 5);
    expect(productionScheduleResizeLogicalDelta(100, 140, 0.8)).toBeCloseTo(50, 5);
    expect(productionScheduleResizeLogicalDelta(100, 140, 0)).toBe(40);
  });

  test("resize next width keeps pointer-aligned logical width under zoom", () => {
    const base = PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS.customer;
    expect(productionScheduleResizeNextWidth("customer", base, 0, 40, 1)).toBe(base + 40);
    expect(productionScheduleResizeNextWidth("customer", base, 0, 40, 1.25)).toBe(
      clampProductionScheduleColWidth("customer", base + 32),
    );
  });
});
