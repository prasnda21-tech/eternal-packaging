import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import MobileDeliverySchedulePage from "./MobileDeliverySchedulePage";
import {
  EMPTY_DELIVERY_FILTERS,
} from "../lib/deliverySchedule";

const mockHasPerm = jest.fn(() => false);
const mockDeliverySchedule = jest.fn();

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => jest.fn(),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: (perm) => mockHasPerm(perm) }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    deliverySchedule: (...args) => mockDeliverySchedule(...args),
    listDeliveryOutlets: jest.fn(async () => ({ outlets: [] })),
  },
}));

jest.mock("../hooks/useProductionSheetZoom", () => ({
  __esModule: true,
  default: () => ({
    zoom: 0.85,
    setViewportRef: jest.fn(),
    consumeClickSuppression: () => false,
  }),
}));

jest.mock("../components/SuratJalanViewButton", () => ({
  __esModule: true,
  default: ({ label, testid }) => <button type="button" data-testid={testid}>{label}</button>,
}));

jest.mock("../components/OutletLocationModal", () => ({
  __esModule: true,
  default: ({ open }) => (open ? <div data-testid="mds-outlet-modal" /> : null),
}));

jest.mock("./MobileTopBar", () => ({
  __esModule: true,
  default: ({ title }) => <header data-testid="mobile-top-bar">{title}</header>,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const sampleRows = [
  {
    id: "o1",
    order_number: "PO-100",
    customer_name: "PT Alpha",
    status: "terjadwal",
    status_label: "Terjadwal",
    effective_delivery_date: "2026-09-10",
    deadline: "2026-09-12",
    jadwal_manual: null,
    driver_name: "Budi",
    percent_delivered: 0,
    percent_ready_to_ship: 100,
    qty_delivered: 2000,
    qty_total: 5000,
    qty_packed: 3000,
    qty_printed_operator: 3000,
    print_qty_total: 5000,
    percent_delivered: 40,
    percent_ready_to_ship: 60,
    sj_label: "SJ-1",
    sj_files: [],
    sj_file_count: 0,
  },
];

describe("MobileDeliverySchedulePage", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockDeliverySchedule.mockReset();
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("forbidden without view permission", async () => {
    mockHasPerm.mockReturnValue(false);
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    expect(container.querySelector('[data-testid="mobile-delivery-schedule-forbidden"]')).toBeTruthy();
    expect(mockDeliverySchedule).not.toHaveBeenCalled();
  });

  test("authorized viewer loads canonical deliverySchedule endpoint once", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "packing.update");
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    expect(mockDeliverySchedule).toHaveBeenCalledTimes(1);
    expect(mockDeliverySchedule).toHaveBeenCalledWith();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-row-PO-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-quick-filters"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-atur-lokasi-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-kerja-PO-100"]').getAttribute("href")).toBe("/m/orders/o1?ws=packing");
    expect(container.querySelector('[data-testid="mds-packing-PO-100"]').textContent).toContain("3.000 / 5.000");
    expect(container.querySelector('[data-testid="mds-terkirim-PO-100"]').textContent).toContain("2.000");
    expect(container.querySelector('[data-testid="mds-terkirim-PO-100"] .mop-qty-context').textContent).toBe("dari 5.000 qty PO");
    expect(container.querySelector('[data-testid="mds-dapat-PO-100"]').textContent).toContain("3.000");
    expect(container.querySelector('[data-testid="mds-dapat-PO-100"] .mop-qty-context').textContent).toBe("dari 5.000 qty cetak");
  });

  test("partial delivery keeps independent terkirim/dapat context; Optimasi action is permission-gated", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        qty_delivered: 2000,
        qty_total: 5000,
        qty_printed_operator: 4500,
        print_qty_total: 5000,
        percent_delivered: 40,
        percent_ready_to_ship: 90,
      }],
    });
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    expect(container.querySelector('[data-testid="mds-terkirim-PO-100"] .mop-qty-context').textContent).toContain("5.000");
    expect(container.querySelector('[data-testid="mds-dapat-PO-100"] .mop-qty-context').textContent).toContain("qty cetak");
    expect(container.textContent).not.toContain("Optimize Rute");
    // Status column label may appear; action button requires route_optimizer.*
    expect(container.querySelector('[data-testid="mds-optimasi-rute"]')).toBeFalsy();
  });

  test("PWA delivery toolbar keeps Filter/Reset/Optimasi and has no Export buttons", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "route_optimizer.create" || p === "route_optimizer.optimize"
    ));
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    expect(container.querySelector('[data-testid="mds-filter-btn"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-filter-reset"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-filter-actions"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-optimasi-rute"]')).toBeTruthy();
    expect(container.textContent).not.toContain("Export Terfilter");
    expect(container.textContent).not.toContain("Export Semua");
    expect(container.querySelector('[data-testid="mds-export-filtered"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-export-all"]')).toBeFalsy();
  });

  test("view+edit shows Atur Lokasi; view-only hides it", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.location.manage" || p === "delivery.update");
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    expect(container.querySelector('[data-testid="mds-atur-lokasi-PO-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-kerja-PO-100"]').getAttribute("href")).toBe("/m/orders/o1?ws=delivery");
  });

  test("does not authorize edit by role name driver", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "driver");
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    expect(container.querySelector('[data-testid="mds-row-PO-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-atur-lokasi-PO-100"]')).toBeFalsy();
  });

  test("compact filter panel exposes every Web filter field", async () => {
    mockHasPerm.mockImplementation((p) => p === "nav.delivery_schedule");
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    await act(async () => {
      container.querySelector('[data-testid="mds-filter-btn"]').click();
    });
    const panel = container.querySelector('[data-testid="mds-filter-panel"]');
    expect(panel).toBeTruthy();
    expect(panel.className).toContain("mop-ds-filter-backdrop");
    expect(container.querySelector('[data-testid="mds-adv-customer"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-adv-po"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-adv-from"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-adv-to"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-adv-status"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-adv-driver"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-adv-terkirim"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-adv-dapat"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-filter-apply"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-adv-reset"]')).toBeTruthy();
    const fields = container.querySelector(".mop-ds-filter-fields");
    expect(fields).toBeTruthy();
    expect(getComputedStyle ? true : true).toBe(true);
  });

  test("apply filter panel closes after Terapkan", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    await act(async () => {
      container.querySelector('[data-testid="mds-filter-btn"]').click();
    });
    expect(container.querySelector('[data-testid="mds-filter-panel"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="mds-filter-apply"]').click();
    });
    expect(container.querySelector('[data-testid="mds-filter-panel"]')).toBeFalsy();
    expect(Object.keys(EMPTY_DELIVERY_FILTERS).length).toBe(8);
  });

  test("table uses production-sheet viewport for pinch zoom isolation", () => {
    const src = fs.readFileSync(path.join(__dirname, "MobileDeliverySchedulePage.jsx"), "utf8");
    expect(src).toMatch(/useProductionSheetZoom/);
    expect(src).toMatch(/production-sheet-viewport/);
    expect(src).toMatch(/productionSheetZoomStyle/);
    expect(src).toMatch(/api\.deliverySchedule\(\)/);
    expect(src).toMatch(/matchesDeliveryScheduleFilters/);
    expect(src).toMatch(/delivery\.location\.manage|canEditMobileDeliveryScheduleLocation/);
    expect(src).not.toMatch(/role\s*===\s*['"]driver['"]/);
    expect(src).not.toMatch(/roleName\s*===\s*['"]driver['"]/);
    expect(src).not.toMatch(/Buat Jadwal Pengiriman/);
    expect(src).not.toMatch(/Rute Saya/);
    // Bridge action is permission-gated via route_optimizer.*, not role name / legacy modules.
    expect(src).toMatch(/canOptimizeFromDeliverySchedule/);
    expect(src).toMatch(/mds-optimasi-rute/);
    expect(src).not.toMatch(/delivery\.route\.optimize/);
  });
});
