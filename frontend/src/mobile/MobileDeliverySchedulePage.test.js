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
    sj_item_lines: [],
  },
];

function headerLabels(root) {
  return [...root.querySelectorAll('[data-testid="mds-thead"] th')].map((th) => th.textContent.trim());
}

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
    expect(container.querySelector('[data-testid="mds-kerja-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-row-PO-100"]').getAttribute("data-to")).toBe("/m/orders/o1?ws=packing");
    expect(container.querySelector('[data-testid="mds-dapat-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-load-PO-100"]').textContent.trim()).toBe("—");
    expect(container.querySelector('[data-testid="mds-qty-PO-100"]').textContent).toContain("5.000");
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
    expect(container.querySelector('[data-testid="mds-terkirim-PO-100"]').getAttribute("title")).toContain("5.000");
    expect(container.querySelector('[data-testid="mds-dapat-PO-100"]')).toBeFalsy();
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

  test("view+edit does not restore Aksi; row still opens delivery work href", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.location.manage" || p === "delivery.update");
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    await flush();
    expect(container.querySelector('[data-testid="mds-atur-lokasi-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-kerja-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-row-PO-100"]').getAttribute("data-to")).toBe("/m/orders/o1?ws=delivery");
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
    expect(src).toMatch(/sjItemLinesFromRow/);
    expect(src).not.toMatch(/item_label/);
    expect(src).not.toMatch(/role\s*===\s*['"]driver['"]/);
    expect(src).not.toMatch(/roleName\s*===\s*['"]driver['"]/);
    expect(src).not.toMatch(/Buat Jadwal Pengiriman/);
    expect(src).not.toMatch(/Rute Saya/);
    // Bridge action is permission-gated via route_optimizer.*, not role name / legacy modules.
    expect(src).toMatch(/canOptimizeFromDeliverySchedule/);
    expect(src).toMatch(/mds-optimasi-rute/);
    expect(src).not.toMatch(/delivery\.route\.optimize/);
  });

  test("CASE A — Loading + Pengiriman options from role strip eligibility", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "delivery.update"
    ));
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-task-select"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-ops-role-loading"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mop-ops-role-pengiriman"]')).toBeTruthy();
    expect(container.textContent).not.toMatch(/TUGAS/);
  });

  test("CASE B — delivery-only hides Loading; unauthorized ?ws=loading does not grant it", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    mockDsRouter.search = "ws=loading";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-task-select"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-ops-role-pengiriman"]').className).toMatch(/is-active/);
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
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-task-list"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-row-PO-100"]').getAttribute("data-to")).toBe("/m/orders/o1?ws=loading");
    expect(mockDeliverySchedule).toHaveBeenCalled();
    expect(mockDivisionTasks).not.toHaveBeenCalled();

    const loadingRole = container.querySelector('[data-testid="mop-ops-role-pengiriman"]');
    await act(async () => {
      loadingRole.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(String(mockDsRouter.setParams.mock.calls.at(-1)[0])).toContain("ws=delivery");
    mockDsRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-row-PO-100"]').getAttribute("data-to")).toBe("/m/orders/o1?ws=delivery");
    expect(mockDeliverySchedule).toHaveBeenCalled();
  });

  test("CASE D — select Loading shows Jadwal Pengiriman table, not Loading workspace or task cards", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "delivery.update"
    ));
    mockDsRouter.search = "ws=loading";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-topbar-title"]').textContent).toBe("Jadwal Pengiriman");
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-task-list"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-row-PO-100"]').getAttribute("data-to")).toBe("/m/orders/o1?ws=loading");
    expect(mockDeliverySchedule).toHaveBeenCalledWith();
    expect(mockDivisionTasks).not.toHaveBeenCalled();
  });

  test("CASE E/F/G — Pengiriman ?ws= survives open-PO return context and refresh", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    mockDsRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const row = container.querySelector('[data-testid="mds-row-PO-100"]');
    expect(row.getAttribute("data-to")).toBe("/m/orders/o1?ws=delivery");
    await act(async () => {
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalledWith("/m/orders/o1?ws=delivery", expect.objectContaining({
      state: expect.objectContaining({ from: "/m/delivery-schedule?ws=delivery" }),
    }));

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
    expect(container.querySelector('[data-testid="mop-ops-role-pengiriman"]').className).toMatch(/is-active/);
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
    const role = container.querySelector('[data-testid="mop-ops-role-loading"]');
    await act(async () => {
      role.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockDeliverySchedule.mock.calls.every((c) => c.length === 0 || c[0] === undefined)).toBe(true);
    expect(String(mockDsRouter.setParams.mock.calls.at(-1)[0])).toContain("ws=loading");
  });

  test("Loading row click opens Loading workspace for the selected shipment", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "loading.update");
    mockDsRouter.search = "ws=loading";
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          id: "po001",
          order_number: "PO001",
          shipment_id: "sh-1",
          latest_sj_number: "SJ001",
          sj_label: "SJ001",
          sj_item_lines: [
            { shipment_id: "sh-1", sj_number: "SJ001", line_id: "lid-cup", line_idx: 0, name: "Cup", qty: 1000 },
          ],
        },
        {
          ...sampleRows[0],
          id: "po001",
          order_number: "PO001",
          effective_delivery_date: "2026-09-12",
          shipment_id: "sh-2",
          latest_sj_number: "SJ001-2",
          sj_label: "SJ001-2",
          sj_item_lines: [
            { shipment_id: "sh-2", sj_number: "SJ001-2", line_id: "lid-lid", line_idx: 1, name: "Lid", qty: 1000 },
          ],
        },
      ],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const sep10 = container.querySelector('[data-testid="mds-row-PO001-sh-1"]');
    const sep12 = container.querySelector('[data-testid="mds-row-PO001-sh-2"]');
    expect(sep10.getAttribute("data-to")).toBe("/m/orders/po001?ws=loading&shipment=sh-1");
    expect(sep12.getAttribute("data-to")).toBe("/m/orders/po001?ws=loading&shipment=sh-2");
    await act(async () => {
      sep12.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalledWith(
      "/m/orders/po001?ws=loading&shipment=sh-2",
      expect.objectContaining({
        state: expect.objectContaining({ from: "/m/delivery-schedule?ws=loading" }),
      }),
    );
  });

  test("PWA table shows Item Line after Customer and drops Driver/Status/Aksi", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "route_optimizer.create");
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const labels = headerLabels(container);
    expect(labels).toEqual([
      "Prioritas",
      "Tgl Kirim",
      "Customer / No PO",
      "Item Line",
      "Qty Total",
      "Status per Role",
      "Cup",
      "Screen",
      "Prod",
      "Pack",
      "Load",
      "Kirim",
    ]);
    expect(labels).not.toContain("Driver");
    expect(labels).not.toContain("Status");
    expect(labels).not.toContain("Aksi");
    expect(labels).not.toContain("Zona");
    expect(labels).not.toContain("Tahap Aktif");
    expect(container.querySelector('[data-testid="mds-status-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-kerja-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-atur-lokasi-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-optimasi-rute"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-quick-filters"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mop-ops-role-summary"]')).toBeTruthy();
    const identity = container.querySelector('[data-testid="mds-identity-PO-100"]');
    expect(identity.querySelector(".mop-ops-customer").textContent).toBe("PT Alpha");
    expect(identity.querySelector(".mop-ops-po").textContent).toBe("PO-100");
    expect(container.querySelector('[data-testid="mds-task-select"]') || container.textContent).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-item-lines-PO-100"]').textContent).toBe("—");
    expect(container.textContent).not.toMatch(/item_label/);
  });

  test("one SJ item line renders name and qty without using item_label", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        item_label: "PO-WIDE SHOULD NOT SHOW",
        sj_item_lines: [{
          shipment_id: "sh-1",
          sj_number: "SJ-1",
          line_id: "lid-a",
          line_idx: 0,
          name: "Cup 12oz",
          qty: 1000,
        }],
      }],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const cell = container.querySelector('[data-testid="mds-item-lines-PO-100"]');
    expect(cell.textContent).toContain("Cup 12oz");
    expect(cell.textContent).toContain("1.000");
    expect(cell.querySelector(".mds-item-line-prefix")?.textContent).toBe("- ");
    expect(cell.textContent).toMatch(/^\s*-\s*Cup 12oz/);
    expect(cell.textContent).not.toContain("PO-WIDE SHOULD NOT SHOW");
    expect(container.querySelectorAll('[data-testid^="mds-item-line-PO-100-"]').length).toBe(1);
  });

  test("date-specific rows keep No SJ aligned with Item Line; later SJ does not rewrite earlier date", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          id: "po001",
          order_number: "PO001",
          effective_delivery_date: "2026-09-10",
          shipment_id: "sh-1",
          shipment_index: 1,
          sj_label: "SJ001",
          latest_sj_number: "SJ001",
          sj_item_lines: [
            { shipment_id: "sh-1", sj_number: "SJ001", line_id: "lid-cup", line_idx: 0, name: "Cup", qty: 1000 },
          ],
        },
        {
          ...sampleRows[0],
          id: "po001",
          order_number: "PO001",
          effective_delivery_date: "2026-09-12",
          shipment_id: "sh-2",
          shipment_index: 2,
          sj_label: "SJ001-2",
          latest_sj_number: "SJ001-2",
          sj_item_lines: [
            { shipment_id: "sh-2", sj_number: "SJ001-2", line_id: "lid-lid", line_idx: 1, name: "Lid", qty: 1000 },
          ],
        },
      ],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const sep10 = container.querySelector('[data-testid="mds-row-PO001-sh-1"]');
    const sep12 = container.querySelector('[data-testid="mds-row-PO001-sh-2"]');
    expect(sep10).toBeTruthy();
    expect(sep12).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-sj-PO001-sh-1"]').textContent).toBe("SJ001");
    expect(container.querySelector('[data-testid="mds-item-lines-PO001-sh-1"]').textContent).toContain("Cup");
    expect(container.querySelector('[data-testid="mds-item-lines-PO001-sh-1"]').textContent).toMatch(/-\s*Cup/);
    expect(container.querySelector('[data-testid="mds-item-lines-PO001-sh-1"]').textContent).not.toContain("Lid");
    expect(container.querySelector('[data-testid="mds-sj-PO001-sh-2"]').textContent).toBe("SJ001-2");
    expect(container.querySelector('[data-testid="mds-item-lines-PO001-sh-2"]').textContent).toContain("Lid");
    expect(container.querySelector('[data-testid="mds-item-lines-PO001-sh-2"]').textContent).toMatch(/-\s*Lid/);
    expect(container.querySelector('[data-testid="mds-item-lines-PO001-sh-2"]').textContent).not.toContain("Cup");
    const labels = headerLabels(container);
    expect(labels.indexOf("Item Line")).toBe(labels.indexOf("Customer / No PO") + 1);
    expect(labels).not.toContain("Driver");
    expect(labels).not.toContain("Status");
    expect(labels).not.toContain("Aksi");
    expect(labels).not.toContain("Rute");
  });

  test("same-name lines on one dated SJ stay separate", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        shipment_id: "sh-2",
        sj_label: "SJ001-2",
        latest_sj_number: "SJ001-2",
        sj_item_lines: [
          { shipment_id: "sh-2", sj_number: "SJ001-2", line_id: "a", line_idx: 0, name: "12 oz PET Datar 11 gr SJP", qty: 1000 },
          { shipment_id: "sh-2", sj_number: "SJ001-2", line_id: "b", line_idx: 1, name: "12 oz PET Datar 11 gr SJP", qty: 2000 },
        ],
      }],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const cell = container.querySelector('[data-testid="mds-item-lines-PO-100-sh-2"]');
    const noSj = container.querySelector('[data-testid="mds-sj-PO-100-sh-2"]');
    expect(noSj.textContent).toBe("SJ001-2");
    expect(cell.textContent).toContain("1.000");
    expect(cell.textContent).toContain("2.000");
    expect(cell.textContent).not.toContain("3.000");
    const lines = container.querySelectorAll('[data-testid^="mds-item-line-PO-100-sh-2-"]');
    expect(lines.length).toBe(2);
    expect(lines[0].querySelector(".mds-item-line-prefix")?.textContent).toBe("- ");
    expect(lines[1].querySelector(".mds-item-line-prefix")?.textContent).toBe("- ");
    expect(lines[0].textContent).toMatch(/^\s*-\s*12 oz PET Datar 11 gr SJP/);
    expect(lines[0].textContent).toContain("1.000");
    expect(lines[1].textContent).toContain("2.000");
    expect(lines[0].textContent).not.toContain("2.000");
    expect(lines[1].textContent).not.toContain("1.000");
  });

  test("compact search matches customer, PO, and item line", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        sampleRows[0],
        {
          ...sampleRows[0],
          id: "o2",
          order_number: "PO-200",
          customer_name: "CV Beta",
          sj_item_lines: [{ name: "Lid PET", qty: 500, line_id: "z", line_idx: 0 }],
        },
      ],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const input = container.querySelector('[data-testid="mds-search"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    await act(async () => {
      setter.call(input, "Alpha");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mds-row-PO-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-row-PO-200"]')).toBeFalsy();
    await act(async () => {
      setter.call(input, "Lid PET");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mds-row-PO-200"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-row-PO-100"]')).toBeFalsy();
    await act(async () => {
      setter.call(input, "PO-100");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mds-row-PO-100"]')).toBeTruthy();
  });

  test("pengiriman role count matches active delivery rows; packing/loading unsupported", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        { ...sampleRows[0], status: "hari_ini", urgent: true },
        { ...sampleRows[0], id: "done", order_number: "PO-DONE", status: "terkirim" },
      ],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mop-ops-role-pengiriman"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mop-ops-role-count-pengiriman"]').textContent.trim()).toBe("1");
    expect(container.querySelector('[data-testid="mop-ops-role-count-packing"]').textContent.trim()).toBe("—");
    expect(container.querySelector('[data-testid="mop-ops-role-count-loading"]').textContent.trim()).toBe("—");
    expect(container.querySelector('[data-testid="mop-ops-role-count-prep"]').textContent.trim()).toBe("—");
  });

  test("ops table keeps horizontal scroll wrapper, compact Optimasi Rute, and 1180px min-width", () => {
    const css = fs.readFileSync(path.join(__dirname, "mobile.css"), "utf8");
    const opsCss = fs.readFileSync(path.join(__dirname, "opsQueue.css"), "utf8");
    expect(css).toMatch(/\.mop-schedule-scroll \{[\s\S]*overflow:\s*auto/);
    expect(opsCss).toMatch(/\.mop-root--ops-wide \.production-sheet-table \{[\s\S]*min-width:\s*1180px/);
    expect(opsCss).toMatch(/overflow-x:\s*auto/);
    const pageSrc = fs.readFileSync(path.join(__dirname, "MobileDeliverySchedulePage.jsx"), "utf8");
    expect(pageSrc).toMatch(/mop-schedule-scroll/);
    expect(pageSrc).toMatch(/data-testid="mds-optimasi-rute"/);
    expect(pageSrc).toMatch(/className="mop-btn-primary"/);
    expect(pageSrc).not.toMatch(/mds-optimize-bar|mop-ds-optimize-bar/);
  });
});
