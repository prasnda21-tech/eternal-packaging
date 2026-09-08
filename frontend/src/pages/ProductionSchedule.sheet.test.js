import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { PRODUCTION_SCHEDULE_TABLE_COLS } from "../lib/productionScheduleTable";
import {
  PRODUCTION_SHEET_ZOOM_CSS_VAR,
  PRODUCTION_SHEET_ZOOM_DEFAULT,
  PRODUCTION_SHEET_ZOOM_MAX,
  PRODUCTION_SHEET_ZOOM_MIN,
  PRODUCTION_SHEET_ZOOM_STORAGE_KEY,
  productionSheetZoomFromWheel,
} from "../lib/productionSheetZoom";
import ProductionSchedule from "./ProductionSchedule";

const mockHasPerm = jest.fn(() => true);

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: (perm) => mockHasPerm(perm) }),
}));

jest.mock("../lib/useAutoRefresh", () => ({
  __esModule: true,
  default: () => ({ lastUpdated: null, isPolling: false, refreshNow: jest.fn() }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    productionDashboard: jest.fn(),
    productionExport: jest.fn(),
  },
}));

jest.mock("../components/ui/popover", () => ({
  Popover: ({ children }) => <div>{children}</div>,
  PopoverTrigger: ({ children }) => children,
  PopoverContent: ({ children }) => <div>{children}</div>,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

let rafNow = 0;
const flushRaf = async () => {
  await act(async () => {
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
  });
};

const flushZoom = async () => {
  for (let i = 0; i < 40; i += 1) await flushRaf();
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
    current_stage: 8,
    status_final: "Operator",
    total_quantity: 25000,
    print_qty_total: 20000,
    operator_assigned_qty: 10000,
    design_status: "Approved",
    payment_status: "Approved",
    packing_status: "Belum",
    operators: [
      { line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz Extra Long Name", operator_name: "Budi", quantity: 10000, line_printable: true },
    ],
  },
];

describe("ProductionSchedule spreadsheet", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockHasPerm.mockImplementation(() => true);
    api.productionDashboard.mockReset();
    api.productionExport.mockReset();
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
    await act(async () => { root.render(<ProductionSchedule />); });
    await flush();
  };

  test("desktop headers match the shared canonical order and omit +/- Fit", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows, total: 1 });
    await renderPage();
    const headers = [...container.querySelectorAll('[data-testid="production-sheet-table"] thead .prod-th-label')].map((el) => el.textContent.trim());
    expect(headers).toEqual(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label));
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-in"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-out"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-fit"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-po-link-PO-1001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-customer-PO-1001"]').textContent).toContain("PT Example");
    expect(container.textContent).toContain("Cup 16oz Extra Long Name");
    expect(container.querySelector('[data-testid="prod-search"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-search-btn"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-ringkasan"]')).toBeTruthy();
  });

  test("Qty Cetak is operator completed over PO total and Item is standalone", async () => {
    api.productionDashboard.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        operators: [{
          split_id: "s-budi",
          operator_email: "budi@x.com",
          operator_name: "Budi",
          quantity: 10000,
          qty_completed: 3000,
          line_idx: 0,
          line_id: "lid-a",
          line_name: "Cup 16oz Extra Long Name",
          line_printable: true,
          work_date: "2026-08-25",
        }],
      }],
      total: 1,
    });
    await renderPage();
    expect(container.querySelector('[data-testid="prod-qty-cetak-PO-1001"]').textContent).toBe("3.000 / 25.000");
    expect(container.querySelector('[data-testid="prod-qty-PO-1001"]').textContent).toBe("25.000");
    expect(container.querySelector('[data-testid="prod-item-PO-1001"]').textContent).toContain("Cup 16oz Extra Long Name");
    expect(container.querySelector('[data-testid="prod-qty-PO-1001"]').textContent).not.toContain("Cup");
    await act(async () => {
      container.querySelector('[data-testid="prod-ringkasan"]').click();
    });
    expect(container.querySelector('[data-testid="prod-ringkasan-panel"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-ringkasan-po"]').textContent).toContain("1");
    expect(container.querySelector('[data-testid="prod-ringkasan-qty"]').textContent).toContain("25.000");
    expect(container.querySelector('[data-testid="prod-ringkasan-printed"]').textContent).toContain("3.000");
    expect(container.querySelector('[data-testid="prod-ringkasan-remain"]').textContent).toContain("22.000");
  });

  test("Ctrl + wheel zooms the desktop sheet via CSS variable", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows, total: 1 });
    await renderPage();
    const viewport = container.querySelector('[data-testid="prod-table"]');
    expect(viewport.style.getPropertyValue(PRODUCTION_SHEET_ZOOM_CSS_VAR)).toBe(String(PRODUCTION_SHEET_ZOOM_DEFAULT));
    const plain = new WheelEvent("wheel", { ctrlKey: false, deltaY: 120, bubbles: true, cancelable: true });
    const ctrl = new WheelEvent("wheel", { ctrlKey: true, deltaY: -80, bubbles: true, cancelable: true });
    await act(async () => { viewport.dispatchEvent(plain); });
    await flushRaf();
    expect(plain.defaultPrevented).toBe(false);
    expect(viewport.style.getPropertyValue(PRODUCTION_SHEET_ZOOM_CSS_VAR)).toBe(String(PRODUCTION_SHEET_ZOOM_DEFAULT));
    await act(async () => { viewport.dispatchEvent(ctrl); });
    await flushRaf();
    await flushRaf();
    expect(ctrl.defaultPrevented).toBe(true);
    expect(window.localStorage.getItem(PRODUCTION_SHEET_ZOOM_STORAGE_KEY)).toBeNull();
    const afterOne = Number(viewport.style.getPropertyValue(PRODUCTION_SHEET_ZOOM_CSS_VAR));
    const target = productionSheetZoomFromWheel(PRODUCTION_SHEET_ZOOM_DEFAULT, -80);
    expect(afterOne).toBeGreaterThan(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(afterOne).toBeLessThan(target);
    await flushZoom();
    expect(Number(viewport.style.getPropertyValue(PRODUCTION_SHEET_ZOOM_CSS_VAR))).toBe(target);
  });

  test("desktop sheet zoom clamps at min and max without resetting scroll", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows, total: 1 });
    await renderPage();
    const viewport = container.querySelector('[data-testid="prod-table"]');
    viewport.scrollLeft = 90;
    viewport.scrollTop = 24;
    for (let i = 0; i < 20; i += 1) {
      await act(async () => {
        viewport.dispatchEvent(new WheelEvent("wheel", { ctrlKey: true, deltaY: 800, bubbles: true, cancelable: true }));
      });
      await flushRaf();
    }
    await flushZoom();
    expect(viewport.style.getPropertyValue(PRODUCTION_SHEET_ZOOM_CSS_VAR)).toBe(String(PRODUCTION_SHEET_ZOOM_MIN));
    expect(viewport.scrollLeft).not.toBe(0);
    for (let i = 0; i < 30; i += 1) {
      await act(async () => {
        viewport.dispatchEvent(new WheelEvent("wheel", { ctrlKey: true, deltaY: -800, bubbles: true, cancelable: true }));
      });
      await flushRaf();
    }
    await flushZoom();
    expect(viewport.style.getPropertyValue(PRODUCTION_SHEET_ZOOM_CSS_VAR)).toBe(String(PRODUCTION_SHEET_ZOOM_MAX));
  });

  test("one PO with three operators becomes three spreadsheet rows", async () => {
    api.productionDashboard.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        operators: [
          { split_id: "s-budi", operator_email: "budi@x.com", operator_name: "Budi", quantity: 1000, line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz Extra Long Name", line_printable: true, work_date: "2026-08-25" },
          { split_id: "s-andi", operator_email: "andi@x.com", operator_name: "Andi", quantity: 800, line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz Extra Long Name", line_printable: true, work_date: "2026-08-26" },
          { split_id: "s-cici", operator_email: "cici@x.com", operator_name: "Cici", quantity: 500, line_idx: 2, line_id: "lid-b", line_name: "Cup 16oz Extra Long Name", line_printable: true, work_date: "2026-08-27" },
        ],
      }],
      total: 1,
    });
    await renderPage();
    const rows = [...container.querySelectorAll('[data-testid="prod-row-PO-1001"]')];
    expect(rows).toHaveLength(3);
    expect(container.querySelector('[data-testid="prod-total-visible"]').textContent).toBe("1 / 1 entri");
    expect(rows.map((row) => row.querySelector(".prod-op-name")?.textContent)).toEqual(["Budi", "Andi", "Cici"]);
    expect(container.querySelectorAll('[data-testid="prod-po-link-PO-1001"]')).toHaveLength(3);
    expect(container.querySelector('[href*="po-1"]')).toBeTruthy();
  });
});
