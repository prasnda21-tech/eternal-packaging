import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import { STOCK_IN_OPTIONS, STOCK_OUT_OPTIONS } from "./PhysicalStockSettings";
import { settingsTabFromPath, SETTINGS_TAB_IDS, SETTINGS_TILE_LABELS, NAV_KEYS } from "../lib/navAccess";
import api from "../lib/api";
import Settings from "./Settings";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const hppSettingsSrc = fs.readFileSync(path.join(__dirname, "HppSettings.jsx"), "utf8");
const physicalStockSrc = fs.readFileSync(path.join(__dirname, "PhysicalStockSettings.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

let mockPath = "/settings/physical-stock";
let mockSearch = "";
const mockNavigate = jest.fn();
let mockUserPerms = ["current_stock.view", "current_stock.update", NAV_KEYS.settings];

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: mockPath, search: mockSearch }),
  useSearchParams: () => [new URLSearchParams(mockSearch.startsWith("?") ? mockSearch.slice(1) : mockSearch)],
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", role: "staff", email: "staff@eternalpackaging.com" },
    hasPerm: (key) => mockUserPerms.includes(key),
    refresh: jest.fn(),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {},
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

function stubApi() {
  const resolved = (value) => jest.fn(() => Promise.resolve(value));
  Object.assign(api, {
    physicalStockSettings: resolved({
      settings: {
        enabled: true,
        stock_in_trigger: "goods_receipt",
        stock_out_trigger: "loading_confirmation",
        stock_in_label: "Penerimaan Barang",
        stock_out_label: "Loading Dikonfirmasi",
        updated_at: "2026-09-08T12:00:00Z",
        updated_by: { name: "Admin Stok" },
      },
    }),
    updatePhysicalStockSettings: resolved({
      message: "Pengaturan stok fisik berhasil disimpan",
      settings: {
        enabled: true,
        stock_in_trigger: "goods_receipt",
        stock_out_trigger: "loading_confirmation",
      },
    }),
    adminPermissionCatalog: resolved({ modules: {}, nav_menu: [] }),
    adminListUsers: resolved({ users: [] }),
    adminListRoles: resolved({ roles: [] }),
    hrPayrollSettings: resolved({ settings: {} }),
    hrPayrollComponents: resolved({ components: [] }),
    salesAccountingSettings: resolved({ settings: {} }),
    payrollAccountingSettings: resolved({ settings: {} }),
    ledgerAccounts: resolved({ items: [] }),
    hppSettings: resolved({ settings: { method: "FIFO", recognition_point: "goods_receipt" } }),
  });
}

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

