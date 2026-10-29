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

  test("list uses compact SO table-scroll fill and nowrap refresh", () => {
    expect(src).toMatch(/so-list-table-scroll/);
    expect(src).toMatch(/pra-po-table-scroll/);
    expect(src).toMatch(/pra-po-refresh-label/);
    expect(src).toMatch(/so-list-summary/);
    expect(src).toMatch(/pra-po-empty-inner/);
    expect(src).not.toMatch(/so-list-table-wrap/);
  });

  test("create form imports SO ux90 create CSS and binds form tab panes", () => {
    expect(src).toMatch(/ux90SalesCreate\.css/);
    expect(src).toMatch(/data-form-tab=\{createTab\}/);
    expect(src).toMatch(/data-create-tab=\{createTab\}/);
    expect(src).toMatch(/inert=\{createTab !== "items"/);
    expect(src).toMatch(/so-ship-panel/);
  });

  test("create form shows order total and new-customer affordance", () => {
    expect(src).toMatch(/orderSellingTotal/);
    expect(src).toMatch(/pra-po-order-total/);
    expect(src).toMatch(/pra-po-new-customer/);
    expect(src).toMatch(/pra-po-customer-card/);
    expect(src).toMatch(/Rincian Barang/);
  });

  test("create CSS keeps customer editor compact and pinned action bar", () => {
    const css = fs.readFileSync(path.join(__dirname, "praPo.css"), "utf8");
    expect(css).toMatch(/pra-po-customer-editor \.mis-root/);
    expect(css).toMatch(/height:\s*auto\s*!important/);
    expect(css).toMatch(/so-create-form[\s\S]*height:\s*100%\s*!important/);
    expect(css).toMatch(/so-create-actionbar[\s\S]*flex:\s*0\s+0\s+auto/);
    expect(css).toMatch(/white-space:\s*nowrap\s*!important/);
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
