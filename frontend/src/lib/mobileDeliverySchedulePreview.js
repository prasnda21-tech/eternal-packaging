/**
 * Development-only dummy rows for PWA Jadwal Pengiriman SJ-only preview.
 *
 * Activation requires BOTH:
 *   - process.env.NODE_ENV === "development"
 *   - query param previewPwaDeliverySj=1
 *
 * Never writes backend/DB/localStorage/sessionStorage/IndexedDB.
 * Never creates/edits/cancels real PO or Surat Jalan.
 */

export const PWA_DELIVERY_SJ_PREVIEW_PARAM = "previewPwaDeliverySj";
export const DUMMY_DATA_COUNT = 3;

export function isPwaDeliverySjPreviewEnabled(search) {
  if (process.env.NODE_ENV !== "development") return false;
  const raw = String(search || "");
  const normalized = raw.startsWith("?") ? raw.slice(1) : raw;
  const params = new URLSearchParams(normalized);
  return params.get(PWA_DELIVERY_SJ_PREVIEW_PARAM) === "1";
}

function line(shipmentId, sjNumber, lineId, lineIdx, name, qty) {
  return {
    shipment_id: shipmentId,
    sj_number: sjNumber,
    line_id: lineId,
    line_idx: lineIdx,
    name,
    qty,
  };
}

function sjRow({
  id,
  orderNumber,
  customer,
  shipmentId,
  sjNumber,
  date,
  status,
  lines,
  qtyTotal,
  qtyPacked = 0,
  qtyDelivered = 0,
  printTotal = 0,
  printed = 0,
  urgent = false,
  sjNotifications = null,
}) {
  const deliveredPct = qtyTotal > 0 ? Math.round((qtyDelivered / qtyTotal) * 100) : 0;
  return {
    id,
    order_number: orderNumber,
    customer_name: customer,
    status,
    status_label: status === "terlambat" ? "Terlambat" : status === "hari_ini" ? "Hari Ini" : "Terjadwal",
    effective_delivery_date: date,
    tanggal_pengiriman: date,
    shipment_id: shipmentId,
    shipment_index: shipmentId ? Number(String(shipmentId).slice(-1)) || 1 : null,
    sj_label: sjNumber,
    latest_sj_number: sjNumber,
    sj_numbers: sjNumber ? [sjNumber] : [],
    sj_item_lines: lines,
    qty_total: qtyTotal,
    qty_packed: qtyPacked,
    qty_delivered: qtyDelivered,
    qty_printed_operator: printed,
    print_qty_total: printTotal,
    percent_delivered: deliveredPct,
    percent_delivered_display: deliveredPct,
    percent_ready_to_ship: printTotal > 0 ? Math.round((printed / printTotal) * 100) : 0,
    urgent,
    optimization_status: "belum_dioptimasi",
    optimization_status_label: "Belum Dioptimasi",
    sj_notifications: sjNotifications || { helper: "idle", driver: "idle", head_production: "idle" },
    preview: true,
  };
}

