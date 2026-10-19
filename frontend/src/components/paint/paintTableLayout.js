import { moveReportColumn, normalizeReportColumns } from "../reportWorkspace/ReportColumnSettings";

export const PAINT_TABLE_SCHEMA = 1;

export function paintLayoutKey(userId, tableId) {
  const user = String(userId || "anon").slice(0, 80);
  const table = String(tableId || "table").slice(0, 80);
  return `paint-table-layout-v${PAINT_TABLE_SCHEMA}:${user}:${table}`;
}

export function readPaintLayout(userId, tableId) {
  try {
    return JSON.parse(window.localStorage.getItem(paintLayoutKey(userId, tableId)) || "null");
  } catch {
    return null;
  }
}

export function writePaintLayout(userId, tableId, layout) {
  try {
    window.localStorage.setItem(paintLayoutKey(userId, tableId), JSON.stringify(layout));
    return true;
  } catch {
    return false;
  }
}

export function mergePaintLayout(definitions, saved) {
  return normalizeReportColumns(definitions, saved || {});
}

export { moveReportColumn, normalizeReportColumns };

export function encodeHeaderFilters(filters = {}) {
  /** filters: { key: { op, value } } → ["key:op:value"] */
  return Object.entries(filters)
    .filter(([, v]) => v && v.value != null && String(v.value).trim() !== "")
    .map(([key, v]) => `${key}:${v.op || "contains"}:${v.value}`);
}

export function clientSortRows(rows, sortBy, sortDir, tieBreak = "id") {
  if (!sortBy) return rows;
  const dir = sortDir === "asc" ? 1 : -1;
  const list = [...rows];
  list.sort((a, b) => {
    const av = a?.[sortBy];
    const bv = b?.[sortBy];
    const an = Number(av);
    const bn = Number(bv);
    let cmp = 0;
    if (Number.isFinite(an) && Number.isFinite(bn) && String(av).trim() !== "" && String(bv).trim() !== "") {
      cmp = an === bn ? 0 : an < bn ? -1 : 1;
    } else {
      cmp = String(av ?? "").localeCompare(String(bv ?? ""), "id", { sensitivity: "base", numeric: true });
    }
    if (cmp === 0) {
      cmp = String(a?.[tieBreak] ?? "").localeCompare(String(b?.[tieBreak] ?? ""));
    }
    return cmp * dir;
  });
  return list;
}

export function clientFilterRows(rows, filters = {}) {
  return rows.filter((row) =>
    Object.entries(filters).every(([key, conf]) => {
      if (!conf || conf.value == null || String(conf.value).trim() === "") return true;
      const cell = row?.[key];
      const op = conf.op || "contains";
      const raw = String(conf.value);
      if (op === "contains" || op === "text") {
        return String(cell ?? "").toLocaleLowerCase("id-ID").includes(raw.toLocaleLowerCase("id-ID"));
      }
      if (op === "eq" || op === "enum") return String(cell ?? "") === raw;
      const left = Number(cell);
      const right = Number(raw);
      if (!Number.isFinite(left) || !Number.isFinite(right)) return false;
      if (op === "gte") return left >= right;
      if (op === "lte") return left <= right;
      if (op === "gt") return left > right;
      if (op === "lt") return left < right;
      if (op === "eq_num") return left === right;
      if (op === "date_gte") return String(cell ?? "") >= raw;
      if (op === "date_lte") return String(cell ?? "").slice(0, 10) <= raw.slice(0, 10);
      return true;
    })
  );
}
