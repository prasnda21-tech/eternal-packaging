/** Helpers for Laporan Harian CRM — no side effects on master/PO. */

export const CRM_DAILY_REPORT_LIST_PATH = "/penjualan/laporan-harian-crm";
export const CRM_DAILY_REPORT_NEW_PATH = "/penjualan/laporan-harian-crm/baru";

export function canCreateCrmDailyReport(hasPerm) {
  return Boolean(hasPerm?.("crm.daily_report.create"));
}

export function canViewOwnCrmDailyReport(hasPerm) {
  return Boolean(
    hasPerm?.("crm.daily_report.view_own")
    || hasPerm?.("crm.daily_report.create")
    || hasPerm?.("crm.daily_report.edit_own"),
  );
}

export function canViewAllCrmDailyReports(hasPerm) {
  return Boolean(hasPerm?.("crm.daily_report.view_all"));
}

export function canEditOwnCrmDailyReport(hasPerm) {
  return Boolean(hasPerm?.("crm.daily_report.edit_own"));
}

export function canAccessCrmDailyReport(hasPerm) {
  return (
    canCreateCrmDailyReport(hasPerm)
    || canViewOwnCrmDailyReport(hasPerm)
    || canViewAllCrmDailyReports(hasPerm)
    || Boolean(hasPerm?.("nav.sales.crm_daily_report"))
  );
}

export function crmDailyReportFormPath(id) {
  if (!id || id === "baru") return CRM_DAILY_REPORT_NEW_PATH;
  return `${CRM_DAILY_REPORT_LIST_PATH}/${encodeURIComponent(id)}`;
}

export function truncateText(value, max = 48) {
  const text = String(value || "").trim();
  if (!text) return "—";
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

export function complaintLabel(row) {
  if (!row) return "—";
  if (!row.has_complaint) return "Tidak";
  return truncateText(row.complaint_description || "Ya", 40);
}

export function issueLabel(row) {
  if (!row) return "—";
  if (!row.has_issue) return "Tidak";
  return truncateText(row.issue_description || "Ya", 40);
}

export function emptyCrmDailyReportForm(todayWib = "") {
  return {
    report_date: todayWib,
    status_customer: "New",
    customer_id: "",
    outlet_name: "",
    whatsapp: "",
    order_id: "",
    po_number: "",
    sales_label: "",
    sales_user_id: "",
    status_order: "ORDER",
    no_order_reason: "",
    has_complaint: false,
    complaint_description: "",
    has_issue: false,
    issue_description: "",
    notes: "",
  };
}

export function validateCrmDailyReportForm(form) {
  const errors = {};
  if (!String(form.report_date || "").trim()) errors.report_date = "Tanggal wajib";
  if (!["New", "Repeat"].includes(form.status_customer)) errors.status_customer = "Status Customer wajib";
  if (!String(form.sales_label || "").trim()) errors.sales_label = "Sales wajib";
  if (!["ORDER", "TIDAK ORDER"].includes(form.status_order)) errors.status_order = "Status Order wajib";

  if (form.status_customer === "New") {
    if (!String(form.whatsapp || "").trim()) errors.whatsapp = "Nomor WhatsApp wajib untuk New";
  } else if (!String(form.customer_id || "").trim()) {
    errors.customer_id = "Pilih customer dari master untuk Repeat";
  }

  if (form.status_order === "TIDAK ORDER" && !String(form.no_order_reason || "").trim()) {
    errors.no_order_reason = "Keterangan Tidak Order wajib";
  }
  if (form.has_complaint && !String(form.complaint_description || "").trim()) {
    errors.complaint_description = "Deskripsi Komplain wajib";
  }
  if (form.has_issue && !String(form.issue_description || "").trim()) {
    errors.issue_description = "Deskripsi Kendala wajib";
  }
  return errors;
}

export function toCrmDailyReportPayload(form, clientSubmitId) {
  const payload = {
    report_date: String(form.report_date || "").trim(),
    status_customer: form.status_customer,
    customer_id: form.status_customer === "Repeat" ? String(form.customer_id || "").trim() : "",
    outlet_name: form.status_customer === "New" ? String(form.outlet_name || "").trim() : "",
    whatsapp: form.status_customer === "New" ? String(form.whatsapp || "").trim() : "",
    order_id: String(form.order_id || "").trim() || null,
    po_number: String(form.po_number || "").trim(),
    sales_label: String(form.sales_label || "").trim(),
    sales_user_id: String(form.sales_user_id || "").trim() || null,
    status_order: form.status_order,
    no_order_reason: form.status_order === "TIDAK ORDER" ? String(form.no_order_reason || "").trim() : "",
    has_complaint: Boolean(form.has_complaint),
    complaint_description: form.has_complaint ? String(form.complaint_description || "").trim() : "",
    has_issue: Boolean(form.has_issue),
    issue_description: form.has_issue ? String(form.issue_description || "").trim() : "",
    notes: String(form.notes || "").trim(),
  };
  if (clientSubmitId) payload.client_submit_id = clientSubmitId;
  return payload;
}
