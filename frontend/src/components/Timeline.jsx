import { STAGES, formatDate } from "../lib/constants";
import { ROLES } from "../lib/constants";
import { resolveTimelineChip } from "../lib/timelineChip.js";

export { resolveTimelineChip };

export default function Timeline({ timeline = [], currentStage = 0, order = null }) {
  const byStage = STAGES.map((label, idx) => {
    const events = timeline.filter((t) => t.stage === idx);
    const last = events[events.length - 1] || null;
    return { idx, label, last };
  });

  return (
    <ol className="timeline" data-testid="order-timeline">
      {byStage.map(({ idx, label, last }) => {
        const { state, label: stateLabel } = resolveTimelineChip(idx, order, currentStage);
        return (
          <li key={idx} className={`timeline-item timeline-${state}`} data-testid={`timeline-stage-${idx}`}>
            <span className="dot" />
            <div className="body">
              <div className="row-top">
                <span className="idx">{String(idx + 1).padStart(2, "0")}</span>
                <span className="lbl">{label}</span>
                <span className={`chip chip-${state}`} data-testid={`timeline-stage-${idx}-chip`}>
                  {stateLabel}
                </span>
              </div>
              {last && (
                <div className="meta">
                  <span>{formatDate(last.at)}</span>
                  <span>·</span>
                  <span>{last.by_name || last.by_email}</span>
                  <span className="role-tag">{ROLES[last.by_role] || last.by_role}</span>
                  {last.note && <p className="note">{last.note}</p>}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
