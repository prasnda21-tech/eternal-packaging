/** Maps audited API payloads into widget view models. No sample numbers. */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];
const MONTHS_LONG = [
  "Januari", "Februari", "Maret", "April", "Mei", "Juni",
  "Juli", "Agustus", "September", "Oktober", "November", "Desember",
];
const WEEKDAYS = ["Minggu", "Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"];

export function formatLongDate(iso) {
  const text = String(iso || "").slice(0, 10);
  const [year, month, day] = text.split("-");
  const monthIndex = Number(month) - 1;
  if (!year || !day || monthIndex < 0 || monthIndex > 11) return "";
  return `${Number(day)} ${MONTHS[monthIndex]} ${year}`;
}

export function formatShortAxisDate(iso) {
  const text = String(iso || "").slice(0, 10);
  const [year, month, day] = text.split("-");
  const monthIndex = Number(month) - 1;
  if (!year || !day || monthIndex < 0 || monthIndex > 11) return "";
  return `${Number(day)} ${MONTHS[monthIndex]}`;
}

/** Full weekday date for tooltips, calendar-day only (no TZ shift). */
export function formatWeekdayLongDate(iso) {
  const text = String(iso || "").slice(0, 10);
  const [year, month, day] = text.split("-").map(Number);
  if (!year || !month || !day) return "";
  const utc = new Date(Date.UTC(year, month - 1, day, 12));
  if (Number.isNaN(utc.getTime())) return "";
  return `${WEEKDAYS[utc.getUTCDay()]}, ${day} ${MONTHS_LONG[month - 1]} ${year}`;
}

export function formatRange(start, end) {
  if (!start || !end) return "";
  if (start === end) return formatLongDate(start);
  return `${formatLongDate(start)} – ${formatLongDate(end)}`;
}

export function failedModel(error) {
  const status = error?.response?.status;
  if (status === 403) return { status: "forbidden", detail: "Tidak ada izin." };
  const detail = error?.response?.data?.detail;
  return { status: "error", detail: typeof detail === "string" ? detail : "Gagal memuat" };
}

export function loadingModel() {
  return { status: "loading" };
}

export function forbiddenModel() {
  return { status: "forbidden", detail: "Tidak ada izin." };
}

function sectionStatus(section) {
  return section && section.status ? section.status : "unavailable";
}

export function mapOmzet(section, periodLabel) {
  if (!section) return { status: "unavailable" };
  return {
    status: sectionStatus(section),
    amount: section.amount,
    invoiceCount: section.invoice_count,
    detail: section.detail || (section.comparison && section.comparison.detail) || null,
    periodLabel,
  };
}

export function mapSettlement(section) {
  if (!section || !Object.prototype.hasOwnProperty.call(section, "open_grand_total")) {
    return { status: "unavailable" };
  }
  return {
    status: sectionStatus(section),
    settledGrand: section.settled_grand_total,
    settledCount: section.settled_count,
    openGrand: section.open_grand_total,
    openCount: section.open_count,
    scale: "total",
    detail: section.detail,
  };
}

export function mapTrend(section) {
  if (!section) return { status: "unavailable", points: [] };
  const points = Array.isArray(section.points)
    ? section.points.map((point) => ({ label: point.label, amount: point.omzet }))
    : [];
  return {
    status: sectionStatus(section),
    points,
    grain: section.grain_label || section.grain || "",
    detail: section.detail,
  };
}

export function mapSales(section) {
  if (!section) return { status: "unavailable", rows: [] };
  const rows = (section.groups || []).map((row, index) => ({
    name: row.label,
    amount: row.omzet,
    invoices: row.invoice_count,
    share: row.share == null ? null : row.share,
    key: row.key || `${row.label || "grup"}:${index}`,
  }));
  return {
    status: sectionStatus(section),
    rows,
    unassigned: section.unassigned_invoice_count,
    detail: section.detail,
  };
}

