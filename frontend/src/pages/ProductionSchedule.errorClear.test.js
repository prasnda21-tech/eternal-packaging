/**
 * Soft Refresh must clear prod-error only after the latest list GET succeeds.
 * Stale/aborted successes must not clear a newer failure.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import ProductionSchedule from "./ProductionSchedule";

const mockHasPerm = jest.fn(() => true);

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useLocation: () => ({ pathname: "/produksi", search: "" }),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ user: { id: "err-clear-user" }, hasPerm: (perm) => mockHasPerm(perm) }),
}));

let mockAutoRefresh;
jest.mock("../lib/useAutoRefresh", () => ({
  __esModule: true,
  default: (fn, opts = {}) => {
    mockAutoRefresh = {
      fn,
      opts,
      refreshNow: async () => {
        try {
          const r = await fn();
          opts.onData?.(r);
        } catch (e) {
          opts.onError?.(e);
        }
      },
    };
    return {
      lastUpdated: null,
      isPolling: false,
      refreshNow: () => mockAutoRefresh.refreshNow(),
    };
  },
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    productionDashboard: jest.fn(),
    productionExport: jest.fn(),
  },
}));

jest.mock("../components/ui/popover", () => ({
  Popover: ({ children }) => <div>{children}</div>,
  PopoverTrigger: ({ children }) => children,
  PopoverContent: ({ children }) => <div>{children}</div>,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const rowPayload = (po = "PO-OK-1") => ({
  rows: [{
    id: `id-${po}`,
    po_number: po,
    customer: "Cust Dummy",
    outlet: "Outlet",
    deadline: "2026-10-20T00:00:00Z",
    urgent: false,
    hold: false,
    work_date: null,
    current_stage: 5,
    status_final: "Penjadwalan",
    total_quantity: 10,
    print_qty_total: 10,
    operators: [],
    progress: {},
  }],
  total: 1,
});

describe("ProductionSchedule soft-refresh error clear", () => {
  let container;
  let root;

  beforeEach(async () => {
    mockHasPerm.mockReturnValue(true);
    api.productionDashboard.mockReset();
    mockAutoRefresh = null;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("500 shows prod-error; successful Soft Refresh clears it and restores rows", async () => {
    api.productionDashboard
      .mockRejectedValueOnce({ response: { data: { detail: "qa-simulated-failure" } } })
      .mockResolvedValueOnce(rowPayload("PO-RECOVERED"));

    await act(async () => {
      root.render(<ProductionSchedule />);
    });
    await flush();
    await flush();

    expect(container.querySelector('[data-testid="prod-error"]')?.textContent).toMatch(/qa-simulated-failure/);
    expect(container.querySelectorAll('[data-testid^="prod-row-"]').length).toBe(0);

    // Soft Refresh only — must not clear error merely by invoking the click handler
    // until the request resolves successfully (onData).
    await act(async () => {
      const p = mockAutoRefresh.refreshNow();
      expect(container.querySelector('[data-testid="prod-error"]')).toBeTruthy();
      await p;
    });
    await flush();

    expect(container.querySelector('[data-testid="prod-error"]')).toBeNull();
    expect(container.textContent).toMatch(/PO-RECOVERED/);
  });

  test("soft refresh 500 keeps error until a later success", async () => {
    api.productionDashboard
      .mockResolvedValueOnce(rowPayload("PO-FIRST"))
      .mockRejectedValueOnce({ response: { data: { detail: "qa-second-failure" } } })
      .mockResolvedValueOnce(rowPayload("PO-THIRD"));

    await act(async () => {
      root.render(<ProductionSchedule />);
    });
    await flush();
    await flush();
    expect(container.textContent).toMatch(/PO-FIRST/);
    expect(container.querySelector('[data-testid="prod-error"]')).toBeNull();

    await act(async () => {
      await mockAutoRefresh.refreshNow();
    });
    await flush();
    expect(container.querySelector('[data-testid="prod-error"]')?.textContent).toMatch(/qa-second-failure/);

    await act(async () => {
      await mockAutoRefresh.refreshNow();
    });
    await flush();
    expect(container.querySelector('[data-testid="prod-error"]')).toBeNull();
    expect(container.textContent).toMatch(/PO-THIRD/);
  });

  test("tab selection survives error then successful Soft Refresh", async () => {
    api.productionDashboard
      .mockResolvedValueOnce(rowPayload("PO-TAB"))
      .mockRejectedValueOnce({ response: { data: { detail: "qa-tab-failure" } } })
      .mockResolvedValueOnce(rowPayload("PO-TAB"));

    await act(async () => {
      root.render(<ProductionSchedule />);
    });
    await flush();
    await flush();

    const holdTab = container.querySelector('[data-testid="prod-status-tab-hold"]');
    expect(holdTab).toBeTruthy();
    await act(async () => {
      holdTab.click();
    });
    await flush();
    expect(holdTab.className).toMatch(/is-active/);

    await act(async () => {
      await mockAutoRefresh.refreshNow();
    });
    await flush();
    expect(container.querySelector('[data-testid="prod-error"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prod-status-tab-hold"]')?.className).toMatch(/is-active/);

    await act(async () => {
      await mockAutoRefresh.refreshNow();
    });
    await flush();
    expect(container.querySelector('[data-testid="prod-error"]')).toBeNull();
    expect(container.querySelector('[data-testid="prod-status-tab-hold"]')?.className).toMatch(/is-active/);
  });

  test("aborted initial load success does not clear a newer failure", async () => {
    let resolveFirst;
    api.productionDashboard
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockRejectedValueOnce({ response: { data: { detail: "qa-abort-newer" } } });

    await act(async () => {
      root.render(<ProductionSchedule />);
    });
    await flush();

    const selesaiCb = container.querySelector('[data-testid="prod-include-completed"]');
    await act(async () => {
      selesaiCb.click();
    });
    await flush();
    await flush();
    expect(container.querySelector('[data-testid="prod-error"]')?.textContent).toMatch(/qa-abort-newer/);

    await act(async () => {
      resolveFirst(rowPayload("PO-ABORTED"));
      await new Promise((r) => setTimeout(r, 0));
    });
    await flush();

    expect(container.querySelector('[data-testid="prod-error"]')?.textContent).toMatch(/qa-abort-newer/);
    expect(container.textContent).not.toMatch(/PO-ABORTED/);
  });

  test("stale soft-refresh success does not clear a newer failed request", async () => {
    let resolveSlow;
    const slowOk = new Promise((resolve) => {
      resolveSlow = resolve;
    });

    api.productionDashboard
      .mockResolvedValueOnce(rowPayload("PO-BASE"))
      .mockImplementationOnce(() => slowOk) // soft refresh in flight
      .mockRejectedValueOnce({ response: { data: { detail: "qa-newer-failure" } } }); // superseded by effect

    await act(async () => {
      root.render(<ProductionSchedule />);
    });
    await flush();
    await flush();

    // Start soft refresh (req N) without awaiting
    let softPromise;
    await act(async () => {
      softPromise = mockAutoRefresh.refreshNow();
    });

    // Trigger a newer list effect failure (toggle includeCompleted)
    const selesaiCb = container.querySelector('[data-testid="prod-include-completed"]');
    expect(selesaiCb).toBeTruthy();
    await act(async () => {
      selesaiCb.click();
    });
    await flush();
    await flush();

    expect(container.querySelector('[data-testid="prod-error"]')?.textContent).toMatch(/qa-newer-failure/);

    // Complete stale soft refresh success — must NOT clear newer error
    await act(async () => {
      resolveSlow(rowPayload("PO-STALE"));
      await softPromise;
    });
    await flush();

    expect(container.querySelector('[data-testid="prod-error"]')?.textContent).toMatch(/qa-newer-failure/);
    expect(container.textContent).not.toMatch(/PO-STALE/);
  });
});
