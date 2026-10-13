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
  test("RolesTab prunes review keys and saves preview===payload", () => {
    expect(settingsSrc).toMatch(/pruneConfirmReviewKeys\(prev, permsDraft\)/);
    expect(settingsSrc).toMatch(/confirmPackageKeysForSave\(permissions, confirmReviewKeys/);
    expect(settingsSrc).toMatch(/rolePermissionsPutBody\(permissions, preview\)/);
    expect(settingsSrc).toMatch(/Re-check does not restore prior confirm/);
    expect(settingsSrc).toMatch(/Keep draft \+ confirm UI/);
  });

  test("EmployeeForm resets confirm on user change and prunes override grants", () => {
    expect(settingsSrc).toMatch(/setUserPkgConfirmOptIn\(false\)/);
    expect(settingsSrc).toMatch(/pruneConfirmReviewKeys\(prev, userOverridePackageKeys\)/);
    expect(settingsSrc).toMatch(/confirmPackageKeysForSave\(\s*userOverridePackageKeys/);
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
