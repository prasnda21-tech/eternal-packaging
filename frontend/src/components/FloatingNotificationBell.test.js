import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { POLL_INTERVAL_MS, storageKeyForUser } from "../lib/floatingNotificationBell";
import FloatingNotificationBell from "./FloatingNotificationBell";

const mockNavigate = jest.fn();
let mockPathname = "/dashboard";
let mockSearch = "";

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: mockPathname, search: mockSearch }),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "user-1", name: "Designer", role: "designer" },
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listNotifications: jest.fn(),
    markNotificationRead: jest.fn(),
  },
}));

jest.mock("lucide-react", () => ({
  Bell: (props) => <span data-testid="bell-icon" {...props} />,
}));

if (typeof global.PointerEvent === "undefined") {
  global.PointerEvent = class PointerEvent extends MouseEvent {
    constructor(type, props = {}) {
      super(type, props);
      Object.defineProperty(this, "pointerId", {
        configurable: true,
        get: () => props.pointerId ?? 1,
      });
    }
  };
}

global.IS_REACT_ACT_ENVIRONMENT = true;

function notif(partial) {
  return {
    id: partial.id,
    title: partial.title || `N-${partial.id}`,
    body: partial.body || "Body",
    po_number: partial.po_number || "PO-1",
    decision: partial.decision || "approved",
    event_type: partial.event_type || "customer_design_approved",
    deep_link: partial.deep_link || `/m/orders/${partial.order_id || "ord-1"}`,
    order_id: partial.order_id || "ord-1",
    created_at: partial.created_at || "2026-09-11T10:00:00Z",
    is_read: partial.is_read ?? false,
    read_at: partial.read_at ?? null,
  };
}

function firePointer(el, type, props) {
  el.dispatchEvent(new PointerEvent(type, { bubbles: true, button: 0, ...props }));
}