export function mapProducts(section) {
  if (!section) return { status: "unavailable", rows: [] };
  const source = Array.isArray(section.all_items) ? section.all_items : (section.items || []);
  return {
    status: sectionStatus(section),
    rows: source.map((row, index) => ({
      name: row.label,
      amount: row.amount,
      qty: row.quantity,
      unit: row.unit || "",
      key: row.key || `${row.label || "barang"}:${index}`,
      share: row.share == null ? null : row.share,
    })),
    unidentified: section.unidentified_line_count,
    detail: section.detail,
  };
}

export function mapAging(section, asOfLabel) {
  if (!section) return { status: "unavailable", rows: [] };
  return {
    status: sectionStatus(section),
    rows: (section.aging || []).map((row) => ({
      key: row.key,
      label: row.label,
      name: row.label,
      amount: row.amount,
    })),
    unbucketed: section.unbucketed_outstanding,
    scope: section.as_of_label || "Saat ini",
    asOfLabel,
    detail: section.detail || section.coverage_note,
  };
}

export function mapCustomerRank(section) {
  if (!section) return { status: "unavailable", rows: [] };
  return {
    status: sectionStatus(section),
    rows: (section.groups || []).map((row) => ({
      key: row.key,
      name: row.label,
      amount: row.amount,
      invoices: row.invoice_count,
    })),
    detail: section.detail,
  };
}

export function mapToday(section, todayLabel) {
  if (!section) return { status: "unavailable" };
  const count = section.invoice_count;
  const ready = section.status === "complete" && Number(count) > 0 && section.amount != null;
  return {
    status: sectionStatus(section),
    amount: section.amount,
    invoiceCount: count,
    average: ready ? Number(section.amount) / Number(count) : null,
    todayLabel,
    detail: section.detail,
  };
}

export function mapPurchases(body) {
  if (!body) return { status: "unavailable" };
  return {
    status: sectionStatus(body),
    amount: body.amount,
    settledGrand: body.paid_amount,
    settledCount: body.paid_count,
    openOutstanding: body.unpaid_amount,
    openCount: body.unpaid_count,
    scale: "total",
    detail: body.detail,
  };
}

export function mapPayables(summary) {
  if (!summary) return { status: "unavailable" };
  const parts = ["d1_30", "d31_60", "d61_90", "over_90"].map((key) => summary[key]);
  if (summary.not_due == null || parts.some((value) => value == null)) {
    return { status: "unavailable", detail: "Ringkasan utang tidak lengkap." };
  }
  const overdue = parts.reduce((sum, value) => sum + Number(value), 0);
  const anomalies = Array.isArray(summary.integrity_anomalies) ? summary.integrity_anomalies.length : 0;
  const total = Number(summary.not_due) + overdue;
  let status = total === 0 ? "zero" : "complete";
  if (anomalies) status = "incomplete";
  return {
    status,
    notDue: Number(summary.not_due),
    overdue,
    scale: "total",
    scope: "Saat ini",
    asOfLabel: formatLongDate(summary.aging_reference_date),
    detail: anomalies ? "Ada peringatan integritas pada utang akuntansi." : "Saldo saat ini, bukan akhir filter.",
  };
}

export function mapCash(section) {
  if (!section) return { status: "unavailable" };
  return {
    status: sectionStatus(section),
    cashIn: section.cash_in,
    cashOut: section.cash_out,
    net: section.net_change,
    internal: section.internal_transfer_volume,
    detail: section.detail || section.note,
  };
}

export function mapExpense(section) {
  if (!section) return { status: "unavailable", rows: [] };
  return {
    status: sectionStatus(section),
    rows: (section.items || []).map((row) => ({
      name: row.name || row.code || "Akun",
      amount: row.amount,
      key: row.account_id || row.code,
    })),
    detail: section.detail,
  };
}

