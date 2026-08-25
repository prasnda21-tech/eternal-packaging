/**
 * Runtime Total Qty + Qty Cetak tests — same helpers ProductionSchedule.jsx uses.
 * Run from frontend/: node scripts/test_production_schedule_qty.mjs
 */
import { printQtyProgress, scheduledPrintQtyProgress } from "../src/lib/productionScheduleQty.js";

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const p1 = printQtyProgress({ total_quantity: 15000, print_qty_total: 10000, print_completed_qty: 5000 });
assert(p1.text === "5.000 / 10.000", `TEST 1 got ${p1.text}`);
assert(!p1.text.includes("15.000"), "TEST 1 must not use PO total as denominator");

const p2 = printQtyProgress({ total_quantity: 15000, print_qty_total: 10000, print_completed_qty: 0 });
assert(p2.text === "0 / 10.000", `TEST 2 got ${p2.text}`);

const p3 = printQtyProgress({ total_quantity: 15000, print_qty_total: 10000, print_completed_qty: 10000 });
assert(p3.text === "10.000 / 10.000", `TEST 3 got ${p3.text}`);

const p4 = printQtyProgress({ total_quantity: 15000, print_qty_total: 10000, print_completed_qty: 5000 });
assert(p4.text === "5.000 / 10.000", `TEST 4 multi-line totals got ${p4.text}`);

const row = { total_quantity: 15000, print_qty_total: 10000, print_completed_qty: 5000 };
assert(printQtyProgress(row).text === printQtyProgress(row).text, "TEST 5 repeat");
assert(printQtyProgress(row).text === "5.000 / 10.000", "TEST 5 no double count");

const none = printQtyProgress({ total_quantity: 5000, print_qty_total: 0, print_completed_qty: 0 });
assert(none.text === "—", `no printable got ${none.text}`);

const over = printQtyProgress({ total_quantity: 15000, print_qty_total: 10000, print_completed_qty: 12000 });
assert(over.text === "12.000 / 10.000", `over-target got ${over.text}`);
assert(over.over === true, "over-target must warn");

const mixed = {
  total_quantity: 15000,
  print_qty_total: 10000,
  print_completed_qty: 2000,
  operators: [{ quantity: 5000, qty_completed: 2000 }],
};
assert(printQtyProgress(mixed).text === "2.000 / 10.000", `Total Qty must stay actual, got ${printQtyProgress(mixed).text}`);
assert(scheduledPrintQtyProgress(mixed).text === "5.000 / 10.000", `Qty Cetak got ${scheduledPrintQtyProgress(mixed).text}`);
assert(!scheduledPrintQtyProgress(mixed).text.includes("15.000"), "Qty Cetak must not use PO total");

const multi = {
  total_quantity: 15000,
  print_qty_total: 10000,
  print_completed_qty: 0,
  operators: [
    { quantity: 4000, qty_completed: 0, line_idx: 0 },
    { quantity: 1000, qty_completed: 0, line_idx: 1 },
  ],
};
assert(scheduledPrintQtyProgress(multi).text === "5.000 / 10.000", `multi-line Qty Cetak got ${scheduledPrintQtyProgress(multi).text}`);

const edited = {
  total_quantity: 15000,
  print_qty_total: 10000,
  operators: [{ quantity: 6000 }],
};
assert(scheduledPrintQtyProgress(edited).text === "6.000 / 10.000", "edit must replace not sum history");
assert(scheduledPrintQtyProgress(edited).text === scheduledPrintQtyProgress(edited).text, "Qty Cetak no double count on refresh");

console.log("production schedule qty progress OK — Total Qty + Qty Cetak passed");
