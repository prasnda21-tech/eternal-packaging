import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import DeliveryScheduleRouteReviewPage from "./DeliveryScheduleRouteReviewPage";
import DeliveryRouteReviewContent from "../components/DeliveryRouteReviewContent";

const mockHasPerm = jest.fn(() => false);
const mockReview = jest.fn();
const mockOptimize = jest.fn();
const mockListDrivers = jest.fn();
const mockAssign = jest.fn();
const mockBulkAssign = jest.fn();
const mockNavigate = jest.fn();
const mockSearchParams = new URLSearchParams("ids=ord-a,ord-b");

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => mockNavigate,
  useSearchParams: () => [mockSearchParams],
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: (perm) => mockHasPerm(perm) }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    reviewScheduleRouteOptimize: (...args) => mockReview(...args),
    optimizeFromSchedule: (...args) => mockOptimize(...args),
    listEligibleDeliveryDrivers: (...args) => mockListDrivers(...args),
    assignDeliveryDriver: (...args) => mockAssign(...args),
    bulkAssignDeliveryDriver: (...args) => mockBulkAssign(...args),
  },
}));

jest.mock("../mobile/MobileTopBar", () => ({
  __esModule: true,
  default: () => <header data-testid="mobile-top-bar">should-not-render</header>,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const baseRows = [
  {
    sales_order_id: "ord-a",
    customer_name: "Cust A",
    order_number: "PO-A",
    eligible: false,
    reason: "Belum ada driver",
    driver_user_id: null,
    driver_name: null,
    optimization_status_label: "Belum Dioptimasi",
    lat: null,
    lng: null,
  },
  {
    sales_order_id: "ord-b",
    customer_name: "Cust B",
    order_number: "PO-B",
    eligible: true,
    driver_user_id: "drv-a",
    driver_name: "Driver A",
    optimization_status_label: "Sudah Dioptimasi",
    lat: -6.2,
    lng: 106.8,
  },
];

describe("DeliveryScheduleRouteReviewPage desktop shell", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockReview.mockReset();
    mockOptimize.mockReset();
    mockListDrivers.mockReset();
    mockAssign.mockReset();
    mockBulkAssign.mockReset();
    mockNavigate.mockReset();
    window.confirm = jest.fn(() => true);
    mockListDrivers.mockResolvedValue({
      drivers: [
        { id: "drv-a", name: "Driver A" },
        { id: "drv-b", name: "Driver B" },
      ],
    });
    mockReview.mockResolvedValue({ rows: baseRows, summary: { selected: 2 }, groups: [] });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("renders desktop AppShell chrome, not MobileTopBar / mobile ops root", async () => {
    mockHasPerm.mockImplementation(
      (p) => p === "route_optimizer.create" || p === "delivery.location.manage",
    );
    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    expect(container.querySelector('[data-testid="ds-review-root"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-review-heading"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-top-bar"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-operational-root"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-review-page"]')).toBeTruthy();
  });

  test("desktop review uses natural-scroll wrapper, not ship-status clipping viewport", async () => {
    mockHasPerm.mockImplementation((p) => p === "route_optimizer.create");
    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    const rootEl = container.querySelector('[data-testid="ds-review-root"]');
    const mainEl = container.querySelector('[data-testid="mds-review-page"]');
    expect(rootEl).toBeTruthy();
    expect(rootEl.classList.contains("ds-delivery-review-root")).toBe(true);
    // ship-status-page locks html/body/.content to 100dvh + overflow:hidden
    expect(rootEl.classList.contains("ship-status-page")).toBe(false);
    expect(mainEl.classList.contains("ds-review-page")).toBe(true);
    expect(mainEl.classList.contains("mop-content")).toBe(false);
    expect(container.querySelector(".mop-delivery-review-root")).toBeFalsy();
  });

  test("Kembali returns to desktop Jadwal Pengiriman", async () => {
    mockHasPerm.mockImplementation((p) => p === "route_optimizer.create");
    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    const back = container.querySelector('[data-testid="mds-review-back"]');
    expect(back.getAttribute("href")).toBe("/produksi/jadwal-pengiriman");
    expect(back.getAttribute("href")).not.toMatch(/^\/m\//);
  });

  test("driver selector + Simpan assigns without calling optimize", async () => {
    mockHasPerm.mockImplementation(
      (p) => p === "route_optimizer.create" || p === "delivery.location.manage",
    );
    mockAssign.mockResolvedValue({
      ok: true,
      result: { status: "updated", driver_user_id: "drv-b", driver_name: "Driver B", sales_order_id: "ord-a" },
    });
    mockReview
      .mockResolvedValueOnce({ rows: [baseRows[0]], summary: {}, groups: [] })
      .mockResolvedValueOnce({
        rows: [{
          ...baseRows[0],
          eligible: true,
          reason: null,
          driver_user_id: "drv-b",
          driver_name: "Driver B",
        }],
        summary: {},
        groups: [{ driver_user_id: "drv-b", sales_order_ids: ["ord-a"] }],
      });

    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    const select = container.querySelector('[data-testid="mds-review-driver-select-ord-a"]');
    await act(async () => {
      select.value = "drv-b";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="mds-review-driver-save-ord-a"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockAssign).toHaveBeenCalledWith("ord-a", "drv-b");
    expect(mockOptimize).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mds-review-driver-label-ord-a"]').textContent)
      .toContain("Driver B");
  });

  test("Optimalkan Rute uses from-schedule optimizer; Lihat Rute goes to /optimasi-rute/:id", async () => {
    const open = jest.fn();
    const origOpen = window.open;
    window.open = open;
    mockHasPerm.mockImplementation((p) => p === "route_optimizer.create" || p === "route_optimizer.optimize");
    mockOptimize.mockResolvedValue({
      ok: true,
      results: [{
        driver_user_id: "drv-a",
        driver_name: "Driver A",
        ok: true,
        route_id: "iro-99",
      }],
    });
    mockReview.mockResolvedValue({
      rows: [{ ...baseRows[1], eligible: true }],
      summary: {},
      groups: [],
    });
    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    await act(async () => {
      container.querySelector('[data-testid="mds-review-confirm"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockOptimize).toHaveBeenCalledWith(["ord-b"]);
    const openBtn = container.querySelector('[data-testid="mds-review-open-iro-99"]');
    expect(openBtn).toBeTruthy();
    await act(async () => {
      openBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalledWith("/optimasi-rute/iro-99", expect.objectContaining({
      state: expect.objectContaining({ from: expect.any(String) }),
    }));
    expect(mockNavigate.mock.calls[0][0]).not.toMatch(/^\/m\//);
    expect(open).not.toHaveBeenCalled();
    window.open = origOpen;
  });

  test("Status Atur Lokasi and eligibility remain canonical / backend-authoritative", async () => {
    mockHasPerm.mockImplementation((p) => p === "route_optimizer.create");
    mockReview.mockResolvedValue({
      rows: [
        {
          ...baseRows[0],
          sales_order_id: "ord-zero",
          eligible: false,
          reason: "Koordinat belum tersedia",
          reasons: ["Koordinat belum tersedia"],
          lat: 0,
          lng: 0,
          address: "Jl Teks Saja",
        },
        {
          ...baseRows[1],
          sales_order_id: "ord-ok",
          eligible: true,
          reason: null,
          reasons: [],
        },
      ],
      summary: {},
      groups: [],
    });
    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    expect(container.querySelector('[data-testid="mds-review-location-status-ord-zero"]').textContent)
      .toContain("Belum Diatur");
    expect(container.querySelector('[data-testid="mds-review-reason-ord-zero"]').textContent)
      .toContain("Koordinat belum tersedia");
    expect(container.querySelector('[data-testid="mds-review-location-status-ord-ok"]').textContent)
      .toContain("Sudah Diatur");
    expect(container.querySelector('[data-testid="mds-review-eligibility-status-ord-ok"]').textContent)
      .toMatch(/Eligible/);
  });
});

describe("desktop vs PWA review wiring (static)", () => {
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const desktopPage = fs.readFileSync(path.join(__dirname, "DeliveryScheduleRouteReviewPage.jsx"), "utf8");
  const mobilePage = fs.readFileSync(path.join(__dirname, "../mobile/MobileDeliveryRouteReviewPage.jsx"), "utf8");
  const shared = fs.readFileSync(path.join(__dirname, "../components/DeliveryRouteReviewContent.jsx"), "utf8");
  const layout = fs.readFileSync(path.join(__dirname, "../mobile/RouteOptimizerLayout.jsx"), "utf8");

  test("desktop review is under AppShell; PWA review stays under MobileOperationalLayout", () => {
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman\/review-optimasi"/);
    expect(app).toMatch(/path="\/m\/delivery-schedule\/review-optimasi"/);
    expect(app).toMatch(/DeliveryScheduleRouteReviewPage/);
    expect(app).toMatch(/MobileDeliveryRouteReviewPage/);
    const desktopRouteIdx = app.indexOf('path="/produksi/jadwal-pengiriman/review-optimasi"');
    const pwaRouteIdx = app.indexOf('path="/m/delivery-schedule/review-optimasi"');
    const appShellIdx = app.indexOf("<AppShell");
    const mopIdx = app.indexOf("MobileOperationalLayout");
    expect(desktopRouteIdx).toBeGreaterThan(appShellIdx);
    expect(pwaRouteIdx).toBeGreaterThan(mopIdx);
    expect(pwaRouteIdx).toBeLessThan(appShellIdx);
  });

  test("both shells reuse shared review content; desktop never marks mobile ops", () => {
    expect(desktopPage).toMatch(/DeliveryRouteReviewContent/);
    expect(desktopPage).toMatch(/shell="desktop"/);
    expect(mobilePage).toMatch(/DeliveryRouteReviewContent/);
    expect(mobilePage).toMatch(/shell="mobile"/);
    expect(shared).toMatch(/navigateToOptimasiRute/);
    expect(shared).toMatch(/optimizeFromSchedule/);
    expect(shared).toMatch(/assignDeliveryDriver/);
    expect(shared).not.toMatch(/markMobileOpsShell/);
    expect(layout).toMatch(/preferMobileOpsShell/);
  });

  test("desktop scroll contract: no ship-status-page; PWA keeps mop overflow scroller", () => {
    expect(shared).toMatch(/ds-delivery-review-root/);
    expect(shared).toMatch(/ds-review-page/);
    expect(shared).toMatch(/mop-delivery-review-root/);
    // Desktop root must not reuse the locked ship-status viewport.
    expect(shared).not.toMatch(/ship-status-page ds-delivery-review-root/);
    expect(shared).not.toMatch(/isDesktop\s*\?\s*["']ship-status-page/);
    const desktopRootMatch = shared.match(/const rootClass = isDesktop\s*\n?\s*\?\s*["']([^"']+)["']/);
    expect(desktopRootMatch?.[1]).toBe("ds-delivery-review-root");
    expect(desktopRootMatch?.[1]).not.toMatch(/ship-status-page/);

    expect(css).toMatch(/\.ds-delivery-review-root\s*\{[^}]*overflow\s*:\s*visible/s);
    expect(css).toMatch(/\.mop-delivery-review-root \.mop-content\.mds-review-page\s*\{[^}]*overflow-y\s*:\s*auto/s);
  });

  test("shared content export is the single review controller", () => {
    expect(typeof DeliveryRouteReviewContent).toBe("function");
  });
});
