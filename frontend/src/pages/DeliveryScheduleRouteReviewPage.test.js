import { act } from "react";
import { createRoot } from "react-dom/client";
import DeliveryScheduleRouteReviewPage from "./DeliveryScheduleRouteReviewPage";

const mockHasPerm = jest.fn(() => false);
const mockReview = jest.fn();
const mockCreateFleets = jest.fn();
const mockPreviewFleets = jest.fn();
const mockOptimizeFromSchedule = jest.fn();
const mockNavigate = jest.fn();
let mockSearchParams = new URLSearchParams("ids=ord-a,ord-b");

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
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
    createFleetsFromSchedule: (...args) => mockCreateFleets(...args),
    previewFleetsFromSchedule: (...args) => mockPreviewFleets(...args),
    optimizeFromSchedule: (...args) => mockOptimizeFromSchedule(...args),
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
    order_number: "PO-A",
    customer_name: "Cust A",
    eligible: true,
    urgent: true,
    reasons: [],
    reason: null,
    sj_label: "SJ-A",
    lat: -6.2,
    lng: 106.8,
    address: "Jl A",
  },
  {
    sales_order_id: "ord-b",
    order_number: "PO-B",
    customer_name: "Cust B",
    eligible: true,
    urgent: false,
    reasons: [],
    sj_label: "SJ-B",
    lat: -0.95,
    lng: 100.35,
    address: "Jl B",
  },
  {
    sales_order_id: "ord-c",
    order_number: "PO-C",
    customer_name: "Cust C",
    eligible: false,
    urgent: false,
    reason: "Surat Jalan belum siap.",
    reasons: ["Surat Jalan belum siap."],
    sj_label: "—",
    lat: -6.3,
    lng: 106.9,
  },
];

function createResult(count) {
  const routes = [
    {
      route_id: "iro-1",
      label: "Armada 1",
      order_ids: count === 1 ? ["ord-a", "ord-b"] : ["ord-a"],
      stop_count: count === 1 ? 2 : 1,
      urgent_count: 1,
      estimated_distance_m: 1200,
      status: "OPTIMIZED",
      fleet_group_id: count === 2 ? "ifg-test" : null,
    },
  ];
  if (count === 2) {
    routes.push({
      route_id: "iro-2",
      label: "Armada 2",
      order_ids: ["ord-b"],
      stop_count: 1,
      urgent_count: 0,
      estimated_distance_m: 800,
      status: "OPTIMIZED",
      fleet_group_id: "ifg-test",
    });
  }
  return {
    ok: true,
    created: true,
    reused: false,
    fleet_count: count,
    created_route_count: count,
    fleet_group_id: count === 2 ? "ifg-test" : null,
    eligible_order_ids: ["ord-a", "ord-b"],
    routes,
  };
}

describe("DeliveryScheduleRouteReviewPage desktop one-stage create", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockSearchParams = new URLSearchParams("ids=ord-a,ord-b,ord-c");
    mockHasPerm
      .mockReset()
      .mockImplementation(
        (p) =>
          p === "route_optimizer.create" ||
          p === "route_optimizer.optimize" ||
          p === "delivery.schedule.optimize",
      );
    mockReview.mockReset().mockResolvedValue({ rows: baseRows, summary: { selected: 3, eligible: 2 } });
    mockCreateFleets.mockReset().mockResolvedValue(createResult(2));
    mockPreviewFleets.mockReset();
    mockOptimizeFromSchedule.mockReset();
    mockNavigate.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("renders prominent create panel and no driver/second-save UI", async () => {
    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    expect(container.querySelector('[data-testid="ds-review-root"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-review-create-panel"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-review-fleet-count"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-review-fleet-count-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-review-fleet-count-2"]')).toBeTruthy();
    expect(container.textContent).toMatch(/Buat Optimasi Rute|Jumlah Armada|Optimasi Rute/);
    expect(container.querySelector('[data-testid="mds-review-fleet-driver-select-armada_1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-review-simpan-2-rute"]')).toBeFalsy();
    expect(container.textContent).not.toMatch(/Simpan 2 Rute/);
  });

  test("2 armada create payload; never calls preview or legacy optimize", async () => {
    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    await act(async () => {
      container
        .querySelector('[data-testid="mds-review-optimasi-armada"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockCreateFleets).toHaveBeenCalledWith(["ord-a", "ord-b"], 2);
    expect(mockPreviewFleets).not.toHaveBeenCalled();
    expect(mockOptimizeFromSchedule).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mds-review-success"]')).toBeTruthy();
    expect(container.textContent).toMatch(/2 rute berhasil dibuat/);
    expect(container.querySelector('[data-testid="mds-review-created-route-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-review-created-route-2"]')).toBeTruthy();
  });

  test("1 armada create renders exactly one created-route summary", async () => {
    mockCreateFleets.mockResolvedValue(createResult(1));
    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    await act(async () => {
      container.querySelector('[data-testid="mds-review-fleet-count-1"]').click();
    });
    await act(async () => {
      container
        .querySelector('[data-testid="mds-review-optimasi-armada"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockCreateFleets).toHaveBeenCalledWith(["ord-a", "ord-b"], 1);
    expect(container.querySelector('[data-testid="mds-review-created-route-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-review-created-route-2"]')).toBeFalsy();
    expect(container.textContent).toMatch(/1 rute berhasil dibuat/);
  });

  test("selection and fleet-count changes clear success; loading blocks duplicate submit", async () => {
    let resolveCreate;
    mockCreateFleets.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCreate = resolve;
        }),
    );
    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    await act(async () => {
      container
        .querySelector('[data-testid="mds-review-optimasi-armada"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.textContent).toMatch(/Membuat dan Mengoptimalkan Rute/);
    expect(container.querySelector('[data-testid="mds-review-optimasi-armada"]').disabled).toBe(true);
    await act(async () => {
      container
        .querySelector('[data-testid="mds-review-optimasi-armada"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockCreateFleets).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveCreate(createResult(2));
    });
    expect(container.querySelector('[data-testid="mds-review-success"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="mds-review-check-ord-a"]').click();
    });
    expect(container.querySelector('[data-testid="mds-review-success"]')).toBeFalsy();
  });

  test("Buka Optimasi Rute navigates to list", async () => {
    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    await act(async () => {
      container
        .querySelector('[data-testid="mds-review-optimasi-armada"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container
        .querySelector('[data-testid="mds-review-open-optimasi-rute"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalled();
    const arg = mockNavigate.mock.calls[0][0];
    expect(String(arg)).toMatch(/optimasi-rute/);
  });

  test("backend error renders clearly", async () => {
    mockCreateFleets.mockRejectedValue({ response: { data: { detail: "PO eligible tidak cukup." } } });
    await act(async () => {
      root.render(<DeliveryScheduleRouteReviewPage />);
    });
    await act(async () => {
      container
        .querySelector('[data-testid="mds-review-optimasi-armada"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mds-review-error"]')).toBeTruthy();
    expect(container.textContent).toMatch(/PO eligible tidak cukup|Gagal membuat/);
  });
});
