import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "SalesUserMappingSettings.jsx"), "utf8");

describe("SalesUserMappingSettings", () => {
  test("maps exact Accurate label to users.id and only sales_eternal targets", () => {
    expect(src).toMatch(/orders\.sales/);
    expect(src).toMatch(/users\.id/);
    expect(src).toMatch(/role === "sales_eternal"/);
    expect(src).toMatch(/updateSalesUserMapping/);
    expect(src).toMatch(/Tidak ada fuzzy match/);
    expect(src).not.toMatch(/toLowerCase\(\)/);
  });
});
