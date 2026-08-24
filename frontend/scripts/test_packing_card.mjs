/** Runtime packing-card visibility — same helpers PODetail + PackingPanel use. */
import { shouldShowPackingCard, packingSubmitEnabled, computePackingLines } from "../src/lib/packingCard.js";

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function availableForPacking(order) {
  const required = Number(order.packing_required_total || order.total_quantity || 0);
  const avail = Number(order.non_print_qty_total || 0) + Number(order.print_completed_qty || 0);
  return Math.max(0, Math.min(required, avail));
}

const BASE = {
  total_quantity: 6000,
  packing_required_total: 6000,
  print_qty_total: 3000,
  non_print_qty_total: 3000,
  current_stage: 2,
};

// A: print_completed 0 → card visible, available = 3000 non-print, submit on
{
  const order = { ...BASE, print_completed_qty: 0 };
  const avail = availableForPacking(order);
  assert(shouldShowPackingCard(true) === true, "A: card visible for packing perm");
  assert(avail === 3000, `A: available ${avail}`);
  assert(packingSubmitEnabled(true, avail) === true, "A: submit enabled");
}

// B: print_completed 1000 → available 4000, card still visible at early stage
{
  const order = { ...BASE, print_completed_qty: 1000, current_stage: 5 };
  const avail = availableForPacking(order);
  assert(shouldShowPackingCard(true) === true, "B: card visible");
  assert(avail === 4000, `B: available ${avail}`);
  assert(packingSubmitEnabled(true, avail) === true, "B: submit enabled without waiting all operators");
}

// C: available 0 → card still visible, submit disabled
{
  const order = {
    total_quantity: 3000,
    packing_required_total: 3000,
    print_qty_total: 3000,
    non_print_qty_total: 0,
    print_completed_qty: 0,
    current_stage: 1,
  };
  const avail = availableForPacking(order);
  assert(avail === 0, "C: available 0");
  assert(shouldShowPackingCard(true) === true, "C: card still visible");
  assert(packingSubmitEnabled(true, avail) === false, "C: submit disabled");
}

// D: no packing permission
{
  assert(shouldShowPackingCard(false) === false, "D: no card without packing.update");
  assert(packingSubmitEnabled(false, 4000) === false, "D: no submit without perm");
}

// E: partial packed
{
  const order = { ...BASE, print_completed_qty: 1000, packing_info: { qty_packed: 1500 } };
  const avail = availableForPacking(order);
  const packed = Number(order.packing_info.qty_packed);
  const remaining = Math.max(avail - packed, 0);
  assert(shouldShowPackingCard(true) === true, "E: card visible after partial");
  assert(avail === 4000, "E: available still 4000");
  assert(packed === 1500, "E: packed 1500");
  assert(remaining === 2500, "E: remaining 2500");
  assert(packingSubmitEnabled(true, avail) === true, "E: can continue packing");
}

// Stage must not hide the card
{
  for (const stage of [0, 3, 7, 10, 11]) {
    assert(shouldShowPackingCard(true) === true, `stage ${stage} must not hide card`);
  }
}

// F: per-line packed comes from packing_info.lines, not allocated PO total
{
  const order = {
    line_items: [
      { name: "Kaos", quantity: 3000, production_process: "print" },
      { name: "Sticker", quantity: 3000, production_process: "non_print" },
    ],
    operator_splits: [{ line_idx: 0, qty_completed: 1000, quantity: 1000 }],
    packing_info: { lines: [{ line_idx: 1, packed_qty: 1500 }], qty_packed: 1500 },
    helper_prep: { items: [{ line_idx: 0, qty_required: 3000, qty_prepared: 1000 }] },
  };
  const lines = computePackingLines(order);
  assert(lines.length === 2, "F: two lines");
  assert(lines[1].available === 3000, `F: non-print available ${lines[1].available}`);
  assert(lines[0].print_completed === 1000, "F: print completed");
  assert(lines[0].print_remaining === 2000, "F: belum dicetak");
  assert(lines[0].available === 1000, `F: print available ${lines[0].available}`);
  assert(lines[1].packed === 1500, `F: non-print packed ${lines[1].packed}`);
  assert(lines[0].packed === 0, "F: print not packed yet");
}

// G: legacy qty_packed total does not invent per-line packed
{
  const order = {
    line_items: [
      { name: "Kaos", quantity: 3000, production_process: "print" },
      { name: "Sticker", quantity: 3000, production_process: "non_print" },
    ],
    operator_splits: [{ line_idx: 0, qty_completed: 1000, quantity: 1000 }],
    packing_info: { qty_packed: 1500 },
  };
  const lines = computePackingLines(order);
  assert(lines[0].packed === 0, "G: legacy print packed stays 0");
  assert(lines[1].packed === 0, "G: legacy non-print packed stays 0");
}

console.log("packing card OK — A–G passed");
