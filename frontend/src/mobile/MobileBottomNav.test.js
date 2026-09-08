import { act } from "react";
import { createRoot } from "react-dom/client";
import MobileBottomNav from "./MobileBottomNav";

const mockHasPerm = jest.fn(() => false);

jest.mock("react-router-dom", () => ({
  NavLink: ({ to, children, className, ...props }) => (
    <a href={to} className={className} {...props}>{children}</a>
  ),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: (perm) => mockHasPerm(perm) }),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const navIds = (container) => [...container.querySelectorAll(".mop-nav-btn")].map((el) => el.getAttribute("data-testid"));

describe("MobileBottomNav", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("authorized nav order is Tugas, PO, Jadwal Produksi, Notifikasi, Profil", async () => {
    await act(async () => {
      root.render(<MobileBottomNav pathname="/m/orders" />);
    });
    expect(navIds(container)).toEqual([
      "mobile-nav-tasks",
      "mobile-nav-orders",
      "mobile-nav-schedule",
      "mobile-nav-notifications",
      "mobile-nav-profile",
    ]);
    expect(container.textContent).toContain("Tugas");
    expect(container.textContent).toContain("PO");
    expect(container.textContent).toContain("Jadwal");
    expect(container.textContent).toContain("Produksi");
    expect(container.textContent).toContain("Notifikasi");
    expect(container.textContent).toContain("Profil");
    expect(container.querySelector('[data-testid="mobile-nav-orders"]').className).toContain("is-active");
    expect(container.querySelector('[data-testid="mobile-nav-schedule"]').className).not.toContain("is-active");
  });

  test("Jadwal Produksi navigates to /m/schedule", async () => {
    await act(async () => {
      root.render(<MobileBottomNav pathname="/m/orders" />);
    });
    expect(container.querySelector('[data-testid="mobile-nav-schedule"]').getAttribute("href")).toBe("/m/schedule");
  });

  test("PO still navigates to /m/orders", async () => {
    await act(async () => {
      root.render(<MobileBottomNav pathname="/m/tasks" />);
    });
    expect(container.querySelector('[data-testid="mobile-nav-orders"]').getAttribute("href")).toBe("/m/orders");
  });

  test("/m/schedule marks Jadwal Produksi active", async () => {
    await act(async () => {
      root.render(<MobileBottomNav pathname="/m/schedule" />);
    });
    expect(container.querySelector('[data-testid="mobile-nav-schedule"]').className).toContain("is-active");
    expect(container.querySelector('[data-testid="mobile-nav-tasks"]').className).not.toContain("is-active");
    expect(container.querySelector('[data-testid="mobile-nav-orders"]').className).not.toContain("is-active");
    expect(container.querySelector('[data-testid="mobile-nav-notifications"]').className).not.toContain("is-active");
    expect(container.querySelector('[data-testid="mobile-nav-profile"]').className).not.toContain("is-active");
  });

  test("unauthorized users do not see Jadwal Produksi", async () => {
    mockHasPerm.mockImplementation((p) => p === "operator.update");
    await act(async () => {
      root.render(<MobileBottomNav pathname="/m/orders" />);
    });
    expect(container.querySelector('[data-testid="mobile-nav-schedule"]')).toBeFalsy();
    expect(navIds(container)).toEqual([
      "mobile-nav-tasks",
      "mobile-nav-orders",
      "mobile-nav-notifications",
      "mobile-nav-profile",
    ]);
  });

  test("existing nav routes stay unchanged", async () => {
    await act(async () => {
      root.render(<MobileBottomNav pathname="/m/notifications" />);
    });
    expect(container.querySelector('[data-testid="mobile-nav-tasks"]').getAttribute("href")).toBe("/m/tasks");
    expect(container.querySelector('[data-testid="mobile-nav-orders"]').getAttribute("href")).toBe("/m/orders");
    expect(container.querySelector('[data-testid="mobile-nav-notifications"]').getAttribute("href")).toBe("/m/notifications");
    expect(container.querySelector('[data-testid="mobile-nav-profile"]').getAttribute("href")).toBe("/m/profile");
    expect(container.querySelector('[data-testid="mobile-nav-schedule"]').getAttribute("href")).toBe("/m/schedule");
  });
});
