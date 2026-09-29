import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "SalesUserMappingSettings.jsx"), "utf8");

describe("SalesUserMappingSettings", () => {
  test("maps exact Accurate label to users.id for sales, holding, and director", () => {
    expect(src).toMatch(/orders\.sales/);
    expect(src).toMatch(/users\.id/);
    expect(src).toMatch(/sales_eternal/);
    expect(src).toMatch(/holding_team/);
    expect(src).toMatch(/direktur_utama/);
    expect(src).toMatch(/status === "inactive"/);
    expect(src).toMatch(/tidak mengubah role atau hak akses/);
    expect(src).toMatch(/updateSalesUserMapping/);
    expect(src).toMatch(/Tidak ada fuzzy match/);
    expect(src).not.toMatch(/toLowerCase\(\)/);
  });
});
