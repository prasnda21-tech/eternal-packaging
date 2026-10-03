import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { clearAllMobileTaskListRetention } from "../lib/mobileTaskListRetention";
import { clearMobileOpsSessionForUser } from "../lib/mobileOpsSession";
import { wibDateKey } from "../lib/suratJalan";
import MobileTasksPage from "./MobileTasksPage";

const mockHasPerm = jest.fn(() => false);
const mockAuthState = {
  user: { id: "u-driver", name: "Budi", email: "budi@x.com", role: "operator" },
};
const defaultAuthUser = () => ({ id: "u-driver", name: "Budi", email: "budi@x.com", role: "operator" });
const mockNavigate = jest.fn();
const mockTasksRouter = {
  search: "",
  setParams: jest.fn((next) => {
    if (next instanceof URLSearchParams) mockTasksRouter.search = next.toString();
    else if (next && typeof next === "object") mockTasksRouter.search = new URLSearchParams(next).toString();
  }),
};

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => mockNavigate,
  useSearchParams: () => [new URLSearchParams(mockTasksRouter.search), mockTasksRouter.setParams],
  useLocation: () => ({
    pathname: "/m/tasks",
    search: mockTasksRouter.search ? `?${mockTasksRouter.search}` : "",
  }),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    get user() { return mockAuthState.user; },
    hasPerm: (perm) => mockHasPerm(perm),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    divisionTasks: jest.fn(),
    getOrder: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const clickFilter = async (container, id) => {
  const btn = container.querySelector(`[data-testid="mobile-task-filter-${id}"]`);
  await act(async () => {
    btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
};

const visibleTaskIds = (container) => (
  [...container.querySelectorAll("a.mop-card")].map((el) => el.getAttribute("data-testid"))
);

describe("MobileTasksPage sorting", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockNavigate.mockReset();
    mockHasPerm.mockImplementation((p) => p === "operator.update");
    mockTasksRouter.search = "";
    mockTasksRouter.setParams.mockClear();
    api.divisionTasks.mockReset();
    api.getOrder.mockReset();
    mockAuthState.user = defaultAuthUser();
    clearAllMobileTaskListRetention();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("sorts scheduled work dates first without changing task count or fetching orders", async () => {
    api.divisionTasks.mockResolvedValue({
      tasks: [
        { task_id: "A", order_id: "a", order_number: "PO-A", work_date: "2026-09-10", item: "Cup", target_qty: 1, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
        { task_id: "B", order_id: "b", order_number: "PO-B", work_date: null, item: "Cup", target_qty: 1, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
        { task_id: "C", order_id: "c", order_number: "PO-C", work_date: "2026-09-08", item: "Cup", target_qty: 1, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
        { task_id: "D", order_id: "d", order_number: "PO-D", work_date: "2026-09-09", item: "Cup", target_qty: 1, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
      ],
    });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(api.divisionTasks).toHaveBeenCalledTimes(1);
    expect(api.divisionTasks).toHaveBeenCalledWith("operator", "mine");
    expect(api.getOrder).not.toHaveBeenCalled();
    await clickFilter(container, "menunggu");
    const ids = visibleTaskIds(container);
    expect(ids).toEqual(["mobile-task-C", "mobile-task-D", "mobile-task-A"]);
    expect(ids).not.toContain("mobile-task-B");
    expect(ids).toHaveLength(3);
    expect(container.querySelector('[data-testid="mobile-workspace-select"]')).toBeTruthy();
    expect(container.textContent).toContain("Pilih Tugas");
    expect(container.querySelector(".mop-ws-tabs")).toBeFalsy();
  });

  test("Helper dropdown lists only granted prep/packing/loading workspaces", async () => {
    mockHasPerm.mockImplementation((p) => ["helper.prepare", "packing.update", "loading.update"].includes(p));
    api.divisionTasks.mockResolvedValue({ tasks: [] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    const select = container.querySelector('[data-testid="mobile-workspace-select-input"]');
    expect([...select.options].map((o) => o.textContent)).toEqual(["Persiapan Cup", "Packing", "Loading"]);
    expect([...select.options].map((o) => o.value)).not.toContain("screener");
    expect([...select.options].map((o) => o.value)).not.toContain("print");
  });

  test("unauthorized workspace options are absent from dropdown", async () => {
    mockHasPerm.mockImplementation((p) => p === "screen.update");
    api.divisionTasks.mockResolvedValue({ tasks: [] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(container.querySelector('[data-testid="mobile-workspace-select-current"]').textContent).toBe("Screener");
    expect(container.querySelector("select")).toBeFalsy();
  });

  test("changing dropdown switches divisionTasks workspace without changing auth role", async () => {
    mockHasPerm.mockImplementation((p) => ["helper.prepare", "packing.update"].includes(p));
    api.divisionTasks.mockResolvedValue({ tasks: [] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(api.divisionTasks).toHaveBeenCalledWith("helper", "mine");
    const select = container.querySelector('[data-testid="mobile-workspace-select-input"]');
    await act(async () => {
      select.value = "packing";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
    expect(mockTasksRouter.setParams).toHaveBeenCalled();
    const lastArg = mockTasksRouter.setParams.mock.calls.at(-1)[0];
    expect(String(lastArg)).toContain("ws=packing");
    // Simulate browser/router applying the replaced query, then remount.
    mockTasksRouter.search = "ws=packing";
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(api.divisionTasks).toHaveBeenCalledWith("packing", "mine");
    expect(container.textContent).toContain("Operator");
  });

  test("selecting Loading routes to Jadwal Pengiriman instead of Loading cards", async () => {
    mockHasPerm.mockImplementation((p) => ["helper.prepare", "packing.update", "loading.update"].includes(p));
    api.divisionTasks.mockResolvedValue({ tasks: [] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(mockNavigate).not.toHaveBeenCalled();
    const select = container.querySelector('[data-testid="mobile-workspace-select-input"]');
    await act(async () => {
      select.value = "loading";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
    expect(mockNavigate).toHaveBeenCalledWith("/m/delivery-schedule?ws=loading");
    expect(api.divisionTasks).not.toHaveBeenCalledWith("loading", "mine");
  });

  test("direct /m/tasks?ws=loading replaces to the schedule table route", async () => {
    mockHasPerm.mockImplementation((p) => p === "loading.update");
    api.divisionTasks.mockResolvedValue({ tasks: [] });
    mockTasksRouter.search = "ws=loading";
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(mockNavigate).toHaveBeenCalledWith("/m/delivery-schedule?ws=loading", { replace: true });
    expect(api.divisionTasks).not.toHaveBeenCalled();
  });

  test("CASE B — Pengiriman workspace stays in URL for back restoration", async () => {
    mockHasPerm.mockImplementation((p) => ["delivery.update", "loading.update"].includes(p));
    api.divisionTasks.mockResolvedValue({ tasks: [] });
    mockTasksRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(api.divisionTasks).toHaveBeenCalledWith("terkirim", "mine");
    expect(container.querySelector('[data-testid="mobile-workspace-select-current"]')?.textContent
      || container.querySelector('[data-testid="mobile-workspace-select-input"]')?.value).toMatch(/delivery|Pengiriman/i);
  });

  test("sorts URGENT then H-1 then existing work_date order", async () => {
    api.divisionTasks.mockResolvedValue({
      tasks: [
        { task_id: "N", order_id: "n", order_number: "PO-N", work_date: "2026-09-07", urgent: false, delivery_deadline_status: "normal", item: "Cup", target_qty: 1, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
        { task_id: "H", order_id: "h", order_number: "PO-H", work_date: "2026-09-10", urgent: false, delivery_deadline_status: "h1", item: "Cup", target_qty: 1, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
        { task_id: "U", order_id: "u", order_number: "PO-U", work_date: "2026-09-11", urgent: true, delivery_deadline_status: "normal", item: "Cup", target_qty: 1, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
      ],
    });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    await clickFilter(container, "aktif");
    const ids = visibleTaskIds(container);
    expect(ids).toEqual(["mobile-task-U", "mobile-task-H", "mobile-task-N"]);
  });

  test("screen_size does not change URGENT/H-1 sort and is visible on each card", async () => {
    api.divisionTasks.mockResolvedValue({
      tasks: [
        { task_id: "N", order_id: "n", order_number: "PO-N", work_date: "2026-09-07", urgent: false, delivery_deadline_status: "normal", item: "Preview Cup", line_idx: 0, screen_size: "large", target_qty: 1, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
        { task_id: "H", order_id: "h", order_number: "PO-H", work_date: "2026-09-10", urgent: false, delivery_deadline_status: "h1", item: "Preview Cup", line_idx: 1, screen_size: "small", target_qty: 1, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
        { task_id: "U", order_id: "u", order_number: "PO-U", work_date: "2026-09-11", urgent: true, delivery_deadline_status: "normal", item: "Preview Cup", line_idx: 0, screen_size: "large", target_qty: 1, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
      ],
    });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    await clickFilter(container, "aktif");
    const cards = [...container.querySelectorAll("a.mop-card")];
    expect(cards.map((el) => el.getAttribute("data-testid"))).toEqual(["mobile-task-U", "mobile-task-H", "mobile-task-N"]);
    expect(cards[0].querySelector('[data-testid="mobile-task-screen"]').textContent).toContain("Screen Besar");
    expect(cards[1].querySelector('[data-testid="mobile-task-screen"]').textContent).toContain("Screen Kecil");
    expect(cards[2].querySelector('[data-testid="mobile-task-screen"]').textContent).toContain("Screen Besar");
  });
});

describe("MobileTasksPage summary filters", () => {
  let container;
  let root;
  const today = wibDateKey(new Date());

  const fixtureTasks = () => ([
    { task_id: "TODAY-WAIT", order_id: "tw", order_number: "PO-TW", work_date: today, item: "Cup", target_qty: 10, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
    { task_id: "TODAY-PART", order_id: "tp", order_number: "PO-TP", work_date: today, item: "Cup", target_qty: 10, completed_qty: 3, status: "sebagian", status_label: "Sebagian" },
    { task_id: "TODAY-DONE", order_id: "td", order_number: "PO-TD", work_date: today, item: "Cup", target_qty: 10, completed_qty: 10, status: "selesai", status_label: "Selesai" },
    { task_id: "OLD-WAIT", order_id: "ow", order_number: "PO-OW", work_date: "2020-01-10", item: "Cup", target_qty: 10, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
    { task_id: "OLD-PART", order_id: "op", order_number: "PO-OP", work_date: "2020-01-08", item: "Cup", target_qty: 10, completed_qty: 4, status: "sebagian", status_label: "Sebagian" },
    { task_id: "OLD-DONE", order_id: "od", order_number: "PO-OD", work_date: "2020-01-08", item: "Cup", target_qty: 10, completed_qty: 10, status: "selesai", status_label: "Selesai" },
    { task_id: "FUTURE", order_id: "fu", order_number: "PO-FU", work_date: "2099-01-01", item: "Cup", target_qty: 10, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
    { task_id: "FUTURE-PART", order_id: "fp", order_number: "PO-FP", work_date: "2099-01-02", item: "Cup", target_qty: 10, completed_qty: 2, status: "sebagian", status_label: "Sebagian" },
    { task_id: "UNSCHED", order_id: "us", order_number: "PO-US", work_date: null, item: "Cup", target_qty: 10, completed_qty: 0, status: "belum", status_label: "Belum Mulai" },
  ]);

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockNavigate.mockReset();
    mockHasPerm.mockImplementation((p) => p === "delivery.update");
    mockTasksRouter.search = "ws=delivery";
    mockTasksRouter.setParams.mockClear();
    api.divisionTasks.mockReset();
    api.getOrder.mockReset();
    mockAuthState.user = defaultAuthUser();
    clearAllMobileTaskListRetention();
    api.divisionTasks.mockResolvedValue({ tasks: fixtureTasks() });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const countText = (id) => container.querySelector(`[data-testid="mobile-task-filter-${id}"] strong`).textContent;
  const selectedId = () => container.querySelector(".mop-summary-cell.is-selected")?.getAttribute("data-testid");

  test("default selected filter is Tugas Hari Ini and list matches that count", async () => {
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(selectedId()).toBe("mobile-task-filter-hariIni");
    expect(container.querySelector('[data-testid="mobile-task-filter-hariIni"]').getAttribute("aria-pressed")).toBe("true");
    expect(visibleTaskIds(container)).toEqual(["mobile-task-TODAY-DONE", "mobile-task-TODAY-PART", "mobile-task-TODAY-WAIT"]);
    expect(visibleTaskIds(container)).toHaveLength(Number(countText("hariIni")));
    expect(countText("hariIni")).toBe("3");
    expect(countText("aktif")).toBe("4");
    expect(countText("proses")).toBe("2");
    expect(countText("menunggu")).toBe("2");
  });

  test("clicking each card changes the visible list and selected visual state", async () => {
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    await clickFilter(container, "aktif");
    expect(selectedId()).toBe("mobile-task-filter-aktif");
    expect(visibleTaskIds(container)).toEqual([
      "mobile-task-OLD-PART",
      "mobile-task-OLD-WAIT",
      "mobile-task-TODAY-PART",
      "mobile-task-TODAY-WAIT",
    ]);
    expect(visibleTaskIds(container)).toHaveLength(Number(countText("aktif")));

    await clickFilter(container, "proses");
    expect(selectedId()).toBe("mobile-task-filter-proses");
    expect(visibleTaskIds(container)).toEqual(["mobile-task-OLD-PART", "mobile-task-TODAY-PART"]);
    expect(visibleTaskIds(container)).toHaveLength(Number(countText("proses")));

    await clickFilter(container, "menunggu");
    expect(selectedId()).toBe("mobile-task-filter-menunggu");
    expect(visibleTaskIds(container)).toEqual([
      "mobile-task-OLD-WAIT",
      "mobile-task-TODAY-WAIT",
    ]);
    expect(visibleTaskIds(container)).toHaveLength(Number(countText("menunggu")));
    expect(visibleTaskIds(container)).not.toContain("mobile-task-FUTURE");
    expect(visibleTaskIds(container)).not.toContain("mobile-task-FUTURE-PART");
    expect(visibleTaskIds(container)).not.toContain("mobile-task-UNSCHED");

    await clickFilter(container, "hariIni");
    expect(selectedId()).toBe("mobile-task-filter-hariIni");
    expect(visibleTaskIds(container)).not.toContain("mobile-task-OLD-WAIT");
    expect(visibleTaskIds(container)).not.toContain("mobile-task-OLD-PART");
    expect(visibleTaskIds(container)).not.toContain("mobile-task-FUTURE");
  });

  test("card counts stay global and do not recalculate after selecting Aktif", async () => {
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    await clickFilter(container, "aktif");
    expect(countText("hariIni")).toBe("3");
    expect(countText("aktif")).toBe("4");
    expect(countText("proses")).toBe("2");
    expect(countText("menunggu")).toBe("2");
  });

  test("future and unscheduled tasks appear in none of the four filters", async () => {
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    for (const id of ["hariIni", "aktif", "proses", "menunggu"]) {
      await clickFilter(container, id);
      const ids = visibleTaskIds(container);
      expect(ids).not.toContain("mobile-task-FUTURE");
      expect(ids).not.toContain("mobile-task-FUTURE-PART");
      expect(ids).not.toContain("mobile-task-UNSCHED");
    }
  });

  test("Driver delivery permission filtering stays intact when cards are clicked", async () => {
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(api.divisionTasks).toHaveBeenCalledTimes(1);
    expect(api.divisionTasks).toHaveBeenCalledWith("terkirim", "mine");
    await clickFilter(container, "aktif");
    await clickFilter(container, "proses");
    expect(api.divisionTasks).toHaveBeenCalledTimes(1);
    expect(api.divisionTasks).not.toHaveBeenCalledWith("operator", "mine");
    expect(api.divisionTasks).not.toHaveBeenCalledWith("packing", "mine");
    expect(api.divisionTasks).not.toHaveBeenCalledWith("loading", "mine");
    expect(mockHasPerm).toHaveBeenCalledWith("delivery.update");
    expect(container.querySelector('[data-testid="mobile-workspace-select-current"]')?.textContent
      || container.querySelector('[data-testid="mobile-workspace-select-input"]')?.value).toMatch(/Pengiriman|delivery/i);
  });
});

describe("MobileTasksPage remount retention", () => {
  let container;
  let root;
  const today = wibDateKey(new Date());

  const task = (id, extra = {}) => ({
    task_id: id,
    order_id: id.toLowerCase(),
    order_number: `PO-${id}`,
    work_date: today,
    item: "Cup",
    target_qty: 1,
    completed_qty: 0,
    status: "belum",
    status_label: "Belum Mulai",
    ...extra,
  });

  const deferred = () => {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  };

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockNavigate.mockReset();
    mockHasPerm.mockImplementation((p) => ["delivery.update", "packing.update"].includes(p));
    mockTasksRouter.search = "ws=delivery";
    mockTasksRouter.setParams.mockClear();
    api.divisionTasks.mockReset();
    api.getOrder.mockReset();
    mockAuthState.user = defaultAuthUser();
    clearAllMobileTaskListRetention();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    clearAllMobileTaskListRetention();
  });

  test("first list load still fetches and shows rows after the request completes", async () => {
    api.divisionTasks.mockResolvedValue({ tasks: [task("DEL-1")] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(api.divisionTasks).toHaveBeenCalledTimes(1);
    expect(api.divisionTasks).toHaveBeenCalledWith("terkirim", "mine");
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-1"]);
    expect(container.textContent).not.toContain("Memuat tugas...");
  });

  test("same user + workspace remount keeps known rows visible while refresh is in flight", async () => {
    const first = { tasks: [task("DEL-1")] };
    const second = deferred();
    let calls = 0;
    api.divisionTasks.mockImplementation(() => {
      calls += 1;
      if (calls === 1) return Promise.resolve(first);
      return second.promise;
    });

    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-1"]);
    act(() => root.unmount());

    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-1"]);
    expect(container.textContent).not.toContain("Memuat tugas...");
    expect(api.divisionTasks).toHaveBeenCalledTimes(2);

    await act(async () => { second.resolve({ tasks: [task("DEL-1"), task("DEL-2")] }); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-1", "mobile-task-DEL-2"]);
    expect(container.textContent).not.toContain("Memuat tugas...");
  });

  test("workspace switch does not keep the previous division's rows as valid", async () => {
    const packingPayload = { tasks: [task("PACK-1")] };
    const deliveryPending = deferred();
    api.divisionTasks.mockImplementation((division) => {
      if (division === "packing") return Promise.resolve(packingPayload);
      return deliveryPending.promise;
    });

    mockTasksRouter.search = "ws=packing";
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-PACK-1"]);

    mockTasksRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileTasksPage />); });
    expect(visibleTaskIds(container)).toEqual([]);
    expect(container.textContent).toContain("Memuat tugas...");
    expect(container.textContent).not.toContain("PO-PACK-1");

    await act(async () => { deliveryPending.resolve({ tasks: [task("DEL-1")] }); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-1"]);
    expect(container.textContent).not.toContain("PO-PACK-1");
  });

  test("user switch does not show the prior user's rows", async () => {
    api.divisionTasks.mockResolvedValue({ tasks: [task("DEL-A")] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-A"]);
    act(() => root.unmount());

    mockAuthState.user = { id: "u-other", name: "Citra", email: "citra@x.com", role: "operator" };
    const pending = deferred();
    api.divisionTasks.mockImplementation(() => pending.promise);
    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    expect(visibleTaskIds(container)).toEqual([]);
    expect(container.textContent).toContain("Memuat tugas...");
    expect(container.textContent).not.toContain("PO-DEL-A");
    expect(api.divisionTasks).toHaveBeenCalledTimes(2);

    await act(async () => { pending.resolve({ tasks: [task("DEL-B")] }); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-B"]);
    expect(container.textContent).not.toContain("PO-DEL-A");
  });

  test("logout clears retained rows so the next mount starts empty", async () => {
    api.divisionTasks.mockResolvedValue({ tasks: [task("DEL-1")] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    act(() => root.unmount());
    clearMobileOpsSessionForUser("u-driver");

    const pending = deferred();
    api.divisionTasks.mockImplementation(() => pending.promise);
    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    expect(visibleTaskIds(container)).toEqual([]);
    expect(container.textContent).toContain("Memuat tugas...");
    await act(async () => { pending.resolve({ tasks: [task("DEL-1")] }); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-1"]);
  });

  test("HTTP 401 clears every retained list; remount does not restore rows", async () => {
    api.divisionTasks.mockImplementation((division) => {
      if (division === "packing") return Promise.resolve({ tasks: [task("PACK-1")] });
      return Promise.resolve({ tasks: [task("DEL-1")] });
    });
    mockTasksRouter.search = "ws=packing";
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    mockTasksRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-1"]);
    act(() => root.unmount());

    api.divisionTasks.mockRejectedValue({
      response: { status: 401, data: { detail: "Sesi berakhir. Silakan login ulang." } },
    });
    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(container.textContent).toContain("Sesi berakhir");
    expect(visibleTaskIds(container)).toEqual([]);

    mockTasksRouter.search = "ws=packing";
    const pending = deferred();
    api.divisionTasks.mockImplementation(() => pending.promise);
    await act(async () => { root.render(<MobileTasksPage />); });
    expect(visibleTaskIds(container)).toEqual([]);
    expect(container.textContent).not.toContain("PO-PACK-1");
    expect(container.textContent).toContain("Memuat tugas...");
    await act(async () => { pending.resolve({ tasks: [task("PACK-2")] }); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-PACK-2"]);
  });

  test("HTTP 403 drops only the failed list and is not treated as 401", async () => {
    api.divisionTasks.mockImplementation((division) => {
      if (division === "packing") return Promise.resolve({ tasks: [task("PACK-1")] });
      return Promise.resolve({ tasks: [task("DEL-1")] });
    });
    mockTasksRouter.search = "ws=packing";
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    mockTasksRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    act(() => root.unmount());

    api.divisionTasks.mockRejectedValue({
      response: { status: 403, data: { detail: "Permission 'delivery.update' diperlukan" } },
    });
    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(container.textContent).toContain("Permission 'delivery.update' diperlukan");
    expect(visibleTaskIds(container)).toEqual([]);
    expect(container.textContent).not.toContain("Sesi berakhir");

    mockTasksRouter.search = "ws=packing";
    const pending = deferred();
    api.divisionTasks.mockImplementation(() => pending.promise);
    await act(async () => { root.render(<MobileTasksPage />); });
    expect(visibleTaskIds(container)).toEqual(["mobile-task-PACK-1"]);
    expect(container.textContent).not.toContain("Memuat tugas...");
    await act(async () => { pending.resolve({ tasks: [task("PACK-1")] }); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-PACK-1"]);
  });

  test("fetch failure wipes the current list and does not keep stale rows as valid", async () => {
    api.divisionTasks.mockResolvedValue({ tasks: [task("DEL-1")] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    act(() => root.unmount());

    api.divisionTasks.mockRejectedValue({ message: "Network Error" });
    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(container.textContent).toContain("Gagal memuat tugas.");
    expect(visibleTaskIds(container)).toEqual([]);

    const pending = deferred();
    api.divisionTasks.mockImplementation(() => pending.promise);
    act(() => root.unmount());
    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    expect(visibleTaskIds(container)).toEqual([]);
    expect(container.textContent).toContain("Memuat tugas...");
    await act(async () => { pending.resolve({ tasks: [task("DEL-9")] }); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-9"]);
  });

  test("remount + successful refresh keeps count, classification, and order parity", async () => {
    const fixture = {
      tasks: [
        task("TODAY-DONE", { completed_qty: 1, status: "selesai", status_label: "Selesai" }),
        task("TODAY-WAIT"),
        task("OLD-WAIT", { work_date: "2020-01-10" }),
      ],
    };
    api.divisionTasks.mockResolvedValue(fixture);
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    const countText = (id) => container.querySelector(`[data-testid="mobile-task-filter-${id}"] strong`).textContent;
    expect(countText("hariIni")).toBe("2");
    expect(countText("aktif")).toBe("2");
    expect(countText("menunggu")).toBe("2");
    expect(visibleTaskIds(container)).toEqual(["mobile-task-TODAY-DONE", "mobile-task-TODAY-WAIT"]);
    act(() => root.unmount());

    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(api.divisionTasks).toHaveBeenCalledTimes(2);
    expect(countText("hariIni")).toBe("2");
    expect(countText("aktif")).toBe("2");
    expect(countText("proses")).toBe("0");
    expect(countText("menunggu")).toBe("2");
    expect(visibleTaskIds(container)).toEqual(["mobile-task-TODAY-DONE", "mobile-task-TODAY-WAIT"]);
  });

  test("local permission loss does not hydrate retained delivery rows", async () => {
    api.divisionTasks.mockResolvedValue({ tasks: [task("DEL-1")] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-1"]);
    act(() => root.unmount());

    mockHasPerm.mockImplementation((p) => p === "packing.update");
    const pending = deferred();
    api.divisionTasks.mockImplementation(() => pending.promise);
    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    expect(visibleTaskIds(container)).toEqual([]);
    expect(container.textContent).not.toContain("PO-DEL-1");
    await act(async () => { pending.resolve({ tasks: [task("PACK-9")] }); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-PACK-9"]);
    expect(container.textContent).not.toContain("PO-DEL-1");
  });

  test("local auth loss does not hydrate retained rows", async () => {
    api.divisionTasks.mockResolvedValue({ tasks: [task("DEL-1")] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    act(() => root.unmount());

    mockAuthState.user = null;
    mockHasPerm.mockImplementation(() => false);
    const pending = deferred();
    api.divisionTasks.mockImplementation(() => pending.promise);
    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    expect(visibleTaskIds(container)).toEqual([]);
    expect(container.textContent).not.toContain("PO-DEL-1");
    expect(container.textContent).toContain("Tidak ada workspace operasional untuk akun ini.");
    expect(api.divisionTasks).toHaveBeenCalledTimes(1);
  });

  test("403 in-flight still shows last-good rows only while client still has the workspace perm", async () => {
    api.divisionTasks.mockResolvedValue({ tasks: [task("DEL-1")] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    act(() => root.unmount());

    const pending = deferred();
    api.divisionTasks.mockImplementation(() => pending.promise);
    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-1"]);
    expect(container.textContent).not.toContain("Memuat tugas...");

    await act(async () => {
      pending.reject({
        response: { status: 403, data: { detail: "Permission 'delivery.update' diperlukan" } },
      });
    });
    await flush();
    expect(container.textContent).toContain("Permission 'delivery.update' diperlukan");
    expect(visibleTaskIds(container)).toEqual([]);
  });

  test("401 in-flight still shows last-good rows only while client user is still present", async () => {
    api.divisionTasks.mockResolvedValue({ tasks: [task("DEL-1")] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    act(() => root.unmount());

    const pending = deferred();
    api.divisionTasks.mockImplementation(() => pending.promise);
    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-1"]);
    expect(container.textContent).not.toContain("Memuat tugas...");

    await act(async () => {
      pending.reject({
        response: { status: 401, data: { detail: "Sesi berakhir. Silakan login ulang." } },
      });
    });
    await flush();
    expect(container.textContent).toContain("Sesi berakhir");
    expect(visibleTaskIds(container)).toEqual([]);
  });

  test("late success or error from the previous division does not replace the latest list", async () => {
    const packing = deferred();
    const delivery = deferred();
    api.divisionTasks.mockImplementation((division) => (
      division === "packing" ? packing.promise : delivery.promise
    ));
    mockTasksRouter.search = "ws=packing";
    await act(async () => { root.render(<MobileTasksPage />); });
    mockTasksRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileTasksPage />); });
    await act(async () => { delivery.resolve({ tasks: [task("DEL-NEW")] }); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-NEW"]);

    await act(async () => { packing.resolve({ tasks: [task("PACK-OLD")] }); });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-NEW"]);
    expect(container.textContent).not.toContain("PO-PACK-OLD");

    const deliveryError = deferred();
    const packingLateError = deferred();
    api.divisionTasks.mockImplementation((division) => (
      division === "packing" ? packingLateError.promise : deliveryError.promise
    ));
    mockTasksRouter.search = "ws=packing";
    await act(async () => { root.render(<MobileTasksPage />); });
    mockTasksRouter.search = "ws=delivery";
    await act(async () => { root.render(<MobileTasksPage />); });
    await act(async () => { deliveryError.resolve({ tasks: [task("DEL-KEEP")] }); });
    await flush();
    await act(async () => {
      packingLateError.reject({ response: { status: 500, data: { detail: "packing gagal" } } });
    });
    await flush();
    expect(visibleTaskIds(container)).toEqual(["mobile-task-DEL-KEEP"]);
    expect(container.textContent).not.toContain("packing gagal");
    expect(container.textContent).not.toContain("Memuat tugas...");
  });

  test("Cetak and Upload Sampel do not refetch when division, user, and scope stay the same", async () => {
    mockHasPerm.mockImplementation((p) => ["operator.update", "operator.sample_upload"].includes(p));
    mockTasksRouter.search = "ws=print";
    api.divisionTasks.mockResolvedValue({ tasks: [task("OP-1")] });
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(api.divisionTasks).toHaveBeenCalledTimes(1);
    expect(api.divisionTasks).toHaveBeenCalledWith("operator", "mine");
    expect(visibleTaskIds(container)).toEqual(["mobile-task-OP-1"]);

    mockTasksRouter.search = "ws=sample";
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(api.divisionTasks).toHaveBeenCalledTimes(1);
    expect(visibleTaskIds(container)).toEqual(["mobile-task-OP-1"]);

    act(() => root.unmount());
    root = createRoot(container);
    await act(async () => { root.render(<MobileTasksPage />); });
    await flush();
    expect(api.divisionTasks).toHaveBeenCalledTimes(2);
    expect(api.divisionTasks).toHaveBeenLastCalledWith("operator", "mine");
  });
});


