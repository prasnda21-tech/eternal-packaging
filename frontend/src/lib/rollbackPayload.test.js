import fs from "fs";
import path from "path";
import { selectiveRollbackBody } from "../lib/rollbackTargets";

describe("rollback API payload", () => {
  test("selective body includes ids, to_stage, and reason", () => {
    const body = selectiveRollbackBody(["abc-uuid"], "preview", 11);
    expect(body).toEqual({ ids: ["abc-uuid"], to_stage: 11, reason: "preview" });
  });

  test("api client treats object payload as body not to_stage", () => {
    const src = fs.readFileSync(path.join(__dirname, "api.js"), "utf8");
    expect(src).toMatch(/!Array\.isArray\(to_stageOrPayload\)/);
    expect(src).toMatch(/\/orders\/\$\{id\}\/rollback/);
    expect(src).not.toMatch(/debugRollbackUi/);
  });
});
