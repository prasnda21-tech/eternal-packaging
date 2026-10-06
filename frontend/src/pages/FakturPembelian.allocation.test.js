import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "FakturPembelian.jsx"), "utf8");
const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

test("invoice can allocate selected supplier advances without touching stock", () => {
  expect(page).toMatch(/Gunakan Uang Muka/);
  expect(page).toMatch(/data-testid="eligible-advance-row"/);
  expect(page).toMatch(/data-testid="eligible-advance-available"/);
  expect(page).toMatch(/data-testid="alloc-amount"/);
  expect(page).toMatch(/Nilai alokasi melebihi uang muka tersedia atau sisa tagihan/);
  expect(page).toMatch(/data-testid="detail-advance-applied"/);
  expect(page).toMatch(/Uang Muka Dialokasikan/);
  expect(page).toMatch(/Sisa Tagihan/);
  expect(page).toMatch(/data-testid="btn-ajukan-pembayaran-inv-detail"/);
  expect(page).toMatch(/Pilih uang muka/);
  expect(page).not.toMatch(/eligibleAdvances\.forEach\(\(row\) => api\.createPurchaseSupplierAdvanceAllocation/);
  expect(page).not.toMatch(/physical_qty/);
  expect(api).toMatch(/createPurchaseSupplierAdvanceAllocation:/);
});
