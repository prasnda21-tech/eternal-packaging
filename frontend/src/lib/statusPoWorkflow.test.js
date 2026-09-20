import {
  STATUS_PO_WORKFLOW_HEADERS,
  STATUS_PO_WORKFLOW_STAGES,
  clampColWidth,
  progressSegmentTone,
  statusBadgeTone,
  statusStageDisplayNumber,
  statusStripTone,
  workflowCanonicalStageIndex,
  workflowCellCaption,
  workflowCellState,
  workflowProgressPercent,
} from "./statusPoWorkflow";

const KEYS = STATUS_PO_WORKFLOW_STAGES.map((s) => s.key);

function wf(overrides = {}) {
  const base = Object.fromEntries(KEYS.map((k) => [k, "pending"]));
  base.order = "completed";
  return { ...base, ...overrides };
}

function completedThrough(lastKey) {
  const overrides = {};
  let hit = false;
  for (const k of KEYS) {
    overrides[k] = "completed";
    if (k === lastKey) {
      hit = true;
      break;
    }
  }
  if (!hit) throw new Error(`unknown key ${lastKey}`);
  return wf(overrides);
}

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
    expect(clampColWidth("progress", 80)).toBe(168);
  });

  test("status strip and segment tones stay mapped from existing states", () => {
    expect(statusStageDisplayNumber({ status: "Packing", current_stage: 11 })).toBe(12);
    expect(statusStageDisplayNumber({ status: "Terkirim", current_stage: 14 })).toBe(15);
    expect(statusStripTone({ status: "Terkirim" })).toBe("done");
    expect(statusStripTone({ status: "Packing" })).toBe("active");
    expect(statusStripTone({ status: "Design" })).toBe("active");
    expect(statusStripTone({ status: "Order Masuk" })).toBe("pending");
    expect(statusStripTone({ status: "Penjadwalan" })).toBe("process");
    expect(statusStripTone({ status: "HOLD", hold: true })).toBe("problem");
    expect(statusStripTone({ status: "Cancelled", cancelled: true })).toBe("problem");
    expect(progressSegmentTone("completed")).toBe("done");
    expect(progressSegmentTone("active")).toBe("active");
    expect(progressSegmentTone("hold")).toBe("problem");
    expect(progressSegmentTone("cancelled")).toBe("problem");
    expect(progressSegmentTone("pending")).toBe("pending");
    expect(progressSegmentTone("unknown")).toBe("pending");
    expect(progressSegmentTone("skipped")).toBe("skipped");
  });
});

