import fs from "fs";
import path from "path";
import {
  confirmPackageKeysForSave,
  packageKeysToActivate,
  pruneConfirmReviewKeys,
  rolePermissionsPutBody,
} from "../lib/packagePermissions";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");

describe("Settings package confirm stale-key fix", () => {
  test("role save uses one patch and does not ask for a second approval", () => {
    expect(settingsSrc).not.toMatch(/confirmPackageKeysForSave/);
    expect(settingsSrc).not.toMatch(/confirm_opt_in_keys/);
    expect(settingsSrc).not.toMatch(/confirm_package_keys/);
    expect(settingsSrc).not.toMatch(/Tinjau izin/);
    expect(settingsSrc).toMatch(/adminPatchRoleBusinessPermissions/);
    expect(settingsSrc).toMatch(/Keep the draft/);
  });

  test("employee override save does not render a package approval panel", () => {
    expect(settingsSrc).not.toMatch(/emp-package-confirm-panel/);
    expect(settingsSrc).not.toMatch(/userPkgConfirmOptIn/);
    expect(settingsSrc).toMatch(/permissions_override = form\.permissions_override/);
  });

  test("stale A+B review with B unchecked produces successful A-only payload", () => {
    const reviewAB = ["purchase_requests.view", "purchase_orders.view"];
    const draftAfterUncheckB = ["purchase_requests.view", "purchase_hpp.view", "po.view", "nav.purchasing"];
    const pruned = pruneConfirmReviewKeys(reviewAB, draftAfterUncheckB);
    expect(pruned).toEqual(["purchase_requests.view"]);
    const confirmKeys = confirmPackageKeysForSave(draftAfterUncheckB, reviewAB, { optIn: true });
    expect(confirmKeys).toEqual(["purchase_requests.view"]);
    expect(confirmKeys).toEqual(packageKeysToActivate(draftAfterUncheckB, reviewAB));
    const body = rolePermissionsPutBody(draftAfterUncheckB, confirmKeys);
    expect(body.confirm_package_keys).toEqual(["purchase_requests.view"]);
    expect(body.confirm_package_keys).not.toContain("purchase_orders.view");
  });
});
