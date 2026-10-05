import fs from "fs";
import path from "path";

describe("Gaji Operator page", () => {
  const src = fs.readFileSync(path.join(__dirname, "GajiOperator.jsx"), "utf8");
  const ms = fs.readFileSync(path.join(__dirname, "..", "components", "OperatorMultiSelect.jsx"), "utf8");

  test("has date filter export rate columns and checkbox multi-select", () => {
    expect(src).toMatch(/gaji-filter-from/);
    expect(src).toMatch(/gaji-filter-to/);
    expect(src).toMatch(/gaji-export/);
    expect(src).toMatch(/Upah Eligible/);
    expect(src).toMatch(/Tambahan Produk/);
    expect(src).toMatch(/Bonus Usulan/);
    expect(src).toMatch(/Total Payroll/);
    expect(src).toMatch(/Estimasi\/Usulan Total/);
    expect(src).toMatch(/gaji-bonus-usulan-/);
    expect(src).toMatch(/gaji-bonus-final-/);
    expect(src).toMatch(/gaji-total-payroll-/);
    expect(src).toMatch(/gaji-estimasi-total-/);
    expect(src).toMatch(/OperatorMultiSelect/);
    expect(src).toMatch(/operator_ids/);
    expect(src).toMatch(/gaji-unset-warning/);
    expect(src).toMatch(/Belum Payroll/);
    expect(src).toMatch(/Held/);
    expect(src).toMatch(/Posted/);
    expect(src).toMatch(/Tidak Eligible/);
    expect(src).toMatch(/payroll_reason/);
    expect(src).toMatch(/gaji-bonus-note/);
    expect(src).not.toMatch(/<select value=\{operatorId\}/);
  });

  test("compact dropdown keeps checkboxes search select-all apply clear", () => {
    expect(ms).toMatch(/gaji-filter-operator-all/);
    expect(ms).toMatch(/type="checkbox"/);
    expect(ms).toMatch(/gaji-filter-operator-search/);
    expect(ms).toMatch(/gaji-filter-operator-trigger/);
    expect(ms).toMatch(/Pilih Semua Operator/);
    expect(ms).toMatch(/Bersihkan/);
    expect(ms).toMatch(/Terapkan/);
    expect(ms).toMatch(/indeterminate/);
    expect(ms).toMatch(/nextSelectAll\(draft, allIds\)/);
    expect(ms).toMatch(/searchOperators\(operators, query\)/);
  });

  test("product filter is live searchable facet select not native scroll select", () => {
    expect(src).toMatch(/FacetSearchSelect/);
    expect(src).toMatch(/gaji-filter-item/);
    expect(src).toMatch(/gajiFilterItemValue/);
    expect(src).not.toMatch(/<select value=\{item\}/);
    expect(src).toMatch(/item_code \|\| it\.item_name/);
  });
});