describe("FloatingNotificationBell", () => {
  let container;
  let root;

  beforeEach(() => {
    jest.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockNavigate.mockReset();
    mockPathname = "/dashboard";
    mockSearch = "";
    api.listNotifications.mockReset();
    api.markNotificationRead.mockReset();
    window.localStorage.clear();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.useRealTimers();
  });

  const renderBell = async () => {
    await act(async () => {
      root.render(<FloatingNotificationBell />);
    });
    await act(async () => {
      await Promise.resolve();
    });
  };

  test("CASE 1 — zero unread: bell visible, badge absent", async () => {
    api.listNotifications.mockResolvedValue({
      notifications: [notif({ id: "r1", is_read: true, read_at: "2026-01-01T00:00:00Z" })],
    });
    await renderBell();
    expect(container.querySelector('[data-testid="web-floating-notification-bell"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="web-notif-badge"]')).toBeFalsy();
  });

  test("CASE 2 — one unread badge is 1", async () => {
    api.listNotifications.mockResolvedValue({
      notifications: [notif({ id: "u1", is_read: false })],
    });
    await renderBell();
    expect(container.querySelector('[data-testid="web-notif-badge"]').textContent).toBe("1");
  });

  test("CASE 3 — two unread badge is 2", async () => {
    api.listNotifications.mockResolvedValue({
      notifications: [notif({ id: "u1" }), notif({ id: "u2" })],
    });
    await renderBell();
    expect(container.querySelector('[data-testid="web-notif-badge"]').textContent).toBe("2");
  });

  test("CASE 4 — mixed unread derives exact count", async () => {
    api.listNotifications.mockResolvedValue({
      notifications: [
        notif({ id: "u1" }),
        notif({ id: "u2" }),
        notif({ id: "r1", is_read: true, read_at: "x" }),
        notif({ id: "r2", is_read: true, read_at: "y" }),
        notif({ id: "r3", is_read: true, read_at: "z" }),
      ],
    });
    await renderBell();
    expect(container.querySelector('[data-testid="web-notif-badge"]').textContent).toBe("2");
  });

  test("CASE 5/6 — open/close panel does not mark-read", async () => {
    api.listNotifications.mockResolvedValue({ notifications: [notif({ id: "u1" })] });
    await renderBell();
    const bell = container.querySelector('[data-testid="web-notif-bell-button"]');
    await act(async () => {
      firePointer(bell, "pointerdown", { clientX: 10, clientY: 10, pointerId: 1 });
      firePointer(bell, "pointerup", { clientX: 10, clientY: 10, pointerId: 1 });
    });
    expect(container.querySelector('[data-testid="web-notif-panel"]')).toBeTruthy();
    expect(api.markNotificationRead).not.toHaveBeenCalled();

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(container.querySelector('[data-testid="web-notif-panel"]')).toBeFalsy();
    expect(api.markNotificationRead).not.toHaveBeenCalled();
  });

  test("CASE 7 — unread item click marks read and navigates desktop /po/:id (not mobile deep_link)", async () => {
    api.listNotifications.mockResolvedValue({
      notifications: [notif({ id: "u1", deep_link: "/m/orders/ord-99", order_id: "ord-99" })],
    });
    api.markNotificationRead.mockResolvedValue({
      notification: { id: "u1", is_read: true, read_at: "2026-09-11T12:00:00Z" },
    });
    await renderBell();
    const bell = container.querySelector('[data-testid="web-notif-bell-button"]');
    await act(async () => {
      firePointer(bell, "pointerdown", { clientX: 5, clientY: 5, pointerId: 1 });
      firePointer(bell, "pointerup", { clientX: 5, clientY: 5, pointerId: 1 });
    });
    await act(async () => {
      container.querySelector('[data-testid="web-notif-item-u1"]').click();
      await Promise.resolve();
    });
    expect(api.markNotificationRead).toHaveBeenCalledWith("u1");
    expect(mockNavigate).toHaveBeenCalledWith("/po/ord-99");
    expect(mockNavigate).not.toHaveBeenCalledWith("/m/orders/ord-99");
    expect(container.querySelector('[data-testid="web-notif-badge"]')).toBeFalsy();
  });

  test("CASE 8 — read item click does not call mark-read and still opens desktop PO", async () => {
    api.listNotifications.mockResolvedValue({
      notifications: [
        notif({ id: "u1" }),
        notif({ id: "r1", is_read: true, read_at: "x", deep_link: "/m/orders/ord-r", order_id: "ord-r" }),
      ],
    });
    await renderBell();
    expect(container.querySelector('[data-testid="web-notif-badge"]').textContent).toBe("1");
    const bell = container.querySelector('[data-testid="web-notif-bell-button"]');
    await act(async () => {
      firePointer(bell, "pointerdown", { clientX: 5, clientY: 5, pointerId: 1 });
      firePointer(bell, "pointerup", { clientX: 5, clientY: 5, pointerId: 1 });
    });
    await act(async () => {
      container.querySelector('[data-testid="web-notif-item-r1"]').click();
      await Promise.resolve();
    });
    expect(api.markNotificationRead).not.toHaveBeenCalled();
    expect(mockNavigate).toHaveBeenCalledWith("/po/ord-r");
    expect(mockNavigate).not.toHaveBeenCalledWith("/m/orders/ord-r");
  });

  test("CASE 9 — polling updates badge without remount", async () => {
    api.listNotifications
      .mockResolvedValueOnce({ notifications: [notif({ id: "u1" })] })
      .mockResolvedValue({ notifications: [notif({ id: "u1" }), notif({ id: "u2" })] });
    await renderBell();
    expect(container.querySelector('[data-testid="web-notif-badge"]').textContent).toBe("1");
    await act(async () => {
      jest.advanceTimersByTime(POLL_INTERVAL_MS);
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="web-notif-badge"]').textContent).toBe("2");
  });

  test("CASE 10 — visibility refresh triggers list fetch", async () => {
    api.listNotifications.mockResolvedValue({ notifications: [] });
    await renderBell();
    const calls = api.listNotifications.mock.calls.length;
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(api.listNotifications.mock.calls.length).toBeGreaterThan(calls);
  });

  test("CASE 10b — hidden document does not periodic-poll; visible return refreshes", async () => {
    let visibility = "visible";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => visibility,
    });
    api.listNotifications.mockResolvedValue({ notifications: [] });
    await renderBell();
    const afterMount = api.listNotifications.mock.calls.length;
    visibility = "hidden";
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(POLL_INTERVAL_MS);
      await Promise.resolve();
    });
    expect(api.listNotifications.mock.calls.length).toBe(afterMount);
    visibility = "visible";
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(api.listNotifications.mock.calls.length).toBeGreaterThan(afterMount);
  });

  test("CASE 11 — unmount clears interval loop", async () => {
    api.listNotifications.mockResolvedValue({ notifications: [] });
    await renderBell();
    const clearSpy = jest.spyOn(window, "clearInterval");
    await act(async () => {
      root.unmount();
    });
    expect(clearSpy).toHaveBeenCalled();
    clearSpy.mockRestore();
    root = createRoot(container);
  });

  test("CASE 12/13 — drag moves bell without opening panel; tiny move opens panel", async () => {
    api.listNotifications.mockResolvedValue({ notifications: [] });
    await renderBell();
    const bell = container.querySelector('[data-testid="web-notif-bell-button"]');
    const rootEl = container.querySelector('[data-testid="web-floating-notification-bell"]');
    const startX = Number(rootEl.getAttribute("data-x"));
    const startY = Number(rootEl.getAttribute("data-y"));

    // Drag left/up so position changes even when default is already near right/bottom clamp.
    await act(async () => {
      firePointer(bell, "pointerdown", { clientX: 200, clientY: 200, pointerId: 7 });
    });
    await act(async () => {
      firePointer(bell, "pointermove", { clientX: 120, clientY: 140, pointerId: 7 });
    });
    await act(async () => {
      firePointer(bell, "pointerup", { clientX: 120, clientY: 140, pointerId: 7 });
    });
    expect(container.querySelector('[data-testid="web-notif-panel"]')).toBeFalsy();
    expect(Number(rootEl.getAttribute("data-x"))).toBeLessThan(startX);
    expect(Number(rootEl.getAttribute("data-y"))).toBeLessThan(startY);

    await act(async () => {
      firePointer(bell, "pointerdown", { clientX: 10, clientY: 10, pointerId: 8 });
      firePointer(bell, "pointermove", { clientX: 12, clientY: 11, pointerId: 8 });
      firePointer(bell, "pointerup", { clientX: 12, clientY: 11, pointerId: 8 });
    });
    expect(container.querySelector('[data-testid="web-notif-panel"]')).toBeTruthy();
  });

  test("CASE 14 — drag end persists position for next mount", async () => {
    api.listNotifications.mockResolvedValue({ notifications: [] });
    await renderBell();
    const bell = container.querySelector('[data-testid="web-notif-bell-button"]');
    await act(async () => {
      firePointer(bell, "pointerdown", { clientX: 200, clientY: 200, pointerId: 3 });
    });
    await act(async () => {
      firePointer(bell, "pointermove", { clientX: 80, clientY: 90, pointerId: 3 });
    });
    await act(async () => {
      firePointer(bell, "pointerup", { clientX: 80, clientY: 90, pointerId: 3 });
    });
    const savedX = Number(container.querySelector('[data-testid="web-floating-notification-bell"]').getAttribute("data-x"));
    const savedY = Number(container.querySelector('[data-testid="web-floating-notification-bell"]').getAttribute("data-y"));
    expect(window.localStorage.getItem(storageKeyForUser("user-1"))).toContain(String(savedX));

    await act(async () => root.unmount());
    root = createRoot(container);
    await renderBell();
    const restored = container.querySelector('[data-testid="web-floating-notification-bell"]');
    expect(Number(restored.getAttribute("data-x"))).toBe(savedX);
    expect(Number(restored.getAttribute("data-y"))).toBe(savedY);
  });

  test("CASE 17 — polling error does not crash shell", async () => {
    api.listNotifications.mockRejectedValue(new Error("network"));
    await renderBell();
    expect(container.querySelector('[data-testid="web-floating-notification-bell"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="web-notif-badge"]')).toBeFalsy();
  });

  test("mark-read failure does not navigate or clear unread", async () => {
    api.listNotifications.mockResolvedValue({
      notifications: [notif({ id: "u1", deep_link: "/m/orders/ord-1" })],
    });
    api.markNotificationRead.mockRejectedValue(new Error("fail"));
    await renderBell();
    const bell = container.querySelector('[data-testid="web-notif-bell-button"]');
    await act(async () => {
      firePointer(bell, "pointerdown", { clientX: 1, clientY: 1, pointerId: 1 });
      firePointer(bell, "pointerup", { clientX: 1, clientY: 1, pointerId: 1 });
    });
    await act(async () => {
      container.querySelector('[data-testid="web-notif-item-u1"]').click();
      await Promise.resolve();
    });
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="web-notif-badge"]').textContent).toBe("1");
  });

  test("CASE 13 existing bell renders canonical SJ-created notification", async () => {
    api.listNotifications.mockResolvedValue({
      notifications: [notif({
        id: "sj-h1",
        title: "Surat Jalan dibuat — Helper",
        body: "PO PO-SJ-1 / PT Kirim. SJ SJ-001.",
        po_number: "PO-SJ-1",
        decision: "helper",
        event_type: "surat_jalan_created",
        deep_link: "/m/orders/ord-sj-1",
        order_id: "ord-sj-1",
      })],
    });
    await renderBell();
    expect(container.querySelector('[data-testid="web-notif-badge"]').textContent).toBe("1");
    const bell = container.querySelector('[data-testid="web-notif-bell-button"]');
    await act(async () => {
      firePointer(bell, "pointerdown", { clientX: 1, clientY: 1, pointerId: 1 });
      firePointer(bell, "pointerup", { clientX: 1, clientY: 1, pointerId: 1 });
    });
    const row = container.querySelector('[data-testid="web-notif-item-sj-h1"]');
    expect(row).toBeTruthy();
    expect(row.textContent).toContain("Surat Jalan dibuat — Helper");
    expect(row.textContent).toContain("SJ SJ-001");
    expect(row.textContent).toContain("PO-SJ-1");
  });
});
