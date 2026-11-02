/** Helpers for Laporan Harian CRM — no side effects on master/PO. */

export const CRM_DAILY_REPORT_LIST_PATH = "/penjualan/laporan-harian-crm";
export const CRM_DAILY_REPORT_NEW_PATH = "/penjualan/laporan-harian-crm/baru";

/** Excel-aligned Status Order literals (DB/API). */
export const STATUS_ORDER_OPTIONS = [
  "ORDER",
  "TIDAK ORDER",
  "PENGAJUAN PO",
  "PENGAJUAN DITOLAK",
  "FOLLOW UP",
];

/** Exact Excel texts for Keterangan Tidak Order. */
export const NO_ORDER_REASON_PRESETS = [
  "Harga terlalu mahal",
  "Slow Respon Customer",
  "Deadline tidak cocok",
  "Customer hanya tanya tanya",
  "Desain belum selesai",
  "Produk tidak ada di perusahaan",
];

export const NO_ORDER_REASON_LAINNYA = "Lainnya";

/** Jenis Komplain when Komplain = Ya. */
export const COMPLAINT_CATEGORY_OPTIONS = [
  "CUP CACAT",
  "SABLON CACAT",
  "SALAH DESIGN",
  "SALAH CUP",
  "KESALAHAN DEALING HARGA",
  "Lainnya",
];

export const SALES_SOURCE_EXISTING = "existing";
export const SALES_SOURCE_MANUAL = "manual";
export const SALES_SOURCE_LEGACY = "legacy";
export const SALES_MANUAL_CHOICE = "__manual__";
export const MANUAL_SALES_MAX_LEN = 80;
export const CRM_OTHER_OPTION_ID = "__other__";

export function canUseManualSales(form) {
  return form?.status_order === "TIDAK ORDER" && !String(form?.order_id || "").trim();
}

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
  // List stays compact: prefer category, fall back to description.
  const cat = String(row.complaint_category || "").trim();
  if (cat) return truncateText(cat, 40);
  return truncateText(row.complaint_description || "Ya", 40);
}

export function issueLabel(row) {
  if (!row) return "—";
  if (!row.has_issue) return "Tidak";
  return truncateText(row.issue_description || "Ya", 40);
}

export function statusOrderBadgeClass(status) {
  if (status === "ORDER") return "is-order";
  if (status === "TIDAK ORDER") return "is-no-order";
  if (status === "PENGAJUAN DITOLAK") return "is-rejected";
  if (status === "PENGAJUAN PO") return "is-pengajuan";
  if (status === "FOLLOW UP") return "is-follow-up";
  return "";
}

/**
 * Map stored no_order_reason → UI select value.
 * Presets stay as-is; free-text / legacy → Lainnya.
 */
export function noOrderReasonChoice(reason) {
  const text = String(reason || "").trim();
  if (!text) return "";
  if (NO_ORDER_REASON_PRESETS.includes(text)) return text;
  return NO_ORDER_REASON_LAINNYA;
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
    sales_master_id: "",
    sales_label: "",
    sales_user_id: "",
    sales_source: "",
    sales_choice: "",
    sales_locked_from_po: false,
    sales_note: "",
    status_order: "ORDER",
    no_order_reason: "",
    no_order_reason_id: "",
    no_order_reason_choice: "",
    has_complaint: false,
    complaint_category: "",
    complaint_category_id: "",
    complaint_description: "",
    legacy_complaint: false,
    has_issue: false,
    issue_category_id: "",
    issue_description: "",
    notes: "",
  };
}

