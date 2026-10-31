import { act } from "react";
import { createRoot } from "react-dom/client";
import ActiveUsersCard, { activityLabel, fullActivity } from "./ActiveUsersCard";
import api from "../../lib/api";
import { financeWidgetAllowed, FINANCE_WIDGETS, PENDING_WIDGETS } from "./financeWidgetCatalog";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { activeUsers: jest.fn() } }));
global.IS_REACT_ACT_ENVIRONMENT = true;
const now = "2026-10-31T08:00:00+00:00";
const body = {
  rows: [{ id: "u1", name: "User Satu", last_seen_at: now, last_active_at: "2026-10-31T07:58:00+00:00" },
    { id: "u2", name: "User Dua", last_seen_at: null, last_active_at: null }],
  total: 51, page: 1, page_size: 50, server_time: now, online_timeout_seconds: 90,
};
let host, root;
beforeEach(() => {
  jest.useFakeTimers(); api.activeUsers.mockReset();
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); jest.useRealTimers(); });
const render = async () => { await act(async () => { root.render(<ActiveUsersCard />); }); };

test("shows account name, real online state and WIB activity; missing remains unknown", async () => {
  api.activeUsers.mockResolvedValue(body); await render();
  expect(host.textContent).toContain("User Satu");
  expect(host.textContent).toContain("● Online");
  expect(host.textContent).toContain("○ Offline");
  expect(host.textContent).toContain("2 menit lalu");
  expect(host.textContent).toContain("Belum tercatat");
  expect(host.textContent).toContain("WIB");
  expect(api.activeUsers).toHaveBeenCalledWith({ page: 1, page_size: 50 });
});

test("paging and polling are bounded and cleanup removes timers", async () => {
  api.activeUsers.mockResolvedValue(body); await render();
  await act(async () => { [...host.querySelectorAll("button")].find(b => b.textContent === "Berikutnya").click(); });
  expect(api.activeUsers).toHaveBeenLastCalledWith({ page: 2, page_size: 50 });
  await act(async () => { jest.advanceTimersByTime(30000); });
  expect(api.activeUsers).toHaveBeenCalledTimes(3);
  act(() => root.unmount()); root = { unmount() {} };
  jest.advanceTimersByTime(60000); expect(api.activeUsers).toHaveBeenCalledTimes(3);
});

test("connection failure removes stale Online label, permission revocation clears names", async () => {
  api.activeUsers.mockResolvedValueOnce(body).mockRejectedValueOnce(new Error("offline")).mockRejectedValue({ response: { status: 403 } });
  await render();
  await act(async () => { jest.advanceTimersByTime(30000); });
  expect(host.textContent).toContain("Gagal memuat");
  expect(host.textContent).not.toContain("Online");
  await act(async () => { jest.advanceTimersByTime(30000); });
  expect(host.textContent).toContain("Tidak ada izin");
  expect(host.textContent).not.toContain("User Satu");
});

test("timestamp formatting and permission catalog match the actual data contract", () => {
  expect(fullActivity(now)).toContain("15.00");
  expect(activityLabel(null, Date.parse(now))).toBe("Belum tercatat");
  expect(financeWidgetAllowed("active_users", p => p === "users.view")).toBe(true);
  expect(financeWidgetAllowed("active_users", p => p === "dashboard.finance.view")).toBe(false);
  expect(FINANCE_WIDGETS.some(w => w.id === "active_users")).toBe(true);
  expect(PENDING_WIDGETS.some(w => w.id === "active_users")).toBe(false);
});
