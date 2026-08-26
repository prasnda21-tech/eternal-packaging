import fs from "fs";
import path from "path";

describe("OperatorMultiSelect source", () => {
  const src = fs.readFileSync(path.join(__dirname, "OperatorMultiSelect.jsx"), "utf8");
  test("select all uses full dataset not search results", () => {
    expect(src).toMatch(/nextSelectAll\(draft, allIds\)/);
    expect(src).not.toMatch(/nextSelectAll\(draft, visible/);
    expect(src).toMatch(/searchOperators\(operators, query\)/);
  });
});
