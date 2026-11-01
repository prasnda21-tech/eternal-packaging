/** Canonical Jadwal Produksi table columns — shared by desktop /produksi and mobile /m/schedule. */

import {
  FILTER_DEFAULTS,
  productionTodayKey,
  rowMatchesDefaultProductionScheduleScope,
} from "./productionScheduleFilters";

/** Safety caps for internal staged fetch (no UI pager). Not a completeness claim. */
export const PRODUCTION_SCHEDULE_FETCH_MAX_PAGES = 100;
export const PRODUCTION_SCHEDULE_FETCH_MAX_MS = 45_000;
/** Light poll: head discovery + bounded id reconcile batches (no full staged replay). */
export const PRODUCTION_SCHEDULE_RECONCILE_IDS_PER_REQUEST = 150;
export const PRODUCTION_SCHEDULE_RECONCILE_MAX_REQUESTS = 4;

/** Frozen inventory of every legitimate schedule column. Reorder allowed; deletion is not. */
export const PRODUCTION_SCHEDULE_REQUIRED_COLUMN_IDS = [
  "customer",
  "deadline",
  "deadline_ship",
  "work_date",
  "prio",
  "buffer",
  "po",
  "outlet",
  "qty",
  "qty_cetak",
  "qty_operator",
  "progress",
  "item",
  "operator",
  "design",
  "payment",
  "hold",
  "screen",
  "helper",
  "packing",
  "sj",
  "driver",
  "status",
  "screen_size",
];

/**
 * Spreadsheet column order shared by /produksi and /m/schedule.
 * Canonical inventory keeps operator qty before scheduled qty.
 * Desktop Produksi shows Item, scheduled qty, operator qty, progress, then Total Qty.
 */
export const PRODUCTION_SCHEDULE_TABLE_COLS = [
  { key: "po", id: "po", label: "No PO", group: "identity", align: "left", type: "id", baseWidth: 112, minWidth: 76, filter: "po" },
  { key: "customer", id: "customer", label: "Customer", group: "identity", align: "left", type: "text", baseWidth: 168, minWidth: 96, filter: "customer", wrap: true },
  { key: "work_date", id: "work_date", label: "Tgl Pengerjaan", group: "planning", align: "left", type: "date", baseWidth: 118, minWidth: 88, testid: "prod-th-work-date", filter: "work_date", sort: "work_date", wrap: true },
  { key: "operator", id: "operator", label: "Operator", group: "assignment", align: "left", type: "text", baseWidth: 168, minWidth: 96, filter: "operator", wrap: true },
  { key: "item", id: "item", label: "Item", group: "identity", align: "left", type: "text", baseWidth: 168, minWidth: 96, wrap: true },
  { key: "qty_operator", id: "qty_operator", label: "Qty Pengerjaan Operator", group: "quantity", align: "right", type: "qty", baseWidth: 168, minWidth: 120, num: true },
  { key: "qty_cetak", id: "qty_cetak", label: "Qty Cetak (Scheduled)", group: "quantity", align: "right", type: "qty", baseWidth: 156, minWidth: 120, num: true },
  { key: "progress", id: "progress", label: "Progress", group: "quantity", align: "right", type: "qty", baseWidth: 96, minWidth: 72, num: true },
  { key: "qty", id: "qty", label: "Total Qty", group: "quantity", align: "right", type: "qty", baseWidth: 104, minWidth: 72, num: true },
  { key: "prio", id: "prio", label: "Prio", group: "planning", align: "left", type: "status", baseWidth: 78, minWidth: 56, filter: "prio" },
  { key: "buffer", id: "buffer", label: "Buffer", group: "planning", align: "left", type: "status", baseWidth: 72, minWidth: 52, testid: "prod-th-buffer", filter: "buffer" },
  { key: "deadline", id: "deadline", label: "Tanggal Pengiriman", group: "planning", align: "left", type: "date", baseWidth: 118, minWidth: 88, filter: "deadline", sort: "deadline" },
  { key: "deadline_ship", id: "deadline_ship", label: "Sumber Jadwal", group: "planning", align: "left", type: "date", baseWidth: 168, minWidth: 110, testid: "prod-th-deadline-effective", wrap: true },
  { key: "outlet", id: "outlet", label: "Outlet", group: "identity", align: "left", type: "text", baseWidth: 140, minWidth: 88, wrap: true },
  { key: "screen_size", id: "screen_size", label: "Screen", group: "assignment", align: "left", type: "status", baseWidth: 108, minWidth: 80, wrap: true },
  { key: "screen", id: "screen", label: "Persiapan Screen", group: "quantity", align: "right", type: "qty", baseWidth: 108, minWidth: 80, num: true, filter: "screener" },
  { key: "design", id: "design", label: "Design", group: "assignment", align: "left", type: "status", baseWidth: 96, minWidth: 72 },
  { key: "payment", id: "payment", label: "Payment", group: "assignment", align: "left", type: "status", baseWidth: 96, minWidth: 72 },
  { key: "hold", id: "hold", label: "HOLD", group: "assignment", align: "left", type: "status", baseWidth: 68, minWidth: 52, filter: "hold" },
  { key: "helper", id: "helper", label: "Persiapan Cup", group: "assignment", align: "right", type: "qty", baseWidth: 108, minWidth: 80, num: true, filter: "helper", wrap: true },
  { key: "packing", id: "packing", label: "Packing", group: "fulfillment", align: "left", type: "status", baseWidth: 84, minWidth: 64, filter: "packing" },
  { key: "sj", id: "sj", label: "Surat Jalan", group: "fulfillment", align: "left", type: "text", baseWidth: 118, minWidth: 80, wrap: true },
  { key: "driver", id: "driver", label: "Driver", group: "fulfillment", align: "left", type: "text", baseWidth: 118, minWidth: 80, wrap: true },
  { key: "status", id: "status", label: "Status Akhir", group: "fulfillment", align: "left", type: "status", baseWidth: 108, minWidth: 80, filter: "status", wrap: true },
];

