/** Sanity checks for stageState — run: node scripts/test_stage_state.mjs (from frontend/) */
import { resolveStageState } from "../src/lib/stageState.js";

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

// TEST A: design not done; payment + approval payment selesai
{
  const order = {
    design_files: [],
    design_status: "Waiting",
    payment_files: [{ name: "p.png" }],
    payment_status: "Approved",
    current_stage: 4,
  };
  const design = resolveStageState(1, order, 4);
  assert(design.state !== "done", `TEST A: design must not be done, got ${design.state}`);
  assert(design.label === "Belum dikerjakan", `TEST A: expected Belum dikerjakan, got ${design.label}`);
  const approvalDesign = resolveStageState(2, order, 4);
  assert(approvalDesign.state !== "done", "TEST A: approval design must not be done");
  assert(approvalDesign.label === "Menunggu", `TEST A: approval design should wait for design work, got ${approvalDesign.label}`);
  const payment = resolveStageState(3, order, 4);
  assert(payment.state === "done", "TEST A: payment should be done");
  const approvalPayment = resolveStageState(4, order, 4);
  assert(approvalPayment.state === "done", "TEST A: approval payment should be done");
}

// TEST B: design done; approval design pending
{
  const order = {
    design_files: [{ name: "d.png", uploaded_at: "2026-01-10T08:00:00+00:00" }],
    design_status: "Waiting",
    payment_files: [{ name: "p.png" }],
    payment_status: "Approved",
    current_stage: 4,
  };
  const design = resolveStageState(1, order, 4);
  assert(design.state === "done", "TEST B: design should be done");
  const approvalDesign = resolveStageState(2, order, 4);
  assert(approvalDesign.state === "current", "TEST B: approval design should be current");
  assert(approvalDesign.label === "Menunggu approval", `TEST B: got ${approvalDesign.label}`);
}

// TEST C: design + approval design selesai
{
  const order = {
    design_files: [{ name: "d.png", uploaded_at: "2026-01-10T08:00:00+00:00" }],
    design_status: "Approved",
    payment_files: [{ name: "p.png" }],
    payment_status: "Approved",
    current_stage: 5,
  };
  assert(resolveStageState(1, order, 5).state === "done", "TEST C: design done");
  assert(resolveStageState(2, order, 5).state === "done", "TEST C: approval design done");
}

// TEST D: payment stages unaffected
{
  const order = {
    design_files: [],
    design_status: "Waiting",
    payment_files: [],
    payment_status: "Waiting",
    current_stage: 3,
  };
  assert(resolveStageState(3, order, 3).label === "Belum dikerjakan", "TEST D: payment belum dikerjakan");
  order.payment_files = [{ name: "p.png" }];
  assert(resolveStageState(3, order, 3).state === "done", "TEST D: payment selesai after upload");
  order.payment_status = "Approved";
  order.current_stage = 4;
  assert(resolveStageState(4, order, 4).state === "done", "TEST D: approval payment selesai");
  assert(resolveStageState(1, order, 4).state !== "done", "TEST D: design still not done");
}

// Regression: generic fallback must not mark design done when past stage
{
  const order = { design_files: [], design_status: "Waiting", current_stage: 10 };
  const design = resolveStageState(1, order, 10);
  assert(design.state !== "done", "regression: design must not inherit done from current_stage");
}

console.log("stageState OK — A/B/C/D passed");

// Cetak status is qty-based, not current_stage / sample-approved alone
{
  const base = {
    design_files: [{ name: "d.png" }],
    design_status: "Approved",
    payment_files: [{ name: "p.png" }],
    payment_status: "Approved",
    requires_printing: true,
    operator_splits: [
      { id: "a", quantity: 1000, qty_completed: 0 },
      { id: "b", quantity: 1000, qty_completed: 0 },
      { id: "c", quantity: 1000, qty_completed: 0 },
    ],
    operator_assigned_qty: 3000,
    print_qty_target: 3000,
    print_qty_total: 6000,
    screener_info: { screen_qty: 2 },
    sample_files: [{ name: "s.png" }],
    sample_approved: true,
    workflow_ready: { operator: true, print_qty: true, packing: true },
    current_stage: 11,
  };
  const pending = { ...base, sample_approved: false, workflow_ready: { operator: true, print_qty: false, packing: false }, print_completed_qty: 0, print_status: "BELUM MULAI CETAK" };
  const cetakPending = resolveStageState(10, pending, 11);
  assert(cetakPending.state !== "done", "Cetak must not be done before qty complete");
  assert(cetakPending.label === "Menunggu", `expected Menunggu before print qty unlock, got ${cetakPending.label}`);

  const partial = { ...base, print_completed_qty: 1000, print_status: "BELUM SELESAI CETAK" };
  const cetakPartial = resolveStageState(10, partial, 11);
  assert(cetakPartial.state !== "done", "partial print must not be Selesai Cetak");
  assert(cetakPartial.label === "BELUM SELESAI CETAK", `got ${cetakPartial.label}`);

  const done = { ...base, print_completed_qty: 3000, print_status: "SELESAI CETAK" };
  const cetakDone = resolveStageState(10, done, 11);
  assert(cetakDone.state === "done", "full qty should mark Cetak done");
  assert(cetakDone.label === "SELESAI CETAK", `got ${cetakDone.label}`);
}

console.log("stageState OK — cetak qty status passed");
