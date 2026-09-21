import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import ProductRequestForm from "./ProductRequestForm";

const src = fs.readFileSync(path.join(__dirname, "ProductRequestForm.jsx"), "utf8");
const listSrc = fs.readFileSync(path.join(__dirname, "ProductRequestList.jsx"), "utf8");
const app = fs.readFileSync(path.join(__dirname, "..", "App.js"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "..", "lib", "api.js"), "utf8");
const searchSrc = fs.readFileSync(path.join(__dirname, "..", "components", "ProductRequestCustomerSearch.jsx"), "utf8");

const mockHasPerm = jest.fn();
const mockNavigate = jest.fn();
const mockParams = { id: undefined };
const mockApi = require("../lib/api").default;

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useParams: () => mockParams,
  Navigate: ({ to }) => <div data-testid="navigate" data-to={to} />,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "crm-1", name: "Andi Dwiputra", email: "andi@eternal.test", role: "crm" },
    hasPerm: mockHasPerm,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    productRequestContext: jest.fn(),
    productRequestCustomers: jest.fn(),
    createProductRequest: jest.fn(),
    updateProductRequest: jest.fn(),
    submitProductRequest: jest.fn(),
    getProductRequest: jest.fn(),
  },
}));

jest.mock("../components/MasterItemSearchSelect", () => (props) => (
  <button type="button" data-testid={props.testId || "master-item-search"} onClick={() => props.onChange({ id: "P1", item_code: "PC-1", item_name: "Cup" })}>
    master search
  </button>
));

jest.mock("../components/ProductRequestCustomerSearch", () => (props) => (
  <button type="button" data-testid={props.testId || "prq-customer-search"} onClick={() => props.onSelectCustomer({ id: "C1", name: "Renee", pic: "Budi", phone: "0812", city: "Bandung" })}>
    customer search
  </button>
));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

describe("ProductRequestForm contracts", () => {
  test("uses existing customer repository search and master item picker", () => {
    expect(searchSrc).toMatch(/api\.productRequestCustomers/);
    expect(src).toMatch(/MasterItemSearchSelect/);
    expect(apiSrc).toMatch(/createProductRequest/);
    expect(apiSrc).toMatch(/submitProductRequest/);
    expect(apiSrc).toMatch(/productRequestCustomers/);
    expect(app).toMatch(/\/penjualan\/permintaan-produk\/baru/);
  });

  test("does not render autosave, attachment, or duplicate checker", () => {
    expect(src).not.toMatch(/autosave/i);
    expect(src).not.toMatch(/Lampiran|attachment/i);
    expect(src).not.toMatch(/duplikat|permintaan serupa/i);
    expect(src).not.toMatch(/Setujui|Tolak|Jadikan Produk|Teruskan ke Purchasing/);
    expect(listSrc).not.toMatch(/Setujui|Tolak|Jadikan Produk/);
  });

  test("CRM receipt does not render submitted detail fields", () => {
    expect(src).toMatch(/prq-receipt-page/);
    expect(src).toMatch(/receipt_only/);
    expect(src).not.toMatch(/Lihat Detail/);
  });
});

