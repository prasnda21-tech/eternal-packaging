import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "PraPo.jsx"), "utf8");

describe("Pra PO page source contract — SO parity", () => {
  test("uses CustomerSearchSelect and MasterItemSearchSelect", () => {
    expect(src).toMatch(/CustomerSearchSelect/);
    expect(src).toMatch(/MasterItemSearchSelect/);
    expect(src).toMatch(/api\.salesCatalogProducts/);
  });

  test("does not render editable raw master_item_id field", () => {
    expect(src).not.toMatch(/placeholder="master_item_id"/);
    expect(src).not.toMatch(/data-testid=\{`pra-po-line-master-/);
  });

  test("mirrors sales-order workspace classes", () => {
    expect(src).toMatch(/so-create-workspace/);
    expect(src).toMatch(/so-list-workspace/);
    expect(src).toMatch(/so-create-lines-table/);
    expect(src).toMatch(/pra-po-tab-items/);
    expect(src).toMatch(/pra-po-tab-ship/);
  });

  test("list uses server pagination params and controls", () => {
    expect(src).toMatch(/page_size/);
    expect(src).toMatch(/pra-po-pagination/);
    expect(src).toMatch(/pra-po-page-next/);
    expect(src).toMatch(/params\.q/);
  });

  test("admin verify/return and CRM submit actions present", () => {
    expect(src).toMatch(/pra-po-verify/);
    expect(src).toMatch(/pra-po-return/);
    expect(src).toMatch(/pra-po-submit/);
    expect(src).toMatch(/Ajukan Ulang/);
    expect(src).toMatch(/Simpan Draft/);
    expect(src).not.toMatch(/btn-submit-sales-order/);
  });

  test("returned reason and line editing controls present", () => {
    expect(src).toMatch(/pra-po-return-reason/);
    expect(src).toMatch(/pra-po-line-price-/);
    expect(src).toMatch(/pra-po-line-qty-/);
    expect(src).toMatch(/pra-po-add-line/);
  });
});
