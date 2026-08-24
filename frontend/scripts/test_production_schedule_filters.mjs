/**
 * Runtime filter tests — imports the same module ProductionSchedule.jsx uses.
 * Run from frontend/: node scripts/test_production_schedule_filters.mjs
 */
import {
  FILTER_DEFAULTS,
  applyProductionFilters,
  sortProductionRows,
  toDateKey,
} from "../src/lib/productionScheduleFilters.js";

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const SOURCE = [
  {
    id: "1", po_number: "PO-A", customer: "Kopi Rumah", outlet: "Kemang",
    buffer_stock: true, packing_status: "Berjalan", current_stage: 11,
    urgent: false, hold: false, cancelled: false,
    operators: [{ operator_name: "Op A", quantity: 100, qty_completed: 10 }],
    screen_qty: 100, helper_required: 100, helper_shortage_status: "Cukup",
    deadline: "2026-08-10T08:00:00+00:00", work_date: "2026-08-08",
    delivery_status: "Belum Terkirim",
  },
  {
    id: "2", po_number: "PO-B", customer: "Bumi Bowl", outlet: "BSD",
    buffer_stock: false, packing_status: "Belum", current_stage: 8,
    urgent: true, hold: false, cancelled: false,
    operators: [{ operator_name: "Op B", quantity: 50, qty_completed: 0 }],
    screen_qty: null, helper_required: null,
    deadline: "2026-08-20T08:00:00+00:00",
    delivery_status: "Belum",
  },
  {
    id: "3", po_number: "PO-C", customer: "Kopi Rumah", outlet: "Senopati",
    buffer_stock: true, packing_status: "Sudah", current_stage: 14,
    urgent: false, hold: false, cancelled: false,
    operators: [], deadline: "2026-08-01T08:00:00+00:00",
    delivery_status: "Terkirim",
  },
  {
    id: "4", po_number: "PO-D", customer: "Nasi Sore", outlet: "Cilandak",
    buffer_stock: false, packing_status: "Belum", current_stage: 2,
    urgent: false, hold: true, cancelled: false,
    operators: [{ operator_name: "Op A" }],
    deadline: "2026-08-15T08:00:00+00:00",
    delivery_status: "Belum Terkirim",
  },
  {
    id: "5", po_number: "PO-E", customer: "Daily Grind", outlet: "Senopati",
    buffer_stock: false, packing_status: "Belum", current_stage: 1,
    urgent: false, hold: false, cancelled: true,
    operators: [], deadline: "2026-07-01T08:00:00+00:00",
  },
];

function run(overrides = {}) {
  return applyProductionFilters(SOURCE, { ...FILTER_DEFAULTS, includeCompleted: true, includeCancelled: true, ...overrides });
}

function ids(rows) {
  return rows.map((r) => r.po_number).sort().join(",");
}

// A. Status X only
{
  const hold = run({ statusFilter: "hold" });
  assert(hold.length === 1 && hold[0].po_number === "PO-D", `A: hold only, got ${ids(hold)}`);
  const aktif = run({ statusFilter: "aktif" });
  assert(aktif.every((r) => !r.hold && !r.cancelled && r.current_stage < 14), "A: aktif excludes hold/selesai/cancel");
}

// B. Buffer Stock only
{
  const yes = run({ bufferFilter: "yes" });
  assert(yes.every((r) => r.buffer_stock) && yes.length === 2, `B: buffer yes got ${ids(yes)}`);
  const no = run({ bufferFilter: "no" });
  assert(no.every((r) => !r.buffer_stock) && no.length === 3, `B: buffer no got ${ids(no)}`);
}

// C. Operator A + Buffer Stock intersection
{
  const rows = run({ operatorFilter: "Op A", bufferFilter: "yes" });
  assert(rows.length === 1 && rows[0].po_number === "PO-A", `C: got ${ids(rows)}`);
}

// D. Search customer (trim + case-insensitive)
{
  const rows = run({ q: "  kopi rumah  " });
  assert(rows.length === 2 && rows.every((r) => r.customer === "Kopi Rumah"), `D: got ${ids(rows)}`);
}

// E. Include Selesai
{
  const off = applyProductionFilters(SOURCE, { ...FILTER_DEFAULTS, includeCompleted: false, includeCancelled: true });
  assert(!off.some((r) => r.po_number === "PO-C"), "E: selesai hidden when off");
  const on = applyProductionFilters(SOURCE, { ...FILTER_DEFAULTS, includeCompleted: true, includeCancelled: true });
  assert(on.some((r) => r.po_number === "PO-C"), "E: selesai shown when on");
}

// F. Include Cancelled
{
  const off = applyProductionFilters(SOURCE, { ...FILTER_DEFAULTS, includeCancelled: false, includeCompleted: true });
  assert(!off.some((r) => r.po_number === "PO-E"), "F: cancelled hidden when off");
  const on = applyProductionFilters(SOURCE, { ...FILTER_DEFAULTS, includeCancelled: true, includeCompleted: true });
  assert(on.some((r) => r.po_number === "PO-E"), "F: cancelled shown when on");
}

// G. Date range (deadline normalized from ISO)
{
  assert(toDateKey("2026-08-10T08:00:00+00:00") === "2026-08-10", "G: toDateKey ISO");
  const rows = run({ dateFrom: "2026-08-09", dateTo: "2026-08-16" });
  const got = ids(rows);
  assert(got === "PO-A,PO-D", `G: expected PO-A,PO-D got ${got}`);
}

// H. Reset → default dataset (completed on, cancelled off)
{
  const rows = applyProductionFilters(SOURCE, { ...FILTER_DEFAULTS });
  assert(!rows.some((r) => r.cancelled), "H: cancelled excluded by default");
  assert(rows.some((r) => r.po_number === "PO-C"), "H: completed included by default");
  assert(rows.length === 4, `H: default count ${rows.length}`);
}

// I. Three-way AND
{
  const rows = run({
    bufferFilter: "yes",
    customerFilter: "Kopi Rumah",
    statusFilter: "aktif",
  });
  assert(rows.length === 1 && rows[0].po_number === "PO-A", `I: got ${ids(rows)}`);
}

{
  const sorted = sortProductionRows(SOURCE.filter((r) => !r.cancelled), "deadline");
  assert(sorted[0].po_number === "PO-C", "sort deadline ascending first");
}

console.log("production schedule filters OK — A–I passed");
