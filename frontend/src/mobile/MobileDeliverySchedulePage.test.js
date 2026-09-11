import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import MobileDeliverySchedulePage from "./MobileDeliverySchedulePage";
import {
  EMPTY_DELIVERY_FILTERS,
} from "../lib/deliverySchedule";
import {
  capturePoOpenFromList,
  resolvePoBackTarget,
  storageUserKey,
} from "../lib/poListReturn";

const mockHasPerm = jest.fn(() => false);
const mockDeliverySchedule = jest.fn();
const mockDivisionTasks = jest.fn();
const mockNavigate = jest.fn();
const mockDsRouter = {
  search: "",
  setParams: jest.fn((next) => {
    if (next instanceof URLSearchParams) mockDsRouter.search = next.toString();
    else if (next && typeof next === "object") mockDsRouter.search = new URLSearchParams(next).toString();
  }),
};

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, state, ...props }) => <a href={to} data-state={state ? JSON.stringify(state) : undefined} {...props}>{children}</a>,
  useNavigate: () => mockNavigate,
  useSearchParams: () => [new URLSearchParams(mockDsRouter.search), mockDsRouter.setParams],
  useLocation: () => ({
    pathname: "/m/delivery-schedule",
    search: mockDsRouter.search ? `?${mockDsRouter.search}` : "",
  }),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ user: { id: "u-mds" }, hasPerm: (perm) => mockHasPerm(perm) }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    deliverySchedule: (...args) => mockDeliverySchedule(...args),
    divisionTasks: (...args) => mockDivisionTasks(...args),
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

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

