import { act } from "react";
import { createRoot } from "react-dom/client";
import { Link, MemoryRouter, Outlet, Route, Routes, useLocation } from "react-router-dom";
import api from "../lib/api";
import { NAV_KEYS } from "../lib/navAccess";
import { useGlobalWorkspaceTabs } from "../hooks/useGlobalWorkspaceTabs";
import WorkspaceTabs from "../components/WorkspaceTabs";
import PODetail from "./PODetail";

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
    customer: customer || `Customer ${id}`,
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
      <div data-testid="shell-state">{location.state ? JSON.stringify(location.state) : ""}</div>
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
      Pesanan Penjualan
      <Link
        to="/po/ord-a"
        state={{ po_number: "PO/2026/09/09/000018", customer: "WELCOME TO PLEASURE" }}
        data-testid="lihat-po-a"
      >
        Lihat PO A
      </Link>
      <Link
        to="/po/ord-b"
        state={{ po_number: "PO/2026/09/09/000017", customer: "PT ABC" }}
        data-testid="lihat-po-b"
      >
        Lihat PO B
      </Link>
      <Link
        to="/po/ord-c"
        state={{ po_number: "PO/2026/09/09/000016", customer: "WELCOME TO PLEASURE" }}
        data-testid="lihat-po-c"
      >
        Lihat PO C same customer
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
            <Route path="/dashboard" element={<div data-testid="dashboard-page">Dashboard</div>} />
            <Route path="/produksi" element={<div data-testid="jadwal-page">Jadwal Produksi</div>} />
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

describe("PO workspace tab render lifecycle", () => {
  beforeEach(() => {
    sessionStorage.clear();
    api.getOrder.mockReset();
    api.listSalesReceipts.mockReset();
    api.listSalesReceipts.mockResolvedValue({ receipts: [] });
    api.getOrder.mockImplementation((id) => Promise.resolve({
      order: makeOrder(
        id,
        id === "ord-b" ? "PO/2026/09/09/000017" : id === "ord-c" ? "PO/2026/09/09/000016" : "PO/2026/09/09/000018",
        id === "ord-b" ? "PT ABC" : "WELCOME TO PLEASURE",
      ),
    }));
  });

  test("CASE 1 — return to same PO tab without navigation state still renders detail and keeps customer label", async () => {
    const view = renderApp(["/penjualan/pesanan-penjualan"]);
    await flush();
    await act(async () => {
      view.container.querySelector('[data-testid="lihat-po-a"]').click();
    });
    await flush();
    expect(view.container.querySelector("[data-testid='po-detail-page']")).toBeTruthy();
    expect(view.container.querySelector("[data-testid='detail-po-number']").textContent).toBe("PO/2026/09/09/000018");
    expect(view.container.querySelector("[data-testid='shell-path']").textContent).toBe("/po/ord-a");
    expect(
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').textContent,
    ).toBe("WELCOME TO PLEASURE");

    await act(async () => {
      view.container.querySelector('[data-testid="workspace-tab-pesanan-penjualan"] .kb-workspace-tab-label').click();
    });
    await flush();
    expect(view.container.querySelector("[data-testid='pesanan-penjualan-page']")).toBeTruthy();
    expect(view.container.querySelector("[data-testid='po-detail-page']")).toBeFalsy();

    await act(async () => {
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').click();
    });
    await flush();
    expect(view.container.querySelector("[data-testid='shell-path']").textContent).toBe("/po/ord-a");
    expect(view.container.querySelector("[data-testid='app-shell']")).toBeTruthy();
    expect(view.container.querySelector("[data-testid='po-detail-page']")).toBeTruthy();
    expect(view.container.querySelector("[data-testid='detail-po-number']").textContent).toBe("PO/2026/09/09/000018");
    expect(
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').textContent,
    ).toBe("WELCOME TO PLEASURE");
    view.unmount();
  });

  test("CASE 2 — PO A → PO B → PO A renders the matching order each time", async () => {
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
    expect(view.container.querySelector("[data-testid='detail-po-number']").textContent).toBe("PO/2026/09/09/000017");
    expect(
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-b"] .kb-workspace-tab-label').textContent,
    ).toBe("PT ABC");

    await act(async () => {
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').click();
    });
    await flush();
    expect(view.container.querySelector("[data-testid='po-detail-page']")).toBeTruthy();
    expect(view.container.querySelector("[data-testid='detail-po-number']").textContent).toBe("PO/2026/09/09/000018");

    await act(async () => {
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-b"] .kb-workspace-tab-label').click();
    });
    await flush();
    expect(view.container.querySelector("[data-testid='detail-po-number']").textContent).toBe("PO/2026/09/09/000017");
    view.unmount();
  });

  test("CASE 3 — same PO reopen focuses existing tab without duplicate and still renders", async () => {
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
      view.container.querySelector('[data-testid="lihat-po-a"]').click();
    });
    await flush();
    const poTabs = view.container.querySelectorAll('[data-testid="workspace-tab-po-detail:ord-a"]');
    expect(poTabs).toHaveLength(1);
    expect(view.container.querySelector("[data-testid='po-detail-page']")).toBeTruthy();
    view.unmount();
  });

  test("CASE 4 — direct /po/:id without navigation state renders detail and enriches customer label", async () => {
    const view = renderApp(["/po/ord-a"]);
    await flush();
    expect(view.container.querySelector("[data-testid='po-detail-page']")).toBeTruthy();
    expect(api.getOrder).toHaveBeenCalledWith("ord-a");
    expect(view.container.querySelector("[data-testid='detail-po-number']").textContent).toBe("PO/2026/09/09/000018");
    expect(
      view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').textContent,
    ).toBe("WELCOME TO PLEASURE");
    view.unmount();
  });

  test("CASE 5 — restored PO tab with null location.state still resolves and keeps customer label", async () => {
    const view = renderApp(["/penjualan/pesanan-penjualan"]);
    await flush();
    await act(async () => {
      view.container.querySelector('[data-testid="lihat-po-a"]').click();
    });
    await flush();
    view.unmount();

    const restored = renderApp(["/penjualan/pesanan-penjualan"]);
    await flush();
    expect(
      restored.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').textContent,
    ).toBe("WELCOME TO PLEASURE");
    await act(async () => {
      restored.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').click();
    });
    await flush();
    expect(restored.container.querySelector("[data-testid='shell-path']").textContent).toBe("/po/ord-a");
    expect(restored.container.querySelector("[data-testid='po-detail-page']")).toBeTruthy();
    expect(
      restored.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label').textContent,
    ).toBe("WELCOME TO PLEASURE");
    restored.unmount();
  });

  test("same customer different PO keeps two distinct tabs", async () => {
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
      view.container.querySelector('[data-testid="lihat-po-c"]').click();
    });
    await flush();
    const a = view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-a"] .kb-workspace-tab-label');
    const c = view.container.querySelector('[data-testid="workspace-tab-po-detail:ord-c"] .kb-workspace-tab-label');
    expect(a.textContent).toBe("WELCOME TO PLEASURE");
    expect(c.textContent).toBe("WELCOME TO PLEASURE");
    expect(view.container.querySelectorAll('[data-testid^="workspace-tab-po-detail:"]')).toHaveLength(2);
    expect(view.container.querySelector("[data-testid='detail-po-number']").textContent).toBe("PO/2026/09/09/000016");
    await act(async () => {
      a.click();
    });
    await flush();
    expect(view.container.querySelector("[data-testid='detail-po-number']").textContent).toBe("PO/2026/09/09/000018");
    view.unmount();
  });
});
