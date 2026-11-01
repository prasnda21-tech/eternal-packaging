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
    expect(app).toMatch(/path="\/sales\/invoices"/);
    expect(app).toMatch(/path="\/sales\/invoices\/:id"/);
    expect(app).toMatch(/path="\/sales\/receivables"/);
    expect(app).toMatch(/path="\/sales\/riwayat-pengiriman"/);
    expect(app).toMatch(/SalesPwaGate/);
    expect(app).toMatch(/SalesEternalRedirect/);
    expect(app).toMatch(/isCustomerDesignApprovalPath\(location\.pathname\)/);
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

  test("salesPwa.css uses ERP forest tokens instead of navy brand chrome", () => {
    const css = fs.readFileSync(path.join(__dirname, "salesPwa.css"), "utf8");
    expect(css).toMatch(/--spwa-header:\s*var\(--ep-dark/);
    expect(css).toMatch(/--spwa-primary:\s*var\(--ep-primary/);
    expect(css).toMatch(/--spwa-paper:\s*var\(--paper/);
    expect(css).toMatch(/font-family:\s*"IBM Plex Sans"/);
    expect(css).toMatch(/IBM Plex Mono/);
    expect(css).not.toMatch(/#0[Bb]1[Ff]2[Aa]/);
    expect(css).not.toMatch(/#08785[Dd]/);
    expect(css).toMatch(/\.spwa-top\s*\{[^}]*background:\s*var\(--spwa-header\)/s);
    expect(css).toMatch(/\.spwa-nav a\.is-on\s*\{[^}]*color:\s*var\(--spwa-primary\)/s);
    expect(css).toMatch(/\.spwa-nav button\.is-on\s*\{[^}]*color:\s*var\(--spwa-primary\)/s);
  });
});
