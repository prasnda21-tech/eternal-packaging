import fs from "fs";
import path from "path";

describe("PO Detail delivery cards", () => {
  const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const proof = fs.readFileSync(path.join(__dirname, "../components/DeliveryProofPanel.jsx"), "utf8");
  const qtyUi = fs.readFileSync(path.join(__dirname, "../components/qtyUi.jsx"), "utf8");
  const wage = fs.readFileSync(path.join(__dirname, "OperatorWageSettings.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");

  test("Bukti Kirim appears before Terkirim without duplicate cards", () => {
    const slot = detail.slice(detail.indexOf("workflow-slot-14"));
    const bukti = slot.indexOf("DeliveryProofPanel");
    const terkirim = slot.indexOf("terkirim-panel");
    expect(bukti).toBeGreaterThan(-1);
    expect(terkirim).toBeGreaterThan(-1);
    expect(bukti).toBeLessThan(terkirim);
    expect(slot.split("DeliveryProofPanel").length - 1).toBe(1);
    expect(slot.split("terkirim-panel").length - 1).toBe(1);
  });

  test("Terkirim summary has total delivered remaining qty", () => {
    expect(detail).toMatch(/terkirim-total-po/);
    expect(detail).toMatch(/terkirim-delivered-qty/);
    expect(detail).toMatch(/terkirim-remaining-qty/);
    expect(detail).toMatch(/terkirimQtySummary/);
  });

  test("Bukti Kirim history shows driver item qty time plate photos", () => {
    expect(proof).toMatch(/<h3>Bukti Kirim<\/h3>/);
    expect(proof).toMatch(/delivery-history/);
    expect(proof).toMatch(/Nama Driver/);
    expect(proof).toMatch(/Barang \/ Item/);
    expect(proof).toMatch(/Plat Kendaraan/);
    expect(proof).toMatch(/delivery-history-photo/);
    expect(proof).toMatch(/confirm-delivered-button/);
    expect(proof).toMatch(/Upload foto tidak menandai Terkirim/);
  });

  test("manual qty input uses dedicated contrast class not global input", () => {
    expect(qtyUi).toMatch(/manual-qty-input/);
    expect(qtyUi).toMatch(/is-manual/);
    expect(qtyUi).toMatch(/is-disabled/);
    expect(css).toMatch(/manual-qty-input/);
  });

  test("wage settings tables scroll inside wrapper not page", () => {
    expect(wage).toMatch(/wage-table-wrap/);
    expect(wage).toMatch(/wage-table/);
    expect(wage).not.toMatch(/prod-table/);
    expect(css).toMatch(/\.wage-settings\{[^}]*max-width:100%/);
    expect(css).toMatch(/\.wage-table-wrap\{[^}]*overflow-x:auto/);
  });
});
