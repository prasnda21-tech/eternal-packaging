/** Millimetre geometry for the print designer. Zoom changes the screen, not the print size. */

export const PX_PER_MM = 3.2;

export function isTypingTarget(target) {
  if (!target || !target.tagName) return false;
  const tag = String(target.tagName).toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return Boolean(target.isContentEditable);
}

export function pointerDeltaToMm(dxPx, dyPx, zoom, pxPerMm = PX_PER_MM) {
  const scale = (Number(zoom) || 1) * pxPerMm;
  if (!scale) return { dx: 0, dy: 0 };
  return { dx: dxPx / scale, dy: dyPx / scale };
}

export function snapMm(value, snapMmValue, enabled) {
  const snap = Number(snapMmValue) || 1;
  if (!enabled) return roundMm(value);
  return roundMm(Math.round(Number(value) / snap) * snap);
}

export function roundMm(value) {
  return Math.round(Number(value) * 10) / 10;
}

export function innerWidthMm(design) {
  const paper = design?.paper || {};
  const width = Number(paper.width_mm) || 210;
  const height = Number(paper.height_mm) || 297;
  const oriented = paper.orientation === "landscape" ? { width: height, height: width } : { width, height };
  return oriented.width - Number(paper.margin_left_mm || 0) - Number(paper.margin_right_mm || 0);
}

export function paperPixelSize(design, zoom) {
  const paper = design?.paper || {};
  let width = Number(paper.width_mm) || 210;
  let height = Number(paper.height_mm) || 297;
  if (paper.orientation === "landscape") {
    const swap = width;
    width = height;
    height = swap;
  }
  const scale = (Number(zoom) || 1) * PX_PER_MM;
  return { width: width * scale, height: height * scale, scale };
}

export function fitZoom(viewportWidth, paperWidthMm) {
  const available = Math.max(120, viewportWidth - 24);
  const natural = (Number(paperWidthMm) || 210) * PX_PER_MM;
  return Math.max(0.4, Math.min(2.5, available / natural));
}

export function moveElement(element, dx, dy, bandHeight, innerWidth, snap, snapEnabled) {
  const next = {
    ...element,
    x_mm: snapMm(Number(element.x_mm) + dx, snap, snapEnabled),
    y_mm: snapMm(Number(element.y_mm) + dy, snap, snapEnabled),
  };
  next.x_mm = clamp(next.x_mm, 0, Math.max(0, innerWidth - Number(element.w_mm)));
  next.y_mm = clamp(next.y_mm, 0, Math.max(0, bandHeight - Number(element.h_mm)));
  return next;
}

export function resizeElement(element, dw, dh, bandHeight, innerWidth, snap, snapEnabled) {
  const width = Math.max(4, snapMm(Number(element.w_mm) + dw, snap, snapEnabled));
  const height = Math.max(3, snapMm(Number(element.h_mm) + dh, snap, snapEnabled));
  return {
    ...element,
    w_mm: Math.min(width, Math.max(4, innerWidth - Number(element.x_mm))),
    h_mm: Math.min(height, Math.max(3, bandHeight - Number(element.y_mm))),
  };
}

export function elementOutOfBand(element, bandHeight, innerWidth) {
  const x = Number(element.x_mm) || 0;
  const y = Number(element.y_mm) || 0;
  const w = Number(element.w_mm) || 0;
  const h = Number(element.h_mm) || 0;
  return x < -0.05 || y < -0.05 || x + w > innerWidth + 0.4 || y + h > bandHeight + 0.4 || w <= 0 || h <= 0;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}
