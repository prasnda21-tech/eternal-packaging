import { formatDisplayDate } from "./constants";
import { formatIdQty } from "./divisionTasks";
import { toDateKey } from "./productionScheduleFilters";
import { productionSchedulePriorityRank } from "./productionSchedulePriority";

export const TASK_OPERATOR_EMPTY = "Belum ditentukan";
export const TASK_WORK_DATE_EMPTY = "Belum Dijadwalkan";
export const TASK_DEADLINE_EMPTY = "Menunggu approval";
export const TASK_PRODUCT_EMPTY = "—";
export const TASK_QTY_EMPTY = "—";

const NON_PCS_INPUT_TYPES = new Set([
  "upload_foto",
  "confirm_terkirim",
  "upload_sj",
  "upload_design",
]);
const NON_PCS_DIVISIONS = new Set(["terkirim", "surat_jalan", "designer"]);

export function formatTaskCardDate(value) {
  if (value === null || value === undefined || String(value).trim() === "") return "";
  const out = formatDisplayDate(value);
  if (!out || out === "-") return "";
  if (/invalid date/i.test(out)) return "";
  return out;
}

export function taskOperatorNames(task) {
  const fromList = Array.isArray(task?.operator_names)
    ? task.operator_names.map((n) => String(n || "").trim()).filter(Boolean)
    : [];
  if (fromList.length) return fromList;
  const assigned = String(task?.assigned_user_name || "").trim();
  return assigned ? [assigned] : [];
}

export function taskOperatorLabel(task) {
  const names = taskOperatorNames(task);
  return names.length ? names.join("\n") : TASK_OPERATOR_EMPTY;
}

export function taskWorkDateLabel(task) {
  return formatTaskCardDate(task?.work_date) || TASK_WORK_DATE_EMPTY;
}

export function taskDeadlineLabel(task) {
  return formatTaskCardDate(task?.delivery_deadline) || TASK_DEADLINE_EMPTY;
}

export function taskProductLabel(task) {
  const item = String(task?.item || "").trim();
  return item || TASK_PRODUCT_EMPTY;
}

export function taskHasPcsQty(task) {
  const type = String(task?.required_input_type || "").trim();
  if (NON_PCS_INPUT_TYPES.has(type)) return false;
  const division = String(task?.division || "").trim();
  if (NON_PCS_DIVISIONS.has(division)) return false;
  if (task?.target_qty === null || task?.target_qty === undefined || String(task?.target_qty).trim() === "") {
    return false;
  }
  return Number.isFinite(Number(task.target_qty));
}

export function taskScopeQtyLabel(task) {
  if (!taskHasPcsQty(task)) return TASK_QTY_EMPTY;
  const formatted = formatIdQty(task.target_qty);
  if (!formatted || formatted === "—") return TASK_QTY_EMPTY;
  return `${formatted} pcs`;
}

function identityKey(task) {
  return String(task?.task_id || `${task?.order_id || ""}:${task?.line_idx ?? "po"}:${task?.order_number || ""}`);
}

export function sortOperationalTasks(tasks) {
  const list = Array.isArray(tasks) ? tasks : [];
  return [...list].sort((a, b) => {
    const ar = productionSchedulePriorityRank(a);
    const br = productionSchedulePriorityRank(b);
    if (ar !== br) return ar - br;
    const aw = toDateKey(a?.work_date);
    const bw = toDateKey(b?.work_date);
    if (Boolean(aw) !== Boolean(bw)) return aw ? -1 : 1;
    if (aw && bw && aw !== bw) return aw.localeCompare(bw);
    const ad = toDateKey(a?.delivery_deadline) || "9999-12-31";
    const bd = toDateKey(b?.delivery_deadline) || "9999-12-31";
    if (ad !== bd) return ad.localeCompare(bd);
    return identityKey(a).localeCompare(identityKey(b));
  });
}
