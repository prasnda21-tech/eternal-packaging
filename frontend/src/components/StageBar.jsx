import { useEffect, useRef, useState } from "react";
import { STAGES } from "../lib/constants";
import { resolveOperationalStage, resolveStageState } from "../lib/stageState";
import { getStagePresentation, stageBarClassForTone } from "../lib/poDetailPresentation";
import { visualStepPresentationIndex } from "../lib/poDetailStageNav";

function defaultSteps() {
  return STAGES.map((label, i) => ({
    id: label,
    label,
    displayNumber: String(i + 1),
    canonicalIndex: i,
    internalIndexes: [i],
  }));
}

function revealInRail(scroller, el) {
  if (!scroller || !el || typeof scroller.getBoundingClientRect !== "function") return;
  const rail = scroller.getBoundingClientRect();
  const item = el.getBoundingClientRect();
  if (!rail.width || !item.width) return;
  const pad = 8;
  let delta = 0;
  if (item.left < rail.left + pad) delta = item.left - rail.left - pad;
  else if (item.right > rail.right - pad) delta = item.right - rail.right + pad;
  if (!delta) return;
  if (typeof scroller.scrollBy === "function") scroller.scrollBy({ left: delta, behavior: "auto" });
  else scroller.scrollLeft += delta;
}

/**
 * Shared stage progress rail.
 * Detail PO (`showLabels`) renders one nowrap horizontal rail. Overflow stays
 * inside the rail. `viewedStage` is display-only and never writes current_stage.
 * Omit `steps` to keep the 15 internal nodes (PO card).
 */
