import fs from "fs";
import path from "path";

describe("Dashboard presentation", () => {
  const src = fs.readFileSync(path.join(__dirname, "../pages/HoPDashboard.jsx"), "utf8");

  test("cards navigate to Status PO views except Pengiriman Hari Ini", () => {
    expect(src).toMatch(/\/laporan\/status-po\?view=/);
    expect(src).toMatch(/view=\$\{view\}|work_today/);
    expect(src).toMatch(/\/produksi\/pengiriman-hari-ini/);
    expect(src).not.toMatch(/status-po\?view=\$\{view\}.*delivery_today|openStatusPo\("delivery_today"\)/);
  });

  test("no inline expanded list under dashboard", () => {
    expect(src).not.toMatch(/hop-filter-panel/);
    expect(src).not.toMatch(/hop-filter-list/);
  });

  test("progress per tahap and beban operator removed from UI", () => {
    expect(src).not.toMatch(/stage-breakdown-panel/);
    expect(src).not.toMatch(/operator-load-panel/);
    expect(src).not.toMatch(/Progres per Tahap/);
    expect(src).not.toMatch(/Beban Operator/);
  });

  test("pengerjaan and pengiriman are kpi cards", () => {
    expect(src).toMatch(/kpi-work-today/);
    expect(src).toMatch(/kpi-delivery-today/);
    expect(src).toMatch(/Pengerjaan Hari Ini/);
    expect(src).toMatch(/Pengiriman Hari Ini/);
  });
});

describe("Dashboard auth-state contracts", () => {
  const src = fs.readFileSync(path.join(__dirname, "../pages/HoPDashboard.jsx"), "utf8");
  const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const authSrc = fs.readFileSync(path.join(__dirname, "../lib/auth.jsx"), "utf8");
  const shellSrc = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");

  test("TEST 1 — authenticated path renders dashboard content, not login copy", () => {
    expect(src).toMatch(/dashboardPageView\(\{ loading: authLoading, user, data, err \}\)/);
    expect(src).toMatch(/data-testid="hop-dashboard"/);
    expect(src).toMatch(/data-testid="hop-title"/);
    expect(src).toMatch(/Dashboard Produksi/);
    expect(src).not.toMatch(/Silakan masuk terlebih dahulu/);
    expect(src).toMatch(/dashboardFetchErrorMessage\(e, \{ user, loading: authLoading \}\)/);
  });

  test("TEST 2 — auth loading uses existing skeleton and skips fetch", () => {
    expect(src).toMatch(/const \{ user, loading: authLoading, hasPerm \} = useAuth\(\)/);
    expect(src).toMatch(/if \(authLoading \|\| !user\) return/);
    expect(src).toMatch(/if \(pageView === "loading"\) return <div className="loading" data-testid="hop-loading">Memuat dashboard\.\.\.<\/div>/);
    expect(shellSrc).toMatch(/const \{ user, hasPerm, logout \} = useAuth\(\)/);
    expect(authSrc).toMatch(/value=\{\{ user, loading, login, logout, refresh/);
  });

  test("TEST 3 — unauthenticated keeps existing login redirect", () => {
    expect(appSrc).toMatch(/if \(loading\) return <div className="loading">Memuat\.\.\.<\/div>/);
    expect(appSrc).toMatch(/if \(!user\) return <Navigate to="\/login" replace \/>/);
    expect(src).toMatch(/if \(pageView === "unauthenticated"\) return null/);
    expect(src).not.toMatch(/Silakan masuk terlebih dahulu/);
  });

  test("TEST 4 — dashboard API failure is hop-error, not login copy; reminders are isolated", () => {
    expect(src).toMatch(/if \(pageView === "error"\) return <div className="error-page" data-testid="hop-error">\{err\}<\/div>/);
    expect(src).toMatch(/api\.reminders\(\)\.catch\(\(\) => null\)/);
    expect(src).toMatch(/api\.accurateStatus\(\)\.catch\(\(\) => null\)/);
    expect(src).toMatch(/DASHBOARD_LOAD_FAILED/);
  });

  test("TEST 5 — permission errors stay mapped through dashboardFetchErrorMessage", () => {
    expect(src).toMatch(/from "\.\.\/lib\/dashboardAuth"/);
    expect(src).toMatch(/dashboardFetchErrorMessage/);
  });

  test("shell and dashboard share useAuth as the session source", () => {
    expect(shellSrc).toMatch(/from "\.\.\/lib\/auth"/);
    expect(src).toMatch(/from "\.\.\/lib\/auth"/);
    expect(appSrc).toMatch(/<Protected><AppShell \/>/);
    expect(appSrc).toMatch(/path="\/dashboard"/);
  });
});
