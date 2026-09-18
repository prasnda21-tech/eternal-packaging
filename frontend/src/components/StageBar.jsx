import { useEffect, useRef } from "react";
import { STAGES } from "../lib/constants";
import { resolveOperationalStage, resolveStageState } from "../lib/stageState";
import { getGroupedStagePresentation, getStagePresentation, stageBarClassForTone } from "../lib/poDetailPresentation";
import { collapsePoDetailUiStage } from "../lib/poDetailStageNav";

/**
 * Shared stage progress rail.
 * When showLabels is true (Detail PO), columns are equal-sized via CSS grid —
 * label length must not change stage width.
 * `viewedStage` is display-only and never changes workflow current_stage.
 */
export default function StageBar({
  stage,
  order = null,
  onStageClick,
  showLabels = false,
  viewedStage = null,
  omitStageIndexes = [],
  groupedPresentation = false,
}) {
  const rootRef = useRef(null);
  const operationalStage = resolveOperationalStage(order, stage);

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
      title={STAGES[operationalStage]}
    >
      {STAGES.map((s, i) => {
        if (omitStageIndexes.includes(i)) return null;
        const isFinal = stage >= STAGES.length - 1;
        let cls = "";
        let tone = "";
        let pres = null;
        if (order) {
          if (showLabels) {
            pres = groupedPresentation
              ? getGroupedStagePresentation(i, order, stage)
              : getStagePresentation(i, order, stage);
            tone = pres.tone;
            cls = stageBarClassForTone(pres.tone);
          } else {
            const { state } = resolveStageState(i, order, stage);
            if (state === "done" || state === "bypass") cls = "done";
            else if (state === "current") cls = "current";
          }
        } else {
          cls = i < stage || isFinal ? "done" : i === stage ? "current" : "";
        }
        const clickable = typeof onStageClick === "function";
        const viewedMapped = viewedStage != null && groupedPresentation
          ? collapsePoDetailUiStage(viewedStage)
          : viewedStage;
        const actualMapped = groupedPresentation
          ? collapsePoDetailUiStage(operationalStage)
          : operationalStage;
        const isViewed = viewedMapped != null && Number(viewedMapped) === i;
        const isActual = Number(actualMapped) === i;
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
            key={s}
            className={colClass}
            data-testid={`stage-bar-col-${i}`}
            data-stage-index={i}
            data-stage-viewed={isViewed ? "true" : undefined}
            data-stage-actual={isActual ? "true" : undefined}
          >
            <span
              className={segClass}
              title={`${i + 1}. ${s}${stateLabel ? ` · ${stateLabel}` : ""}${isViewed ? " · sedang dilihat" : ""}${clickable ? " · klik untuk membuka workspace" : ""}`}
              aria-label={`${i + 1}. ${s}${stateLabel ? `. ${stateLabel}` : ""}${isViewed ? ". Sedang dilihat" : ""}${isActual ? ". Tahap aktif" : ""}`}
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
                  <span className="stage-bar-step-num">{i + 1}</span>
                  <span className="stage-bar-label">{s}</span>
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
