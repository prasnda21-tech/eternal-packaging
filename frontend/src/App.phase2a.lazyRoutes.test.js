import fs from "fs";
import path from "path";

const app = fs.readFileSync(path.join(__dirname, "App.js"), "utf8");
const shell = fs.readFileSync(path.join(__dirname, "components/AppShell.jsx"), "utf8");
const optimizerLayout = fs.readFileSync(path.join(__dirname, "mobile/RouteOptimizerLayout.jsx"), "utf8");
const mobileLayout = fs.readFileSync(path.join(__dirname, "mobile/MobileOperationalLayout.jsx"), "utf8");
const fallback = fs.readFileSync(path.join(__dirname, "components/WorkspaceChunkFallback.jsx"), "utf8");

function lazyLine(name) {
  return new RegExp(`const ${name} = lazy\\(\\(\\) => import\\("\\.\\/pages\\/${name}"\\)\\)`);
}

function staticPage(name) {
  return new RegExp(`import ${name} from "\\.\\/pages\\/${name}"`);
}

describe("Phase 2A route code splitting", () => {
  test("approved page modules use lazy dynamic imports", () => {
    [
      "Settings",
      "FakturPenjualan",
      "PengirimanPesanan",
      "PenerimaanPenjualan",
      "DataPelanggan",
      "ImportPO",
      "AkunPerkiraan",
      "JurnalUmum",
      "SaldoAwal",
      "BukuBesar",
      "NeracaSaldo",
      "LabaRugi",
      "Neraca",
      "DaftarKasBank",
      "PenerimaanKasBank",
      "PembayaranKasBank",
      "TransferKasBank",
      "MutasiKasBank",
      "RekonsiliasiKasBank",
      "HrPayroll",
      "EmployeeAdmin",
      "EmployeeAdminDetail",
      "AnalitikOutput",
      "AnalyticsOperators",
      "StatusPO",
      "GajiOperator",
      "PrediksiOrder",
      "OptimasiRute",
      "OptimasiRuteEditor",
    ].forEach((name) => {
      expect(app).toMatch(lazyLine(name));
      expect(app).not.toMatch(staticPage(name));
    });
  });

  test("critical desktop and auth modules remain eager", () => {
    [
      "Login",
      "Dashboard",
      "PesananPenjualan",
      "ProductionSchedule",
      "PODetail",
      "HoPDashboard",
      "ChatPage",
    ].forEach((name) => {
      expect(app).toMatch(staticPage(name));
      expect(app).not.toMatch(lazyLine(name));
    });
    expect(app).toMatch(/import AppShell from "\.\/components\/AppShell"/);
    expect(app).not.toMatch(/const AppShell = lazy/);
    expect(app).toMatch(/import \{ AuthProvider, useAuth \} from "\.\/lib\/auth"/);
    expect(app).toMatch(/function Protected\(/);
    expect(app).toMatch(/function PermGate\(/);
  });

  test("/m/* pages stay statically imported", () => {
    [
      "MobileOperationalLayout",
      "MobileTasksPage",
      "MobileOrdersPage",
      "MobileOrderDetailPage",
      "MobileProductionSchedulePage",
      "MobileDeliverySchedulePage",
      "MobileDeliveryRouteReviewPage",
      "MobileNotificationsPage",
      "MobileProfilePage",
      "MobileChatPage",
      "MobileChatRoomPage",
    ].forEach((name) => {
      expect(app).toMatch(new RegExp(`import ${name} from "\\.\\/mobile\\/${name}"`));
      expect(app).not.toMatch(new RegExp(`const ${name} = lazy`));
    });
    expect(app).toMatch(/import RouteOptimizerLayout from "\.\/mobile\/RouteOptimizerLayout"/);
    expect(app).not.toMatch(/const RouteOptimizerLayout = lazy/);
  });

  test("AppShell keeps chrome eager and Suspense wraps only Outlet", () => {
    expect(shell).toMatch(/<Suspense fallback=\{<WorkspaceChunkFallback \/>\}>/);
    const suspenseIdx = shell.indexOf("<Suspense fallback={<WorkspaceChunkFallback />}>");
    const outletIdx = shell.indexOf("<Outlet />");
    const suspenseEnd = shell.indexOf("</Suspense>", suspenseIdx);
    expect(suspenseIdx).toBeGreaterThan(-1);
    expect(outletIdx).toBeGreaterThan(suspenseIdx);
    expect(outletIdx).toBeLessThan(suspenseEnd);
    expect(shell.indexOf("<aside")).toBeLessThan(suspenseIdx);
    expect(shell.indexOf("<header className=\"topbar\"")).toBeLessThan(suspenseIdx);
    expect(shell.indexOf("<WorkspaceTabs")).toBeLessThan(suspenseIdx);
    expect(shell.indexOf("<FloatingNotificationBell />")).toBeGreaterThan(suspenseEnd);
  });

  test("RouteOptimizerLayout stays eager; mobile Outlet has Suspense for lazy optimizer pages", () => {
    expect(optimizerLayout).toMatch(/export default function RouteOptimizerLayout/);
    expect(optimizerLayout).toMatch(/return <AppShell \/>/);
    expect(optimizerLayout).toMatch(/return <MobileOperationalLayout \/>/);
    expect(optimizerLayout).not.toMatch(/lazy\(/);
    expect(mobileLayout).toMatch(/<Suspense fallback=\{<WorkspaceChunkFallback \/>\}>/);
    const suspenseIdx = mobileLayout.indexOf("<Suspense fallback={<WorkspaceChunkFallback />}>");
    const outletIdx = mobileLayout.indexOf("<Outlet />");
    const suspenseEnd = mobileLayout.indexOf("</Suspense>", suspenseIdx);
    expect(outletIdx).toBeGreaterThan(suspenseIdx);
    expect(outletIdx).toBeLessThan(suspenseEnd);
    expect(mobileLayout.indexOf("<MobileSidebar")).toBeGreaterThan(suspenseEnd);
  });

  test("chunk fallback is compact Indonesian copy without .loading", () => {
    expect(fallback).toMatch(/Memuat halaman\.\.\./);
    expect(fallback).toMatch(/workspace-chunk-fallback/);
    expect(fallback).toMatch(/role="status"/);
    expect(fallback).not.toMatch(/className="loading"/);
  });

  test("representative PermGate and Protected contracts stay identical", () => {
    expect(app).toMatch(/<Route element=\{<Protected><AppShell \/><\/Protected>\}>/);
    expect(app).toMatch(/<Route element=\{<Protected><RouteOptimizerLayout \/><\/Protected>\}>/);
    expect(app).toMatch(/<Route element=\{<Protected><MobileOperationalLayout \/><\/Protected>\}>/);
    expect(app).toMatch(/path="\/settings" element=\{<PermGate anyOf=\{\[NAV_KEYS\.settings\]\} alsoAnyOf=\{SETTINGS_HUB_PERMS\}><Settings \/><\/PermGate>\}/);
    expect(app).toMatch(/path="\/penjualan\/faktur-penjualan" element=\{<PermGate anyOf=\{\[NAV_KEYS\.salesInvoice, NAV_KEYS\.sales, "sales\.invoice\.view", "sales\.invoice\.create", "sales_order\.view", "po\.view"\]\}><FakturPenjualan \/><\/PermGate>\}/);
    expect(app).toMatch(/path="\/buku-besar\/akun-perkiraan" element=\{<PermGate anyOf=\{\[NAV_KEYS\.ledgerAccounts, NAV_KEYS\.ledger, "ledger\.accounts\.view", "ledger\.accounts\.manage"\]\}><AkunPerkiraan \/><\/PermGate>\}/);
    expect(app).toMatch(/path="\/kas-bank\/daftar" element=\{<PermGate anyOf=\{\[NAV_KEYS\.cashBankAccounts, NAV_KEYS\.cashBank, "cash_bank\.view", "cash_bank\.manage"\]\}><DaftarKasBank \/><\/PermGate>\}/);
    expect(app).toMatch(/path="\/laporan\/analitik-output" element=\{<PermGate anyOf=\{\[NAV_KEYS\.analytics\]\} alsoAnyOf=\{\["schedule\.view"\]\}><AnalitikOutput \/><\/PermGate>\}/);
    expect(app).toMatch(/path="\/hr-payroll\/penggajian" element=\{<PermGate anyOf=\{\[NAV_KEYS\.hrPayrollPayroll, NAV_KEYS\.hrPayroll, "payroll\.view", "payroll\.calculate", "payroll\.review", "payroll\.approve", "payroll\.finalize"\]\}><HrPayroll mode="payroll" \/><\/PermGate>\}/);
    expect(app).toMatch(/path="\/optimasi-rute" element=\{<PermGate anyOf=\{\[NAV_KEYS\.routeOptimizer, "route_optimizer\.view", "route_optimizer\.create", "route_optimizer\.edit_own", "route_optimizer\.optimize", "route_optimizer\.manage"\]\}><OptimasiRute \/><\/PermGate>\}/);
    expect(app).toMatch(/path="\/po\/:id" element=\{<PODetail \/>\}/);
    expect(app).toMatch(/path="\/penjualan\/pesanan-penjualan" element=\{<PermGate anyOf=\{\[NAV_KEYS\.salesOrder, NAV_KEYS\.sales, "sales_order\.view", "sales_order\.create", "po\.view"\]\}><PesananPenjualan \/><\/PermGate>\}/);
    expect(app).toMatch(/path="\/produksi" element=\{<PermGate anyOf=\{\[NAV_KEYS\.schedule\]\}><ProductionSchedule \/><\/PermGate>\}/);
    expect(app).toMatch(/if \(!ok \|\| !also\) return <Navigate to="\/" replace \/>/);
  });
});
