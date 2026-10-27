/**
 * Rincian Pengiriman for Rencana Jadwal Kirim — PO-shaped, full filtered set.
 * Does not reuse SJ shipment qty semantics from Jadwal Pengiriman.
 */
import { encodeHeaderFilters } from "../components/deliveryPlan/deliveryPlanTableLayout";
import { formatDeliveryDetailDate } from "./deliverySchedule";

export const PLAN_DETAILS_PAGE_SIZE = 200;
export const DEFAULT_PLAN_SORT = Object.freeze({
  sortBy: "tanggal_pengiriman",
  sortDir: "desc",
});

function dateKey(value) {
  const s = String(value || "").trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
  return "";
}

function intQty(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.trunc(n) : 0;
}

function fmtQty(n) {
  return intQty(n).toLocaleString("id-ID");
}

export function formatPlanPeriodLabel(dateFrom, dateTo) {
  const from = dateKey(dateFrom);
  const to = dateKey(dateTo);
  if (from && to && from === to) return formatDeliveryDetailDate(from);
  if (from && to) return `${formatDeliveryDetailDate(from)} – ${formatDeliveryDetailDate(to)}`;
  if (from) return `Dari ${formatDeliveryDetailDate(from)}`;
  if (to) return `Sampai ${formatDeliveryDetailDate(to)}`;
  return "Semua tanggal";
}

export function buildDeliveryPlanListParams({
  filters = {},
  colFilters = {},
  sortBy = DEFAULT_PLAN_SORT.sortBy,
  sortDir = DEFAULT_PLAN_SORT.sortDir,
  page = 1,
  pageSize = PLAN_DETAILS_PAGE_SIZE,
} = {}) {
  const params = {
    page: Math.max(1, Number(page) || 1),
    page_size: Math.max(1, Math.min(Number(pageSize) || PLAN_DETAILS_PAGE_SIZE, PLAN_DETAILS_PAGE_SIZE)),
  };
  const q = String(filters.q || "").trim();
  if (q) params.q = q;
  if (filters.source) params.source = filters.source;
  if (filters.status) params.status = filters.status;
  if (filters.date_from) params.date_from = filters.date_from;
  if (filters.date_to) params.date_to = filters.date_to;
  if (filters.unit) params.unit = filters.unit;
  const by = String(sortBy || "").trim() || DEFAULT_PLAN_SORT.sortBy;
  const dir = String(sortDir || "").trim() || DEFAULT_PLAN_SORT.sortDir;
  params.sort_by = by;
  params.sort_dir = dir === "asc" ? "asc" : "desc";
  const hf = encodeHeaderFilters(colFilters);
  if (hf.length) params.hf = hf;
  return params;
}

/**
 * Fetch every page of the filtered Delivery Plan list (max page_size=200).
 * Dedupes by canonical PO id. Throws on abort or mid-flight failure.
 */
export async function fetchAllDeliveryPlanRows({
  fetchPage,
  filters,
  colFilters,
  sortBy = DEFAULT_PLAN_SORT.sortBy,
  sortDir = DEFAULT_PLAN_SORT.sortDir,
  signal,
} = {}) {
  if (typeof fetchPage !== "function") {
    throw new Error("fetchPage wajib diisi.");
  }
  const rows = [];
  const seen = new Set();
  let page = 1;
  let totalPages = 1;
  let total = 0;
  let apiTotals = null;
  const maxPages = 500;

  while (page <= totalPages) {
    if (signal?.aborted) {
      const err = new Error("Dibatalkan");
      err.name = "AbortError";
      throw err;
    }
    const params = buildDeliveryPlanListParams({
      filters,
      colFilters,
      sortBy,
      sortDir,
      page,
      pageSize: PLAN_DETAILS_PAGE_SIZE,
    });
    const data = await fetchPage(params);
    if (signal?.aborted) {
      const err = new Error("Dibatalkan");
      err.name = "AbortError";
      throw err;
    }
    total = Number(data?.total) || 0;
    totalPages = Math.max(1, Number(data?.total_pages) || 1);
    if (data?.totals) apiTotals = data.totals;
    for (const row of data?.rows || []) {
      if (!row || typeof row !== "object") continue;
      const id = String(row.id || row.order_id || "").trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      rows.push(row);
    }
    if (page >= totalPages) break;
    page += 1;
    if (page > maxPages) {
      throw new Error("Dataset Rencana Jadwal Kirim terlalu besar untuk dirinci sekaligus.");
    }
  }

  const complete = rows.length === total || (total === 0 && rows.length === 0);
  return {
    rows,
    total,
    totals: apiTotals,
    complete,
    pageCount: page,
  };
}

