import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS, PRODUCTION_SCHEDULE_TABLE_COLS } from "../lib/productionScheduleTable";
import {
  PRODUCTION_SHEET_ZOOM_CSS_VAR,
  PRODUCTION_SHEET_ZOOM_DEFAULT,
  PRODUCTION_SHEET_ZOOM_MAX,
  PRODUCTION_SHEET_ZOOM_MIN,
  PRODUCTION_SHEET_ZOOM_STORAGE_KEY,
  productionSheetZoomFromWheel,
} from "../lib/productionSheetZoom";
import ProductionSchedule from "./ProductionSchedule";
import { productionTodayKey } from "../lib/productionScheduleFilters";
import { buildProductionScheduleHariIniPreviewDummyRows, shiftProductionDateKey } from "../lib/productionScheduleHariIniPreviewDummy";

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
    expect(api.productionDashboard).toHaveBeenCalledWith({
      include_cancelled: false,
      include_completed: true,
      view: "summary",
    });
    const headers = [...container.querySelectorAll('[data-testid="production-sheet-table"] thead .prod-th-label')].map((el) => el.textContent.trim());
    expect(headers).toEqual(DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label));
    expect(headers).not.toContain("Driver");
    expect(headers).not.toContain("Surat Jalan");
    expect(headers).toContain("No PO");
    expect(headers).toContain("Customer");
    expect(headers).toContain("Status Akhir");
    expect(container.querySelector('[data-testid="prod-col-groups"]')).toBeTruthy();
    expect(container.querySelector(".prod-sticky-po")).toBeTruthy();
    expect(container.querySelector(".prod-sticky-customer")).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-th-filter-po"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-th-filter-status"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-in"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-out"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-schedule-zoom-fit"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-po-link-PO-1001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-customer-PO-1001"]').textContent).toContain("PT Example");
    expect(container.textContent).toContain("Cup 16oz Extra Long Name");
    expect(container.querySelector('[data-testid="prod-search"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-search-btn"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-ringkasan"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-work-date-PO-1001"]').textContent).toContain("25/08/26");
    expect(container.querySelector('[data-testid="prod-work-date-PO-1001"]').textContent).not.toContain("2026-08-25");
  });

  test("Tanggal Pengerjaan displays DD/MM/YY without mutating raw work_date", async () => {
    api.productionDashboard.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          po_number: "PO-A",
          work_date: "2026-09-09",
          operators: [{ ...sampleRows[0].operators[0], work_date: "2026-09-09" }],
        },
        {
          ...sampleRows[0],
          id: "po-b",
          po_number: "PO-B",
          work_date: "2026-01-05",
          operators: [{ ...sampleRows[0].operators[0], work_date: "2026-01-05" }],
        },
        {
          ...sampleRows[0],
          id: "po-c",
          po_number: "PO-C",
          work_date: "2025-12-31",
          operators: [{ ...sampleRows[0].operators[0], work_date: "2025-12-31" }],
        },
        {
          ...sampleRows[0],
          id: "po-d",
          po_number: "PO-D",
          work_date: "2026-09-09T00:00:00Z",
          operators: [{ ...sampleRows[0].operators[0], work_date: "2026-09-09T00:00:00Z" }],
        },
        {
          ...sampleRows[0],
          id: "po-e",
          po_number: "PO-E",
          work_date: null,
          operators: [],
        },
      ],
      total: 5,
    });
    await renderPage();
    expect(container.querySelector('[data-testid="prod-work-date-PO-A"]').textContent).toContain("09/09/26");
    expect(container.querySelector('[data-testid="prod-work-date-PO-B"]').textContent).toContain("05/01/26");
    expect(container.querySelector('[data-testid="prod-work-date-PO-C"]').textContent).toContain("31/12/25");
    expect(container.querySelector('[data-testid="prod-work-date-PO-D"]').textContent).toContain("09/09/26");
    expect(container.querySelector('[data-testid="prod-work-date-PO-E"]').textContent).toContain("Belum Dijadwalkan");
    expect(container.textContent).not.toContain("2026-09-09");
    expect(container.textContent).not.toContain("2026-01-05");
    expect(container.textContent).not.toContain("2025-12-31");
    expect(container.textContent).not.toMatch(/Invalid Date|NaN\/NaN\/NaN|01\/01\/70/);
    const payload = api.productionDashboard.mock.results[0].value;
    const rows = await payload;
    expect(rows.rows.map((r) => r.work_date)).toEqual([
      "2026-09-09",
      "2026-01-05",
      "2025-12-31",
      "2026-09-09T00:00:00Z",
      null,
    ]);
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
    expect(afterOne).toBe(target);
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

  test("one operator with two item lines becomes two rows; qty sits on the item line", async () => {
    api.productionDashboard.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        operators: [
          { split_id: "s-isma-a", operator_user_id: "u-isma", operator_name: "Isma Nur Alamsyah", quantity: 2000, line_idx: 0, line_id: "lid-a", line_name: "Plastik PE 28", line_printable: true, work_date: "2026-08-25" },
          { split_id: "s-isma-b", operator_user_id: "u-isma", operator_name: "Isma Nur Alamsyah", quantity: 2600, line_idx: 1, line_id: "lid-b", line_name: "Plastik PE 28", line_printable: true, work_date: "2026-08-25" },
        ],
      }],
      total: 1,
    });
    await renderPage();
    const rows = [...container.querySelectorAll('[data-testid="prod-row-PO-1001"]')];
    expect(rows).toHaveLength(2);
    expect(rows.map((row) => row.querySelector(".prod-op-name")?.textContent)).toEqual([
      "Isma Nur Alamsyah",
      "Isma Nur Alamsyah",
    ]);
    expect(container.querySelector(".prod-op-qty")).toBeFalsy();
    expect([...container.querySelectorAll(".mop-ops-item-name")].map((el) => el.textContent)).toEqual([
      "Plastik PE 28",
      "Plastik PE 28",
    ]);
    expect([...container.querySelectorAll(".mop-ops-item-qty")].map((el) => el.textContent)).toEqual([
      "2.000 pcs",
      "2.600 pcs",
    ]);
    expect(container.textContent).not.toContain("4.600");
    expect(container.querySelector('[data-testid="prod-total-visible"]').textContent).toBe("1 / 1 entri");
  });

  test("Screen column shows line-level Screen Besar / Screen Kecil / em dash", async () => {
    api.productionDashboard.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        operators: [
          { split_id: "s-a", operator_name: "Budi", quantity: 4000, line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz", line_printable: true, screen_size: "large" },
          { split_id: "s-b", operator_name: "Andi", quantity: 1000, line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz", line_printable: true, screen_size: "large" },
          { split_id: "s-c", operator_name: "Sari", quantity: 500, line_idx: 2, line_id: "lid-b", line_name: "Cup 16oz", line_printable: true, screen_size: "small" },
          { split_id: "s-d", operator_name: "Cici", quantity: 200, line_idx: 3, line_id: "lid-c", line_name: "Box", line_printable: true },
        ],
      }],
      total: 1,
    });
    await renderPage();
    expect(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).toContain("Screen");
    const texts = [...container.querySelectorAll('[data-testid="prod-row-PO-1001"] [data-testid^="prod-screen-size-PO-1001-"]')]
      .map((el) => el.textContent.trim());
    expect(texts).toEqual(["Screen Besar", "Screen Besar", "—", "Screen Kecil"]);
  });

  test("Hari Ini table keeps today + unfinished carry-over and hides future/unscheduled/historical-complete", async () => {
    const today = productionTodayKey();
    const past = shiftProductionDateKey(today, -2);
    const future = shiftProductionDateKey(today, 1);
    api.productionDashboard.mockResolvedValue({
      rows: [
        {
          id: "a", po_number: "PO-TODAY-OPEN", work_date: today, current_stage: 8, total_quantity: 1000, print_qty_total: 1000,
          operators: [{ split_id: "a", operator_email: "a@x.com", operator_name: "A", quantity: 1000, qty_completed: 100, work_date: today, line_printable: true }],
        },
        {
          id: "b", po_number: "PO-TODAY-DONE", work_date: today, current_stage: 14, status_final: "Terkirim", total_quantity: 1000, print_qty_total: 1000,
          operators: [{ split_id: "b", operator_email: "b@x.com", operator_name: "B", quantity: 1000, qty_completed: 1000, work_date: today, line_printable: true }],
        },
        {
          id: "b2", po_number: "PO-TODAY-DONE-ACTIVE", work_date: today, current_stage: 8, status_final: "Operator", total_quantity: 1000, print_qty_total: 1000,
          operators: [{ split_id: "b2", operator_email: "b2@x.com", operator_user_id: "u-b2", operator_name: "B2", quantity: 1000, qty_completed: 1000, work_date: today, line_printable: true }],
        },
        {
          id: "c", po_number: "PO-CARRY", work_date: past, current_stage: 8, total_quantity: 1000, print_qty_total: 1000,
          operators: [{ split_id: "c", operator_email: "c@x.com", operator_name: "C", quantity: 1000, qty_completed: 400, work_date: past, line_printable: true }],
        },
        {
          id: "d", po_number: "PO-HIST-DONE", work_date: past, current_stage: 8, total_quantity: 1000, print_qty_total: 1000,
          operators: [{ split_id: "d", operator_email: "d@x.com", operator_name: "D", quantity: 1000, qty_completed: 1000, work_date: past, line_printable: true }],
        },
        {
          id: "e", po_number: "PO-FUTURE", work_date: future, current_stage: 8, total_quantity: 1000, print_qty_total: 1000,
          operators: [{ split_id: "e", operator_email: "e@x.com", operator_name: "E", quantity: 1000, qty_completed: 0, work_date: future, line_printable: true }],
        },
        { id: "f", po_number: "PO-UNSCHED", work_date: null, current_stage: 8, operators: [] },
      ],
      total: 7,
    });
    await renderPage();
    expect(container.querySelector('[data-testid="prod-row-PO-UNSCHED"]')).toBeTruthy();
    const select = container.querySelector('[data-testid="prod-workdate-filter"]');
    await act(async () => {
      select.value = "hari_ini";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="prod-row-PO-TODAY-OPEN"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-TODAY-DONE"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-TODAY-DONE-ACTIVE"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-CARRY"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-HIST-DONE"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-row-PO-FUTURE"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-row-PO-UNSCHED"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-total-visible"]').textContent).toBe("4 / 7 entri");

    const selesai = container.querySelector('[data-testid="prod-include-completed"]');
    await act(async () => { selesai.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(api.productionDashboard).toHaveBeenCalledWith({
      include_cancelled: false,
      include_completed: true,
      view: "summary",
    });
    expect(container.querySelector('[data-testid="prod-row-PO-TODAY-DONE"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-row-PO-TODAY-DONE-ACTIVE"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-row-PO-TODAY-OPEN"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-CARRY"]')).toBeTruthy();
  });

  test("Tgl Pengerjaan offers Hari Ini and Dari/Sampai leaves that mode", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows, total: 1 });
    await renderPage();
    const select = container.querySelector('[data-testid="prod-workdate-filter"]');
    expect([...select.options].map((opt) => opt.value)).toEqual(["all", "hari_ini", "scheduled", "unscheduled"]);
    expect([...select.options].map((opt) => opt.textContent)).toContain("Hari Ini");
    await act(async () => {
      select.value = "hari_ini";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(select.value).toBe("hari_ini");
    const from = container.querySelector('[data-testid="prod-workdate-from"]');
    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setValue.call(from, "2026-09-14");
      from.dispatchEvent(new Event("input", { bubbles: true }));
      from.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
    expect(select.value).toBe("all");
    expect(from.value).toBe("2026-09-14");
  });

  test("date-column popups have Hari Ini; non-date and toolbar do not", async () => {
    api.productionDashboard.mockResolvedValue({ rows: sampleRows, total: 1 });
    await renderPage();
    expect(container.querySelector('[data-testid="prod-th-today-work_date"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-th-today-deadline"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-th-today-customer"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-th-today-operator"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-filters"] .prod-th-today')).toBeFalsy();
    const toolbarHariIni = [...container.querySelectorAll('[data-testid="prod-filters"] button')]
      .some((btn) => btn.textContent.trim() === "Hari Ini");
    expect(toolbarHariIni).toBe(false);
  });

  test("Hari Ini sets independent today ranges and composes with operator; reset clears", async () => {
    const dummy = buildProductionScheduleHariIniPreviewDummyRows();
    api.productionDashboard.mockResolvedValue({ rows: dummy, total: dummy.length });
    await renderPage();
    const today = productionTodayKey();
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D3"]')).toBeTruthy();

    await act(async () => {
      container.querySelector('[data-testid="prod-th-today-work_date"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="prod-workdate-from"]').value).toBe(today);
    expect(container.querySelector('[data-testid="prod-workdate-to"]').value).toBe(today);
    expect(container.querySelector('[data-testid="prod-date-from"]').value).toBe("");
    expect(container.querySelector('[data-testid="prod-date-to"]').value).toBe("");
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D2"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D3"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="prod-th-today-deadline"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="prod-date-from"]').value).toBe(today);
    expect(container.querySelector('[data-testid="prod-date-to"]').value).toBe(today);
    expect(container.querySelector('[data-testid="prod-workdate-from"]').value).toBe(today);
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D2"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D3"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="prod-th-filter-deadline-clear"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const operator = container.querySelector('[data-testid="prod-filter-operator"]');
    await act(async () => {
      operator.value = "Fauzan";
      operator.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D2"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D3"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="prod-th-filter-work_date-clear"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="prod-workdate-from"]').value).toBe("");
    expect(container.querySelector('[data-testid="prod-workdate-to"]').value).toBe("");
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D2"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prod-row-PO-HI-D3"]')).toBeFalsy();
  });
});