export function formFromCrmDailyReport(report) {
  const reason = report?.no_order_reason || "";
  const salesUserId = report?.sales_user_id || "";
  const salesLabel = report?.sales_label || "";
  let salesSource = report?.sales_source || "";
  if (!salesSource) {
    salesSource = salesUserId
      ? SALES_SOURCE_EXISTING
      : (salesLabel ? SALES_SOURCE_LEGACY : "");
  }
  const salesMasterId = report?.sales_master_id || "";
  // Existing reports with Master but no Eternal account stay verified (not manual/legacy).
  if (!salesSource && salesMasterId && salesLabel && !salesUserId) {
    salesSource = SALES_SOURCE_EXISTING;
  }
  let salesChoice = "";
  if (salesSource === SALES_SOURCE_MANUAL) salesChoice = SALES_MANUAL_CHOICE;
  else if (salesUserId) salesChoice = salesUserId;
  else if (salesSource === SALES_SOURCE_EXISTING && salesMasterId) salesChoice = salesMasterId;
  // Legacy free-text keeps empty choice so UI shows the legacy hint without
  // pretending the value is a verified dropdown selection or new manual entry.

  return {
    report_date: report?.report_date || "",
    status_customer: report?.status_customer || "New",
    customer_id: report?.customer_id || "",
    outlet_name: report?.outlet_name || "",
    whatsapp: report?.whatsapp || "",
    order_id: report?.order_id || "",
    po_number: report?.po_number || "",
    sales_master_id: salesMasterId,
    sales_label: salesLabel,
    sales_user_id: salesUserId,
    sales_source: salesSource,
    sales_choice: salesChoice,
    sales_locked_from_po: false,
    sales_note: report?.sales_note || "",
    status_order: report?.status_order || "ORDER",
    no_order_reason: reason,
    no_order_reason_id: report?.no_order_reason_id || "",
    no_order_reason_choice: noOrderReasonChoice(reason),
    has_complaint: Boolean(report?.has_complaint),
    complaint_category: report?.complaint_category || "",
    complaint_category_id: report?.complaint_category_id || "",
    complaint_description: report?.complaint_description || "",
    // Legacy rows may lack category — still openable/editable without forcing one.
    legacy_complaint: Boolean(report?.has_complaint) && !String(report?.complaint_category || "").trim(),
    has_issue: Boolean(report?.has_issue),
    issue_category_id: report?.issue_category_id || "",
    issue_description: report?.issue_description || "",
    notes: report?.notes || "",
  };
}

export function validateCrmDailyReportForm(form) {
  const errors = {};
  if (!String(form.report_date || "").trim()) errors.report_date = "Tanggal wajib";
  if (!["New", "Repeat"].includes(form.status_customer)) errors.status_customer = "Status Customer wajib";
  if (!STATUS_ORDER_OPTIONS.includes(form.status_order)) errors.status_order = "Status Order wajib";

  if (form.status_customer === "New") {
    if (!String(form.whatsapp || "").trim()) errors.whatsapp = "Nomor WhatsApp wajib untuk New";
  } else if (!String(form.customer_id || "").trim()) {
    errors.customer_id = "Pilih customer dari master untuk Repeat";
  }

  const salesSource = String(form.sales_source || "").trim();
  const salesUserId = String(form.sales_user_id || "").trim();
  const salesLabel = String(form.sales_label || "").trim();
  const salesMasterId = String(form.sales_master_id || "").trim();
  const hasPo = Boolean(String(form.order_id || "").trim());
  if (salesSource === SALES_SOURCE_MANUAL || form.sales_choice === SALES_MANUAL_CHOICE) {
    if (!canUseManualSales(form)) {
      errors.sales_label = "Input Manual hanya untuk TIDAK ORDER tanpa PO";
    } else if (!salesLabel) {
      errors.sales_label = "Nama Sales Manual wajib diisi";
    } else if (salesLabel.length > MANUAL_SALES_MAX_LEN) {
      errors.sales_label = `Nama Sales Manual maksimal ${MANUAL_SALES_MAX_LEN} karakter`;
    } else if (salesUserId) {
      errors.sales_label = "Sales manual tidak boleh memakai ID terverifikasi";
    } else if (salesMasterId) {
      errors.sales_label = "Sales manual tidak boleh memakai Master Sales terverifikasi";
    }
  } else if (salesSource === SALES_SOURCE_LEGACY && salesLabel && !salesUserId) {
    // Keep legacy snapshot editable without forcing remapping on open.
  } else if (salesMasterId && salesLabel) {
    // Verified Master Sales — Eternal account optional.
  } else if (!salesUserId) {
    errors.sales_label = hasPo && form.sales_note
      ? "Pilih Sales dari daftar (PO tanpa Sales terverifikasi)"
      : "Sales / Siapa yang Menjual wajib dipilih";
  }

  if (form.status_order === "TIDAK ORDER") {
    const choice = String(form.no_order_reason_choice || "").trim();
    const reason = String(form.no_order_reason || "").trim();
    const reasonId = String(form.no_order_reason_id || "").trim();
    if (!choice && !reasonId) {
      errors.no_order_reason = "Keterangan Tidak Order wajib";
    } else if ((choice === NO_ORDER_REASON_LAINNYA || reasonId === CRM_OTHER_OPTION_ID) && !reason) {
      errors.no_order_reason = "Isi keterangan untuk Lainnya";
    } else if (
      choice
      && choice !== NO_ORDER_REASON_LAINNYA
      && !NO_ORDER_REASON_PRESETS.includes(choice)
      && !reasonId
    ) {
      errors.no_order_reason = "Keterangan Tidak Order tidak valid";
    }
  }
  if (form.has_complaint) {
    const category = String(form.complaint_category || "").trim();
    const categoryId = String(form.complaint_category_id || "").trim();
    if (!category && !categoryId) {
      if (!form.legacy_complaint) {
        errors.complaint_category = "Jenis Komplain wajib";
      }
    } else if (category && !COMPLAINT_CATEGORY_OPTIONS.includes(category) && !categoryId) {
      errors.complaint_category = "Jenis Komplain tidak valid";
    }
    if (!String(form.complaint_description || "").trim()) {
      errors.complaint_description = "Deskripsi Komplain wajib";
    }
  }
  if (form.has_issue && !String(form.issue_description || "").trim()) {
    errors.issue_description = "Deskripsi Kendala wajib";
  }
  return errors;
}

