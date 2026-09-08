import {
  PRODUCTION_SHEET_PINCH_MOVE_PX,
  PRODUCTION_SHEET_ZOOM_CSS_VAR,
  PRODUCTION_SHEET_ZOOM_DEFAULT,
  PRODUCTION_SHEET_ZOOM_MAX,
  PRODUCTION_SHEET_ZOOM_MIN,
  PRODUCTION_SHEET_ZOOM_PERSIST_MS,
  PRODUCTION_SHEET_ZOOM_SENSITIVITY,
  PRODUCTION_SHEET_ZOOM_STORAGE_KEY,
  PRODUCTION_SHEET_WHEEL_ACCEL,
  PRODUCTION_SHEET_WHEEL_ACCEL_CAP,
  PRODUCTION_SHEET_WHEEL_DELTA_CLAMP,
  PRODUCTION_SHEET_ZOOM_PINCH_SMOOTH_RATE,
  PRODUCTION_SHEET_ZOOM_SMOOTH_EPSILON,
  PRODUCTION_SHEET_ZOOM_SMOOTH_RATE,
  PRODUCTION_SHEET_ZOOM_DT_MAX,
  accelerateProductionSheetWheelDelta,
  applyProductionSheetZoomVar,
  beginSheetPinch,
  clampProductionSheetZoom,
  createSheetPinchGesture,
  endSheetPinch,
  persistProductionSheetZoom,
  pinchDistance,
  productionSheetZoomFromNormalizedDelta,
  productionSheetZoomFromPinch,
  productionSheetZoomFromWheel,
  productionSheetZoomSmoothAlpha,
  productionSheetZoomSmoothDt,
  productionSheetZoomStyle,
  readProductionSheetZoom,
  normalizeProductionSheetWheelDelta,
  shouldHandleProductionSheetWheel,
  shouldSuppressSheetRowClick,
  simulateProductionSheetDisplayZoom,
  stepProductionSheetDisplayZoom,
  updateSheetPinch,
  zoomScrollPreserve,
} from "./productionSheetZoom";
import fs from "fs";
import path from "path";

