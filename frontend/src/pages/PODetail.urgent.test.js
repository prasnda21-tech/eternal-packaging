import fs from "fs";
import path from "path";

describe("PODetail urgent + payment proof wiring", () => {
  const src = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const commandBar = fs.readFileSync(
    path.join(__dirname, "../components/poDetail/PoGlobalCommandBar.jsx"),
    "utf8"
  );

  test("urgent control stays permission-gated and calls setUrgent", () => {
    expect(src).toMatch(/canManageUrgentPerm\(hasPerm\)/);
    expect(commandBar).toMatch(/toggle-urgent-button/);
    expect(src).toMatch(/api\.setUrgent\(order\.id, order\.priority !== "urgent"\)/);
    expect(src).not.toMatch(/require\("admin", "head_production"\)/);
    expect(src).toMatch(/order\.priority === "urgent" \?[\s\S]*data-testid="detail-priority"/);
    expect(src).toMatch(/order\.buffer_stock \?[\s\S]*data-testid="buffer-stock-badge"/);
  });

  test("payment files open through blob helper, not raw data navigation only", () => {
    expect(src).toMatch(/openPaymentProofSource/);
    expect(src).toMatch(/downloadSalesReceiptPaymentProof/);
    expect(src).toMatch(/payment-files-list/);
  });
});
