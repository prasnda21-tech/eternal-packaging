/** Quick sanity checks for stageState — run: node frontend/scripts/test_stage_state.mjs */
import { resolveStageState } from "../src/lib/stageState.js";

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

// TEST A visual: design not done, payment approved (current_stage=4)
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
}

// TEST B visual: design done with data preserved display
{
  const order = {
    design_files: [{ name: "d.png" }],
    design_status: "Approved",
    design_approved_by: "designer@x.com",
    payment_files: [{ name: "p.png" }],
    payment_status: "Approved",
    current_stage: 4,
  };
  const design = resolveStageState(1, order, 4);
  assert(design.state === "done", "TEST B: design upload stage should be done");
  const approvalDesign = resolveStageState(2, order, 4);
  assert(approvalDesign.state === "done", "TEST B: approval design should be done");
}

console.log("stageState OK");
