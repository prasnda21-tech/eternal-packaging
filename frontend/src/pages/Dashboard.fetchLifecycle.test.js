/**
 * Dashboard request lifecycle: statusTab must not refetch reminders / today.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import api from "../lib/api";
import Dashboard from "./Dashboard";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={typeof to === "string" ? to : "#"} {...props}>{children}</a>,
  useSearchParams: () => [new URLSearchParams(""), jest.fn()],
  useLocation: () => ({ pathname: "/", search: "", state: null }),
  useNavigate: () => jest.fn(),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { role: "admin", name: "Admin" },
    hasPerm: () => true,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listOrders: jest.fn(),
    reminders: jest.fn(),
    dashboardToday: jest.fn(),
  },
}));

jest.mock("../components/ActionCenter", () => () => null);
jest.mock("../components/TodayActivityCards", () => ({ data }) => (
  <div data-testid="today-activity">{data ? "today" : ""}</div>
));
jest.mock("../components/ReminderList", () => () => <div data-testid="reminder-list" />);
jest.mock("../components/POCard", () => ({ order }) => <div>{order.po_number}</div>);

global.IS_REACT_ACT_ENVIRONMENT = true;

const src = fs.readFileSync(path.join(__dirname, "Dashboard.jsx"), "utf8");

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const ordersPayload = (status) => ({
  orders: [
    {
      id: `po-${status}`,
      po_number: `EP-${status}`,
      deadline: new Date().toISOString(),
      priority: "normal",
      current_stage: 1,
    },
  ],
  counts: { aktif: 1, selesai: 0, cancelled: 0, hold: 1 },
  stage_counts: {},
});

describe("Dashboard fetch lifecycle (source)", () => {
  test("orders effect depends on statusTab; supplemental effect is mount-only", () => {
    expect(src).toMatch(/const loadOrders = async/);
    expect(src).toMatch(/const loadSupplemental = async/);
    expect(src).toMatch(/api\.listOrders\(status,\s*\{\s*view:\s*"dashboard"\s*\}\)/);
    expect(src).toMatch(/api\.reminders\(\)\.catch\(\(\) => null\)/);
    expect(src).toMatch(/api\.dashboardToday\(\)\.catch\(\(\) => null\)/);
    expect(src).toMatch(/loadOrders\(statusTab\)/);
    expect(src).toMatch(/loadSupplemental\(\);/);
    expect(src).toMatch(/\}, \[stageIsValid, stageSlug, statusTab\]\);/);
    expect(src).toMatch(/useEffect\(\(\) => \{\s*loadSupplemental\(\);/s);
    const afterSupplemental = src.slice(src.indexOf("loadSupplemental();"));
    expect(afterSupplemental).toMatch(/\},\s*\[\]\);/);
  });

  test("manual refresh still calls full load of orders + reminders + today", () => {
    expect(src).toMatch(/onClick=\{\(\) => load\(statusTab\)\}/);
    expect(src).toMatch(/data-testid="refresh-orders-button"/);
    const loadStart = src.indexOf("const load = async");
    const loadEnd = src.indexOf("useEffect(() => {", loadStart);
    const loadFn = src.slice(loadStart, loadEnd);
    expect(loadFn).toMatch(/api\.listOrders/);
    expect(loadFn).toMatch(/api\.reminders/);
    expect(loadFn).toMatch(/api\.dashboardToday/);
  });

  test("statusTab click only updates tab state; orders reload is via statusTab effect", () => {
    expect(src).toMatch(/onClick=\{\(\) => setStatusTab\(t\.key\)\}/);
    expect(src).not.toMatch(/onClick=\{\(\) => \{[\s\S]*loadSupplemental/);
  });
});

describe("Dashboard fetch lifecycle (runtime)", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.listOrders.mockReset();
    api.reminders.mockReset();
    api.dashboardToday.mockReset();
    api.listOrders.mockImplementation((status) => Promise.resolve(ordersPayload(status)));
    api.reminders.mockResolvedValue({ reminders: [], counts: {}, role_scope: "all" });
    api.dashboardToday.mockResolvedValue({ work: [], shipments: [] });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("initial load requests listOrders dashboard + reminders + dashboardToday once each", async () => {
    await act(async () => {
      root.render(<Dashboard />);
    });
    await flush();
    expect(api.listOrders).toHaveBeenCalledTimes(1);
    expect(api.listOrders).toHaveBeenCalledWith("aktif", { view: "dashboard" });
    expect(api.reminders).toHaveBeenCalledTimes(1);
    expect(api.dashboardToday).toHaveBeenCalledTimes(1);
  });

  test("changing statusTab refetches only listOrders, not reminders or dashboardToday", async () => {
    await act(async () => {
      root.render(<Dashboard />);
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="nav-orders"]').click();
    });
    await flush();
    expect(api.listOrders).toHaveBeenCalledTimes(1);
    expect(api.reminders).toHaveBeenCalledTimes(1);
    expect(api.dashboardToday).toHaveBeenCalledTimes(1);

    await act(async () => {
      container.querySelector('[data-testid="po-status-tab-hold"]').click();
    });
    await flush();
    expect(api.listOrders).toHaveBeenCalledTimes(2);
    expect(api.listOrders).toHaveBeenLastCalledWith("hold", { view: "dashboard" });
    expect(api.reminders).toHaveBeenCalledTimes(1);
    expect(api.dashboardToday).toHaveBeenCalledTimes(1);
  });

  test("incomplete reminders show the read cap and hide the matched population", async () => {
    api.reminders.mockResolvedValue({
      reminders: [],
      counts: { overdue: 0, warn_6h: 0, warn_h1: 0 },
      completeness: "incomplete",
      read_limit: 1000,
      matched_count: 1800,
    });
    await act(async () => {
      root.render(<Dashboard />);
    });
    await flush();
    const note = container.querySelector("[data-testid='reminder-incomplete']");
    expect(note.textContent).toContain("Reminder belum lengkap");
    expect(note.textContent).toContain("1000");
    expect(note.textContent).not.toContain("1800");
  });

  test("complete reminders omit the incomplete note", async () => {
    await act(async () => {
      root.render(<Dashboard />);
    });
    await flush();
    expect(container.querySelector("[data-testid='reminder-incomplete']")).toBeNull();
  });

  test("manual refresh refetches orders, reminders, and dashboardToday", async () => {
    await act(async () => {
      root.render(<Dashboard />);
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="refresh-orders-button"]').click();
    });
    await flush();
    expect(api.listOrders).toHaveBeenCalledTimes(2);
    expect(api.reminders).toHaveBeenCalledTimes(2);
    expect(api.dashboardToday).toHaveBeenCalledTimes(2);
  });
});
