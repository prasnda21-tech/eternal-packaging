/**
 * Pure Pra PO form helpers (UI + submit validation).
 * Keep PesananPenjualan create behavior untouched.
 */
import { getWibTodayString } from "./wibCalendarDate";
import { linePriceNumber } from "./salesOrderLinePrice";

export const STATUS_LABEL = {
  draft: "Draft",
  submitted: "Diajukan",
  returned: "Dikembalikan",
  resubmitted: "Diajukan Ulang",
  verifying: "Sedang diverifikasi",
  verified: "Disetujui",
};

export const PRA_PO_STATUS_FILTERS = [
  { id: "all", label: "Semua" },
  { id: "draft", label: "Draft" },
  { id: "queue", label: "Antrean" },
  { id: "returned", label: "Dikembalikan" },
  { id: "verified", label: "Disetujui" },
];

function newLineId() {
  return `ln-${Math.random().toString(36).slice(2, 10)}`;
}

export function emptyPraPoLine() {
  return {
    line_id: newLineId(),
    rowKey: newLineId(),
    name: "",
    item_code: "",
    quantity: 1,
    unit: "pcs",
    color: "",
    production_process: "print",
    notes: "",
    master_item_id: null,
    accurate_item_id: null,
    unit_price: "",
    item_type: "",
  };
}

export function emptyPraPoForm() {
  return {
    customer: "",
    customer_id: null,
    customer_phone: "",
    address: "",
    order_date: getWibTodayString(),
    notes: "",
    outlet: "",
    recipient: "",
    delivery_zone: "",
    expedition: "",
    estimated_delivery: "",
    deadline: "",
    payment_terms: "",
    sales: "",
    line_items: [emptyPraPoLine()],
  };
}

export function praPoDocToForm(doc) {
  const base = emptyPraPoForm();
  if (!doc) return base;
  return {
    ...base,
    customer: doc.customer || "",
    customer_id: doc.customer_id || null,
    customer_phone: doc.customer_phone || "",
    address: doc.address || "",
    order_date: doc.order_date || getWibTodayString(),
    notes: doc.notes || "",
    outlet: doc.outlet || "",
    recipient: doc.recipient || "",
    delivery_zone: doc.delivery_zone || "",
    expedition: doc.expedition || "",
    estimated_delivery: doc.estimated_delivery || "",
    deadline: doc.deadline || "",
    payment_terms: doc.payment_terms || "",
    sales: doc.sales || "",
    line_items: (doc.line_items || []).length
      ? doc.line_items.map((li) => ({
          ...emptyPraPoLine(),
          ...li,
          line_id: li.line_id || newLineId(),
          rowKey: li.line_id || newLineId(),
          unit_price: li.unit_price === null || li.unit_price === undefined ? "" : String(li.unit_price),
          production_process: li.production_process === "non_print" ? "non_print" : "print",
        }))
      : [emptyPraPoLine()],
  };
}

export function buildPraPoPayload(form) {
  return {
    customer: String(form.customer || "").trim(),
    customer_id: form.customer_id || null,
    customer_phone: String(form.customer_phone || "").trim() || null,
    address: String(form.address || "").trim() || null,
    order_date: String(form.order_date || "").trim() || null,
    notes: String(form.notes || "").trim() || "",
    outlet: String(form.outlet || "").trim() || null,
    recipient: String(form.recipient || "").trim() || null,
    delivery_zone: String(form.delivery_zone || "").trim() || null,
    expedition: String(form.expedition || "").trim() || null,
    estimated_delivery: String(form.estimated_delivery || "").trim() || null,
    deadline: String(form.deadline || "").trim() || null,
    payment_terms: String(form.payment_terms || "").trim() || null,
    sales: String(form.sales || "").trim() || null,
    line_items: (form.line_items || []).map((li) => {
      const price = linePriceNumber(li.unit_price);
      const mid = String(li.master_item_id || "").trim() || null;
      const aid = String(li.accurate_item_id || "").trim() || null;
      return {
        line_id: li.line_id || undefined,
        name: String(li.name || "").trim(),
        item_code: String(li.item_code || "").trim() || null,
        quantity: Number(li.quantity) || 0,
        unit: String(li.unit || "pcs").trim() || "pcs",
        color: String(li.color || "").trim() || null,
        production_process: li.production_process === "non_print" ? "non_print" : "print",
        notes: String(li.notes || "").trim() || null,
        master_item_id: mid,
        accurate_item_id: aid,
        unit_price: price,
        item_type: String(li.item_type || "").trim() || null,
        price_source: price === null ? null : "manual",
      };
    }),
  };
}

/** Draft save: only require a customer name so empty shells are not stored blindly. */
export function validatePraPoDraft(form) {
  if (!String(form?.customer || "").trim()) {
    return "Customer wajib diisi.";
  }
  if (!form?.line_items?.length) {
    return "Minimal harus ada 1 line item.";
  }
  return "";
}

/** Submit / resubmit — Owner business rules. */
export function validatePraPoSubmit(form) {
  const draftErr = validatePraPoDraft(form);
  if (draftErr) return draftErr;
  if (!form?.customer_id) {
    if (!String(form?.customer_phone || "").trim()) {
      return "Pelanggan baru terdeteksi: No. HP / WhatsApp pelanggan wajib diisi.";
    }
    if (!String(form?.address || "").trim()) {
      return "Pelanggan baru terdeteksi: Alamat lengkap pengiriman wajib diisi.";
    }
  }
  for (let i = 0; i < form.line_items.length; i += 1) {
    const li = form.line_items[i];
    if (!String(li?.name || "").trim()) {
      return `Baris #${i + 1}: Nama barang/jasa wajib diisi.`;
    }
    const mid = String(li?.master_item_id || "").trim();
    const aid = String(li?.accurate_item_id || "").trim();
    if (!mid && !aid) {
      return `Baris #${i + 1}: Master Produk wajib dipilih.`;
    }
    const q = Number(li.quantity);
    if (!Number.isFinite(q) || q <= 0) {
      return `Baris #${i + 1}: Quantity harus angka lebih besar dari 0.`;
    }
    if (!String(li?.unit || "").trim()) {
      return `Baris #${i + 1}: Satuan wajib diisi.`;
    }
    const price = linePriceNumber(li.unit_price);
    if (price === null || price <= 0) {
      return `Baris #${i + 1}: Harga jual wajib angka lebih besar dari 0.`;
    }
  }
  return "";
}

export function filterPraPoRows(rows, { q = "", status = "all" } = {}) {
  const query = String(q || "").trim().toLowerCase();
  return (rows || []).filter((row) => {
    const st = String(row?.status || "");
    if (status === "queue") {
      if (st !== "submitted" && st !== "resubmitted") return false;
    } else if (status !== "all" && st !== status) {
      return false;
    }
    if (!query) return true;
    const hay = [row.number, row.customer, row.sales_order_po_number, STATUS_LABEL[st] || st]
      .map((v) => String(v || "").toLowerCase())
      .join(" ");
    return hay.includes(query);
  });
}

export function praPoLineQtyLabel(row) {
  const lines = row?.line_items || [];
  const count = row?.item_count != null ? Number(row.item_count) : lines.length;
  const qty = row?.total_quantity != null
    ? Number(row.total_quantity)
    : lines.reduce((sum, li) => sum + (Number(li.quantity) || 0), 0);
  if (!count) return "—";
  return `${count} jenis · ${qty} qty`;
}
