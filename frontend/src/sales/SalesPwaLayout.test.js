import fs from "fs";
import path from "path";
import { SALES_PWA_NAV } from "../lib/salesPwa";

describe("sales PWA wiring", () => {
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const layout = fs.readFileSync(path.join(__dirname, "SalesPwaLayout.jsx"), "utf8");

  test("sales namespace is outside AppShell and operational /m", () => {
    expect(app).toMatch(/path="\/sales"/);
    expect(app).toMatch(/path="\/sales\/po"/);
    expect(app).toMatch(/path="\/sales\/visit"/);
    expect(app).toMatch(/path="\/sales\/po\/:id"/);
    expect(app).toMatch(/path="\/sales\/po\/:id\/dokumen\/:kind\/:documentId"/);
    expect(app).toMatch(/path="\/sales\/pelanggan"/);
    expect(app).toMatch(/path="\/sales\/prediksi"/);
    expect(app).toMatch(/path="\/sales\/prediksi\/:customerKey"/);
    expect(app).toMatch(/path="\/sales\/profil"/);
    expect(app).toMatch(/SalesPwaGate/);
    expect(app).toMatch(/SalesEternalRedirect/);
    const salesIdx = app.indexOf("SalesPwaLayout");
    const shellIdx = app.indexOf("<AppShell");
    expect(salesIdx).toBeGreaterThan(0);
    expect(salesIdx).toBeLessThan(shellIdx);
  });

  test("bottom nav keeps four compact primary actions", () => {
    expect(SALES_PWA_NAV.map((i) => i.id)).toEqual(["dashboard", "orders", "visits", "more"]);
    expect(layout).toMatch(/sales-pwa-nav/);
    expect(layout).toMatch(/sales-more-drawer/);
  });
});
