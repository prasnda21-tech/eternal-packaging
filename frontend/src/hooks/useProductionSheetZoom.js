import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  PRODUCTION_SHEET_CLICK_SUPPRESS_MS,
  PRODUCTION_SHEET_ZOOM_PERSIST_MS,
  PRODUCTION_SHEET_ZOOM_SMOOTH_RATE,
  applyProductionSheetZoomVar,
  beginSheetPinch,
  clampProductionSheetZoom,
  consumeSheetRowClickSuppression,
  createSheetPinchGesture,
  endSheetPinch,
  eventTouches,
  persistProductionSheetZoom,
  pinchAnchorInViewport,
  pinchDistance,
  productionSheetZoomFromWheel,
  productionSheetZoomSmoothDt,
  readProductionSheetZoom,
  shouldHandleProductionSheetWheel,
  stepProductionSheetDisplayZoom,
  updateSheetPinch,
  zoomScrollPreserve,
} from "../lib/productionSheetZoom";

export default function useProductionSheetZoom({ userId } = {}) {
  const persistUserId = String(userId || "").trim();
  const [zoom, setZoom] = useState(() => readProductionSheetZoom(undefined, persistUserId));
  const [viewportNode, setViewportNode] = useState(null);
  const viewportRef = useRef(null);
  const targetZoomRef = useRef(zoom);
  const displayZoomRef = useRef(zoom);
  const originRef = useRef(null);
  const lastTimeRef = useRef(0);
  const rafRef = useRef(0);
  const liveRafRef = useRef(0);
  const smoothRateRef = useRef(PRODUCTION_SHEET_ZOOM_SMOOTH_RATE);
  const pendingZoomRef = useRef(null);
  const pendingLiveRef = useRef(null);
  const persistTimerRef = useRef(0);
  const suppressTimerRef = useRef(0);
  const userIdRef = useRef(persistUserId);
  userIdRef.current = persistUserId;
  const gestureRef = useRef(createSheetPinchGesture(zoom));
  const tickRef = useRef(null);

  const clearSuppressTimer = useCallback(() => {
    if (!suppressTimerRef.current) return;
    window.clearTimeout(suppressTimerRef.current);
    suppressTimerRef.current = 0;
  }, []);

  const disarmClickSuppression = useCallback(() => {
    clearSuppressTimer();
    const consumed = consumeSheetRowClickSuppression(gestureRef.current);
    if (gestureRef.current.pinching) {
      gestureRef.current = consumed.next;
      return;
    }
    gestureRef.current = createSheetPinchGesture(targetZoomRef.current);
  }, [clearSuppressTimer]);

  const armClickSuppression = useCallback(() => {
    gestureRef.current = {
      ...gestureRef.current,
      suppressClick: true,
      moved: true,
    };
    clearSuppressTimer();
    suppressTimerRef.current = window.setTimeout(() => {
      suppressTimerRef.current = 0;
      if (gestureRef.current.pinching) {
        gestureRef.current = {
          ...gestureRef.current,
          suppressClick: false,
        };
        return;
      }
      gestureRef.current = createSheetPinchGesture(targetZoomRef.current);
    }, PRODUCTION_SHEET_CLICK_SUPPRESS_MS);
  }, [clearSuppressTimer]);

  const setViewportRef = useCallback((node) => {
    viewportRef.current = node;
    setViewportNode(node);
  }, []);

  const persistSoon = useCallback((next) => {
    if (persistTimerRef.current) window.clearTimeout(persistTimerRef.current);
    persistTimerRef.current = window.setTimeout(() => {
      persistTimerRef.current = 0;
      persistProductionSheetZoom(next, undefined, userIdRef.current);
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

  const applyLivePinchZoom = useCallback((next, origin) => {
    const target = clampProductionSheetZoom(next);
    targetZoomRef.current = target;
    pendingZoomRef.current = target;
    pendingLiveRef.current = { zoom: target, origin: origin || originRef.current };
    if (origin) originRef.current = origin;
    persistSoon(target);
    if (liveRafRef.current) return target;
    liveRafRef.current = window.requestAnimationFrame(() => {
      liveRafRef.current = 0;
      const pending = pendingLiveRef.current;
      pendingLiveRef.current = null;
      if (!pending) return;
      paintDisplay(displayZoomRef.current, pending.zoom, pending.origin);
    });
    return target;
  }, [paintDisplay, persistSoon]);

  useLayoutEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    applyProductionSheetZoomVar(el, displayZoomRef.current);
  });

  useEffect(() => () => {
    if (rafRef.current) window.cancelAnimationFrame(rafRef.current);
    if (liveRafRef.current) window.cancelAnimationFrame(liveRafRef.current);
    if (persistTimerRef.current) window.clearTimeout(persistTimerRef.current);
    if (suppressTimerRef.current) window.clearTimeout(suppressTimerRef.current);
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

    const originFromTouches = (a, b) => {
      const rect = el.getBoundingClientRect();
      const anchor = pinchAnchorInViewport(rect, a, b);
      return { originX: anchor.originX, originY: anchor.originY };
    };

    const onWheel = (event) => {
      if (!shouldHandleProductionSheetWheel(event)) return;
      event.preventDefault();
      event.stopPropagation();
      const current = pendingLiveRef.current?.zoom ?? displayZoomRef.current;
      const next = productionSheetZoomFromWheel(current, event.deltaY, event.deltaMode);
      if (next !== current) {
        armClickSuppression();
      }
      applyLivePinchZoom(next, originFromClient(event.clientX, event.clientY));
    };

    const onTouchStart = (event) => {
      const touches = eventTouches(event);
      if (touches.length < 2) return;
      const dist = pinchDistance(touches[0], touches[1]);
      if (!(dist > 0)) return;
      const origin = originFromTouches(touches[0], touches[1]);
      const visual = displayZoomRef.current;
      targetZoomRef.current = visual;
      pendingZoomRef.current = visual;
      pendingLiveRef.current = null;
      if (rafRef.current) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
      }
      lastTimeRef.current = 0;
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
      applyLivePinchZoom(next.zoom, originFromTouches(touches[0], touches[1]));
    };

    const endPinch = () => {
      if (!gestureRef.current.pinching && !gestureRef.current.suppressClick) return;
      gestureRef.current = endSheetPinch(gestureRef.current);
      if (!gestureRef.current.suppressClick) return;
      armClickSuppression();
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
      clearSuppressTimer();
      gestureRef.current = createSheetPinchGesture(targetZoomRef.current);
    };
  }, [applyLivePinchZoom, armClickSuppression, clearSuppressTimer, setTargetZoom, viewportNode]);

  const consumeClickSuppression = useCallback(() => {
    const { consumed } = consumeSheetRowClickSuppression(gestureRef.current);
    if (!consumed) return false;
    disarmClickSuppression();
    return true;
  }, [disarmClickSuppression]);

  return { zoom, applyZoom: applyLivePinchZoom, setViewportRef, viewportRef, consumeClickSuppression, gestureRef };
}
