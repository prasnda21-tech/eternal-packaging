import fs from "fs";
import path from "path";
import {
  canShowLegacyPoBranchComplete,
  filterActiveBranchesForPoComplete,
} from "../lib/purchaseUi";

const page = fs.readFileSync(path.join(__dirname, "PesananPembelian.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

describe("PesananPembelian legacy PO Lengkapi Cabang", () => {
  test("API client maps PATCH purchase-orders/{id}/branch with branch_id + reason", () => {
    expect(apiSrc).toMatch(/setPurchaseOrderBranch:\s*\(id,\s*payload\)\s*=>/);
    expect(apiSrc).toMatch(/\.patch\(`\/purchase-orders\/\$\{encodeURIComponent\(id\)\}\/branch`/);
  });

  test("UI exposes Lengkapi Cabang gated by canShowLegacyPoBranchComplete + edit_branch perm", () => {
    expect(page).toMatch(/btn-lengkapi-cabang-po/);
    expect(page).toMatch(/Lengkapi Cabang/);
    expect(page).toMatch(/canShowLegacyPoBranchComplete\(detail,\s*canEditBranch\)/);
    expect(page).toMatch(/purchase_orders\.edit_branch/);
    expect(page).toMatch(/setPurchaseOrderBranch/);
    expect(page).toMatch(/po-lengkapi-cabang-modal/);
    expect(page).toMatch(/po-complete-branch-select/);
    expect(page).toMatch(/po-complete-branch-reason/);
    expect(page).toMatch(/branchSubmitLockRef/);
    expect(page).toMatch(/branchConfirmReady/);
  });

  test("legacy missing branch + perm → show; existing branch or no perm → hide", () => {
    expect(canShowLegacyPoBranchComplete({ id: "po-1", branch_id: null }, true)).toBe(true);
    expect(canShowLegacyPoBranchComplete({ id: "po-1", branch_id: "" }, true)).toBe(true);
    expect(canShowLegacyPoBranchComplete({ id: "po-1", branch_id: "br-ygy" }, true)).toBe(false);
    expect(canShowLegacyPoBranchComplete({ id: "po-1", branch_id: null }, false)).toBe(false);
    expect(canShowLegacyPoBranchComplete({ id: "po-1", branch_id: null, cancelled: true }, true)).toBe(false);
  });

  test("branch options keep only active rows with id", () => {
    const rows = filterActiveBranchesForPoComplete([
      { id: "a", code: "YGY", active: true },
      { id: "b", code: "SMG", active: false },
      { id: "", code: "X", active: true },
      { id: "c", code: "BDG" },
    ]);
    expect(rows.map((r) => r.id)).toEqual(["a", "c"]);
  });

  test("reason min length enforced before PATCH; confirm step before save", () => {
    expect(page).toMatch(/reason\.length < 3/);
    expect(page).toMatch(/Alasan perubahan wajib diisi \(minimal 3 karakter\)/);
    expect(page).toMatch(/if \(!branchConfirmReady\)/);
    expect(page).toMatch(/Simpan dan Lanjutkan/);
  });

  test("success refreshes detail from response order; 409/400 refreshes getPurchaseOrder", () => {
    expect(page).toMatch(/res\?\.order/);
    expect(page).toMatch(/getPurchaseOrder\(detail\.id\)/);
    expect(page).toMatch(/status === 400 \|\| status === 409/);
  });

  test("double-submit guarded by busy + submit lock", () => {
    expect(page).toMatch(/if \(branchBusy \|\| branchSubmitLockRef\.current/);
    expect(page).toMatch(/branchSubmitLockRef\.current = true/);
  });
});
