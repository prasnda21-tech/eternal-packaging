/**
 * Regression: dynamic PO workspace/history tab must EXIST after Lihat PO,
 * keep id po-detail:<order_id>, show customer label, and survive in-place
 * enrichment after PODetail loads (no navigate/replace label updates).
 */
import fs from "fs";
import path from "path";
import { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Link, MemoryRouter, Outlet, Route, Routes, useLocation } from "react-router-dom";
import api from "../lib/api";
import { NAV_KEYS } from "../lib/navAccess";
import { useGlobalWorkspaceTabs } from "../hooks/useGlobalWorkspaceTabs";
import WorkspaceTabs from "../components/WorkspaceTabs";
import PODetail from "./PODetail";
import { openOrActivateGlobalTab } from "../lib/globalWorkspace";
import { clearPoDetailRetention } from "../lib/poDetailRetention";

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: jest.fn(),
}));

const mockPermissions = [...Object.values(NAV_KEYS), "po.view", "sales_order.view"];
const mockAuthUser = {
  id: "u-1",
  email: "ops@eternal.test",
  name: "Ops",
  role: "admin",
  permissions: mockPermissions,
};

jest.mock("react-router-dom", () => {
  const React = require("react");
  const Ctx = React.createContext(null);

  function parseEntry(entry) {
    if (typeof entry === "string") return { pathname: entry, state: null };
    return { pathname: entry.pathname, state: entry.state ?? null };
  }

  function matchPath(pattern, pathname) {
    const p = String(pattern || "").split("/").filter(Boolean);
    const l = String(pathname || "").split("/").filter(Boolean);
    if (p.length !== l.length) return null;
    const params = {};
    for (let i = 0; i < p.length; i += 1) {
      if (p[i].startsWith(":")) params[p[i].slice(1)] = decodeURIComponent(l[i]);
      else if (p[i] !== l[i]) return null;
    }
    return { params };
  }

  function MemoryRouter({ initialEntries = ["/"], children }) {
    const [location, setLocation] = React.useState(() => parseEntry(initialEntries[0]));
    const navigate = React.useCallback((to, opts = {}) => {
      const pathname = typeof to === "string" ? to : to.pathname;
      setLocation({
        pathname,
        state: opts.state ?? (typeof to === "object" ? to.state : null) ?? null,
      });
    }, []);
    return React.createElement(Ctx.Provider, { value: { location, navigate, params: {} } }, children);
  }

  function useLocation() {
    return React.useContext(Ctx).location;
  }

  function useNavigate() {
    return React.useContext(Ctx).navigate;
  }

  function useParams() {
    return React.useContext(Ctx).params || {};
  }

  function Link({ to, state, children, ...props }) {
    const { navigate } = React.useContext(Ctx);
    return React.createElement(
      "a",
      {
        ...props,
        href: to,
        onClick: (e) => {
          e.preventDefault();
          navigate(to, { state });
        },
      },
      children,
    );
  }

  function Route() {
    return null;
  }

  function Routes({ children }) {
    const ctx = React.useContext(Ctx);
    const nodes = React.Children.toArray(children);
    const layout = nodes.find((n) => n?.props?.element && n?.props?.children);
    const leaves = layout ? React.Children.toArray(layout.props.children) : nodes;
    let params = {};
    let leaf = null;
    leaves.forEach((n) => {
      if (!n?.props?.path) return;
      const matched = matchPath(n.props.path, ctx.location.pathname);
      if (matched) {
        params = matched.params;
        leaf = n.props.element;
      }
    });
    const next = { ...ctx, params, outlet: leaf };
    return React.createElement(Ctx.Provider, { value: next }, layout ? layout.props.element : leaf);
  }

  function Outlet() {
    const { outlet } = React.useContext(Ctx);
    return outlet || null;
  }

  function Navigate({ to }) {
    const navigate = useNavigate();
    React.useEffect(() => {
      navigate(to, { replace: true });
    }, [navigate, to]);
    return null;
  }

  return {
    Link,
    MemoryRouter,
    Navigate,
    Outlet,
    Route,
    Routes,
    useLocation,
    useNavigate,
    useParams,
  };
}, { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: mockAuthUser,
    hasPerm: (key) => mockPermissions.includes(key) || key === "po.view",
    loading: false,
    logout: jest.fn(),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    getOrder: jest.fn(),
    listSalesReceipts: jest.fn(),
    systemHealth: jest.fn().mockResolvedValue({ mongo: true }),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

function makeOrder(id, poNumber, customer) {
  return {
    id,
    po_number: poNumber,
    customer,
    outlet: "Outlet",
    current_stage: 0,
    status: "draft",
    priority: "normal",
    line_items: [],
    total_quantity: 10,
  };
}

function WorkspaceShell() {
  const workspace = useGlobalWorkspaceTabs();
  const location = useLocation();
  return (
    <div data-testid="app-shell">
      <div data-testid="shell-path">{location.pathname}</div>
      <div data-testid="tab-ids">{workspace.tabs.map((t) => t.id).join(",")}</div>
      <div data-testid="tab-labels">{workspace.tabs.map((t) => t.label).join("|")}</div>
      {workspace.visible && (
        <WorkspaceTabs
          tabs={workspace.tabs}
          activePath={workspace.activePath}
          onActivate={workspace.activateTab}
          onClose={workspace.closeTab}
          tabByPath={workspace.tabByPath}
          ariaLabel="Workspace"
          testid="workspace-tabs"
          testidPrefix="workspace-tab"
        />
      )}
      <Outlet />
    </div>
  );
}

function ListPage() {
  return (
    <div data-testid="pesanan-penjualan-page">
      <Link
        to="/po/ord-a"
        state={{ po_number: "PO/2026/09/09/000018", customer: "WELCOME TO PLEASURE" }}
        data-testid="lihat-po-a"
      >
        Lihat PO A
      </Link>
      <Link
        to="/po/ord-b"
        state={{ po_number: "PO/2026/09/09/000017", customer: "WELCOME TO PLEASURE" }}
        data-testid="lihat-po-b"
      >
        Lihat PO B
      </Link>
    </div>
  );
}

function renderApp(initialEntries) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route element={<WorkspaceShell />}>
            <Route path="/penjualan/pesanan-penjualan" element={<ListPage />} />
            <Route path="/po/:id" element={<PODetail />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
  });
  return {
    container,
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

describe("PO tab existence + customer label (regression)", () => {
  beforeEach(() => {
    sessionStorage.clear();
    clearPoDetailRetention();
    api.getOrder.mockReset();
    api.listSalesReceipts.mockReset();
    api.listSalesReceipts.mockResolvedValue({ receipts: [] });
    api.getOrder.mockImplementation((id) => Promise.resolve({
      order: makeOrder(
        id,
        id === "ord-b" ? "PO/2026/09/09/000017" : "PO/2026/09/09/000018",
        "WELCOME TO PLEASURE",
      ),
    }));
  });

  test("PODetail must not enrich tab labels via navigate/replace", () => {
    const src = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
    expect(src).toMatch(/enrichWorkspaceTab/);
    expect(src).not.toMatch(/Enrich workspace tab label after order load[\s\S]*navigate\(`\/po\/\$\{order\.id\}`/);
    expect(src).not.toMatch(/navigate\(\s*`\/po\/\$\{order\.id\}`\s*,\s*\{\s*replace:\s*true/);
  });

  test("Lihat PO creates po-detail tab, customer label, and keeps it after order load enrichment", async () => {
    const view = renderApp(["/penjualan/pesanan-penjualan"]);
    await flush();

    await act(async () => {
      view.container.querySelector('[data-testid="lihat-po-a"]').click();
    });
    await flush();
    await flush();

    const ids = (view.container.querySelector("[data-testid='tab-ids']")?.textContent || "").split(",");
    expect(ids).toContain("po-detail:ord-a");
    expect(view.container.querySelector("[data-testid='workspace-tabs']")).toBeTruthy();
    expect(view.container.querySelector("[data-testid='workspace-tab-po-detail:ord-a']")).toBeTruthy();
    expect(
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').textContent,
    ).toBe("WELCOME TO PLEASURE");
    expect(view.container.querySelector("[data-testid='po-detail-page']")).toBeTruthy();
    expect(view.container.querySelector("[data-testid='shell-path']").textContent).toBe("/po/ord-a");

    // Still present after enrichment tick
    await flush();
    expect(view.container.querySelector("[data-testid='workspace-tab-po-detail:ord-a']")).toBeTruthy();
    expect(
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').textContent,
    ).toBe("WELCOME TO PLEASURE");
    view.unmount();
  });

  test("direct /po/:id starts with fallback then enriches customer label in place without changing tab id", async () => {
    const view = renderApp(["/po/ord-a"]);
    await flush();
    expect(view.container.querySelector("[data-testid='workspace-tab-po-detail:ord-a']")).toBeTruthy();
    await flush();
    await flush();
    const ids = (view.container.querySelector("[data-testid='tab-ids']")?.textContent || "").split(",");
    expect(ids.filter((id) => id === "po-detail:ord-a")).toHaveLength(1);
    expect(
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').textContent,
    ).toBe("WELCOME TO PLEASURE");
    view.unmount();
  });

  test("two POs with same customer remain two independent tabs", async () => {
    const view = renderApp(["/penjualan/pesanan-penjualan"]);
    await flush();
    await act(async () => {
      view.container.querySelector('[data-testid="lihat-po-a"]').click();
    });
    await flush();
    await act(async () => {
      view.container.querySelector('[data-testid="workspace-tab-pesanan-penjualan"] .kb-workspace-tab-label').click();
    });
    await flush();
    await act(async () => {
      view.container.querySelector('[data-testid="lihat-po-b"]').click();
    });
    await flush();
    const ids = (view.container.querySelector("[data-testid='tab-ids']")?.textContent || "").split(",");
    expect(ids).toEqual(expect.arrayContaining(["po-detail:ord-a", "po-detail:ord-b"]));
    expect(view.container.querySelectorAll('[data-testid^="workspace-tab-po-detail:"]')).toHaveLength(2);
    expect(
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').textContent,
    ).toBe("WELCOME TO PLEASURE");
    expect(
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-b"] .kb-workspace-tab-label').textContent,
    ).toBe("WELCOME TO PLEASURE");
    view.unmount();
  });

  test("USER CASE — GIMBO PO UUID registers po-detail tab and labels customer after load", async () => {
    const ORDER = "b8aecfed-3a03-401d-9197-08d75cef1bfd";
    api.getOrder.mockImplementation((id) => Promise.resolve({
      order: makeOrder(id, "PO/09/09/26/001", "GIMBO 2.0"),
    }));

    function GimboList() {
      return (
        <div data-testid="pesanan-penjualan-page">
          <Link
            to={`/po/${ORDER}`}
            state={{ po_number: "PO/09/09/26/001", customer: "GIMBO 2.0" }}
            data-testid={`btn-view-po-${ORDER}`}
          >
            Lihat PO
          </Link>
        </div>
      );
    }

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/penjualan/pesanan-penjualan"]}>
          <Routes>
            <Route element={<WorkspaceShell />}>
              <Route path="/penjualan/pesanan-penjualan" element={<GimboList />} />
              <Route path="/po/:id" element={<PODetail />} />
            </Route>
          </Routes>
        </MemoryRouter>,
      );
    });
    await flush();
    await act(async () => {
      container.querySelector(`[data-testid="btn-view-po-${ORDER}"]`).click();
    });
    await flush();
    await flush();

    const ids = (container.querySelector("[data-testid='tab-ids']")?.textContent || "").split(",");
    expect(ids).toContain("pesanan-penjualan");
    expect(ids).toContain(`po-detail:${ORDER}`);
    expect(container.querySelector(`[data-testid="workspace-tab-po-detail:${ORDER}"]`)).toBeTruthy();
    expect(
      container.querySelector(`[data-testid="workspace-tab-po-detail:${ORDER}"] .kb-workspace-tab-label`).textContent,
    ).toBe("GIMBO 2.0");
    expect(container.querySelector("[data-testid='detail-customer-name']").textContent).toBe("GIMBO 2.0");

    act(() => root.unmount());
    container.remove();
  });

  test("USER CASE — direct /po/<gimbo-id> with null state still creates tab then enriches", async () => {
    const ORDER = "b8aecfed-3a03-401d-9197-08d75cef1bfd";
    api.getOrder.mockImplementation((id) => Promise.resolve({
      order: makeOrder(id, "PO/09/09/26/001", "GIMBO 2.0"),
    }));
    const view = renderApp([`/po/${ORDER}`]);
    await flush();
    expect(view.container.querySelector(`[data-testid="workspace-tab-po-detail:${ORDER}"]`)).toBeTruthy();
    await flush();
    await flush();
    expect(
      view.container.querySelector(`[data-testid="workspace-tab-po-detail:${ORDER}"] .kb-workspace-tab-label`).textContent,
    ).toBe("GIMBO 2.0");
    view.unmount();
  });

  test("USER CASE — seeded Dashboard+Pesanan + delayed order still registers po-detail on /po/:id", async () => {
    const ORDER = "b8aecfed-3a03-401d-9197-08d75cef1bfd";
    sessionStorage.setItem("ep.workspaceTabs.u-1", JSON.stringify(["dashboard", "pesanan-penjualan"]));
    let resolveOrder;
    api.getOrder.mockImplementation(() => new Promise((resolve) => {
      resolveOrder = resolve;
    }));

    const view = renderApp([`/po/${ORDER}`]);
    await flush();

    const idsBefore = (view.container.querySelector("[data-testid='tab-ids']")?.textContent || "").split(",");
    expect(idsBefore).toEqual(expect.arrayContaining([
      "dashboard",
      "pesanan-penjualan",
      `po-detail:${ORDER}`,
    ]));
    expect(view.container.querySelector(`[data-testid="workspace-tab-po-detail:${ORDER}"]`)).toBeTruthy();
    expect(
      view.container.querySelector(`[data-testid="workspace-tab-po-detail:${ORDER}"] .kb-workspace-tab-label`).textContent,
    ).toBe(`PO ${ORDER}`);

    await act(async () => {
      resolveOrder({ order: makeOrder(ORDER, "PO/09/09/26/001", "GIMBO 2.0") });
    });
    await flush();
    await flush();

    expect(view.container.querySelector(`[data-testid="workspace-tab-po-detail:${ORDER}"]`)).toBeTruthy();
    expect(
      view.container.querySelector(`[data-testid="workspace-tab-po-detail:${ORDER}"] .kb-workspace-tab-label`).textContent,
    ).toBe("GIMBO 2.0");
    const idsAfter = (view.container.querySelector("[data-testid='tab-ids']")?.textContent || "").split(",");
    expect(idsAfter.filter((id) => id === `po-detail:${ORDER}`)).toHaveLength(1);
    view.unmount();
  });

  test("USER CASE — StrictMode remount keeps GIMBO po-detail tab (does not persist empty workspace)", async () => {
    const ORDER = "b8aecfed-3a03-401d-9197-08d75cef1bfd";
    sessionStorage.setItem("ep.workspaceTabs.u-1", JSON.stringify(["dashboard", "pesanan-penjualan"]));
    api.getOrder.mockImplementation((id) => Promise.resolve({
      order: makeOrder(id, "PO/09/09/26/001", "GIMBO 2.0"),
    }));

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(
        <StrictMode>
          <MemoryRouter initialEntries={[`/po/${ORDER}`]}>
            <Routes>
              <Route element={<WorkspaceShell />}>
                <Route path="/penjualan/pesanan-penjualan" element={<ListPage />} />
                <Route path="/po/:id" element={<PODetail />} />
              </Route>
            </Routes>
          </MemoryRouter>
        </StrictMode>,
      );
    });
    await flush();
    await flush();
    await flush();

    const ids = (container.querySelector("[data-testid='tab-ids']")?.textContent || "").split(",");
    expect(ids).toEqual(expect.arrayContaining([
      "dashboard",
      "pesanan-penjualan",
      `po-detail:${ORDER}`,
    ]));
    expect(container.querySelector(`[data-testid="workspace-tab-po-detail:${ORDER}"]`)).toBeTruthy();
    expect(
      container.querySelector(`[data-testid="workspace-tab-po-detail:${ORDER}"] .kb-workspace-tab-label`).textContent,
    ).toBe("GIMBO 2.0");
    const stored = JSON.parse(sessionStorage.getItem("ep.workspaceTabs.u-1") || "[]");
    expect(stored).not.toEqual([]);
    expect(stored.some((item) => item === `po-detail:${ORDER}` || item?.id === `po-detail:${ORDER}`)).toBe(true);

    act(() => root.unmount());
    container.remove();
  });
});
