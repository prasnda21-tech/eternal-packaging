import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { FILTER_DEFAULTS } from "../lib/productionScheduleFilters";
import { MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS } from "../lib/mobileProductionScheduleCols";
import {
  PRODUCTION_SHEET_ZOOM_CSS_VAR,
  PRODUCTION_SHEET_ZOOM_DEFAULT,
  PRODUCTION_SHEET_ZOOM_MAX,
  PRODUCTION_SHEET_ZOOM_MIN,
  PRODUCTION_SHEET_ZOOM_STORAGE_KEY,
  productionSheetZoomFromPinch,
  productionSheetZoomFromWheel,
  productionSheetZoomStorageKey,
} from "../lib/productionSheetZoom";
import MobileProductionSchedulePage, { scheduleLoadErrorMessage } from "./MobileProductionSchedulePage";

const mockHasPerm = jest.fn(() => false);
const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useSearchParams: () => [new URLSearchParams(""), jest.fn()],
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ user: { id: "u-mps" }, hasPerm: (perm) => mockHasPerm(perm) }),
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
    screen_qty: 3000,
    helper_prepared: 0,
    helper_required: 25000,
    design_status: "Approved",
    payment_status: "Approved",
    packing_status: "Belum",
    delivery_note_number: "SJ-1001",
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
    window.sessionStorage.clear();
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

  test("renders compact ops columns with customer above PO and visible item lines", async () => {
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
    expect(headers).toEqual([
      "Prioritas",
      "Tgl Kerja",
      "Customer / No PO",
      "Surat Jalan",
      "Item Line",
      "Qty Total",
      "Status per Role",
      "Cup",
      "Screen",
      "Cetak",
      "Pack",
      "Load",
      "Kirim",
    ]);
    expect(headers).not.toContain("Zona");
    expect(headers).not.toContain("Driver");
    expect(headers).not.toContain("Tahap Aktif");
    expect(headers).not.toContain("Payment");
    expect(headers).not.toContain("Packing");
    expect(headers).toContain("Surat Jalan");
    expect(headers).not.toContain("No PO");
    expect(headers.filter((h) => h === "Screen")).toHaveLength(1);
    expect(container.querySelectorAll('[data-testid="mobile-schedule-row-PO-1001"]')).toHaveLength(2);
    const identity = container.querySelector('[data-testid="mobile-schedule-identity-PO-1001"]');
    expect(identity.querySelector(".mop-ops-customer").textContent).toBe("PT Example");
    expect(container.querySelector('[data-testid="mobile-schedule-sj-PO-1001"]').textContent).toBe("SJ-1001");
    expect(container.querySelector('[data-testid="mobile-schedule-qty-line-PO-1001-0"]')).toBeFalsy();
    expect(container.querySelectorAll('[data-testid="mobile-schedule-item-PO-1001"]').length).toBeGreaterThan(0);
    expect(container.textContent).toContain("Cup 16oz");
    expect(container.textContent).toContain("25/08/26");
    expect(container.textContent).not.toContain("2026-08-25");
    expect(container.textContent).toContain("Belum Dijadwalkan");
    expect(container.textContent).toContain("25.000");
    expect(container.querySelector('[data-testid="prod-screen-qty-PO-1001"]').getAttribute("aria-label")).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-search"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-search-btn"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-ringkasan"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-ops-role-summary"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-ops-role-aside"]')).toBeFalsy();
    expect(container.textContent).not.toContain("Satu Sistem, Setiap Role");
    expect(container.querySelector('[data-testid="mop-active-role"]')).toBeFalsy();
    expect(container.querySelector(".production-sheet-table .mop-ops-badge")).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-scroll"]').className).toMatch(/mop-schedule-scroll/);
    expect(container.querySelector('[data-testid="mobile-schedule-count"]').textContent).toBe("2 / 2 entri");
    expect(container.querySelector('[data-testid="mobile-schedule-include-completed"]').checked).toBe(true);
    expect(container.querySelector('[data-testid="mobile-schedule-include-cancelled"]').checked).toBe(false);
    expect(container.querySelector('[data-testid="mobile-schedule-quick-belum_dijadwalkan"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-quick-minggu_ini"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-quick-semua_aktif"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-filter-panel"]')).toBeFalsy();
    const header = container.querySelector('[data-testid="mobile-topbar"]');
    expect(header.textContent).not.toContain("ETERNAL PACKAGING");
    expect(header.textContent).not.toContain("OPERATION ON HAND");
    expect(header.textContent).not.toContain("Operation on hand");
    expect(header.textContent).not.toContain("Dari Produksi ke Pengiriman");
    expect(header.querySelector(".ops-qh-brand")).toBeFalsy();
    expect(header.querySelector(".ops-qh-sub")).toBeFalsy();
    expect(container.querySelector(".mop-ops-role-icon")).toBeFalsy();
    expect(api.getOrder).not.toHaveBeenCalled();
    expect(api.listOrders).not.toHaveBeenCalled();
    expect(api.productionExport).not.toHaveBeenCalled();
  });

  test("Tanggal Pengerjaan displays DD/MM/YY without mutating raw work_date", async () => {
    api.productionDashboard.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          po_number: "PO-A",
          work_date: "2026-09-09",
          operator_work_dates: [{ split_id: "s1", operator_name: "Budi", work_date: "2026-09-09" }],
          operators: [{ ...sampleRows[0].operators[0], work_date: "2026-09-09" }],
        },
        {
          ...sampleRows[0],
          id: "po-b",
          po_number: "PO-B",
          work_date: "2026-01-05",
          operator_work_dates: [{ split_id: "s1", operator_name: "Budi", work_date: "2026-01-05" }],
          operators: [{ ...sampleRows[0].operators[0], work_date: "2026-01-05" }],
        },
        {
          ...sampleRows[0],
          id: "po-c",
          po_number: "PO-C",
          work_date: "2025-12-31",
          operator_work_dates: [{ split_id: "s1", operator_name: "Budi", work_date: "2025-12-31" }],
          operators: [{ ...sampleRows[0].operators[0], work_date: "2025-12-31" }],
        },
        {
          ...sampleRows[0],
          id: "po-d",
          po_number: "PO-D",
          work_date: "2026-09-09T00:00:00Z",
          operator_work_dates: [{ split_id: "s1", operator_name: "Budi", work_date: "2026-09-09T00:00:00Z" }],
          operators: [{ ...sampleRows[0].operators[0], work_date: "2026-09-09T00:00:00Z" }],
        },
        {
          ...sampleRows[1],
          po_number: "PO-E",
          work_date: null,
        },
      ],
      total: 5,
    });
    await renderPage();
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-A"]').textContent).toContain("09/09/26");
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-B"]').textContent).toContain("05/01/26");
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-C"]').textContent).toContain("31/12/25");
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-D"]').textContent).toContain("09/09/26");
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-E"]').textContent).toContain("Belum Dijadwalkan");
    expect(container.textContent).not.toContain("2026-09-09");
    expect(container.textContent).not.toContain("2026-01-05");
    expect(container.textContent).not.toContain("2025-12-31");
    const payload = await api.productionDashboard.mock.results[0].value;
    expect(payload.rows.map((r) => r.work_date)).toEqual([
      "2026-09-09",
      "2026-01-05",
      "2025-12-31",
      "2026-09-09T00:00:00Z",
      null,
    ]);
  });

  test("empty canonical dataset shows Belum ada jadwal produksi", async () => {
    api.productionDashboard.mockResolvedValue({ rows: [] });
    await renderPage();
    expect(container.textContent).toContain("Belum ada jadwal produksi.");
    expect(container.textContent).not.toContain("pada tanggal ini");
    expect(container.querySelector("table.production-sheet-table")).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-empty"]')).toBeTruthy();
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

  test("PWA toolbar keeps Filter/Reset and removes Export buttons", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "dashboard.production.export");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    expect(container.querySelector('[data-testid="mobile-schedule-filter-btn"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-reset"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-filter-actions"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-export-filtered"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-export-all"]')).toBeFalsy();
    expect(container.textContent).not.toContain("Export Terfilter");
    expect(container.textContent).not.toContain("Export Semua");
    expect(container.textContent).toContain("Filter");
    expect(container.textContent).toContain("Reset");
    expect(api.productionExport).not.toHaveBeenCalled();
  });

  test("row click opens role-relevant workspace when permitted, else stays on schedule", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const rows = [...container.querySelectorAll('[data-testid="mobile-schedule-row-PO-1001"]')];
    expect(rows).toHaveLength(2);
    expect(rows.every((row) => row.getAttribute("data-to") === "/m/orders/po-1")).toBe(true);
    await act(async () => { rows[1].dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(api.getOrder).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mobile-task-modal"]')).toBeFalsy();

    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "screen.update");
    api.getOrder.mockResolvedValue({
      order: { id: "po-1", po_number: "PO-1001", customer: "PT Example", line_items: [] },
    });
    await renderPage();
    const screenerRows = [...container.querySelectorAll('[data-testid="mobile-schedule-row-PO-1001"]')];
    expect(screenerRows[0].getAttribute("data-to")).toBe("/m/orders/po-1?ws=screener");
    await act(async () => { screenerRows[0].dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mobile-ws-screener"]')).toBeTruthy();
    expect(container.textContent).not.toContain("Catatan");
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
    expect(afterOne).toBe(target);
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
    window.localStorage.setItem(productionSheetZoomStorageKey("u-mps"), "1.2");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    expect(zoomVar()).toBe("1.2");
  });

  test("ops priority badges stay inside the zoomed schedule table", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const table = container.querySelector('[data-testid="mobile-schedule-table"]');
    const viewport = container.querySelector('[data-testid="mobile-schedule-scroll"]');
    const badge = table.querySelector(".mop-ops-badge");
    expect(badge).toBeTruthy();
    expect(table.contains(badge)).toBe(true);
    await act(async () => {
      viewport.dispatchEvent(new WheelEvent("wheel", { ctrlKey: true, deltaY: 4000, bubbles: true, cancelable: true }));
    });
    await flushRaf();
    await flushRaf();
    expect(Number(zoomVar())).toBeLessThan(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(viewport.getAttribute("style")).toContain(PRODUCTION_SHEET_ZOOM_CSS_VAR);
    expect(container.querySelector(".production-sheet-table .mop-ops-badge")).toBeTruthy();
  });

  test("row click opens active-role task modal without PO summary navigation", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "packing.update");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    api.getOrder.mockResolvedValue({
      order: { id: "po-1", po_number: "PO-1001", customer: "PT Example", line_items: [] },
    });
    await renderPage();
    expect(container.querySelector('[data-testid="mop-active-role"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mop-active-role-current"]')?.textContent
      || container.querySelector('[data-testid="mop-active-role-input"]')?.value).toMatch(/Packing|packing/i);
    const row = container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]');
    await act(async () => { row.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(api.getOrder).toHaveBeenCalledWith("po-1");
    expect(container.querySelector('[data-testid="mobile-task-modal"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-ws-packing"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeFalsy();
    expect(container.textContent).not.toContain("Catatan");
  });

  test("active Loading on /m/schedule refuses without guessing an SJ", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "loading.update" || p === "loading.confirm_match");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    expect(container.querySelector('[data-testid="mop-active-role-current"]')?.textContent
      || container.querySelector('[data-testid="mop-active-role-input"]')?.value).toMatch(/Loading|loading/i);
    const row = container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]');
    await act(async () => { row.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(api.getOrder).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-schedule-task-error"]').textContent)
      .toMatch(/Surat Jalan tidak dapat ditentukan/);
    expect(container.querySelector('[data-testid="mop-schedule-task-error"]').textContent)
      .toMatch(/Jadwal Pengiriman/);
  });

  test("active Pengiriman on /m/schedule refuses without guessing an SJ", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    expect(container.querySelector('[data-testid="mop-active-role-current"]')?.textContent
      || container.querySelector('[data-testid="mop-active-role-input"]')?.value).toMatch(/Pengiriman|delivery/i);
    const row = container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]');
    await act(async () => { row.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(api.getOrder).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mobile-ws-delivery"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-schedule-task-error"]').textContent)
      .toMatch(/Surat Jalan tidak dapat ditentukan/);
  });

  test("row navigation remains available outside a zoom gesture", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "packing.update");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    api.getOrder.mockResolvedValue({
      order: { id: "po-1", po_number: "PO-1001", customer: "PT Example", line_items: [] },
    });
    await renderPage();
    const row = container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]');
    await act(async () => { row.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mobile-task-modal"]')).toBeTruthy();
  });

  const clickScheduleRow = async () => {
    const row = container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]');
    await act(async () => { row.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    return row;
  };

  const clickScheduleCell = async (index) => {
    const row = container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]');
    const cells = [...row.querySelectorAll("td")];
    const cell = cells.at(index);
    await act(async () => { cell.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    return cell;
  };

  const closeTaskModal = async () => {
    const close = container.querySelector('[data-testid="mobile-task-modal-close"]');
    if (!close) return;
    await act(async () => { close.click(); });
    await flush();
  };

  const allOpsPerm = (p) => (
    p === "schedule.view"
    || p === "screen.update"
    || p === "helper.prepare"
    || p === "packing.update"
    || p === "loading.update"
    || p === "delivery.update"
  );

  test("Screen allowed + active opens Screen modal, not another role", async () => {
    const { writeActiveRoleId } = require("../lib/mobileOpsSession");
    writeActiveRoleId("u-mps", "screener");
    mockHasPerm.mockImplementation(allOpsPerm);
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    api.getOrder.mockResolvedValue({
      order: { id: "po-1", po_number: "PO-1001", customer: "PT Example", line_items: [{ name: "Cup", quantity: 1000 }] },
    });
    await renderPage();
    expect(container.querySelector('[data-testid="mop-active-role-input"]').value).toBe("screener");
    await clickScheduleRow();
    expect(container.querySelector('[data-testid="mobile-ws-screener"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-ws-packing"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeFalsy();
  });

  test("Helper allowed + active opens Helper modal", async () => {
    const { writeActiveRoleId } = require("../lib/mobileOpsSession");
    writeActiveRoleId("u-mps", "prep");
    mockHasPerm.mockImplementation(allOpsPerm);
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    api.getOrder.mockResolvedValue({
      order: {
        id: "po-1",
        po_number: "PO-1001",
        customer: "PT Example",
        line_items: [{ name: "Cup", quantity: 1000, line_id: "lid-a" }],
      },
    });
    await renderPage();
    expect(container.querySelector('[data-testid="mop-active-role-input"]').value).toBe("prep");
    await clickScheduleRow();
    expect(container.querySelector('[data-testid="mobile-ws-prep"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-ws-screener"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeFalsy();
  });

  test("active Packing does not open Screen when both are allowed", async () => {
    const { writeActiveRoleId } = require("../lib/mobileOpsSession");
    writeActiveRoleId("u-mps", "packing");
    mockHasPerm.mockImplementation(allOpsPerm);
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    api.getOrder.mockResolvedValue({
      order: { id: "po-1", po_number: "PO-1001", customer: "PT Example", line_items: [] },
    });
    await renderPage();
    expect(container.querySelector('[data-testid="mop-active-role-input"]').value).toBe("packing");
    await clickScheduleRow();
    expect(container.querySelector('[data-testid="mobile-ws-packing"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-ws-screener"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeFalsy();
  });

  test("unauthorized Screen does not open Screen workspace", async () => {
    const { writeActiveRoleId } = require("../lib/mobileOpsSession");
    writeActiveRoleId("u-mps", "screener");
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "packing.update");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    api.getOrder.mockResolvedValue({
      order: { id: "po-1", po_number: "PO-1001", customer: "PT Example", line_items: [] },
    });
    await renderPage();
    expect(container.querySelector('[data-testid="mop-active-role-input"]')?.value
      || container.querySelector('[data-testid="mop-active-role-current"]')?.textContent).toMatch(/packing|Packing/i);
    await clickScheduleRow();
    expect(container.querySelector('[data-testid="mobile-ws-screener"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-ws-packing"]')).toBeTruthy();
  });

  test("role not permitted fails closed and does not insert a PO summary", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    await clickScheduleRow();
    expect(api.getOrder).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mobile-task-modal"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeFalsy();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  test("normal click without pinch opens; pinch blocks one click; next real click opens", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "packing.update");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    api.getOrder.mockResolvedValue({
      order: { id: "po-1", po_number: "PO-1001", customer: "PT Example", line_items: [] },
    });
    await renderPage();
    await clickScheduleRow();
    expect(container.querySelector('[data-testid="mobile-ws-packing"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="mobile-task-modal-close"]').click();
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-task-modal"]')).toBeFalsy();

    const viewport = container.querySelector('[data-testid="mobile-schedule-scroll"]');
    await act(async () => {
      viewport.dispatchEvent(new WheelEvent("wheel", { ctrlKey: true, deltaY: 40, bubbles: true, cancelable: true }));
    });
    await flushRaf();
    await clickScheduleRow();
    expect(container.querySelector('[data-testid="mobile-task-modal"]')).toBeFalsy();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 320));
    });
    await clickScheduleRow();
    expect(container.querySelector('[data-testid="mobile-ws-packing"]')).toBeTruthy();
  });

  test("ordinary wheel does not suppress a following row click", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "packing.update");
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    api.getOrder.mockResolvedValue({
      order: { id: "po-1", po_number: "PO-1001", customer: "PT Example", line_items: [] },
    });
    await renderPage();
    const viewport = container.querySelector('[data-testid="mobile-schedule-scroll"]');
    await act(async () => {
      viewport.dispatchEvent(new WheelEvent("wheel", { ctrlKey: false, deltaY: 240, bubbles: true, cancelable: true }));
    });
    await flushRaf();
    await clickScheduleRow();
    expect(container.querySelector('[data-testid="mobile-ws-packing"]')).toBeTruthy();
  });

  test("entire production row is clickable: first, middle, last cells follow Role Aktif", async () => {
    const { writeActiveRoleId } = require("../lib/mobileOpsSession");
    mockHasPerm.mockImplementation(allOpsPerm);
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    api.getOrder.mockResolvedValue({
      order: { id: "po-1", po_number: "PO-1001", customer: "PT Example", line_items: [{ name: "Cup", quantity: 1000 }] },
    });
    const cases = [
      { role: "screener", workspace: "mobile-ws-screener", others: ["mobile-ws-prep", "mobile-ws-packing"] },
      { role: "prep", workspace: "mobile-ws-prep", others: ["mobile-ws-screener", "mobile-ws-packing"] },
      { role: "packing", workspace: "mobile-ws-packing", others: ["mobile-ws-screener", "mobile-ws-prep"] },
    ];
    for (const spec of cases) {
      act(() => { root.unmount(); });
      root = createRoot(container);
      writeActiveRoleId("u-mps", spec.role);
      await renderPage();
      expect(container.querySelector('[data-testid="mop-active-role-input"]').value).toBe(spec.role);
      const cells = [...container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]').querySelectorAll("td")];
      expect(cells.length).toBeGreaterThanOrEqual(3);
      const indexes = [0, Math.floor(cells.length / 2), cells.length - 1];
      for (const idx of indexes) {
        await clickScheduleCell(idx);
        expect(container.querySelector(`[data-testid="${spec.workspace}"]`)).toBeTruthy();
        spec.others.forEach((other) => {
          expect(container.querySelector(`[data-testid="${other}"]`)).toBeFalsy();
        });
        expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeFalsy();
        await closeTaskModal();
      }
    }
  });

  test("Packing role opens Packing when a Screen status cell is clicked", async () => {
    const { writeActiveRoleId } = require("../lib/mobileOpsSession");
    writeActiveRoleId("u-mps", "packing");
    mockHasPerm.mockImplementation(allOpsPerm);
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    api.getOrder.mockResolvedValue({
      order: { id: "po-1", po_number: "PO-1001", customer: "PT Example", line_items: [] },
    });
    await renderPage();
    const mark = container.querySelector('[data-testid="prod-screen-qty-PO-1001"]');
    await act(async () => { mark.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-packing"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-ws-screener"]')).toBeFalsy();
  });

  test("whitespace inside a cell opens the row; interactive child does not", async () => {
    const { writeActiveRoleId } = require("../lib/mobileOpsSession");
    writeActiveRoleId("u-mps", "packing");
    mockHasPerm.mockImplementation(allOpsPerm);
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    api.getOrder.mockResolvedValue({
      order: { id: "po-1", po_number: "PO-1001", customer: "PT Example", line_items: [] },
    });
    await renderPage();
    const row = container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]');
    const qtyCell = [...row.querySelectorAll("td")].find((td) => td.querySelector(".mop-ops-qty-total")) || row.querySelectorAll("td")[1];
    await act(async () => { qtyCell.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-packing"]')).toBeTruthy();
    await closeTaskModal();

    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "native";
    qtyCell.appendChild(btn);
    await act(async () => { btn.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-task-modal"]')).toBeFalsy();
  });

  test("PWA Screen column is qty-only and screen_size column is absent", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    api.productionDashboard.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        screen_qty: 3000,
        operators: [
          { split_id: "s1", line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz", operator_name: "Budi", quantity: 10000, line_printable: true, screen_size: "large" },
          { split_id: "s2", line_idx: 2, line_id: "lid-b", line_name: "Cup 16oz", operator_name: "Sari", quantity: 500, line_printable: true, screen_size: "small" },
        ],
      }],
    });
    await renderPage();
    expect(MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).toContain("Screen");
    expect(MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.key)).not.toContain("screen_size");
    expect(container.querySelectorAll('[data-testid^="mobile-schedule-screen-size-"]').length).toBe(0);
    const screenCell = container.querySelector('[data-testid="prod-screen-qty-PO-1001"]');
    expect(screenCell.getAttribute("aria-label")).toBe("selesai semua");
    expect(screenCell.textContent).not.toContain("/");
  });

  test("role summary strip is gone; Role Aktif lists only permitted workspaces without sample", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "helper.prepare" || p === "packing.update" || p === "operator.sample_upload"
    ));
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    expect(container.querySelector('[data-testid="mop-ops-role-summary"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-ops-role-produksi"]')).toBeFalsy();
    const select = container.querySelector('[data-testid="mop-active-role-input"]');
    expect(select).toBeTruthy();
    expect([...select.options].map((o) => o.value)).toEqual(["prep", "packing"]);
    expect([...select.options].map((o) => o.textContent)).not.toContain("Upload Sampel Cetak");
    expect(container.querySelector('[data-testid="mobile-schedule-thead"]').textContent).toContain("Status per Role");
  });

  test("persisted packing role restores and revoked role falls back", async () => {
    const { writeActiveRoleId, readActiveRoleId } = require("../lib/mobileOpsSession");
    writeActiveRoleId("u-mps", "packing");
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "helper.prepare" || p === "packing.update"
    ));
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    expect(container.querySelector('[data-testid="mop-active-role-input"]').value).toBe("packing");
    expect(readActiveRoleId("u-mps")).toBe("packing");

    writeActiveRoleId("u-mps", "packing");
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "helper.prepare");
    await act(async () => { root.render(<MobileProductionSchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mop-active-role-current"]').textContent).toBe("Persiapan Cup");
    expect(readActiveRoleId("u-mps")).toBe("prep");
  });

  test("Hari Ini filter count matches visible population after click", async () => {
    const todayKey = new Date().toISOString().slice(0, 10);
    api.productionDashboard.mockResolvedValue({
      rows: [
        { ...sampleRows[0], work_date: todayKey, operator_work_dates: [{ split_id: "s1", operator_name: "Budi", work_date: todayKey }], operators: [{ ...sampleRows[0].operators[0], work_date: todayKey }] },
        sampleRows[1],
      ],
    });
    await renderPage();
    const chip = container.querySelector('[data-testid="mobile-schedule-quick-hari_ini"]');
    const labeled = Number(container.querySelector('[data-testid="mobile-schedule-quick-count-hari_ini"]').textContent);
    await act(async () => { chip.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const visible = container.querySelectorAll("tbody tr.mop-schedule-row").length;
    expect(labeled).toBeGreaterThan(0);
    expect(visible).toBe(labeled);
  });

  test("search matches item line names without extra fetches", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const input = container.querySelector('[data-testid="mobile-schedule-search"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, "Cup 16oz");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-1001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-row-PO-1002"]')).toBeFalsy();
    expect(api.productionDashboard).toHaveBeenCalledTimes(1);
  });

  test("multiple item lines on one operator stay fully visible", async () => {
    api.productionDashboard.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        operators: [
          { split_id: "s1", line_idx: 0, line_id: "a", line_name: "Cup A", operator_name: "Budi", quantity: 1000, line_printable: true, work_date: "2026-08-25" },
          { split_id: "s1b", line_idx: 1, line_id: "b", line_name: "Lid B", operator_name: "Budi", quantity: 2000, line_printable: true, work_date: "2026-08-25" },
          { split_id: "s1c", line_idx: 2, line_id: "c", line_name: "Cup C", operator_name: "Budi", quantity: 3000, line_printable: true, work_date: "2026-08-25" },
        ],
        operator_work_dates: [{ split_id: "s1", operator_name: "Budi", work_date: "2026-08-25" }],
      }],
    });
    await renderPage();
    expect(container.textContent).toContain("Cup A");
    expect(container.textContent).toContain("Lid B");
    expect(container.textContent).toContain("Cup C");
    expect(container.textContent).not.toMatch(/\+1 item/);
    expect(container.textContent).not.toMatch(/item lainnya/);
  });

  test("today customer cells stay unpainted and the table wrapper can scroll horizontally", async () => {
    api.productionDashboard.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        po_number: "PO-TODAY",
        work_date: "2026-09-14",
        operator_work_dates: [{ split_id: "s1", operator_name: "Budi", work_date: "2026-09-14" }],
        operators: [{ ...sampleRows[0].operators[0], work_date: "2026-09-14" }],
      }],
    });
    await renderPage();
    const identity = container.querySelector('[data-testid="mobile-schedule-identity-PO-TODAY"]');
    expect(identity).toBeTruthy();
    const td = identity.closest("td");
    expect(td.className).not.toMatch(/prod-cell-today/);
    expect(String(td.getAttribute("style") || "")).not.toMatch(/fef08a/i);
    const scroll = container.querySelector('[data-testid="mobile-schedule-scroll"]');
    expect(scroll).toBeTruthy();
    expect(scroll.className).toMatch(/mop-schedule-scroll/);
  });

  test("quick filters live in one horizontal rail including Selesai and Cancelled", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows });
    await renderPage();
    const rail = container.querySelector('[data-testid="mobile-schedule-quick-filters"]');
    expect(rail.className).toMatch(/mop-ops-filter-rail/);
    expect(rail.querySelector('[data-testid="mobile-schedule-quick-semua"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-quick-hari_ini"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-quick-besok"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-quick-terlambat"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-quick-belum_dijadwalkan"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-quick-minggu_ini"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-quick-semua_aktif"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-include-completed"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-include-cancelled"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-search"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-filter-btn"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-reset"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mobile-schedule-filter-actions"]')).toBeTruthy();
    const order = [
      "mobile-schedule-quick-semua",
      "mobile-schedule-quick-hari_ini",
      "mobile-schedule-quick-besok",
      "mobile-schedule-quick-terlambat",
      "mobile-schedule-quick-belum_dijadwalkan",
      "mobile-schedule-quick-minggu_ini",
      "mobile-schedule-quick-semua_aktif",
    ].map((id) => rail.querySelector(`[data-testid="${id}"]`));
    order.forEach((el, idx) => {
      if (idx === 0) return;
      expect(el.compareDocumentPosition(order[idx - 1]) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    });
    const search = rail.querySelector('[data-testid="mobile-schedule-search"]');
    const filterBtn = rail.querySelector('[data-testid="mobile-schedule-filter-btn"]');
    const resetBtn = rail.querySelector('[data-testid="mobile-schedule-reset"]');
    const cancelled = rail.querySelector('[data-testid="mobile-schedule-include-cancelled"]');
    expect(cancelled.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(search.compareDocumentPosition(filterBtn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(filterBtn.compareDocumentPosition(resetBtn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-controls"]')).toBeFalsy();
    expect(container.querySelector(".mop-schedule-toolbar--stacked")).toBeFalsy();
    const opsCss = require("fs").readFileSync(require("path").join(__dirname, "opsQueue.css"), "utf8");
    expect(opsCss).toMatch(/\.mop-ops-filter-rail[\s\S]*flex-wrap:\s*nowrap/);
    expect(opsCss).toMatch(/\.mop-ops-filter-rail[\s\S]*overflow-x:\s*auto/);
    expect(opsCss).toMatch(/\.mop-ops-filter-rail[\s\S]*overflow-y:\s*hidden/);
  });

  test("Pack follows packing_status; Load/Kirim stay idle without per-SJ qty", async () => {
    api.productionDashboard.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        current_stage: 13,
        packing_status: "Berjalan",
        packing_qty: 4000,
        qty_delivered: 0,
        qty_total: 25000,
        percent_delivered: 0,
        role_status: { cup: "done", screen: "done", prod: "done", pack: "done", load: "done", kirim: "pending" },
      }],
      total: 1,
    });
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    await act(async () => { root.render(<MobileProductionSchedulePage />); });
    await flush();
    const pack = container.querySelector('[data-testid="prod-packing-PO-1001"]');
    const load = container.querySelector('[data-testid="prod-loading-PO-1001"]');
    const kirim = container.querySelector('[data-testid="prod-shipped-PO-1001"]');
    expect(pack.className).toMatch(/is-pending/);
    expect(load.className).toMatch(/is-idle/);
    expect(kirim.className).toMatch(/is-idle/);
  });

  test("redesigned workspaces keep existing handlers and hide generic Catatan", () => {
    const fs = require("fs");
    const path = require("path");
    const screener = fs.readFileSync(path.join(__dirname, "workspaces/MobileScreenerWorkspace.jsx"), "utf8");
    const helper = fs.readFileSync(path.join(__dirname, "workspaces/MobileHelperPrepWorkspace.jsx"), "utf8");
    const printWs = fs.readFileSync(path.join(__dirname, "workspaces/MobilePrintWorkspace.jsx"), "utf8");
    const packing = fs.readFileSync(path.join(__dirname, "workspaces/MobilePackingWorkspace.jsx"), "utf8");
    const loading = fs.readFileSync(path.join(__dirname, "workspaces/MobileLoadingWorkspace.jsx"), "utf8");
    expect(screener).not.toMatch(/<span>Catatan<\/span>/);
    expect(screener).toMatch(/api\.updateScreenerInfo/);
    expect(helper).toMatch(/api\.updateHelperPrepItems/);
    expect(helper).toMatch(/MobileQtyStepper/);
    expect(printWs).toMatch(/operatorMyAssignmentModels/);
    expect(printWs).toMatch(/pickSubmitSplit/);
    expect(printWs).toMatch(/api\.updateOperatorProgress/);
    expect(packing).toMatch(/api\.savePackingInfo/);
    expect(loading).toMatch(/confirmLoadingMatch/);
    expect(loading).toMatch(/surat_jalan_id: activeSj\.id/);
    expect(loading).toMatch(/matchLoadingSjFile/);
    expect(loading).not.toMatch(/files\[0\]/);
    expect(loading).toMatch(/reportLoadingDiscrepancy/);
    expect(loading).toMatch(/Catatan \(opsional\)/);
    expect(loading).not.toMatch(/MobileQtyStepper/);
    const page = fs.readFileSync(path.join(__dirname, "MobileProductionSchedulePage.jsx"), "utf8");
    expect(page).not.toMatch(/MobileOpsRoleSummary/);
    expect(page).toMatch(/api\.getOrder/);
  });
});
