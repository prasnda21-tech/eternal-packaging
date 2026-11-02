import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");

describe("Pesanan Penjualan create sales catalog contract", () => {
  test("create picker uses Master Sales catalog only", () => {
    expect(src).toMatch(/api\.salesMasterCatalog/);
    expect(src).toMatch(/sales_master_catalog/);
    expect(src).toMatch(/sales_master_id: sid \|\| ""/);
    expect(src).toMatch(/sales: hit\?\.sales_name \|\| ""/);
  });

  test("does not fall back to sales_options or free-text for create", () => {
    expect(src).not.toMatch(/options\.sales_options \|\| \[\]\)\.map\(\(name\) => \(\{ id: name/);
    expect(src).not.toMatch(/placeholder="mis\. Vira"/);
    expect(src).toMatch(/so-sales-catalog-empty/);
    expect(src).toMatch(/Input nama bebas tidak tersedia/);
  });

  test("list filters may still read sales_options snapshots", () => {
    expect(src).toMatch(/salesFilterOptions/);
    expect(src).toMatch(/options\.sales_options/);
  });
});
