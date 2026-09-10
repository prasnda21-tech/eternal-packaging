/**
 * Explicit Repeat Order mapping: copy-safe source DTO → create-form patch.
 * Never blind-spreads the source object into form state.
 */

export const REPEAT_NO_PREVIOUS_MESSAGE =
  "Tidak ada pesanan sebelumnya untuk customer ini.";

const HEADER_FIELDS = [
  "customer_phone",
  "outlet",
  "recipient",
  "address",
  "delivery_zone",
  "payment_terms",
  "sales",
  "expedition",
  "estimated_delivery",
  "deadline",
  "notes",
  "order_date",
];

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

function mapDesignFile(raw) {
  if (!raw || typeof raw !== "object") return null;
  const dataUrl = typeof raw.data_url === "string" ? raw.data_url : "";
  if (!dataUrl.startsWith("data:")) return null;
  return {
    name: String(raw.name || "design").trim() || "design",
    data_url: dataUrl,
    content_type: raw.content_type ? String(raw.content_type) : "",
  };
}

/**
 * Build a controlled form patch from a repeat-source DTO.
 * Selected customer (id + display name) remains authoritative.
 */
export function mapRepeatSourceToFormPatch(source, selectedCustomer) {
  const customerId = selectedCustomer?.customer_id || null;
  const customerName = String(selectedCustomer?.customer || "").trim();
  if (!source || typeof source !== "object") {
    return { formPatch: null, designFiles: [], sourcePoNumber: "" };
  }
  if (customerId && String(source.customer_id || "").trim() !== String(customerId).trim()) {
    return { formPatch: null, designFiles: [], sourcePoNumber: "" };
  }

  const lineItems = (source.line_items || [])
    .map(mapLineItem)
    .filter(Boolean);

  const formPatch = {
    customer: customerName || String(source.customer || "").trim(),
    customer_id: customerId || source.customer_id || null,
  };

  for (const key of HEADER_FIELDS) {
    if (source[key] == null) continue;
    const val = source[key];
    if (typeof val === "string") {
      // Keep ISO date prefix for date inputs when present.
      if (key === "order_date" || key === "estimated_delivery" || key === "deadline") {
        const iso = String(val).trim();
        formPatch[key] = iso.includes("T") ? iso.slice(0, 10) : iso.slice(0, 10);
      } else {
        formPatch[key] = val;
      }
    } else {
      formPatch[key] = val;
    }
  }

  if (lineItems.length) {
    formPatch.line_items = lineItems;
  }

  const designFiles = (source.design_files || []).map(mapDesignFile).filter(Boolean);

  return {
    formPatch,
    designFiles,
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