export const PRODUCTION_SCHEDULE_COLUMN_IDS = PRODUCTION_SCHEDULE_TABLE_COLS.map((col) => col.key);

/** Desktop /produksi display only — Driver + Surat Jalan stay in the canonical inventory. */
export const DESKTOP_PRODUCTION_SCHEDULE_HIDDEN_KEYS = ["sj", "driver"];

export const DESKTOP_PRODUCTION_SCHEDULE_GROUPS = [
  { id: "identity", label: "Identitas PO", keys: ["po", "customer"] },
  { id: "planning", label: "Penjadwalan", keys: ["work_date", "operator"] },
  { id: "production", label: "Produksi", keys: ["item", "qty_cetak", "qty_operator", "progress", "qty", "prio", "buffer", "deadline", "deadline_ship", "outlet", "screen_size"] },
  { id: "progress", label: "Progress Produksi", keys: ["screen", "design", "payment", "hold", "helper", "packing"] },
  { id: "status", label: "Status", keys: ["status"] },
];

export const DESKTOP_PRODUCTION_SCHEDULE_COL_KEYS = DESKTOP_PRODUCTION_SCHEDULE_GROUPS.flatMap((g) => g.keys);

export function buildDesktopProductionScheduleCols(source = PRODUCTION_SCHEDULE_TABLE_COLS) {
  const byKey = new Map(source.map((col) => [col.key, col]));
  return DESKTOP_PRODUCTION_SCHEDULE_GROUPS.flatMap((group, groupIdx) => (
    group.keys.map((key, keyIdx) => {
      const col = byKey.get(key);
      if (!col) return null;
      const next = { ...col, desktopGroup: group.id, desktopGroupLabel: group.label };
      if (key === "po") next.sticky = "po";
      if (key === "customer") next.sticky = "customer";
      if (key === "item") {
        next.wrap = false;
        next.baseWidth = 148;
        next.maxWidth = 168;
      }
      if (keyIdx === 0 && groupIdx > 0) next.groupStart = true;
      return next;
    })
  )).filter(Boolean);
}

