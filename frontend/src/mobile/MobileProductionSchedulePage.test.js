import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { FILTER_DEFAULTS } from "../lib/productionScheduleFilters";
import { PRODUCTION_SCHEDULE_TABLE_COLS } from "../lib/productionScheduleTable";
import { formatIdQty } from "../lib/productionScheduleQty";
import {
  MOBILE_SCHEDULE_ZOOM_DEFAULT,
  MOBILE_SCHEDULE_ZOOM_FIT,
  MOBILE_SCHEDULE_ZOOM_MAX,
  MOBILE_SCHEDULE_ZOOM_MIN,
} from "../lib/mobileSchedule";
import MobileProductionSchedulePage, { scheduleLoadErrorMessage } from "./MobileProductionSchedulePage";

const mockHasPerm = jest.fn(() => false);
const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: (perm) => mockHasPerm(perm) }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    productionDashboard: jest.fn(),
    productionExport: jest.fn(),
    getOrder: jest.fn(),
    listOrders: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const sampleRows = [
  {
    id: "po-1",
    po_number: "PO-1001",
    customer: "PT Example",
    outlet: "Tebet",
    deadline: "2026-09-10T00:00:00Z",
    delivery_deadline_effective: "2026-09-01T00:00:00Z",
    delivery_deadline_source: "auto",
    delivery_deadline_status: "overdue",
    urgent: true,
    work_date: "2026-08-25",
    operator_work_dates: [
      { split_id: "s1", operator_name: "Budi", work_date: "2026-08-25" },
      { split_id: "s2", operator_name: "Sari", work_date: "2026-08-26" },
    ],
    current_stage: 8,
    status_final: "Operator",
    total_quantity: 25000,
    print_qty_total: 20000,
    operator_assigned_qty: 10000,
    design_status: "Approved",
    payment_status: "Approved",
    packing_status: "Belum",
    operators: [
      { line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz", operator_name: "Budi", quantity: 10000, line_printable: true },
      { line_idx: 2, line_id: "lid-b", line_name: "Cup 16oz", operator_name: "Sari", quantity: 500, line_printable: true },
    ],
  },
  {
    id: "po-2",
    po_number: "PO-1002",
    customer: "CV Lain",
    outlet: "PIK",
    deadline: "2026-09-12T00:00:00Z",
    work_date: null,
    current_stage: 8,
    status_final: "Operator",
    total_quantity: 100,
    operators: [],
  },
];

describe("scheduleLoadErrorMessage", () => {
  test("object FastAPI detail does not become a React child object", () => {
    const msg = scheduleLoadErrorMessage({
      response: { status: 500, data: { detail: { message: "server busy" } } },
    });
    expect(typeof msg).toBe("string");
    expect(msg).toContain("server busy");
  });
});

describe("MobileProductionSchedulePage", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockNavigate.mockReset();
    api.productionDashboard.mockReset();
    api.productionExport.mockReset();
    api.getOrder.mockReset();
    api.listOrders.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const renderPage = async () => {
    await act(async () => { root.render(<MobileProductionSchedulePage />); });
    await flush();
  };

  const zoomVar = () => container.querySelector('[data-testid="mobile-schedule-table"]')
    ?.style.getPropertyValue("--mop-sched-zoom");

  test("initial loading state renders", async () => {
    let resolveDash;
    api.productionDashboard.mockReturnValue(new Promise((resolve) => { resolveDash = resolve; }));
    await act(async () => { root.render(<MobileProductionSchedulePage />); });
    expect(container.textContent).toContain("Memuat jadwal produksi...");
    await act(async () => { resolveDash({ rows: [] }); });
    await flush();
  });

  test("renders one PO row with desktop columns and multiline same-name lines", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows, total: 2 });
    await renderPage();
    expect(api.productionDashboard).toHaveBeenCalledWith({
      include_cancelled: FILTER_DEFAULTS.includeCancelled,
      include_completed: FILTER_DEFAULTS.includeCompleted,
    });
    expect(container.querySelector('[data-testid="mobile-schedule-prev"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-today"]')).toBeFalsy();
    expect(container.textContent).not.toContain("pada tanggal ini");
    const headers = [...container.querySelectorAll("thead th")].map((th) => th.textContent);
    expect(headers).toEqual(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label));
    expect(container.querySelectorAll('[data-testid="mobile-schedule-row-PO-1001"]')).toHaveLength(1);
    expect(container.querySelector('[data-testid="mobile-schedule-qty-line-PO-1001-0"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-qty-line-PO-1001-1"]')).toBeTruthy();
    expect(container.textContent).toContain("Budi");
    expect(container.textContent).toContain("Sari");
    expect(container.textContent).toContain("Cup 16oz");
    expect(container.textContent).toContain("2026-08-25");
    expect(container.textContent).toContain("Belum Dijadwalkan");
    expect(container.textContent).toContain(`${formatIdQty(10000)} / ${formatIdQty(20000)}`);
    expect(container.textContent).toContain("OVERDUE");
    expect(container.querySelector(".mop-schedule-table .urgent-badge")).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-count"]').textContent).toBe("2 / 2 entri");
    expect(container.querySelector('[data-testid="mobile-schedule-include-completed"]').checked).toBe(true);
    expect(container.querySelector('[data-testid="mobile-schedule-include-cancelled"]').checked).toBe(false);
    expect(api.getOrder).not.toHaveBeenCalled();
    expect(api.listOrders).not.toHaveBeenCalled();
  });

  test("empty canonical dataset shows Belum ada jadwal produksi", async () => {
    api.productionDashboard.mockResolvedValue({ rows: [] });
    await renderPage();
    expect(container.textContent).toContain("Belum ada jadwal produksi.");
    expect(container.textContent).not.toContain("pada tanggal ini");
    expect(container.querySelector("table.mop-schedule-table")).toBeFalsy();
  });

  test("API error shows Gagal memuat and Coba Lagi", async () => {
    api.productionDashboard.mockRejectedValue({
      response: { status: 500, data: { detail: { message: "upstream" } } },
    });
    await renderPage();
    expect(container.textContent).toContain("Gagal memuat jadwal produksi.");
    expect(container.textContent).toContain("Coba Lagi");
    expect(container.querySelector("table.mop-schedule-table")).toBeFalsy();
  });

  test("Coba Lagi retries and then renders data", async () => {
    api.productionDashboard
      .mockRejectedValueOnce({ response: { status: 500, data: { detail: "boom" } } })
      .mockResolvedValueOnce({ rows: sampleRows });
    await renderPage();
    const retry = container.querySelector('[data-testid="mobile-schedule-retry"]');
    await act(async () => { retry.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(api.productionDashboard).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]')).toBeTruthy();
  });

  test("search uses desktop PO/customer/outlet semantics without extra fetches", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const input = container.querySelector('[data-testid="mobile-schedule-search"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, "PIK");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-1002"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-count"]').textContent).toBe("1 / 2 entri");
    expect(api.productionDashboard).toHaveBeenCalledTimes(1);
    expect(api.getOrder).not.toHaveBeenCalled();
  });

  test("Selesai toggle refetches with desktop include_completed flag", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const box = container.querySelector('[data-testid="mobile-schedule-include-completed"]');
    await act(async () => { box.click(); });
    await flush();
    expect(api.productionDashboard).toHaveBeenLastCalledWith({
      include_cancelled: false,
      include_completed: false,
    });
  });

  test("Cancelled toggle refetches with desktop include_cancelled flag", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const box = container.querySelector('[data-testid="mobile-schedule-include-cancelled"]');
    await act(async () => { box.click(); });
    await flush();
    expect(api.productionDashboard).toHaveBeenLastCalledWith({
      include_cancelled: true,
      include_completed: true,
    });
  });

  test("Reset Filter restores desktop defaults", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const input = container.querySelector('[data-testid="mobile-schedule-search"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, "PIK");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const reset = container.querySelector('[data-testid="mobile-schedule-reset"]');
    await act(async () => { reset.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(container.querySelector('[data-testid="mobile-schedule-search"]').value).toBe("");
    expect(container.querySelector('[data-testid="mobile-schedule-include-completed"]').checked).toBe(true);
    expect(container.querySelector('[data-testid="mobile-schedule-include-cancelled"]').checked).toBe(false);
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]')).toBeTruthy();
  });

  test("row click opens mobile PO detail without workspace query", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const row = container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]');
    expect(row.getAttribute("data-to")).toBe("/m/orders/po-1");
    await act(async () => { row.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(mockNavigate).toHaveBeenCalledWith("/m/orders/po-1");
  });

  test("unauthorized direct URL is blocked and does not render the spreadsheet", async () => {
    mockHasPerm.mockImplementation((p) => p === "operator.update");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    expect(container.querySelector('[data-testid="mobile-schedule-forbidden"]')).toBeTruthy();
    expect(container.querySelector("table.mop-schedule-table")).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom"]')).toBeFalsy();
    expect(api.productionDashboard).not.toHaveBeenCalled();
  });

  test("zoom default is 100% and plus/minus/fit only change the grid", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-value"]').textContent).toBe(`${MOBILE_SCHEDULE_ZOOM_DEFAULT}%`);
    expect(zoomVar()).toBe("1");
    const poBefore = container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]').textContent;
    await act(async () => {
      container.querySelector('[data-testid="mobile-schedule-zoom-in"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-value"]').textContent).toBe("110%");
    await act(async () => {
      container.querySelector('[data-testid="mobile-schedule-zoom-fit"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-value"]').textContent).toBe(`${MOBILE_SCHEDULE_ZOOM_FIT}%`);
    expect(zoomVar()).toBe("0.7");
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]').textContent).toBe(poBefore);
    expect(container.querySelector('[data-testid="mobile-topbar"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom"]').style.getPropertyValue("--mop-sched-zoom")).toBe("");
  });

  test("zoom clamps at minimum and maximum", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const minus = container.querySelector('[data-testid="mobile-schedule-zoom-out"]');
    const plus = container.querySelector('[data-testid="mobile-schedule-zoom-in"]');
    for (let i = 0; i < 8; i += 1) {
      await act(async () => { minus.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    }
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-value"]').textContent).toBe(`${MOBILE_SCHEDULE_ZOOM_MIN}%`);
    await act(async () => { minus.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-value"]').textContent).toBe(`${MOBILE_SCHEDULE_ZOOM_MIN}%`);
    for (let i = 0; i < 12; i += 1) {
      await act(async () => { plus.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    }
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-value"]').textContent).toBe(`${MOBILE_SCHEDULE_ZOOM_MAX}%`);
    expect(zoomVar()).toBe("1.5");
  });

  test("deadline badges stay inside the zoomed schedule table", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const table = container.querySelector('[data-testid="mobile-schedule-table"]');
    const overdue = [...table.querySelectorAll(".urgent-badge")].find((el) => el.textContent === "OVERDUE");
    expect(overdue).toBeTruthy();
    expect(table.contains(overdue)).toBe(true);
    const minus = container.querySelector('[data-testid="mobile-schedule-zoom-out"]');
    for (let i = 0; i < 8; i += 1) {
      await act(async () => { minus.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    }
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-value"]').textContent).toBe("50%");
    expect(zoomVar()).toBe("0.5");
    expect(table.getAttribute("style")).toContain("--mop-sched-zoom");
    expect(table.querySelector(".urgent-badge").textContent).toBe("OVERDUE");
    expect(container.querySelector('[data-testid="mobile-schedule-zoom"]').querySelector(".urgent-badge")).toBeFalsy();
  });
});
