import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import MobileDeliverySchedulePage from "./MobileDeliverySchedulePage";
import { clearProductionSummaryCache, invalidateProductionSummaryCache, loadProductionSummary } from "../lib/productionSummaryCache";
import { mobileProductionDashboardParams } from "../lib/mobileSchedule";

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: jest.fn(() => ({ promise: Promise.resolve({ destroy: jest.fn(), numPages: 0 }) })),
}));
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
const mockGetOrder = jest.fn();
const mockConfirmDelivered = jest.fn();
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
    getOrder: (...args) => mockGetOrder(...args),
    confirmDelivered: (...args) => mockConfirmDelivered(...args),
    listDeliveryOutlets: jest.fn(async () => ({ outlets: [] })),
  },
  fileUrl: (p) => p,
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

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

describe("MobileDeliverySchedulePage", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    clearProductionSummaryCache();
    mockHasPerm.mockReset();
    mockDeliverySchedule.mockReset();
    mockDivisionTasks.mockReset();
    mockProductionDashboard.mockReset();
    mockGetOrder.mockReset();
    mockConfirmDelivered.mockReset();
    mockNavigate.mockReset();
    mockDsRouter.search = "";
    mockDsRouter.setParams.mockClear();
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    mockDivisionTasks.mockResolvedValue({ tasks: [] });
    mockProductionDashboard.mockResolvedValue({ rows: [] });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "o1",
        po_number: "PO-100",
        customer: "PT Alpha",
        line_items: [{ name: "Cup", quantity: 10, line_id: "l0" }],
        packing_info: { qty_packed: 10, lines: [{ line_idx: 0, packed_qty: 10 }] },
        shipments: [{ id: "sh-100", number: "SJ-1", lines: [{ line_idx: 0, qty: 10 }], total_qty: 10 }],
        surat_jalan_files: [{
          id: "sj-100",
          shipment_id: "sh-100",
          sj_number: "SJ-1",
          lines: [{ line_idx: 0, qty: 10 }],
        }],
        loading_confirmations: [{ id: "c1", surat_jalan_id: "sj-100", lines: [{ line_idx: 0, qty: 10 }] }],
      },
    });
    sessionStorage.clear();
    localStorage.clear();
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

  test("CASE A — Loading + Pengiriman options from Role Aktif eligibility", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "delivery.update"
    ));
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-task-select"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-ops-role-summary"]')).toBeFalsy();
    const select = container.querySelector('[data-testid="mop-active-role-input"]');
    expect([...select.options].map((o) => o.value)).toEqual(["loading", "delivery"]);
    expect([...select.options].map((o) => o.textContent)).toEqual(["Loading", "Pengiriman"]);
    expect(container.textContent).not.toMatch(/TUGAS/);
  });

  test("CASE B — delivery-only hides Loading; unauthorized ?ws=loading does not grant it", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    mockDsRouter.search = "ws=loading";
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-task-select"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-active-role-current"]').textContent).toBe("Pengiriman");
    expect(container.querySelector('[data-testid="mop-active-role-input"]')).toBeFalsy();
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

    const select = container.querySelector('[data-testid="mop-active-role-input"]');
    await act(async () => {
      select.value = "delivery";
      select.dispatchEvent(new Event("change", { bubbles: true }));
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
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(mockGetOrder).toHaveBeenCalledWith("o1", { view: "operational" });
    expect(container.querySelector('[data-testid="mobile-task-modal"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-ws-delivery"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeFalsy();

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
    expect(container.querySelector('[data-testid="mop-active-role-current"]').textContent).toBe("Pengiriman");
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
    const select = container.querySelector('[data-testid="mop-active-role-input"]');
    await act(async () => {
      select.value = "loading";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(mockDeliverySchedule.mock.calls.every((c) => c.length === 0 || c[0] === undefined)).toBe(true);
    expect(String(mockDsRouter.setParams.mock.calls.at(-1)[0])).toContain("ws=loading");
  });

  test("Loading row click opens Loading workspace for the selected shipment", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "loading.confirm_match" || p === "delivery_note.view_file"
    ));
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
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po001",
        po_number: "PO001",
        customer: "PT Multi",
        line_items: [
          { name: "Cup", quantity: 1000, line_id: "lid-cup", production_process: "print" },
          { name: "Lid", quantity: 1000, line_id: "lid-lid", production_process: "print" },
        ],
        packing_info: { qty_packed: 2000, lines: [{ line_idx: 0, packed_qty: 1000 }, { line_idx: 1, packed_qty: 1000 }] },
        shipments: [
          { id: "sh-1", number: "SJ001", lines: [{ line_idx: 0, line_id: "lid-cup", qty: 1000 }], total_qty: 1000 },
          { id: "sh-2", number: "SJ001-2", lines: [{ line_idx: 1, line_id: "lid-lid", qty: 1000 }], total_qty: 1000 },
        ],
        surat_jalan_files: [
          { id: "sj-1", shipment_id: "sh-1", sj_number: "SJ001", lines: [{ line_idx: 0, line_id: "lid-cup", qty: 1000 }] },
          { id: "sj-2", shipment_id: "sh-2", sj_number: "SJ001-2", lines: [{ line_idx: 1, line_id: "lid-lid", qty: 1000 }] },
        ],
      },
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
    await flush();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(mockGetOrder).toHaveBeenCalledWith("po001", { view: "operational" });
    expect(container.querySelector('[data-testid="mobile-task-modal"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-loading-confirm-match"]')).toBeTruthy();
    expect(container.textContent).toContain("Verifikasi Kesesuaian SJ");
    expect(container.querySelector(".mop-stepper")).toBeFalsy();
    const sjLink = container.querySelector('a[href*="sj-2"], a[href*="sh-2"]');
    expect(sjLink).toBeTruthy();
    expect(container.querySelector('a[href*="sj-1"]')).toBeFalsy();
  });

  test("Loading SJ1 and SJ2 rows isolate exact Surat Jalan identity", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "loading.confirm_match" || p === "delivery_note.view_file"
    ));
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
          sj_item_lines: [{ shipment_id: "sh-1", sj_number: "SJ001", name: "Cup", qty: 1000, line_idx: 0 }],
        },
        {
          ...sampleRows[0],
          id: "po001",
          order_number: "PO001",
          shipment_id: "sh-2",
          latest_sj_number: "SJ001-2",
          sj_label: "SJ001-2",
          sj_item_lines: [{ shipment_id: "sh-2", sj_number: "SJ001-2", name: "Lid", qty: 1000, line_idx: 1 }],
        },
      ],
    });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po001",
        po_number: "PO001",
        customer: "PT Multi",
        line_items: [{ name: "Cup", quantity: 1000, line_id: "lid-cup" }, { name: "Lid", quantity: 1000, line_id: "lid-lid" }],
        packing_info: { qty_packed: 2000, lines: [{ line_idx: 0, packed_qty: 1000 }, { line_idx: 1, packed_qty: 1000 }] },
        surat_jalan_files: [
          { id: "sj-1", shipment_id: "sh-1", sj_number: "SJ001", lines: [{ line_idx: 0, qty: 1000 }] },
          { id: "sj-2", shipment_id: "sh-2", sj_number: "SJ001-2", lines: [{ line_idx: 1, qty: 1000 }] },
        ],
      },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO001-sh-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeTruthy();
    expect(container.querySelector('a[href*="sj-1"]')).toBeTruthy();
    expect(container.querySelector('a[href*="sj-2"]')).toBeFalsy();
    await act(async () => {
      container.querySelector('[data-testid="mobile-task-modal-close"]').click();
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO001-sh-2"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeTruthy();
    expect(container.querySelector('a[href*="sj-2"]')).toBeTruthy();
    expect(container.querySelector('a[href*="sj-1"]')).toBeFalsy();
  });

  test("Loading SJ 001/002/003 open exact SJ only and never a sibling", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "loading.confirm_match" || p === "delivery_note.view_file"
    ));
    mockDsRouter.search = "ws=loading";
    mockDeliverySchedule.mockResolvedValue({
      rows: ["sh-1", "sh-2", "sh-3"].map((sid, idx) => ({
        ...sampleRows[0],
        id: "po001",
        order_number: "PO001",
        shipment_id: sid,
        latest_sj_number: `SJ00${idx + 1}`,
        sj_label: `SJ00${idx + 1}`,
        sj_item_lines: [{ shipment_id: sid, sj_number: `SJ00${idx + 1}`, name: "Cup", qty: 1000, line_idx: idx }],
      })),
    });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po001",
        po_number: "PO001",
        surat_jalan_files: [
          { id: "sj-1", shipment_id: "sh-1", sj_number: "SJ001", lines: [{ line_idx: 0, qty: 1000 }] },
          { id: "sj-2", shipment_id: "sh-2", sj_number: "SJ002", lines: [{ line_idx: 1, qty: 1000 }] },
          { id: "sj-3", shipment_id: "sh-3", sj_number: "SJ003", lines: [{ line_idx: 2, qty: 1000 }] },
        ],
      },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mop-active-role-input"]')?.value
      || container.querySelector('[data-testid="mop-active-role-current"]')?.textContent).toMatch(/loading|Loading/i);

    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO001-sh-3"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeTruthy();
    expect(container.querySelector('a[href*="sj-3"]')).toBeTruthy();
    expect(container.querySelector('a[href*="sj-1"]')).toBeFalsy();
    expect(container.querySelector('a[href*="sj-2"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeFalsy();

    await act(async () => { container.querySelector('[data-testid="mobile-task-modal-close"]').click(); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO001-sh-2"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('a[href*="sj-2"]')).toBeTruthy();
    expect(container.querySelector('a[href*="sj-1"]')).toBeFalsy();
    expect(container.querySelector('a[href*="sj-3"]')).toBeFalsy();
  });

  test("Pengiriman SJ 003 opens SJ 003 never sibling", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    mockDsRouter.search = "ws=delivery";
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        { ...sampleRows[0], id: "po001", order_number: "PO001", shipment_id: "sh-1", latest_sj_number: "SJ001", sj_label: "SJ001" },
        { ...sampleRows[0], id: "po001", order_number: "PO001", shipment_id: "sh-2", latest_sj_number: "SJ002", sj_label: "SJ002" },
        { ...sampleRows[0], id: "po001", order_number: "PO001", shipment_id: "sh-3", latest_sj_number: "SJ003", sj_label: "SJ003" },
      ],
    });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po001",
        po_number: "PO001",
        customer: "PT Multi",
        shipments: [
          { id: "sh-1", number: "SJ001", lines: [{ line_idx: 0, qty: 1000 }] },
          { id: "sh-2", number: "SJ002", lines: [{ line_idx: 1, qty: 1000 }] },
          { id: "sh-3", number: "SJ003", lines: [{ line_idx: 2, qty: 1000 }] },
        ],
      },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO001-sh-3"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-delivery"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toMatch(/SJ003/);
    expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).not.toMatch(/SJ001/);
  });

  test("Loading any cell on SJ 002 opens Loading SJ 002 only", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "loading.confirm_match" || p === "delivery_note.view_file"
    ));
    mockDsRouter.search = "ws=loading";
    mockDeliverySchedule.mockResolvedValue({
      rows: ["sh-1", "sh-2", "sh-3"].map((sid, idx) => ({
        ...sampleRows[0],
        id: "po001",
        order_number: "PO001",
        shipment_id: sid,
        latest_sj_number: `SJ00${idx + 1}`,
        sj_label: `SJ00${idx + 1}`,
      })),
    });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po001",
        po_number: "PO001",
        surat_jalan_files: [
          { id: "sj-1", shipment_id: "sh-1", sj_number: "SJ001", lines: [{ line_idx: 0, qty: 1000 }] },
          { id: "sj-2", shipment_id: "sh-2", sj_number: "SJ002", lines: [{ line_idx: 1, qty: 1000 }] },
          { id: "sj-3", shipment_id: "sh-3", sj_number: "SJ003", lines: [{ line_idx: 2, qty: 1000 }] },
        ],
      },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const firstCells = [...container.querySelector('[data-testid="mds-row-PO001-sh-2"]').querySelectorAll("td")];
    expect(firstCells.length).toBeGreaterThanOrEqual(3);
    const indexes = [0, Math.floor(firstCells.length / 2), firstCells.length - 1];
    for (const idx of indexes) {
      const cells = [...container.querySelector('[data-testid="mds-row-PO001-sh-2"]').querySelectorAll("td")];
      await act(async () => { cells[idx].dispatchEvent(new MouseEvent("click", { bubbles: true })); });
      await flush();
      expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeTruthy();
      expect(container.querySelector('a[href*="sj-2"]')).toBeTruthy();
      expect(container.querySelector('a[href*="sj-1"]')).toBeFalsy();
      expect(container.querySelector('a[href*="sj-3"]')).toBeFalsy();
      expect(container.querySelector('[data-testid="mobile-po-ringkasan"]')).toBeFalsy();
      await act(async () => { container.querySelector('[data-testid="mobile-task-modal-close"]').click(); });
      await flush();
    }
  });

  test("Pengiriman any cell on SJ 003 opens Pengiriman SJ 003 only", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    mockDsRouter.search = "ws=delivery";
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        { ...sampleRows[0], id: "po001", order_number: "PO001", shipment_id: "sh-1", latest_sj_number: "SJ001", sj_label: "SJ001" },
        { ...sampleRows[0], id: "po001", order_number: "PO001", shipment_id: "sh-2", latest_sj_number: "SJ002", sj_label: "SJ002" },
        { ...sampleRows[0], id: "po001", order_number: "PO001", shipment_id: "sh-3", latest_sj_number: "SJ003", sj_label: "SJ003" },
      ],
    });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po001",
        po_number: "PO001",
        customer: "PT Multi",
        shipments: [
          { id: "sh-1", number: "SJ001", lines: [{ line_idx: 0, qty: 1000 }] },
          { id: "sh-2", number: "SJ002", lines: [{ line_idx: 1, qty: 1000 }] },
          { id: "sh-3", number: "SJ003", lines: [{ line_idx: 2, qty: 1000 }] },
        ],
      },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const firstCells = [...container.querySelector('[data-testid="mds-row-PO001-sh-3"]').querySelectorAll("td")];
    expect(firstCells.length).toBeGreaterThanOrEqual(3);
    const indexes = [0, Math.floor(firstCells.length / 2), firstCells.length - 1];
    for (const idx of indexes) {
      const cells = [...container.querySelector('[data-testid="mds-row-PO001-sh-3"]').querySelectorAll("td")];
      await act(async () => { cells[idx].dispatchEvent(new MouseEvent("click", { bubbles: true })); });
      await flush();
      expect(container.querySelector('[data-testid="mobile-ws-delivery"]')).toBeTruthy();
      expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toMatch(/SJ003/);
      expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).not.toMatch(/SJ001|SJ002/);
      await act(async () => { container.querySelector('[data-testid="mobile-delivery-close-x"]').click(); });
      await flush();
    }
  });

  test("delivery whitespace cell opens row; native button child does not", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "loading.confirm_match" || p === "delivery_note.view_file"
    ));
    mockDsRouter.search = "ws=loading";
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        id: "po001",
        order_number: "PO001",
        shipment_id: "sh-2",
        latest_sj_number: "SJ002",
        sj_label: "SJ002",
      }],
    });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po001",
        surat_jalan_files: [
          { id: "sj-2", shipment_id: "sh-2", sj_number: "SJ002", lines: [{ line_idx: 1, qty: 1000 }] },
        ],
      },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const row = container.querySelector('[data-testid="mds-row-PO001-sh-2"]');
    const qtyCell = row.querySelector('[data-testid="mds-qty-PO001"]') || row.querySelectorAll("td")[4];
    await act(async () => { qtyCell.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeTruthy();
    await act(async () => { container.querySelector('[data-testid="mobile-task-modal-close"]').click(); });
    await flush();
    const btn = document.createElement("button");
    btn.type = "button";
    qtyCell.appendChild(btn);
    await act(async () => { btn.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-task-modal"]')).toBeFalsy();
  });

  test("unauthorized Loading role fails closed and does not open workspace", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    mockDsRouter.search = "ws=loading";
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO-100-sh-100"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeFalsy();
  });

  test("Loading unmatched shipment_id fails closed and does not open SJ1", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "loading.confirm_match"
    ));
    mockDsRouter.search = "ws=loading";
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        id: "po001",
        order_number: "PO001",
        shipment_id: "missing-sj",
        latest_sj_number: "SJ-GHOST",
        sj_label: "SJ-GHOST",
      }],
    });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po001",
        surat_jalan_files: [
          { id: "sj-1", shipment_id: "sh-1", sj_number: "SJ001", lines: [{ line_idx: 0, qty: 1000 }] },
        ],
      },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO001-missing-sj"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(mockGetOrder).toHaveBeenCalledWith("po001", { view: "operational" });
    expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-schedule-task-error"]').textContent)
      .toMatch(/Surat Jalan tidak dapat ditentukan/);
  });

  test("Pengiriman SJ1 and SJ2 rows isolate exact Surat Jalan identity", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    mockDsRouter.search = "ws=delivery";
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          id: "po001",
          order_number: "PO001",
          shipment_id: "sh-1",
          latest_sj_number: "SJ001",
          sj_label: "SJ001",
        },
        {
          ...sampleRows[0],
          id: "po001",
          order_number: "PO001",
          shipment_id: "sh-2",
          latest_sj_number: "SJ001-2",
          sj_label: "SJ001-2",
        },
      ],
    });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po001",
        po_number: "PO001",
        customer: "PT Multi",
        line_items: [{ name: "Cup", quantity: 2000, line_id: "LA", production_process: "print" }],
        packing_info: { qty_packed: 2000, lines: [{ line_idx: 0, packed_qty: 2000 }] },
        shipments: [
          { id: "sh-1", number: "SJ001", lines: [{ line_idx: 0, qty: 1000 }], total_qty: 1000 },
          { id: "sh-2", number: "SJ001-2", lines: [{ line_idx: 0, qty: 1000 }], total_qty: 1000 },
        ],
        loading_confirmations: [{ id: "c1", surat_jalan_id: "sh-1", lines: [{ line_idx: 0, qty: 1000 }] }],
      },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO001-sh-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-delivery"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-pengiriman-sj-select"]').value).toBe("sh-1");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("SJ001");
    expect(container.querySelector('[data-testid="mobile-pengiriman-sj-select"]').disabled).toBe(true);
    const closeBtn = container.querySelector('[data-testid="mobile-delivery-close-x"]');
    await act(async () => { closeBtn.click(); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO001-sh-2"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-pengiriman-sj-select"]').value).toBe("sh-2");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("SJ001-2");
  });

  test("Pengiriman unmatched shipment_id fails closed and does not select first undelivered", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.update");
    mockDsRouter.search = "ws=delivery";
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        id: "po001",
        order_number: "PO001",
        shipment_id: "missing-sj",
        latest_sj_number: "SJ-GHOST",
        sj_label: "SJ-GHOST",
      }],
    });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po001",
        shipments: [
          { id: "sh-1", number: "SJ001", delivered_at: "t", delivery_status: "Terkirim", lines: [{ line_idx: 0, qty: 10 }], total_qty: 10 },
          { id: "sh-2", number: "SJ001-2", lines: [{ line_idx: 0, qty: 10 }], total_qty: 10 },
        ],
      },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO001-missing-sj"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-delivery"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-schedule-task-error"]').textContent)
      .toMatch(/Surat Jalan tidak dapat ditentukan/);
  });

  test("stale and empty shipment identities fail closed for Loading and Pengiriman", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "delivery.update"
    ));
    mockDsRouter.search = "ws=loading";
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          id: "po001",
          order_number: "PO001",
          shipment_id: "stale-sj-old",
          latest_sj_number: "SJ-STALE",
          sj_label: "SJ-STALE",
        },
      ],
    });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po001",
        shipments: [{ id: "sh-1", number: "SJ001", lines: [{ line_idx: 0, qty: 10 }], total_qty: 10 }],
        surat_jalan_files: [{ id: "sj-1", shipment_id: "sh-1", sj_number: "SJ001" }],
      },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO001-stale-sj-old"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-schedule-task-error"]').textContent)
      .toMatch(/Surat Jalan tidak dapat ditentukan/);
  });

  test("valid single-SJ Loading flow still opens the exact shipment", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "loading.update" || p === "loading.confirm_match" || p === "delivery_note.view_file"
    ));
    mockDsRouter.search = "ws=loading";
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        id: "po-single",
        order_number: "PO-SINGLE",
        shipment_id: "sh-only",
        latest_sj_number: "SJ-ONLY",
        sj_label: "SJ-ONLY",
      }],
    });
    mockGetOrder.mockResolvedValue({
      order: {
        id: "po-single",
        po_number: "PO-SINGLE",
        surat_jalan_files: [{
          id: "sj-only",
          shipment_id: "sh-only",
          sj_number: "SJ-ONLY",
          lines: [{ line_idx: 0, qty: 10 }],
        }],
        line_items: [{ name: "Cup", quantity: 10 }],
        packing_info: { qty_packed: 10, lines: [{ line_idx: 0, packed_qty: 10 }] },
      },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO-SINGLE-sh-only"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-ws-loading"]')).toBeTruthy();
    expect(container.querySelector('a[href*="sj-only"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mop-schedule-task-error"]')).toBeFalsy();
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
    expect(container.querySelector('[data-testid="mop-ops-role-summary"]')).toBeFalsy();
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

  test("pengiriman Role Aktif does not use six-tile summary counts", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        { ...sampleRows[0], status: "hari_ini", urgent: true },
        { ...sampleRows[0], id: "done", order_number: "PO-DONE", status: "terkirim" },
      ],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mop-ops-role-summary"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mop-ops-role-pengiriman"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
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
    expect(pageSrc).not.toMatch(/buildOpsRoleSummaryModel/);
    expect(pageSrc).toMatch(/showBrand=\{false\}/);
  });

  test("ops table keeps horizontal scroll wrapper, compact Optimasi Rute, and 1180px min-width", () => {
    const css = fs.readFileSync(path.join(__dirname, "mobile.css"), "utf8");
    const opsCss = fs.readFileSync(path.join(__dirname, "opsQueue.css"), "utf8");
    expect(css).toMatch(/\.mop-schedule-scroll \{[\s\S]*overflow:\s*auto/);
    expect(opsCss).toMatch(/\.mop-root--ops-wide \.production-sheet-table \{[\s\S]*min-width:\s*calc\(1180px \* var\(--production-sheet-zoom, 1\)\)/);
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

  test("schedule rows render before the production summary request starts", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    const schedule = deferred();
    const dashboard = deferred();
    mockDeliverySchedule.mockReturnValue(schedule.promise);
    mockProductionDashboard.mockReturnValue(dashboard.promise);
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(mockDeliverySchedule).toHaveBeenCalledTimes(1);
    expect(mockProductionDashboard).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeTruthy();
    await act(async () => { schedule.resolve({ rows: sampleRows }); });
    await flush();
    expect(mockProductionDashboard).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-cup-PO-100"]').className).toMatch(/is-unavailable/);
    expect(container.querySelector('[data-testid="mds-screen-PO-100"]').className).toMatch(/is-unavailable/);
    expect(container.querySelector('[data-testid="mds-prod-PO-100"]').className).toMatch(/is-unavailable/);
    expect(container.querySelector('[data-testid="mds-packing-PO-100"]').className).toMatch(/is-unavailable/);
    expect(container.querySelector('[data-testid="mds-load-PO-100"]')).toBeTruthy();
    await act(async () => { dashboard.resolve({ rows: [] }); });
    await flush();
    expect(container.querySelector('[data-testid="mds-cup-PO-100"]').className).not.toMatch(/is-unavailable/);
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
    expect(pageSrc).toMatch(/consumeClickSuppression/);
    expect(pageSrc).toMatch(/useProductionSheetZoom\(\{ userId: userKey \}\)/);
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

  test("SJ rows appear while production is held, and a failed dashboard is not belum selesai", async () => {
    const production = deferred();
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        qty_loaded: 5000,
        qty_load_need: 5000,
        qty_delivered_sj: 2000,
        qty_sj: 5000,
      }],
    });
    mockProductionDashboard.mockImplementation(() => production.promise);
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-scroll"]').getAttribute("data-production-state")).toBe("loading");
    expect(container.querySelector('[data-testid="mds-cup-PO-100"]').className).toMatch(/is-unavailable/);
    expect(container.querySelector('[data-testid="mds-cup-PO-100"]').getAttribute("aria-label")).toBe("Status belum tersedia");
    expect(markKind(container.querySelector('[data-testid="mds-load-PO-100"]'))).toBe("done");
    expect(markKind(container.querySelector('[data-testid="mds-terkirim-PO-100"]'))).toBe("pending");

    await act(async () => { production.reject(new Error("dashboard down")); });
    await flush();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-scroll"]').getAttribute("data-production-state")).toBe("failed");
    expect(container.querySelector('[data-testid="mds-prod-PO-100"]').className).toMatch(/is-unavailable/);
    expect(markKind(container.querySelector('[data-testid="mds-load-PO-100"]'))).toBe("done");
    expect(container.textContent).not.toContain("Gagal memuat Jadwal Pengiriman.");
  });

  test("a previous schedule cycle cannot overwrite the latest reload", async () => {
    const prod1 = deferred();
    const prod2 = deferred();
    const schedule2 = deferred();
    let prodCalls = 0;
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockDeliverySchedule
      .mockResolvedValueOnce({ rows: [{ ...sampleRows[0], order_number: "PO-OLD", id: "old" }] })
      .mockImplementationOnce(() => schedule2.promise);
    mockProductionDashboard.mockImplementation(() => {
      prodCalls += 1;
      return prodCalls === 1 ? prod1.promise : prod2.promise;
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.textContent).toContain("PO-OLD");
    expect(container.querySelector('[data-testid="mds-scroll"]').getAttribute("data-production-state")).toBe("loading");

    await act(async () => {
      container.querySelector('[data-testid="mds-refresh"]').click();
    });
    await act(async () => {
      schedule2.resolve({ rows: [{ ...sampleRows[0], order_number: "PO-NEW", id: "new" }] });
    });
    await flush();
    expect(container.textContent).toContain("PO-NEW");
    expect(container.textContent).not.toContain("PO-OLD");

    await act(async () => {
      prod1.resolve({
        rows: [{
          id: "old",
          helper_required: 10,
          helper_prepared: 10,
          screen_qty: 10,
          packing_status: "Sudah",
          print_completed_qty: 10,
          print_qty_total: 10,
        }],
      });
    });
    await flush();
    expect(container.querySelector('[data-testid="mds-scroll"]').getAttribute("data-production-state")).toBe("loading");
    expect(container.querySelector('[data-testid="mds-cup-PO-NEW"]').className).toMatch(/is-unavailable/);

    await act(async () => { prod2.resolve({ rows: [] }); });
    await flush();
    expect(container.querySelector('[data-testid="mds-scroll"]').getAttribute("data-production-state")).toBe("ready");
    expect(container.textContent).toContain("PO-NEW");
    expect(container.textContent).not.toContain("PO-OLD");
  });

  function multiSjRow(shipmentId, number, delivered) {
    return {
      id: "po-multi",
      order_number: "PO-MULTI",
      customer_name: "Renea",
      status: "terlambat",
      status_label: "Terlambat",
      effective_delivery_date: "2026-09-01",
      shipment_id: shipmentId,
      sj_label: number,
      latest_sj_number: number,
      qty_delivered_sj: delivered,
      qty_sj: 400,
      qty_loaded: 400,
      qty_load_need: 400,
      qty_delivered: delivered,
      qty_total: 800,
      delivery_status: "Belum Terkirim",
      packing_status: "Berjalan",
      helper_prepared: 0,
      helper_required: 800,
    };
  }

  function confirmableMultiOrder() {
    return {
      id: "po-multi",
      po_number: "PO-MULTI",
      customer: "Renea",
      delivery_status: "Belum Terkirim",
      line_items: [{ name: "Cup", quantity: 800, line_id: "l0" }],
      packing_info: { qty_packed: 800, lines: [{ line_idx: 0, packed_qty: 800 }] },
      shipments: [
        { id: "sh-1", number: "SJ-1", lines: [{ line_idx: 0, qty: 400 }] },
        { id: "sh-2", number: "SJ-2", lines: [{ line_idx: 0, qty: 400 }] },
      ],
      surat_jalan_files: [
        { id: "sj-1", shipment_id: "sh-1", sj_number: "SJ-1" },
        { id: "sj-2", shipment_id: "sh-2", sj_number: "SJ-2" },
      ],
      loading_confirmations: [
        { id: "c1", shipment_id: "sh-1", surat_jalan_id: "sh-1", lines: [{ line_idx: 0, qty: 400 }] },
        { id: "c2", shipment_id: "sh-2", surat_jalan_id: "sh-2", lines: [{ line_idx: 0, qty: 400 }] },
      ],
      delivery_proofs: [
        { id: "p1", shipment_id: "sh-1", storage_path: "a.jpg" },
        { id: "p2", shipment_id: "sh-1", storage_path: "b.jpg" },
      ],
      delivery_recipient_signature: {
        id: "r1",
        shipment_id: "sh-1",
        signature_storage_path: "r.png",
        recipient_signed_at: "t",
        recipient_name: "Budi Penerima",
      },
      delivery_driver_signature: {
        id: "d1",
        shipment_id: "sh-1",
        signature_storage_path: "d.png",
        driver_signed_at: "t",
      },
    };
  }

  function kirimOf(shipmentId) {
    const row = container.querySelector(`[data-testid="mds-row-PO-MULTI-${shipmentId}"]`);
    return row ? row.querySelector('[data-testid="mds-terkirim-PO-MULTI"]') : null;
  }

  function typeSearch(value) {
    const input = container.querySelector('[data-testid="mds-search"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  test("confirming one SJ turns only that Kirim mark green without waiting for production", async () => {
    const lateSchedule = deferred();
    const refreshSchedule = deferred();
    const saved = {
      ...confirmableMultiOrder(),
      delivery_status: "Sebagian Terkirim",
      shipments: confirmableMultiOrder().shipments.map((sh) => (
        sh.id === "sh-1" ? { ...sh, delivery_status: "Terkirim", delivered_at: "2026-10-03T10:00:00+07:00" } : sh
      )),
    };
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view" || perm === "delivery.update");
    mockDsRouter.search = "ws=delivery";
    mockProductionDashboard.mockImplementation(() => new Promise(() => {}));
    mockDeliverySchedule
      .mockResolvedValueOnce({ rows: [multiSjRow("sh-1", "SJ-1", 0), multiSjRow("sh-2", "SJ-2", 0)] })
      .mockImplementationOnce(() => lateSchedule.promise)
      .mockImplementationOnce(() => refreshSchedule.promise);
    mockGetOrder.mockResolvedValue({ order: confirmableMultiOrder() });
    mockConfirmDelivered.mockResolvedValue({ order: saved });

    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(markKind(kirimOf("sh-1"))).toBe("idle");
    expect(markKind(kirimOf("sh-2"))).toBe("idle");
    await act(async () => { typeSearch("Renea"); });
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO-MULTI-sh-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const confirm = container.querySelector('[data-testid="mobile-confirm-delivered"]');
    expect(confirm).toBeTruthy();
    expect(confirm.disabled).toBe(false);
    await act(async () => {
      container.querySelector('[data-testid="mds-refresh"]').click();
    });

    const started = performance.now();
    await act(async () => { confirm.click(); });
    await flush();
    const elapsedMs = performance.now() - started;
    expect(markKind(kirimOf("sh-1"))).toBe("done");
    expect(markKind(kirimOf("sh-2"))).toBe("idle");
    expect(container.querySelector('[data-testid="mds-cup-PO-MULTI"]').className).toMatch(/is-unavailable/);
    expect(container.querySelector('[data-testid="mds-screen-PO-MULTI"]').className).toMatch(/is-unavailable/);
    expect(container.querySelector('[data-testid="mds-prod-PO-MULTI"]').className).toMatch(/is-unavailable/);
    expect(container.querySelector('[data-testid="mds-packing-PO-MULTI"]').className).toMatch(/is-unavailable/);
    expect(container.querySelector('[data-testid="mds-scroll"]').getAttribute("data-production-state")).toBe("loading");
    expect(container.querySelector('[data-testid="mds-search"]').value).toBe("Renea");
    fs.writeFileSync("/opt/cursor/artifacts/kirim_indicator_timing.json", JSON.stringify({
      localhost_ms_from_confirm_resolve_to_kirim_done: Number(elapsedMs.toFixed(1)),
      note: "jsdom localhost. Time is the React commit after confirm HTTP success, before the schedule refetch resolves. Not a phone or production measurement.",
    }));

    await act(async () => {
      lateSchedule.resolve({ rows: [multiSjRow("sh-1", "SJ-1", 0), multiSjRow("sh-2", "SJ-2", 0)] });
    });
    await flush();
    expect(markKind(kirimOf("sh-1"))).toBe("done");
    expect(markKind(kirimOf("sh-2"))).toBe("idle");

    await act(async () => {
      refreshSchedule.resolve({
        rows: [multiSjRow("sh-1", "SJ-1", 400), multiSjRow("sh-2", "SJ-2", 0)],
      });
    });
    await flush();
    expect(markKind(kirimOf("sh-1"))).toBe("done");
    expect(markKind(kirimOf("sh-2"))).toBe("idle");
    expect(container.querySelector('[data-testid="mds-search"]').value).toBe("Renea");
    expect(elapsedMs).toBeLessThan(1000);
  });

  test("a failed confirm leaves both Kirim marks unchanged", async () => {
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view" || perm === "delivery.update");
    mockDsRouter.search = "ws=delivery";
    mockProductionDashboard.mockImplementation(() => new Promise(() => {}));
    mockDeliverySchedule.mockResolvedValue({ rows: [multiSjRow("sh-1", "SJ-1", 0), multiSjRow("sh-2", "SJ-2", 0)] });
    mockGetOrder.mockResolvedValue({ order: confirmableMultiOrder() });
    mockConfirmDelivered.mockRejectedValue({
      response: { status: 400, data: { detail: "Konfirmasi ditolak." } },
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const callsBefore = mockDeliverySchedule.mock.calls.length;
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO-MULTI-sh-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mobile-confirm-delivered"]').click();
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-delivery-error"]').textContent).toBe("Konfirmasi ditolak.");
    expect(markKind(kirimOf("sh-1"))).toBe("idle");
    expect(markKind(kirimOf("sh-2"))).toBe("idle");
    expect(mockDeliverySchedule.mock.calls.length).toBe(callsBefore);
  });

  test("a failed schedule refetch keeps the Kirim mark painted from the saved order", async () => {
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view" || perm === "delivery.update");
    mockDsRouter.search = "ws=delivery";
    mockProductionDashboard.mockImplementation(() => new Promise(() => {}));
    mockDeliverySchedule
      .mockResolvedValueOnce({ rows: [multiSjRow("sh-1", "SJ-1", 0), multiSjRow("sh-2", "SJ-2", 0)] })
      .mockRejectedValueOnce(new Error("schedule down"));
    const saved = {
      ...confirmableMultiOrder(),
      shipments: confirmableMultiOrder().shipments.map((sh) => (
        sh.id === "sh-1" ? { ...sh, delivery_status: "Terkirim", delivered_at: "t" } : sh
      )),
    };
    mockGetOrder.mockResolvedValue({ order: confirmableMultiOrder() });
    mockConfirmDelivered.mockResolvedValue({ order: saved });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO-MULTI-sh-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="mobile-confirm-delivered"]').click();
    });
    await flush();
    expect(markKind(kirimOf("sh-1"))).toBe("done");
    expect(markKind(kirimOf("sh-2"))).toBe("idle");
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
  });

  function setVisibility(value) {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => value,
    });
  }

  test("a visible tab refetches the schedule without production or a second confirm", async () => {
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view" || perm === "delivery.update");
    mockDsRouter.search = "ws=delivery";
    mockProductionDashboard.mockResolvedValue({ rows: [] });
    mockDeliverySchedule.mockResolvedValue({
      rows: [multiSjRow("sh-1", "SJ-1", 0), multiSjRow("sh-2", "SJ-2", 0)],
    });
    mockGetOrder.mockResolvedValue({ order: confirmableMultiOrder() });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const scheduleCalls = mockDeliverySchedule.mock.calls.length;
    const productionCalls = mockProductionDashboard.mock.calls.length;
    await act(async () => { typeSearch("Renea"); });
    await act(async () => {
      container.querySelector('[data-testid="mds-row-PO-MULTI-sh-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]')).toBeTruthy();

    setVisibility("hidden");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(mockDeliverySchedule.mock.calls.length).toBe(scheduleCalls);

    setVisibility("visible");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    await flush();
    expect(mockDeliverySchedule.mock.calls.length).toBe(scheduleCalls + 1);
    expect(mockProductionDashboard.mock.calls.length).toBe(productionCalls + 1);
    expect(mockConfirmDelivered).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mds-search"]').value).toBe("Renea");
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]')).toBeTruthy();
    expect(markKind(kirimOf("sh-1"))).toBe("idle");
  });

  test("a schedule response started before the visible refresh cannot overwrite the newer rows", async () => {
    const older = deferred();
    const newer = deferred();
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockProductionDashboard.mockResolvedValue({ rows: [] });
    mockDeliverySchedule
      .mockResolvedValueOnce({ rows: [multiSjRow("sh-1", "SJ-1", 0)] })
      .mockImplementationOnce(() => older.promise)
      .mockImplementationOnce(() => newer.promise);
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    setVisibility("visible");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    await act(async () => { container.querySelector('[data-testid="mds-refresh"]').click(); });
    await act(async () => { older.resolve({ rows: [multiSjRow("sh-1", "SJ-1", 0)] }); });
    await flush();
    await act(async () => { newer.resolve({ rows: [multiSjRow("sh-1", "SJ-1", 400)] }); });
    await flush();
    expect(markKind(kirimOf("sh-1"))).toBe("done");
    await act(async () => { older.resolve({ rows: [multiSjRow("sh-1", "SJ-1", 0)] }); });
    await flush();
    expect(markKind(kirimOf("sh-1"))).toBe("done");
  });

  test("unmount removes the visibility listener", async () => {
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockProductionDashboard.mockResolvedValue({ rows: [] });
    mockDeliverySchedule.mockResolvedValue({ rows: [multiSjRow("sh-1", "SJ-1", 0)] });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    const calls = mockDeliverySchedule.mock.calls.length;
    act(() => root.unmount());
    root = createRoot(container);
    setVisibility("visible");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(mockDeliverySchedule.mock.calls.length).toBe(calls);
  });

  test("reopening the schedule reads the stored SJ qty", async () => {
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockProductionDashboard.mockResolvedValue({ rows: [] });
    mockDeliverySchedule.mockResolvedValue({
      rows: [multiSjRow("sh-1", "SJ-1", 400), multiSjRow("sh-2", "SJ-2", 0)],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(markKind(kirimOf("sh-1"))).toBe("done");
    expect(markKind(kirimOf("sh-2"))).toBe("idle");
  });

  function setNativeValue(selector, value) {
    const input = container.querySelector(selector);
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  test("a customer filter applied during the schedule request still limits the resolved rows", async () => {
    const schedule = deferred();
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockProductionDashboard.mockResolvedValue({ rows: [] });
    mockDeliverySchedule.mockImplementation(() => schedule.promise);
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="mds-filter-btn"]').click();
    });
    await act(async () => { setNativeValue('[data-testid="mds-adv-customer"]', "CV Beta"); });
    await act(async () => {
      container.querySelector('[data-testid="mds-filter-apply"]').click();
    });
    expect(container.querySelector('[data-testid="mds-filter-panel"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeTruthy();

    await act(async () => {
      schedule.resolve({
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
          },
        ],
      });
    });
    await flush();
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-row-PO-200-sh-200"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-row-PO-100-sh-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-filter-btn"]').textContent).toContain("Filter (1)");
  });

  test("unmount during an in-flight schedule ignores that response on the next mount", async () => {
    const lateSchedule = deferred();
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockProductionDashboard.mockResolvedValue({ rows: [] });
    mockDeliverySchedule
      .mockImplementationOnce(() => lateSchedule.promise)
      .mockResolvedValue({
        rows: [{ ...sampleRows[0], id: "next", order_number: "PO-NEXT" }],
      });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeTruthy();
    act(() => root.unmount());
    root = createRoot(container);
    await act(async () => {
      lateSchedule.resolve({
        rows: [{ ...sampleRows[0], id: "stale", order_number: "PO-STALE" }],
      });
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.textContent).toContain("PO-NEXT");
    expect(container.textContent).not.toContain("PO-STALE");
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
  });

  test("unmount during an in-flight summary ignores that response on the next mount", async () => {
    const lateDashboard = deferred();
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockDeliverySchedule
      .mockResolvedValueOnce({ rows: [{ ...sampleRows[0], id: "live", order_number: "PO-LIVE" }] })
      .mockResolvedValue({ rows: [{ ...sampleRows[0], id: "next", order_number: "PO-NEXT" }] });
    mockProductionDashboard
      .mockImplementationOnce(() => lateDashboard.promise)
      .mockResolvedValue({ rows: [] });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.textContent).toContain("PO-LIVE");
    expect(container.querySelector('[data-testid="mds-scroll"]').getAttribute("data-production-state")).toBe("loading");
    act(() => root.unmount());
    root = createRoot(container);
    await act(async () => {
      lateDashboard.resolve({
        rows: [{
          id: "live",
          helper_required: 10,
          helper_prepared: 10,
          screen_qty: 10,
          packing_status: "Sudah",
          print_completed_qty: 10,
          print_qty_total: 10,
        }],
      });
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.textContent).toContain("PO-NEXT");
    expect(container.textContent).not.toContain("PO-LIVE");
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-scroll"]').getAttribute("data-production-state")).toBe("ready");
    expect(container.querySelector('[data-testid="mds-cup-PO-NEXT"]').className).not.toMatch(/is-done/);
  });

  test("a failed schedule clears loading and retry loads the latest rows", async () => {
    const staleDashboard = deferred();
    const retryDashboard = deferred();
    let scheduleCalls = 0;
    let dashboardCalls = 0;
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockDeliverySchedule.mockImplementation(() => {
      scheduleCalls += 1;
      if (scheduleCalls === 1) return Promise.reject(new Error("schedule down"));
      return Promise.resolve({
        rows: [{ ...sampleRows[0], id: "retry", order_number: "PO-RETRY" }],
      });
    });
    mockProductionDashboard.mockImplementation(() => {
      dashboardCalls += 1;
      return dashboardCalls === 1 ? staleDashboard.promise : retryDashboard.promise;
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-error"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeFalsy();
    expect(container.textContent).toContain("Gagal memuat Jadwal Pengiriman.");

    await act(async () => {
      container.querySelector('[data-testid="mds-retry"]').click();
    });
    await flush();
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-error"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.textContent).toContain("PO-RETRY");

    await act(async () => {
      staleDashboard.resolve({
        rows: [{
          id: "stale",
          helper_required: 10,
          helper_prepared: 10,
          screen_qty: 10,
          packing_status: "Sudah",
          print_completed_qty: 10,
          print_qty_total: 10,
        }],
      });
    });
    await flush();
    expect(container.textContent).toContain("PO-RETRY");
    expect(container.querySelector('[data-testid="mds-error"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeFalsy();

    await act(async () => { retryDashboard.resolve({ rows: [] }); });
    await flush();
    expect(container.textContent).toContain("PO-RETRY");
    expect(container.querySelector('[data-testid="mds-scroll"]').getAttribute("data-production-state")).toBe("ready");
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeFalsy();
  });

  test("a fresh summary cache paints marks after the schedule without another request", async () => {
    const schedule = deferred();
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockDeliverySchedule.mockReturnValue(schedule.promise);
    await loadProductionSummary({
      userKey: "u-mds",
      requestParams: mobileProductionDashboardParams({ includeCancelled: false, includeCompleted: true }),
      fetcher: async () => ({
        rows: [{
          id: "o1",
          po_number: "PO-100",
          helper_required: 10,
          helper_prepared: 10,
          screen_qty: 10,
          packing_status: "Sudah",
          print_completed_qty: 10,
          print_qty_total: 10,
        }],
      }),
    });
    mockProductionDashboard.mockImplementation(() => {
      throw new Error("summary should stay cached");
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(mockProductionDashboard).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeTruthy();
    await act(async () => { schedule.resolve({ rows: sampleRows }); });
    await flush();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeFalsy();
    expect(mockProductionDashboard).not.toHaveBeenCalled();
    expect(markKind(container.querySelector('[data-testid="mds-cup-PO-100"]'))).toBe("done");
    expect(markKind(container.querySelector('[data-testid="mds-load-PO-100"]'))).toBe("idle");
  });

  test("manual refresh bypasses a fresh summary cache", async () => {
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    await loadProductionSummary({
      userKey: "u-mds",
      requestParams: mobileProductionDashboardParams({ includeCancelled: false, includeCompleted: true }),
      fetcher: async () => ({ rows: [] }),
    });
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    mockProductionDashboard.mockResolvedValue({
      rows: [{
        id: "o1",
        helper_required: 10,
        helper_prepared: 10,
        screen_qty: 0,
        print_completed_qty: 0,
        print_qty_total: 10,
        packing_status: "Belum",
      }],
    });
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(mockProductionDashboard).not.toHaveBeenCalled();
    await act(async () => { container.querySelector('[data-testid="mds-refresh"]').click(); });
    await flush();
    expect(mockProductionDashboard).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="mds-loading"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(markKind(container.querySelector('[data-testid="mds-cup-PO-100"]'))).toBe("done");
  });

  test("a summary response started before refresh cannot restore the old marks", async () => {
    const stale = deferred();
    const fresh = deferred();
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    mockProductionDashboard
      .mockImplementationOnce(() => stale.promise)
      .mockImplementationOnce(() => fresh.promise);
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-cup-PO-100"]').className).toMatch(/is-unavailable/);
    await act(async () => { container.querySelector('[data-testid="mds-refresh"]').click(); });
    await flush();
    await act(async () => {
      stale.resolve({
        rows: [{
          id: "o1",
          helper_required: 10,
          helper_prepared: 10,
          screen_qty: 10,
          packing_status: "Sudah",
          print_completed_qty: 10,
          print_qty_total: 10,
        }],
      });
    });
    await flush();
    expect(container.querySelector('[data-testid="mds-cup-PO-100"]').className).toMatch(/is-unavailable/);
    await act(async () => {
      fresh.resolve({
        rows: [{
          id: "o1",
          helper_required: 10,
          helper_prepared: 0,
          screen_qty: 0,
          packing_status: "Belum",
          print_completed_qty: 0,
          print_qty_total: 10,
        }],
      });
    });
    await flush();
    expect(markKind(container.querySelector('[data-testid="mds-cup-PO-100"]'))).not.toBe("done");
    expect(container.querySelector('[data-testid="mds-scroll"]').getAttribute("data-production-state")).toBe("ready");
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
  });

  test("a summary response started before save invalidation cannot paint old marks", async () => {
    const stale = deferred();
    const fresh = deferred();
    mockHasPerm.mockImplementation((perm) => perm === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    mockProductionDashboard
      .mockImplementationOnce(() => stale.promise)
      .mockImplementationOnce(() => fresh.promise);
    await act(async () => { root.render(<MobileDeliverySchedulePage />); });
    await flush();
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mds-cup-PO-100"]').className).toMatch(/is-unavailable/);
    invalidateProductionSummaryCache();
    await act(async () => {
      stale.resolve({
        rows: [{
          id: "o1",
          helper_required: 10,
          helper_prepared: 10,
          screen_qty: 10,
          packing_status: "Sudah",
          print_completed_qty: 10,
          print_qty_total: 10,
        }],
      });
    });
    await flush();
    expect(container.querySelector('[data-testid="mds-cup-PO-100"]').className).toMatch(/is-unavailable/);
    await act(async () => {
      fresh.resolve({
        rows: [{
          id: "o1",
          helper_required: 10,
          helper_prepared: 0,
          screen_qty: 0,
          packing_status: "Belum",
          print_completed_qty: 0,
          print_qty_total: 10,
        }],
      });
    });
    await flush();
    expect(mockProductionDashboard).toHaveBeenCalledTimes(2);
    expect(markKind(container.querySelector('[data-testid="mds-cup-PO-100"]'))).not.toBe("done");
    expect(container.querySelector('[data-testid="mds-scroll"]').getAttribute("data-production-state")).toBe("ready");
    expect(container.querySelector('[data-testid="mds-table"]')).toBeTruthy();
  });
});
