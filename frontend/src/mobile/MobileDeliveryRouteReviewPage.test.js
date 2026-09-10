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
  default: ({ title }) => <header data-testid="mobile-top-bar">{title}</header>,
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
    // deselect eligible ord-b (auto-selected); keep ord-a by checking it
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
});
