import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import { NAV_KEYS, purchasingMenuItems } from "../lib/navAccess";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("react-router-dom", () => {
  const React = require("react");
  return {
    NavLink: ({ to, children, className, ...props }) => (
      <a href={to} className={typeof className === "function" ? className({ isActive: false }) : className} {...props}>{children}</a>
    ),
  };
}, { virtual: true });

const has = (...keys) => (key) => keys.includes(key);
const mockHasPerm = jest.fn();

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "retur-ui", name: "Pengguna Uji" },
    hasPerm: (key) => mockHasPerm(key),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listPurchaseReturns: jest.fn(),
    listPurchaseGoodsReceipts: jest.fn(),
    getPurchaseGoodsReceipt: jest.fn(),
    getPurchaseReturn: jest.fn(),
    getPurchaseReturnFinancial: jest.fn(),
    createPurchaseReturn: jest.fn(),
    confirmPurchaseReturn: jest.fn(),
    cancelPurchaseReturn: jest.fn(),
    warehouses: jest.fn(),
  },
}));

import ReturPembelian from "./ReturPembelian";

const mockApi = require("../lib/api").default;

const FINANCIAL_PENDING = "PENDING_FINANCIAL_ALLOCATION";
const pageSrc = fs.readFileSync(path.join(__dirname, "ReturPembelian.jsx"), "utf8");
const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

const receipt = {
  id: "gr-1",
  receipt_number: "PB-RC-1",
  po_number: "PB-1",
  supplier: "Supplier Uji",
  lines: [{
    line_id: "gr-line-1",
    item_code: "ITEM-1",
    item_name: "Dus Uji",
    unit: "PCS",
    received_qty: 10,
    confirmed_return_qty: 4,
  }],
};

