import {
  operatorAssignmentKey,
  operatorCanSubmit,
  operatorConfirmEnabled,
  operatorPanelModel,
  operatorSplitDone,
  operatorStatusLabel,
} from "./workModels";

function assertEqual(actual: unknown, expected: unknown, label: string) {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

const user = { email: "op@x.com", id: "u-op" };

function orderFixture(over: Record<string, unknown> = {}) {
  return {
    sample_approved: true,
    requires_printing: true,
    current_stage: 8,
    print_completed_qty: 0,
    print_qty_total: 1000,
    workflow_ready: { operator: true, print_qty: true },
    helper_prep: { items: [{ line_idx: 0, qty_prepared: 0 }] },
    line_items: [
      { name: "Cup A", quantity: 1000, color: "Hitam", production_process: "print" },
    ],
    operator_splits: [
      {
        id: "split-a",
        line_idx: 0,
        operator_email: "op@x.com",
        operator_user_id: "u-op",
        quantity: 1000,
        qty_completed: 0,
        progress_entries: [],
      },
    ],
    ...over,
  };
}

function panel(over: Record<string, unknown> = {}) {
  return operatorPanelModel(orderFixture(over), user);
}

{
  const m = panel();
  assertEqual(m.target, 1000, "A target");
  assertEqual(m.done, 0, "A done");
  assertEqual(m.remaining, 1000, "A remaining");
  assertEqual(m.status === "Selesai", false, "A not selesai");
  assertEqual(m.status, "Belum Mulai", "A status");
  assertEqual(m.lines[0].splitId, "split-a", "A split id");
  assertEqual(operatorConfirmEnabled({
    hasOperatorUpdate: true,
    workflowEligible: true,
    remaining: m.remaining,
    inputQty: 0,
    submitting: false,
  }), false, "A confirm empty");
  assertEqual(operatorConfirmEnabled({
    hasOperatorUpdate: true,
    workflowEligible: true,
    remaining: m.remaining,
    inputQty: 400,
    submitting: false,
  }), true, "A confirm valid");
}

{
  const m = panel({
    current_stage: 12,
    helper_prep: { items: [{ line_idx: 0, qty_prepared: 1000 }], entries: [{ qty: 1000, stage: "helper" }] },
    print_completed_qty: 1000,
    operator_splits: [{
      id: "split-a",
      line_idx: 0,
      operator_email: "op@x.com",
      quantity: 1000,
      qty_completed: 0,
      progress_entries: [{ qty: 1000, cancelled: true, stage: "operator" }],
    }],
  });
  assertEqual(m.done, 0, "cancelled leftover done");
  assertEqual(m.remaining, 1000, "cancelled leftover remaining");
  assertEqual(m.status, "Belum Mulai", "cancelled leftover status");
}

{
  const m = panel({
    operator_splits: [{
      id: "split-a",
      line_idx: 0,
      operator_email: "op@x.com",
      quantity: 1000,
      qty_completed: 400,
      progress_entries: [{ qty: 400, action_id: "a1" }],
    }],
  });
  assertEqual(m.done, 400, "B done");
  assertEqual(m.remaining, 600, "B remaining");
  assertEqual(m.status, "Proses", "B status");
}

{
  const m = panel({
    operator_splits: [{
      id: "split-a",
      line_idx: 0,
      operator_email: "op@x.com",
      quantity: 1000,
      qty_completed: 1000,
      progress_entries: [{ qty: 400 }, { qty: 600 }],
    }],
  });
  assertEqual(m.done, 1000, "C done");
  assertEqual(m.remaining, 0, "C remaining");
  assertEqual(m.status, "Selesai", "C status");
  assertEqual(operatorConfirmEnabled({
    hasOperatorUpdate: true,
    workflowEligible: true,
    remaining: m.remaining,
    inputQty: 1,
    submitting: false,
  }), false, "C confirm disabled");
}

{
  const m = panel({
    helper_prep: { items: [{ line_idx: 0, qty_prepared: 1000 }] },
    print_completed_qty: 0,
    operator_splits: [{
      id: "split-a",
      line_idx: 0,
      operator_email: "op@x.com",
      quantity: 1000,
      qty_completed: 0,
      progress_entries: [],
    }],
  });
  assertEqual(m.done, 0, "D done");
  assertEqual(m.remaining, 1000, "D remaining");
  assertEqual(m.status, "Belum Mulai", "D status");
}

{
  const m = panel({
    current_stage: 12,
    print_status: "SELESAI CETAK",
    workflow_ready: { operator: true, print_qty: true, packing: true },
    operator_splits: [{
      id: "split-a",
      line_idx: 0,
      operator_email: "op@x.com",
      quantity: 1000,
      qty_completed: 0,
    }],
  });
  assertEqual(operatorSplitDone(m.lines[0].split), 0, "E split done");
  assertEqual(m.done, 0, "E done");
  assertEqual(m.status === "Selesai", false, "E not selesai");
}

{
  const m = panel({
    line_items: [
      { name: "Cup A", quantity: 1000, production_process: "print" },
      { name: "Cup A", quantity: 1000, production_process: "print" },
    ],
    operator_splits: [
      { id: "line-0", line_idx: 0, operator_email: "op@x.com", quantity: 1000, qty_completed: 400 },
      { id: "line-1", line_idx: 1, operator_email: "op@x.com", quantity: 1000, qty_completed: 0 },
    ],
  });
  assertEqual(m.lines.length, 2, "F lines");
  assertEqual(m.lines[0].splitId, "line-0", "F id0");
  assertEqual(m.lines[0].done, 400, "F done0");
  assertEqual(m.lines[1].splitId, "line-1", "F id1");
  assertEqual(m.lines[1].done, 0, "F done1");
  assertEqual(m.done, 400, "F header done");
  assertEqual(m.remaining, 1600, "F header remaining");
  assertEqual(
    operatorAssignmentKey(m.lines[0].split) === operatorAssignmentKey(m.lines[1].split),
    false,
    "F keys distinct"
  );
}

assertEqual(operatorSplitDone({
  quantity: 1000,
  qty_completed: 0,
  progress_entries: [{ qty: 1000, stage: "operator" }],
}), 0, "snapshot 0 beats leftover entry");

assertEqual(operatorSplitDone({ quantity: 1000, qty: 1000 }), 0, "assignment qty is not done");
assertEqual(operatorStatusLabel(0, 1000), "Belum Mulai", "label belum mulai");
assertEqual(operatorStatusLabel(1, 1000), "Proses", "label proses");
assertEqual(operatorStatusLabel(1000, 1000), "Selesai", "label selesai");

assertEqual(operatorCanSubmit({ sample_approved: true }).ok, true, "gate ok");
assertEqual(operatorConfirmEnabled({
  hasOperatorUpdate: true,
  workflowEligible: true,
  remaining: 1000,
  inputQty: 1001,
  submitting: false,
}), false, "qty over remaining");

console.log("workModels.operator tests ok");
