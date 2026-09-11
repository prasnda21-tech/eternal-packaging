import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import MobileNotificationsPage from "./MobileNotificationsPage";

const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listNotifications: jest.fn(),
    markNotificationRead: jest.fn(),
  },
}));

jest.mock("./MobileTopBar", () => ({
  __esModule: true,
  default: ({ title }) => <div data-testid="mobile-topbar">{title}</div>,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("MobileNotificationsPage regression", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockNavigate.mockReset();
    api.listNotifications.mockReset();
    api.markNotificationRead.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("lists notifications and opens deep_link after mark-read", async () => {
    api.listNotifications.mockResolvedValue({
      notifications: [
        {
          id: "n1",
          title: "Design disetujui",
          body: "Customer menyetujui",
          po_number: "PO-9",
          decision: "approved",
          deep_link: "/m/orders/ord-9",
          is_read: false,
          read_at: null,
          created_at: "2026-09-11T10:00:00Z",
        },
      ],
    });
    api.markNotificationRead.mockResolvedValue({
      notification: { id: "n1", is_read: true, read_at: "2026-09-11T11:00:00Z" },
    });

    await act(async () => {
      root.render(<MobileNotificationsPage />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.querySelector('[data-testid="mobile-notifications-page"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-notification-n1"]')).toBeTruthy();

    await act(async () => {
      container.querySelector('[data-testid="mobile-notification-n1"]').click();
      await Promise.resolve();
    });
    expect(api.markNotificationRead).toHaveBeenCalledWith("n1");
    expect(mockNavigate).toHaveBeenCalledWith("/m/orders/ord-9");
  });

  test("source still uses shared notification APIs", () => {
    const src = fs.readFileSync(path.join(__dirname, "MobileNotificationsPage.jsx"), "utf8");
    expect(src).toMatch(/listNotifications/);
    expect(src).toMatch(/markNotificationRead/);
    expect(src).toMatch(/deep_link/);
  });
});
