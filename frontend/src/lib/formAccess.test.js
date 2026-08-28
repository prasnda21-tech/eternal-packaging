import {
  FORM_ACCESS_DEFS,
  applyFormToggle,
  formsForRole,
  payloadForRoleSave,
  roleHasForm,
  rolesForForm,
} from "./formAccess";

describe("form access many-to-many over existing RBAC", () => {
  test("reuses existing permission keys", () => {
    const byId = Object.fromEntries(FORM_ACCESS_DEFS.map((f) => [f.id, f.perm]));
    expect(byId.helper).toBe("helper.prepare");
    expect(byId.packing).toBe("packing.update");
    expect(byId.loading).toBe("loading.update");
    expect(byId.design_approve).toBe("design.approve");
  });

  test("one role can hold many forms", () => {
    let perms = [];
    perms = applyFormToggle(perms, "helper", true);
    perms = applyFormToggle(perms, "packing", true);
    expect(roleHasForm(perms, "helper")).toBe(true);
    expect(roleHasForm(perms, "packing")).toBe(true);
    expect(roleHasForm(perms, "loading")).toBe(false);
    expect(formsForRole(perms).sort()).toEqual(["helper", "packing"]);
  });

  test("one form can be granted to many roles without role-name hardcoding", () => {
    const helper = applyFormToggle(["helper.prepare"], "packing", true);
    const operator = applyFormToggle(["operator.update"], "packing", true);
    const custom = applyFormToggle([], "packing", true);
    const roles = [
      { role: "helper", permissions: helper },
      { role: "operator", permissions: operator },
      { role: "qc_lead", permissions: custom },
      { role: "driver", permissions: ["loading.update"] },
    ];
    expect(rolesForForm(roles, "packing").sort()).toEqual(["helper", "operator", "qc_lead"]);
  });

  test("loading stays optional until granted", () => {
    const perms = applyFormToggle(["helper.prepare", "packing.update"], "loading", false);
    expect(roleHasForm(perms, "loading")).toBe(false);
    expect(applyFormToggle(perms, "loading", true).includes("loading.update")).toBe(true);
  });

  test("approve design uses existing design.approve key", () => {
    const perms = applyFormToggle(["po.view"], "design_approve", true);
    expect(perms).toContain("design.approve");
    expect(perms).toContain("design.view");
  });

  test("payload keeps nav.configured when nav keys exist", () => {
    const perms = applyFormToggle([], "packing", true);
    const payload = payloadForRoleSave(perms);
    expect(payload).toContain("packing.update");
    expect(payload).toContain("nav.tasks.packing");
    expect(payload).toContain("nav.configured");
  });
});
