import fs from "fs";
import path from "path";
import { activeSalesPwaNav, documentTotal, formatSalesDate, isCustomerDesignApprovalPath, isSafeSalesNextPath, isSalesPwaRole, isValidSalesManifest, salesJakartaDay, SALES_PWA_MORE_NAV, SALES_PWA_NAV } from "./salesPwa";

describe("sales PWA helpers", () => {
  test("role gate is sales_eternal only", () => {
    expect(isCustomerDesignApprovalPath("/customer/design-approval/track-cup")).toBe(true);
    expect(isCustomerDesignApprovalPath("/customer/design-approval/")).toBe(false);
    expect(isCustomerDesignApprovalPath("/sales")).toBe(false);
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

  test("bottom nav is dashboard, PO, visit, and more", () => {
    // Prediksi Order moved into the more drawer when Visit and Lainnya became primary.
    // The old four labels are not the current bar, so this expectation follows the nav in use.
    expect(SALES_PWA_NAV.map((item) => item.label)).toEqual(["Dashboard", "PO Saya", "Visit", "Lainnya"]);
    expect(SALES_PWA_MORE_NAV.map((item) => item.label)).toContain("Prediksi Order");
    expect(SALES_PWA_NAV.map((item) => item.label)).not.toContain("Prediksi Order");
    expect(activeSalesPwaNav("/sales")).toBe("dashboard");
    expect(activeSalesPwaNav("/sales/po")).toBe("orders");
    expect(activeSalesPwaNav("/sales/visit")).toBe("visits");
    expect(activeSalesPwaNav("/sales/prediksi")).toBe("predictions");
  });

  test("manifest is a separate sales PWA", () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "../../public/sales-manifest.json"), "utf8"));
    expect(isValidSalesManifest(manifest)).toBe(true);
  });

  test("delivery day follows the Asia/Jakarta calendar", () => {
    expect(salesJakartaDay(new Date("2026-10-02T16:30:00Z"))).toBe("2026-10-02");
    expect(salesJakartaDay(new Date("2026-10-02T17:30:00Z"))).toBe("2026-10-03");
  });

  test("display date and document count stay local", () => {
    expect(formatSalesDate("2026-09-17")).toBe("17/09/26");
    expect(documentTotal({ documents: { po: 1, sj: 2, invoice: 1, bukti_kirim: 0, total: 4 } })).toBe(4);
  });
});
