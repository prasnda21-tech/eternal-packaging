import fs from "fs";
import path from "path";

describe("Sistem Upah Operator settings", () => {
  const src = fs.readFileSync(path.join(__dirname, "OperatorWageSettings.jsx"), "utf8");

  test("three compact sections with manual entry and no hardcoded production rates", () => {
    expect(src).toMatch(/wage-base-section/);
    expect(src).toMatch(/wage-bonus-section/);
    expect(src).toMatch(/wage-product-section/);
    expect(src).toMatch(/Upah Dasar Operator/);
    expect(src).toMatch(/Bonus Operator/);
    expect(src).toMatch(/Tambahan Upah Produk Khusus/);
    expect(src).toMatch(/Berlaku Mulai/);
    expect(src).toMatch(/once_per_period/);
    expect(src).toMatch(/per_target_block/);
    expect(src).toMatch(/wage-bonus-target/);
    expect(src).toMatch(/wage-bonus-amount/);
    expect(src).toMatch(/Isi manual/);
    expect(src).toMatch(/Belum Disetel/);
    expect(src).not.toMatch(/default_rate_per_pcs:\s*20/);
    expect(src).not.toMatch(/target_qty:\s*5000/);
    expect(src).not.toMatch(/amount:\s*10000/);
    expect(src).not.toMatch(/placeholder=["']20/);
    expect(src).not.toMatch(/placeholder=["']5000/);
  });

  test("product addition uses stable ids not name matching", () => {
    expect(src).toMatch(/accurate_item_id/);
    expect(src).toMatch(/master_item_id/);
    expect(src).toMatch(/additional_rate_per_pcs/);
    expect(src).toMatch(/rate_type: o.rate_type \|\| "override"/);
  });
});
