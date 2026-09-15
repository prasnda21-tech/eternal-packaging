/** Status per Role marks — same workflow chips as the task form. */

import { resolveStageState } from "./stageState";

export const ROLE_STAGE_INDEX = {
  cup: 7,
  screen: 6,
  prod: 10,
  pack: 11,
  load: 13,
  kirim: 14,
};

export function chipStateToRoleTone(state) {
  if (state === "done" || state === "bypass") return "done";
  if (state === "current") return "pending";
  return "idle";
}

function payloadRoleTone(row, key) {
  const tone = row?.role_status?.[key];
  if (tone === "done" || tone === "pending" || tone === "idle") return tone;
  return null;
}

function orderViewForRoleMarks(row) {
  if (!row || typeof row !== "object") return row;
  if (row.helper_prep || row.screener_info || row.operator_splits) return row;
  const view = { ...row };
  if (row.helper_prepared != null && view.helper_prep == null) {
    view.helper_prep = { qty_prepared: row.helper_prepared };
  }
  if (row.screen_qty != null && view.screener_info == null) {
    view.screener_info = { screen_qty: row.screen_qty };
  }
  if (Array.isArray(row.operators) && !row.operator_splits) {
    view.operator_splits = row.operators;
  }
  return view;
}

/** Prefer backend role_status (resolve_stage_chip); else the same frontend resolver. */
export function roleMarkTone(row, key) {
  const fromPayload = payloadRoleTone(row, key);
  if (fromPayload) return fromPayload;
  const idx = ROLE_STAGE_INDEX[key];
  if (idx == null || !row) return "idle";
  const view = orderViewForRoleMarks(row);
  const { state } = resolveStageState(idx, view, Number(view.current_stage || 0));
  return chipStateToRoleTone(state);
}

export function cupMarkTone(row) {
  return roleMarkTone(row, "cup");
}

export function screenMarkTone(row) {
  return roleMarkTone(row, "screen");
}

export function prodMarkTone(row) {
  return roleMarkTone(row, "prod");
}

export function packMarkTone(row) {
  return roleMarkTone(row, "pack");
}

export function loadMarkTone(row) {
  return roleMarkTone(row, "load");
}

export function kirimMarkTone(row) {
  return roleMarkTone(row, "kirim");
}

export function deliveryRoleMarkTones(row) {
  return {
    cup: cupMarkTone(row),
    screen: screenMarkTone(row),
    prod: prodMarkTone(row),
    pack: packMarkTone(row),
    load: loadMarkTone(row),
    kirim: kirimMarkTone(row),
  };
}
