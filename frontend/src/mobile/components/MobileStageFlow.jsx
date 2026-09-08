import { STAGES } from "../../lib/constants";
import { resolveStageState } from "../../lib/stageState";

export default function MobileStageFlow({ order }) {
  const stage = Number(order?.current_stage || 0);
  return (
    <div className="mop-stageflow" data-testid="mobile-stageflow" role="list">
      {STAGES.map((label, i) => {
        const { state } = resolveStageState(i, order, stage);
        const cls = state === "done" || state === "bypass" ? "is-done" : state === "current" ? "is-current" : "";
        return (
          <div key={label} className={`mop-stage ${cls}`} role="listitem" data-testid={`mobile-stage-${i}`}>
            <b>{i + 1}</b>
            {label}
          </div>
        );
      })}
    </div>
  );
}