export function toCrmDailyReportPayload(form, clientSubmitId) {
  let noOrderReason = "";
  if (form.status_order === "TIDAK ORDER") {
    const choice = String(form.no_order_reason_choice || "").trim();
    if (choice === NO_ORDER_REASON_LAINNYA) {
      noOrderReason = String(form.no_order_reason || "").trim();
    } else {
      noOrderReason = choice;
    }
  }
  const isManual = form.sales_source === SALES_SOURCE_MANUAL
    || form.sales_choice === SALES_MANUAL_CHOICE;
  const salesMasterId = isManual ? null : (String(form.sales_master_id || "").trim() || null);
  const salesUserId = isManual ? null : (String(form.sales_user_id || "").trim() || null);
  const payload = {
    report_date: String(form.report_date || "").trim(),
    status_customer: form.status_customer,
    customer_id: form.status_customer === "Repeat" ? String(form.customer_id || "").trim() : "",
    outlet_name: form.status_customer === "New" ? String(form.outlet_name || "").trim() : "",
    whatsapp: form.status_customer === "New" ? String(form.whatsapp || "").trim() : "",
    order_id: String(form.order_id || "").trim() || null,
    po_number: String(form.po_number || "").trim(),
    sales_master_id: salesMasterId,
    sales_label: String(form.sales_label || "").trim(),
    sales_user_id: salesUserId,
    sales_source: isManual
      ? SALES_SOURCE_MANUAL
      : (form.sales_source === SALES_SOURCE_LEGACY && !salesUserId && !salesMasterId
        ? SALES_SOURCE_LEGACY
        : SALES_SOURCE_EXISTING),
    status_order: form.status_order,
    no_order_reason_id: form.status_order === "TIDAK ORDER"
      ? (String(form.no_order_reason_id || "").trim() || null)
      : null,
    no_order_reason: noOrderReason,
    has_complaint: Boolean(form.has_complaint),
    complaint_category_id: form.has_complaint
      ? (String(form.complaint_category_id || "").trim() || null)
      : null,
    complaint_category: form.has_complaint ? String(form.complaint_category || "").trim() : "",
    complaint_description: form.has_complaint ? String(form.complaint_description || "").trim() : "",
    has_issue: Boolean(form.has_issue),
    issue_category_id: form.has_issue
      ? (String(form.issue_category_id || "").trim() || null)
      : null,
    issue_description: form.has_issue ? String(form.issue_description || "").trim() : "",
    notes: String(form.notes || "").trim(),
  };
  if (clientSubmitId) payload.client_submit_id = clientSubmitId;
  return payload;
}

