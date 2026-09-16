import {
  STATUS_PO_WORKFLOW_HEADERS,
  STATUS_PO_WORKFLOW_STAGES,
  clampColWidth,
  statusBadgeTone,
  workflowCellState,
  workflowProgressPercent,
} from "./statusPoWorkflow";

describe("Status PO workflow matrix mapping", () => {
  test("workflow headers stay in the required order", () => {
    expect(STATUS_PO_WORKFLOW_HEADERS).toEqual([
      "Order", "Design", "Appr. Design", "Payment", "Appr. Payment",
      "Jadwal", "Screen", "Helper", "Operator", "Sample",
      "Cetak", "Packing", "SJ", "Loading", "Kirim",
    ]);
    expect(STATUS_PO_WORKFLOW_STAGES).toHaveLength(15);
  });

  test("cells use explicit workflow evidence only — never sequential completion", () => {
    const packing = {
      status: "Packing",
      current_stage: 11,
      workflow: {
        order: "completed",
        design: "pending",
        cetak: "completed",
        packing: "active",
        kirim: "pending",
      },
    };
    expect(workflowCellState(packing, "design")).toBe("pending");
    expect(workflowCellState(packing, "cetak")).toBe("completed");
    expect(workflowCellState(packing, "packing")).toBe("active");
    expect(workflowCellState(packing, "kirim")).toBe("pending");
    expect(workflowCellState({ status: "Packing", current_stage: 11 }, "design")).toBe("unknown");
    expect(workflowCellState({ status: "Cancelled", cancelled: true }, "order")).toBe("cancelled");
  });

  test("unknown stays unknown", () => {
    expect(workflowCellState({ workflow: { packing: "unknown" } }, "packing")).toBe("unknown");
  });

  test("progress percent counts completed evidence states", () => {
    const all = Object.fromEntries(STATUS_PO_WORKFLOW_STAGES.map((s) => [s.key, "completed"]));
    expect(workflowProgressPercent({ workflow: all })).toBe(100);
    expect(workflowProgressPercent({ status: "Packing", current_stage: 11 })).toBe(0);
    expect(workflowProgressPercent({
      workflow: { order: "completed", packing: "active", design: "pending" },
    })).toBe(7);
  });

  test("status badge tones follow current stage", () => {
    expect(statusBadgeTone("Design")).toBe("design");
    expect(statusBadgeTone("Payment")).toBe("payment");
    expect(statusBadgeTone("Terkirim")).toBe("kirim");
    expect(statusBadgeTone("Cancelled")).toBe("cancel");
  });

  test("column resize cannot collapse below minimum", () => {
    expect(clampColWidth("po", 10)).toBe(132);
    expect(clampColWidth("order", 20)).toBe(60);
    expect(clampColWidth("progress", 80)).toBe(120);
  });
});
