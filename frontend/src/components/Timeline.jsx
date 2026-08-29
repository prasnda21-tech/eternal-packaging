import { STAGES, formatDisplayDateTime } from "../lib/constants";
import { ROLES } from "../lib/constants";
import { resolveTimelineChip } from "../lib/timelineChip.js";
import { approvalDesignAssignEvent } from "../lib/approvalDesignHistory";

export { resolveTimelineChip };

function stageActor(idx, last, order) {
  if (idx === 2) {
    return approvalDesignAssignEvent(order).name || "";
  }
  if (last?.by_name || last?.by_email) return last.by_name || last.by_email;
  if (idx === 6) return order?.screener_info?.updated_by_name || order?.screener_info?.updated_by || "";
  if (idx === 7) return order?.helper_prep?.updated_by_name || order?.helper_prep?.updated_by || "";
  if (idx === 8 || idx === 10) {
    const split = (order?.operator_splits || []).find((s) => s.operator_name || s.operator_email);
    return split?.operator_name || split?.operator_email || "";
  }
  if (idx === 11) return order?.packing_info?.updated_by_name || order?.packing_info?.updated_by || "";
  if (idx === 12) return order?.delivery_note_created_by_name || order?.delivery_note_created_by || "";
  if (idx === 13) return order?.loading_checklist?.updated_by_name || order?.loading_info?.updated_by_name || "";
  if (idx === 14) return order?.delivered_by_name || order?.delivered_by || "";
  return "";
}

function stageAssignAt(idx, last, order) {
  if (idx === 2) {
    return approvalDesignAssignEvent(order).at || "";
  }
  if (last?.at) return last.at;
  if (idx === 6) return order?.screener_info?.updated_at || "";
  if (idx === 7) return order?.helper_prep?.updated_at || "";
  if (idx === 8 || idx === 10) return (order?.operator_splits || [])[0]?.updated_at || "";
  if (idx === 11) return order?.packing_info?.updated_at || "";
  if (idx === 12) return order?.delivery_note_created_at || "";
  if (idx === 13) return order?.loading_checklist?.updated_at || order?.loading_info?.updated_at || "";
  if (idx === 14) return order?.delivered_at || "";
  return "";
}

export default function Timeline({ timeline = [], currentStage = 0, order = null }) {
  const source = { ...(order || {}), timeline };
  const byStage = STAGES.map((label, idx) => {
    const events = timeline.filter((t) => t.stage === idx);
    const last = events[events.length - 1] || null;
    return { idx, label, last };
  });

  return (
    <section className="panel" data-testid="order-timeline-panel">
      <header className="panel-head"><h3>Alur Pekerjaan</h3></header>
      <div className="workflow-table-wrap">
        <table className="workflow-table" data-testid="order-timeline">
          <thead>
            <tr>
              <th>Tahap</th>
              <th>Status</th>
              <th>Jam Assign</th>
              <th>Nama</th>
            </tr>
          </thead>
          <tbody>
            {byStage.map(({ idx, label, last }) => {
              const { state, label: stateLabel } = resolveTimelineChip(idx, source, currentStage);
              const at = stageAssignAt(idx, last, source);
              const name = stageActor(idx, last, source);
              const assignMethod = idx === 2 ? approvalDesignAssignEvent(source).method : null;
              return (
                <tr key={idx} className={`timeline-${state}`} data-testid={`timeline-stage-${idx}`}>
                  <td>{label}</td>
                  <td>
                    <span className={`chip chip-${state}`} data-testid={`timeline-stage-${idx}-chip`}>
                      {stateLabel}
                    </span>
                  </td>
                  <td className="mono small" data-testid={`timeline-stage-${idx}-assign`}>
                    {at ? formatDisplayDateTime(at) : "-"}
                    {assignMethod ? <span className="muted"> · {assignMethod}</span> : null}
                  </td>
                  <td data-testid={`timeline-stage-${idx}-actor`}>{name || "-"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
