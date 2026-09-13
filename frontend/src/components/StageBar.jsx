import { useEffect, useRef } from "react";
import { STAGES } from "../lib/constants";
import { resolveStageState } from "../lib/stageState";
import { getStagePresentation, stageBarClassForTone } from "../lib/poDetailPresentation";

/**
 * Shared stage progress rail.
 * When showLabels is true (Detail PO), columns are equal-sized via CSS grid —
 * label length must not change stage width.
 */
export default function StageBar({ stage, order = null, onStageClick, showLabels = false }) {
  const rootRef = useRef(null);

  useEffect(() => {
    if (!showLabels || !rootRef.current) return;
    const current = rootRef.current.querySelector(".stage-bar-col.is-current, .stage-bar-seg.current");
    const col = current?.closest?.(".stage-bar-col") || current;
    if (col && typeof col.scrollIntoView === "function") {
      col.scrollIntoView({ inline: "nearest", block: "nearest", behavior: "smooth" });
    }
  }, [stage, showLabels, order?.id]);

  return (
    <div
      ref={rootRef}
      className={"stage-bar" + (showLabels ? " has-labels equal-stages" : "")}
      data-testid="order-stage-progress"
      title={STAGES[stage]}
    >
      {STAGES.map((s, i) => {
        const isFinal = stage >= STAGES.length - 1;
        let cls = "";
        let tone = "";
        if (order) {
          if (showLabels) {
            const pres = getStagePresentation(i, order, stage);
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
        return (
          <div
            key={s}
            className={"stage-bar-col" + (cls ? ` is-${cls}` : "") + (tone ? ` tone-${tone}` : "")}
            data-testid={`stage-bar-col-${i}`}
            data-stage-index={i}
          >
            <span
              className={"stage-bar-seg " + cls + (clickable ? " clickable" : "")}
              title={`${i + 1}. ${s}${clickable ? " · klik untuk ke bagian" : ""}`}
              role={clickable ? "button" : undefined}
              tabIndex={clickable ? 0 : undefined}
              onClick={clickable ? () => onStageClick(i) : undefined}
              onKeyDown={clickable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onStageClick(i); } } : undefined}
              data-testid={`stage-node-${i}`}
              data-stage-tone={tone || undefined}
            >
              {showLabels ? (
                <>
                  <span className="stage-bar-step-num">{i + 1}</span>
                  <span className="stage-bar-label">{s}</span>
                </>
              ) : null}
            </span>
          </div>
        );
      })}
    </div>
  );
}
