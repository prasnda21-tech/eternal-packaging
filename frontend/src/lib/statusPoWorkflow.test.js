import {
  STATUS_PO_WORKFLOW_HEADERS,
  STATUS_PO_WORKFLOW_STAGES,
  clampColWidth,
  statusBadgeTone,
  workflowCellState,
  workflowProgressPercent,
  workflowStageIndex,
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

  test("maps existing status labels to stage index without inventing completion", () => {
    expect(workflowStageIndex({ status: "Packing" })).toBe(11);
    expect(workflowStageIndex({ status: "Cetak berjalan" })).toBe(10);
    expect(workflowStageIndex({ status: "Loading" })).toBe(13);
    expect(workflowStageIndex({ status: "Penjadwalan" })).toBe(5);
    expect(workflowStageIndex({ status: "Design" })).toBe(1);
    expect(workflowStageIndex({ status: "Payment" })).toBe(3);
    expect(workflowStageIndex({ status: "Screener" })).toBe(6);
    expect(workflowStageIndex({ status: "Konfirmasi Sample" })).toBe(9);
    expect(workflowStageIndex({ status: "Terkirim" })).toBe(15);
  });

  test("done / current / pending / cancelled cell states", () => {
    const packing = { status: "Packing" };
    expect(workflowCellState(packing, 10)).toBe("done");
    expect(workflowCellState(packing, 11)).toBe("current");
    expect(workflowCellState(packing, 12)).toBe("pending");
    expect(workflowCellState({ status: "Cancelled", cancelled: true }, 3)).toBe("cancelled");
    expect(workflowCellState({ status: "HOLD", hold: true, current_stage: 5 }, 5)).toBe("hold");
  });

  test("progress percent is derived from mapped stage", () => {
    expect(workflowProgressPercent({ status: "Operator" })).toBe(53);
    expect(workflowProgressPercent({ status: "Packing" })).toBe(73);
    expect(workflowProgressPercent({ status: "Terkirim" })).toBe(100);
  });

  test("status badge tones follow current stage", () => {
    expect(statusBadgeTone("Design")).toBe("design");
    expect(statusBadgeTone("Payment")).toBe("payment");
    expect(statusBadgeTone("Terkirim")).toBe("kirim");
    expect(statusBadgeTone("Cancelled")).toBe("cancel");
  });

  test("column resize cannot collapse below minimum", () => {
    expect(clampColWidth("po", 10)).toBe(175);
    expect(clampColWidth("order", 20)).toBe(60);
    expect(clampColWidth("progress", 80)).toBe(120);
  });
});
