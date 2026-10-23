import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "FakturPembelian.jsx"), "utf8");

describe("FakturPembelian LOC-C1 Cabang/Gudang UI", () => {
  test("create and detail show readonly Cabang and Gudang", () => {
    expect(page).toMatch(/create-branch/);
    expect(page).toMatch(/create-warehouse/);
    expect(page).toMatch(/detail-branch/);
    expect(page).toMatch(/detail-warehouse/);
    expect(page).toMatch(/purchaseBranchLabel/);
    expect(page).toMatch(/purchaseInvoiceWarehouseLabel/);
    expect(page).toMatch(/aria-readonly="true"/);
  });

  test("POST invoice does not send editable branch_id or warehouse_id", () => {
    const start = page.indexOf("createPurchaseInvoice({");
    expect(start).toBeGreaterThan(-1);
    const end = page.indexOf("});", start);
    const payload = page.slice(start, end);
    expect(payload).not.toMatch(/branch_id/);
    expect(payload).not.toMatch(/warehouse_id/);
    expect(payload).toMatch(/payment_term_days/);
    expect(payload).toMatch(/due_date/);
  });

  test("approved layout anchors remain (meta + six-column list)", () => {
    expect(page).toMatch(/fpb-meta-fields/);
    expect(page).toMatch(/fpb-final/);
    expect(page).toMatch(/invoice-term-presets/);
    const tableStart = page.indexOf('data-testid="invoices-table"');
    const tableEnd = page.indexOf("</table>", tableStart);
    const table = page.slice(tableStart, tableEnd);
    expect(table).toMatch(/Umur Faktur/);
    expect(table).toMatch(/Jatuh Tempo/);
    expect(table).toMatch(/Sisa Tagihan/);
  });
});
