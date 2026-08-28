import { STAGES } from "./constants";
import { lineStableId, rollbackStageOptions, rollbackTargets, selectiveRollbackBody, validateRollbackSelection, validateRollbackStage } from "./rollbackTargets";

const threeSplits = {
  operator_splits: [
    { id: "split-1", operator_name: "Budi", line_idx: 0, qty_completed: 3000, progress_entries: [{ qty: 3000 }] },
    { id: "split-2", operator_name: "Andi", line_idx: 1, qty_completed: 4000, progress_entries: [{ qty: 4000 }] },
    { id: "split-3", operator_name: "Cici", line_idx: 2, qty_completed: 2000, progress_entries: [{ qty: 2000 }] },
  ],
  line_items: [
    { id: "line-a", name: "Cup A" },
    { id: "line-b", name: "Cup B" },
    { id: "line-c", name: "Cup C" },
  ],
};

describe("rollbackTargets", () => {
  test("uses split id not display index", () => {
    const rows = rollbackTargets(threeSplits);
    expect(rows.map((r) => r.nomor)).toEqual([1, 2, 3]);
    expect(rows.map((r) => r.id)).toEqual(["split-1", "split-2", "split-3"]);
    expect(rows[1].label).toMatch(/Andi/);
  });

  test("empty selection is invalid", () => {
    expect(validateRollbackSelection([])).toBe("Pilih minimal satu nomor.");
    expect(validateRollbackSelection(["split-2"])).toBe("");
  });

  test("lineStableId prefers id over index", () => {
    expect(lineStableId(threeSplits, 1)).toBe("line-b");
  });

  test("does not fall back to stage slugs", () => {
    const rows = rollbackTargets({
      design_status: "Approved",
      design_files: [{ id: "d1" }],
      payment_status: "Approved",
      payment_files: [{ id: "p1" }],
      operator_splits: [],
      line_items: [],
    });
    expect(rows.map((r) => r.id)).not.toEqual(expect.arrayContaining(["design", "approval_design", "payment"]));
    expect(rows).toEqual([]);
  });

  test("splits without id use unique line accurate_item_id", () => {
    const rows = rollbackTargets({
      operator_splits: [
        { operator_name: "Rafi", line_idx: 1, qty_completed: 4000 },
      ],
      line_items: [
        { accurate_item_id: "101", name: "Cup A" },
        { accurate_item_id: "202", name: "Cup B" },
      ],
    });
    expect(rows.map((r) => r.id)).toEqual(["item:101", "item:202"]);
    expect(rows[1].filled).toBe(true);
  });

  test("selectiveRollbackBody sends ids, to_stage, and reason", () => {
    const packing = STAGES.indexOf("Packing");
    const body = selectiveRollbackBody(["split-2"], "koreksi qty", packing);
    expect(body).toEqual({ ids: ["split-2"], to_stage: packing, reason: "koreksi qty" });
  });

  test("selective dropdown uses STAGES through Packing only", () => {
    const packing = STAGES.indexOf("Packing");
    const terkirim = STAGES.indexOf("Terkirim");
    const opts = rollbackStageOptions(terkirim);
    expect(opts.map((o) => o.label)).toEqual(STAGES.slice(0, packing + 1));
    expect(opts[opts.length - 1].label).toBe("Packing");
    expect(opts.some((o) => o.label === "Surat Jalan")).toBe(false);
    expect(opts.some((o) => o.label === "Loading")).toBe(false);
    expect(opts.some((o) => o.label === "Terkirim")).toBe(false);
    expect(opts.every((o) => !/\d/.test(o.label))).toBe(true);
    expect(rollbackStageOptions(packing).some((o) => o.label === "Packing")).toBe(false);
  });

  test("validateRollbackStage requires a selected stage", () => {
    expect(validateRollbackStage("")).toBe("Pilih tahap tujuan rollback.");
    expect(validateRollbackStage(STAGES.indexOf("Packing"))).toBe("");
    expect(validateRollbackStage(STAGES.indexOf("Surat Jalan"))).toBe("Pilih tahap tujuan rollback.");
  });
});
