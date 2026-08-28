import fs from "fs";
import path from "path";

describe("rollback modal UI", () => {
  test("PO detail uses selective rollback modal with stable ids", () => {
    const detail = fs.readFileSync(path.join(__dirname, "../pages/PODetail.jsx"), "utf8");
    const modal = fs.readFileSync(path.join(__dirname, "RollbackModal.jsx"), "utf8");
    const lib = fs.readFileSync(path.join(__dirname, "../lib/rollbackTargets.js"), "utf8");
    expect(detail).toMatch(/RollbackModal/);
    expect(detail).toMatch(/selectiveRollbackBody/);
    expect(detail).toMatch(/api\.getOrder\(order\.id\)/);
    expect(detail).toMatch(/paused: busy \|\| showRollback/);
    expect(detail).toMatch(/to_stage/);
    expect(detail).not.toMatch(/Rollback ke tahap apa/);
    expect(detail).not.toMatch(/Ketik angka 0\.\.11/);
    expect(modal).toMatch(/ROLLBACK WORKFLOW/);
    expect(modal).toMatch(/rollback-nomor-list/);
    expect(modal).toMatch(/validateRollbackSelection/);
    expect(modal).toMatch(/rollback-check-/);
    expect(modal).toMatch(/rollback-stage-select/);
    expect(modal).toMatch(/Rollback sampai tahap/);
    expect(modal).toMatch(/Konfirmasi Rollback/);
    expect(modal).not.toMatch(/Ketik angka/);
    expect(modal).not.toMatch(/window\.prompt/);
    expect(lib).toMatch(/Pilih minimal satu nomor/);
    expect(lib).toMatch(/selectiveRollbackBody/);
    expect(lib).toMatch(/rollbackStageOptions/);
    expect(lib).toMatch(/from \".\/constants\"/);
    expect(lib).not.toMatch(/approval_design/);
  });
});
