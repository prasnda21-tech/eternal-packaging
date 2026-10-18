export const CUSTOMER_MASTER = "master_crm";
export const CUSTOMER_NEW = "new_prospect";
export const PRODUCT_MASTER = "product_master";
export const PRODUCT_REQUESTED = "requested_product";
export const NOTE_MAX_LEN = 2000;
export const FEEDBACK_MAX_LEN = 2000;

export const STATUS_DRAFT = "draft";
export const STATUS_SUBMITTED = "submitted";
export const STATUS_REVIEWED = "reviewed";
export const STATUS_PENDING = "pending";
export const STATUS_PURCHASED = "purchased";
export const STATUS_REJECTED = "rejected";
export const STATUS_COMPLETED = "completed";

export const STATUS_OPTIONS = [
  { value: STATUS_SUBMITTED, label: "Terkirim" },
  { value: STATUS_REVIEWED, label: "Ditinjau" },
  { value: STATUS_PENDING, label: "Pending" },
  { value: STATUS_PURCHASED, label: "Terbeli" },
  { value: STATUS_REJECTED, label: "Ditolak" },
  { value: STATUS_COMPLETED, label: "Selesai" },
];

export const NOTE_REQUIRED_STATUSES = new Set([STATUS_PENDING, STATUS_REJECTED]);

export function statusLabel(status) {
  const found = STATUS_OPTIONS.find((row) => row.value === status);
  if (found) return found.label;
  if (status === STATUS_DRAFT) return "Draft";
  return status || "—";
}

export function statusBadgeClass(status) {
  switch (status) {
    case STATUS_DRAFT:
      return "prq-status-draft";
    case STATUS_SUBMITTED:
      return "prq-status-submitted";
    case STATUS_REVIEWED:
      return "prq-status-reviewed";
    case STATUS_PENDING:
      return "prq-status-pending";
    case STATUS_PURCHASED:
      return "prq-status-purchased";
    case STATUS_REJECTED:
      return "prq-status-rejected";
    case STATUS_COMPLETED:
      return "prq-status-completed";
    default:
      return "prq-status-draft";
  }
}

/** True when the request is past draft and should render as read-only detail. */
export function isPostSubmitStatus(status) {
  return Boolean(status) && status !== STATUS_DRAFT;
}

export const EMPTY_FORM = {
  customer_source: "",
  customer_id: null,
  customer_name: "",
  pic: "",
  whatsapp: "",
  city: "",
  product_source: "",
  product_id: null,
  product_code: "",
  product_name: "",
  category: "",
  size: "",
  material: "",
  model: "",
  potential_qty: "",
  frequency: "",
  target_price: "",
  needed_date: "",
  request_note: "",
  request_channel: "",
  doc_version: null,
};

export function customerSourceLabel(source) {
  if (source === CUSTOMER_MASTER) return "Master CRM";
  if (source === CUSTOMER_NEW) return "Input CRM";
  return "—";
}

export function productSourceLabel(source) {
  if (source === PRODUCT_MASTER) return "Master Produk";
  if (source === PRODUCT_REQUESTED) return "Requested Product";
  return "—";
}

export function parsePositiveInt(value) {
  if (value === "" || value == null) return null;
  const n = Number(value);
  if (!Number.isInteger(n) || n <= 0) return null;
  return n;
}

export function parseNonNegativeInt(value) {
  if (value === "" || value == null) return null;
  const n = Number(value);
  if (!Number.isInteger(n) || n < 0) return null;
  return n;
}

export function isIsoDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return false;
  const d = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value;
}

export function requiredChecks(form) {
  const qty = parsePositiveInt(form.potential_qty);
  const price = parseNonNegativeInt(form.target_price);
  const note = String(form.request_note || "");
  return {
    customerSource: Boolean(form.customer_source),
    customerIdentity: form.customer_source === CUSTOMER_MASTER
      ? Boolean(form.customer_id)
      : form.customer_source === CUSTOMER_NEW
        ? Boolean(String(form.customer_name || "").trim())
        : false,
    productSource: Boolean(form.product_source),
    productIdentity: form.product_source === PRODUCT_MASTER
      ? Boolean(form.product_id)
      : form.product_source === PRODUCT_REQUESTED
        ? Boolean(String(form.product_name || "").trim())
        : false,
    qty: qty != null,
    frequency: Boolean(String(form.frequency || "").trim()),
    price: price != null,
    neededDate: isIsoDate(form.needed_date),
    note: note.length <= NOTE_MAX_LEN,
  };
}

export function requiredComplete(form) {
  const checks = requiredChecks(form);
  return Object.values(checks).every(Boolean);
}

export function completenessCount(form) {
  const checks = requiredChecks(form);
  const values = Object.values(checks);
  return { done: values.filter(Boolean).length, total: values.length, checks };
}

