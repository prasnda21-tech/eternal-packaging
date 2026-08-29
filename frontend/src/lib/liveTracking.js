import { STAGES } from "./constants";
import { resolveTimelineChip } from "./timelineChip";

/** Same 15 stages + chip labels as Alur Pekerjaan. */
export function liveTrackingRows(order) {
  const current = Number(order?.current_stage || 0);
  return STAGES.map((stage, idx) => {
    const chip = resolveTimelineChip(idx, order, current);
    return { idx, stage, state: chip.state, label: chip.label };
  });
}
