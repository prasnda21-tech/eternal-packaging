/**
 * Explicit Repeat Order mapping: copy-safe source DTO → create-form patch.
 * Never blind-spreads the source object into form state.
 * Artwork is never copied.
 */

export const REPEAT_NO_PREVIOUS_MESSAGE =
  "Tidak ada pesanan sebelumnya untuk customer ini.";

export const REPEAT_ORDER_QUERY_PARAM = "repeat_order_id";

const HEADER_FIELDS = [
  "outlet",
  "recipient",
  "address",
  "delivery_zone",
  "payment_terms",
  "sales",
  "expedition",
  "notes",
];

export function normalizeCustomerName(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

export function resolveRepeatMasterCustomer(source, masters) {
  const list = Array.isArray(masters) ? masters : [];
  const sourceId = String(source?.customer_id || "").trim();
  if (sourceId) {
    const byId = list.find((m) => String(m?.id || "").trim() === sourceId);
    if (byId) return byId;
  }
  const norm = normalizeCustomerName(source?.customer);
  if (!norm) return null;
  const hits = list.filter((m) => normalizeCustomerName(m?.name) === norm);
  if (hits.length === 1) return hits[0];
  return null;
}

function mapLineItem(raw) {
  if (!raw || typeof raw !== "object") return null;
  const name = String(raw.name || "").trim();
  if (!name) return null;
  const quantity = Number(raw.quantity);
  if (!Number.isFinite(quantity) || quantity <= 0) return null;
  const proc = String(raw.production_process || "print").toLowerCase() === "non_print"
    ? "non_print"
    : "print";
  return {
    item_code: raw.item_code != null ? String(raw.item_code) : "",
    name,
    quantity,
    unit: String(raw.unit || "pcs").trim() || "pcs",
    color: raw.color != null ? String(raw.color) : "",
    production_process: proc,
    notes: raw.notes != null ? String(raw.notes) : "",
    accurate_item_id: raw.accurate_item_id || null,
    master_item_id: raw.master_item_id || null,
  };
}

/**
 * Build a controlled form patch from a repeat-source DTO.
 * Selected customer (id + display name) remains authoritative when provided.
 * Artwork is never mapped.
 */
export function mapRepeatSourceToFormPatch(source, selectedCustomer) {
  const customerId = selectedCustomer?.customer_id || null;
  const customerName = String(selectedCustomer?.customer || "").trim();
  if (!source || typeof source !== "object") {
    return { formPatch: null, designFiles: [], sourcePoNumber: "" };
  }
  const sourceCid = String(source.customer_id || "").trim();
  const selectedCid = String(customerId || "").trim();
  if (sourceCid && selectedCid && sourceCid !== selectedCid) {
    return { formPatch: null, designFiles: [], sourcePoNumber: "" };
  }

  const lineItems = (source.line_items || [])
    .map(mapLineItem)
    .filter(Boolean);

  const formPatch = {
    customer: customerName || String(source.customer || "").trim(),
    customer_id: selectedCid || sourceCid || null,
  };

  for (const key of HEADER_FIELDS) {
    if (source[key] == null) continue;
    formPatch[key] = source[key];
  }

  if (lineItems.length) {
    formPatch.line_items = lineItems;
  }

  return {
    formPatch,
    designFiles: [],
    sourcePoNumber: String(source.source_po_number || "").trim(),
  };
}

export function clearRepeatDerivedFormFields(prev, { keepCustomer = true } = {}) {
  const next = { ...prev };
  if (!keepCustomer) {
    next.customer = "";
    next.customer_id = null;
    next.customer_phone = "";
    next.customer_address = "";
    next.customer_pic = "";
    next.customer_email = "";
    next.customer_city = "";
  }
  next.outlet = "";
  next.recipient = "";
  next.address = "";
  next.delivery_zone = "";
  next.payment_terms = "";
  next.sales = "";
  next.expedition = "Internal fleet";
  next.notes = "";
  next.line_items = [
    {
      item_code: "",
      name: "",
      quantity: 1,
      unit: "pcs",
      color: "",
      production_process: "print",
      notes: "",
      accurate_item_id: null,
      master_item_id: null,
    },
  ];
  return next;
}
