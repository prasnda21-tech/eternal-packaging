import { STAGES } from "../lib/constants";
import { resolveStageState } from "../lib/stageState";

export default function StageBar({ stage, order = null, onStageClick, showLabels = false }) {
  return (
    <div className={"stage-bar" + (showLabels ? " has-labels" : "")} data-testid="order-stage-progress" title={STAGES[stage]}>
      {STAGES.map((s, i) => {
        const isFinal = stage >= STAGES.length - 1;
        let cls = "";
        if (order) {
          const { state } = resolveStageState(i, order, stage);
          if (state === "done" || state === "bypass") cls = "done";
          else if (state === "current") cls = "current";
        } else {
          cls = i < stage || isFinal ? "done" : i === stage ? "current" : "";
        }
        const clickable = typeof onStageClick === "function";
        return (
          <div
            key={s}
            className={"stage-bar-col" + (cls ? ` is-${cls}` : "")}
          >
            <span
              className={"stage-bar-seg " + cls + (clickable ? " clickable" : "")}
              title={`${i + 1}. ${s}${clickable ? " · klik untuk ke bagian" : ""}`}
              role={clickable ? "button" : undefined}
              tabIndex={clickable ? 0 : undefined}
              onClick={clickable ? () => onStageClick(i) : undefined}
              onKeyDown={clickable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onStageClick(i); } } : undefined}
              data-testid={`stage-node-${i}`}
            >
              {showLabels ? (
                <>
                  <span className="stage-bar-step-num">{i + 1}</span>
                  <span className="stage-bar-label" title={`${i + 1}. ${s}`}>{s}</span>
                </>
              ) : null}
            </span>
          </div>
        );
      })}
    </div>
  );
}
