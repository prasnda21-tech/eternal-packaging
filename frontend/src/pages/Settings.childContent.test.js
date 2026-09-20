import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import { settingsTabFromPath } from "../lib/navAccess";
import api from "../lib/api";
import Settings from "./Settings";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");

let mockPath = "/settings/hr-payroll";
let mockSearch = "";
const mockNavigate = jest.fn();
let mockHasPerm = () => true;

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: mockPath, search: mockSearch }),
  useSearchParams: () => [new URLSearchParams(mockSearch.startsWith("?") ? mockSearch.slice(1) : mockSearch)],
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", role: "admin", email: "admin@eternalpackaging.com" },
    hasPerm: (key) => mockHasPerm(key),
    refresh: jest.fn(),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {},
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const hrSettings = {
  config_version: 1,
  attendance: {},
  overtime: {},
  kasbon: {},
  payslip: { company_name: "", header_text: "", footer_text: "" },
};

function stubApi() {
  const resolved = (value) => jest.fn(() => Promise.resolve(value));
  Object.assign(api, {
    adminPermissionCatalog: resolved({ modules: {}, nav_menu: [] }),
    adminListUsers: resolved({ users: [] }),
    adminListRoles: resolved({ roles: [] }),
    hrPayrollSettings: resolved({ settings: hrSettings }),
    hrPayrollComponents: resolved({ components: [] }),
    salesAccountingSettings: resolved({ settings: {} }),
    purchaseAccountingSettings: resolved({ settings: {} }),
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

describe("Settings child content layout", () => {
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
    mockHasPerm = () => true;
    mockPath = "/settings/hr-payroll";
    mockSearch = "";
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("source hides the category grid on child routes only", () => {
    expect(settingsSrc).toMatch(/showCategoryNav = location\.pathname === "\/settings" && !urlTab/);
    expect(settingsSrc).toMatch(/showCategoryNav && \(/);
    expect(settingsSrc).toMatch(/<SettingsCategoryNav/);
    expect(settingsSrc).toMatch(/settings-active-panel/);
    expect(settingsSrc).toMatch(/tab === "hr-payroll" && canViewHrPayroll/);
    expect(settingsSrc).toMatch(/tab === "sales-accounting" && canViewSalesAccounting/);
    expect(settingsSrc).toMatch(/tab === "hpp" && canViewHpp/);
  });

  test("/settings/hr-payroll renders HR content without the old category grid", async () => {
    await renderAt("/settings/hr-payroll");
    expect(container.querySelector("[data-testid='settings-hr-payroll']")).toBeTruthy();
    expect(container.querySelector("[data-testid='settings-category-nav']")).toBeNull();
    expect(container.querySelector(".settings-cat-grid")).toBeNull();
    expect(container.textContent).toMatch(/HR & Payroll/);
  });

  test("/settings/sales-accounting renders Sales Accounting without the old category grid", async () => {
    await renderAt("/settings/sales-accounting");
    expect(container.querySelector("[data-testid='sales-accounting-settings']")).toBeTruthy();
    expect(container.querySelector("[data-testid='settings-category-nav']")).toBeNull();
    expect(container.querySelector(".settings-cat-grid")).toBeNull();
  });

  test("/settings/hpp renders HPP content without the old category grid", async () => {
    await renderAt("/settings/hpp");
    expect(container.querySelector("[data-testid='hpp-settings']")).toBeTruthy();
    expect(container.querySelector("[data-testid='settings-category-nav']")).toBeNull();
    expect(container.querySelector(".settings-cat-grid")).toBeNull();
  });

  test("legacy ?tab= still opens the same child without the category grid", async () => {
    expect(settingsTabFromPath("/settings", "?tab=sales-accounting")).toBe("sales-accounting");
    await renderAt("/settings", "?tab=sales-accounting");
    expect(container.querySelector("[data-testid='sales-accounting-settings']")).toBeTruthy();
    expect(container.querySelector("[data-testid='settings-category-nav']")).toBeNull();
    expect(mockNavigate).toHaveBeenCalledWith("/settings/sales-accounting", { replace: true });
  });

  test("invalid tab stays safe and does not invent a new hub", async () => {
    expect(settingsTabFromPath("/settings/not-a-real-tab")).toBe(null);
    await renderAt("/settings/not-a-real-tab");
    expect(container.querySelector("[data-testid='settings-page']")).toBeTruthy();
    expect(container.querySelector("[data-testid='settings-category-nav']")).toBeNull();
    expect(container.querySelector("[data-testid='employees-tab']")).toBeTruthy();
  });

  test("child permission gates still hide unauthorized panels", async () => {
    mockHasPerm = (key) => key !== "payroll.settings";
    await renderAt("/settings/hr-payroll");
    expect(container.querySelector("[data-testid='settings-hr-payroll']")).toBeNull();
    expect(container.querySelector("[data-testid='settings-category-nav']")).toBeNull();
  });

  test("bare /settings keeps existing hub compatibility", async () => {
    await renderAt("/settings");
    expect(container.querySelector("[data-testid='settings-category-nav']")).toBeTruthy();
    expect(container.querySelector(".settings-cat-grid")).toBeTruthy();
    expect(container.querySelector("[data-testid='employees-tab']")).toBeTruthy();
  });
});
