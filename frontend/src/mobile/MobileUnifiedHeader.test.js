import { act } from "react";
import { createRoot } from "react-dom/client";
import MobileTopBar from "./MobileTopBar";
import MobileSidebar from "./MobileSidebar";
import OpsQueueHeader from "./components/OpsQueueHeader";
import { MobileNavProvider } from "./MobileNavContext";
import {
  MOBILE_HEADER_CHAT,
  MOBILE_HEADER_NOTIFICATIONS,
  visibleMobileNavItems,
} from "../lib/mobileWorkspaces";
import api from "../lib/api";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  NavLink: ({ to, children, className, onClick, ...props }) => (
    <a
      href={to}
      className={className}
      onClick={(e) => {
        e.preventDefault();
        onClick?.(e);
      }}
      {...props}
    >
      {children}
    </a>
  ),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { name: "Budi" },
    hasPerm: () => true,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    chatUnreadSummary: jest.fn(async () => ({ total_unread: 4 })),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

function Harness({ pathname = "/m/tasks", header }) {
  return (
    <MobileNavProvider>
      {header}
      <MobileSidebar pathname={pathname} />
    </MobileNavProvider>
  );
}

describe("PWA unified header + drawer", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.chatUnreadSummary.mockClear();
    api.chatUnreadSummary.mockResolvedValue({ total_unread: 4 });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const openDrawer = async () => {
    await act(async () => {
      container.querySelector('[data-testid="mobile-nav-menu"]').click();
    });
  };

  test("drawer omits Chat and Notifikasi and keeps Profil", async () => {
    await act(async () => {
      root.render(
        <Harness header={<MobileTopBar title="Tugas Saya" />} />,
      );
    });
    await openDrawer();
    expect(container.querySelector('[data-testid="mobile-nav-chat"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-nav-notifications"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-nav-profile"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-nav-profile"]').textContent).toContain("Profil");
    expect(container.querySelector('[data-testid="mobile-sidebar"]').textContent).not.toContain("Chat");
    expect(container.querySelector('[data-testid="mobile-sidebar"]').textContent).not.toContain("Notifikasi");
    const ids = visibleMobileNavItems(() => true).map((i) => i.id);
    expect(ids).not.toContain("chat");
    expect(ids).not.toContain("notifications");
    expect(ids).toContain("profile");
  });

  test("global header exposes Chat and Notification icons with existing routes and unread badge", async () => {
    await act(async () => {
      root.render(
        <Harness header={<MobileTopBar title="Tugas Saya" />} />,
      );
    });
    await act(async () => { await Promise.resolve(); });
    const chat = container.querySelector('[data-testid="mobile-header-chat"]');
    const notif = container.querySelector('[data-testid="mobile-header-notifications"]');
    expect(chat).toBeTruthy();
    expect(notif).toBeTruthy();
    expect(chat.getAttribute("href")).toBe(MOBILE_HEADER_CHAT.to);
    expect(notif.getAttribute("href")).toBe(MOBILE_HEADER_NOTIFICATIONS.to);
    expect(MOBILE_HEADER_CHAT.to).toBe("/m/chat");
    expect(MOBILE_HEADER_NOTIFICATIONS.to).toBe("/m/notifications");
    expect(container.querySelector('[data-testid="mobile-menu-chat-unread"]').textContent).toBe("4");
    const header = container.querySelector('[data-testid="mobile-topbar"]');
    expect(header.textContent).not.toMatch(/Eternal Packaging/i);
    expect(header.querySelector('[data-testid="mobile-topbar-kicker"]')).toBeFalsy();
    const title = container.querySelector('[data-testid="mobile-topbar-title"]');
    expect(header.querySelector(".mop-topbar-row").children[0].getAttribute("data-testid")).toBe("mobile-nav-menu");
    const actions = header.querySelector('[data-testid="mobile-header-actions"]');
    expect(actions).toBeTruthy();
    expect(actions.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  test("Jadwal Produksi and Jadwal Pengiriman compact headers share the same chrome", async () => {
    await act(async () => {
      root.render(
        <Harness
          pathname="/m/schedule"
          header={<OpsQueueHeader title="Jadwal Produksi" showBrand={false} subtitle="" />}
        />,
      );
    });
    await act(async () => { await Promise.resolve(); });
    const header = container.querySelector('[data-testid="mobile-topbar"]');
    expect(header.querySelector('[data-testid="mobile-topbar-title"]').textContent).toBe("Jadwal Produksi");
    expect(header.querySelector('[data-testid="mobile-nav-menu"]')).toBeTruthy();
    expect(header.querySelector('[data-testid="mobile-header-chat"]').getAttribute("href")).toBe("/m/chat");
    expect(header.querySelector('[data-testid="mobile-header-notifications"]').getAttribute("href")).toBe("/m/notifications");
    expect(header.textContent).not.toContain("ETERNAL PACKAGING");
    expect(header.querySelector(".ops-qh-brand")).toBeFalsy();

    await act(async () => {
      root.render(
        <Harness
          pathname="/m/delivery-schedule"
          header={<OpsQueueHeader title="Jadwal Pengiriman" showBrand={false} subtitle="" />}
        />,
      );
    });
    await act(async () => { await Promise.resolve(); });
    const header2 = container.querySelector('[data-testid="mobile-topbar"]');
    expect(header2.querySelector('[data-testid="mobile-topbar-title"]').textContent).toBe("Jadwal Pengiriman");
    expect(header2.querySelector('[data-testid="mobile-header-chat"]')).toBeTruthy();
    expect(header2.querySelector('[data-testid="mobile-header-notifications"]')).toBeTruthy();
    expect(header2.className).toMatch(/ops-queue-header/);
    expect(header2.className).toMatch(/is-compact/);
  });

  test("PO page header uses the same hamburger + title + actions structure", async () => {
    await act(async () => {
      root.render(
        <Harness pathname="/m/orders" header={<MobileTopBar title="PO" />} />,
      );
    });
    const header = container.querySelector('[data-testid="mobile-topbar"]');
    expect(header.querySelector('[data-testid="mobile-topbar-title"]').textContent).toBe("PO");
    expect(header.querySelector('[data-testid="mobile-nav-menu"]')).toBeTruthy();
    expect(header.querySelector('[data-testid="mobile-header-chat"]')).toBeTruthy();
    expect(header.querySelector('[data-testid="mobile-header-notifications"]')).toBeTruthy();
    expect(header.className).toMatch(/mop-topbar--unified/);
    expect(header.textContent).not.toMatch(/Eternal Packaging/i);
  });
});