/**
 * Build PO-shaped Rincian Pengiriman from the complete filtered row set.
 * Day groups newest-first. Quantities use plan KPI fields (not SJ qty).
 */
export function buildDeliveryPlanDetailsSummary(rows, opts = {}) {
  const list = Array.isArray(rows) ? rows : [];
  const periodLabel = formatPlanPeriodLabel(opts.dateFrom || opts.date_from, opts.dateTo || opts.date_to);

  const byDate = new Map();
  let qtyPesanan = 0;
  let qtyTerkirim = 0;
  let sisaQty = 0;

  for (const row of list) {
    const date = dateKey(row?.tanggal_pengiriman || row?.effective_delivery_date);
    const groupKey = date || "";
    if (!byDate.has(groupKey)) {
      byDate.set(groupKey, {
        date: groupKey,
        dateLabel: date ? formatDeliveryDetailDate(date) : "Tanpa tanggal",
        rows: [],
        qtyPesanan: 0,
        qtyTerkirim: 0,
        sisaQty: 0,
        units: new Set(),
      });
    }
    const group = byDate.get(groupKey);
    const pesanan = intQty(row?.qty_total);
    const terkirim = intQty(row?.qty_delivered);
    const sisa = intQty(row?.qty_remaining);
    const unit = String(row?.qty_unit || "").trim();
    if (unit) group.units.add(unit);
    group.qtyPesanan += pesanan;
    group.qtyTerkirim += terkirim;
    group.sisaQty += sisa;
    qtyPesanan += pesanan;
    qtyTerkirim += terkirim;
    sisaQty += sisa;
    const poId = String(row?.id || row?.order_id || "").trim();
    group.rows.push({
      key: poId || `${row?.po_number || ""}:${date}`,
      date,
      poNumber: String(row?.po_number || "").trim() || "—",
      customerName: String(row?.customer_name || "").trim() || "—",
      zone: String(row?.delivery_zone || "").trim() || "—",
      qtyPesanan: pesanan,
      qtyTerkirim: terkirim,
      sisaQty: sisa,
      qtyPesananLabel: fmtQty(pesanan),
      qtyTerkirimLabel: fmtQty(terkirim),
      sisaQtyLabel: fmtQty(sisa),
      unit,
    });
  }

  const days = Array.from(byDate.values())
    .sort((a, b) => String(b.date).localeCompare(String(a.date)))
    .map((g) => {
      const unitHint = g.units.size === 1 ? Array.from(g.units)[0] : (g.units.size > 1 ? "campuran" : "");
      return {
        date: g.date,
        dateLabel: g.dateLabel,
        rows: g.rows,
        qtyPesanan: g.qtyPesanan,
        qtyTerkirim: g.qtyTerkirim,
        sisaQty: g.sisaQty,
        unitHint,
        totalLabel: `Pesanan ${fmtQty(g.qtyPesanan)} · Terkirim ${fmtQty(g.qtyTerkirim)} · Sisa ${fmtQty(g.sisaQty)}`,
      };
    });

  const poCount = list.length;
  return {
    periodLabel,
    poCount,
    qtyPesanan,
    qtyTerkirim,
    sisaQty,
    qtyPesananLabel: fmtQty(qtyPesanan),
    qtyTerkirimLabel: fmtQty(qtyTerkirim),
    sisaQtyLabel: fmtQty(sisaQty),
    days,
    empty: poCount === 0,
  };
}