describe("Status PO stage number, boxes, and percent share one workflow", () => {
  test("tahap awal: Order Masuk is stage 1 with 1 completed box and 7%", () => {
    const row = { status: "Order Masuk", current_stage: 0, workflow: wf() };
    expect(statusStageDisplayNumber(row)).toBe(1);
    expect(workflowCanonicalStageIndex(row)).toBe(0);
    expect(workflowCellState(row, "order")).toBe("completed");
    expect(workflowCellState(row, "design")).toBe("pending");
    expect(workflowProgressPercent(row)).toBe(7);
    expect(statusStripTone(row)).toBe("pending");
  });

  test("Approval Payment: number 5 matches 5 completed boxes and 33%", () => {
    const row = {
      status: "Approval Payment",
      current_stage: 4,
      workflow: completedThrough("appr_payment"),
    };
    expect(statusStageDisplayNumber(row)).toBe(5);
    expect(workflowProgressPercent(row)).toBe(33);
    expect(KEYS.filter((k) => workflowCellState(row, k) === "completed")).toHaveLength(5);
    expect(workflowCellState(row, "jadwal")).toBe("pending");
    expect(statusStripTone(row)).toBe("active");
  });

  test("Penjadwalan: number 6 matches 6 completed boxes and 40%, strip orange", () => {
    const row = {
      status: "Penjadwalan",
      current_stage: 5,
      workflow: completedThrough("jadwal"),
    };
    expect(statusStageDisplayNumber(row)).toBe(6);
    expect(workflowProgressPercent(row)).toBe(40);
    expect(KEYS.filter((k) => workflowCellState(row, k) === "completed")).toHaveLength(6);
    expect(statusStripTone(row)).toBe("process");
  });

  test("Produksi/Cetak active: number 11, 10 green boxes, 67%, active box blue", () => {
    const row = {
      status: "Cetak berjalan",
      current_stage: 10,
      workflow: {
        ...completedThrough("sample"),
        cetak: "active",
      },
    };
    expect(statusStageDisplayNumber(row)).toBe(11);
    expect(workflowProgressPercent(row)).toBe(67);
    expect(KEYS.filter((k) => workflowCellState(row, k) === "completed")).toHaveLength(10);
    expect(workflowCellState(row, "cetak")).toBe("active");
    expect(progressSegmentTone("active")).toBe("active");
    expect(statusStripTone(row)).toBe("process");
  });

  test("Packing active: number 12, 11 green boxes, 73%", () => {
    const row = {
      status: "Packing berjalan",
      current_stage: 11,
      workflow: {
        ...completedThrough("cetak"),
        packing: "active",
      },
    };
    expect(statusStageDisplayNumber(row)).toBe(12);
    expect(workflowProgressPercent(row)).toBe(73);
    expect(KEYS.filter((k) => workflowCellState(row, k) === "completed")).toHaveLength(11);
    expect(workflowCellState(row, "packing")).toBe("active");
  });

  test("HOLD uses red strip and red hold box, percent still completed/15", () => {
    const row = {
      status: "HOLD",
      hold: true,
      current_stage: 4,
      workflow: wf({
        design: "completed",
        appr_design: "completed",
        payment: "completed",
        appr_payment: "hold",
      }),
    };
    expect(statusStripTone(row)).toBe("problem");
    expect(statusStageDisplayNumber(row)).toBe(5);
    expect(workflowCellState(row, "appr_payment")).toBe("hold");
    expect(progressSegmentTone(workflowCellState(row, "appr_payment"))).toBe("problem");
    expect(workflowProgressPercent(row)).toBe(27);
    expect(KEYS.filter((k) => workflowCellState(row, k) === "completed")).toHaveLength(4);
  });

  test("Selesai: 15 completed boxes, number 15, 100%, green strip", () => {
    const all = Object.fromEntries(KEYS.map((k) => [k, "completed"]));
    const row = { status: "Terkirim", current_stage: 14, workflow: all };
    expect(statusStageDisplayNumber(row)).toBe(15);
    expect(workflowProgressPercent(row)).toBe(100);
    expect(KEYS.every((k) => workflowCellState(row, k) === "completed")).toBe(true);
    expect(statusStripTone(row)).toBe("done");
  });

  test("skipped/not applicable counts with completed so percent matches green boxes", () => {
    const row = {
      status: "Packing",
      current_stage: 11,
      workflow: {
        ...completedThrough("sample"),
        cetak: "skipped",
        packing: "active",
      },
    };
    expect(workflowCellState(row, "cetak")).toBe("skipped");
    expect(progressSegmentTone("skipped")).toBe("skipped");
    expect(workflowProgressPercent(row)).toBe(73);
    expect(statusStageDisplayNumber(row)).toBe(12);
  });

  test("gappy evidence uses active box, not status-label / 15", () => {
    const row = {
      status: "Approval Payment",
      current_stage: 4,
      workflow: wf({ design: "pending", appr_design: "active" }),
    };
    expect(statusStageDisplayNumber(row)).toBe(3);
    expect(workflowProgressPercent(row)).toBe(7);
    expect(workflowCellState(row, "appr_design")).toBe("active");
  });

  test("fully delivered modern is 15 Terkirim with boxes 13-15 completed and 100%", () => {
    const row = {
      status: "Terkirim",
      current_stage: 14,
      workflow: Object.fromEntries(KEYS.map((k) => [k, "completed"])),
    };
    expect(statusStageDisplayNumber(row)).toBe(15);
    expect(`${statusStageDisplayNumber(row)} ${row.status}`).toBe("15 Terkirim");
    expect(`${statusStageDisplayNumber(row)} ${row.status}`).not.toBe("13 Terkirim");
    expect(workflowCellState(row, "sj")).toBe("completed");
    expect(workflowCellState(row, "loading")).toBe("completed");
    expect(workflowCellState(row, "kirim")).toBe("completed");
    expect(workflowProgressPercent(row)).toBe(100);
    expect(workflowCellCaption(STATUS_PO_WORKFLOW_STAGES[12], "completed")).toBe("13. Surat Jalan — Selesai");
    expect(workflowCellCaption(STATUS_PO_WORKFLOW_STAGES[13], "completed")).toBe("14. Loading — Selesai");
    expect(workflowCellCaption(STATUS_PO_WORKFLOW_STAGES[14], "completed")).toBe("15. Pengiriman — Selesai");
  });

  test("fully delivered legacy skips Loading and stays 15 Terkirim 100%", () => {
    const row = {
      status: "Terkirim",
      current_stage: 14,
      workflow: {
        ...Object.fromEntries(KEYS.map((k) => [k, "completed"])),
        loading: "skipped",
      },
      workflow_reasons: { loading: "legacy_no_loading_evidence" },
    };
    expect(statusStageDisplayNumber(row)).toBe(15);
    expect(`${statusStageDisplayNumber(row)} ${row.status}`).toBe("15 Terkirim");
    expect(workflowCellState(row, "loading")).toBe("skipped");
    expect(progressSegmentTone("skipped", "legacy_no_loading_evidence")).toBe("skipped");
    expect(workflowProgressPercent(row)).toBe(100);
    expect(workflowCellCaption(
      STATUS_PO_WORKFLOW_STAGES[13],
      "skipped",
      "legacy_no_loading_evidence",
    )).toBe("14. Loading — Dilewati (data lama, bukti Loading tidak tercatat)");
  });

  test("SJ without Loading never shows 13 Terkirim", () => {
    const row = {
      status: "Surat Jalan",
      current_stage: 12,
      workflow: {
        ...completedThrough("packing"),
        sj: "active",
        loading: "pending",
        kirim: "pending",
      },
    };
    expect(statusStageDisplayNumber(row)).toBe(13);
    expect(`${statusStageDisplayNumber(row)} ${row.status}`).toBe("13 Surat Jalan");
    expect(`${statusStageDisplayNumber(row)} ${row.status}`).not.toContain("Terkirim");
    expect(workflowCellState(row, "loading")).toBe("pending");
    expect(workflowCellState(row, "kirim")).toBe("pending");
    expect(workflowProgressPercent(row)).toBeLessThan(100);
    expect(workflowCellCaption(STATUS_PO_WORKFLOW_STAGES[13], "pending")).toBe("14. Loading — Belum dimulai");
    expect(workflowCellCaption(STATUS_PO_WORKFLOW_STAGES[14], "pending")).toBe("15. Pengiriman — Belum dimulai");
  });

  test("partial delivery stays Parsial Terkirim below 100%", () => {
    const row = {
      status: "Parsial Terkirim",
      current_stage: 14,
      workflow: {
        ...completedThrough("sj"),
        loading: "completed",
        kirim: "active",
      },
    };
    expect(statusStageDisplayNumber(row)).toBe(15);
    expect(`${statusStageDisplayNumber(row)} ${row.status}`).toBe("15 Parsial Terkirim");
    expect(workflowCellState(row, "kirim")).toBe("active");
    expect(workflowProgressPercent(row)).toBeLessThan(100);
  });
});
