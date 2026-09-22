import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import ProductRequestList from "./ProductRequestList";

const src = fs.readFileSync(path.join(__dirname, "ProductRequestList.jsx"), "utf8");
const mockHasPerm = jest.fn();
const mockNavigate = jest.fn();
const mockApi = require("../lib/api").default;

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
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
  submitted_at: "2026-09-20T01:00:00+00:00",
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
  test("create action is permission gated and drawer is read-only", () => {
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
    mockHasPerm.mockImplementation((p) => p === "product_request.read_all" || p === "product_request.create");
    mockApi.listProductRequests.mockImplementation((params = {}) => {
      if (params.view === "own_drafts") return Promise.resolve({ requests: [OWN_DRAFT] });
      return Promise.resolve({ requests: [SUBMITTED_ROW] });
    });
    mockApi.getProductRequest.mockResolvedValue({
      request: COMPLETE_SUBMITTED_DETAIL,
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("Holding can open read-only detail and see Buat Permintaan when create is granted", async () => {
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
    expect(container.querySelector('[data-testid="prq-detail-drawer"]').textContent).toMatch(/Kedai Baru/);
    await act(async () => {
      container.querySelector('[data-testid="prq-detail-tab-product"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-detail-product-name"]').textContent).toBe("Paper Cup 12oz");
    expect(container.querySelector('[data-testid="prq-detail-product-category"]').textContent).toBe("Paper Cup");
    expect(container.querySelector('[data-testid="prq-detail-product-size"]').textContent).toBe("12 oz");
    expect(container.querySelector('[data-testid="prq-detail-product-material"]').textContent).toBe("Food Grade Paper");
    expect(container.querySelector('[data-testid="prq-detail-product-model"]').textContent).toBe("Custom Logo");
    await act(async () => {
      container.querySelector('[data-testid="prq-detail-tab-potential"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-detail-channel"]').textContent).toBe("Referensi Customer");
  });

  test("CRM receipt-only detail is ignored by the Holding drawer", async () => {
    mockApi.getProductRequest.mockResolvedValue({
      request: { id: "r1", request_number: "PPR-1", status: "submitted", receipt_only: true },
    });
    await act(async () => {
      root.render(<ProductRequestList />);
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="prq-row-r1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="prq-detail-drawer"]')).toBeFalsy();
    expect(container.textContent).not.toMatch(/Food Grade Paper/);
  });

  test("CRM create-only is redirected to the form", async () => {
    mockHasPerm.mockImplementation((p) => p === "product_request.create");
    await act(async () => {
      root.render(<ProductRequestList />);
    });
    await flush();
    expect(container.querySelector('[data-testid="navigate"]').getAttribute("data-to")).toBe("/penjualan/permintaan-produk/baru");
    expect(mockApi.listProductRequests).not.toHaveBeenCalled();
  });

  test("read_all plus create shows Terkirim by default and own draft on Draft Saya", async () => {
    await act(async () => {
      root.render(<ProductRequestList />);
    });
    await flush();
    expect(container.querySelector('[data-testid="prq-tab-submitted"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="prq-count-submitted"]').textContent).toBe("1");
    expect(container.querySelector('[data-testid="prq-count-draft"]').textContent).toBe("1");
    expect(container.querySelector('[data-testid="prq-list-table"]').textContent).toMatch(/Renee/);
    expect(container.querySelector('[data-testid="prq-list-table"]').textContent).toMatch(/Terkirim/);
    expect(container.querySelector('[data-testid="prq-view-r1"]').textContent).toMatch(/Lihat Detail/);
    expect(container.querySelector('[data-testid="prq-draft-table"]')).toBeFalsy();
    expect(container.textContent).not.toMatch(/Draft Orang Lain/);
    expect(mockApi.listProductRequests).toHaveBeenCalledWith({ view: "submitted" });
    expect(mockApi.listProductRequests).toHaveBeenCalledWith({ view: "own_drafts" });

    await act(async () => {
      container.querySelector('[data-testid="prq-tab-own-drafts"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-draft-row-d1"]').textContent).toMatch(/Draft Saya/);
    expect(container.querySelector('[data-testid="prq-draft-row-d1"]').textContent).toMatch(/Draft/);
    expect(container.querySelector('[data-testid="prq-draft-complete-d1"]').textContent).toBe("4/9");
    expect(container.querySelector('[data-testid="prq-list-table"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="prq-continue-d1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalledWith("/penjualan/permintaan-produk/d1");
  });

  test("read_all without create does not show Draft Saya", async () => {
    mockHasPerm.mockImplementation((p) => p === "product_request.read_all");
    await act(async () => {
      root.render(<ProductRequestList />);
    });
    await flush();
    expect(container.querySelector('[data-testid="prq-tab-submitted"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-tab-own-drafts"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prq-row-r1"]')).toBeTruthy();
    expect(mockApi.listProductRequests).toHaveBeenCalledWith({ view: "submitted" });
    expect(mockApi.listProductRequests).not.toHaveBeenCalledWith({ view: "own_drafts" });
  });
});