function draftReturn(extra = {}) {
  return {
    id: "ret-1",
    return_number: "RPB-1",
    return_date: "2026-10-05",
    supplier: "Supplier Uji",
    po_number: "PB-1",
    purchase_goods_receipt_id: "gr-1",
    status: "DRAFT",
    financial_status: "NO_FINANCIAL_EFFECT",
    lines: [{
      line_id: "rl-1",
      item_code: "ITEM-1",
      item_name: "Dus Uji",
      unit: "PCS",
      received_qty: 10,
      return_qty: 2,
      warehouse_id: "wh-receipt",
      financial_status: "NO_FINANCIAL_EFFECT",
    }],
    ...extra,
  };
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function click(container, selector) {
  const node = container.querySelector(selector);
  expect(node).toBeTruthy();
  act(() => node.click());
}

describe("Retur Pembelian navigation", () => {
  test("menu places Retur Pembelian after Penerimaan and before Faktur", () => {
    const labels = purchasingMenuItems(has(
      NAV_KEYS.purchasing,
      NAV_KEYS.purchaseRequests,
      NAV_KEYS.purchaseOrders,
      NAV_KEYS.purchaseInvoices,
      NAV_KEYS.purchasePayments,
      NAV_KEYS.purchaseReceipts,
    )).map((item) => item.label);
    expect(labels).toEqual([
      "Permintaan",
      "Pesanan Pembelian",
      "Uang Muka",
      "Penerimaan Barang",
      "Retur Pembelian",
      "Faktur Pembelian",
      "Pembayaran Pembelian",
    ]);
    const item = labels && purchasingMenuItems(has(NAV_KEYS.purchasing, NAV_KEYS.purchaseReceipts))
      .find((row) => row.label === "Retur Pembelian");
    expect(item.to).toBe("/pembelian/retur");
    expect(appSrc).toMatch(/path="\/pembelian\/retur"/);
  });
});

describe("Retur Pembelian page", () => {
  let container;
  let root;

  beforeEach(() => {
    mockHasPerm.mockImplementation((key) => key === "purchase_receipts.receive" || key === "purchase_receipts.view");
    mockApi.listPurchaseReturns.mockResolvedValue({ returns: [draftReturn()] });
    mockApi.listPurchaseGoodsReceipts.mockResolvedValue({ receipts: [receipt] });
    mockApi.warehouses.mockResolvedValue({
      items: [{ id: "wh-receipt", name: "Gudang Yogyakarta" }],
    });
    mockApi.getPurchaseGoodsReceipt.mockResolvedValue({ receipt });
    mockApi.getPurchaseReturn.mockImplementation(async () => ({ return: draftReturn() }));
    mockApi.getPurchaseReturnFinancial.mockResolvedValue({
      financial_status: "NO_FINANCIAL_EFFECT",
      credits: [],
      supplier_credits: [],
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.clearAllMocks();
  });

  async function renderPage() {
    await act(async () => {
      root.render(<ReturPembelian />);
    });
    await flush();
  }

  test("route page lists an existing return", async () => {
    await renderPage();
    expect(container.querySelector('[data-testid="retur-pembelian-page"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="retur-number"]').textContent).toBe("RPB-1");
    expect(container.querySelector('[data-testid="retur-supplier"]').textContent).toBe("Supplier Uji");
    expect(container.querySelector('[data-testid="retur-receipt"]').textContent).toBe("PB-RC-1");
    expect(container.querySelector('[data-testid="retur-list-status"]').textContent).toBe("Draf");
  });

  test("create uses the goods receipt line and blocks invalid qty", async () => {
    await renderPage();
    click(container, '[data-testid="btn-create-retur"]');
    const select = container.querySelector('[data-testid="retur-receipt-select"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
      setter.call(select, "gr-1");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="retur-returnable-qty"]').textContent).toBe("6");
    const qty = container.querySelector('[data-testid="retur-qty-gr-line-1"]');
    const setQty = async (value) => {
      await act(async () => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        setter.call(qty, value);
        qty.dispatchEvent(new Event("input", { bubbles: true }));
      });
    };
    await setQty("0");
    click(container, '[data-testid="retur-save"]');
    await flush();
    expect(mockApi.createPurchaseReturn).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="retur-error"]').textContent).toMatch(/lebih besar dari 0/);

    await setQty("7");
    click(container, '[data-testid="retur-save"]');
    await flush();
    expect(mockApi.createPurchaseReturn).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="retur-error"]').textContent).toMatch(/melebihi/);

    await setQty("2");
    mockApi.createPurchaseReturn.mockResolvedValue({ return: draftReturn() });
    click(container, '[data-testid="retur-save"]');
    await flush();
    await flush();
    const payload = mockApi.createPurchaseReturn.mock.calls[0][0];
    expect(payload.lines).toEqual([{
      purchase_goods_receipt_id: "gr-1",
      purchase_goods_receipt_line_id: "gr-line-1",
      return_qty: 2,
    }]);
    expect(payload.warehouse_id).toBeUndefined();
    expect(payload.lines[0].warehouse_id).toBeUndefined();
  });

  test("confirm and cancel call the purchase return endpoints", async () => {
    await renderPage();
    click(container, '[data-testid="retur-open"]');
    await flush();
    mockApi.confirmPurchaseReturn.mockResolvedValue({ return: draftReturn({ status: "CONFIRMED" }) });
    mockApi.getPurchaseReturn.mockResolvedValue({ return: draftReturn({ status: "CONFIRMED" }) });
    click(container, '[data-testid="retur-confirm"]');
    await flush();
    await flush();
    expect(mockApi.confirmPurchaseReturn).toHaveBeenCalledWith("ret-1", expect.any(Object));
    expect(container.querySelector('[data-testid="retur-detail-status"]').textContent).toMatch(/Terkonfirmasi/);

    mockApi.cancelPurchaseReturn.mockResolvedValue({ return: draftReturn({ status: "REVERSED" }) });
    mockApi.getPurchaseReturn.mockResolvedValue({ return: draftReturn({ status: "REVERSED" }) });
    click(container, '[data-testid="retur-reverse"]');
    await flush();
    await flush();
    expect(mockApi.cancelPurchaseReturn).toHaveBeenCalledWith("ret-1", expect.any(String));
    expect(container.querySelector('[data-testid="retur-detail-status"]').textContent).toMatch(/Direversal/);
    expect(container.querySelector('[data-testid="retur-readonly"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="retur-confirm"]')).toBeNull();
    expect(container.querySelector('[data-testid="retur-reverse"]')).toBeNull();
  });

  test("detail card shows document facts and can close", async () => {
    await renderPage();
    click(container, '[data-testid="retur-open"]');
    await flush();
    expect(container.querySelector('[data-testid="retur-detail-number"]').textContent).toBe("RPB-1");
    expect(container.querySelector('[data-testid="retur-detail-supplier"]').textContent).toBe("Supplier Uji");
    expect(container.querySelector('[data-testid="retur-detail-po"]').textContent).toBe("PB-1");
    expect(container.querySelector('[data-testid="retur-detail-receipt"]').textContent).toBe("PB-RC-1");
    expect(container.querySelector('[data-testid="retur-detail-warehouse"]').textContent).toBe("Gudang Yogyakarta");
    expect(container.querySelector('[data-testid="retur-detail"]').textContent).not.toContain("wh-receipt");
    expect(container.querySelector('[data-testid="retur-detail-date"]').textContent).toBe("05 Oktober 2026");
    expect(container.querySelector('[data-testid="retur-detail-item"]').textContent).toBe("Dus Uji");
    expect(container.querySelector('[data-testid="retur-detail-financial"]').textContent).toBe("Belum terkait kredit faktur");
    expect(container.querySelectorAll('[data-testid="retur-detail"]')).toHaveLength(1);
    click(container, '[data-testid="retur-detail-close"]');
    expect(container.querySelector('[data-testid="retur-detail"]')).toBeNull();
  });

  test("cancelled state is read-only and pending finance is not guessed", async () => {
    mockApi.listPurchaseReturns.mockResolvedValue({
      returns: [draftReturn({ status: "CANCELLED", financial_status: FINANCIAL_PENDING })],
    });
    mockApi.getPurchaseReturn.mockResolvedValue({
      return: draftReturn({ status: "CANCELLED", financial_status: "PENDING_FINANCIAL_ALLOCATION" }),
    });
    mockApi.getPurchaseReturnFinancial.mockResolvedValue({
      financial_status: "PENDING_FINANCIAL_ALLOCATION",
      credits: [],
      supplier_credits: [],
    });
    await renderPage();
    expect(container.querySelector('[data-testid="retur-list-status"]').textContent).toBe("Dibatalkan");
    expect(container.querySelector('[data-testid="retur-list-financial"]').textContent).toBe("Menunggu Alokasi Finansial");
    click(container, '[data-testid="retur-open"]');
    await flush();
    expect(container.querySelector('[data-testid="retur-pending-allocation"]').textContent).toMatch(/tidak dipilih otomatis/);
    expect(container.querySelector('[data-testid="retur-readonly"]')).toBeTruthy();
    expect(pageSrc).not.toMatch(/financial-allocations/);
    expect(pageSrc).not.toMatch(/physical-stock|physicalStock|applyPurchaseReturn/);
  });
});
