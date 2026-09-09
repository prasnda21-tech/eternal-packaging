import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import MobileTasksPage from "./MobileTasksPage";

const mockHasPerm = jest.fn(() => false);

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { name: "Budi", email: "budi@x.com", role: "operator" },
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
    mockHasPerm.mockImplementation((p) => p === "operator.update");
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
});
