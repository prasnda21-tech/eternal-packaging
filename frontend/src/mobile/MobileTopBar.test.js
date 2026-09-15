import { act } from "react";
import { createRoot } from "react-dom/client";
import MobileTopBar from "./MobileTopBar";
import { MobileNavProvider } from "./MobileNavContext";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ user: { name: "QA" }, hasPerm: () => true }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    chatUnreadSummary: jest.fn(async () => ({ total_unread: 0 })),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("MobileTopBar title + meta hierarchy", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("renders customer primary title and PO secondary meta", () => {
    act(() => {
      root.render(
        <MobileNavProvider>
          <MobileTopBar
            kicker="Tugas Saya › Packing"
            title="TEST_QA reimport"
            meta="PO-QA15-DFAC2C"
            onBack={() => {}}
          />
        </MobileNavProvider>,
      );
    });
    expect(container.querySelector('[data-testid="mobile-topbar-kicker"]').textContent).toBe("Tugas Saya › Packing");
    expect(container.querySelector('[data-testid="mobile-topbar-title"]').tagName).toBe("H1");
    expect(container.querySelector('[data-testid="mobile-topbar-title"]').textContent).toBe("TEST_QA reimport");
    expect(container.querySelector('[data-testid="mobile-topbar-meta"]').textContent).toBe("PO-QA15-DFAC2C");
    expect(container.querySelector('[data-testid="mobile-back"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-nav-menu"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-header-chat"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-header-notifications"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-topbar"]').textContent).not.toMatch(/Eternal Packaging/i);
  });

  test("title-only pages stay unchanged without meta row overflow", () => {
    act(() => {
      root.render(
        <MobileNavProvider>
          <MobileTopBar title="Jadwal Pengiriman" />
        </MobileNavProvider>,
      );
    });
    expect(container.querySelector('[data-testid="mobile-topbar-title"]').textContent).toBe("Jadwal Pengiriman");
    expect(container.querySelector('[data-testid="mobile-topbar-meta"]')).toBeFalsy();
  });

  test("shared header omits Eternal Packaging brand kicker", async () => {
    act(() => {
      root.render(
        <MobileNavProvider>
          <MobileTopBar kicker="ETERNAL PACKAGING" title="Profil" />
        </MobileNavProvider>,
      );
    });
    expect(container.querySelector('[data-testid="mobile-topbar-kicker"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-topbar"]').textContent).not.toMatch(/Eternal Packaging/i);
    expect(container.querySelector('[data-testid="mobile-header-chat"]').getAttribute("href")).toBe("/m/chat");
    expect(container.querySelector('[data-testid="mobile-header-notifications"]').getAttribute("href")).toBe("/m/notifications");
  });
});