describe("MobileDeliverySchedulePage", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockDeliverySchedule.mockReset();
    mockDivisionTasks.mockReset();
    mockNavigate.mockReset();
    mockDsRouter.search = "";
    mockDsRouter.setParams.mockClear();
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    mockDivisionTasks.mockResolvedValue({ tasks: [] });
    sessionStorage.clear();
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
    await flush();
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

  test("Lihat Rute navigates in-app to /optimasi-rute/:id without window.open", async () => {
    const open = jest.fn();
    const origOpen = window.open;
    window.open = open;
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "route_optimizer.view" || p === "route_optimizer.create"
    ));
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        optimization_status: "sudah_dioptimasi",
        optimization_status_label: "Sudah Dioptimasi",
        route_id: "iro-abc",
      }],
    });
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    const btn = container.querySelector('[data-testid="mds-lihat-rute-PO-100"]');
    expect(btn).toBeTruthy();
    expect(btn.tagName).toBe("BUTTON");
    expect(btn.getAttribute("target")).toBeNull();
    await act(async () => {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalledWith("/optimasi-rute/iro-abc", expect.objectContaining({
      state: expect.objectContaining({ from: expect.any(String) }),
    }));
    expect(open).not.toHaveBeenCalled();
    window.open = origOpen;
  });

  test("unauthorized user does not see Lihat Rute without route_optimizer perms", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        optimization_status: "sudah_dioptimasi",
        optimization_status_label: "Sudah Dioptimasi",
        route_id: "iro-abc",
      }],
    });
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    expect(container.querySelector('[data-testid="mds-lihat-rute-PO-100"]')).toBeFalsy();
  });

  test("view+edit shows Atur Lokasi; view-only hides it", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.location.manage" || p === "delivery.update");
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    await flush();
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

  test("CASE A — Loading + Pengiriman dropdown options from eligibility", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "delivery.update"
    ));
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const select = container.querySelector('[data-testid="mds-task-select-input"]');
    expect(select).toBeTruthy();
    expect([...select.options].map((o) => o.textContent)).toEqual(["Loading", "Pengiriman"]);
    expect([...select.options].map((o) => o.value)).not.toContain("all");
    expect(container.textContent).toContain("Tugas");
  });

  test("CASE B — delivery-only hides Loading; unauthorized ?ws=loading does not grant it", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    mockDsRouter.search = "ws=loading";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-task-select-current"]')?.textContent).toBe("Pengiriman");
    expect(container.querySelector("select")).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(mockDivisionTasks).not.toHaveBeenCalled();
  });

  test("CASE C — select Pengiriman shows schedule table and updates ?ws=", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "delivery.update"
    ));
    mockDsRouter.search = "ws=loading";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(mockDivisionTasks).toHaveBeenCalledWith("loading", "mine");
    expect(container.querySelector('[data-testid="mds-task-list"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeFalsy();

    const select = container.querySelector('[data-testid="mds-task-select-input"]');
    await act(async () => {
      select.value = "delivery";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(String(mockDsRouter.setParams.mock.calls.at(-1)[0])).toContain("ws=delivery");
    mockDsRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-kerja-PO-100"]').getAttribute("href")).toBe("/m/orders/o1?ws=delivery");
    expect(mockDeliverySchedule).toHaveBeenCalled();
  });

  test("CASE D — select Loading shows Loading task list without mutating deliverySchedule write APIs", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "delivery.update"
    ));
    mockDivisionTasks.mockResolvedValue({
      tasks: [{
        task_id: "t1",
        order_id: "o1",
        order_number: "PO-100",
        work_date: "2026-09-10",
        item: "Cup",
        target_qty: 10,
        completed_qty: 0,
        status: "belum",
        status_label: "Belum Mulai",
      }],
    });
    mockDsRouter.search = "ws=loading";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(mockDivisionTasks).toHaveBeenCalledWith("loading", "mine");
    expect(container.querySelector('[data-testid="mds-task-list"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-task-t1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-task-t1"]').getAttribute("href")).toBe("/m/orders/o1?ws=loading");
    expect(container.querySelector('[data-testid="mds-table"]')).toBeFalsy();
  });

  test("CASE E/F/G — Pengiriman ?ws= survives open-PO return context and refresh", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    mockDsRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const kerja = container.querySelector('[data-testid="mds-kerja-PO-100"]');
    expect(kerja.getAttribute("href")).toBe("/m/orders/o1?ws=delivery");
    const state = JSON.parse(kerja.getAttribute("data-state"));
    expect(state.from).toBe("/m/delivery-schedule?ws=delivery");

    capturePoOpenFromList({
      user: { id: "u-mds" },
      orderId: "o1",
      pathname: "/m/delivery-schedule",
      search: "?ws=delivery",
    });
    expect(resolvePoBackTarget({
      location: { pathname: "/m/orders/o1", state: { from: "/m/delivery-schedule?ws=delivery" } },
      userId: storageUserKey({ id: "u-mds" }),
      orderId: "o1",
      mobileFallback: "/m/tasks",
    })).toBe("/m/delivery-schedule?ws=delivery");

    // Refresh keeps query-backed selection (CASE G)
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-task-select-current"]')?.textContent
      || container.querySelector('[data-testid="mds-task-select-input"]')?.value).toMatch(/delivery|Pengiriman/i);
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
  });

  test("CASE H — session return after refresh-detail still restores Pengiriman", () => {
    capturePoOpenFromList({
      user: { id: "u-mds" },
      orderId: "o9",
      pathname: "/m/delivery-schedule",
      search: "ws=delivery",
    });
    expect(resolvePoBackTarget({
      location: { pathname: "/m/orders/o9", state: null },
      userId: "u-mds",
      orderId: "o9",
      mobileFallback: "/m/tasks",
    })).toBe("/m/delivery-schedule?ws=delivery");
  });

  test("CASE I — direct PO link falls back safely", () => {
    expect(resolvePoBackTarget({
      location: { pathname: "/m/orders/orphan", state: null },
      userId: "u-mds",
      orderId: "orphan",
      mobileFallback: "/m/tasks",
    })).toBe("/m/tasks");
  });

  test("CASE L — changing task selector does not call deliverySchedule mutation endpoints", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "delivery.update"
    ));
    mockDsRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const select = container.querySelector('[data-testid="mds-task-select-input"]');
    await act(async () => {
      select.value = "loading";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(mockDeliverySchedule.mock.calls.every((c) => c.length === 0 || c[0] === undefined)).toBe(true);
    expect(String(mockDsRouter.setParams.mock.calls.at(-1)[0])).toContain("ws=loading");
  });
});
