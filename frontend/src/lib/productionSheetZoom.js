export const PRODUCTION_SHEET_ZOOM_MIN = 0.60;
export const PRODUCTION_SHEET_ZOOM_MAX = 1.5;
export const PRODUCTION_SHEET_ZOOM_DEFAULT = 1;
export const PRODUCTION_SHEET_ZOOM_STORAGE_KEY = "eternal.ui.production-sheet-zoom";
export const PRODUCTION_SHEET_ZOOM_CSS_VAR = "--production-sheet-zoom";
export const PRODUCTION_SHEET_ZOOM_SENSITIVITY = 0.0017;
export const PRODUCTION_SHEET_WHEEL_DELTA_CLAMP = 800;
export const PRODUCTION_SHEET_WHEEL_ACCEL = 0.0006;
export const PRODUCTION_SHEET_WHEEL_ACCEL_CAP = 0.4;
export const PRODUCTION_SHEET_WHEEL_LINE_PX = 40;
export const PRODUCTION_SHEET_WHEEL_PAGE_PX = 400;
export const PRODUCTION_SHEET_ZOOM_PERSIST_MS = 360;
export const PRODUCTION_SHEET_PINCH_MOVE_PX = 10;
export const PRODUCTION_SHEET_ZOOM_SMOOTH_RATE = 24;
export const PRODUCTION_SHEET_ZOOM_PINCH_SMOOTH_RATE = 34;
export const PRODUCTION_SHEET_ZOOM_SMOOTH_EPSILON = 0.0008;
export const PRODUCTION_SHEET_ZOOM_DT_MAX = 0.05;

export function clampProductionSheetZoom(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return PRODUCTION_SHEET_ZOOM_DEFAULT;
  if (n < PRODUCTION_SHEET_ZOOM_MIN) return PRODUCTION_SHEET_ZOOM_MIN;
  if (n > PRODUCTION_SHEET_ZOOM_MAX) return PRODUCTION_SHEET_ZOOM_MAX;
  return Math.round(n * 10000) / 10000;
}

export function productionSheetZoomStorageKey(userId) {
  const uid = userId != null && String(userId).trim() !== "" ? String(userId).trim() : "";
  return uid ? `${PRODUCTION_SHEET_ZOOM_STORAGE_KEY}.${uid}` : PRODUCTION_SHEET_ZOOM_STORAGE_KEY;
}

export function readProductionSheetZoom(storage, userId) {
  try {
    const store = storage || (typeof localStorage !== "undefined" ? localStorage : null);
    if (!store) return PRODUCTION_SHEET_ZOOM_DEFAULT;
    const raw = store.getItem(productionSheetZoomStorageKey(userId));
    if (raw == null || raw === "") return PRODUCTION_SHEET_ZOOM_DEFAULT;
    return clampProductionSheetZoom(Number(raw));
  } catch {
    return PRODUCTION_SHEET_ZOOM_DEFAULT;
  }
}

export function persistProductionSheetZoom(zoom, storage, userId) {
  try {
    const store = storage || (typeof localStorage !== "undefined" ? localStorage : null);
    if (!store) return;
    store.setItem(productionSheetZoomStorageKey(userId), String(clampProductionSheetZoom(zoom)));
  } catch {
    /* quota / private mode */
  }
}

export function shouldHandleProductionSheetWheel(event) {
  if (!event) return false;
  return event.ctrlKey === true;
}

export function normalizeProductionSheetWheelDelta(deltaY, deltaMode = 0) {
  let dy = Number(deltaY) || 0;
  if (deltaMode === 1) dy *= PRODUCTION_SHEET_WHEEL_LINE_PX;
  if (deltaMode === 2) dy *= PRODUCTION_SHEET_WHEEL_PAGE_PX;
  if (!Number.isFinite(dy) || dy === 0) return 0;
  return Math.max(-PRODUCTION_SHEET_WHEEL_DELTA_CLAMP, Math.min(PRODUCTION_SHEET_WHEEL_DELTA_CLAMP, dy));
}

export function accelerateProductionSheetWheelDelta(delta) {
  const mag = Math.abs(Number(delta) || 0);
  if (!(mag > 0)) return 0;
  const gain = 1 + Math.min(mag * PRODUCTION_SHEET_WHEEL_ACCEL, PRODUCTION_SHEET_WHEEL_ACCEL_CAP);
  return delta * gain;
}

export function productionSheetZoomFromNormalizedDelta(current, normalizedDelta) {
  const delta = accelerateProductionSheetWheelDelta(Number(normalizedDelta) || 0);
  if (delta === 0) return clampProductionSheetZoom(current);
  return clampProductionSheetZoom(Number(current) * Math.exp(-delta * PRODUCTION_SHEET_ZOOM_SENSITIVITY));
}

