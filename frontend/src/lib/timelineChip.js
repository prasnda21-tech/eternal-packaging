import { resolveStageState } from "./stageState.js";
import { resolveDesignChip, resolveApprovalDesignChip } from "./designEvidence.js";

/**
 * Exact chip resolver used by Timeline row render.
 * Design / Approval Design never use generic idx < currentStage progression.
 */
export function resolveTimelineChip(idx, order, currentStage) {
  if (idx === 1) return resolveDesignChip(order, currentStage);
  if (idx === 2) return resolveApprovalDesignChip(order, currentStage);
  return resolveStageState(idx, order, currentStage);
}
