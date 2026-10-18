import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import ProductRequestList from "./ProductRequestList";

const src = fs.readFileSync(path.join(__dirname, "ProductRequestList.jsx"), "utf8");
const mockHasPerm = jest.fn();
const mockNavigate = jest.fn();
const mockSetSearchParams = jest.fn();
const mockApi = require("../lib/api").default;

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useSearchParams: () => [new URLSearchParams(), mockSetSearchParams],
  Navigate: ({ to }) => <div data-testid="navigate" data-to={to} />,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "h1", name: "Holding", role: "holding_team" },
    hasPerm: mockHasPerm,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listProductRequests: jest.fn(),
    getProductRequest: jest.fn(),
    listProductRequestActivities: jest.fn(),
    addProductRequestFeedback: jest.fn(),
    updateProductRequestStatus: jest.fn(),
  },
}));

jest.mock("lucide-react", () => new Proxy({}, {
  get: (_target, prop) => {
    if (prop === "__esModule") return true;
    return (props) => <span data-icon={String(prop)} {...props} />;
  },
}), { virtual: true });

const SUBMITTED_ROW = {
  id: "r1",
  request_number: "PPR-1",
  submitted_at: "2026-09-20T01:00:00+00:00",
  customer_name: "Renee",
  customer_source: "master_crm",
  product_name: "Cup",
  product_source: "requested_product",
  potential_qty: 50,
  created_by_name: "Andi",
  branch_name: "Surabaya",
  status: "submitted",
  status_label: "Terkirim",
};

const REVIEWED_ROW = {
  id: "r2",
  request_number: "PPR-2",
  submitted_at: "2026-09-21T01:00:00+00:00",
  customer_name: "Budi",
  customer_source: "master_crm",
  product_name: "Bowl",
  product_source: "requested_product",
  potential_qty: 20,
  created_by_name: "Sari",
  branch_name: "Jakarta",
  status: "reviewed",
  status_label: "Ditinjau",
};

const OWN_DRAFT = {
  id: "d1",
  request_number: "PPR-D1",
  updated_at: "2026-09-21T02:00:00+00:00",
  customer_name: "Draft Saya",
  product_name: "Bowl",
  potential_qty: 12,
  status: "draft",
  completeness: { done: 4, total: 9 },
};

const COMPLETE_SUBMITTED_DETAIL = {
  id: "r1",
  request_number: "PPR-1",
  status: "submitted",
  status_label: "Terkirim",
  submitted_at: "2026-09-20T01:00:00+00:00",
  doc_version: 2,
  customer_source: "new_prospect",
  customer_snapshot: { name: "Kedai Baru", pic: "Sari", whatsapp: "081298765432", city: "Jakarta" },
  product_source: "requested_product",
  product_snapshot: {
    name: "Paper Cup 12oz",
    category: "Paper Cup",
    size: "12 oz",
    material: "Food Grade Paper",
    model: "Custom Logo",
    label: "Requested Product",
  },
  potential_qty: 50000,
  frequency: "Bulanan",
  target_price: 850,
  needed_date: "2026-10-15",
  request_note: "Customer rebranding",
  request_channel: "Referensi Customer",
  created_by_snapshot: { name: "Andi" },
  branch_snapshot: { name: "Surabaya" },
};

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

