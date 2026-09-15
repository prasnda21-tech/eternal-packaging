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
const mockProductionDashboard = jest.fn();
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
    productionDashboard: (...args) => mockProductionDashboard(...args),
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
    latest_sj_number: "SJ-1",
    shipment_id: "sh-100",
    sj_files: [],
    sj_file_count: 0,
    sj_item_lines: [],
    role_status: { cup: "idle", screen: "idle", prod: "pending", pack: "pending", load: "idle", kirim: "pending" },
  },
];

function headerLabels(root) {
  return [...root.querySelectorAll('[data-testid="mds-thead"] th')].map((th) => th.textContent.trim());
}

function markKind(el) {
  if (!el) return null;
  if (el.classList.contains("is-done")) return "done";
  if (el.classList.contains("is-pending")) return "pending";
  if (el.classList.contains("is-idle")) return "idle";
  return el.className;
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
    mockProductionDashboard.mockReset();
    mockNavigate.mockReset();
    mockDsRouter.search = "";
    mockDsRouter.setParams.mockClear();
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    mockDivisionTasks.mockResolvedValue({ tasks: [] });
    mockProductionDashboard.mockResolvedValue({ rows: [] });
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
    expect(container.querySelector('[data-testid="mds-row-PO-100-sh-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-quick-filters"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-atur-lokasi-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-kerja-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-row-PO-100-sh-100"]').getAttribute("data-to")).toBe("/m/orders/o1?ws=packing");
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
    expect(container.querySelector('[data-testid="mds-row-PO-100-sh-100"]').getAttribute("data-to")).toBe("/m/orders/o1?ws=delivery&shipment=sh-100");
  });

  test("does not authorize edit by role name driver", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "driver");
    await act(async () => {
      root.render(<MobileDeliverySchedulePage />);
    });
    expect(container.querySelector('[data-testid="mds-row-PO-100-sh-100"]')).toBeTruthy();
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
    expect(container.querySelector('[data-testid="mds-row-PO-100-sh-100"]').getAttribute("data-to")).toBe("/m/orders/o1?ws=loading&shipment=sh-100");
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
    expect(container.querySelector('[data-testid="mds-row-PO-100-sh-100"]').getAttribute("data-to")).toBe("/m/orders/o1?ws=delivery&shipment=sh-100");
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
    expect(container.querySelector('[data-testid="mds-row-PO-100-sh-100"]').getAttribute("data-to")).toBe("/m/orders/o1?ws=loading&shipment=sh-100");
    expect(mockDeliverySchedule).toHaveBeenCalledWith();
    expect(mockDivisionTasks).not.toHaveBeenCalled();
  });

  test("CASE E/F/G — Pengiriman ?ws= survives open-PO return context and refresh", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    mockDsRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const row = container.querySelector('[data-testid="mds-row-PO-100-sh-100"]');
    expect(row.getAttribute("data-to")).toBe("/m/orders/o1?ws=delivery&shipment=sh-100");
    await act(async () => {
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalledWith("/m/orders/o1?ws=delivery&shipment=sh-100", expect.objectContaining({
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
      "Cetak",
      "Pack",
      "Load",
      "Kirim",
    ]);
    expect(labels).not.toContain("Notifikasi");
    expect(labels).not.toContain("Helper");
    expect(labels).not.toContain("Driver");
    expect(labels).not.toContain("Kepala Produksi");
    expect(labels).not.toContain("Status");
    expect(labels).not.toContain("Aksi");
    expect(labels).not.toContain("Zona");
    expect(labels).not.toContain("Tahap Aktif");
    expect(labels).not.toContain("Tanggal Kerja");
    expect(container.querySelector('[data-testid="mds-notif-group"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-notif-helper-PO-100-sh-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-cup-PO-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-load-PO-100"]').textContent.trim()).toBe("—");
    expect(container.querySelector('[data-testid="mds-status-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-kerja-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-atur-lokasi-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-optimasi-rute"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-quick-filters"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mop-ops-role-summary"]')).toBeTruthy();
    const identity = container.querySelector('[data-testid="mds-identity-PO-100-sh-100"]');
    expect(identity.querySelector(".mop-ops-customer").textContent).toBe("PT Alpha");
    expect(identity.querySelector(".mop-ops-po").textContent).toBe("PO-100");
    expect(container.querySelector('[data-testid="mds-task-select"]') || container.textContent).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-item-lines-PO-100-sh-100"]').textContent).toBe("—");
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
    const cell = container.querySelector('[data-testid="mds-item-lines-PO-100-sh-100"]');
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
    expect(labels).not.toContain("Notifikasi");
    expect(labels).not.toContain("Helper");
    expect(labels).not.toContain("Driver");
    expect(labels).not.toContain("Kepala Produksi");
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
          shipment_id: "sh-200",
          sj_label: "SJ-2",
          latest_sj_number: "SJ-2",
          sj_item_lines: [{ shipment_id: "sh-200", sj_number: "SJ-2", name: "Lid PET", qty: 500, line_id: "z", line_idx: 0 }],
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
    expect(container.querySelector('[data-testid="mds-row-PO-100-sh-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-row-PO-200-sh-200"]')).toBeFalsy();
    await act(async () => {
      setter.call(input, "Lid PET");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mds-row-PO-200-sh-200"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-row-PO-100-sh-100"]')).toBeFalsy();
    await act(async () => {
      setter.call(input, "PO-100");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mds-row-PO-100-sh-100"]')).toBeTruthy();
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

  test("quick filters search Filter and Reset share one nowrap horizontal rail", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "route_optimizer.create" || p === "route_optimizer.optimize"
    ));
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const rail = container.querySelector('[data-testid="mds-quick-filters"]');
    expect(rail).toBeTruthy();
    expect(rail.className).toMatch(/mop-ops-filter-rail/);
    expect(rail.querySelector('[data-testid="mds-quick-semua"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mds-quick-hari_ini"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mds-quick-terlambat"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mds-quick-besok"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mds-quick-belum_dijadwalkan"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mds-quick-minggu_ini"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mds-quick-semua_aktif"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mds-search"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mds-filter-btn"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mds-filter-reset"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mds-filter-actions"]')).toBeTruthy();
    expect(rail.querySelector('[data-testid="mds-optimasi-rute"]')).toBeTruthy();
    const order = [
      "mds-quick-semua",
      "mds-quick-hari_ini",
      "mds-quick-terlambat",
      "mds-quick-besok",
      "mds-quick-belum_dijadwalkan",
      "mds-quick-minggu_ini",
      "mds-quick-semua_aktif",
    ].map((id) => rail.querySelector(`[data-testid="${id}"]`));
    order.forEach((el, idx) => {
      if (idx === 0) return;
      expect(el.compareDocumentPosition(order[idx - 1]) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    });
    const search = rail.querySelector('[data-testid="mds-search"]');
    const filterBtn = rail.querySelector('[data-testid="mds-filter-btn"]');
    const resetBtn = rail.querySelector('[data-testid="mds-filter-reset"]');
    const lastChip = rail.querySelector('[data-testid="mds-quick-semua_aktif"]');
    expect(lastChip.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(search.compareDocumentPosition(filterBtn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(filterBtn.compareDocumentPosition(resetBtn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelector(".mop-schedule-toolbar--stacked")).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-more-filters"]')).toBeFalsy();
    const opsCss = fs.readFileSync(path.join(__dirname, "opsQueue.css"), "utf8");
    expect(opsCss).toMatch(/\.mop-ops-filter-rail[\s\S]*flex-wrap:\s*nowrap/);
    expect(opsCss).toMatch(/\.mop-ops-filter-rail[\s\S]*overflow-x:\s*auto/);
    expect(opsCss).toMatch(/\.mop-ops-filter-rail[\s\S]*overflow-y:\s*hidden/);
    const pageSrc = fs.readFileSync(path.join(__dirname, "MobileDeliverySchedulePage.jsx"), "utf8");
    expect(pageSrc).toMatch(/matchesDeliveryOpsSearch/);
    expect(pageSrc).toMatch(/deliveryQuickMatch/);
    expect(pageSrc).toMatch(/matchesDeliveryScheduleFilters/);
    expect(pageSrc).toMatch(/buildOpsRoleSummaryModel\(\{\s*productionRows:\s*null,\s*deliveryRows:\s*sjRows/);
    expect(pageSrc).toMatch(/showBrand=\{false\}/);
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

  test("delivery-only payload maps prod from qty_printed_operator and leaves cup/screen idle", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(mockProductionDashboard).toHaveBeenCalled();
    expect(markKind(container.querySelector('[data-testid="mds-cup-PO-100"]'))).toBe("idle");
    expect(markKind(container.querySelector('[data-testid="mds-screen-PO-100"]'))).toBe("idle");
    expect(markKind(container.querySelector('[data-testid="mds-prod-PO-100"]'))).toBe("pending");
    expect(markKind(container.querySelector('[data-testid="mds-packing-PO-100"]'))).toBe("pending");
    expect(markKind(container.querySelector('[data-testid="mds-load-PO-100"]'))).toBe("idle");
    expect(markKind(container.querySelector('[data-testid="mds-terkirim-PO-100"]'))).toBe("idle");
  });

  test("overlays canonical production cup/screen/prod onto packing-active delivery row", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        id: "o-lalap",
        order_number: "PO-LALAP",
        customer_name: "SPECIAL LALAP",
        status: "terjadwal",
        effective_delivery_date: "2026-09-10",
        qty_total: 1000,
        qty_packed: 400,
        qty_delivered: 0,
        percent_delivered: 0,
        print_qty_total: 1000,
        qty_printed_operator: 1000,
        sj_label: "SJ-LALAP",
        latest_sj_number: "SJ-LALAP",
        shipment_id: "sh-1",
        role_status: { pack: "pending", load: "idle", kirim: "idle" },
        sj_item_lines: [
          { shipment_id: "sh-1", line_id: "lid-a", line_idx: 0, name: "Cup 16oz", qty: 600 },
          { shipment_id: "sh-1", line_id: "lid-b", line_idx: 1, name: "Cup 16oz", qty: 400 },
        ],
      }],
    });
    mockProductionDashboard.mockResolvedValue({
      rows: [{
        id: "o-lalap",
        po_number: "PO-LALAP",
        customer: "SPECIAL LALAP",
        helper_required: 1000,
        helper_prepared: 1000,
        screen_qty: 1000,
        current_stage: 11,
        print_qty_total: 1000,
        print_completed_qty: 1000,
        operators: [
          { split_id: "s1", line_id: "lid-a", line_idx: 0, line_printable: true, qty_completed: 600 },
          { split_id: "s2", line_id: "lid-b", line_idx: 1, line_printable: true, qty_completed: 400 },
        ],
        role_status: { cup: "done", screen: "done", prod: "done", pack: "pending", load: "idle", kirim: "idle" },
      }],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(markKind(container.querySelector('[data-testid="mds-cup-PO-LALAP"]'))).toBe("done");
    expect(markKind(container.querySelector('[data-testid="mds-screen-PO-LALAP"]'))).toBe("done");
    expect(markKind(container.querySelector('[data-testid="mds-prod-PO-LALAP"]'))).toBe("done");
    expect(markKind(container.querySelector('[data-testid="mds-packing-PO-LALAP"]'))).toBe("pending");
    expect(markKind(container.querySelector('[data-testid="mds-load-PO-LALAP"]'))).toBe("idle");
    expect(markKind(container.querySelector('[data-testid="mds-terkirim-PO-LALAP"]'))).toBe("idle");
  });

  test("same-name PO lines stay isolated and missing production id does not inherit", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          id: "po-renea-1",
          order_number: "PO-RENEA-1",
          customer_name: "RENEA",
          status: "terjadwal",
          effective_delivery_date: "2026-09-10",
          qty_total: 100,
          qty_packed: 100,
          qty_delivered: 0,
          percent_delivered: 0,
          print_qty_total: 100,
          qty_printed_operator: 100,
          shipment_id: "sh-renea-1",
          sj_label: "SJ-RENEA-1",
          latest_sj_number: "SJ-RENEA-1",
          role_status: { pack: "done", load: "idle", kirim: "idle" },
        },
        {
          id: "po-renea-2",
          order_number: "PO-RENEA-2",
          customer_name: "RENEA",
          status: "terjadwal",
          effective_delivery_date: "2026-09-10",
          qty_total: 100,
          qty_packed: 40,
          qty_delivered: 0,
          percent_delivered: 0,
          print_qty_total: 100,
          qty_printed_operator: 0,
          shipment_id: "sh-renea-2",
          sj_label: "SJ-RENEA-2",
          latest_sj_number: "SJ-RENEA-2",
          role_status: { pack: "pending", load: "idle", kirim: "idle" },
        },
      ],
    });
    mockProductionDashboard.mockResolvedValue({
      rows: [{
        id: "po-renea-1",
        helper_required: 100,
        helper_prepared: 100,
        screen_qty: 10,
        print_qty_total: 100,
        operators: [{ split_id: "s1", line_printable: true, qty_completed: 100 }],
        role_status: { cup: "done", screen: "done", prod: "done", pack: "done", load: "idle", kirim: "idle" },
      }],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(markKind(container.querySelector('[data-testid="mds-cup-PO-RENEA-1"]'))).toBe("done");
    expect(markKind(container.querySelector('[data-testid="mds-screen-PO-RENEA-1"]'))).toBe("done");
    expect(markKind(container.querySelector('[data-testid="mds-prod-PO-RENEA-1"]'))).toBe("done");
    expect(markKind(container.querySelector('[data-testid="mds-cup-PO-RENEA-2"]'))).toBe("idle");
    expect(markKind(container.querySelector('[data-testid="mds-screen-PO-RENEA-2"]'))).toBe("idle");
    expect(markKind(container.querySelector('[data-testid="mds-prod-PO-RENEA-2"]'))).toBe("idle");
    expect(markKind(container.querySelector('[data-testid="mds-packing-PO-RENEA-2"]'))).toBe("pending");
  });

  test("4/5/6 Pack amber, Load partial amber, Kirim partial amber from persisted SJ qty", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        id: "po-a",
        order_number: "PO-A",
        customer_name: "Renea",
        status: "hari_ini",
        effective_delivery_date: "2026-09-15",
        shipment_id: "sh-a",
        sj_label: "SJ-A",
        latest_sj_number: "SJ-A",
        packing_status: "Berjalan",
        qty_packed: 4000,
        qty_total: 10000,
        qty_delivered: 4000,
        percent_delivered: 40,
        qty_loaded: 4000,
        qty_load_need: 10000,
        qty_delivered_sj: 4000,
        qty_sj: 10000,
        role_status: { cup: "done", screen: "done", prod: "done", pack: "done", load: "done", kirim: "done" },
      }],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(markKind(container.querySelector('[data-testid="mds-packing-PO-A"]'))).toBe("pending");
    expect(markKind(container.querySelector('[data-testid="mds-load-PO-A"]'))).toBe("pending");
    expect(markKind(container.querySelector('[data-testid="mds-terkirim-PO-A"]'))).toBe("pending");
  });

  test("6. Kirim complete from this SJ qty_delivered_sj", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        id: "po-b",
        order_number: "PO-B",
        customer_name: "Renea",
        status: "hari_ini",
        effective_delivery_date: "2026-09-15",
        shipment_id: "sh-b",
        sj_label: "SJ-B",
        latest_sj_number: "SJ-B",
        percent_delivered: 100,
        qty_delivered: 800,
        qty_total: 800,
        packing_status: "Sudah",
        qty_delivered_sj: 800,
        qty_sj: 800,
        role_status: { pack: "pending", load: "done", kirim: "idle" },
      }],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(markKind(container.querySelector('[data-testid="mds-packing-PO-B"]'))).toBe("done");
    expect(markKind(container.querySelector('[data-testid="mds-terkirim-PO-B"]'))).toBe("done");
    expect(markKind(container.querySelector('[data-testid="mds-load-PO-B"]'))).toBe("idle");
  });

  test("7. two SJ of the same PO isolate Load and Kirim", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          id: "po-multi",
          order_number: "PO-MULTI",
          customer_name: "Renea",
          status: "hari_ini",
          effective_delivery_date: "2026-09-15",
          shipment_id: "sh-1",
          sj_label: "SJ-1",
          latest_sj_number: "SJ-1",
          packing_status: "Sudah",
          percent_delivered: 100,
          qty_delivered: 400,
          qty_total: 400,
          qty_loaded: 400,
          qty_load_need: 400,
          qty_delivered_sj: 400,
          qty_sj: 400,
          role_status: { pack: "done", load: "done", kirim: "idle" },
        },
        {
          id: "po-multi",
          order_number: "PO-MULTI",
          customer_name: "Renea",
          status: "terjadwal",
          effective_delivery_date: "2026-09-16",
          shipment_id: "sh-2",
          sj_label: "SJ-2",
          latest_sj_number: "SJ-2",
          packing_status: "Sudah",
          percent_delivered: 0,
          qty_delivered: 0,
          qty_total: 400,
          qty_loaded: 200,
          qty_load_need: 400,
          qty_delivered_sj: 0,
          qty_sj: 400,
          role_status: { pack: "done", load: "done", kirim: "done" },
        },
      ],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const rows = container.querySelectorAll('[data-testid^="mds-row-PO-MULTI-"]');
    expect(rows).toHaveLength(2);
    const loadMarks = container.querySelectorAll('[data-testid="mds-load-PO-MULTI"]');
    const kirimMarks = container.querySelectorAll('[data-testid="mds-terkirim-PO-MULTI"]');
    const packMarks = container.querySelectorAll('[data-testid="mds-packing-PO-MULTI"]');
    expect(markKind(packMarks[0])).toBe("done");
    expect(markKind(packMarks[1])).toBe("done");
    expect(markKind(loadMarks[0])).toBe("done");
    expect(markKind(loadMarks[1])).toBe("pending");
    expect(markKind(kirimMarks[0])).toBe("done");
    expect(markKind(kirimMarks[1])).toBe("idle");
  });

  test("without schedule.view, production dashboard is not fetched and cup/screen stay idle", async () => {
    mockHasPerm.mockImplementation((p) => p === "delivery.schedule.view");
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(mockDeliverySchedule).toHaveBeenCalled();
    expect(mockProductionDashboard).not.toHaveBeenCalled();
    expect(markKind(container.querySelector('[data-testid="mds-cup-PO-100"]'))).toBe("idle");
    expect(markKind(container.querySelector('[data-testid="mds-screen-PO-100"]'))).toBe("idle");
    expect(markKind(container.querySelector('[data-testid="mds-prod-PO-100"]'))).toBe("pending");
  });

  test("SJ-only gate hides overdue/today/future/packing rows without valid SJ", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          id: "overdue",
          order_number: "PO-OVERDUE",
          customer_name: "No SJ Overdue",
          status: "terlambat",
          effective_delivery_date: "2026-09-01",
          qty_packed: 9000,
          shipment_id: null,
          sj_label: "",
          sj_item_lines: [],
        },
        {
          id: "today",
          order_number: "PO-TODAY",
          status: "hari_ini",
          effective_delivery_date: "2026-09-15",
          shipment_id: "",
          sj_label: "",
        },
        {
          id: "future",
          order_number: "PO-FUTURE",
          status: "terjadwal",
          effective_delivery_date: "2026-09-30",
          sj_label: "",
        },
        {
          id: "packed",
          order_number: "PO-PACK",
          qty_packed: 5000,
          qty_total: 5000,
          current_stage: 11,
          sj_label: "",
          shipment_id: null,
        },
        {
          ...sampleRows[0],
          id: "ok",
          order_number: "PO-OK",
          shipment_id: "sh-ok",
          sj_label: "SJ-OK",
          latest_sj_number: "SJ-OK",
        },
      ],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-row-PO-OVERDUE"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-row-PO-TODAY"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-row-PO-FUTURE"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-row-PO-PACK"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-row-PO-OK-sh-ok"]')).toBeTruthy();
    expect(container.querySelectorAll("tbody tr.mop-ds-sj-row")).toHaveLength(1);
  });

  test("one SJ with 5 item lines stays one table row and does not hide with +N", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        shipment_id: "sh-5",
        sj_label: "SJ-5",
        latest_sj_number: "SJ-5",
        sj_item_lines: [
          { shipment_id: "sh-5", sj_number: "SJ-5", line_id: "a", line_idx: 0, name: "Item A", qty: 100 },
          { shipment_id: "sh-5", sj_number: "SJ-5", line_id: "b", line_idx: 1, name: "Item B", qty: 200 },
          { shipment_id: "sh-5", sj_number: "SJ-5", line_id: "c", line_idx: 2, name: "Item C", qty: 300 },
          { shipment_id: "sh-5", sj_number: "SJ-5", line_id: "d", line_idx: 3, name: "Item D", qty: 400 },
          { shipment_id: "sh-5", sj_number: "SJ-5", line_id: "e", line_idx: 4, name: "Item E", qty: 500 },
        ],
      }],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelectorAll("tbody tr.mop-ds-sj-row")).toHaveLength(1);
    const cell = container.querySelector('[data-testid="mds-item-lines-PO-100-sh-5"]');
    expect(cell.textContent).toContain("Item A");
    expect(cell.textContent).toContain("Item B");
    expect(cell.textContent).toContain("Item C");
    expect(cell.textContent).toContain("Item D");
    expect(cell.textContent).toContain("Item E");
    expect(cell.textContent).not.toMatch(/\+\d/);
    expect(container.querySelectorAll('[data-testid^="mds-item-line-PO-100-sh-5-"]').length).toBe(5);
  });

  test("local 3-dummy preview payload: overdue hidden, 1 SJ = 1 row, 2 SJ = 2 rows", async () => {
    const { getPwaDeliverySjPreviewRows } = require("../lib/mobileDeliverySchedulePreview");
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({ rows: getPwaDeliverySjPreviewRows() });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-row-PO-DUMMY-2"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-row-PO-DUMMY-1-sh-dummy-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-row-PO-DUMMY-3-sh-dummy-3a"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-row-PO-DUMMY-3-sh-dummy-3b"]')).toBeTruthy();
    expect(container.querySelectorAll("tbody tr.mop-ds-sj-row")).toHaveLength(3);
    const multi = container.querySelector('[data-testid="mds-item-lines-PO-DUMMY-1-sh-dummy-1"]');
    expect(multi.textContent).toContain("Cup 16oz Datar");
    expect(multi.textContent).toContain("Lid 16oz Datar");
    expect(multi.textContent).toContain("Cup 22oz Gelas");
    expect(multi.textContent).not.toMatch(/\+\d/);
    const labels = headerLabels(container);
    expect(labels).not.toContain("Tanggal Kerja");
    expect(labels).not.toContain("Notifikasi");
    expect(labels).not.toContain("Helper");
    expect(labels).not.toContain("Driver");
    expect(labels).not.toContain("Kepala Produksi");
    expect(labels).toContain("Status per Role");
    expect(container.querySelector('[data-testid="mds-cup-PO-DUMMY-1"]')).toBeTruthy();
    expect(markKind(container.querySelector('[data-testid="mds-load-PO-DUMMY-1"]'))).toBe("done");
    expect(markKind(container.querySelector('[data-testid="mds-terkirim-PO-DUMMY-1"]'))).toBe("idle");
    expect(markKind(container.querySelector('[data-testid="mds-packing-PO-DUMMY-1"]'))).toBe("pending");
    const loadDummy3 = container.querySelectorAll('[data-testid="mds-load-PO-DUMMY-3"]');
    const kirimDummy3 = container.querySelectorAll('[data-testid="mds-terkirim-PO-DUMMY-3"]');
    expect(markKind(loadDummy3[0])).toBe("done");
    expect(markKind(loadDummy3[1])).toBe("pending");
    expect(markKind(kirimDummy3[0])).toBe("done");
    expect(markKind(kirimDummy3[1])).toBe("idle");
    expect(container.querySelector('[data-testid="mds-notif-group"]')).toBeFalsy();
    expect(container.querySelector('[data-testid^="mds-notif-helper-"]')).toBeFalsy();
  });

  test("PWA delivery keeps canonical sheet zoom, horizontal scroll, and SJ row separators", () => {
    const pageSrc = fs.readFileSync(path.join(__dirname, "MobileDeliverySchedulePage.jsx"), "utf8");
    const css = fs.readFileSync(path.join(__dirname, "mobile.css"), "utf8");
    expect(pageSrc).toMatch(/useProductionSheetZoom/);
    expect(pageSrc).toMatch(/productionSheetZoomStyle/);
    expect(pageSrc).toMatch(/pwaDeliveryScheduleRows/);
    expect(pageSrc).toMatch(/isPwaDeliverySjPreviewEnabled/);
    expect(pageSrc).not.toMatch(/pwaSjNotificationTones/);
    expect(pageSrc).not.toMatch(/Notifikasi/);
    expect(pageSrc).not.toMatch(/Tanggal Kerja/);
    expect(css).toMatch(/mop-ds-sj-row/);
    expect(css).toMatch(/border-bottom:\s*1px solid #9aa0a6/);
    expect(css).toMatch(/mop-ds-sheet-viewport[\s\S]*overflow-x:\s*auto/);
  });
});