export const DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS = buildDesktopProductionScheduleCols();

export function desktopProductionScheduleGroupSpans(cols = DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS) {
  const spans = [];
  cols.forEach((col) => {
    const last = spans[spans.length - 1];
    if (last && last.id === col.desktopGroup) {
      last.span += 1;
      last.keys.push(col.key);
      return;
    }
    spans.push({
      id: col.desktopGroup,
      label: col.desktopGroupLabel,
      span: 1,
      keys: [col.key],
      sticky: col.sticky === "po" ? "identity" : null,
    });
  });
  return spans;
}

export function productionDashboardRequestParams(filters = {}) {
  const f = { ...FILTER_DEFAULTS, ...filters };
  const scope = f.scheduleScope === "all" ? "all" : "default";
  const q = String(f.q || "").trim();
  const workDateFrom = String(f.workDateFrom || "").trim();
  const workDateTo = String(f.workDateTo || "").trim();
  const dateFrom = String(f.dateFrom || "").trim();
  const dateTo = String(f.dateTo || "").trim();
  // Histori / date overrides need the catalog stream (not default-only).
  // Plain text search stays on the active schedule_scope so default mode still
  // returns bulan berjalan + unfinished, while Semua/riwayat can search history.
  const historyOpen = Boolean(
    f.workDateFilter === "hari_ini"
    || workDateFrom
    || workDateTo
    || dateFrom
    || dateTo
  );
  const params = {
    include_cancelled: Boolean(f.includeCancelled),
    include_completed: Boolean(f.includeCompleted) || f.workDateFilter === "hari_ini",
    // Additive backend path: same `_production_row` columns, no file-byte documents.
    view: "summary",
    schedule_scope: historyOpen || scope === "all" ? "all" : "default",
  };
  if (q) params.q = q;
  if (workDateFrom) params.work_date_from = workDateFrom;
  if (workDateTo) params.work_date_to = workDateTo;
  if (dateFrom) params.deadline_from = dateFrom;
  if (dateTo) params.deadline_to = dateTo;
  return params;
}

function summarizeDashboardPages(byId, last, baseParams, { pages, cursor, stoppedEarly, stoppedByTime }) {
  const scopeComplete = Boolean(last?.scope_complete) && !stoppedEarly && !stoppedByTime;
  const scanCapped = Boolean(last?.scan_capped) || stoppedEarly || stoppedByTime;
  const completeness = scopeComplete && !scanCapped ? "complete" : "incomplete";
  return {
    rows: Array.from(byId.values()),
    total: byId.size,
    completeness,
    read_limit: last?.read_limit,
    matched_count: last?.matched_count,
    candidate_count: last?.candidate_count,
    schedule_scope: last?.schedule_scope || baseParams.schedule_scope,
    scope_complete: scopeComplete,
    next_cursor: (stoppedEarly || stoppedByTime) ? cursor : null,
    pages_fetched: pages,
    scan_capped: scanCapped,
    stopped_by_time: Boolean(stoppedByTime),
  };
}

/** Internal staged fetch (no UI pager). Stops on scope_complete or safety caps. */
export async function fetchProductionDashboardPages(apiClient, baseParams = {}, options = {}) {
  const maxPages = Number(options.maxPages) > 0
    ? Number(options.maxPages)
    : PRODUCTION_SCHEDULE_FETCH_MAX_PAGES;
  const maxMs = Number(options.maxMs) > 0
    ? Number(options.maxMs)
    : PRODUCTION_SCHEDULE_FETCH_MAX_MS;
  const started = Date.now();
  const byId = new Map();
  let cursor;
  let pages = 0;
  let last = null;
  let stoppedByTime = false;
  do {
    if (pages > 0 && Date.now() - started >= maxMs) {
      stoppedByTime = true;
      break;
    }
    const params = { ...baseParams };
    if (cursor) params.cursor = cursor;
    delete params.refresh;
    // eslint-disable-next-line no-await-in-loop
    const payload = await apiClient.productionDashboard(params);
    last = payload || {};
    for (const row of last.rows || []) {
      if (row?.id == null || row.id === "") continue;
      byId.set(String(row.id), row);
    }
    pages += 1;
    cursor = last.next_cursor || null;
  } while (cursor && pages < maxPages);

  const hitPageCap = Boolean(cursor) && pages >= maxPages && !stoppedByTime;
  return summarizeDashboardPages(byId, last, baseParams, {
    pages,
    cursor,
    stoppedEarly: hitPageCap || stoppedByTime,
    stoppedByTime,
  });
}

