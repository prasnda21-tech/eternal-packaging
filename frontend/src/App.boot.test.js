import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import AppBootScreen, { Protected, PublicOnly } from "./components/AppBootScreen";

const mockAuth = {
  user: null,
  loading: true,
  hasPerm: () => false,
};

const mockLocation = { pathname: "/admin/import", search: "" };

jest.mock("react-router-dom", () => ({
  Navigate: ({ to }) => <div data-testid="navigate-to">{typeof to === "string" ? to : ""}</div>,
  useLocation: () => mockLocation,
  useSearchParams: () => [new URLSearchParams(mockLocation.search.replace(/^\?/, "")), () => {}],
}), { virtual: true });

jest.mock("./lib/auth", () => ({
  useAuth: () => mockAuth,
  AuthProvider: ({ children }) => children,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

function renderInto(ui) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(ui);
  });
  return {
    container,
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

describe("static HTML boot surface", () => {
  const html = fs.readFileSync(path.join(__dirname, "../public/index.html"), "utf8");

  test("initial HTML contains a visible Eternal boot mark inside #root", () => {
    expect(html).toMatch(/id="root"/);
    expect(html).toMatch(/id="eternal-static-boot"/);
    expect(html).toMatch(/data-testid="eternal-static-boot"/);
    expect(html).toMatch(/ETERNAL PACKAGING/);
    expect(html).toMatch(/Memuat\.\.\./);
    expect(html).toMatch(/#eternal-static-boot\{/);
    expect(html).not.toMatch(/<div id="root"><\/div>/);
  });

  test("static boot has no authenticated navigation, identity, or route content", () => {
    const start = html.indexOf('id="eternal-static-boot"');
    const boot = html.slice(start, html.indexOf("</div>", start));
    expect(boot).toMatch(/ETERNAL PACKAGING/);
    expect(boot).not.toMatch(/href=/);
    expect(boot).not.toMatch(/<nav/i);
    expect(boot).not.toMatch(/sidebar|AppShell|logout/i);
    expect(boot).not.toMatch(/admin\/import|m\/tasks/);
  });

  test("emergent-main.js is deferred and does not precede inline boot CSS", () => {
    const styleIdx = html.indexOf("<style>");
    const emergentIdx = html.indexOf("assets.emergent.sh/scripts/emergent-main.js");
    const rootIdx = html.indexOf('id="eternal-static-boot"');
    expect(styleIdx).toBeGreaterThan(-1);
    expect(emergentIdx).toBeGreaterThan(styleIdx);
    expect(rootIdx).toBeGreaterThan(emergentIdx);
    expect(html).toMatch(/<script defer src="https:\/\/assets\.emergent\.sh\/scripts\/emergent-main\.js"><\/script>/);
    expect(html).not.toMatch(/<script src="https:\/\/assets\.emergent\.sh\/scripts\/emergent-main\.js"><\/script>/);
  });
});

describe("AppBootScreen", () => {
  test("renders neutral loading copy without nav or identity", () => {
    const { container, unmount } = renderInto(<AppBootScreen />);
    expect(container.querySelector('[data-testid="app-boot-screen"]')).toBeTruthy();
    expect(container.textContent).toContain("ETERNAL PACKAGING");
    expect(container.textContent).toContain("Memuat...");
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("nav")).toBeNull();
    expect(container.textContent).not.toMatch(/Admin|Driver|sidebar|Tugas Saya/i);
    unmount();
  });
});

describe("Protected / PublicOnly boot gates", () => {
  const appSrc = fs.readFileSync(path.join(__dirname, "App.js"), "utf8");
  const bootSrc = fs.readFileSync(path.join(__dirname, "components/AppBootScreen.jsx"), "utf8");

  test("source: loading uses AppBootScreen; redirects stay identical", () => {
    expect(appSrc).toMatch(/function Protected\(/);
    expect(appSrc).toMatch(/function PublicOnly\(/);
    expect(appSrc).toMatch(/<Route element=\{<Protected><AppShell \/><\/Protected>\}>/);
    expect(appSrc).toMatch(/<Route element=\{<Protected><MobileOperationalLayout \/><\/Protected>\}>/);
    expect(bootSrc).toMatch(/if \(loading\) return <AppBootScreen \/>/);
    expect(bootSrc).toMatch(/if \(!user\) return <Navigate to=\{loginPathForLocation\(location\.pathname, location\.search\)\} replace \/>/);
    expect(bootSrc).toMatch(/if \(user\) return <Navigate to=\{postLoginPath\(params\.get\("next"\)\)\} replace \/>/);
    expect(bootSrc).not.toMatch(/AppShell|MobileSidebar|hasPerm\(/);
    expect(bootSrc).not.toMatch(/user\.(email|name|role)/);
  });

  test("loading does not render protected desktop content or navigation", () => {
    mockAuth.loading = true;
    mockAuth.user = null;
    mockLocation.pathname = "/admin/import";
    mockLocation.search = "";
    const { container, unmount } = renderInto(
      <Protected>
        <nav data-testid="real-shell-nav">Sidebar</nav>
        <div data-testid="import-page">Import PO</div>
      </Protected>,
    );
    expect(container.querySelector('[data-testid="app-boot-screen"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="import-page"]')).toBeNull();
    expect(container.querySelector('[data-testid="real-shell-nav"]')).toBeNull();
    expect(container.querySelector('[data-testid="navigate-to"]')).toBeNull();
    unmount();
  });

  test("loading mobile tasks does not expose task content", () => {
    mockAuth.loading = true;
    mockAuth.user = null;
    mockLocation.pathname = "/m/tasks";
    mockLocation.search = "";
    const { container, unmount } = renderInto(
      <Protected>
        <div data-testid="mobile-tasks-page">Tugas Saya</div>
      </Protected>,
    );
    expect(container.querySelector('[data-testid="app-boot-screen"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-tasks-page"]')).toBeNull();
    expect(container.textContent).not.toContain("Tugas Saya");
    unmount();
  });

  test("authenticated resolution still renders protected children", () => {
    mockAuth.loading = false;
    mockAuth.user = { id: "u1", email: "a@x.com", permissions: ["po.import.view"] };
    mockLocation.pathname = "/admin/import";
    mockLocation.search = "";
    const { container, unmount } = renderInto(
      <Protected>
        <div data-testid="import-page">Import PO</div>
      </Protected>,
    );
    expect(container.querySelector('[data-testid="app-boot-screen"]')).toBeNull();
    expect(container.querySelector('[data-testid="import-page"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="navigate-to"]')).toBeNull();
    unmount();
  });

  test("unauthenticated still redirects to login, preserving /m next", () => {
    mockAuth.loading = false;
    mockAuth.user = null;
    mockLocation.pathname = "/m/tasks";
    mockLocation.search = "";
    const { container, unmount } = renderInto(
      <Protected>
        <div data-testid="mobile-tasks-page">Tugas</div>
      </Protected>,
    );
    expect(container.querySelector('[data-testid="mobile-tasks-page"]')).toBeNull();
    expect(container.querySelector('[data-testid="app-boot-screen"]')).toBeNull();
    expect(container.querySelector('[data-testid="navigate-to"]')?.textContent).toContain("/login?next=");
    expect(container.querySelector('[data-testid="navigate-to"]')?.textContent).toContain("%2Fm%2Ftasks");
    unmount();
  });

  test("PublicOnly loading uses boot screen; authenticated still redirects away from login", () => {
    mockAuth.loading = true;
    mockAuth.user = null;
    mockLocation.pathname = "/login";
    mockLocation.search = "";
    const loading = renderInto(<PublicOnly><div data-testid="login-form">Masuk</div></PublicOnly>);
    expect(loading.container.querySelector('[data-testid="app-boot-screen"]')).toBeTruthy();
    expect(loading.container.querySelector('[data-testid="login-form"]')).toBeNull();
    loading.unmount();

    mockAuth.loading = false;
    mockAuth.user = { id: "u1", email: "a@x.com" };
    const authed = renderInto(<PublicOnly><div data-testid="login-form">Masuk</div></PublicOnly>);
    expect(authed.container.querySelector('[data-testid="navigate-to"]')?.textContent).toBe("/");
    expect(authed.container.querySelector('[data-testid="login-form"]')).toBeNull();
    authed.unmount();

    mockAuth.loading = false;
    mockAuth.user = null;
    const guest = renderInto(<PublicOnly><div data-testid="login-form">Masuk</div></PublicOnly>);
    expect(guest.container.querySelector('[data-testid="login-form"]')).toBeTruthy();
    expect(guest.container.querySelector('[data-testid="navigate-to"]')).toBeNull();
    guest.unmount();
  });
});

