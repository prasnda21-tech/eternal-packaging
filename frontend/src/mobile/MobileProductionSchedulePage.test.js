import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { FILTER_DEFAULTS } from "../lib/productionScheduleFilters";
import { PRODUCTION_SCHEDULE_TABLE_COLS } from "../lib/productionScheduleTable";
import {
  PRODUCTION_SHEET_ZOOM_CSS_VAR,
  PRODUCTION_SHEET_ZOOM_DEFAULT,
  PRODUCTION_SHEET_ZOOM_MAX,
  PRODUCTION_SHEET_ZOOM_MIN,
  PRODUCTION_SHEET_ZOOM_STORAGE_KEY,
  productionSheetZoomFromPinch,
  productionSheetZoomFromWheel,
} from "../lib/productionSheetZoom";
import MobileProductionSchedulePage, { scheduleLoadErrorMessage } from "./MobileProductionSchedulePage";

const mockHasPerm = jest.fn(() => false);
const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
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
      { split_id: "s1", line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz", operator_name: "Budi", quantity: 10000, line_printable: true, work_date: "2026-08-25" },
      { split_id: "s2", line_idx: 2, line_id: "lid-b", line_name: "Cup 16oz", operator_name: "Sari", quantity: 500, line_printable: true, work_date: "2026-08-26" },
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
  let rafNow = 0;

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
    window.localStorage.clear();
    rafNow = 0;
    jest.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => (
      window.setTimeout(() => {
        rafNow += 1000 / 120;
        cb(rafNow);
      }, 0)
    ));
    jest.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => window.clearTimeout(id));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    window.requestAnimationFrame.mockRestore();
    window.cancelAnimationFrame.mockRestore();
  });

  const renderPage = async () => {
    await act(async () => { root.render(<MobileProductionSchedulePage />); });
    await flush();
  };

  const zoomVar = () => container.querySelector('[data-testid="mobile-schedule-scroll"]')
    ?.style.getPropertyValue(PRODUCTION_SHEET_ZOOM_CSS_VAR);

  const flushRaf = async () => {
    await act(async () => {
      await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    });
  };

  const flushZoom = async () => {
    for (let i = 0; i < 40; i += 1) await flushRaf();
  };

  const wheelCtrl = async (viewport, deltaY, times = 1) => {
    for (let i = 0; i < times; i += 1) {
      await act(async () => {
        viewport.dispatchEvent(new WheelEvent("wheel", {
          ctrlKey: true, deltaY, bubbles: true, cancelable: true,
        }));
      });
      await flushRaf();
    }
  };

  const dispatchTouches = (viewport, type, points) => {
    const event = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(event, "touches", {
      value: points.map((point, identifier) => ({
        identifier,
        clientX: point.x,
        clientY: point.y,
      })),
    });
    viewport.dispatchEvent(event);
    return event;
  };

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
      view: "summary",
    });
    expect(container.querySelector('[data-testid="mobile-schedule-prev"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-today"]')).toBeFalsy();
    expect(container.textContent).not.toContain("pada tanggal ini");
    const headers = [...container.querySelectorAll("thead th")].map((th) => th.textContent);
    expect(headers).toEqual(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label));
    expect(container.querySelectorAll('[data-testid="mobile-schedule-row-PO-1001"]')).toHaveLength(2);
    const opNames = [...container.querySelectorAll('[data-testid="mobile-schedule-row-PO-1001"] .prod-op-name')].map((el) => el.textContent);
    expect(opNames).toEqual(["Budi", "Sari"]);
    expect(container.querySelector('[data-testid="mobile-schedule-qty-line-PO-1001-0"]')).toBeFalsy();
    expect(container.querySelectorAll('[data-testid="mobile-schedule-item-PO-1001"]').length).toBeGreaterThan(0);
    expect(container.textContent).toContain("Budi");
    expect(container.textContent).toContain("Sari");
    expect(container.textContent).toContain("Cup 16oz");
    expect(container.textContent).toContain("2026-08-25");
    expect(container.textContent).toContain("Belum Dijadwalkan");
    expect(container.textContent).toContain("0 / 25.000");
    expect(container.textContent).toContain("25.000");
    expect(container.querySelector('[data-testid="mobile-schedule-search"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-search-btn"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-ringkasan"]')).toBeTruthy();
    expect(container.textContent).toContain("OVERDUE");
    expect(container.querySelector(".production-sheet-table .urgent-badge")).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-count"]').textContent).toBe("2 / 2 entri");
    expect(container.querySelector('[data-testid="mobile-schedule-include-completed"]').checked).toBe(true);
    expect(container.querySelector('[data-testid="mobile-schedule-include-cancelled"]').checked).toBe(false);
    expect(api.getOrder).not.toHaveBeenCalled();
    expect(api.listOrders).not.toHaveBeenCalled();
    expect(api.productionExport).not.toHaveBeenCalled();
  });

  test("empty canonical dataset shows Belum ada jadwal produksi", async () => {
    api.productionDashboard.mockResolvedValue({ rows: [] });
    await renderPage();
    expect(container.textContent).toContain("Belum ada jadwal produksi.");
    expect(container.textContent).not.toContain("pada tanggal ini");
    expect(container.querySelector("table.production-sheet-table")).toBeFalsy();
  });

  test("API error shows Gagal memuat and Coba Lagi", async () => {
    api.productionDashboard.mockRejectedValue({
      response: { status: 500, data: { detail: { message: "upstream" } } },
    });
    await renderPage();
    expect(container.textContent).toContain("Gagal memuat jadwal produksi.");
    expect(container.textContent).toContain("Coba Lagi");
    expect(container.querySelector("table.production-sheet-table")).toBeFalsy();
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
      view: "summary",
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
      view: "summary",
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
    const rows = [...container.querySelectorAll('[data-testid="mobile-schedule-row-PO-1001"]')];
    expect(rows).toHaveLength(2);
    expect(rows.every((row) => row.getAttribute("data-to") === "/m/orders/po-1")).toBe(true);
    await act(async () => { rows[1].dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(mockNavigate).toHaveBeenCalledWith("/m/orders/po-1");
  });

  test("unauthorized direct URL is blocked and does not render the spreadsheet", async () => {
    mockHasPerm.mockImplementation((p) => p === "operator.update");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    expect(container.querySelector('[data-testid="mobile-schedule-forbidden"]')).toBeTruthy();
    expect(container.querySelector("table.production-sheet-table")).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom"]')).toBeFalsy();
    expect(api.productionDashboard).not.toHaveBeenCalled();
  });

  test("default spreadsheet zoom is applied without +/- Fit controls", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    expect(zoomVar()).toBe(String(PRODUCTION_SHEET_ZOOM_DEFAULT));
    expect(container.querySelector('[data-testid="mobile-schedule-zoom"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-in"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-out"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-fit"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-value"]')).toBeFalsy();
    expect(container.textContent).not.toMatch(/\bFit\b/);
    expect(container.querySelector('[data-testid="mobile-topbar"]')).toBeTruthy();
  });

  test("Ctrl + wheel changes sheet zoom and plain wheel does not", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const viewport = container.querySelector('[data-testid="mobile-schedule-scroll"]');
    const before = zoomVar();
    await act(async () => {
      viewport.dispatchEvent(new WheelEvent("wheel", { ctrlKey: false, deltaY: 120, bubbles: true, cancelable: true }));
    });
    await flushRaf();
    expect(zoomVar()).toBe(before);
    await act(async () => {
      viewport.dispatchEvent(new WheelEvent("wheel", { ctrlKey: true, deltaY: -80, bubbles: true, cancelable: true }));
    });
    await flushRaf();
    await flushRaf();
    expect(window.localStorage.getItem(PRODUCTION_SHEET_ZOOM_STORAGE_KEY)).toBeNull();
    const afterOne = Number(zoomVar());
    const target = productionSheetZoomFromWheel(PRODUCTION_SHEET_ZOOM_DEFAULT, -80);
    expect(afterOne).toBeGreaterThan(Number(before));
    expect(afterOne).toBeLessThan(target);
    await flushZoom();
    expect(Number(zoomVar())).toBe(target);
  });

  test("sheet zoom clamps at min and max", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const viewport = container.querySelector('[data-testid="mobile-schedule-scroll"]');
    await wheelCtrl(viewport, 800, 20);
    await flushZoom();
    expect(zoomVar()).toBe(String(PRODUCTION_SHEET_ZOOM_MIN));
    await wheelCtrl(viewport, 800, 4);
    expect(zoomVar()).toBe(String(PRODUCTION_SHEET_ZOOM_MIN));
    await wheelCtrl(viewport, -800, 30);
    await flushZoom();
    expect(zoomVar()).toBe(String(PRODUCTION_SHEET_ZOOM_MAX));
    await wheelCtrl(viewport, -800, 4);
    expect(zoomVar()).toBe(String(PRODUCTION_SHEET_ZOOM_MAX));
  });

  test("Ctrl+wheel preserves scroll position instead of resetting", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const viewport = container.querySelector('[data-testid="mobile-schedule-scroll"]');
    viewport.scrollLeft = 120;
    viewport.scrollTop = 48;
    await wheelCtrl(viewport, -80, 1);
    expect(viewport.scrollLeft).not.toBe(0);
    expect(viewport.scrollTop).not.toBe(0);
  });

  test("pinch uses initial distance and suppresses row click after movement", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const viewport = container.querySelector('[data-testid="mobile-schedule-scroll"]');
    const row = container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]');
    await act(async () => {
      dispatchTouches(viewport, "touchstart", [{ x: 10, y: 10 }, { x: 110, y: 10 }]);
      dispatchTouches(viewport, "touchmove", [{ x: 10, y: 10 }, { x: 160, y: 10 }]);
    });
    await flushZoom();
    expect(Number(zoomVar())).toBe(productionSheetZoomFromPinch(PRODUCTION_SHEET_ZOOM_DEFAULT, 100, 150));
    await act(async () => {
      dispatchTouches(viewport, "touchend", []);
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  test("localStorage preference restores spreadsheet zoom", async () => {
    window.localStorage.setItem(PRODUCTION_SHEET_ZOOM_STORAGE_KEY, "1.2");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    expect(zoomVar()).toBe("1.2");
  });

  test("deadline badges stay inside the zoomed schedule table", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const table = container.querySelector('[data-testid="mobile-schedule-table"]');
    const viewport = container.querySelector('[data-testid="mobile-schedule-scroll"]');
    const overdue = [...table.querySelectorAll(".urgent-badge")].find((el) => el.textContent === "OVERDUE");
    expect(overdue).toBeTruthy();
    expect(table.contains(overdue)).toBe(true);
    await act(async () => {
      viewport.dispatchEvent(new WheelEvent("wheel", { ctrlKey: true, deltaY: 4000, bubbles: true, cancelable: true }));
    });
    await flushRaf();
    await flushRaf();
    expect(Number(zoomVar())).toBeLessThan(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(viewport.getAttribute("style")).toContain(PRODUCTION_SHEET_ZOOM_CSS_VAR);
    expect([...table.querySelectorAll(".urgent-badge")].some((el) => el.textContent === "OVERDUE")).toBe(true);
  });

  test("row navigation remains available outside a zoom gesture", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const row = container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]');
    await act(async () => { row.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(mockNavigate).toHaveBeenCalledWith("/m/orders/po-1");
  });
});
