import { act } from "react";
import { createRoot } from "react-dom/client";
import MobileDeliveryRouteReviewPage from "./MobileDeliveryRouteReviewPage";

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

jest.mock("./MobileTopBar", () => ({
  __esModule: true,
  default: ({ title, onBack }) => (
    <header data-testid="mobile-top-bar">
      {title}
      {onBack ? (
        <button type="button" data-testid="mobile-back" onClick={onBack}>Kembali</button>
      ) : null}
    </header>
  ),
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
  },
  {
    sales_order_id: "ord-b",
    customer_name: "Cust B",
    order_number: "PO-B",
    eligible: true,
    driver_user_id: "drv-a",
    driver_name: "Driver A",
    optimization_status_label: "Sudah Dioptimasi",
  },
];

describe("MobileDeliveryRouteReviewPage driver assign", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockReview.mockReset();
    mockListDrivers.mockReset();
    mockAssign.mockReset();
    mockBulkAssign.mockReset();
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

  test("shows Belum Ada Driver and assign controls when authorized", async () => {
    mockHasPerm.mockImplementation(
      (p) => p === "route_optimizer.create" || p === "delivery.location.manage",
    );
    await act(async () => {
      root.render(<MobileDeliveryRouteReviewPage />);
    });
    expect(container.querySelector('[data-testid="mds-review-driver-label-ord-a"]').textContent).toContain("Belum Ada Driver");
    expect(container.querySelector('[data-testid="mds-review-driver-label-ord-b"]').textContent).toContain("Driver A");
    expect(container.querySelector('[data-testid="mds-review-bulk-driver"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-review-pick-driver-ord-a"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-review-select-all"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-review-exclude"]')).toBeTruthy();
    expect(mockListDrivers).toHaveBeenCalled();
  });

  test("hides assign UI without delivery.location.manage", async () => {
    mockHasPerm.mockImplementation((p) => p === "route_optimizer.create");
    await act(async () => {
      root.render(<MobileDeliveryRouteReviewPage />);
    });
    expect(container.querySelector('[data-testid="mds-review-list"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-review-bulk-driver"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-review-pick-driver-ord-a"]')).toBeFalsy();
    expect(mockListDrivers).not.toHaveBeenCalled();
  });

  test("per-row Simpan uses exact driver id and refreshes", async () => {
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
      root.render(<MobileDeliveryRouteReviewPage />);
    });
    const select = container.querySelector('[data-testid="mds-review-driver-select-ord-a"]');
    await act(async () => {
      select.value = "drv-b";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const save = container.querySelector('[data-testid="mds-review-driver-save-ord-a"]');
    await act(async () => {
      save.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockAssign).toHaveBeenCalledWith("ord-a", "drv-b");
    expect(container.querySelector('[data-testid="mds-review-driver-label-ord-a"]').textContent).toContain("Driver B");
  });

  test("Driver summary uses persisted driver after Save even when still ineligible", async () => {
    mockHasPerm.mockImplementation(
      (p) => p === "route_optimizer.create" || p === "delivery.location.manage",
    );
    mockAssign.mockResolvedValue({
      ok: true,
      result: { status: "updated", driver_user_id: "drv-b", driver_name: "Driver B", sales_order_id: "ord-a" },
    });
    mockReview
      .mockResolvedValueOnce({
        rows: [
          { ...baseRows[0], sales_order_id: "ord-a", eligible: false, reason: "Already in active valid route", driver_user_id: null, driver_name: null },
          { ...baseRows[0], sales_order_id: "ord-b", eligible: false, reason: "Already in active valid route", driver_user_id: null, driver_name: null },
        ],
        summary: {},
        groups: [],
      })
      .mockResolvedValueOnce({
        // After save: draft cleared via reload; persisted drivers present; still ineligible.
        rows: [
          {
            ...baseRows[0],
            sales_order_id: "ord-a",
            eligible: false,
            reason: "Already in active valid route",
            driver_user_id: "drv-b",
            driver_name: "Driver B",
          },
          {
            ...baseRows[0],
            sales_order_id: "ord-b",
            eligible: false,
            reason: "Already in active valid route",
            driver_user_id: "drv-b",
            driver_name: "Driver B",
          },
        ],
        summary: {},
        groups: [],
      });

    await act(async () => {
      root.render(<MobileDeliveryRouteReviewPage />);
    });
    const summaryBefore = container.querySelector('[data-testid="mds-review-summary"]').textContent;
    expect(summaryBefore).toMatch(/Dipilih:\s*2/);
    expect(summaryBefore).toMatch(/Eligible:\s*0/);
    expect(summaryBefore).toMatch(/Tidak eligible:\s*2/);
    expect(summaryBefore).toMatch(/Driver:\s*0/);

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
    expect(container.querySelector('[data-testid="mds-review-driver-label-ord-a"]').textContent).toContain("Driver B");
    expect(container.querySelector('[data-testid="mds-review-driver-label-ord-b"]').textContent).toContain("Driver B");
    const summaryAfter = container.querySelector('[data-testid="mds-review-summary"]').textContent;
    expect(summaryAfter).toMatch(/Dipilih:\s*2/);
    expect(summaryAfter).toMatch(/Eligible:\s*0/);
    expect(summaryAfter).toMatch(/Tidak eligible:\s*2/);
    expect(summaryAfter).toMatch(/Driver:\s*2/);
    expect(summaryAfter).toMatch(/Rute dibuat:\s*0/);
    expect(container.querySelector('[data-testid="mds-review-confirm"]').disabled).toBe(true);
  });

  test("bulk Terapkan ke Terpilih posts exact selected ids", async () => {
    mockHasPerm.mockImplementation(
      (p) => p === "route_optimizer.create" || p === "delivery.location.manage",
    );
    mockBulkAssign.mockResolvedValue({
      ok: true,
      counts: { updated: 1, unchanged: 0, failed: 0 },
      results: [{ sales_order_id: "ord-a", status: "updated" }],
    });
    await act(async () => {
      root.render(<MobileDeliveryRouteReviewPage />);
    });
    // Initial select-all now includes ineligible ord-a; deselect eligible ord-b.
    const checkB = container.querySelector('[data-testid="mds-review-check-ord-b"]');
    await act(async () => {
      checkB.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const checkA = container.querySelector('[data-testid="mds-review-check-ord-a"]');
    if (!checkA.checked) {
      await act(async () => {
        checkA.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
    }
    const bulkSelect = container.querySelector('[data-testid="mds-review-bulk-driver-select"]');
    await act(async () => {
      bulkSelect.value = "drv-b";
      bulkSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="mds-review-bulk-apply"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockBulkAssign).toHaveBeenCalled();
    const [ids, driverId] = mockBulkAssign.mock.calls[0];
    expect(ids).toEqual(["ord-a"]);
    expect(driverId).toBe("drv-b");
    expect(window.confirm).toHaveBeenCalled();
  });

  test("Pilih Semua selects ineligible rows and Keluarkan clears selection", async () => {
    mockHasPerm.mockImplementation(
      (p) => p === "route_optimizer.create" || p === "delivery.location.manage",
    );
    mockReview.mockResolvedValue({
      rows: [baseRows[0]],
      summary: {},
      groups: [],
    });
    await act(async () => {
      root.render(<MobileDeliveryRouteReviewPage />);
    });
    // After load, ineligible row is pre-selected for bulk driver workflow.
    expect(container.querySelector('[data-testid="mds-review-check-ord-a"]').checked).toBe(true);
    expect(container.querySelector('[data-testid="mds-review-summary"]').textContent).toMatch(/Dipilih:\s*1/);
    expect(container.querySelector('[data-testid="mds-review-summary"]').textContent).toMatch(/Eligible:\s*0/);

    await act(async () => {
      container.querySelector('[data-testid="mds-review-exclude"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mds-review-check-ord-a"]').checked).toBe(false);
    expect(container.querySelector('[data-testid="mds-review-summary"]').textContent).toMatch(/Dipilih:\s*0/);

    await act(async () => {
      container.querySelector('[data-testid="mds-review-select-all"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mds-review-check-ord-a"]').checked).toBe(true);
    expect(container.querySelector('[data-testid="mds-review-summary"]').textContent).toMatch(/Dipilih:\s*1/);
  });

  test("Kembali links to canonical delivery schedule path", async () => {
    mockHasPerm.mockImplementation((p) => p === "route_optimizer.create");
    await act(async () => {
      root.render(<MobileDeliveryRouteReviewPage />);
    });
    const back = container.querySelector('[data-testid="mds-review-back"]');
    expect(back.getAttribute("href")).toBe("/m/delivery-schedule");
  });

  test("Optimalkan Rute stays disabled when selected rows are ineligible", async () => {
    mockHasPerm.mockImplementation(
      (p) => p === "route_optimizer.create" || p === "delivery.location.manage",
    );
    mockReview.mockResolvedValue({
      rows: [baseRows[0]],
      summary: {},
      groups: [],
    });
    await act(async () => {
      root.render(<MobileDeliveryRouteReviewPage />);
    });
    const confirm = container.querySelector('[data-testid="mds-review-confirm"]');
    expect(confirm.disabled).toBe(true);
  });

  test("shows Status Atur Lokasi and authoritative eligibility reason per PO", async () => {
    mockHasPerm.mockImplementation(
      (p) => p === "route_optimizer.create" || p === "delivery.location.manage",
    );
    mockReview.mockResolvedValue({
      rows: [
        {
          sales_order_id: "ord-route",
          customer_name: "Cust Route",
          order_number: "PO-ROUTE",
          eligible: false,
          reason: "Already in active valid route",
          reasons: ["Already in active valid route"],
          lat: -6.2,
          lng: 106.8,
          driver_user_id: "drv-a",
          driver_name: "Driver A",
          optimization_status_label: "Sudah Dioptimasi",
        },
        {
          sales_order_id: "ord-nocoords",
          customer_name: "Cust NoCoords",
          order_number: "PO-NC",
          eligible: false,
          reason: "Koordinat belum tersedia",
          reasons: ["Koordinat belum tersedia"],
          lat: null,
          lng: null,
          driver_user_id: null,
          driver_name: null,
          optimization_status_label: "Belum Dioptimasi",
        },
        {
          sales_order_id: "ord-ok",
          customer_name: "Cust OK",
          order_number: "PO-OK",
          eligible: true,
          reason: null,
          reasons: [],
          lat: -6.21,
          lng: 106.81,
          driver_user_id: "drv-a",
          driver_name: "Driver A",
          optimization_status_label: "Belum Dioptimasi",
        },
      ],
      summary: {},
      groups: [],
    });
    await act(async () => {
      root.render(<MobileDeliveryRouteReviewPage />);
    });

    // CASE 1+4: active route + location set
    expect(container.querySelector('[data-testid="mds-review-location-status-ord-route"]').textContent)
      .toContain("Sudah Diatur");
    expect(container.querySelector('[data-testid="mds-review-eligibility-status-ord-route"]').textContent)
      .toContain("Tidak Eligible");
    expect(container.querySelector('[data-testid="mds-review-reason-ord-route"]').textContent)
      .toContain("Already in active valid route");
    expect(container.querySelector('[data-testid="mds-review-driver-label-ord-route"]').textContent)
      .toContain("Driver A");

    // CASE 2: location missing + canonical location reason
    expect(container.querySelector('[data-testid="mds-review-location-status-ord-nocoords"]').textContent)
      .toContain("Belum Diatur");
    expect(container.querySelector('[data-testid="mds-review-reason-ord-nocoords"]').textContent)
      .toContain("Koordinat belum tersedia");

    // CASE 3: location set + eligible (no reason block)
    expect(container.querySelector('[data-testid="mds-review-location-status-ord-ok"]').textContent)
      .toContain("Sudah Diatur");
    expect(container.querySelector('[data-testid="mds-review-eligibility-status-ord-ok"]').textContent)
      .toMatch(/Eligible/);
    expect(container.querySelector('[data-testid="mds-review-reason-ord-ok"]')).toBeFalsy();

    // Optimalkan Rute still requires eligible selection count
    const summary = container.querySelector('[data-testid="mds-review-summary"]').textContent;
    expect(summary).toMatch(/Eligible:\s*1/);
  });

  test("Lihat Rute uses in-app navigate to /optimasi-rute/:id (no window.open / target=_blank)", async () => {
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
        route_id: "iro-42",
      }],
    });
    mockReview.mockResolvedValue({
      rows: [{ ...baseRows[1], eligible: true }],
      summary: {},
      groups: [],
    });
    await act(async () => {
      root.render(<MobileDeliveryRouteReviewPage />);
    });
    await act(async () => {
      container.querySelector('[data-testid="mds-review-confirm"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const openBtn = container.querySelector('[data-testid="mds-review-open-iro-42"]');
    expect(openBtn).toBeTruthy();
    expect(openBtn.tagName).toBe("BUTTON");
    expect(openBtn.getAttribute("href")).toBeNull();
    expect(openBtn.getAttribute("target")).toBeNull();
    await act(async () => {
      openBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalledWith("/optimasi-rute/iro-42", expect.objectContaining({
      state: expect.objectContaining({ from: expect.any(String) }),
    }));
    expect(open).not.toHaveBeenCalled();
    window.open = origOpen;
  });
});
