/**
 * Development-only Repeat Order / Customer Live Search preview fixtures.
 * Activated only when NODE_ENV === "development" AND ?previewRepeatOrder=1.
 * Never persists or calls production write endpoints.
 */

export const PREVIEW_REPEAT_ORDER_PARAM = "previewRepeatOrder";
export const PREVIEW_REPEAT_ORDER_VALUE = "1";

export const PREVIEW_CUSTOMER_BAKSO_ROSSO_ID = "preview-cust-bakso-rosso";
export const PREVIEW_CUSTOMER_BAKSO_ROSSO_UTARA_ID = "preview-cust-bakso-rosso-utara";
export const PREVIEW_CUSTOMER_INACTIVE_ID = "preview-cust-bakso-rosso-inactive";
export const PREVIEW_CUSTOMER_NO_HISTORY_ID = "preview-cust-bakso-no-history";

/** Pure gate — safe to unit-test without DOM. */
export function isSalesOrderRepeatPreviewActive({
  searchParams,
  nodeEnv = typeof process !== "undefined" ? process.env.NODE_ENV : "",
} = {}) {
  if (String(nodeEnv) !== "development") return false;
  if (!searchParams) return false;
  const raw =
    typeof searchParams.get === "function"
      ? searchParams.get(PREVIEW_REPEAT_ORDER_PARAM)
      : searchParams[PREVIEW_REPEAT_ORDER_PARAM];
  return String(raw ?? "") === PREVIEW_REPEAT_ORDER_VALUE;
}

/**
 * Master customers for preview search.
 * Includes one inactive row that CustomerSearchSelect must exclude.
 */
export function getPreviewMasterCustomers() {
  return [
    {
      id: PREVIEW_CUSTOMER_BAKSO_ROSSO_ID,
      name: "BAKSO ROSSO",
      phone: "0812-9000-1111",
      address: "Jl. Master Bakso Rosso No. 1",
      pic: "PIC Master Rosso",
      email: "rosso@preview.local",
      city: "Jakarta",
      is_active: true,
    },
    {
      id: PREVIEW_CUSTOMER_BAKSO_ROSSO_UTARA_ID,
      name: "BAKSO ROSSO CABANG UTARA",
      phone: "0812-9000-2222",
      address: "Jl. Utara Preview 2",
      pic: "PIC Utara",
      email: "utara@preview.local",
      city: "Jakarta Utara",
      is_active: true,
    },
    {
      id: PREVIEW_CUSTOMER_INACTIVE_ID,
      name: "BAKSO ROSSO NONAKTIF",
      phone: "0812-9000-9999",
      address: "Jl. Nonaktif",
      pic: "PIC Nonaktif",
      email: "inactive@preview.local",
      city: "Depok",
      is_active: false,
    },
    {
      id: PREVIEW_CUSTOMER_NO_HISTORY_ID,
      name: "BAKSO TANPA HISTORI",
      phone: "0812-9000-3333",
      address: "Jl. Baru Preview",
      pic: "PIC Baru",
      email: "baru@preview.local",
      city: "Bekasi",
      is_active: true,
    },
  ];
}

function buildBaksoRossoRepeatSource() {
  return {
    source_po_number: "PO/PREVIEW/ROSSO/001",
    source_created_at: "2026-01-15T08:00:00+00:00",
    source_order_date: "2026-01-10T00:00:00+00:00",
    customer: "BAKSO ROSSO LEGACY NAME",
    customer_id: PREVIEW_CUSTOMER_BAKSO_ROSSO_ID,
    // Stale phone — must NOT overwrite master baseline on apply.
    customer_phone: "0812-OLD-PHONE",
    outlet: "Outlet Preview Pusat",
    recipient: "Penerima Order Lama",
    address: "Alamat kirim order lama Blok A",
    delivery_zone: "Zona Preview A",
    payment_terms: "Net 14 Preview",
    sales: "Sales Preview",
    expedition: "Internal fleet",
    notes: "Catatan repeat preview",
    // Old dates — mapper must not copy these into the new form.
    order_date: "2026-01-10",
    estimated_delivery: "2026-01-20",
    deadline: "2026-01-18",
    // Historical / workflow identity — must never reach form patch.
    id: "historical-order-id-rosso",
    current_stage: 12,
    packing_status: "Selesai",
    payment_status: "Lunas",
    delivery_status: "Terkirim",
    discount_amount: 99999,
    line_count: 3,
    design_file_count: 0,
    line_items: [
      {
        id: "hist-line-1",
        line_id: "legacy-line-1",
        item_code: "CUP-8",
        name: "Paper Cup 8oz",
        quantity: 1000,
        unit: "pcs",
        color: "Navy",
        production_process: "print",
        notes: "Baris A",
        accurate_item_id: "acc-preview-1",
        master_item_id: "mst-preview-1",
        operator_split_id: "split-old-1",
        progress_pct: 100,
      },
      {
        id: "hist-line-2",
        line_id: "legacy-line-2",
        item_code: "CUP-8",
        name: "Paper Cup 8oz",
        quantity: 500,
        unit: "pcs",
        color: "Merah",
        production_process: "print",
        notes: "Baris B same-name",
        accurate_item_id: "acc-preview-1",
        master_item_id: "mst-preview-2",
        operator_split_id: "split-old-2",
        progress_pct: 40,
      },
      {
        id: "hist-line-3",
        line_id: "legacy-line-3",
        item_code: "BOX-1",
        name: "Food Box",
        quantity: 200,
        unit: "pcs",
        color: "Kraft",
        production_process: "non_print",
        notes: "Baris C",
        accurate_item_id: "acc-preview-3",
        master_item_id: "mst-preview-3",
      },
    ],
    design_files: [],
  };
}

/**
 * Read-only repeat-source lookup for preview customers.
 * Shape mirrors GET /sales-orders/repeat-source: { found, source }.
 */
export function getPreviewRepeatSource(customerId) {
  const cid = String(customerId || "").trim();
  if (cid === PREVIEW_CUSTOMER_BAKSO_ROSSO_ID) {
    return { found: true, customer_id: cid, source: buildBaksoRossoRepeatSource() };
  }
  // Similar-name sibling + no-history + inactive: no valid prior PO.
  if (
    cid === PREVIEW_CUSTOMER_BAKSO_ROSSO_UTARA_ID ||
    cid === PREVIEW_CUSTOMER_NO_HISTORY_ID ||
    cid === PREVIEW_CUSTOMER_INACTIVE_ID
  ) {
    return { found: false, customer_id: cid, source: null };
  }
  return { found: false, customer_id: cid || null, source: null };
}