/** sessionStorage draft — client-only; never POSTed automatically. */
export const CRM_DAILY_DRAFT_VERSION = 1;
export const CRM_DAILY_DRAFT_PREFIX = `cdr-draft:v${CRM_DAILY_DRAFT_VERSION}:`;
export const CRM_DAILY_DRAFT_DEBOUNCE_MS = 300;

const DRAFT_FORM_KEYS = Object.keys(emptyCrmDailyReportForm());

export function crmDailyDraftUserKey(user) {
  const id = String(user?.id || user?.email || user?.sub || "").trim();
  return id;
}

export function crmDailyDraftStorageKey(user, mode, recordId = "new") {
  const uid = crmDailyDraftUserKey(user);
  if (!uid) return "";
  if (mode === "edit") {
    const rid = String(recordId || "").trim();
    if (!rid || rid === "baru") return "";
    return `${CRM_DAILY_DRAFT_PREFIX}${uid}:edit:${rid}`;
  }
  return `${CRM_DAILY_DRAFT_PREFIX}${uid}:create:new`;
}

function draftSessionStore(storage) {
  if (storage) return storage;
  try {
    return typeof sessionStorage !== "undefined" ? sessionStorage : null;
  } catch {
    return null;
  }
}

function sanitizeDraftCustomer(raw) {
  if (!raw || typeof raw !== "object") return null;
  const id = String(raw.id || "").trim();
  if (!id) return null;
  return {
    id,
    name: String(raw.name || "").slice(0, 200),
    phone: String(raw.phone || "").slice(0, 40),
  };
}

function sanitizeDraftOrder(raw) {
  if (!raw || typeof raw !== "object") return null;
  const id = String(raw.id || "").trim();
  const po = String(raw.po_number || "").trim();
  if (!id && !po) return null;
  return {
    id,
    po_number: po.slice(0, 80),
    customer: String(raw.customer || "").slice(0, 200),
    sales_verified: Boolean(raw.sales_verified),
    sales_user_id: String(raw.sales_user_id || "").slice(0, 80),
    sales_label_resolved: String(raw.sales_label_resolved || raw.sales || "").slice(0, 120),
    sales: String(raw.sales || "").slice(0, 120),
  };
}

function sanitizeDraftForm(raw) {
  const base = emptyCrmDailyReportForm();
  if (!raw || typeof raw !== "object") return base;
  const next = { ...base };
  for (const key of DRAFT_FORM_KEYS) {
    if (!(key in raw)) continue;
    const value = raw[key];
    if (typeof base[key] === "boolean") next[key] = Boolean(value);
    else next[key] = value == null ? "" : String(value).slice(0, key === "notes" || key.includes("description") || key === "no_order_reason" ? 2000 : 200);
  }
  if (!["New", "Repeat"].includes(next.status_customer)) next.status_customer = "New";
  if (!STATUS_ORDER_OPTIONS.includes(next.status_order)) next.status_order = "ORDER";
  if (next.sales_source === SALES_SOURCE_MANUAL || next.sales_choice === SALES_MANUAL_CHOICE) {
    if (!canUseManualSales(next)) {
      next.sales_source = "";
      next.sales_choice = "";
      next.sales_user_id = "";
      next.sales_label = "";
      next.sales_master_id = "";
      next.sales_locked_from_po = false;
      next.sales_note = "";
    } else {
      next.sales_source = SALES_SOURCE_MANUAL;
      next.sales_choice = SALES_MANUAL_CHOICE;
      next.sales_user_id = "";
      next.sales_master_id = "";
      next.sales_locked_from_po = false;
    }
  } else if (
    next.sales_master_id
    && next.sales_label
    && !next.sales_user_id
    && next.sales_source !== SALES_SOURCE_LEGACY
  ) {
    next.sales_source = SALES_SOURCE_EXISTING;
    if (!next.sales_choice) next.sales_choice = next.sales_master_id;
  }
  if (next.status_order !== "TIDAK ORDER") {
    next.no_order_reason = "";
    next.no_order_reason_choice = "";
  } else if (next.no_order_reason_choice && next.no_order_reason_choice !== NO_ORDER_REASON_LAINNYA
    && !NO_ORDER_REASON_PRESETS.includes(next.no_order_reason_choice)) {
    next.no_order_reason_choice = noOrderReasonChoice(next.no_order_reason);
  }
  if (!next.has_complaint) {
    next.complaint_category = "";
    next.complaint_description = "";
    next.legacy_complaint = false;
  }
  if (!next.has_issue) next.issue_description = "";
  return next;
}

