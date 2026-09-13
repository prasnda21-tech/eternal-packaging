import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
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
    const ids = [...container.querySelectorAll("a.mop-card")].map((el) => el.getAttribute("data-testid"));
    expect(ids).toEqual(["mobile-task-C", "mobile-task-D", "mobile-task-A", "mobile-task-B"]);
    expect(ids).toHaveLength(4);
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
    const ids = [...container.querySelectorAll("a.mop-card")].map((el) => el.getAttribute("data-testid"));
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
    const cards = [...container.querySelectorAll("a.mop-card")];
    expect(cards.map((el) => el.getAttribute("data-testid"))).toEqual(["mobile-task-U", "mobile-task-H", "mobile-task-N"]);
    expect(cards[0].querySelector('[data-testid="mobile-task-screen"]').textContent).toContain("Screen Besar");
    expect(cards[1].querySelector('[data-testid="mobile-task-screen"]').textContent).toContain("Screen Kecil");
    expect(cards[2].querySelector('[data-testid="mobile-task-screen"]').textContent).toContain("Screen Besar");
  });
});
