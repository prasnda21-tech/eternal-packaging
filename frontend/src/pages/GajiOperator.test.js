import fs from "fs";
import path from "path";

describe("Gaji Operator page", () => {
  const src = fs.readFileSync(path.join(__dirname, "GajiOperator.jsx"), "utf8");
  test("has date filter export rate columns and checkbox multi-select", () => {
    expect(src).toMatch(/gaji-filter-from/);
    expect(src).toMatch(/gaji-filter-to/);
    expect(src).toMatch(/gaji-export/);
    expect(src).toMatch(/Rate \/ pcs/);
    expect(src).toMatch(/Total Upah/);
    expect(src).toMatch(/gaji-filter-operator-all/);
    expect(src).toMatch(/type="checkbox"/);
    expect(src).not.toMatch(/<select value=\{operatorId\}/);
    expect(src).toMatch(/operator_ids/);
  });
});