export function buildCrmDailyDraftSnapshot(form, ui = {}) {
  return {
    v: CRM_DAILY_DRAFT_VERSION,
    savedAt: Date.now(),
    form: sanitizeDraftForm(form),
    ui: {
      customerQ: String(ui.customerQ || "").slice(0, 120),
      poQ: String(ui.poQ || "").slice(0, 120),
      salesQ: String(ui.salesQ || "").slice(0, 120),
      selectedCustomer: sanitizeDraftCustomer(ui.selectedCustomer),
      selectedOrder: sanitizeDraftOrder(ui.selectedOrder),
    },
  };
}

export function sanitizeCrmDailyDraft(raw) {
  if (!raw || typeof raw !== "object") return null;
  if (Number(raw.v) !== CRM_DAILY_DRAFT_VERSION) return null;
  const form = sanitizeDraftForm(raw.form);
  const uiRaw = raw.ui && typeof raw.ui === "object" ? raw.ui : {};
  return {
    v: CRM_DAILY_DRAFT_VERSION,
    savedAt: Number(raw.savedAt) || 0,
    form,
    ui: {
      customerQ: String(uiRaw.customerQ || "").slice(0, 120),
      poQ: String(uiRaw.poQ || "").slice(0, 120),
      salesQ: String(uiRaw.salesQ || "").slice(0, 120),
      selectedCustomer: sanitizeDraftCustomer(uiRaw.selectedCustomer),
      selectedOrder: sanitizeDraftOrder(uiRaw.selectedOrder),
    },
  };
}

export function readCrmDailyDraft(key, storage) {
  const store = draftSessionStore(storage);
  if (!key || !store) return null;
  try {
    const raw = store.getItem(key);
    if (!raw) return null;
    return sanitizeCrmDailyDraft(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function writeCrmDailyDraft(key, snapshot, storage) {
  const store = draftSessionStore(storage);
  if (!key || !store) return false;
  const safe = sanitizeCrmDailyDraft(snapshot) || buildCrmDailyDraftSnapshot(snapshot?.form, snapshot?.ui);
  try {
    store.setItem(key, JSON.stringify(safe));
    return true;
  } catch {
    return false;
  }
}

export function clearCrmDailyDraft(key, storage) {
  const store = draftSessionStore(storage);
  if (!key || !store) return;
  try {
    store.removeItem(key);
  } catch {
    /* ignore quota / private mode */
  }
}

export function clearCrmDailyDraftsForUser(user, storage) {
  const store = draftSessionStore(storage);
  const uid = crmDailyDraftUserKey(user);
  if (!store || !uid) return;
  const prefix = `${CRM_DAILY_DRAFT_PREFIX}${uid}:`;
  try {
    const keys = [];
    for (let i = 0; i < store.length; i += 1) {
      const k = store.key(i);
      if (k && k.startsWith(prefix)) keys.push(k);
    }
    keys.forEach((k) => {
      try { store.removeItem(k); } catch { /* ignore */ }
    });
  } catch {
    /* ignore */
  }
}
