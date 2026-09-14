import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { wibDateKey } from "../lib/suratJalan";
import MobileTasksPage from "./MobileTasksPage";

const mockHasPerm = jest.fn(() => false);
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
    user: { id: "u-driver", name: "Budi", email: "budi@x.com", role: "operator" },
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
