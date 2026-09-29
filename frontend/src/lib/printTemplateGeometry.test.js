import {
  elementOutOfBand,
  fitZoom,
  isTypingTarget,
  moveElement,
  pointerDeltaToMm,
  resizeElement,
} from "./printTemplateGeometry";

test("drag distance stays in millimetres when zoom changes", () => {
  const at100 = pointerDeltaToMm(32, 16, 1);
  const at150 = pointerDeltaToMm(48, 24, 1.5);
  expect(at100.dx).toBeCloseTo(10, 5);
  expect(at100.dy).toBeCloseTo(5, 5);
  expect(at150.dx).toBeCloseTo(at100.dx, 5);
  expect(at150.dy).toBeCloseTo(at100.dy, 5);
});

test("resize uses the same zoom conversion", () => {
  const element = { x_mm: 10, y_mm: 4, w_mm: 20, h_mm: 8 };
  const delta = pointerDeltaToMm(32, 16, 2);
  const resized = resizeElement(element, delta.dx, delta.dy, 40, 180, 1, true);
  expect(resized.w_mm).toBe(25);
  expect(resized.h_mm).toBe(11);
});

test("move clamps inside the band", () => {
  const element = { x_mm: 170, y_mm: 30, w_mm: 20, h_mm: 8 };
  const moved = moveElement(element, 40, 40, 36, 180, 1, true);
  expect(moved.x_mm).toBe(160);
  expect(moved.y_mm).toBe(28);
  expect(elementOutOfBand(moved, 36, 180)).toBe(false);
});

test("keyboard edits ignore typing targets", () => {
  expect(isTypingTarget({ tagName: "INPUT" })).toBe(true);
  expect(isTypingTarget({ tagName: "TEXTAREA" })).toBe(true);
  expect(isTypingTarget({ tagName: "DIV", isContentEditable: true })).toBe(true);
  expect(isTypingTarget({ tagName: "BUTTON" })).toBe(false);
});

test("fit zoom uses the viewport and not the stored print size", () => {
  const zoom = fitZoom(700, 210);
  expect(zoom).toBeGreaterThan(0.4);
  expect(zoom).toBeLessThan(2.5);
});
