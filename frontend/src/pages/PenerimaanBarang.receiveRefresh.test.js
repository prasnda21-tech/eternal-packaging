import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "PenerimaanBarang.jsx"), "utf8");

describe("PO goods receipt post-save UI refresh", () => {
  test("successful save refreshes PO detail/list from API before leaving modal", () => {
    expect(page).toMatch(/refreshAfterPoGoodsReceipt/);
    expect(page).toMatch(/await refreshAfterPoGoodsReceipt\(poId\)/);
    expect(page).toMatch(/openPoReceivingDetail\(id\)/);
    expect(page).toMatch(/mergePurchaseOrderIntoList/);
    expect(page).toMatch(/setSearchParams\(\{\}, \{ replace: true \}\)/);
    // Must not leave only loadPoOrders without detail invalidation.
    expect(page).toMatch(/createPurchaseGoodsReceipt/);
    const saveIdx = page.indexOf("const savePoReceipt");
    const saveBlock = page.slice(saveIdx, saveIdx + 1800);
    expect(saveBlock).toMatch(/await refreshAfterPoGoodsReceipt\(poId\)/);
    expect(saveBlock).toMatch(/closeReceive\(\)/);
  });

  test("fully received PO does not offer another receive action", () => {
    expect(page).toMatch(/canOfferPurchaseReceive\(poDetail\)/);
    expect(page).toMatch(/btn-terima-po-\$\{poDetail\.id\}/);
  });
});
