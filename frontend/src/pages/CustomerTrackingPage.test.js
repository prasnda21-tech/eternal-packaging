import fs from "fs";
import path from "path";

describe("Customer tracking public page", () => {
  const page = fs.readFileSync(path.join(__dirname, "CustomerTrackingPage.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");

  test("public route is outside Protected AppShell and does not go to login", () => {
    expect(app).toMatch(/path="\/tracking\/:token"/);
    expect(app).toMatch(/CustomerTrackingPage/);
    const trackingIdx = app.indexOf('path="/tracking/:token"');
    const protectedIdx = app.indexOf("<Protected>");
    const catchAllIdx = app.indexOf('path="*"');
    expect(trackingIdx).toBeGreaterThan(-1);
    expect(trackingIdx).toBeLessThan(protectedIdx);
    expect(trackingIdx).toBeLessThan(catchAllIdx);
    const trackingLine = app.split("\n").find((l) => l.includes("/tracking/:token")) || "";
    expect(trackingLine).toMatch(/CustomerTrackingPage/);
    expect(trackingLine).not.toMatch(/login/);
  });

  test("page has no internal shell, login redirect, or sensitive fields", () => {
    expect(page).toMatch(/customer-tracking-page/);
    expect(page).toMatch(/Alur Kerja/);
    expect(page).toMatch(/No PO/);
    expect(page).not.toMatch(/Navigate to="\/login"/);
    expect(page).not.toMatch(/AppShell/);
    expect(page).not.toMatch(/employee/);
    expect(page).not.toMatch(/accurate/i);
    expect(page).not.toMatch(/qty_/);
    expect(page).not.toMatch(/unit_price|harga/i);
    expect(page).not.toMatch(/audit/i);
  });

  test("public fetch does not send credentials", () => {
    expect(api).toMatch(/publicTracking:\s*async/);
    expect(api).toMatch(/\/public\/tracking\//);
    const start = api.indexOf("publicTracking:");
    const fn = api.slice(start, start + 400);
    expect(fn).toMatch(/fetch\(/);
    expect(fn).not.toMatch(/withCredentials:\s*true/);
    expect(fn).not.toMatch(/credentials:\s*['"]include['"]/);
  });

  test("internal detail has compact copy-link action", () => {
    expect(detail).toMatch(/Live Tracking Customer/);
    expect(detail).toMatch(/Salin Link Tracking/);
    expect(detail).toMatch(/copy-tracking-link/);
    expect(detail).toMatch(/orderTrackingLink/);
    const routeIdx = detail.indexOf("Proses Produksi");
    const trackIdx = detail.indexOf("tracking-customer-panel");
    expect(trackIdx).toBeGreaterThan(detail.indexOf("ProductionRoutePanel"));
  });

  test("public page shows customer name po and proofs", () => {
    expect(page).toMatch(/Nama Customer/);
    expect(page).toMatch(/tracking-customer-name/);
    expect(page).toMatch(/Bukti Pengiriman/);
    expect(page).toMatch(/slice\(0, 5\)/);
  });

  test("public page may show reschedule date only", () => {
    expect(page).toMatch(/tracking-reschedule/);
    expect(page).toMatch(/scheduled_date/);
    expect(page).not.toMatch(/requested_by/);
    expect(page).not.toMatch(/reason/);
  });

  test("public page shows customer-safe delivery history", () => {
    expect(page).toMatch(/tracking-delivery-history/);
    expect(page).toMatch(/Nama Driver/);
    expect(page).toMatch(/Plat Kendaraan/);
    expect(page).toMatch(/slice\(0, 5\)/);
    expect(page).not.toMatch(/driver_id/);
    expect(page).not.toMatch(/storage_path/);
    expect(page).not.toMatch(/user_id/);
  });

  test("Penjadwalan panel keeps sequential Set without wiping via operator_splits identity", () => {
    const split = fs.readFileSync(path.join(__dirname, "../components/OperatorSplitPanel.jsx"), "utf8");
    expect(split).toMatch(/shouldApplyRemoteSplits/);
    expect(split).toMatch(/dirtyRef/);
    expect(split).toMatch(/patchScheduleRow/);
  });
});
