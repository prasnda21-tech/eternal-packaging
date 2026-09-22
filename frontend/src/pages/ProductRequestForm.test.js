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
    expect(container.querySelector('[data-testid="prq-detail-tabs"]')).toBeFalsy();
    expect(container.textContent).not.toMatch(/Requested Product/);
    expect(container.textContent).not.toMatch(/Food Grade Paper/);
    expect(container.textContent).not.toMatch(/Referensi Customer/);
  });

  test("Holding detail page shows complete requested-product specification via tabs", async () => {
    mockParams.id = "r1";
    mockHasPerm.mockImplementation((p) => p === "product_request.read_all");
    mockApi.getProductRequest.mockResolvedValue({
      request: COMPLETE_SUBMITTED_DETAIL,
    });
    await renderForm();
    expect(container.querySelector('[data-testid="prq-readonly-page"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-receipt-page"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prq-detail-customer-name"]').textContent).toBe("Kedai Baru");
    await act(async () => {
      container.querySelector('[data-testid="prq-detail-tab-product"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-detail-product-name"]').textContent).toBe("Paper Cup 12oz");
    expect(container.querySelector('[data-testid="prq-detail-product-category"]').textContent).toBe("Paper Cup");
    expect(container.querySelector('[data-testid="prq-detail-product-size"]').textContent).toBe("12 oz");
    expect(container.querySelector('[data-testid="prq-detail-product-material"]').textContent).toBe("Food Grade Paper");
    expect(container.querySelector('[data-testid="prq-detail-product-model"]').textContent).toBe("Custom Logo");
    expect(container.querySelector('[data-testid="prq-detail-product-code"]').textContent).toBe("—");
    expect(container.querySelector('[data-testid="prq-detail-product-unit"]').textContent).toBe("—");
    await act(async () => {
      container.querySelector('[data-testid="prq-detail-tab-potential"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="prq-detail-channel"]').textContent).toBe("Referensi Customer");
    expect(container.querySelector('[data-testid="prq-detail-note"]').textContent).toBe("Customer rebranding");
  });

  const setNote = async (value) => {
    const note = container.querySelector('[data-testid="prq-note"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    await act(async () => {
      setter.call(note, value);
      note.dispatchEvent(new Event("input", { bubbles: true }));
    });
  };

  const click = async (node) => {
    await act(async () => {
      node.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
    });
  };

  test("unsaved note blocks tab and menu navigation until discard", async () => {
    await renderForm();
    await setNote("catatan belum disimpan");
    const link = document.createElement("a");
    link.href = "/penjualan/pesanan-penjualan";
    let followed = 0;
    link.addEventListener("click", () => { followed += 1; });
    document.body.appendChild(link);
    await click(link);
    expect(document.querySelector('[data-testid="prq-unsaved-dialog"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="prq-note"]').value).toBe("catatan belum disimpan");
    expect(followed).toBe(0);
    await click(document.querySelector('[data-testid="prq-unsaved-cancel"]'));
    expect(document.querySelector('[data-testid="prq-unsaved-dialog"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prq-note"]').value).toBe("catatan belum disimpan");
    expect(followed).toBe(0);

    const tab = document.createElement("div");
    tab.setAttribute("data-workspace-tab", "1");
    tab.setAttribute("data-path", "/penjualan/pesanan-penjualan");
    tab.setAttribute("data-active", "0");
    const label = document.createElement("button");
    label.className = "kb-workspace-tab-label";
    tab.appendChild(label);
    document.body.appendChild(tab);
    await click(label);
    expect(document.querySelector('[data-testid="prq-unsaved-dialog"]')).toBeTruthy();
    await click(document.querySelector('[data-testid="prq-unsaved-discard"]'));
    expect(document.querySelector('[data-testid="prq-unsaved-dialog"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="prq-note"]').value).toBe("catatan belum disimpan");
    link.remove();
    tab.remove();
  });

  test("beforeunload is armed only while the form is dirty and is removed once", async () => {
    const added = [];
    const removed = [];
    const origAdd = window.addEventListener.bind(window);
    const origRemove = window.removeEventListener.bind(window);
    window.addEventListener = (type, fn, cap) => {
      if (type === "beforeunload") added.push(fn);
      return origAdd(type, fn, cap);
    };
    window.removeEventListener = (type, fn, cap) => {
      if (type === "beforeunload") removed.push(fn);
      return origRemove(type, fn, cap);
    };
    try {
      await renderForm();
      expect(added).toHaveLength(0);
      await setNote("belum disimpan");
      expect(added).toHaveLength(1);
      await setNote("belum disimpan lagi");
      expect(added).toHaveLength(1);
      const event = {
        defaultPrevented: false,
        returnValue: undefined,
        preventDefault() { this.defaultPrevented = true; },
      };
      added[0](event);
      expect(event.defaultPrevented).toBe(true);
      expect(event.returnValue).toBe("");
      await setNote("");
      expect(removed).toEqual([added[0]]);
      const clean = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(clean);
      expect(clean.defaultPrevented).toBe(false);
    } finally {
      window.addEventListener = origAdd;
      window.removeEventListener = origRemove;
    }
  });

  test("unchanged form does not warn", async () => {
    await renderForm();
    const link = document.createElement("a");
    link.href = "/dashboard";
    let followed = 0;
    link.addEventListener("click", () => { followed += 1; });
    document.body.appendChild(link);
    await click(link);
    expect(document.querySelector('[data-testid="prq-unsaved-dialog"]')).toBeFalsy();
    expect(followed).toBe(1);
    link.remove();
  });

  test("failed save keeps the note dirty and successful save does not warn", async () => {
    mockApi.createProductRequest.mockRejectedValueOnce(new Error("jaringan"));
    await renderForm();
    await setNote("tetap di form");
    await click(container.querySelector('[data-testid="prq-save-draft"]'));
    await flush();
    expect(container.querySelector('[data-testid="prq-note"]').value).toBe("tetap di form");
    const link = document.createElement("a");
    link.href = "/dashboard";
    document.body.appendChild(link);
    await click(link);
    expect(document.querySelector('[data-testid="prq-unsaved-dialog"]')).toBeTruthy();
    await click(document.querySelector('[data-testid="prq-unsaved-cancel"]'));
    link.remove();

    mockApi.createProductRequest.mockResolvedValueOnce({
      request: { id: "r9", status: "draft", request_note: "tersimpan" },
    });
    await setNote("tersimpan");
    await click(container.querySelector('[data-testid="prq-save-draft"]'));
    await flush();
    expect(mockNavigate).toHaveBeenCalledWith("/penjualan/permintaan-produk/r9", { replace: true });
    expect(container.querySelector('[data-testid="prq-note"]').value).toBe("tersimpan");
    const after = document.createElement("a");
    after.href = "/dashboard";
    let followed = 0;
    after.addEventListener("click", () => { followed += 1; });
    document.body.appendChild(after);
    await click(after);
    expect(document.querySelector('[data-testid="prq-unsaved-dialog"]')).toBeFalsy();
    expect(followed).toBe(1);
    after.remove();
  });

  test("successful submit leaves the receipt without a warning", async () => {
    mockParams.id = "r1";
    mockApi.getProductRequest.mockResolvedValue({
      request: {
        id: "r1",
        status: "draft",
        doc_version: 1,
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
        request_note: "siap",
      },
    });
    await renderForm();
    await setNote("diubah lalu dikirim");
    await click(container.querySelector('[data-testid="prq-submit"]'));
    await flush();
    expect(container.querySelector('[data-testid="prq-receipt-page"]')).toBeTruthy();
    const link = document.createElement("a");
    link.href = "/penjualan/pesanan-penjualan";
    let followed = 0;
    link.addEventListener("click", () => { followed += 1; });
    document.body.appendChild(link);
    await click(link);
    expect(document.querySelector('[data-testid="prq-unsaved-dialog"]')).toBeFalsy();
    expect(followed).toBe(1);
    link.remove();
  });
});
