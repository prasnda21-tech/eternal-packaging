import fs from "fs";
import path from "path";
import { activeSalesPwaNav, documentTotal, formatSalesDate, isSafeSalesNextPath, isSalesPwaRole, isValidSalesManifest, SALES_PWA_NAV } from "./salesPwa";

describe("sales PWA helpers", () => {
  test("role gate is sales_eternal only", () => {
    expect(isSalesPwaRole({ role: "sales_eternal" })).toBe(true);
    expect(isSalesPwaRole({ role: "sales" })).toBe(false);
    expect(isSalesPwaRole({ role: "admin" })).toBe(false);
    expect(isSalesPwaRole({ role: "crm" })).toBe(false);
  });

  test("safe next path is /sales only", () => {
    expect(isSafeSalesNextPath("/sales")).toBe(true);
    expect(isSafeSalesNextPath("/sales/prediksi")).toBe(true);
    expect(isSafeSalesNextPath("/m/tasks")).toBe(false);
    expect(isSafeSalesNextPath("https://evil/sales")).toBe(false);
  });

  test("bottom nav replaces Dokumen with Prediksi Order", () => {
    expect(SALES_PWA_NAV.map((i) => i.label)).toEqual(["PO Saya", "Pelanggan", "Prediksi Order", "Profil"]);
    expect(activeSalesPwaNav("/sales")).toBe("orders");
    expect(activeSalesPwaNav("/sales/prediksi")).toBe("predictions");
  });

  test("manifest is a separate sales PWA", () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "../../public/sales-manifest.json"), "utf8"));
    expect(isValidSalesManifest(manifest)).toBe(true);
  });

  test("display date and document count stay local", () => {
    expect(formatSalesDate("2026-09-17")).toBe("17/09/26");
    expect(documentTotal({ documents: { po: 1, sj: 2, invoice: 1, bukti_kirim: 0, total: 4 } })).toBe(4);
  });
});