describe("ProductRequestForm interactions", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockParams.id = undefined;
    mockHasPerm.mockImplementation((p) => p === "product_request.create");
    mockApi.productRequestContext.mockResolvedValue({
      context: { created_by: { name: "Andi Dwiputra" }, branch: { name: "Surabaya" } },
    });
    mockApi.createProductRequest.mockResolvedValue({
      request: { id: "r1", status: "draft", customer_source: "master_crm", customer_id: "C1", customer_snapshot: { name: "Renee" }, product_source: "requested_product", product_snapshot: { name: "Cup" } },
    });
    mockApi.updateProductRequest.mockResolvedValue({
      request: { id: "r1", status: "draft" },
    });
    mockApi.submitProductRequest.mockResolvedValue({
      receipt: { id: "r1", request_number: "PPR-20260920-001", status: "submitted", submitted_at: "2026-09-20T01:00:00+00:00", receipt_only: true },
    });
    mockApi.getProductRequest.mockResolvedValue({
      request: { id: "r1", status: "draft", customer_source: "master_crm", customer_id: "C1", customer_snapshot: { name: "Renee" } },
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const renderForm = async () => {
    await act(async () => {
      root.render(<ProductRequestForm />);
    });
    await flush();
  };

  test("existing and new modes work and clear stale ids", async () => {
    await renderForm();
    expect(container.querySelector('[data-testid="prq-form-page"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-summary-creator"]').textContent).toBe("Andi Dwiputra");
    expect(container.querySelector('[data-testid="prq-summary-branch"]').textContent).toBe("Surabaya");

    await act(async () => {
      container.querySelector('[data-testid="prq-customer-master"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="prq-customer-search"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-summary-customer-source"]').textContent).toBe("Master CRM");
    expect(container.querySelector('[data-testid="prq-customer-snapshot"]')).toBeTruthy();

    await act(async () => {
      container.querySelector('[data-testid="prq-customer-new"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-new-customer-name"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-customer-snapshot"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prq-summary-customer-source"]').textContent).toBe("Input CRM");

    await act(async () => {
      container.querySelector('[data-testid="prq-product-master"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="prq-product-search"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-summary-product-source"]').textContent).toBe("Master Produk");

    await act(async () => {
      container.querySelector('[data-testid="prq-product-new"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-new-product-name"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-summary-product-source"]').textContent).toBe("Requested Product");
    expect(container.querySelector('[data-testid="prq-submit"]').disabled).toBe(true);
  });

  test("draft and submit handle loading success and disable submit until complete", async () => {
    await renderForm();
    await act(async () => {
      container.querySelector('[data-testid="prq-save-draft"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(mockApi.createProductRequest).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="prq-submit"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="prq-receipt-page"]')).toBeFalsy();
  });

  test("submit sends the doc_version returned by update", async () => {
    mockParams.id = "r1";
    mockApi.getProductRequest.mockResolvedValue({
      request: {
        id: "r1",
        status: "draft",
        doc_version: 3,
        customer_source: "master_crm",
        customer_id: "C1",
        customer_snapshot: { name: "Renee" },
        product_source: "product_master",
        product_id: "P1",
        product_snapshot: { item_name: "Cup" },
        potential_qty: 12,
        frequency: "Bulanan",
        target_price: 0,
        needed_date: "2026-10-15",
      },
    });
    mockApi.updateProductRequest.mockResolvedValue({
      request: {
        id: "r1",
        status: "draft",
        doc_version: 4,
        customer_source: "master_crm",
        customer_id: "C1",
        customer_snapshot: { name: "Renee" },
        product_source: "product_master",
        product_id: "P1",
        product_snapshot: { item_name: "Cup" },
        potential_qty: 12,
        frequency: "Bulanan",
        target_price: 0,
        needed_date: "2026-10-15",
      },
    });
    await renderForm();
    expect(container.querySelector('[data-testid="prq-submit"]').disabled).toBe(false);
    await act(async () => {
      container.querySelector('[data-testid="prq-submit"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(mockApi.updateProductRequest).toHaveBeenCalledWith("r1", expect.objectContaining({ doc_version: 3 }));
    expect(mockApi.submitProductRequest).toHaveBeenCalledWith("r1", expect.objectContaining({ doc_version: 4 }));
    const submittedVersion = mockApi.submitProductRequest.mock.calls[0][1].doc_version;
    expect(submittedVersion).toBe(4);
  });

  test("CRM submitted view is receipt only", async () => {
    mockParams.id = "r1";
    mockApi.getProductRequest.mockResolvedValue({
      request: { id: "r1", request_number: "PPR-1", status: "submitted", submitted_at: "2026-09-20T01:00:00+00:00", receipt_only: true },
    });
    await renderForm();
    expect(container.querySelector('[data-testid="prq-receipt-page"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-receipt-number"]').textContent).toBe("PPR-1");
    expect(container.querySelector('[data-testid="prq-readonly-detail"]')).toBeFalsy();
    expect(container.textContent).not.toMatch(/Requested Product/);
  });
});