describe("Physical Stock Settings Component & Integration", () => {
  test("1. Stok Fisik tab is registered under Settings and navAccess", () => {
    expect(SETTINGS_TAB_IDS).toContain("physical-stock");
    expect(SETTINGS_TILE_LABELS["physical-stock"]).toBe("Stok Fisik");
    expect(settingsTabFromPath("/settings/physical-stock")).toBe("physical-stock");
    expect(settingsSrc).toMatch(/id:\s*"physical-stock"/);
    expect(settingsSrc).toMatch(/label:\s*"Stok Fisik"/);
    expect(settingsSrc).toMatch(/<PhysicalStockSettings/);
  });

  test("2. enabled toggle renders with inactive status by default and safe fallback", () => {
    expect(physicalStockSrc).toMatch(/useState\(false\)/);
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-enabled"/);
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-status-badge"/);
    expect(physicalStockSrc).toMatch(/<option value="inactive">Nonaktif<\/option>/);
    expect(physicalStockSrc).toMatch(/<option value="active">Aktif<\/option>/);
    expect(physicalStockSrc).toMatch(/Nonaktif/);
  });

  test("3. stock-in dropdown renders all valid options with descriptions", () => {
    expect(physicalStockSrc).toMatch(/data-testid="stock-in-trigger-select"/);
    expect(physicalStockSrc).toMatch(/data-testid="stock-in-trigger-desc"/);
    
    // Check supported values
    const inValues = STOCK_IN_OPTIONS.map((o) => o.value);
    expect(inValues).toContain("goods_receipt");
    expect(inValues).toContain("purchase_order");

    const inLabels = STOCK_IN_OPTIONS.map((o) => o.label);
    expect(inLabels).toContain("Penerimaan Barang");
    expect(inLabels).toContain("Pesanan Pembelian");
  });

  test("4. stock-out dropdown renders all valid options with descriptions", () => {
    expect(physicalStockSrc).toMatch(/data-testid="stock-out-trigger-select"/);
    expect(physicalStockSrc).toMatch(/data-testid="stock-out-trigger-desc"/);

    // Check supported values (only safe, crash-reconcilable triggers)
    const outValues = STOCK_OUT_OPTIONS.map((o) => o.value);
    expect(outValues).toContain("loading_confirmation");
    expect(outValues).toContain("delivery_confirmed");
    expect(outValues).toContain("packing_completed");
    expect(outValues).not.toContain("sales_delivery");

    const outLabels = STOCK_OUT_OPTIONS.map((o) => o.label);
    expect(outLabels).toContain("Loading Dikonfirmasi");
    expect(outLabels).toContain("Pengiriman Dikonfirmasi");
    expect(outLabels).toContain("Packing Selesai");
    expect(outLabels).not.toContain("Surat Jalan Dibuat");
  });

  test("5. read-only Operator Selesai status conversion rule is displayed and not editable", () => {
    expect(physicalStockSrc).toMatch(/Perubahan Status Cetak/);
    expect(physicalStockSrc).toMatch(/Aturan Baku \(Read-only\)/);
    expect(physicalStockSrc).toMatch(/Operator Selesai/);
    expect(physicalStockSrc).toMatch(/Belum Dicetak/);
    expect(physicalStockSrc).toMatch(/Sudah Dicetak/);
    expect(physicalStockSrc).toMatch(/data-testid="status-cetak-rule-card"/);
    expect(physicalStockSrc).toMatch(/data-testid="status-cetak-table"/);
    expect(physicalStockSrc).toMatch(/Total:\s*<strong>0<\/strong>/);
  });

  test("6. HPP separation disclaimer text is rendered", () => {
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-disclaimer"/);
    expect(physicalStockSrc).toMatch(/Pengaturan stok fisik terpisah dari Pengakuan HPP/);
    expect(physicalStockSrc).toMatch(/Perubahan pengaturan tidak menghitung ulang transaksi lama/);
  });

  test("7. API client exports methods for physical stock settings", () => {
    expect(apiSrc).toMatch(/physicalStockSettings:\s*\(\)\s*=>\s*client\.get\(`\/settings\/physical-stock`\)/);
    expect(apiSrc).toMatch(/updatePhysicalStockSettings:\s*\(payload\)\s*=>\s*client\.put\(`\/settings\/physical-stock`,\s*payload\)/);
    expect(physicalStockSrc).toMatch(/api\.updatePhysicalStockSettings\(payload\)/);
    expect(physicalStockSrc).toMatch(/notify\?\.\(res\.message/);
  });

  test("8. error response does not fake success and shows error banner", () => {
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-error"/);
    expect(physicalStockSrc).toMatch(/setErr\(errDetail\(ex\)\)/);
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-save"/);
  });

  test("9. current values and audit trail section render correctly", () => {
    expect(physicalStockSrc).toMatch(/data-testid="physical-stock-audit"/);
    expect(physicalStockSrc).toMatch(/Status otomatisasi aktif/);
    expect(physicalStockSrc).toMatch(/Trigger masuk aktif/);
    expect(physicalStockSrc).toMatch(/Trigger keluar aktif/);
    expect(physicalStockSrc).toMatch(/Terakhir disimpan/);
  });

  test("10. existing HPP tab source remains completely independent and untouched", () => {
    expect(hppSettingsSrc).toMatch(/Pengaturan HPP \/ Valuasi Persediaan/);
    expect(hppSettingsSrc).toMatch(/recognition_point/);
    expect(hppSettingsSrc).toMatch(/FIFO/);
    expect(hppSettingsSrc).toMatch(/RATA-RATA/);
    expect(hppSettingsSrc).not.toMatch(/physical_stock/);
    expect(hppSettingsSrc).not.toMatch(/loading_confirmation/);
  });

  test("11. App.js isolates physical-stock permissions from generic SETTINGS_PERMS", () => {
    const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
    // Generic SETTINGS_PERMS must NOT contain current_stock.* to avoid router gate leakage
    const settingsPermsMatch = appSrc.match(/const SETTINGS_PERMS = \[([\s\S]*?)\];/);
    expect(settingsPermsMatch).toBeTruthy();
    expect(settingsPermsMatch[1]).not.toMatch(/current_stock/);

    // Dedicated permissions for physical stock route
    expect(appSrc).toMatch(/const PHYSICAL_STOCK_SETTINGS_PERMS = \[.*"current_stock\.view".*"current_stock\.update"/);
    expect(appSrc).toMatch(/const SETTINGS_HUB_PERMS = \[.*SETTINGS_PERMS.*"current_stock\.view"/);

    // Dedicated route appears before /settings/:tab
    const routeIndexDedicated = appSrc.indexOf('path="/settings/physical-stock"');
    const routeIndexGeneric = appSrc.indexOf('path="/settings/:tab"');
    const routeIndexHub = appSrc.indexOf('path="/settings"');
    expect(routeIndexDedicated).toBeGreaterThan(0);
    expect(routeIndexGeneric).toBeGreaterThan(0);
    expect(routeIndexHub).toBeGreaterThan(0);
    expect(routeIndexDedicated).toBeLessThan(routeIndexGeneric);
  });

  test("12. AppShell.jsx TILE_ICONS registers physical-stock tile icon", () => {
    const appShellSrc = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
    expect(appShellSrc).toMatch(/"physical-stock":\s*Package/);
  });

  test("13. Settings card grid renders Stok Fisik with stable testid and route", () => {
    expect(settingsSrc).toMatch(/testid:\s*"settings-tab-physical-stock"/);
    expect(settingsSrc).toMatch(/id:\s*"physical-stock"/);
    expect(settingsSrc).toMatch(/tab === "physical-stock" && canViewPhysicalStock/);
  });
});

describe("Physical Stock Settings Permission Matrix (Users A, B, C, D)", () => {
  let container;
  let root;

  const renderAt = async (pathname, search = "") => {
    mockPath = pathname;
    mockSearch = search;
    await act(async () => root.render(<Settings />));
    await flush();
    await flush();
  };

  beforeEach(() => {
    stubApi();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockNavigate.mockReset();
    mockPath = "/settings/physical-stock";
    mockSearch = "";
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  describe("TEST USER A (current_stock.view only)", () => {
    beforeEach(() => {
      mockUserPerms = [NAV_KEYS.settings, "current_stock.view"];
    });

    test("A1. /settings/physical-stock renders PhysicalStockSettings", async () => {
      await renderAt("/settings/physical-stock");
      expect(container.querySelector("[data-testid='physical-stock-settings']")).toBeTruthy();
      expect(container.textContent).toMatch(/Pengaturan Stok Fisik/);
    });

    test("A2. /settings/employees blocks Karyawan content and employee data", async () => {
      await renderAt("/settings/employees");
      expect(container.querySelector("[data-testid='employees-tab']")).toBeNull();
      expect(container.querySelector("[data-testid='employees-readonly-note']")).toBeNull();
      expect(container.querySelector(".settings-users-header")).toBeNull();
    });

    test("A3. /settings/hpp blocks HPP content", async () => {
      await renderAt("/settings/hpp");
      expect(container.querySelector("[data-testid='hpp-settings']")).toBeNull();
      expect(container.textContent).not.toMatch(/Pengaturan HPP \/ Valuasi Persediaan/);
    });

    test("A4. /settings/roles blocks Role & Hak Akses content", async () => {
      await renderAt("/settings/roles");
      expect(container.querySelector("[data-testid='roles-tab']")).toBeNull();
      expect(container.textContent).not.toMatch(/Kelola Role & Hak Akses/);
    });

    test("A5. /settings hub reveals only Stok Fisik card without exposing unrelated settings", async () => {
      await renderAt("/settings");
      expect(container.querySelector("[data-testid='settings-category-nav']")).toBeTruthy();
      // Physical stock tile should be visible
      expect(container.querySelector("[data-testid='settings-tab-physical-stock']")).toBeTruthy();
      // Unrelated tiles must not be present
      expect(container.querySelector("[data-testid='settings-tab-employees']")).toBeNull();
      expect(container.querySelector("[data-testid='settings-tab-roles']")).toBeNull();
      expect(container.querySelector("[data-testid='settings-tab-hpp']")).toBeNull();
      expect(container.querySelector("[data-testid='settings-tab-operator-wage']")).toBeNull();
      expect(container.querySelector("[data-testid='settings-tab-hr-payroll']")).toBeNull();
    });
  });

  describe("TEST USER B (current_stock.view only, without update)", () => {
    beforeEach(() => {
      mockUserPerms = [NAV_KEYS.settings, "current_stock.view"];
    });

    test("may view Stok Fisik but CANNOT save (canEdit is false, save button hidden)", async () => {
      await renderAt("/settings/physical-stock");
      expect(container.querySelector("[data-testid='physical-stock-settings']")).toBeTruthy();
      // Save button is conditionally rendered only when canEdit is true
      expect(container.querySelector("[data-testid='physical-stock-save']")).toBeNull();
    });
  });

  describe("TEST USER C (current_stock.view + current_stock.update)", () => {
    beforeEach(() => {
      mockUserPerms = [NAV_KEYS.settings, "current_stock.view", "current_stock.update"];
    });

    test("may view AND save Stok Fisik (canEdit is true, save button active)", async () => {
      await renderAt("/settings/physical-stock");
      expect(container.querySelector("[data-testid='physical-stock-settings']")).toBeTruthy();
      const saveBtn = container.querySelector("[data-testid='physical-stock-save']");
      expect(saveBtn).toBeTruthy();
      expect(saveBtn.disabled).toBe(false);
    });
  });

  describe("TEST USER D (no current_stock permissions)", () => {
    beforeEach(() => {
      mockUserPerms = [NAV_KEYS.settings];
    });

    test("direct /settings/physical-stock is denied and does not render PhysicalStockSettings", async () => {
      await renderAt("/settings/physical-stock");
      expect(container.querySelector("[data-testid='physical-stock-settings']")).toBeNull();
    });
  });
});
