import {
  discrepancyDiff,
  validateDiscrepancySelection,
  loadingVerificationStatus,
  canManageLoadingActions,
  canViewLoading,
  LOADING_CONFIRM_MATCH_PERM,
  LOADING_REPORT_DISCREPANCY_PERM,
  LOADING_VIEW_PERM,
} from "./loadingConfirm";

describe("Loading SJ verification helpers", () => {
  test("discrepancyDiff classifies kurang and lebih", () => {
    const short = discrepancyDiff(4000, 3000);
    expect(short.ok).toBe(true);
    expect(String(short.classification || short.label || "")).toMatch(/kurang/i);
    const over = discrepancyDiff(4000, 4500);
    expect(over.ok).toBe(true);
    expect(String(over.classification || over.label || "")).toMatch(/lebih/i);
    expect(discrepancyDiff(4000, 4000).ok).toBe(false);
    expect(discrepancyDiff(4000, -1).ok).toBe(false);
    expect(discrepancyDiff(4000, "").ok).toBe(false);
  });

  test("validateDiscrepancySelection requires genuine mismatches only on marked lines", () => {
    const lines = [
      { line_idx: 0, name: "Cup", qty_sj: 5000 },
      { line_idx: 1, name: "LID", qty_sj: 4000 },
    ];
    expect(validateDiscrepancySelection({}, lines).ok).toBe(false);
    expect(validateDiscrepancySelection({ 1: { marked: true, actual_qty: "4000" } }, lines).ok).toBe(false);
    const ok = validateDiscrepancySelection({ 1: { marked: true, actual_qty: "3000" } }, lines);
    expect(ok.ok).toBe(true);
    expect(ok.mismatches).toEqual([{ line_idx: 1, actual_qty: 3000 }]);
  });

  test("permission helpers keep view separate from mutate", () => {
    const has = (k) => k === LOADING_VIEW_PERM;
    expect(canViewLoading(has)).toBe(true);
    expect(canManageLoadingActions(has)).toBe(false);
    const mut = (k) => k === LOADING_CONFIRM_MATCH_PERM || k === LOADING_REPORT_DISCREPANCY_PERM;
    expect(canManageLoadingActions(mut)).toBe(true);
  });

  test("loadingVerificationStatus surfaces held mismatch", () => {
    const order = {
      loading_discrepancies: [{ id: "d1", status: "open", surat_jalan_id: "sj1", lines: [] }],
      surat_jalan_files: [{ id: "sj1", lines: [{ line_idx: 0, qty: 1 }] }],
    };
    const st = loadingVerificationStatus(order, "sj1");
    expect(String(st.label || "")).toMatch(/Tertahan|Sesuai SJ|Tidak Sesuai/i);
  });
});
