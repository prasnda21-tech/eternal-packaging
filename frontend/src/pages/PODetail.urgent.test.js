import fs from "fs";
import path from "path";

describe("PODetail urgent + payment proof wiring", () => {
  const src = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");

  test("urgent control stays permission-gated and calls setUrgent", () => {
    expect(src).toMatch(/canManageUrgentPerm\(hasPerm\)/);
    expect(src).toMatch(/toggle-urgent-button/);
    expect(src).toMatch(/api\.setUrgent\(order\.id, order\.priority !== "urgent"\)/);
    expect(src).not.toMatch(/require\("admin", "head_production"\)/);
  });

  test("payment files open through the in-app viewer", () => {
    expect(src).toMatch(/InAppDocumentViewer/);
    expect(src).toMatch(/dataUrlToBlob/);
    expect(src).toMatch(/downloadSalesReceiptPaymentProof/);
    expect(src).toMatch(/payment-files-list/);
    expect(src).not.toMatch(/window\.open/);
    expect(src).not.toMatch(/openPaymentProofSource/);
  });
});