export function mapProfit(periodBody, ytdBody, ytdLabel) {
  const rowsOf = (body) => {
    if (!body || body.status === "empty" || body.status === "error" || body.status === "unavailable") return [];
    return [
      { name: "Pendapatan", amount: body.revenue },
      { name: "Harga pokok", amount: body.cogs },
      { name: "Beban", amount: body.operating_expense },
      { name: "Pendapatan lain", amount: body.other_income },
      { name: "Beban lain", amount: body.other_expense },
      { name: "Laba bersih", amount: body.net_profit },
    ];
  };
  return {
    status: sectionStatus(periodBody),
    rows: rowsOf(periodBody),
    ytd: rowsOf(ytdBody),
    ytdStatus: ytdBody ? sectionStatus(ytdBody) : "loading",
    ytdLabel,
    detail: periodBody?.detail,
    ytdDetail: ytdBody?.detail,
  };
}

export function mapAssets(sheet, asOfLabel) {
  const summary = sheet?.summary;
  if (!summary || summary.total_assets == null) return { status: "unavailable", detail: "Total aset belum tersedia." };
  return {
    status: summary.is_balanced === false ? "incomplete" : "complete",
    amount: summary.total_assets,
    difference: summary.balance_difference,
    balanced: summary.is_balanced === true,
    asOfLabel,
    scope: "Saat ini",
    detail: summary.is_balanced === false ? "Neraca belum seimbang." : null,
  };
}

export function mapOverdue(body) {
  if (!body) return { status: "unavailable", rows: [] };
  return {
    status: sectionStatus(body),
    rows: (body.items || []).map((row) => ({
      key: row.customer_key,
      name: row.customer_name,
      amount: row.accounting_outstanding,
      days: row.days_overdue,
    })),
    scope: "Saat ini",
    asOfLabel: formatLongDate(body.aging_reference_date),
    detail: body.detail,
  };
}

export function mapReconciliations(body) {
  if (!body) return { status: "unavailable", rows: [], pairs: [] };
  const items = Array.isArray(body.items) ? body.items : (Array.isArray(body) ? body : []);
  const pairItems = Array.isArray(body.pairs) ? body.pairs : [];
  const rows = items.map((row) => ({
    name: row.cash_bank_name || "Akun",
    order: row.number || "",
    date: formatLongDate(row.reconciliation_date),
    statusLabel: row.status === "finalized" ? "Finalized" : "Draft",
    amount: row.difference,
    zero: Number(row.difference) === 0,
    key: row.id,
    kind: "legacy",
  }));
  const pairs = pairItems.map((row, index) => ({
    name: row.cash_bank_name || "Rekening",
    date: formatLongDate(row.date),
    statusLabel: "Aktif",
    direction: row.direction || "",
    key: row.id || `pair:${index}`,
    kind: "pair",
  }));
  const status = body.status || ((rows.length || pairs.length) ? "complete" : "empty");
  return {
    status,
    rows,
    pairs,
    detail: body.detail || (status === "empty" ? "Belum ada pencocokan transaksi maupun rekonsiliasi saldo lama." : null),
  };
}

export function mapShipToday(payload) {
  const ship = payload?.shipments_today;
  if (!ship) return { status: "unavailable", rows: [] };
  const rows = (ship.preview || []).map((row) => ({
    name: row.customer || "—",
    order: row.po_number || "",
    date: formatLongDate(row.tanggal_pengiriman || ship.date),
    key: row.row_id || row.id,
  }));
  const truncated = Boolean(ship.has_more);
  return {
    status: truncated ? "incomplete" : (ship.total === 0 ? "empty" : "complete"),
    rows,
    total: ship.total,
    detail: truncated
      ? `Tabel menampilkan ${rows.length} dari ${ship.total} baris. Jumlah mengikuti total service, bukan panjang tabel.`
      : null,
  };
}

