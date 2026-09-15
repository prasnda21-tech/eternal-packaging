import { act } from "react";
import { createRoot } from "react-dom/client";
import MobileTopBar from "./MobileTopBar";
import MobileSidebar from "./MobileSidebar";
import { MobileNavProvider } from "./MobileNavContext";
import MobileOperationalLayout from "./MobileOperationalLayout";

const mockHasPerm = jest.fn(() => false);

jest.mock("react-router-dom", () => ({
  Outlet: () => <div data-testid="outlet" />,
  useLocation: () => ({ pathname: "/m/orders" }),
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
  useAuth: () => ({ hasPerm: (perm) => mockHasPerm(perm) }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    chatUnreadSummary: jest.fn(async () => ({ total_unread: 0 })),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

function MenuHarness({ pathname = "/m/orders" }) {
  return (
    <MobileNavProvider>
      <MobileTopBar title="Test" />
      <MobileSidebar pathname={pathname} />
    </MobileNavProvider>
  );
}

describe("PWA left sidebar menu", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "nav.delivery_schedule" || p === "chat.use");
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("menu button top-left opens left sidebar", async () => {
    await act(async () => {
      root.render(<MenuHarness />);
    });
    const btn = container.querySelector('[data-testid="mobile-nav-menu"]');
    expect(btn).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-sidebar"]')).toBeFalsy();
    await act(async () => {
      btn.click();
    });
    const sidebar = container.querySelector('[data-testid="mobile-sidebar"]');
    expect(sidebar).toBeTruthy();
    expect(sidebar.className).toContain("mop-sidebar");
    expect(container.querySelector('[data-testid="mobile-nav-menu-popup"]')).toBeFalsy();
  });

  test("sidebar lists destinations including Jadwal Produksi and Jadwal Pengiriman", async () => {
    await act(async () => {
      root.render(<MenuHarness />);
    });
    await act(async () => {
      container.querySelector('[data-testid="mobile-nav-menu"]').click();
    });
    expect(container.querySelector('[data-testid="mobile-nav-tasks"]').textContent).toContain("Tugas Saya");
    expect(container.querySelector('[data-testid="mobile-nav-orders"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-nav-schedule"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-nav-deliverySchedule"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-nav-chat"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-nav-notifications"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-nav-profile"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-header-chat"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-header-notifications"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-nav-deliverySchedule"]').getAttribute("href")).toBe("/m/delivery-schedule");
  });

  test("outside tap and route selection close sidebar", async () => {
    await act(async () => {
      root.render(<MenuHarness />);
    });
    await act(async () => {
      container.querySelector('[data-testid="mobile-nav-menu"]').click();
    });
    expect(container.querySelector('[data-testid="mobile-sidebar"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="mobile-sidebar-backdrop"]').click();
    });
    expect(container.querySelector('[data-testid="mobile-sidebar"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="mobile-nav-menu"]').click();
    });
    await act(async () => {
      container.querySelector('[data-testid="mobile-nav-tasks"]').click();
    });
    expect(container.querySelector('[data-testid="mobile-sidebar"]')).toBeFalsy();
  });

  test("layout uses sidebar provider, not bottom-sheet nav", async () => {
    await act(async () => {
      root.render(<MobileOperationalLayout />);
    });
    expect(container.querySelector('[data-testid="mobile-operational-root"]').className).toContain("mop-root--sidebar");
    expect(container.querySelector('[data-testid="mobile-bottom-nav"]')).toBeFalsy();
  });
});