export function progressSteps(form) {
  const checks = requiredChecks(form);
  return [
    { key: "customer", label: "Customer", done: checks.customerSource && checks.customerIdentity },
    { key: "product", label: "Produk", done: checks.productSource && checks.productIdentity },
    { key: "potential", label: "Potensi", done: checks.qty && checks.frequency && checks.price && checks.neededDate && checks.note },
    { key: "submit", label: "Kirim", done: requiredComplete(form) },
  ];
}

export function toPayload(form) {
  const payload = {
    customer_source: form.customer_source || null,
    product_source: form.product_source || null,
    potential_qty: form.potential_qty === "" ? null : Number(form.potential_qty),
    frequency: String(form.frequency || "").trim(),
    target_price: form.target_price === "" ? null : Number(form.target_price),
    needed_date: form.needed_date || null,
    request_note: String(form.request_note || "").trim(),
    request_channel: String(form.request_channel || "").trim(),
  };
  if (Number.isInteger(form.doc_version) && form.doc_version >= 0) {
    payload.doc_version = form.doc_version;
  }
  if (form.customer_source === CUSTOMER_MASTER) {
    payload.customer_id = form.customer_id || null;
    payload.customer_name = "";
    payload.pic = "";
    payload.whatsapp = "";
    payload.city = "";
  } else if (form.customer_source === CUSTOMER_NEW) {
    payload.customer_id = null;
    payload.customer_name = String(form.customer_name || "").trim();
    payload.pic = String(form.pic || "").trim();
    payload.whatsapp = String(form.whatsapp || "").trim();
    payload.city = String(form.city || "").trim();
  }
  if (form.product_source === PRODUCT_MASTER) {
    payload.product_id = form.product_id || null;
    payload.product_name = "";
    payload.category = "";
    payload.size = "";
    payload.material = "";
    payload.model = "";
  } else if (form.product_source === PRODUCT_REQUESTED) {
    payload.product_id = null;
    payload.product_name = String(form.product_name || "").trim();
    payload.category = String(form.category || "").trim();
    payload.size = String(form.size || "").trim();
    payload.material = String(form.material || "").trim();
    payload.model = String(form.model || "").trim();
  }
  return payload;
}

export function formFromRequest(request) {
  if (!request || request.receipt_only) return { ...EMPTY_FORM };
  const customer = request.customer_snapshot || {};
  const product = request.product_snapshot || {};
  return {
    ...EMPTY_FORM,
    customer_source: request.customer_source || "",
    customer_id: request.customer_id || null,
    customer_name: customer.name || "",
    pic: customer.pic || "",
    whatsapp: customer.whatsapp || customer.phone || "",
    city: customer.city || "",
    product_source: request.product_source || "",
    product_id: request.product_id || null,
    product_code: product.item_code || "",
    product_name: product.item_name || product.name || "",
    category: product.category || "",
    size: product.size || "",
    material: product.material || "",
    model: product.model || "",
    potential_qty: request.potential_qty ?? "",
    frequency: request.frequency || "",
    target_price: request.target_price ?? "",
    needed_date: request.needed_date || "",
    request_note: request.request_note || "",
    request_channel: request.request_channel || "",
    doc_version: Number.isInteger(request.doc_version) ? request.doc_version : null,
  };
}

export function clearCustomerMode(form, source) {
  return {
    ...form,
    customer_source: source,
    customer_id: null,
    customer_name: "",
    pic: "",
    whatsapp: "",
    city: "",
  };
}

export function selectMasterCustomer(form, customer) {
  return {
    ...form,
    customer_source: CUSTOMER_MASTER,
    customer_id: customer?.id || null,
    customer_name: customer?.name || "",
    pic: customer?.pic || "",
    whatsapp: customer?.phone || customer?.whatsapp || "",
    city: customer?.city || "",
  };
}

export function clearProductMode(form, source) {
  return {
    ...form,
    product_source: source,
    product_id: null,
    product_code: "",
    product_name: "",
    category: "",
    size: "",
    material: "",
    model: "",
  };
}

export function selectMasterProduct(form, item) {
  return {
    ...form,
    product_source: PRODUCT_MASTER,
    product_id: item?.id || item?.accurate_item_id || null,
    product_code: item?.item_code || "",
    product_name: item?.item_name || item?.name || "",
    category: "",
    size: "",
    material: "",
    model: "",
  };
}

function comparableField(key, value) {
  if (value == null) return "";
  if (key === "potential_qty" || key === "target_price" || key === "doc_version") return String(value);
  return String(value);
}

/** True when two form snapshots show the same saved input. */
export function productRequestFormsEqual(left, right) {
  return Object.keys(EMPTY_FORM).every(
    (key) => comparableField(key, left?.[key]) === comparableField(key, right?.[key]),
  );
}

export function formatError(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  return err?.message || "Terjadi kesalahan.";
}
