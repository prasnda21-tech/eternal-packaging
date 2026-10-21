import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";

jest.mock("react-router-dom", () => {
  const mock = require("../test/reactRouterMock");
  return {
    ...mock,
    useNavigate: () => jest.fn(),
    Navigate: ({ to }) => <div data-testid="navigate" data-to={to} />,
  };
}, { virtual: true });

import { MemoryRouter } from "react-router-dom";
import ReportCatalog from "./ReportCatalog";

const mockPermissions = new Set(["reports.sales.view", "nav.reports", "profit_loss.view"]);
const mockUser = { id: "rpc-user-1", email: "rpc@eternal.test" };

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: mockUser,
    hasPerm: (key) => mockPermissions.has(key),
  }),
}));

jest.mock("../lib/api", () => ({
  api: {
    salesReportCatalog: jest.fn(),
  },
}));

import { api } from "../lib/api";

function renderAt(path) {
  const search = String(path || "").includes("?") ? String(path).slice(String(path).indexOf("?")) : "";
  require("../test/reactRouterMock").__reset(search);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter>
        <ReportCatalog />
      </MemoryRouter>,
    );
  });
  return { container, root };
}

describe("ReportCatalog page", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockPermissions.clear();
    mockPermissions.add("reports.sales.view");
    mockPermissions.add("nav.reports");
    mockPermissions.add("profit_loss.view");
    api.salesReportCatalog.mockReset();
    api.salesReportCatalog.mockResolvedValue({ memorized: [] });
  });

  test("renders permission-filtered cards and category chip", async () => {
    const { container, root } = renderAt("/laporan");
    expect(container.querySelector('[data-testid="report-catalog"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="report-catalog-card-penjualan-per-barang"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="report-catalog-card-laba-rugi"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="report-catalog-card-neraca"]')).toBeNull();
    await act(async () => {
      container.querySelector('[data-testid="report-catalog-chip-penjualan"]').click();
    });
    expect(container.querySelector('[data-testid="report-catalog-card-laba-rugi"]')).toBeNull();
    expect(container.querySelectorAll('[data-testid^="report-catalog-card-"]').length).toBe(5);
    root.unmount();
    container.remove();
  });

  test("saved view uses sales memorized only and shows empty state", async () => {
    let resolveCatalog;
    api.salesReportCatalog.mockImplementation(
      () => new Promise((resolve) => { resolveCatalog = resolve; }),
    );
    const { container, root } = renderAt("/laporan?view=saved");
    expect(api.salesReportCatalog).toHaveBeenCalled();
    await act(async () => {
      resolveCatalog({ memorized: [] });
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="report-catalog-saved-empty"]')).toBeTruthy();
    root.unmount();
    container.remove();
  });

  test("favorites stay isolated per user id", () => {
    const { container, root } = renderAt("/laporan");
    act(() => {
      container.querySelector('[data-testid="report-catalog-fav-penjualan-per-barang"]').click();
    });
    expect(JSON.parse(window.localStorage.getItem("eternal-report-favorites-v1:rpc-user-1"))).toEqual([
      "penjualan-per-barang",
    ]);
    expect(window.localStorage.getItem("eternal-report-favorites-v1:other")).toBeNull();
    root.unmount();
    container.remove();
  });

  test("continuous typing keeps focus class and filters cards", () => {
    const { container, root } = renderAt("/laporan");
    const input = container.querySelector('[data-testid="report-catalog-search"]');
    const wrap = container.querySelector(".rpc-search");
    const before = container.querySelectorAll('[data-testid^="report-catalog-card-"]').length;
    expect(before).toBeGreaterThan(0);
    act(() => {
      input.focus();
      input.dispatchEvent(new Event("focus", { bubbles: true }));
    });
    expect(wrap.className).toMatch(/is-focused/);
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    let typed = "";
    for (const ch of "ABCDE123") {
      typed += ch;
      act(() => {
        setter.call(input, typed);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      expect(document.activeElement).toBe(input);
      expect(wrap.className).toMatch(/is-focused/);
    }
    expect(input.value).toBe("ABCDE123");
    expect(wrap.className).toMatch(/has-value/);
    expect(container.querySelectorAll('[data-testid^="report-catalog-card-"]').length).toBe(0);
    expect(document.activeElement).toBe(input);
    root.unmount();
    container.remove();
  });

  test("restricted finance-only catalog hides sales cards", () => {
    mockPermissions.clear();
    mockPermissions.add("nav.reports");
    mockPermissions.add("profit_loss.view");
    const { container, root } = renderAt("/laporan");
    expect(container.querySelector('[data-testid="report-catalog-card-laba-rugi"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="report-catalog-card-penjualan-per-barang"]')).toBeNull();
    root.unmount();
    container.remove();
  });
});