export default function StageBar({
  stage,
  order = null,
  onStageClick,
  showLabels = false,
  viewedStage = null,
  steps = null,
}) {
  const rootRef = useRef(null);
  const scrollerRef = useRef(null);
  const operationalStage = resolveOperationalStage(order, stage);
  const nodes = Array.isArray(steps) && steps.length ? steps : defaultSteps();
  const [edges, setEdges] = useState({ prev: false, next: false, overflow: false });

  useEffect(() => {
    if (!showLabels) return undefined;
    const el = scrollerRef.current;
    if (!el) return undefined;
    const measure = () => {
      const max = el.scrollWidth - el.clientWidth;
      const next = {
        prev: el.scrollLeft > 2,
        next: max - el.scrollLeft > 2,
        overflow: max > 2,
      };
      setEdges((prev) => (
        prev.prev === next.prev && prev.next === next.next && prev.overflow === next.overflow
          ? prev
          : next
      ));
    };
    measure();
    el.addEventListener("scroll", measure, { passive: true });
    let ro;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(measure);
      ro.observe(el);
    }
    window.addEventListener("resize", measure);
    return () => {
      el.removeEventListener("scroll", measure);
      if (ro) ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [showLabels, nodes.length]);

  useEffect(() => {
    if (!showLabels || !scrollerRef.current) return;
    const viewed = scrollerRef.current.querySelector(".stage-bar-col.is-viewed");
    if (viewed) revealInRail(scrollerRef.current, viewed);
  }, [viewedStage, showLabels, order?.id]);

  const scrollRail = (dir) => {
    const el = scrollerRef.current;
    if (!el) return;
    const amount = Math.max(160, Math.round(el.clientWidth * 0.7)) * dir;
    if (typeof el.scrollBy === "function") el.scrollBy({ left: amount, behavior: "smooth" });
    else el.scrollLeft += amount;
  };

  const moveFocus = (current, key) => {
    const root = scrollerRef.current || rootRef.current;
    if (!root) return;
    const items = [...root.querySelectorAll("[data-testid^='stage-node-']")];
    const idx = items.indexOf(current);
    if (idx < 0) return;
    let next = idx;
    if (key === "ArrowLeft") next = Math.max(0, idx - 1);
    if (key === "ArrowRight") next = Math.min(items.length - 1, idx + 1);
    if (key === "Home") next = 0;
    if (key === "End") next = items.length - 1;
    if (next !== idx) items[next]?.focus();
  };

  const bar = (
    <div
      ref={rootRef}
      className={"stage-bar" + (showLabels ? " has-labels equal-stages" : "")}
      data-testid="order-stage-progress"
      data-visual-step-count={nodes.length}
      title={STAGES[operationalStage]}
    >
      {nodes.map((step) => {
        const i = step.canonicalIndex;
        const presIdx = visualStepPresentationIndex(step, stage);
        const isFinal = stage >= STAGES.length - 1;
        let cls = "";
        let tone = "";
        let pres = null;
        if (order) {
          if (showLabels) {
            pres = getStagePresentation(presIdx, order, stage);
            tone = pres.tone;
            cls = stageBarClassForTone(pres.tone);
          } else {
            const { state } = resolveStageState(presIdx, order, stage);
            if (state === "done" || state === "bypass") cls = "done";
            else if (state === "current") cls = "current";
          }
        } else {
          cls = i < stage || isFinal ? "done" : i === stage ? "current" : "";
        }
        const clickable = typeof onStageClick === "function";
        const isViewed = viewedStage != null && step.internalIndexes.includes(Number(viewedStage));
        const isActual = step.internalIndexes.includes(Number(operationalStage));
        const holdish = !!(order?.hold && isActual);
        const cancelled = !!(order?.cancelled && isActual);
        const groupLast = step.internalIndexes[step.internalIndexes.length - 1];
        const done = cls === "done" && presIdx === groupLast;
        const stateLabel = holdish
          ? "Tertahan"
          : cancelled
            ? "Dibatalkan"
            : done
              ? "Selesai"
              : (pres ? (pres.label || pres.summary) : "");
        const colClass = [
          "stage-bar-col",
          cls ? `is-${cls}` : "",
          tone ? `tone-${tone}` : "",
          isViewed ? "is-viewed" : "",
          isActual ? "is-actual" : "",
          holdish ? "is-hold" : "",
          cancelled ? "is-cancelled" : "",
        ].filter(Boolean).join(" ");
        const segClass = [
          "stage-bar-seg",
          cls,
          clickable ? "clickable" : "",
          isViewed ? "is-viewed" : "",
          isActual ? "is-actual" : "",
          holdish ? "is-hold" : "",
          cancelled ? "is-cancelled" : "",
        ].filter(Boolean).join(" ");
        return (
          <div
            key={step.id}
            className={colClass}
            data-testid={`stage-bar-col-${i}`}
            data-stage-index={i}
            data-visual-id={step.id}
            data-visual-number={step.displayNumber}
            data-stage-viewed={isViewed ? "true" : undefined}
            data-stage-actual={isActual ? "true" : undefined}
            data-stage-done={done ? "true" : undefined}
          >
            <span
              className={segClass}
              title={`${step.displayNumber}. ${step.label}${stateLabel ? ` · ${stateLabel}` : ""}${isViewed ? " · sedang dilihat" : ""}${isActual ? " · tahap aktif" : ""}${clickable ? " · klik untuk membuka workspace" : ""}`}
              aria-label={`${step.displayNumber}. ${step.label}${stateLabel ? `. ${stateLabel}` : ""}${isViewed ? ". Sedang dilihat" : ""}${isActual ? ". Tahap aktif" : ""}`}
              aria-current={isViewed ? "step" : undefined}
              role={clickable ? "button" : undefined}
              tabIndex={clickable ? 0 : undefined}
              onClick={clickable ? () => onStageClick(i) : undefined}
              onFocus={showLabels ? (e) => revealInRail(scrollerRef.current, e.currentTarget.closest(".stage-bar-col") || e.currentTarget) : undefined}
              onKeyDown={clickable ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onStageClick(i);
                  return;
                }
                if (showLabels && (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "Home" || e.key === "End")) {
                  e.preventDefault();
                  moveFocus(e.currentTarget, e.key);
                }
              } : undefined}
              data-testid={`stage-node-${i}`}
              data-stage-tone={tone || undefined}
              data-stage-state={stateLabel || undefined}
            >
              {showLabels ? (
                <>
                  {isActual ? <span className="stage-bar-active-mark" data-testid={`stage-active-mark-${i}`} aria-hidden="true" /> : null}
                  {done ? <span className="stage-bar-done-mark" data-testid={`stage-done-mark-${i}`} aria-hidden="true">✓</span> : null}
                  <span className="stage-bar-step-num">{step.displayNumber}</span>
                  <span className="stage-bar-label">{step.label}</span>
                  {stateLabel ? <span className="po-sr-only">{stateLabel}</span> : null}
                </>
              ) : null}
            </span>
          </div>
        );
      })}
    </div>
  );

  if (!showLabels) return bar;

  return (
    <div className="po-stage-rail" data-testid="po-stage-rail" data-rail-overflow={edges.overflow ? "true" : "false"}>
      <button
        type="button"
        className="ghost sm po-stage-rail-nav"
        data-testid="po-stage-rail-prev"
        aria-label="Geser tahap ke kiri"
        disabled={!edges.prev}
        onClick={() => scrollRail(-1)}
      >
        ‹
      </button>
      <div className="po-stage-rail-viewport">
        <div
          ref={scrollerRef}
          className="po-stage-rail-scroller"
          data-testid="po-stage-rail-scroller"
          tabIndex={-1}
        >
          {bar}
        </div>
        <span className={"po-stage-rail-fade is-start" + (edges.prev ? " is-on" : "")} aria-hidden="true" />
        <span className={"po-stage-rail-fade is-end" + (edges.next ? " is-on" : "")} aria-hidden="true" />
      </div>
      <button
        type="button"
        className="ghost sm po-stage-rail-nav"
        data-testid="po-stage-rail-next"
        aria-label="Geser tahap ke kanan"
        disabled={!edges.next}
        onClick={() => scrollRail(1)}
      >
        ›
      </button>
      <p className="po-sr-only" data-testid="po-stage-rail-overflow" aria-live="polite">
        {edges.overflow ? "Ada tahap di luar tampilan. Geser rail untuk melihatnya." : "Semua tahap terlihat."}
      </p>
    </div>
  );
}
