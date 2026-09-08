import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  PRODUCTION_SHEET_ZOOM_PERSIST_MS,
  PRODUCTION_SHEET_ZOOM_PINCH_SMOOTH_RATE,
  PRODUCTION_SHEET_ZOOM_SMOOTH_RATE,
  applyProductionSheetZoomVar,
  beginSheetPinch,
  clampProductionSheetZoom,
  createSheetPinchGesture,
  endSheetPinch,
  eventTouches,
  persistProductionSheetZoom,
  pinchDistance,
  productionSheetZoomFromNormalizedDelta,
  productionSheetZoomSmoothDt,
  readProductionSheetZoom,
  normalizeProductionSheetWheelDelta,
  shouldHandleProductionSheetWheel,
  stepProductionSheetDisplayZoom,
  updateSheetPinch,
  zoomScrollPreserve,
} from "../lib/productionSheetZoom";

export default function useProductionSheetZoom() {
  const [zoom, setZoom] = useState(() => readProductionSheetZoom());
  const [viewportNode, setViewportNode] = useState(null);
  const viewportRef = useRef(null);
  const targetZoomRef = useRef(zoom);
  const displayZoomRef = useRef(zoom);
  const originRef = useRef(null);
  const lastTimeRef = useRef(0);
  const rafRef = useRef(0);
  const smoothRateRef = useRef(PRODUCTION_SHEET_ZOOM_SMOOTH_RATE);
  const pendingZoomRef = useRef(null);
  const pendingWheelDeltaRef = useRef(null);
  const pendingWheelStartRef = useRef(null);
  const persistTimerRef = useRef(0);
  const gestureRef = useRef(createSheetPinchGesture(zoom));
  const tickRef = useRef(null);

  const setViewportRef = useCallback((node) => {
    viewportRef.current = node;
    setViewportNode(node);
  }, []);

  const persistSoon = useCallback((next) => {
    if (persistTimerRef.current) window.clearTimeout(persistTimerRef.current);
    persistTimerRef.current = window.setTimeout(() => {
      persistTimerRef.current = 0;
      persistProductionSheetZoom(next);
      setZoom((prev) => (prev === next ? prev : next));
    }, PRODUCTION_SHEET_ZOOM_PERSIST_MS);
  }, []);

  const paintDisplay = useCallback((oldDisplay, nextDisplay, origin) => {
    const el = viewportRef.current;
    const left = el ? Number(el.scrollLeft) || 0 : 0;
    const top = el ? Number(el.scrollTop) || 0 : 0;
    if (el) {
      applyProductionSheetZoomVar(el, nextDisplay);
      if (nextDisplay !== oldDisplay) {
        zoomScrollPreserve(el, oldDisplay, nextDisplay, origin?.originX, origin?.originY, {
          left,
          top,
          originX: origin?.originX,
          originY: origin?.originY,
        });
      }
    }
    displayZoomRef.current = nextDisplay;
  }, []);

  tickRef.current = (now) => {
    rafRef.current = 0;
    const last = lastTimeRef.current;
    lastTimeRef.current = now;
    if (!(last > 0)) {
      rafRef.current = window.requestAnimationFrame((time) => tickRef.current(time));
      return;
    }
    const dt = productionSheetZoomSmoothDt((now - last) / 1000);
    pendingWheelDeltaRef.current = null;
    pendingWheelStartRef.current = null;
    pendingZoomRef.current = null;

    const oldDisplay = displayZoomRef.current;
    const target = targetZoomRef.current;
    const stepped = stepProductionSheetDisplayZoom(oldDisplay, target, dt, smoothRateRef.current);
    paintDisplay(oldDisplay, stepped.displayZoom, originRef.current);
    if (stepped.done) {
      lastTimeRef.current = 0;
      return;
    }
    rafRef.current = window.requestAnimationFrame((time) => tickRef.current(time));
  };

  const ensureLoop = useCallback(() => {
    if (rafRef.current) return;
    rafRef.current = window.requestAnimationFrame((time) => tickRef.current(time));
  }, []);

  const setTargetZoom = useCallback((next, origin, rate) => {
    const target = clampProductionSheetZoom(next);
    targetZoomRef.current = target;
    pendingZoomRef.current = target;
    if (origin) originRef.current = origin;
    if (Number.isFinite(rate) && rate > 0) smoothRateRef.current = rate;
    persistSoon(target);
    ensureLoop();
    return target;
  }, [ensureLoop, persistSoon]);

  const applyZoom = useCallback((next, origin) => {
    pendingWheelDeltaRef.current = null;
    pendingWheelStartRef.current = null;
    return setTargetZoom(next, origin, PRODUCTION_SHEET_ZOOM_PINCH_SMOOTH_RATE);
  }, [setTargetZoom]);

  useLayoutEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    applyProductionSheetZoomVar(el, displayZoomRef.current);
  });

  useEffect(() => () => {
    if (rafRef.current) window.cancelAnimationFrame(rafRef.current);
    if (persistTimerRef.current) window.clearTimeout(persistTimerRef.current);
  }, []);

  useEffect(() => {
    const resumeIfNeeded = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      if (Math.abs(targetZoomRef.current - displayZoomRef.current) <= 0.0008) return;
      lastTimeRef.current = 0;
      ensureLoop();
    };
    document.addEventListener("visibilitychange", resumeIfNeeded);
    window.addEventListener("focus", resumeIfNeeded);
    return () => {
      document.removeEventListener("visibilitychange", resumeIfNeeded);
      window.removeEventListener("focus", resumeIfNeeded);
    };
  }, [ensureLoop]);

  useEffect(() => {
    const el = viewportNode;
    if (!el) return undefined;

    const originFromClient = (clientX, clientY) => {
      const rect = el.getBoundingClientRect();
      return {
        originX: clientX - rect.left,
        originY: clientY - rect.top,
      };
    };

    const onWheel = (event) => {
      if (!shouldHandleProductionSheetWheel(event)) return;
      event.preventDefault();
      event.stopPropagation();
      if (pendingWheelDeltaRef.current == null) {
        pendingWheelDeltaRef.current = 0;
        pendingWheelStartRef.current = targetZoomRef.current;
      }
      pendingWheelDeltaRef.current += normalizeProductionSheetWheelDelta(event.deltaY, event.deltaMode);
      const target = productionSheetZoomFromNormalizedDelta(
        pendingWheelStartRef.current,
        pendingWheelDeltaRef.current,
      );
      setTargetZoom(target, originFromClient(event.clientX, event.clientY), PRODUCTION_SHEET_ZOOM_SMOOTH_RATE);
    };

    const onTouchStart = (event) => {
      const touches = eventTouches(event);
      if (touches.length < 2) return;
      const dist = pinchDistance(touches[0], touches[1]);
      if (!(dist > 0)) return;
      const midX = (touches[0].clientX + touches[1].clientX) / 2;
      const midY = (touches[0].clientY + touches[1].clientY) / 2;
      const origin = originFromClient(midX, midY);
      const visual = displayZoomRef.current;
      targetZoomRef.current = visual;
      pendingZoomRef.current = visual;
      pendingWheelDeltaRef.current = null;
      pendingWheelStartRef.current = null;
      smoothRateRef.current = PRODUCTION_SHEET_ZOOM_PINCH_SMOOTH_RATE;
      gestureRef.current = beginSheetPinch(gestureRef.current, visual, dist, {
        centerX: origin.originX,
        centerY: origin.originY,
        scrollLeft: el.scrollLeft,
        scrollTop: el.scrollTop,
      });
    };

    const onTouchMove = (event) => {
      const touches = eventTouches(event);
      if (!gestureRef.current.pinching || touches.length < 2) return;
      event.preventDefault();
      const dist = pinchDistance(touches[0], touches[1]);
      const next = updateSheetPinch(gestureRef.current, dist);
      gestureRef.current = next;
      const midX = (touches[0].clientX + touches[1].clientX) / 2;
      const midY = (touches[0].clientY + touches[1].clientY) / 2;
      applyZoom(next.zoom, originFromClient(midX, midY));
    };

    const endPinch = () => {
      if (!gestureRef.current.pinching && !gestureRef.current.suppressClick) return;
      gestureRef.current = endSheetPinch(gestureRef.current);
      if (!gestureRef.current.suppressClick) return;
      window.setTimeout(() => {
        if (!gestureRef.current.pinching) {
          gestureRef.current = createSheetPinchGesture(targetZoomRef.current);
        }
      }, 280);
    };

    el.addEventListener("wheel", onWheel, { passive: false, capture: true });
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: false });
    el.addEventListener("touchend", endPinch);
    el.addEventListener("touchcancel", endPinch);
    return () => {
      el.removeEventListener("wheel", onWheel, { capture: true });
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("touchend", endPinch);
      el.removeEventListener("touchcancel", endPinch);
    };
  }, [applyZoom, setTargetZoom, viewportNode]);

  const consumeClickSuppression = useCallback(() => {
    if (gestureRef.current.pinching || gestureRef.current.suppressClick) {
      gestureRef.current = {
        ...gestureRef.current,
        suppressClick: false,
      };
      return true;
    }
    return false;
  }, []);

  return { zoom, applyZoom, setViewportRef, viewportRef, consumeClickSuppression, gestureRef };
}
