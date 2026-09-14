/**
 * Runtime fixtures for Timeline chip logic — calls resolveTimelineChip
 * (the same function Timeline.jsx uses at render).
 *
 * Run from frontend/: node scripts/test_timeline_design_chip.mjs
 */
import { resolveStageState } from "../src/lib/stageState.js";
import { resolveTimelineChip } from "../src/lib/timelineChip.js";

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function chip(idx, order) {
  return resolveTimelineChip(idx, order, order.current_stage);
}

{
  const order = {
    design_files: [],
    design_status: "Waiting",
    payment_files: [{ name: "p.png", uploaded_at: "2026-01-01" }],
    payment_status: "Approved",
    current_stage: 4,
  };
  assert(resolveStageState(1, order, 4).state !== "done", "stageState Design must not be done");
  assert(resolveStageState(1, order, 4).label === "Menunggu", "stageState Design pending until reached");
}

// FIXTURE A — production bug payload
{
  const order = {
    current_stage: 5,
    design_files: [],
    design_status: "Waiting",
    design_bypassed: false,
    payment_files: [{ name: "bukti-bayar.png", data_url: "data:image/png;base64,xx", uploaded_at: "2026-01-01T00:00:00Z" }],
    payment_status: "Approved",
    payment_approved_at: "2026-01-02T00:00:00Z",
    timeline: [
      { stage: 3, note: "Upload payment: bukti-bayar.png" },
      { stage: 4, note: "Payment di-approve" },
    ],
  };
  const design = chip(1, order);
  assert(design.state !== "done", `A: Design must not be SELESAI, got ${design.state}/${design.label}`);
  assert(design.state === "pending", `A: untouched Design is pending, got ${design.state}`);
  assert(design.label === "Menunggu", `A: expected Menunggu, got ${design.label}`);
  const apprDesign = chip(2, order);
  assert(apprDesign.label !== "Menunggu approval", `A: Approval Design must not say Menunggu approval without design file, got ${apprDesign.label}`);
  assert(chip(3, order).state === "done", "A: Payment SELESAI");
  assert(chip(4, order).state === "done", "A: Approval Payment SELESAI");
}

// FIXTURE B — designer uploaded; approval design still waiting
{
  const order = {
    current_stage: 5,
    design_files: [{
      id: "d1",
      name: "mockup.png",
      data_url: "data:image/png;base64,aa",
      uploaded_by: "designer@eternalpackaging.com",
      uploaded_by_role: "designer",
      uploaded_at: "2026-01-03T00:00:00Z",
    }],
    design_status: "Waiting",
    design_bypassed: false,
    payment_files: [{ name: "bukti-bayar.png", data_url: "data:image/png;base64,xx", uploaded_at: "2026-01-01T00:00:00Z" }],
    payment_status: "Approved",
  };
  const design = chip(1, order);
  assert(design.state === "done" && design.label === "Selesai", `B: Design SELESAI, got ${design.label}`);
  const apprDesign = chip(2, order);
  assert(apprDesign.label === "Menunggu approval", `B: Approval Design Menunggu approval, got ${apprDesign.label}`);
}

{
  const order = {
    current_stage: 5,
    design_files: [],
    design_status: "Waiting",
    payment_files: [{ name: "p.png", data_url: "data:image/png;base64,xx", uploaded_at: "2026-01-01T00:00:00Z" }],
    payment_status: "Approved",
  };
  assert(chip(1, order).state !== "done", "payment_files must not make Design SELESAI");
}

{
  const order = {
    current_stage: 10,
    design_files: [],
    design_status: "Waiting",
    payment_files: [],
    payment_status: "Waiting",
  };
  assert(chip(1, order).state !== "done", "current_stage alone must not complete Design");
  assert(chip(1, order).label === "Menunggu", "current_stage alone must not mark Design current");
}

{
  const order = {
    current_stage: 2,
    design_files: [{
      id: "d1",
      name: "art.pdf",
      data_url: "data:application/pdf;base64,aa",
      uploaded_at: "2026-09-11T00:00:00Z",
    }],
    design_status: "Waiting",
    design_bypassed: false,
    customer_approval: { status: "approved" },
    payment_files: [],
    payment_status: "Waiting",
  };
  const apprDesign = chip(2, order);
  assert(apprDesign.state === "done" && apprDesign.label === "Selesai", `CA approved must complete Approval Design, got ${apprDesign.state}/${apprDesign.label}`);
  assert(chip(3, order).state !== "done", "Payment must not auto-complete");
  assert(chip(3, order).state === "pending", "Payment stays pending until actually reached");
}

console.log("timeline design chip OK — fixtures A/B + regressions passed");
