import { useEffect, useRef } from "react";
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

/**
 * Shared stage progress rail.
 * When showLabels is true (Detail PO), columns are equal-sized via CSS grid —
 * label length must not change stage width.
 * `viewedStage` is display-only and never changes workflow current_stage.
 * `steps` is optional presentational grouping; omit to keep the 15 internal nodes.
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
  const operationalStage = resolveOperationalStage(order, stage);
  const nodes = Array.isArray(steps) && steps.length ? steps : defaultSteps();

  useEffect(() => {
    if (!showLabels || !rootRef.current) return;
    const viewed = rootRef.current.querySelector(".stage-bar-col.is-viewed, .stage-bar-seg.is-viewed");
    const current = rootRef.current.querySelector(".stage-bar-col.is-current, .stage-bar-seg.current");
    const target = viewed?.closest?.(".stage-bar-col") || viewed || current?.closest?.(".stage-bar-col") || current;
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ inline: "nearest", block: "nearest", behavior: "smooth" });
    }
  }, [operationalStage, viewedStage, showLabels, order?.id]);

  return (
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
        const stateLabel = holdish
          ? "Tertahan"
          : cancelled
            ? "Dibatalkan"
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
          >
            <span
              className={segClass}
              title={`${step.displayNumber}. ${step.label}${stateLabel ? ` · ${stateLabel}` : ""}${isViewed ? " · sedang dilihat" : ""}${clickable ? " · klik untuk membuka workspace" : ""}`}
              aria-label={`${step.displayNumber}. ${step.label}${stateLabel ? `. ${stateLabel}` : ""}${isViewed ? ". Sedang dilihat" : ""}${isActual ? ". Tahap aktif" : ""}`}
              aria-current={isViewed ? "step" : undefined}
              role={clickable ? "button" : undefined}
              tabIndex={clickable ? 0 : undefined}
              onClick={clickable ? () => onStageClick(i) : undefined}
              onKeyDown={clickable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onStageClick(i); } } : undefined}
              data-testid={`stage-node-${i}`}
              data-stage-tone={tone || undefined}
              data-stage-state={stateLabel || undefined}
            >
              {showLabels ? (
                <>
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
}
