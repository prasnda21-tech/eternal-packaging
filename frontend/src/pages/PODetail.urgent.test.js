import fs from "fs";
import path from "path";

describe("PODetail urgent approval wiring", () => {
  const src = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");

  test("urgent control opens submit flow and does not call setUrgent", () => {
    expect(src).toMatch(/canShowUrgentRequestButton/);
    expect(src).toMatch(/toggle-urgent-button/);
    expect(src).toMatch(/submitUrgentRequest/);
    expect(src).toMatch(/UrgentRequestSubmitModal/);
    expect(src).not.toMatch(/api\.setUrgent\(/);
    expect(src).not.toMatch(/require\("admin", "head_production"\)/);
  });

  test("payment files open through the in-app viewer", () => {
    expect(src).toMatch(/InAppDocumentViewer/);
    expect(src).toMatch(/dataUrlToBlob/);
    expect(src).toMatch(/downloadSalesReceiptPaymentProof/);
    expect(src).toMatch(/payment-files-list/);
    expect(src).not.toMatch(/openPaymentProofSource/);
  });
});
