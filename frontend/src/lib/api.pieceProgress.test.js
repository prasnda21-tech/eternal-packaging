import fs from "fs";
import path from "path";

describe("operator progress API payload", () => {
  const api = fs.readFileSync(path.join(__dirname, "api.js"), "utf8");

  test("work_date is forwarded and is not replaced with today", () => {
    expect(api).toMatch(/work_date: extra\.work_date \|\| undefined/);
    expect(api).toMatch(/action_id: extra\.action_id/);
    expect(api).not.toMatch(/work_date:\s*new Date/);
    expect(api).toMatch(/assignOperators: \(id, splits, actionId\) => client\.post\(`\/orders\/\$\{id\}\/assign-operators`, \{ splits, \.\.\.\(actionId \? \{ action_id: actionId \} : \{\}\) \}/);
  });
});