describe("ProductRequestList", () => {
  test("create action is permission gated and drawer is read-only base", () => {
    expect(src).toMatch(/canCreateProductRequest/);
    expect(src).toMatch(/prq-create-from-list/);
    expect(src).toMatch(/prq-detail-drawer/);
    expect(src).not.toMatch(/Setujui|Tolak|Jadikan Produk/);
  });

  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockImplementation((p) => (
      p === "product_request.read_all"
      || p === "product_request.create"
      || p === "product_request.feedback"
      || p === "product_request.update_status"
    ));
    mockApi.listProductRequests.mockImplementation((params = {}) => {
      if (params.view === "own_drafts") return Promise.resolve({ requests: [OWN_DRAFT] });
      return Promise.resolve({ requests: [SUBMITTED_ROW, REVIEWED_ROW] });
    });
    mockApi.getProductRequest.mockResolvedValue({
      request: COMPLETE_SUBMITTED_DETAIL,
    });
    mockApi.listProductRequestActivities.mockResolvedValue({ activities: [] });
    mockApi.addProductRequestFeedback.mockResolvedValue({
      request: { ...COMPLETE_SUBMITTED_DETAIL, doc_version: 3 },
      activity: { id: "a1", type: "feedback", note: "ABCDE123", actor_snapshot: { name: "Holding" }, created_at: "2026-10-01T00:00:00Z" },
    });
    mockApi.updateProductRequestStatus.mockResolvedValue({
      request: { ...COMPLETE_SUBMITTED_DETAIL, status: "reviewed", status_label: "Ditinjau", doc_version: 4 },
      activity: { id: "a2", type: "status_change", from_status: "submitted", to_status: "reviewed" },
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("Holding can open detail with workflow tabs", async () => {
    await act(async () => {
      root.render(<ProductRequestList />);
    });
    await flush();
    expect(container.querySelector('[data-testid="prq-list-page"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-create-from-list"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="prq-row-r1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(mockApi.getProductRequest).toHaveBeenCalledWith("r1");
    expect(mockApi.listProductRequestActivities).toHaveBeenCalledWith("r1");
    expect(container.querySelector('[data-testid="prq-detail-drawer"]').textContent).toMatch(/Kedai Baru/);
    await act(async () => {
      container.querySelector('[data-testid="prq-detail-tab-product"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-detail-product-name"]').textContent).toBe("Paper Cup 12oz");
    await act(async () => {
      container.querySelector('[data-testid="prq-detail-tab-workflow"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-feedback-input"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-status-select"]')).toBeTruthy();
  });

  test("feedback input keeps focus while typing ABCDE123", async () => {
    await act(async () => {
      root.render(<ProductRequestList />);
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="prq-row-r1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="prq-detail-tab-workflow"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const input = container.querySelector('[data-testid="prq-feedback-input"]');
    expect(input).toBeTruthy();
    await act(async () => {
      input.focus();
    });
    expect(document.activeElement).toBe(input);
    for (const ch of "ABCDE123") {
      await act(async () => {
        const next = `${input.value}${ch}`;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
        setter?.call(input, next);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      expect(document.activeElement).toBe(input);
      expect(input.value.endsWith(ch)).toBe(true);
    }
    expect(input.value).toBe("ABCDE123");
  });

  test("create-only user sees submitted list and Draft Saya tab", async () => {
    mockHasPerm.mockImplementation((p) => p === "product_request.create");
    mockNavigate.mockClear();
    await act(async () => {
      root.render(<ProductRequestList />);
    });
    await flush();
    expect(container.querySelector('[data-testid="navigate"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prq-list-page"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-filter-all"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="prq-tab-own-drafts"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-filter-submitted"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-create-from-list"]')).toBeTruthy();
    expect(mockApi.listProductRequests).toHaveBeenCalledWith({ view: "submitted" });
    expect(mockApi.listProductRequests).toHaveBeenCalledWith({ view: "own_drafts" });
    await act(async () => {
      container.querySelector('[data-testid="prq-tab-own-drafts"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-draft-row-d1"]').textContent).toMatch(/Draft Saya/);
    expect(container.querySelector('[data-testid="prq-list-table"]')).toBeFalsy();
    await act(async () => {
      container.querySelector('[data-testid="prq-continue-d1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalledWith("/penjualan/permintaan-produk/d1");
  });

  test("Semua shows all submitted statuses; individual filters and Draft Saya stay separate", async () => {
    await act(async () => {
      root.render(<ProductRequestList />);
    });
    await flush();
    expect(container.querySelector('[data-testid="prq-filter-all"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="prq-filter-all"]').textContent).toMatch(/Semua/);
    expect(container.querySelector('[data-testid="prq-count-all"]').textContent).toBe("2");
    expect(container.querySelector('[data-testid="prq-count-draft"]').textContent).toBe("1");
    expect(container.querySelector('[data-testid="prq-list-table"]').textContent).toMatch(/Renee/);
    expect(container.querySelector('[data-testid="prq-list-table"]').textContent).toMatch(/Budi/);
    expect(container.querySelector('[data-testid="prq-row-r1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-row-r2"]')).toBeTruthy();
    expect(mockApi.listProductRequests).toHaveBeenCalledWith({ view: "submitted" });
    expect(mockApi.listProductRequests).toHaveBeenCalledWith({ view: "own_drafts" });
    const callsAfterLoad = mockApi.listProductRequests.mock.calls.length;

    await act(async () => {
      container.querySelector('[data-testid="prq-filter-submitted"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-filter-submitted"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="prq-row-r1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-row-r2"]')).toBeFalsy();
    expect(mockApi.listProductRequests.mock.calls.length).toBe(callsAfterLoad);

    await act(async () => {
      container.querySelector('[data-testid="prq-filter-reviewed"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-filter-reviewed"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="prq-row-r1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prq-row-r2"]')).toBeTruthy();
    expect(mockApi.listProductRequests.mock.calls.length).toBe(callsAfterLoad);

    await act(async () => {
      container.querySelector('[data-testid="prq-tab-own-drafts"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-draft-row-d1"]').textContent).toMatch(/Draft Saya/);
    expect(container.querySelector('[data-testid="prq-draft-complete-d1"]').textContent).toBe("4/9");
    expect(container.querySelector('[data-testid="prq-list-table"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prq-row-r1"]')).toBeFalsy();
    expect(mockApi.listProductRequests.mock.calls.length).toBe(callsAfterLoad);
  });

  test("user without draft access does not show Draft Saya", async () => {
    mockHasPerm.mockImplementation(() => false);
    await act(async () => {
      root.render(<ProductRequestList />);
    });
    await flush();
    expect(container.querySelector('[data-testid="prq-filter-all"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-tab-own-drafts"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prq-row-r1"]')).toBeTruthy();
    expect(mockApi.listProductRequests).toHaveBeenCalledWith({ view: "submitted" });
    expect(mockApi.listProductRequests).not.toHaveBeenCalledWith({ view: "own_drafts" });
    expect(container.querySelector('[data-testid="prq-feedback-input"]')).toBeFalsy();
  });
});