export function productionSheetZoomFromWheel(current, deltaY, deltaMode = 0) {
  return productionSheetZoomFromNormalizedDelta(
    current,
    normalizeProductionSheetWheelDelta(deltaY, deltaMode),
  );
}

export function productionSheetZoomSmoothDt(rawSeconds) {
  const dt = Number(rawSeconds);
  if (!Number.isFinite(dt) || dt <= 0) return 0;
  return Math.min(dt, PRODUCTION_SHEET_ZOOM_DT_MAX);
}

export function productionSheetZoomSmoothAlpha(dtSeconds, rate = PRODUCTION_SHEET_ZOOM_SMOOTH_RATE) {
  const dt = productionSheetZoomSmoothDt(dtSeconds);
  if (!(dt > 0)) return 0;
  const r = Number(rate);
  const smoothRate = Number.isFinite(r) && r > 0 ? r : PRODUCTION_SHEET_ZOOM_SMOOTH_RATE;
  return 1 - Math.exp(-smoothRate * dt);
}

export function stepProductionSheetDisplayZoom(
  displayZoom,
  targetZoom,
  dtSeconds,
  rate = PRODUCTION_SHEET_ZOOM_SMOOTH_RATE,
) {
  const target = clampProductionSheetZoom(targetZoom);
  const display = Number(displayZoom);
  if (!Number.isFinite(display)) {
    return { displayZoom: target, done: true };
  }
  const gap = target - display;
  if (Math.abs(gap) <= PRODUCTION_SHEET_ZOOM_SMOOTH_EPSILON) {
    return { displayZoom: target, done: true };
  }
  const dt = productionSheetZoomSmoothDt(dtSeconds);
  if (!(dt > 0)) {
    return { displayZoom: display, done: false };
  }
  const next = display + gap * productionSheetZoomSmoothAlpha(dt, rate);
  if (
    Math.abs(target - next) <= PRODUCTION_SHEET_ZOOM_SMOOTH_EPSILON
    || Math.abs(next - display) <= PRODUCTION_SHEET_ZOOM_SMOOTH_EPSILON * 0.4
  ) {
    return { displayZoom: target, done: true };
  }
  return { displayZoom: next, done: false };
}

export function simulateProductionSheetDisplayZoom(
  displayZoom,
  targetZoom,
  options = {},
) {
  const dt = productionSheetZoomSmoothDt(options.dtSeconds);
  const rate = options.rate == null ? PRODUCTION_SHEET_ZOOM_SMOOTH_RATE : options.rate;
  const elapsedMs = Number(options.elapsedMs);
  const maxFrames = Number.isFinite(elapsedMs) && dt > 0
    ? Math.max(1, Math.round(elapsedMs / (dt * 1000)))
    : (Number.isFinite(options.maxFrames) ? options.maxFrames : 48);
  const samples = [];
  let current = Number(displayZoom);
  let done = false;
  for (let i = 0; i < maxFrames; i += 1) {
    const stepped = stepProductionSheetDisplayZoom(current, targetZoom, dt, rate);
    current = stepped.displayZoom;
    done = stepped.done;
    samples.push({
      frame: i + 1,
      ms: Math.round((i + 1) * dt * 1000),
      displayZoom: current,
      done,
    });
    if (done) break;
  }
  return {
    displayZoom: current,
    done,
    samples,
    targetZoom: clampProductionSheetZoom(targetZoom),
  };
}

export function applyProductionSheetZoomVar(element, zoom) {
  if (!element || !element.style) return clampProductionSheetZoom(zoom);
  const next = clampProductionSheetZoom(zoom);
  const token = String(next);
  element.style.setProperty(PRODUCTION_SHEET_ZOOM_CSS_VAR, token);
  if (element.dataset) element.dataset.productionSheetZoom = token;
  if (typeof element.querySelector === "function") {
    const table = element.querySelector(".production-sheet-table");
    if (table?.style) {
      table.style.setProperty(PRODUCTION_SHEET_ZOOM_CSS_VAR, token);
      if (table.dataset) table.dataset.productionSheetZoom = token;
    }
  }
  return next;
}

export function pinchDistance(a, b) {
  if (!a || !b) return 0;
  const dx = Number(a.clientX) - Number(b.clientX);
  const dy = Number(a.clientY) - Number(b.clientY);
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) return 0;
  return Math.hypot(dx, dy);
}

export function pinchMidpoint(a, b) {
  if (!a || !b) return { x: 0, y: 0 };
  const x = (Number(a.clientX) + Number(b.clientX)) / 2;
  const y = (Number(a.clientY) + Number(b.clientY)) / 2;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return { x: 0, y: 0 };
  return { x, y };
}

