/**
 * Local visual fixture for the V4 design preview.
 * Loaded only when Production Dashboard is opened with ?simulasi=1
 * on a development build. Never sent to the production snapshot API
 * and never written into a layout document.
 */

const TONE = ["#2563EB", "#059669", "#F97316", "#7C3AED", "#94A3B8"];

function card(id, x, y, w, h) {
  return { widget_id: id, x, y, w, h, settings: {} };
}

export function simulationBoard() {
  return {
    widgets: [
      card("perlu_produksi", 0, 0, 3, 2),
      card("po_urgent", 3, 0, 3, 2),
      card("po_hold", 6, 0, 3, 2),
      card("po_aktif", 9, 0, 3, 2),
      card("sisa_qty", 0, 2, 4, 4),
      card("sim_output", 4, 2, 4, 4),
      card("deadline", 8, 2, 4, 4),
      card("sim_workflow", 0, 6, 4, 4),
      card("sim_operator", 4, 6, 4, 4),
      card("sim_composition", 8, 6, 4, 4),
      card("approval", 0, 10, 4, 3),
      card("prioritas", 4, 10, 8, 4),
    ],
    models: {
      perlu_produksi: { status: "complete", count: 12, delta: "+25%", spark: [6, 7, 8, 7, 9, 11, 12], drill: null },
      po_urgent: { status: "complete", count: 3, delta: "+50%", spark: [1, 1, 2, 2, 1, 3, 3], drill: null },
      po_hold: { status: "complete", count: 2, delta: "0%", spark: [2, 2, 3, 2, 2, 2, 2], drill: null },
      po_aktif: { status: "complete", count: 28, delta: "+12%", spark: [20, 22, 21, 24, 25, 26, 28], drill: null },
      sisa_qty: {
        status: "complete",
        presentation: "simulated-products",
        unit: "pcs",
        rows: [
          { name: "Cup 12 oz", qty: 52000 },
          { name: "Cup 16 oz", qty: 28500 },
          { name: "Cup 22 oz", qty: 12000 },
          { name: "Paper Cup 12 oz", qty: 6500 },
          { name: "Paper Bowl 650 ml", qty: 3200 },
        ],
        drill: null,
      },
      sim_output: {
        status: "complete",
        kind: "bars",
        unit: "pcs",
        categories: [
          { name: "Cup PET/PP", color: TONE[0] },
          { name: "Paper Cup/Bowl", color: TONE[1] },
          { name: "Lainnya", color: TONE[4] },
        ],
        series: [
          { label: "17", parts: [18000, 12000, 4000] },
          { label: "18", parts: [22000, 14000, 5000] },
          { label: "19", parts: [20000, 16000, 4500] },
          { label: "20", parts: [26000, 15000, 6000] },
          { label: "21", parts: [30000, 18000, 5500] },
          { label: "22", parts: [28000, 17000, 5000] },
          { label: "23", parts: [32000, 19000, 6500] },
        ],
      },
      deadline: { status: "complete", within6h: 2, within24h: 6, drill: null },
      sim_workflow: {
        status: "complete",
        kind: "rank",
        unit: "line",
        rows: [
          { name: "Design", qty: 18, color: TONE[0] },
          { name: "Screener", qty: 32, color: TONE[2] },
          { name: "Operator", qty: 28, color: TONE[1] },
          { name: "Helper", qty: 16, color: TONE[3] },
          { name: "Packing", qty: 12, color: "#FB7185" },
          { name: "Selesai", qty: 40, color: TONE[4] },
        ],
      },
      sim_operator: {
        status: "complete",
        kind: "rank",
        unit: "pcs",
        rows: [
          { name: "Andi", qty: 185000, color: TONE[0] },
          { name: "Budi", qty: 142000, color: TONE[0] },
          { name: "Candra", qty: 128500, color: TONE[0] },
          { name: "Dimas", qty: 96000, color: TONE[0] },
          { name: "Ega", qty: 74500, color: TONE[0] },
        ],
      },
      sim_composition: {
        status: "complete",
        kind: "donut",
        unit: "pcs",
        total: 568000,
        slices: [
          { name: "Cup 12 oz", pct: 36, color: TONE[1] },
          { name: "Cup 16 oz", pct: 28, color: TONE[0] },
          { name: "Cup 22 oz", pct: 18, color: TONE[2] },
          { name: "Paper Cup 12 oz", pct: 12, color: TONE[3] },
          { name: "Lainnya", pct: 6, color: TONE[4] },
        ],
      },
      approval: { status: "complete", design: 4, payment: 2, drill: null },
      prioritas: {
        status: "complete",
        rows: [
          { order_id: "sim-1", line_idx: 1, po_number: "PO-2026-10-001", item: "Cup 16 oz", unit: "pcs", qty_production_remaining: 90000, labels: ["URGENT", "DUE TODAY"] },
          { order_id: "sim-2", line_idx: 0, po_number: "PO-2026-10-001", item: "Cup 16 oz", unit: "pack", qty_production_remaining: 5000, labels: ["URGENT"] },
          { order_id: "sim-3", line_idx: 0, po_number: "PO-2026-10-003", item: "Paper Cup 12 oz", unit: "pcs", qty_production_remaining: 10000, labels: ["NORMAL"] },
          { order_id: "sim-4", line_idx: 2, po_number: "PO-2026-10-004", item: "Cup 22 oz", unit: "pcs", qty_production_remaining: 4000, labels: ["HOLD", "≤ 24 jam"] },
          { order_id: "sim-5", line_idx: 1, po_number: "PO-2026-10-007", item: "Cup 12 oz", unit: "pcs", qty_production_remaining: 4000, labels: ["HOLD"] },
        ],
        drill: null,
      },
    },
  };
}