export function mapSchedule(payload, todayIso) {
  const rows = Array.isArray(payload?.rows) ? payload.rows : null;
  if (!rows) return { late: { status: "unavailable", rows: [] }, next: { status: "unavailable", rows: [] } };
  const late = [];
  const next = [];
  rows.forEach((row) => {
    const view = {
      name: row.customer_name || "—",
      order: row.order_number || "",
      date: formatLongDate(row.tanggal_pengiriman),
      key: `${row.id || ""}:${row.shipment_id || row.shipment_index || ""}`,
    };
    if (row.status === "terlambat") late.push(view);
    if (row.tanggal_pengiriman && todayIso && row.tanggal_pengiriman > todayIso) next.push(view);
  });
  const pack = (list) => ({
    status: list.length ? "complete" : "empty",
    rows: list,
    detail: list.length ? null : "Belum ada baris pada status ini.",
  });
  return { late: pack(late), next: pack(next) };
}

export function mapProduction(payload, todayIso) {
  const source = Array.isArray(payload?.rows) ? payload.rows : null;
  if (!source) return { status: "unavailable", rows: [] };
  const truncated = source.length >= 1000;
  const rows = [];
  source.forEach((row) => {
    const dates = [];
    if (row.work_date) dates.push(String(row.work_date).slice(0, 10));
    (row.operator_work_dates || []).forEach((item) => {
      if (item?.work_date) dates.push(String(item.work_date).slice(0, 10));
    });
    const future = dates.filter((value) => value && todayIso && value > todayIso).sort();
    if (!future.length) return;
    const names = [...new Set((row.operators || []).map((op) => op.line_name).filter(Boolean))];
    const label = names.length > 1 ? `${names[0]} · ${names.length} barang` : (names[0] || row.customer || "—");
    rows.push({
      name: label,
      order: row.po_number || "",
      date: formatLongDate(future[0]),
      key: row.id,
    });
  });
  if (truncated) {
    return {
      status: "incomplete",
      rows,
      detail: "Daftar jadwal produksi terpotong. Total pengerjaan mendatang tidak dihitung dari potongan.",
    };
  }
  return {
    status: rows.length ? "complete" : "empty",
    rows,
    detail: rows.length ? null : "Belum ada tanggal pengerjaan sesudah hari ini.",
  };
}

const DELIVERY_UNIT_LABEL = {
  tanpa_satuan: "tanpa satuan",
  campuran: "campuran",
};

export function deliveryQtyUnitLabel(unit) {
  if (DELIVERY_UNIT_LABEL[unit]) return DELIVERY_UNIT_LABEL[unit];
  return String(unit || "").toLowerCase();
}

export function mapDeliveryQty(body) {
  if (!body || !Array.isArray(body.series)) return { status: "unavailable", series: [], units: [], notes: [] };
  const series = body.series.map((row) => ({
    unit: row.unit,
    label: deliveryQtyUnitLabel(row.unit),
    todayQty: Number(row.today_qty) || 0,
    todayPoCount: Number(row.today_po_count) || 0,
    overdueQty: Number(row.overdue_qty) || 0,
    overduePoCount: Number(row.overdue_po_count) || 0,
    points: Array.isArray(row.points)
      ? row.points.map((point) => ({
        date: point.date,
        day: point.date,
        label: formatLongDate(point.date),
        shortLabel: formatShortAxisDate(point.date),
        tipLabel: formatWeekdayLongDate(point.date),
        qty: Number(point.qty) || 0,
        poCount: Number(point.po_count) || 0,
      }))
      : [],
  }));
  return {
    status: body.status || "unavailable",
    days: body.days,
    today: body.today,
    todayLabel: formatLongDate(body.today),
    units: series.map((row) => row.unit),
    series,
    notes: Array.isArray(body.notes) ? body.notes : [],
    detail: body.detail || null,
  };
}

export function chartWithheld(amounts) {
  const numbers = amounts.map((value) => Number(value));
  if (numbers.some((value) => !Number.isFinite(value))) return true;
  if (numbers.some((value) => value < 0)) return true;
  const total = numbers.reduce((sum, value) => sum + value, 0);
  return total <= 0;
}
