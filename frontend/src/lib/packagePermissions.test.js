import {
  DIRECTOR_FULL_ACCESS_ROLE,
  PACKAGE_PERMISSIONS,
  confirmPackageKeysForSave,
  isDirectorFullAccessRole,
  isPackagePermissionKey,
  normalizePackageConfirmedKeys,
  packageKeysFromOverride,
  packageKeysInGrant,
  packageKeysToActivate,
  pruneConfirmReviewKeys,
  rolePermissionsPutBody,
  unconfirmedPackageKeys,
  userOverridePutBody,
} from "./packagePermissions";

describe("packagePermissions", () => {
  test("Direktur identity helper", () => {
    expect(isDirectorFullAccessRole(DIRECTOR_FULL_ACCESS_ROLE)).toBe(true);
    expect(isDirectorFullAccessRole("Direktur_Utama")).toBe(true);
    expect(isDirectorFullAccessRole("admin")).toBe(false);
    expect(isDirectorFullAccessRole("holding_team")).toBe(false);
  });

  test("catalog covers purchasing/inventory package keys", () => {
    expect(PACKAGE_PERMISSIONS).toContain("purchase_hpp.view");
    expect(PACKAGE_PERMISSIONS).toContain("nav.purchasing.advances");
    expect(PACKAGE_PERMISSIONS).toContain("nav.purchasing.returns");
    expect(PACKAGE_PERMISSIONS).toContain("current_stock.update");
    expect(isPackagePermissionKey("po.view")).toBe(false);
  });

  test("ordinary put body omits confirm_package_keys (legacy editor safe)", () => {
    expect(rolePermissionsPutBody(["po.view", "purchase_hpp.view"])).toEqual({
      permissions: ["po.view", "purchase_hpp.view"],
    });
    expect(userOverridePutBody({ "purchase_hpp.view": true })).toEqual({
      permissions_override: { "purchase_hpp.view": true },
    });
  });

  test("explicit confirm payload lists only reviewed keys", () => {
    const body = rolePermissionsPutBody(
      ["po.view", "purchase_hpp.view", "nav.purchasing"],
      ["purchase_hpp.view"],
    );
    expect(body.confirm_package_keys).toEqual(["purchase_hpp.view"]);
    expect(body.permissions).toContain("nav.purchasing");
  });

  test("revoking C does not imply activation of A+B", () => {
    const grant = ["purchase_requests.view", "purchase_orders.view", "po.view"];
    const confirmed = [];
    expect(unconfirmedPackageKeys(grant, confirmed)).toEqual([
      "purchase_orders.view",
      "purchase_requests.view",
    ]);
    const ordinary = rolePermissionsPutBody(["purchase_requests.view", "purchase_orders.view", "po.view"]);
    expect(ordinary.confirm_package_keys).toBeUndefined();
    expect(packageKeysToActivate(grant, ["purchase_requests.view"])).toEqual(["purchase_requests.view"]);
    expect(packageKeysToActivate(grant, ["purchase_requests.view"])).not.toContain("purchase_orders.view");
  });

  test("confirm of reviewed subset does not activate keys outside list", () => {
    const grant = packageKeysInGrant([
      "purchase_hpp.view",
      "current_stock.view",
      "nav.purchasing",
      "po.view",
    ]);
    expect(packageKeysToActivate(grant, ["purchase_hpp.view"])).toEqual(["purchase_hpp.view"]);
    expect(packageKeysToActivate(grant, ["purchase_hpp.view", "unknown.x"])).toEqual(["purchase_hpp.view"]);
  });

  test("normalize drops unknown keys", () => {
    expect([...normalizePackageConfirmedKeys(["po.view", "purchase_hpp.view"])].sort()).toEqual([
      "purchase_hpp.view",
    ]);
  });

  test("user override package keys only track Grant=true", () => {
    expect(
      packageKeysFromOverride({
        "purchase_hpp.view": true,
        "current_stock.view": false,
        "po.view": true,
      }),
    ).toEqual(["purchase_hpp.view"]);
  });

  test("prune drops unselected B; re-select does not auto-restore confirm", () => {
    const reviewed = ["purchase_requests.view", "purchase_orders.view"];
    const afterUncheckB = pruneConfirmReviewKeys(reviewed, ["purchase_requests.view", "po.view"]);
    expect(afterUncheckB).toEqual(["purchase_requests.view"]);
    // B granted again but not re-reviewed
    const afterRecheckB = pruneConfirmReviewKeys(afterUncheckB, [
      "purchase_requests.view",
      "purchase_orders.view",
      "po.view",
    ]);
    expect(afterRecheckB).toEqual(["purchase_requests.view"]);
    expect(afterRecheckB).not.toContain("purchase_orders.view");
  });

  test("new grant C does not auto-enter confirm set", () => {
    const reviewed = ["purchase_requests.view"];
    const grantWithC = [
      "purchase_requests.view",
      "purchase_hpp.view",
      "po.view",
    ];
    expect(pruneConfirmReviewKeys(reviewed, grantWithC)).toEqual(["purchase_requests.view"]);
    expect(confirmPackageKeysForSave(grantWithC, reviewed, { optIn: true })).toEqual([
      "purchase_requests.view",
    ]);
  });

  test("preview list equals confirm_package_keys on save body", () => {
    const permissions = [
      "purchase_requests.view",
      "purchase_orders.view",
      "purchase_hpp.view",
      "po.view",
    ];
    // Review A+B then uncheck B from grant → only A
    const review = ["purchase_requests.view", "purchase_orders.view"];
    const grantAfterUncheckB = ["purchase_requests.view", "purchase_hpp.view", "po.view"];
    const preview = packageKeysToActivate(grantAfterUncheckB, review);
    const confirmKeys = confirmPackageKeysForSave(grantAfterUncheckB, review, { optIn: true });
    expect(preview).toEqual(["purchase_requests.view"]);
    expect(confirmKeys).toEqual(preview);
    const body = rolePermissionsPutBody(permissions.filter((k) => grantAfterUncheckB.includes(k) || k === "po.view"), confirmKeys);
    expect(body.confirm_package_keys).toEqual(preview);
    expect(confirmPackageKeysForSave(grantAfterUncheckB, review, { optIn: false })).toBeUndefined();
  });

  test("role/user switch clears confirm via empty review seed", () => {
    expect(pruneConfirmReviewKeys(["purchase_hpp.view"], [])).toEqual([]);
    expect(confirmPackageKeysForSave(["purchase_hpp.view"], [], { optIn: true })).toEqual([]);
  });
});
