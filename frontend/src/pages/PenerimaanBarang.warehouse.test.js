import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "PenerimaanBarang.jsx"), "utf8");

describe("canonical goods receipt warehouse", () => {
  test("sends an explicit warehouse and keeps partial receipt quantities", () => {
    expect(page).toMatch(/goodsReceiptWarehouseChoiceForPo/);
    expect(page).toMatch(/warehouse_id: recvWarehouseId/);
    expect(page).toMatch(/data-testid="receive-warehouse"/);
    expect(page).toMatch(/Pilih gudang sebelum menyimpan penerimaan|goodsReceiptWarehouseBlockMessage/);
    expect(page).toMatch(/belum ada gudang aktif|legacy_branch_missing/);
    expect(page).toMatch(/data-testid="receive-qty-input"/);
    expect(page).toMatch(/remaining_receivable_qty/);
    expect(page).toMatch(/createPurchaseGoodsReceipt/);
  });
});