function chunkIds(ids, size) {
  const out = [];
  const n = Math.max(1, Number(size) || 1);
  for (let i = 0; i < ids.length; i += n) out.push(ids.slice(i, i + n));
  return out;
}

/**
 * Light poll refresh:
 * 1) head discovery page (new / reshuffled scope matches)
 * 2) bounded id-batch reconcile for cached rows outside that head page
 *
 * Does not wipe unchanged cached rows on partial failure. Only drops ids listed
 * in an explicit reconcile ``missing_ids`` contract for batches that succeeded.
 */
export async function fetchProductionDashboardRefresh(apiClient, baseParams = {}, options = {}) {
  const prevRows = Array.isArray(options.prevRows) ? options.prevRows : [];
  const today = options.today || productionTodayKey();
  const scheduleScope = (baseParams.schedule_scope || "default") === "all" ? "all" : "default";
  const perRequest = Number(options.reconcileIdsPerRequest) > 0
    ? Number(options.reconcileIdsPerRequest)
    : PRODUCTION_SCHEDULE_RECONCILE_IDS_PER_REQUEST;
  const maxReconcileRequests = Number(options.reconcileMaxRequests) > 0
    ? Number(options.reconcileMaxRequests)
    : PRODUCTION_SCHEDULE_RECONCILE_MAX_REQUESTS;
  const rotateOffset = Math.max(0, Number(options.reconcileOffset) || 0);

  const byId = new Map();
  for (const row of prevRows) {
    if (row?.id == null || row.id === "") continue;
    byId.set(String(row.id), row);
  }

  let head = {};
  let pagesFetched = 0;
  let headFailed = false;
  let headErrorDetail = "";
  try {
    head = await apiClient.productionDashboard({
      ...baseParams,
      refresh: true,
    }) || {};
    pagesFetched += 1;
    for (const row of head.rows || []) {
      if (row?.id == null || row.id === "") continue;
      byId.set(String(row.id), row);
    }
  } catch (error) {
    headFailed = true;
    headErrorDetail = error?.response?.data?.detail || error?.message || "Gagal memverifikasi ulang jadwal";
    if (!byId.size) throw error;
  }

  const headIds = new Set((head.rows || []).map((row) => String(row.id)).filter(Boolean));
  const cachedIds = Array.from(byId.keys()).filter((id) => !headIds.has(id)).sort();
  const budget = perRequest * maxReconcileRequests;
  const rotateStart = cachedIds.length ? (rotateOffset % cachedIds.length) : 0;
  const windowIds = [];
  for (let i = 0; i < cachedIds.length && windowIds.length < budget; i += 1) {
    windowIds.push(cachedIds[(rotateStart + i) % cachedIds.length]);
  }
  const skippedIds = cachedIds.length > windowIds.length;
  // If head discovery failed, skip reconcile this cycle — keep cache, mark stale.
  const batches = headFailed
    ? []
    : chunkIds(windowIds, perRequest).slice(0, maxReconcileRequests);

  let reconcileFailed = false;
  let reconcileTruncated = false;
  let reconcileErrorDetail = "";
  const missingIds = [];
  for (const batch of batches) {
    if (!batch.length) continue;
    try {
      // eslint-disable-next-line no-await-in-loop
      const payload = await apiClient.productionDashboard({
        ...baseParams,
        ids: batch.join(","),
      }) || {};
      pagesFetched += 1;
      if (payload.reconcile_truncated) reconcileTruncated = true;
      // Contract: only drop ids explicitly reported missing for this batch.
      if (Array.isArray(payload.missing_ids)) {
        for (const oid of payload.missing_ids) {
          const key = String(oid || "");
          if (!key) continue;
          missingIds.push(key);
          byId.delete(key);
        }
      }
      for (const row of payload.rows || []) {
        if (row?.id == null || row.id === "") continue;
        byId.set(String(row.id), row);
      }
    } catch (error) {
      reconcileFailed = true;
      reconcileErrorDetail = error?.response?.data?.detail || error?.message || "Gagal rekonsiliasi batch jadwal";
      // Keep cached rows for this batch — do not wipe on partial failure.
      break;
    }
  }

  let rows = Array.from(byId.values());
  if (scheduleScope === "default") {
    rows = rows.filter((row) => rowMatchesDefaultProductionScheduleScope(row, today));
  }

  const priorComplete = options.priorScopeComplete === true
    && options.priorCompleteness === "complete";
  const reconcileStale = Boolean(
    headFailed || reconcileFailed || reconcileTruncated || skippedIds || options.priorScanCapped
  );
  const completeness = (priorComplete && !reconcileStale)
    ? "complete"
    : (reconcileStale ? "incomplete" : (options.priorCompleteness || "incomplete"));
  const nextOffset = headFailed
    ? rotateOffset
    : (cachedIds.length ? (rotateStart + windowIds.length) % cachedIds.length : 0);

  return {
    rows,
    total: rows.length,
    completeness,
    read_limit: head.read_limit ?? options.priorReadLimit,
    matched_count: head.matched_count ?? options.priorMatchedCount,
    candidate_count: head.candidate_count ?? options.priorCandidateCount,
    schedule_scope: head.schedule_scope || baseParams.schedule_scope,
    scope_complete: priorComplete && !reconcileStale,
    next_cursor: (priorComplete && !reconcileStale)
      ? null
      : (options.priorNextCursor || head.next_cursor || null),
    pages_fetched: pagesFetched,
    refresh: true,
    mode: "refresh",
    scan_capped: Boolean(head.scan_capped) || Boolean(options.priorScanCapped),
    reconcile_stale: reconcileStale,
    reconcile_failed: reconcileFailed || headFailed,
    refresh_error: headErrorDetail || reconcileErrorDetail || "",
    missing_ids: missingIds,
    reconcile_offset: nextOffset,
    requests_this_cycle: pagesFetched,
  };
}

