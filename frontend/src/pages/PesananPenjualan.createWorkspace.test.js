import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import api from "../lib/api";
import PesananPenjualan, { buildSalesOrderCreatePayload, validateSalesOrderCreate } from "./PesananPenjualan";

const mockNavigate = jest.fn();
const mockSetSearchParams = jest.fn();

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: "/penjualan/pesanan-penjualan", search: "", state: null }),
  useSearchParams: () => [new URLSearchParams(), mockSetSearchParams],
  useParams: () => ({}),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", name: "Sales User", email: "sales@example.com", role: "admin" },
    hasPerm: () => true,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listSalesOrders: jest.fn(),
    salesOrderOptions: jest.fn(),
    createSalesOrder: jest.fn(),
    salesOrderRepeatSource: jest.fn(),
    masterItemsPicker: jest.fn(),
    salesOrderItemImages: jest.fn(),
    salesOrderPriceQuote: jest.fn().mockResolvedValue({ status: "missing", unit_price: null }),
  },
}));

jest.mock("../components/ui/popover", () => ({
  Popover: ({ children }) => <div>{children}</div>,
  PopoverTrigger: ({ children }) => children,
  PopoverContent: ({ children }) => <div>{children}</div>,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

Element.prototype.scrollIntoView = () => {};

const MASTER = {
  id: "c1",
  name: "PT Maju",
  phone: "081111",
  address: "Jl. Master",
  pic: "Rina",
  email: "maju@example.com",
  city: "Jakarta",
  is_active: true,
};

function setNativeValue(el, value) {
  const proto = el instanceof HTMLTextAreaElement
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

async function tick(ms) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

describe("Buat Pesanan Penjualan compact workspace", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.listSalesOrders.mockReset();
    api.listSalesOrders.mockResolvedValue({ orders: [] });
    api.salesOrderOptions.mockReset();
    api.salesOrderOptions.mockResolvedValue({
      suggested_po_number: "PO-OPEN",
      master_customers: [MASTER],
      outlets_by_customer: { "PT Maju": [{ outlet: "Pusat", address: "Jl. Master", recipient: "Rina" }] },
      sales_options: ["Vira"],
    });
    api.createSalesOrder.mockReset();
    api.createSalesOrder.mockResolvedValue({ message: "Pesanan Penjualan berhasil dibuat!", order: { id: "po-1" } });
    api.salesOrderRepeatSource.mockReset();
    api.masterItemsPicker.mockReset();
    api.masterItemsPicker.mockResolvedValue({ items: [] });
    api.salesOrderItemImages.mockReset();
    api.salesOrderItemImages.mockResolvedValue({ images: {} });
    api.salesOrderPriceQuote.mockReset();
    api.salesOrderPriceQuote.mockResolvedValue({ status: "missing", unit_price: null });
    mockNavigate.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function renderPage() {
    await act(async () => {
      root.render(<PesananPenjualan />);
    });
    await tick(0);
  }

  async function openCreate() {
    await renderPage();
    await act(async () => {
      container.querySelector('[data-testid="create-sales-order-btn"]').click();
    });
    await tick(0);
  }

  function mainRegion() {
    return container.querySelector('[data-so-region="main"]');
  }

  test("list page is unchanged and create workspace is isolated", async () => {
    await renderPage();
    const page = container.querySelector('[data-testid="sales-order-page"]');
    expect(page.className).toContain("penjualan-page");
    expect(page.className).not.toContain("so-create-workspace");
    expect(container.querySelector('[data-testid="sales-order-title"]').textContent).toBe("Pesanan Penjualan");
    expect(container.querySelector('[data-testid="so-create-actionbar"]')).toBeNull();
    expect(container.querySelector('[data-testid="btn-detail-tambahan"]')).toBeNull();
  });

  test("required fields stay on the main page and extra fields stay in the summary", async () => {
    await openCreate();
    const main = mainRegion();
    ["input-po-number", "btn-generate-po", "input-order-date", "input-customer", "btn-repeat-order", "input-customer-phone", "input-address", "input-outlet", "input-estimated-delivery"]
      .forEach((id) => {
        expect(main.querySelector(`[data-testid="${id}"]`)).toBeTruthy();
      });
    const extra = container.querySelector('[data-testid="so-extra-fields"]');
    expect(extra).toBeTruthy();
    ["input-sales", "input-created-by-user", "input-recipient", "input-delivery-zone", "input-tanggal-pengiriman", "input-payment-terms", "input-expedition"]
      .forEach((id) => {
        expect(extra.querySelector(`[data-testid="${id}"]`)).toBeTruthy();
        expect(main.querySelector(`[data-testid="${id}"]`)).toBeNull();
      });
    expect(container.querySelector('[data-testid="so-order-summary"]').querySelector('[data-testid="input-notes"]')).toBeTruthy();
    expect(extra.querySelector('[data-testid="input-customer-phone"]')).toBeNull();
    expect(container.querySelector('[data-testid="so-extra-drawer"]')).toBeNull();
    expect(container.querySelector('[data-testid="btn-detail-tambahan"]')).toBeNull();
  });

  test("outlet and notes stay on the form without a drawer", async () => {
    await openCreate();
    const outlet = container.querySelector('[data-testid="input-outlet"]');
    await act(async () => {
      setNativeValue(outlet, "Cabang Tebet");
      setNativeValue(container.querySelector('[data-testid="input-notes"]'), "Catatan tetap");
    });
    expect(container.querySelector('[data-testid="input-outlet"]').value).toBe("Cabang Tebet");
    expect(container.querySelector('[data-testid="input-notes"]').value).toBe("Catatan tetap");
    expect(container.querySelector('[data-testid="input-po-number"]').value).toBe("PO-OPEN");
    expect(container.querySelector('[data-testid="so-extra-drawer"]')).toBeNull();
  });

  test("changing customer clears the previous customer's outlet", async () => {
    api.salesOrderOptions.mockResolvedValue({
      suggested_po_number: "PO-OPEN",
      master_customers: [
        MASTER,
        { id: "c2", name: "PT Baru", phone: "08222", address: "Jl. Baru", is_active: true },
      ],
      outlets_by_customer: {
        "PT Maju": [{ id: "o1", outlet: "Pusat", address: "Jl. Master" }],
        "PT Baru": [
          { id: "o2", outlet: "Cabang", address: "Jl. Cabang" },
          { id: "o3", outlet: "Gudang", address: "Jl. Gudang" },
        ],
      },
      sales_options: [],
    });
    await openCreate();
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "Maju");
    });
    await tick(180);
    await act(async () => {
      container.querySelector('[data-testid="input-customer-option-c1"]').click();
    });
    expect(container.querySelector('[data-testid="input-outlet"]').value).toBe("Pusat");
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "Baru");
    });
    await tick(180);
    await act(async () => {
      container.querySelector('[data-testid="input-customer-option-c2"]').click();
    });
    expect(container.querySelector('[data-testid="input-outlet"]').value).toBe("");
    expect(container.querySelector('[data-testid="so-order-summary"]').textContent).not.toContain("Pusat");
  });

  test("summary keeps different units separate and drops the ui-only outlet id", async () => {
    await openCreate();
    await act(async () => {
      container.querySelector('[data-testid="btn-add-line-item"]').click();
    });
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-qty-0"]'), "3");
      setNativeValue(container.querySelector('[data-testid="line-unit-0"]'), "pcs");
      setNativeValue(container.querySelector('[data-testid="line-qty-1"]'), "2");
      setNativeValue(container.querySelector('[data-testid="line-unit-1"]'), "dus");
    });
    const qty = container.querySelector('[data-testid="so-total-qty"]').textContent;
    expect(qty).toContain("3 pcs");
    expect(qty).toContain("2 dus");
    expect(qty).not.toContain("5 pcs");
    const payload = buildSalesOrderCreatePayload({
      customer: "PT Maju",
      outlet: "Pusat",
      outlet_id: "o1",
      line_items: [],
    });
    expect(payload.outlet).toBe("Pusat");
    expect(payload.outlet_id).toBeUndefined();
  });

  test("a zone error focuses the field in detail tambahan", async () => {
    await openCreate();
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "Pelanggan Baru");
      setNativeValue(container.querySelector('[data-testid="input-customer-phone"]'), "0812");
      setNativeValue(container.querySelector('[data-testid="input-address"]'), "Jl. Baru");
    });
    await act(async () => {
      container.querySelector('[data-testid="line-mode-manual-0"]').click();
    });
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-name-0"]'), "Dus Manual");
    });
    api.createSalesOrder.mockRejectedValue({
      response: { data: { detail: { message: "Zona Kirim tidak dikenali" } } },
    });
    await act(async () => {
      container.querySelector('[data-testid="btn-submit-sales-order"]').click();
    });
    await tick(0);
    const extra = container.querySelector('[data-testid="so-extra-fields"]');
    expect(extra).toBeTruthy();
    expect(container.querySelector('[data-testid="form-error-banner"]').textContent).toContain("Zona Kirim tidak dikenali");
    expect(document.activeElement).toBe(container.querySelector('[data-testid="input-delivery-zone"]'));
    expect(extra.querySelector('[data-testid="input-delivery-zone"]').getAttribute("aria-invalid")).toBe("true");
    expect(container.querySelector('[data-testid="so-extra-drawer"]')).toBeNull();
  });

  test("phone validation stays on the main page and does not open the drawer", async () => {
    await openCreate();
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "Pelanggan Baru");
    });
    await act(async () => {
      container.querySelector('[data-testid="line-mode-manual-0"]').click();
    });
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-name-0"]'), "Dus");
    });
    await act(async () => {
      container.querySelector('[data-testid="sales-order-form"]').requestSubmit();
    });
    expect(container.querySelector('[data-testid="form-error-banner"]').textContent).toContain("No. HP / WhatsApp");
    expect(container.querySelector('[data-testid="so-extra-drawer"]')).toBeNull();
    expect(mainRegion().querySelector('[data-testid="input-customer-phone"]').getAttribute("aria-invalid")).toBe("true");
    expect(api.createSalesOrder).not.toHaveBeenCalled();
  });

  test("auto PO, customer search, repeat order, and totals keep their behavior", async () => {
    api.salesOrderOptions
      .mockResolvedValueOnce({
        suggested_po_number: "PO-OPEN",
        master_customers: [MASTER],
        outlets_by_customer: {},
        sales_options: [],
      })
      .mockResolvedValueOnce({ suggested_po_number: "PO-AUTO-2" });
    await openCreate();
    expect(container.querySelector('[data-testid="input-po-number"]').value).toBe("PO-OPEN");
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-po-number"]'), "");
      container.querySelector('[data-testid="btn-generate-po"]').click();
    });
    await tick(0);
    expect(container.querySelector('[data-testid="input-po-number"]').value).toBe("PO-AUTO-2");

    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "Maju");
    });
    await tick(180);
    await act(async () => {
      container.querySelector('[data-testid="input-customer-option-c1"]').click();
    });
    expect(container.querySelector('[data-testid="badge-customer-registered"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="btn-repeat-order"]').disabled).toBe(false);

    api.salesOrderRepeatSource.mockResolvedValue({
      found: true,
      source: {
        source_po_number: "PO-LAMA",
        customer: "PT Maju",
        customer_id: "c1",
        outlet: "Outlet Lama",
        recipient: "Budi",
        address: "Jl. Lama",
        notes: "Catatan lama",
        sales: "Vira",
        line_items: [
          { name: "Cup", item_code: "C1", quantity: 4, unit: "pcs", color: "", production_process: "print", notes: "", accurate_item_id: null, master_item_id: null },
          { name: "Cup", item_code: "C1", quantity: 6, unit: "pcs", color: "Merah", production_process: "print", notes: "beda", accurate_item_id: null, master_item_id: null },
        ],
      },
    });
    await act(async () => {
      container.querySelector('[data-testid="btn-repeat-order"]').click();
    });
    await tick(0);
    expect(container.querySelector('[data-testid="repeat-confirm-modal"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="btn-repeat-confirm"]').click();
    });
    expect(container.querySelector('[data-testid="repeat-source-indicator"]').textContent).toContain("PO-LAMA");
    expect(container.querySelectorAll('[data-testid^="line-item-row-"]')).toHaveLength(2);
    expect(container.querySelector('[data-testid="so-total-items"]').textContent).toContain("2");
    expect(container.querySelector('[data-testid="so-total-qty"]').textContent).toContain("10");
    expect(container.querySelector('[data-testid="input-outlet"]').value).toBe("Outlet Lama");
    expect(container.querySelector('[data-testid="input-notes"]').value).toBe("Catatan lama");
  });

  test("master search and manual entry do not mix identity, and rows are not merged", async () => {
    api.masterItemsPicker.mockResolvedValue({
      items: [{
        id: "m1",
        accurate_item_id: "a1",
        item_name: "Box Karton",
        item_code: "BX-01",
        unit: "pcs",
      }],
    });
    await openCreate();
    const search = container.querySelector('[data-testid="line-item-search-0-input"]');
    await act(async () => {
      search.focus();
      setNativeValue(search, "box");
    });
    await tick(350);
    const option = document.body.querySelector('[data-testid="line-item-search-0-option-a1"]');
    expect(option).toBeTruthy();
    await act(async () => {
      option.click();
    });
    expect(container.querySelector('[data-testid="line-code-0"]').value).toBe("BX-01");
    await tick(0);
    expect(api.salesOrderItemImages).toHaveBeenCalledWith(
      { accurate_item_ids: "a1" },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    await act(async () => {
      container.querySelector('[data-testid="line-mode-manual-0"]').click();
    });
    const manual = container.querySelector('[data-testid="line-name-0"]');
    expect(manual.value).toBe("Box Karton");
    await act(async () => {
      setNativeValue(manual, "Nama Manual");
      container.querySelector('[data-testid="btn-add-line-item"]').click();
    });
    await act(async () => {
      container.querySelector('[data-testid="line-mode-manual-1"]').click();
    });
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-name-1"]'), "Nama Manual");
      setNativeValue(container.querySelector('[data-testid="line-qty-0"]'), "2");
      setNativeValue(container.querySelector('[data-testid="line-qty-1"]'), "3");
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "Pelanggan Baru");
      setNativeValue(container.querySelector('[data-testid="input-customer-phone"]'), "08123");
      setNativeValue(container.querySelector('[data-testid="input-address"]'), "Jl. Baru");
    });
    expect(container.querySelector('[data-testid="so-total-qty"]').textContent).toContain("5");
    await act(async () => {
      container.querySelector('[data-testid="btn-submit-sales-order"]').click();
    });
    await tick(0);
    expect(api.createSalesOrder).toHaveBeenCalledTimes(1);
    const payload = api.createSalesOrder.mock.calls[0][0];
    expect(payload.line_items).toHaveLength(2);
    expect(payload.line_items[0].name).toBe("Nama Manual");
    expect(payload.line_items[0].accurate_item_id).toBeNull();
    expect(payload.line_items[0].master_item_id).toBeNull();
    expect(payload.line_items[0].item_code).toBe("BX-01");
    expect(payload.line_items[0].quantity).toBe(2);
    expect(payload.line_items[1].name).toBe("Nama Manual");
    expect(payload.line_items[1].quantity).toBe(3);
    expect(payload.customer).toBe("Pelanggan Baru");
    expect(payload.customer_phone).toBe("08123");
    expect(payload.address).toBe("Jl. Baru");
    expect(payload.entry_mode).toBeUndefined();
  });

  test("remove line keeps the remaining row and the last row cannot be removed", async () => {
    await openCreate();
    await act(async () => {
      container.querySelector('[data-testid="btn-add-line-item"]').click();
    });
    expect(container.querySelectorAll('[data-testid^="line-item-row-"]')).toHaveLength(2);
    await act(async () => {
      container.querySelector('[data-testid="btn-remove-line-0"]').click();
    });
    expect(container.querySelectorAll('[data-testid^="line-item-row-"]')).toHaveLength(1);
    expect(container.querySelector('[data-testid="btn-remove-line-0"]').disabled).toBe(true);
  });

  test("save stays visible, disables while saving, and ignores a second submit", async () => {
    let release;
    api.createSalesOrder.mockImplementation(() => new Promise((resolve) => {
      release = resolve;
    }));
    await openCreate();
    await act(async () => {
      container.querySelector('[data-testid="line-mode-manual-0"]').click();
    });
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-name-0"]'), "Dus");
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "Pelanggan Baru");
      setNativeValue(container.querySelector('[data-testid="input-customer-phone"]'), "0812");
      setNativeValue(container.querySelector('[data-testid="input-address"]'), "Jl");
    });
    const bar = container.querySelector('[data-testid="so-create-actionbar"]');
    const submit = bar.querySelector('[data-testid="btn-submit-sales-order"]');
    expect(submit.textContent).toContain("Simpan Pesanan");
    await act(async () => {
      submit.click();
      submit.click();
    });
    expect(api.createSalesOrder).toHaveBeenCalledTimes(1);
    expect(submit.disabled).toBe(true);
    expect(submit.textContent).toContain("Menyimpan...");
    expect(container.querySelector('[data-testid="btn-cancel-form"]').disabled).toBe(true);
    await act(async () => {
      release({ message: "ok", order: { id: "new" } });
    });
    await tick(0);
    expect(submit.disabled).toBe(true);
    await act(async () => {
      container.querySelector('[data-testid="sales-order-form"]').requestSubmit();
    });
    expect(api.createSalesOrder).toHaveBeenCalledTimes(1);
  });

  function requiredMarker(field) {
    return Boolean(container.querySelector(`[data-so-field="${field}"] .so-req`));
  }

  function formFromScreen() {
    const value = (id) => container.querySelector(`[data-testid="${id}"]`)?.value ?? "";
    const rows = [...container.querySelectorAll('[data-testid^="line-item-row-"]')];
    return {
      customer: value("input-customer"),
      customer_id: container.querySelector('[data-testid="badge-customer-registered"]') ? "selected" : null,
      customer_phone: value("input-customer-phone"),
      address: value("input-address"),
      line_items: rows.map((_, index) => ({
        name: container.querySelector(`[data-testid="line-name-${index}"]`)?.value
          || container.querySelector(`[data-testid="line-item-search-${index}-input"]`)?.value
          || "",
        quantity: container.querySelector(`[data-testid="line-qty-${index}"]`)?.value,
      })),
    };
  }

  test("required markers follow validateSalesOrderCreate for new and existing customers", async () => {
    await openCreate();
    expect(requiredMarker("po_number")).toBe(false);
    expect(requiredMarker("order_date")).toBe(false);
    expect(requiredMarker("customer")).toBe(true);
    expect(requiredMarker("customer_phone")).toBe(true);
    expect(requiredMarker("address")).toBe(true);
    expect(container.querySelector(".so-col-name").textContent).toContain("*");
    expect(container.querySelector(".so-col-qty").textContent).toContain("*");
    expect(container.querySelector('[data-testid="so-progress-label"]').textContent).toBe("0/4 wajib");
    expect(container.querySelector('[data-testid="so-status-chip"]').textContent).toBe("Belum Lengkap");
    expect(validateSalesOrderCreate(formFromScreen())).not.toBe("");

    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "Maju");
    });
    await tick(180);
    await act(async () => {
      container.querySelector('[data-testid="input-customer-option-c1"]').click();
    });
    expect(requiredMarker("po_number")).toBe(false);
    expect(requiredMarker("order_date")).toBe(false);
    expect(requiredMarker("customer")).toBe(true);
    expect(requiredMarker("customer_phone")).toBe(false);
    expect(requiredMarker("address")).toBe(false);
    expect(container.querySelector(".so-col-name").textContent).toContain("*");
    expect(container.querySelector(".so-col-qty").textContent).toContain("*");
    expect(container.querySelector('[data-testid="so-progress-label"]').textContent).toBe("1/2 wajib");

    await act(async () => {
      container.querySelector('[data-testid="line-mode-manual-0"]').click();
    });
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-name-0"]'), "Dus");
    });
    const screenForm = formFromScreen();
    const validationError = validateSalesOrderCreate(screenForm);
    expect(validationError).toBe("");
    expect(container.querySelector('[data-testid="so-progress-label"]').textContent).toBe("2/2 wajib");
    expect(container.querySelector('[data-testid="so-status-chip"]').textContent).toBe(
      validationError === "" ? "Lengkap" : "Belum Lengkap"
    );
  });

  test("progress follows new-customer and registered-customer validation", async () => {
    await openCreate();
    expect(container.querySelector('[data-testid="so-progress-label"]').textContent).toBe("0/4 wajib");
    expect(container.querySelector('[data-testid="so-status-chip"]').textContent).toBe("Belum Lengkap");

    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "Maju");
    });
    await tick(180);
    await act(async () => {
      container.querySelector('[data-testid="input-customer-option-c1"]').click();
    });
    expect(container.querySelector('[data-testid="so-progress-label"]').textContent).toBe("1/2 wajib");
    expect(container.querySelector('[data-testid="so-status-chip"]').textContent).toBe("Belum Lengkap");

    await act(async () => {
      container.querySelector('[data-testid="line-mode-manual-0"]').click();
    });
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-name-0"]'), "Dus");
    });
    expect(container.querySelector('[data-testid="so-progress-label"]').textContent).toBe("2/2 wajib");
    expect(container.querySelector('[data-testid="so-status-chip"]').textContent).toBe("Lengkap");

    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "Pelanggan Baru");
      setNativeValue(container.querySelector('[data-testid="input-customer-phone"]'), "");
    });
    expect(container.querySelector('[data-testid="so-progress-label"]').textContent).toMatch(/\/4 wajib/);
    expect(container.querySelector('[data-testid="so-status-chip"]').textContent).toBe("Belum Lengkap");

    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-customer-phone"]'), "0812");
      setNativeValue(container.querySelector('[data-testid="input-address"]'), "Jl. Baru");
    });
    expect(container.querySelector('[data-testid="so-progress-label"]').textContent).toBe("4/4 wajib");
    expect(container.querySelector('[data-testid="so-status-chip"]').textContent).toBe("Lengkap");
  });

  test("removing the first row keeps later modes and line data", async () => {
    await openCreate();
    await act(async () => {
      container.querySelector('[data-testid="btn-add-line-item"]').click();
    });
    await act(async () => {
      container.querySelector('[data-testid="btn-add-line-item"]').click();
    });
    await act(async () => {
      container.querySelector('[data-testid="line-mode-manual-1"]').click();
    });
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-name-1"]'), "MANUAL-2");
      setNativeValue(container.querySelector('[data-testid="line-code-1"]'), "KODE-2");
      setNativeValue(container.querySelector('[data-testid="line-code-2"]'), "KODE-3");
      setNativeValue(container.querySelector('[data-testid="line-qty-2"]'), "7");
    });
    expect(container.querySelector('[data-testid="line-mode-master-0"]').getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector('[data-testid="line-mode-manual-1"]').getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector('[data-testid="line-mode-master-2"]').getAttribute("aria-pressed")).toBe("true");
    await act(async () => {
      container.querySelector('[data-testid="btn-remove-line-0"]').click();
    });
    expect(container.querySelector('[data-testid="line-name-0"]').value).toBe("MANUAL-2");
    expect(container.querySelector('[data-testid="line-code-0"]').value).toBe("KODE-2");
    expect(container.querySelector('[data-testid="line-mode-manual-0"]').getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector('[data-testid="line-name-1"]')).toBeNull();
    expect(container.querySelector('[data-testid="line-item-search-1-input"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="line-code-1"]').value).toBe("KODE-3");
    expect(container.querySelector('[data-testid="line-qty-1"]').value).toBe("7");
  });

  test("repeated mode changes do not keep stale master ids or manual names", async () => {
    api.masterItemsPicker.mockResolvedValue({
      items: [{ id: "m1", accurate_item_id: "a1", item_name: "Box Karton", item_code: "BX-01", unit: "pcs" }],
    });
    await openCreate();
    const search = container.querySelector('[data-testid="line-item-search-0-input"]');
    await act(async () => {
      search.focus();
      setNativeValue(search, "box");
    });
    await tick(350);
    await act(async () => {
      document.body.querySelector('[data-testid="line-item-search-0-option-a1"]').click();
    });
    await act(async () => {
      container.querySelector('[data-testid="line-mode-manual-0"]').click();
    });
    expect(container.querySelector('[data-testid="line-name-0"]').value).toBe("Box Karton");
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-name-0"]'), "Nama Lama");
    });
    await act(async () => {
      container.querySelector('[data-testid="line-mode-master-0"]').click();
    });
    expect(container.querySelector('[data-testid="line-name-0"]')).toBeNull();
    await act(async () => {
      container.querySelector('[data-testid="line-mode-manual-0"]').click();
    });
    expect(container.querySelector('[data-testid="line-name-0"]').value).toBe("");
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-name-0"]'), "Nama Baru");
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "Pelanggan Baru");
      setNativeValue(container.querySelector('[data-testid="input-customer-phone"]'), "0812");
      setNativeValue(container.querySelector('[data-testid="input-address"]'), "Jl");
    });
    await act(async () => {
      container.querySelector('[data-testid="btn-submit-sales-order"]').click();
    });
    await tick(0);
    const line = api.createSalesOrder.mock.calls[0][0].line_items[0];
    expect(line.name).toBe("Nama Baru");
    expect(line.accurate_item_id).toBeNull();
    expect(line.master_item_id).toBeNull();
    expect(line.rowKey).toBeUndefined();
    expect(line.entry_mode).toBeUndefined();
  });

  test("leaving create returns to the list without a drawer", async () => {
    await openCreate();
    await act(async () => {
      container.querySelector('[data-testid="back-to-list-btn"]').click();
    });
    const page = container.querySelector('[data-testid="sales-order-page"]');
    expect(page.className).toContain("penjualan-page");
    expect(page.className).not.toContain("so-create-workspace");
    expect(container.querySelector('[data-testid="so-extra-drawer"]')).toBeNull();
    expect(document.body.style.overflow).not.toBe("hidden");
    expect(api.createSalesOrder).not.toHaveBeenCalled();
  });

  test("Batal leaves create without submitting", async () => {
    await openCreate();
    await act(async () => {
      container.querySelector('[data-testid="btn-cancel-form"]').click();
    });
    expect(container.querySelector('[data-testid="sales-order-page"]').className).toContain("penjualan-page");
    expect(api.createSalesOrder).not.toHaveBeenCalled();
  });

  test("workspace CSS locks the page and scrolls the item table internally", () => {
    const css = fs.readFileSync(path.join(__dirname, "pesananPenjualan.css"), "utf8");
    const page = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
    const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
    expect(page).toMatch(/hasPerm\("sales_order\.create"\) \|\| hasPerm\("po\.edit"\) \|\| user\?\.role === "admin"/);
    expect(css).not.toMatch(/--so-notif-safe:\s*96px/);
    expect(css).toMatch(/\.app-shell:has\(\.so-create-workspace\)\s*\{[^}]*overflow:\s*hidden/);
    expect(css).toMatch(/\.content:has\(\.so-create-workspace\)\s*\{[^}]*overflow:\s*hidden/);
    expect(css).toMatch(/\.so-create-lines-scroll\s*\{[^}]*overflow-y:\s*auto/);
    expect(css).toMatch(/\.so-create-lines-scroll\s*\{[^}]*min-height:\s*0/);
    expect(css).toMatch(/\.so-list-table-scroll\s*\{[^}]*overflow:\s*auto/);
    expect(css).toMatch(/\.so-create-actionbar\s*\{[^}]*position:\s*static/);
    expect(css).not.toMatch(/\.so-create-actionbar\s*\{[^}]*position:\s*fixed/);
    expect(css).not.toMatch(/\.so-create-actionbar\s*\{[^}]*position:\s*sticky/);
    expect(css).toMatch(/@media \(min-width: 1024px\)\s*\{\s*\.so-create-workspace \.so-create-actionbar\s*\{[^}]*padding-right:\s*72px/);
    expect(css).not.toMatch(/(^|\n)\s*(input|table|\.card|\.btn)\s*[,{]/);
    expect(css).not.toMatch(/\.po-detail/);
    expect(detail).not.toMatch(/so-create-workspace/);
    expect(page).toMatch(/data-testid="so-create-actionbar"/);
    expect(page).toMatch(/data-testid="btn-submit-sales-order"/);
    expect(page).toMatch(/data-scroll-owner=\{viewMode === "create" \? "workspace" : "list"\}/);
  });

  test("first row is visible as number 1 and there is no global item search", async () => {
    await openCreate();
    expect(container.querySelector('[data-testid="line-items-search-input"]')).toBeNull();
    expect(container.querySelector('[data-testid="line-item-row-0"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="line-item-number-0"]').textContent).toBe("1");
    expect(container.querySelector('[data-testid="line-item-search-0-input"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="sales-order-page"]').getAttribute("data-scroll-owner")).toBe("workspace");
    const table = container.querySelector('[data-testid="line-items-table-container"]');
    expect(table.getAttribute("data-scroll-owner")).toBe("line-items");
  });

  test("item autocomplete portals to the document and closes on Escape and outside click", async () => {
    api.masterItemsPicker.mockResolvedValue({
      items: [{ id: "m1", accurate_item_id: "a1", item_name: "Box Karton", item_code: "BX-01", unit: "pcs" }],
    });
    await openCreate();
    const search = container.querySelector('[data-testid="line-item-search-0-input"]');
    await act(async () => {
      search.focus();
      setNativeValue(search, "box");
    });
    await tick(350);
    const list = document.body.querySelector('[data-testid="line-item-search-0-list"]');
    expect(list).toBeTruthy();
    expect(list.getAttribute("data-overlay")).toBe("portal");
    expect(container.querySelector('[data-testid="line-item-search-0-list"]')).toBeNull();
    await act(async () => {
      search.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(document.body.querySelector('[data-testid="line-item-search-0-list"]')).toBeNull();
    await act(async () => {
      search.focus();
      setNativeValue(search, "box");
    });
    await tick(350);
    expect(document.body.querySelector('[data-testid="line-item-search-0-list"]')).toBeTruthy();
    await act(async () => {
      document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(document.body.querySelector('[data-testid="line-item-search-0-list"]')).toBeNull();
  });

  test("price quote is a real client method, not a test-only guard", () => {
    const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
    const page = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
    expect(apiSrc).toMatch(/salesOrderPriceQuote:\s*\(payload[\s\S]*?\/sales-orders\/price-quote/);
    expect(page).toMatch(/api\.salesOrderPriceQuote\(/);
    expect(page).not.toMatch(/typeof api\.salesOrderPriceQuote/);
  });

  async function chooseFirstMaster(query) {
    const search = container.querySelector('[data-testid="line-item-search-0-input"]');
    await act(async () => {
      search.focus();
      setNativeValue(search, query);
    });
    await tick(350);
    await act(async () => {
      document.body.querySelector('[data-testid="line-item-search-0-option-a1"]').click();
    });
    await tick(0);
  }

  test("selecting an item loads the selling price and the saved payload keeps it", async () => {
    api.masterItemsPicker.mockResolvedValue({
      items: [{ id: "m-cup", accurate_item_id: "a1", item_name: "Cup PET", item_code: "BRG-CP001", unit: "pcs" }],
    });
    api.salesOrderPriceQuote.mockResolvedValue({
      status: "matched",
      unit_price: 700,
      source: "pengaturan_harga",
    });
    await openCreate();
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-qty-0"]'), "1000");
    });
    await chooseFirstMaster("Cup");
    expect(api.salesOrderPriceQuote).toHaveBeenCalledWith({
      accurate_item_id: "a1",
      unit: "pcs",
      quantity: 1000,
    });
    expect(container.querySelector('[data-testid="line-price-0"]').value).toBe("700");
    expect(container.querySelector('[data-testid="line-price-source-0"]').textContent).toBe("Harga dari Pengaturan Harga");
    expect(container.querySelector('[data-testid="line-subtotal-0"]').textContent).toContain("700.000");
    await act(async () => {
      container.querySelector('[data-testid="so-tab-ship"]').click();
    });
    await act(async () => {
      container.querySelector('[data-testid="so-tab-items"]').click();
    });
    expect(container.querySelector('[data-testid="line-price-0"]').value).toBe("700");
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "GOHO COFFEE");
      setNativeValue(container.querySelector('[data-testid="input-customer-phone"]'), "0812");
      setNativeValue(container.querySelector('[data-testid="input-address"]'), "Jl. Malioboro");
    });
    await act(async () => {
      container.querySelector('[data-testid="btn-submit-sales-order"]').click();
    });
    await tick(0);
    const line = api.createSalesOrder.mock.calls[0][0].line_items[0];
    expect(line.unit_price).toBe(700);
    expect(line.price_source).toBe("pengaturan_harga");
    expect(line.quantity).toBe(1000);
    expect(line.unit_price).not.toBe(0);
    expect(line.price_message).toBeUndefined();
  });

  test("a failed or missing quote stays blank and explains that manual input is required", async () => {
    api.masterItemsPicker.mockResolvedValue({
      items: [{ id: "m-cup", accurate_item_id: "a1", item_name: "Cup PET", item_code: "BRG-CP001", unit: "pcs" }],
    });
    api.salesOrderPriceQuote.mockRejectedValue(new Error("quote down"));
    await openCreate();
    await chooseFirstMaster("Cup");
    expect(container.querySelector('[data-testid="line-price-0"]').value).toBe("");
    expect(container.querySelector('[data-testid="line-price-source-0"]').textContent).toBe("Harga jual tidak dapat diambil. Isi manual.");
    expect(container.querySelector('[data-testid="line-subtotal-0"]').textContent).not.toContain("Rp0");

    api.salesOrderPriceQuote.mockResolvedValue({ status: "missing", unit_price: 0 });
    await act(async () => {
      container.querySelector('[data-testid="btn-add-line-item"]').click();
    });
    const search = container.querySelector('[data-testid="line-item-search-1-input"]');
    await act(async () => {
      search.focus();
      setNativeValue(search, "Lid");
    });
    await tick(350);
    await act(async () => {
      document.body.querySelector('[data-testid="line-item-search-1-option-a1"]').click();
    });
    await tick(0);
    expect(container.querySelector('[data-testid="line-price-1"]').value).toBe("");
    expect(container.querySelector('[data-testid="line-price-source-1"]').textContent).toBe("Harga jual tidak ditemukan di Pengaturan Harga. Isi manual.");

    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="line-price-1"]'), "150");
    });
    expect(container.querySelector('[data-testid="line-price-source-1"]').textContent).toBe("Input manual");
    expect(container.querySelector('[data-testid="line-price-1"]').value).toBe("150");
  });

  test("the same draft keeps customer, lines, prices, and shipping across tabs", async () => {
    await openCreate();
    const poBefore = container.querySelector('[data-testid="input-po-number"]').value;
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-customer"]'), "GOHO COFFEE");
      setNativeValue(container.querySelector('[data-testid="line-qty-0"]'), "1000");
      setNativeValue(container.querySelector('[data-testid="line-price-0"]'), "700");
      setNativeValue(container.querySelector('[data-testid="line-code-0"]'), "BRG-CP001");
      setNativeValue(container.querySelector('[data-testid="line-color-0"]'), "Merah");
      setNativeValue(container.querySelector('[data-testid="line-notes-0"]'), "Sablon depan");
    });
    expect(container.querySelector(".so-sum-chip.is-customer .so-sum-text").textContent).toBe("GOHO COFFEE");
    await act(async () => {
      container.querySelector('[data-testid="btn-line-detail-0"]').click();
    });
    expect(container.querySelector('[data-testid="line-item-detail-0"]').hidden).toBe(true);
    expect(container.querySelector('[data-testid="line-code-0"]').value).toBe("BRG-CP001");
    expect(container.querySelector('[data-testid="line-detail-summary-0"]').textContent).toContain("BRG-CP001");
    expect(container.querySelector('[data-testid="line-detail-summary-0"]').textContent).toContain("Merah");

    await act(async () => {
      container.querySelector('[data-testid="btn-add-line-item"]').click();
    });
    expect(container.querySelector('[data-testid="line-item-detail-0"]').hidden).toBe(true);
    expect(container.querySelector('[data-testid="line-item-detail-1"]').hidden).toBe(false);
    expect(container.querySelector('[data-testid="line-color-0"]').value).toBe("Merah");
    await act(async () => {
      container.querySelector('[data-testid="btn-line-detail-0"]').click();
    });
    expect(container.querySelector('[data-testid="line-item-detail-0"]').hidden).toBe(false);
    expect(container.querySelector('[data-testid="line-item-detail-1"]').hidden).toBe(true);
    expect(container.querySelector('[data-testid="line-color-1"]').value).toBe("");

    await act(async () => {
      container.querySelector('[data-testid="so-tab-ship"]').click();
    });
    await act(async () => {
      setNativeValue(container.querySelector('[data-testid="input-expedition"]'), "Ekspedisi Uji Draf");
      setNativeValue(container.querySelector('[data-testid="input-address"]'), "Jl. Malioboro 10");
    });
    await act(async () => {
      container.querySelector('[data-testid="so-tab-items"]').click();
    });
    expect(container.querySelector('[data-testid="input-po-number"]').value).toBe(poBefore);
    expect(container.querySelector(".so-sum-chip.is-customer .so-sum-text").textContent).toBe("GOHO COFFEE");
    expect(container.querySelector('[data-testid="line-qty-0"]').value).toBe("1000");
    expect(container.querySelector('[data-testid="line-price-0"]').value).toBe("700");
    expect(container.querySelector('[data-testid="line-notes-0"]').value).toBe("Sablon depan");
    await act(async () => {
      container.querySelector('[data-testid="so-tab-ship"]').click();
    });
    expect(container.querySelector('[data-testid="input-expedition"]').value).toBe("Ekspedisi Uji Draf");
    expect(container.querySelector('[data-testid="input-address"]').value).toBe("Jl. Malioboro 10");
    expect(container.querySelector('[data-testid="input-po-number"]').value).toBe(poBefore);
  });
});
