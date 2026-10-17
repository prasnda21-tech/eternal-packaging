import {
  PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS,
  clampProductionScheduleColWidth,
  clearProductionScheduleColWidths,
  normalizeProductionScheduleColWidths,
  productionScheduleColWidthsStorageKey,
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
});
