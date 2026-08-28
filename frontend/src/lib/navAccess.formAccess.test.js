import fs from "fs";
import path from "path";
import { actionKeyToTaskPath, TASK_NAV_PATHS } from "./navAccess";

describe("actionKeyToTaskPath multi-form", () => {
  test("loading goes to loading tasks", () => {
    expect(actionKeyToTaskPath("loading")).toBe(TASK_NAV_PATHS.loading);
  });

  test("design approve stays on dashboard list not /tasks", () => {
    expect(actionKeyToTaskPath("design_approve")).toBe(null);
  });

  test("helper and packing remain distinct", () => {
    expect(actionKeyToTaskPath("helper")).toBe(TASK_NAV_PATHS.helper);
    expect(actionKeyToTaskPath("packing")).toBe(TASK_NAV_PATHS.packing);
  });
});

describe("navAccess source does not hardcode helper-only packing", () => {
  const src = fs.readFileSync(path.join(__dirname, "navAccess.js"), "utf8");
  test("submit perms map packing independently", () => {
    expect(src).toMatch(/packing:\s*"packing\.update"/);
    expect(src).toMatch(/loading:\s*"loading\.update"/);
    expect(src).not.toMatch(/role\s*===\s*["']helper["']/);
  });
});
