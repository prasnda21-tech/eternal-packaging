/**
 * UI-only helpers for Buat Pesanan Penjualan.
 * Submit validation and the create payload stay in PesananPenjualan.jsx.
 */

export const SALES_ORDER_MAIN_FIELD_IDS = [
  "po_number",
  "order_date",
  "customer",
  "customer_phone",
  "address",
];

export const SALES_ORDER_DRAWER_FIELD_IDS = [
  "outlet",
  "sales",
  "created_by_user",
  "recipient",
  "delivery_zone",
  "estimated_delivery",
  "deadline",
  "payment_terms",
  "expedition",
  "notes",
];

export const SALES_ORDER_DRAWER_INPUTS = [
  { field: "outlet", label: "Outlet / Cabang", testId: "input-outlet", placeholder: "mis. Cabang Tebet / Pusat", list: "outlet-suggestions" },
  { field: "sales", label: "Tenaga Penjual (Sales)", testId: "input-sales", placeholder: "mis. Vira", list: "sales-suggestions" },
  { field: "created_by_user", label: "User / Pembuat PO", testId: "input-created-by-user", placeholder: "Nama user pembuat" },
  { field: "recipient", label: "Penerima (PIC)", testId: "input-recipient", placeholder: "mis. Ibu Rina" },
  { field: "delivery_zone", label: "Zona Kirim", testId: "input-delivery-zone", placeholder: "mis. Jakarta Selatan" },
  { field: "estimated_delivery", label: "Estimasi Kirim", testId: "input-estimated-delivery", type: "date" },
  { field: "deadline", label: "Tanggal Pengiriman", testId: "input-tanggal-pengiriman", type: "date" },
  { field: "payment_terms", label: "Termin Pembayaran", testId: "input-payment-terms", placeholder: "mis. Cash, Net 14, Net 30, COD" },
  { field: "expedition", label: "Ekspedisi / Pengiriman", testId: "input-expedition", placeholder: "mis. Internal fleet" },
  { field: "notes", label: "Catatan Pesanan", testId: "input-notes", placeholder: "Catatan khusus pengerjaan, packing, atau instruksi" },
];

export function salesOrderFieldRegion(fieldId) {
  if (SALES_ORDER_MAIN_FIELD_IDS.includes(fieldId)) return "main";
  if (SALES_ORDER_DRAWER_FIELD_IDS.includes(fieldId)) return "drawer";
  if (fieldId === "line_items") return "lines";
  return null;
}

export function salesOrderIssuePlacement(message) {
  const msg = String(message || "");
  if (!msg) return null;
  if (/Customer wajib diisi/i.test(msg)) return { field: "customer", region: "main" };
  if (/No\. HP \/ WhatsApp/i.test(msg)) return { field: "customer_phone", region: "main" };
  if (/Alamat lengkap pengiriman/i.test(msg)) return { field: "address", region: "main" };
  if (/Baris #|Minimal harus ada 1 line item|Quantity harus/i.test(msg)) {
    return { field: "line_items", region: "lines" };
  }
  const drawerRules = [
    ["outlet", /\bOutlet\b/i],
    ["sales", /Tenaga Penjual/i],
    ["created_by_user", /Pembuat PO/i],
    ["recipient", /\bPenerima\b|\bPIC\b/i],
    ["delivery_zone", /Zona Kirim/i],
    ["estimated_delivery", /Estimasi Kirim/i],
    ["deadline", /Tanggal Pengiriman/i],
    ["payment_terms", /Termin Pembayaran|\bTermin\b/i],
    ["expedition", /Ekspedisi/i],
    ["notes", /Catatan Pesanan/i],
  ];
  for (let i = 0; i < drawerRules.length; i += 1) {
    const [field, pattern] = drawerRules[i];
    if (pattern.test(msg)) return { field, region: "drawer" };
  }
  return null;
}

/** Manual keeps the typed name. Master drops the previous name and master ids. */
export function lineAfterEntryModeChange(line, nextMode) {
  if (nextMode === "manual") {
    return {
      ...line,
      accurate_item_id: null,
      master_item_id: null,
    };
  }
  if (nextMode === "master") {
    return {
      ...line,
      name: "",
      item_code: "",
      accurate_item_id: null,
      master_item_id: null,
    };
  }
  return { ...line };
}

export function lineEntryModeAfterRepeat(line) {
  if (line?.accurate_item_id || line?.master_item_id) return "master";
  return "manual";
}