export function productionSheetColumnStyle(col, widthOverride) {
  const width = Number(widthOverride != null ? widthOverride : col?.baseWidth) || 96;
  const minWidth = Number(col?.minWidth) || 56;
  return {
    width: `calc(${width}px * var(--production-sheet-zoom, 1))`,
    minWidth: `calc(${minWidth}px * var(--production-sheet-zoom, 1))`,
    maxWidth: `calc(${width}px * var(--production-sheet-zoom, 1))`,
  };
}

export function productionSheetCellClassName(col, extra = "") {
  const parts = ["production-sheet-cell"];
  if (col?.num || col?.align === "right" || col?.type === "qty") {
    parts.push("num", "production-sheet-cell-num");
  }
  if (col?.wrap) parts.push("production-sheet-cell-wrap");
  if (col?.type === "status") parts.push("production-sheet-cell-status");
  if (col?.sticky === "po") parts.push("prod-sticky-col", "prod-sticky-po");
  if (col?.sticky === "customer") parts.push("prod-sticky-col", "prod-sticky-customer");
  if (col?.groupStart) parts.push("prod-group-start");
  if (col?.desktopGroup) parts.push(`prod-col-group-${col.desktopGroup}`);
  if (extra) parts.push(extra);
  return parts.filter(Boolean).join(" ");
}