/** Exactly 3 dummy scenarios. Dummy 3 expands to two canonical SJ schedule rows. */
export function getPwaDeliverySjPreviewScenarios() {
  return [
    {
      id: "dummy-1",
      title: "valid SJ with 3+ item lines",
      rows: [
        sjRow({
          id: "preview-d1",
          orderNumber: "PO-DUMMY-1",
          customer: "Preview CV Satu SJ",
          shipmentId: "sh-dummy-1",
          sjNumber: "SJ-DUMMY-001",
          date: "2026-09-15",
          status: "hari_ini",
          qtyTotal: 6000,
          qtyPacked: 4000,
          qtyDelivered: 0,
          printTotal: 6000,
          printed: 6000,
          sjNotifications: { helper: "sent", driver: "sent", head_production: "sent" },
          lines: [
            line("sh-dummy-1", "SJ-DUMMY-001", "lid-a", 0, "Cup 16oz Datar", 2000),
            line("sh-dummy-1", "SJ-DUMMY-001", "lid-b", 1, "Lid 16oz Datar", 2000),
            line("sh-dummy-1", "SJ-DUMMY-001", "lid-c", 2, "Cup 22oz Gelas", 2000),
          ],
        }),
      ],
    },
    {
      id: "dummy-2",
      title: "overdue PO with no valid SJ",
      rows: [
        {
          id: "preview-d2",
          order_number: "PO-DUMMY-2",
          customer_name: "Preview PT Overdue Tanpa SJ",
          status: "terlambat",
          status_label: "Terlambat",
          effective_delivery_date: "2026-09-01",
          tanggal_pengiriman: "2026-09-01",
          shipment_id: null,
          sj_label: "",
          latest_sj_number: "",
          sj_numbers: [],
          sj_item_lines: [],
          qty_total: 5000,
          qty_packed: 5000,
          qty_delivered: 0,
          qty_printed_operator: 5000,
          print_qty_total: 5000,
          percent_delivered: 0,
          current_stage: 11,
          urgent: true,
          preview: true,
        },
      ],
    },
    {
      id: "dummy-3",
      title: "one PO with two valid SJ",
      rows: [
        sjRow({
          id: "preview-d3",
          orderNumber: "PO-DUMMY-3",
          customer: "Preview UD Dua SJ",
          shipmentId: "sh-dummy-3a",
          sjNumber: "SJ-DUMMY-003A",
          date: "2026-09-14",
          status: "terlambat",
          qtyTotal: 3000,
          qtyPacked: 1500,
          printTotal: 3000,
          printed: 3000,
          sjNotifications: { helper: "sent", driver: "idle", head_production: "sent" },
          lines: [
            line("sh-dummy-3a", "SJ-DUMMY-003A", "lid-x", 0, "Cup 12oz PET", 1000),
            line("sh-dummy-3a", "SJ-DUMMY-003A", "lid-y", 1, "Lid 12oz PET", 2000),
          ],
        }),
        sjRow({
          id: "preview-d3",
          orderNumber: "PO-DUMMY-3",
          customer: "Preview UD Dua SJ",
          shipmentId: "sh-dummy-3b",
          sjNumber: "SJ-DUMMY-003B",
          date: "2026-09-16",
          status: "terjadwal",
          qtyTotal: 3000,
          qtyPacked: 800,
          printTotal: 3000,
          printed: 2500,
          sjNotifications: { helper: "idle", driver: "sent", head_production: "idle" },
          lines: [
            line("sh-dummy-3b", "SJ-DUMMY-003B", "lid-p", 0, "Cup 9oz Datar", 800),
            line("sh-dummy-3b", "SJ-DUMMY-003B", "lid-q", 1, "Lid 9oz Datar", 800),
            line("sh-dummy-3b", "SJ-DUMMY-003B", "lid-r", 2, "Sedotan Pack", 1400),
          ],
        }),
      ],
    },
  ];
}

export function getPwaDeliverySjPreviewRows() {
  return getPwaDeliverySjPreviewScenarios().flatMap((scenario) => scenario.rows);
}

export function getPwaDeliverySjPreviewProductionRows() {
  return [
    {
      id: "preview-d1",
      helper_required: 6000,
      helper_prepared: 6000,
      screen_qty: 20,
      print_qty_total: 6000,
      print_completed_qty: 6000,
      current_stage: 11,
      operators: [
        { split_id: "s1", line_id: "lid-a", line_idx: 0, line_printable: true, qty_completed: 2000 },
        { split_id: "s2", line_id: "lid-b", line_idx: 1, line_printable: true, qty_completed: 2000 },
        { split_id: "s3", line_id: "lid-c", line_idx: 2, line_printable: true, qty_completed: 2000 },
      ],
    },
    {
      id: "preview-d3",
      helper_required: 3000,
      helper_prepared: 3000,
      screen_qty: 12,
      print_qty_total: 3000,
      print_completed_qty: 3000,
      current_stage: 11,
      operators: [
        { split_id: "t1", line_id: "lid-x", line_idx: 0, line_printable: true, qty_completed: 1000 },
        { split_id: "t2", line_id: "lid-y", line_idx: 1, line_printable: true, qty_completed: 2000 },
      ],
    },
  ];
}