describe("productionSheetZoom", () => {
  test("default zoom is compact and within bounds", () => {
    expect(PRODUCTION_SHEET_ZOOM_DEFAULT).toBe(0.85);
    expect(PRODUCTION_SHEET_ZOOM_DEFAULT).toBeGreaterThanOrEqual(PRODUCTION_SHEET_ZOOM_MIN);
    expect(PRODUCTION_SHEET_ZOOM_DEFAULT).toBeLessThanOrEqual(PRODUCTION_SHEET_ZOOM_MAX);
    expect(PRODUCTION_SHEET_ZOOM_SENSITIVITY).toBe(0.0017);
    expect(PRODUCTION_SHEET_WHEEL_ACCEL).toBe(0.0006);
    expect(PRODUCTION_SHEET_WHEEL_ACCEL_CAP).toBe(0.4);
    expect(PRODUCTION_SHEET_WHEEL_DELTA_CLAMP).toBe(800);
    expect(PRODUCTION_SHEET_ZOOM_SMOOTH_RATE).toBeGreaterThanOrEqual(18);
    expect(PRODUCTION_SHEET_ZOOM_SMOOTH_RATE).toBeLessThanOrEqual(28);
    expect(PRODUCTION_SHEET_ZOOM_PINCH_SMOOTH_RATE).toBeGreaterThan(PRODUCTION_SHEET_ZOOM_SMOOTH_RATE);
    expect(PRODUCTION_SHEET_ZOOM_SMOOTH_EPSILON).toBeGreaterThanOrEqual(0.0005);
    expect(PRODUCTION_SHEET_ZOOM_SMOOTH_EPSILON).toBeLessThanOrEqual(0.001);
    expect(PRODUCTION_SHEET_ZOOM_DT_MAX).toBe(0.05);
    expect(PRODUCTION_SHEET_ZOOM_PERSIST_MS).toBeGreaterThanOrEqual(250);
  });

  test("clamps at min and max and rejects invalid values", () => {
    expect(clampProductionSheetZoom(0.2)).toBe(PRODUCTION_SHEET_ZOOM_MIN);
    expect(clampProductionSheetZoom(9)).toBe(PRODUCTION_SHEET_ZOOM_MAX);
    expect(clampProductionSheetZoom(NaN)).toBe(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(clampProductionSheetZoom(-1)).toBe(PRODUCTION_SHEET_ZOOM_MIN);
    expect(clampProductionSheetZoom(0)).toBe(PRODUCTION_SHEET_ZOOM_MIN);
    expect(clampProductionSheetZoom(0.8317)).toBe(0.8317);
    expect(clampProductionSheetZoom(1.07)).toBe(1.07);
  });

  test("Ctrl + wheel changes zoom; wheel without Ctrl does not", () => {
    expect(shouldHandleProductionSheetWheel({ ctrlKey: true, deltaY: 40 })).toBe(true);
    expect(shouldHandleProductionSheetWheel({ ctrlKey: false, deltaY: 40 })).toBe(false);
    const zoomedIn = productionSheetZoomFromWheel(1, -120);
    const zoomedOut = productionSheetZoomFromWheel(1, 120);
    expect(zoomedIn).toBeGreaterThan(1);
    expect(zoomedOut).toBeLessThan(1);
  });

  test("tiny, medium, and large wheel deltas scale continuously without a fixed step", () => {
    const start = PRODUCTION_SHEET_ZOOM_DEFAULT;
    const tinyIn = productionSheetZoomFromWheel(start, -8);
    const tinyOut = productionSheetZoomFromWheel(start, 8);
    const mediumIn = productionSheetZoomFromWheel(start, -120);
    const mediumOut = productionSheetZoomFromWheel(start, 120);
    const largeIn = productionSheetZoomFromWheel(start, -240);
    const largeOut = productionSheetZoomFromWheel(start, 240);
    expect(tinyIn - start).toBeGreaterThan(0);
    expect(tinyIn - start).toBeLessThan(0.03);
    expect(start - tinyOut).toBeGreaterThan(0);
    expect(start - tinyOut).toBeLessThan(0.03);
    expect(mediumIn - start).toBeGreaterThan(tinyIn - start);
    expect(start - mediumOut).toBeGreaterThan(start - tinyOut);
    expect(largeIn - start).toBeGreaterThan(mediumIn - start);
    expect(start - largeOut).toBeGreaterThan(start - mediumOut);
    expect(Math.abs(mediumIn - start)).not.toBeCloseTo(0.05, 3);
    expect(Math.abs(largeIn - start)).not.toBeCloseTo(0.05, 3);
    expect(tinyIn).toBeCloseTo(0.86, 1);
    expect(tinyOut).toBeCloseTo(0.84, 1);
    expect(mediumOut).toBeLessThan(0.75);
    expect(largeOut).toBeLessThan(0.65);
    expect(largeIn).toBeGreaterThan(1.2);
    const tinyGain = accelerateProductionSheetWheelDelta(8) / 8;
    const largeGain = accelerateProductionSheetWheelDelta(240) / 240;
    expect(tinyGain).toBeGreaterThan(1);
    expect(tinyGain).toBeLessThan(1.02);
    expect(largeGain).toBeGreaterThan(tinyGain);
    expect(PRODUCTION_SHEET_WHEEL_ACCEL).toBeGreaterThan(0);
  });

  test("deltaMode line/page normalize to pixel-like units", () => {
    const pixel = productionSheetZoomFromWheel(1, -120, 0);
    const line = productionSheetZoomFromWheel(1, -3, 1);
    expect(line).toBeCloseTo(pixel, 2);
    expect(productionSheetZoomFromWheel(1, -1, 2)).toBeGreaterThan(pixel);
    expect(Math.abs(normalizeProductionSheetWheelDelta(900, 0))).toBe(PRODUCTION_SHEET_WHEEL_DELTA_CLAMP);
  });

  test("repeated fast events in one frame use summed delta, not a per-event cap", () => {
    const start = PRODUCTION_SHEET_ZOOM_DEFAULT;
    const summed = productionSheetZoomFromNormalizedDelta(
      start,
      normalizeProductionSheetWheelDelta(80) + normalizeProductionSheetWheelDelta(80),
    );
    const first = productionSheetZoomFromWheel(start, 80);
    const sequential = productionSheetZoomFromWheel(first, 80);
    expect(start - summed).toBeGreaterThan(start - sequential);
    expect(start - summed).toBeGreaterThan(start - first);
    let zoom = start;
    for (let i = 0; i < 8; i += 1) zoom = productionSheetZoomFromWheel(zoom, -40);
    expect(zoom).toBeGreaterThan(start);
    expect(zoom).toBeLessThanOrEqual(PRODUCTION_SHEET_ZOOM_MAX);
    expect(productionSheetZoomFromWheel(PRODUCTION_SHEET_ZOOM_MIN, 800)).toBe(PRODUCTION_SHEET_ZOOM_MIN);
    expect(productionSheetZoomFromWheel(PRODUCTION_SHEET_ZOOM_MAX, -800)).toBe(PRODUCTION_SHEET_ZOOM_MAX);
    zoom = PRODUCTION_SHEET_ZOOM_MIN;
    for (let i = 0; i < 40; i += 1) zoom = productionSheetZoomFromWheel(zoom, 120);
    expect(zoom).toBe(PRODUCTION_SHEET_ZOOM_MIN);
    zoom = PRODUCTION_SHEET_ZOOM_MAX;
    for (let i = 0; i < 40; i += 1) zoom = productionSheetZoomFromWheel(zoom, -120);
    expect(zoom).toBe(PRODUCTION_SHEET_ZOOM_MAX);
  });

  test("pinch helper uses initial distance and initial zoom 1:1, then clamps", () => {
    expect(pinchDistance({ clientX: 0, clientY: 0 }, { clientX: 3, clientY: 4 })).toBe(5);
    expect(productionSheetZoomFromPinch(1, 100, 120)).toBe(1.2);
    expect(productionSheetZoomFromPinch(0.8, 100, 150)).toBe(1.2);
    expect(productionSheetZoomFromPinch(1, 100, 80)).toBe(0.8);
    expect(productionSheetZoomFromPinch(PRODUCTION_SHEET_ZOOM_DEFAULT, 100, 150))
      .toBe(clampProductionSheetZoom(PRODUCTION_SHEET_ZOOM_DEFAULT * 1.5));
    expect(productionSheetZoomFromPinch(1, 100, 1000)).toBe(PRODUCTION_SHEET_ZOOM_MAX);
    expect(productionSheetZoomFromPinch(1, 100, 10)).toBe(PRODUCTION_SHEET_ZOOM_MIN);
    const started = beginSheetPinch(createSheetPinchGesture(0.85), 0.85, 100);
    const mid = updateSheetPinch(started, 130);
    const again = updateSheetPinch({ ...started, zoom: mid.zoom }, 130);
    expect(again.zoom).toBe(mid.zoom);
    expect(mid.zoom).toBe(productionSheetZoomFromPinch(0.85, 100, 130));
    expect(mid.zoom).toBe(clampProductionSheetZoom(0.85 * 1.3));
  });

  test("pinch end clears active state and only suppresses click after movement", () => {
    const started = beginSheetPinch(createSheetPinchGesture(1), 1, 100, {
      centerX: 40, centerY: 20, scrollLeft: 80, scrollTop: 10,
    });
    expect(started.pinching).toBe(true);
    expect(started.startScrollLeft).toBe(80);
    expect(started.suppressClick).toBe(false);
    const still = updateSheetPinch(started, 104, PRODUCTION_SHEET_PINCH_MOVE_PX);
    expect(still.moved).toBe(false);
    expect(still.suppressClick).toBe(false);
    expect(still.zoom).toBe(productionSheetZoomFromPinch(1, 100, 104));
    const moved = updateSheetPinch(started, 140, PRODUCTION_SHEET_PINCH_MOVE_PX);
    expect(moved.moved).toBe(true);
    expect(moved.suppressClick).toBe(true);
    const ended = endSheetPinch(moved);
    expect(ended.pinching).toBe(false);
    expect(ended.suppressClick).toBe(true);
    expect(ended.startDistance).toBe(0);
    expect(shouldSuppressSheetRowClick(ended)).toBe(true);
    expect(shouldSuppressSheetRowClick(endSheetPinch(still))).toBe(false);
  });

  test("style variable uses the spreadsheet zoom token", () => {
    expect(productionSheetZoomStyle(1.1)[PRODUCTION_SHEET_ZOOM_CSS_VAR]).toBe("1.1");
    const el = { style: { props: {}, setProperty(k, v) { this.props[k] = v; } } };
    applyProductionSheetZoomVar(el, 0.91);
    expect(el.style.props[PRODUCTION_SHEET_ZOOM_CSS_VAR]).toBe("0.91");
  });

  test("localStorage preference restores and persist helper is idle-based", () => {
    const store = {
      data: {},
      getItem(key) { return this.data[key] ?? null; },
      setItem(key, value) { this.data[key] = String(value); },
    };
    expect(readProductionSheetZoom(store)).toBe(PRODUCTION_SHEET_ZOOM_DEFAULT);
    persistProductionSheetZoom(1.25, store);
    expect(store.data[PRODUCTION_SHEET_ZOOM_STORAGE_KEY]).toBe("1.25");
    expect(readProductionSheetZoom(store)).toBe(1.25);
  });

  test("preserves viewport position while zooming and does not reset to origin", () => {
    const viewport = { scrollLeft: 200, scrollTop: 40, clientWidth: 400, clientHeight: 200 };
    zoomScrollPreserve(viewport, 1, 2, 100, 50);
    expect(viewport.scrollLeft).toBe(500);
    expect(viewport.scrollTop).toBe(130);
    expect(viewport.scrollLeft).not.toBe(0);
    expect(viewport.scrollTop).not.toBe(0);
  });

  test("display zoom eases toward target with time-based dt and then stops", () => {
    const start = PRODUCTION_SHEET_ZOOM_DEFAULT;
    const target = productionSheetZoomFromWheel(start, -240);
    expect(target).toBeGreaterThan(1.2);
    const first = stepProductionSheetDisplayZoom(start, target, 1 / 60);
    expect(first.displayZoom).toBeGreaterThan(start);
    expect(first.displayZoom).toBeLessThan(target);
    expect(first.done).toBe(false);
    const hz60 = simulateProductionSheetDisplayZoom(start, target, { dtSeconds: 1 / 60, maxFrames: 8 });
    const hz120 = simulateProductionSheetDisplayZoom(start, target, { dtSeconds: 1 / 120, maxFrames: 16 });
    expect(hz60.displayZoom).toBeCloseTo(hz120.displayZoom, 2);
    expect(hz60.samples[0].displayZoom).not.toBe(target);
    const settled = simulateProductionSheetDisplayZoom(start, target, { dtSeconds: 1 / 60, maxFrames: 40 });
    expect(settled.done).toBe(true);
    expect(settled.displayZoom).toBe(target);
    const extra = stepProductionSheetDisplayZoom(settled.displayZoom, target, 1 / 60);
    expect(extra.done).toBe(true);
    expect(extra.displayZoom).toBe(target);
    expect(productionSheetZoomSmoothAlpha(1 / 60)).toBeCloseTo(1 - Math.exp(-PRODUCTION_SHEET_ZOOM_SMOOTH_RATE / 60), 8);
    expect(productionSheetZoomSmoothDt(0)).toBe(0);
    expect(productionSheetZoomSmoothDt(1 / 120)).toBeCloseTo(1 / 120, 10);
    expect(productionSheetZoomSmoothDt(1 / 144)).toBeCloseTo(1 / 144, 10);
    expect(productionSheetZoomSmoothDt(1 / 90)).toBeCloseTo(1 / 90, 10);
    expect(productionSheetZoomSmoothDt(0.2)).toBe(PRODUCTION_SHEET_ZOOM_DT_MAX);
    expect(stepProductionSheetDisplayZoom(start, target, 0).displayZoom).toBe(start);
    expect(stepProductionSheetDisplayZoom(start, target, 0).done).toBe(false);
  });

  test("same elapsed time interpolates similarly at 60Hz, 90Hz, 120Hz, and 144Hz", () => {
    const start = PRODUCTION_SHEET_ZOOM_DEFAULT;
    const target = productionSheetZoomFromWheel(start, -240);
    const at = (hz) => simulateProductionSheetDisplayZoom(start, target, {
      dtSeconds: 1 / hz,
      elapsedMs: 100,
    }).displayZoom;
    const hz60 = at(60);
    const hz90 = at(90);
    const hz120 = at(120);
    const hz144 = at(144);
    expect(productionSheetZoomSmoothDt(1 / 120)).toBeLessThan(1 / 60);
    expect(hz60).toBeGreaterThan(start);
    expect(hz60).toBeLessThan(target);
    expect(hz90).toBeCloseTo(hz60, 2);
    expect(hz120).toBeCloseTo(hz60, 2);
    expect(hz144).toBeCloseTo(hz60, 2);
    expect(simulateProductionSheetDisplayZoom(start, target, { dtSeconds: 1 / 120, maxFrames: 1 }).samples[0].ms).toBe(8);
  });

  test("fast input keeps the previous target curve and can reverse mid-ease", () => {
    const start = PRODUCTION_SHEET_ZOOM_DEFAULT;
    expect(productionSheetZoomFromWheel(start, 8)).toBe(0.8385);
    expect(productionSheetZoomFromWheel(start, 120)).toBe(0.683);
    expect(productionSheetZoomFromWheel(start, 240)).toBe(0.533);
    expect(productionSheetZoomFromWheel(start, -240)).toBe(1.3556);
    const zoomIn = productionSheetZoomFromWheel(start, -240);
    const mid = simulateProductionSheetDisplayZoom(start, zoomIn, { dtSeconds: 1 / 60, maxFrames: 4 });
    expect(mid.displayZoom).toBeGreaterThan(start);
    expect(mid.displayZoom).toBeLessThan(zoomIn);
    const zoomOut = productionSheetZoomFromWheel(zoomIn, 240);
    const reversed = simulateProductionSheetDisplayZoom(mid.displayZoom, zoomOut, { dtSeconds: 1 / 60, maxFrames: 8 });
    expect(reversed.displayZoom).toBeLessThan(mid.displayZoom);
    expect(reversed.samples.every((sample, idx) => idx === 0 || sample.displayZoom <= reversed.samples[idx - 1].displayZoom)).toBe(true);
    const continued = simulateProductionSheetDisplayZoom(mid.displayZoom, zoomIn, { dtSeconds: 1 / 60, maxFrames: 4 });
    expect(continued.displayZoom).toBeGreaterThan(mid.displayZoom);
    expect(continued.displayZoom).not.toBe(start);
  });

  test("pinch target math stays 1:1 while display interpolation still eases", () => {
    const start = PRODUCTION_SHEET_ZOOM_DEFAULT;
    const target = productionSheetZoomFromPinch(start, 100, 150);
    expect(target).toBe(clampProductionSheetZoom(start * 1.5));
    const first = stepProductionSheetDisplayZoom(start, target, 1 / 60, PRODUCTION_SHEET_ZOOM_PINCH_SMOOTH_RATE);
    expect(first.displayZoom).toBeGreaterThan(start);
    expect(first.displayZoom).toBeLessThan(target);
    const pinch = simulateProductionSheetDisplayZoom(start, target, {
      dtSeconds: 1 / 60,
      rate: PRODUCTION_SHEET_ZOOM_PINCH_SMOOTH_RATE,
      maxFrames: 24,
    });
    expect(pinch.done).toBe(true);
    expect(pinch.displayZoom).toBe(target);
    const wheel = simulateProductionSheetDisplayZoom(start, target, {
      dtSeconds: 1 / 60,
      rate: PRODUCTION_SHEET_ZOOM_SMOOTH_RATE,
      maxFrames: 6,
    });
    expect(pinch.samples[2].displayZoom).toBeGreaterThan(wheel.samples[2].displayZoom);
  });
});

describe("shared production sheet zoom wiring", () => {
  test("desktop and PWA use the same helper with non-passive Ctrl+wheel capture", () => {
    const hookSrc = fs.readFileSync(path.join(__dirname, "../hooks/useProductionSheetZoom.js"), "utf8");
    const desktopSrc = fs.readFileSync(path.join(__dirname, "../pages/ProductionSchedule.jsx"), "utf8");
    const mobileSrc = fs.readFileSync(path.join(__dirname, "../mobile/MobileProductionSchedulePage.jsx"), "utf8");
    const cssSrc = fs.readFileSync(path.join(__dirname, "../productionSheet.css"), "utf8");
    const desktopPageSrc = desktopSrc;
    expect(desktopSrc).toMatch(/useProductionSheetZoom/);
    expect(mobileSrc).toMatch(/useProductionSheetZoom/);
    expect(desktopPageSrc).toMatch(/buildProductionScheduleDisplayRows\(sorted\)/);
    expect(mobileSrc).toMatch(/buildProductionScheduleDisplayRows\(sorted\)/);
    expect(hookSrc).toMatch(/passive:\s*false,\s*capture:\s*true/);
    expect(hookSrc).toMatch(/beginSheetPinch/);
    expect(hookSrc).toMatch(/endSheetPinch/);
    expect(hookSrc).toMatch(/stepProductionSheetDisplayZoom/);
    expect(hookSrc).toMatch(/targetZoomRef/);
    expect(hookSrc).toMatch(/displayZoomRef/);
    expect(hookSrc).toMatch(/productionSheetZoomSmoothDt/);
    expect(hookSrc).toMatch(/PRODUCTION_SHEET_ZOOM_SMOOTH_RATE/);
    expect(hookSrc).toMatch(/PRODUCTION_SHEET_ZOOM_PINCH_SMOOTH_RATE/);
    expect(hookSrc).toMatch(/normalizeProductionSheetWheelDelta/);
    expect(hookSrc).toMatch(/productionSheetZoomFromNormalizedDelta/);
    expect(hookSrc).toMatch(/applyProductionSheetZoomVar/);
    expect(hookSrc).toMatch(/PRODUCTION_SHEET_ZOOM_PERSIST_MS/);
    expect(hookSrc.match(/setZoom/g)).toEqual(["setZoom", "setZoom"]);
    expect(hookSrc).toMatch(/setZoom\(\(prev\)/);
    expect(hookSrc).not.toMatch(/accumulateProductionSheetWheelStep/);
    expect(hookSrc).not.toMatch(/transform:\s*scale/);
    expect(hookSrc).not.toMatch(/scrollWidth/);
    expect(hookSrc).not.toMatch(/1\s*\/\s*60/);
    expect(hookSrc).not.toMatch(/setTimeout\(\s*16/);
    expect(cssSrc).not.toMatch(/transition\s*:/);
    expect(mobileSrc).not.toMatch(/mobile-schedule-zoom-in/);
    expect(desktopSrc).not.toMatch(/mobile-schedule-zoom-in/);
  });
});
