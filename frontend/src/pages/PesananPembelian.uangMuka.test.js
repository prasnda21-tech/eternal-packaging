import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "PesananPembelian.jsx"), "utf8");
const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

test("PO detail exposes Uang Muka without invoice allocation", () => {
  expect(page).toMatch(/data-testid="po-uang-muka"/);
  expect(page).toMatch(/Uang Muka/);
  expect(page).toMatch(/data-testid="uang-muka-amount"/);
  expect(page).toMatch(/data-testid="uang-muka-status"/);
  expect(page).toMatch(/data-testid="uang-muka-available"/);
  expect(page).toMatch(/data-testid="uang-muka-row"/);
  expect(page).toMatch(/Tersedia \/ Belum Dialokasikan/);
  expect(page).not.toMatch(/Gunakan Uang Muka/);
  expect(page).toMatch(/createPurchaseSupplierAdvance/);
  expect(page).toMatch(/purchase_order_id: detail\.id/);
  expect(page).not.toMatch(/physical_qty/);
  expect(api).toMatch(/createPurchaseSupplierAdvance:/);
  expect(api).toMatch(/\/purchase-supplier-advances/);
});
