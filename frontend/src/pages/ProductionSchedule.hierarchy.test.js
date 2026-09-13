import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS } from "../lib/productionScheduleTable";
import ProductionSchedule from "./ProductionSchedule";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: () => true }),
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

const sampleRow = {
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
};

describe("ProductionSchedule visual hierarchy", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.productionDashboard.mockReset();
    api.productionDashboard.mockResolvedValue({ rows: [sampleRow], total: 1 });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("renders the dense table without Driver or Surat Jalan columns", async () => {
    await act(async () => { root.render(<ProductionSchedule />); });
    await flush();
    const table = container.querySelector('[data-testid="production-sheet-table"]');
    expect(table).toBeTruthy();
    const labels = [...table.querySelectorAll("thead .prod-th-label")].map((el) => el.textContent.trim());
    expect(labels).toEqual(DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label));
    expect(labels).toContain("No PO");
    expect(labels).toContain("Customer");
    expect(labels).toContain("Status Akhir");
    expect(labels).not.toContain("Driver");
    expect(labels).not.toContain("Surat Jalan");
    expect(container.querySelector('[data-testid="prod-po-link-PO-1001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-customer-PO-1001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-status-PO-1001"]').textContent).toContain("Operator");
    expect(container.querySelector('[data-testid="prod-th-filter-po"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-th-filter-customer"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-th-filter-status"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-col-group-identity"]').textContent).toContain("Identitas PO");
    expect(container.querySelector('[data-testid="prod-col-group-planning"]').textContent).toContain("Penjadwalan");
    expect(container.querySelector('[data-testid="prod-col-group-production"]').textContent).toContain("Produksi");
    expect(container.querySelector('[data-testid="prod-col-group-progress"]').textContent).toContain("Progress Produksi");
    expect(container.querySelector('[data-testid="prod-col-group-status"]').textContent).toContain("Status");
    expect(container.querySelector('[data-testid="prod-col-group-identity"]').getAttribute("colspan")).toBe("2");
    expect(container.querySelector('[data-testid="prod-col-group-planning"]').getAttribute("colspan")).toBe("2");
    expect(container.querySelector('[data-testid="prod-col-group-production"]').getAttribute("colspan")).toBe("9");
    expect(container.querySelector('[data-testid="prod-col-group-progress"]').getAttribute("colspan")).toBe("6");
    expect(container.querySelector('[data-testid="prod-col-group-status"]').getAttribute("colspan")).toBe("1");
    expect(container.querySelector("th.prod-sticky-po")).toBeTruthy();
    expect(container.querySelector("th.prod-sticky-customer")).toBeTruthy();
    expect(container.querySelector("td.prod-sticky-po")).toBeTruthy();
    expect(container.querySelector("td.prod-sticky-customer")).toBeTruthy();
    expect(container.querySelector("col.prod-col-po")).toBeTruthy();
    expect(container.querySelector("col.prod-col-customer")).toBeTruthy();
    const labelHeaders = [...container.querySelectorAll("thead .prod-col-label-row th")];
    expect(labelHeaders.length).toBe(DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS.length);
    expect(labelHeaders.every((th) => th.classList.contains("prod-col-label"))).toBe(true);
    expect(labelHeaders.some((th) => /prod-col-group-(identity|planning|production|progress|status)/.test(th.className))).toBe(false);
    expect(container.querySelector("thead .prod-col-group-row th.prod-col-group-identity")).toBeTruthy();
    expect(container.querySelector("thead .prod-col-group-row th.prod-col-group-planning")).toBeTruthy();
    expect(container.querySelector("thead .prod-col-group-row th.prod-col-group-production")).toBeTruthy();
    expect(container.querySelector("thead .prod-col-group-row th.prod-col-group-progress")).toBeTruthy();
    expect(container.querySelector("thead .prod-col-group-row th.prod-col-group-status")).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-row-PO-1001"]').className).toContain("prod-row-urgent");
    expect(container.querySelector('[data-testid="prod-qty-PO-1001"]').className).toMatch(/\bnum\b/);
    expect(container.querySelector('[data-testid="prod-qty-cetak-PO-1001"]').className).toMatch(/\bnum\b/);
    expect(api.productionDashboard).toHaveBeenCalledWith({
      include_cancelled: false,
      include_completed: true,
      view: "summary",
    });
  });

  test("sticky widths and individual header colors are defined once on the sheet", () => {
    const sheetCss = fs.readFileSync(path.join(__dirname, "../productionSheet.css"), "utf8");
    const scheduleSrc = fs.readFileSync(path.join(__dirname, "ProductionSchedule.jsx"), "utf8");
    expect(sheetCss).toMatch(/\.prod-schedule-page \.production-sheet\s*\{[\s\S]*--prod-sticky-po-w:\s*calc\(112px \* var\(--production-sheet-zoom/);
    expect(sheetCss).toMatch(/--prod-sticky-customer-w:\s*calc\(168px \* var\(--production-sheet-zoom/);
    expect(sheetCss).toMatch(/\.prod-sticky-customer\s*\{[\s\S]*left:\s*var\(--prod-sticky-po-w\)/);
    expect(sheetCss).toMatch(/thead \.prod-col-label-row th\s*\{[\s\S]*background:\s*var\(--production-sheet-header-bg\)/);
    expect(sheetCss).toMatch(/thead \.prod-col-group-row th\.prod-col-group-identity\s*\{[\s\S]*background:\s*#dbeafe/);
    expect(scheduleSrc).toMatch(/className=\{c\.sticky === "po" \? "prod-col-po"/);
    expect(scheduleSrc).not.toMatch(/c\.desktopGroup \? `prod-col-group-\$\{c\.desktopGroup\}`/);
  });
});
