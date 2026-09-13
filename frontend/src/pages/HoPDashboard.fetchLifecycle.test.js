/**
 * HoPDashboard request lifecycle: manual refresh must not double dashboardSummary.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import api from "../lib/api";
import HoPDashboard from "./HoPDashboard";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={typeof to === "string" ? to : "#"} {...props}>{children}</a>,
  useNavigate: () => jest.fn(),
}), { virtual: true });

jest.mock("../lib/auth", () => {
  const user = { role: "head_production", email: "hop@x.com", name: "HoP" };
  return {
    useAuth: () => ({
      user,
      loading: false,
      hasPerm: () => true,
    }),
  };
});

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    dashboardSummary: jest.fn(),
    reminders: jest.fn(),
    accurateStatus: jest.fn(),
  },
}));

jest.mock("../components/ActionCenter", () => () => <div data-testid="action-center-stub" />);
jest.mock("../components/ReminderList", () => () => <div data-testid="reminder-list" />);

global.IS_REACT_ACT_ENVIRONMENT = true;

const src = fs.readFileSync(path.join(__dirname, "HoPDashboard.jsx"), "utf8");
const actionCenterSrc = fs.readFileSync(
  path.join(__dirname, "../components/ActionCenter.jsx"),
  "utf8",
);

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const summaryPayload = {
  kpi: { total_active: 1, total_urgent: 0, due_today: 0, overdue: 0, deadline_h1: 0, deadline_h6: 0 },
  work_today: { total: 0 },
  shipments_today: { total: 0 },
  generated_at: "2026-09-13T00:00:00+00:00",
};

describe("HoPDashboard fetch lifecycle (source)", () => {
  test("load still fetches summary + reminders + accurate status", () => {
    const loadFn = src.slice(src.indexOf("const load = async"), src.indexOf("useEffect(() => {"));
    expect(loadFn).toMatch(/api\.dashboardSummary\(\)/);
    expect(loadFn).toMatch(/api\.reminders\(\)\.catch\(\(\) => null\)/);
    expect(loadFn).toMatch(/api\.accurateStatus\(\)\.catch\(\(\) => null\)/);
  });

  test("manual refresh calls load only — not refreshDash", () => {
    expect(src).toMatch(/data-testid="hop-refresh-button"/);
    expect(src).toMatch(/onClick=\{\(\) => \{ load\(\); \}\}/);
    expect(src).not.toMatch(/refreshDash\(\)/);
    expect(src).not.toMatch(/refreshNow:\s*refreshDash/);
  });

  test("30s summary auto-refresh remains independent", () => {
    expect(src).toMatch(/useAutoRefresh\(/);
    expect(src).toMatch(/intervalMs:\s*30_000/);
    expect(src).toMatch(/enabled:\s*!!data/);
    expect(src).toMatch(/\(\) => api\.dashboardSummary\(\)/);
  });

  test("Action Center source is untouched", () => {
    expect(actionCenterSrc).toMatch(/api\.dashboardActionItems\(\)/);
    expect(src).toMatch(/<ActionCenter \/>/);
  });
});

describe("HoPDashboard fetch lifecycle (runtime)", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.dashboardSummary.mockReset();
    api.reminders.mockReset();
    api.accurateStatus.mockReset();
    api.dashboardSummary.mockResolvedValue(summaryPayload);
    api.reminders.mockResolvedValue({ reminders: [], counts: {}, role_scope: "all" });
    api.accurateStatus.mockResolvedValue({ mode: "mock", message: "ok" });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("initial load requests dashboardSummary, reminders, and accurateStatus once each", async () => {
    await act(async () => {
      root.render(<HoPDashboard />);
    });
    await flush();
    expect(api.dashboardSummary).toHaveBeenCalledTimes(1);
    expect(api.reminders).toHaveBeenCalledTimes(1);
    expect(api.accurateStatus).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="hop-dashboard"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="action-center-stub"]')).toBeTruthy();
  });

  test("manual refresh requests dashboardSummary exactly once more, plus reminders and accurateStatus", async () => {
    await act(async () => {
      root.render(<HoPDashboard />);
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="hop-refresh-button"]').click();
    });
    await flush();
    expect(api.dashboardSummary).toHaveBeenCalledTimes(2);
    expect(api.reminders).toHaveBeenCalledTimes(2);
    expect(api.accurateStatus).toHaveBeenCalledTimes(2);
  });
});
