/** Helpers for dashboard KPI → Rincian Dashboard (spreadsheet) drills. */

export function canOpenDrill(drill, hasPerm) {
  if (!drill?.path || typeof hasPerm !== "function") return false;
  const anyOf = drill.any_of || [];
  if (anyOf.length && !anyOf.some((key) => hasPerm(key))) return false;
  const also = drill.also_any_of || [];
  if (also.length && !also.some((key) => hasPerm(key))) return false;
  return true;
}

export function drillPath(drill, hasPerm) {
  return canOpenDrill(drill, hasPerm) ? drill.path : null;
}

/** Build `/dashboard/rincian?...` for one widget. */
export function dashboardRincianPath({
  widget,
  dashboard = "produksi",
  startDate = "",
  endDate = "",
  unit = "",
  status = "",
  basis = "",
  cashBankId = "",
  q = "",
} = {}) {
  const params = new URLSearchParams();
  if (dashboard) params.set("dashboard", dashboard);
  if (widget) params.set("widget", widget);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (unit) params.set("unit", unit);
  if (status) params.set("status", status);
  if (basis) params.set("basis", basis);
  if (cashBankId) params.set("cash_bank_id", cashBankId);
  if (q) params.set("q", q);
  return `/dashboard/rincian?${params.toString()}`;
}

/** Legacy ops list URL (not used by dashboard cards). */
export function fakturPenjualanOpsPath({
  dateFrom = "",
  dateTo = "",
  datePreset = "",
  financialStatus = "finalized",
  paymentStatus = "",
  sales = "",
  customerId = "",
  tab = "all",
} = {}) {
  const params = new URLSearchParams();
  params.set("tab", tab || "all");
  if (financialStatus) params.set("financial_status", financialStatus);
  if (paymentStatus) params.set("payment_status", paymentStatus);
  if (datePreset) params.set("date_preset", datePreset);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  if (sales) params.set("sales", sales);
  if (customerId) params.set("customer_id", customerId);
  return `/penjualan/faktur-penjualan?${params.toString()}`;
}

/** Sales/finance omzet-style cards → Rincian Dashboard. */
export function fakturPenjualanDrillPath({
  dateFrom = "",
  dateTo = "",
  datePreset = "",
  widget = "omzet",
  dashboard = "penjualan",
} = {}) {
  let key = widget;
  if (widget === "omzet" && (datePreset === "today" || (dateFrom && dateTo && dateFrom === dateTo))) {
    key = "today";
  }
  return dashboardRincianPath({
    widget: key,
    dashboard,
    startDate: dateFrom,
    endDate: dateTo,
  });
}

export function deliveryScheduleDrillPath(status, { dashboard = "penjualan" } = {}) {
  const key = String(status || "").trim().toLowerCase();
  const widget = key === "terlambat" ? "shipLate" : "shipToday";
  return dashboardRincianPath({
    widget,
    dashboard,
    status: key || undefined,
  });
}

export function deliveryPlanDrillPath({ status = "", dateFrom = "", dateTo = "", unit = "" } = {}) {
  const st = String(status || "").trim().toLowerCase();
  const widget = st === "terlambat" ? "delivery_qty_overdue" : "delivery_qty_today";
  return dashboardRincianPath({
    widget,
    dashboard: "produksi",
    status: st,
    startDate: dateFrom,
    endDate: dateTo,
    unit,
  });
}

export function mutasiKasDrillPath({ start = "", end = "", dashboard = "keuangan" } = {}) {
  return dashboardRincianPath({
    widget: "cash",
    dashboard,
    startDate: start,
    endDate: end,
  });
}

export function labaRugiDrillPath({ start = "", end = "", widget = "profit", dashboard = "keuangan" } = {}) {
  return dashboardRincianPath({
    widget: widget === "expense" ? "expense" : "profit",
    dashboard,
    startDate: start,
    endDate: end,
  });
}

export function fakturPembelianDrillPath({ dateFrom = "", dateTo = "", dashboard = "keuangan" } = {}) {
  return dashboardRincianPath({
    widget: "purchases",
    dashboard,
    startDate: dateFrom,
    endDate: dateTo,
  });
}

export function isInteractiveDrillEventTarget(target) {
  if (!target || typeof target.closest !== "function") return false;
  return Boolean(
    target.closest(
      "a,button,input,select,textarea,label,option,[role='menu'],[role='menuitem'],[role='listbox'],[data-drill-ignore='true']",
    ),
  );
}