export function pinchAnchorInViewport(viewportRect, a, b) {
  const mid = pinchMidpoint(a, b);
  const left = Number(viewportRect?.left) || 0;
  const top = Number(viewportRect?.top) || 0;
  return {
    originX: mid.x - left,
    originY: mid.y - top,
    midX: mid.x,
    midY: mid.y,
  };
}

export function productionSheetZoomFromPinch(startZoom, startDistance, currentDistance) {
  const start = Number(startDistance);
  const current = Number(currentDistance);
  if (!(start > 0) || !(current > 0)) return clampProductionSheetZoom(startZoom);
  return clampProductionSheetZoom(Number(startZoom) * (current / start));
}

export function createSheetPinchGesture(zoom = PRODUCTION_SHEET_ZOOM_DEFAULT) {
  return {
    pinching: false,
    suppressClick: false,
    moved: false,
    startDistance: 0,
    startZoom: clampProductionSheetZoom(zoom),
    startCenterX: 0,
    startCenterY: 0,
    startScrollLeft: 0,
    startScrollTop: 0,
  };
}

export function beginSheetPinch(gesture, startZoom, startDistance, meta = {}) {
  return {
    pinching: true,
    suppressClick: false,
    moved: false,
    startDistance: Number(startDistance) || 0,
    startZoom: clampProductionSheetZoom(startZoom),
    startCenterX: Number(meta.centerX) || 0,
    startCenterY: Number(meta.centerY) || 0,
    startScrollLeft: Number(meta.scrollLeft) || 0,
    startScrollTop: Number(meta.scrollTop) || 0,
  };
}

export function updateSheetPinch(gesture, currentDistance, moveThreshold = PRODUCTION_SHEET_PINCH_MOVE_PX) {
  const startDistance = Number(gesture?.startDistance) || 0;
  const dist = Number(currentDistance) || 0;
  const moved = Boolean(gesture?.moved) || Math.abs(dist - startDistance) >= moveThreshold;
  return {
    pinching: true,
    suppressClick: moved,
    moved,
    startDistance,
    startZoom: gesture?.startZoom,
    startCenterX: gesture?.startCenterX,
    startCenterY: gesture?.startCenterY,
    startScrollLeft: gesture?.startScrollLeft,
    startScrollTop: gesture?.startScrollTop,
    zoom: productionSheetZoomFromPinch(gesture?.startZoom, startDistance, dist),
  };
}

export function endSheetPinch(gesture) {
  return {
    pinching: false,
    suppressClick: Boolean(gesture?.moved),
    moved: false,
    startDistance: 0,
    startZoom: gesture?.startZoom,
    startCenterX: 0,
    startCenterY: 0,
    startScrollLeft: 0,
    startScrollTop: 0,
  };
}

export function productionSheetZoomStyle(zoom) {
  return { [PRODUCTION_SHEET_ZOOM_CSS_VAR]: String(clampProductionSheetZoom(zoom)) };
}

export function zoomScrollPreserve(viewport, oldZoom, newZoom, originX, originY, fromScroll) {
  if (!viewport) return;
  const oldZ = Number(oldZoom);
  const newZ = Number(newZoom);
  if (!(oldZ > 0) || !(newZ > 0) || oldZ === newZ) return;
  const prevLeft = fromScroll && Number.isFinite(fromScroll.left)
    ? Number(fromScroll.left)
    : (Number(viewport.scrollLeft) || 0);
  const prevTop = fromScroll && Number.isFinite(fromScroll.top)
    ? Number(fromScroll.top)
    : (Number(viewport.scrollTop) || 0);
  const rectW = viewport.clientWidth || 0;
  const rectH = viewport.clientHeight || 0;
  const contentOx = fromScroll && Number.isFinite(fromScroll.originX) ? fromScroll.originX : (
    Number.isFinite(originX) ? originX : rectW / 2
  );
  const contentOy = fromScroll && Number.isFinite(fromScroll.originY) ? fromScroll.originY : (
    Number.isFinite(originY) ? originY : rectH / 2
  );
  const ox = Number.isFinite(originX) ? originX : contentOx;
  const oy = Number.isFinite(originY) ? originY : contentOy;
  const contentX = (prevLeft + contentOx) / oldZ;
  const contentY = (prevTop + contentOy) / oldZ;
  viewport.scrollLeft = Math.max(0, contentX * newZ - ox);
  viewport.scrollTop = Math.max(0, contentY * newZ - oy);
}

export function eventTouches(event) {
  if (event?.touches && event.touches.length) {
    return Array.from(event.touches).map((t) => ({ clientX: t.clientX, clientY: t.clientY }));
  }
  return [];
}

export function shouldSuppressSheetRowClick(gesture) {
  return Boolean(gesture?.pinching || gesture?.suppressClick);
}
