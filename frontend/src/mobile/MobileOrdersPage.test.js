import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import MobileOrdersPage, {
  ordersFromListResponse,
  ordersLoadErrorMessage,
} from "./MobileOrdersPage";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listOrders: jest.fn(),
    getOrder: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const sampleOrders = [
  {
    id: "po-1",
    po_number: "PO-1001",
    customer_name: "Kopi Rumah",
    outlet: "Kelapa Gading",
    item: "Paper cup 12oz",
    current_stage: 11,
    deadline: "2026-08-29",
  },
  {
    id: "po-2",
    po_number: "PO-1002",
    customer_name: "Roti Sederhana",
    outlet: "PIK",
    item: "Paper bag",
    current_stage: 12,
    deadline: "2026-08-30",
  },
];

describe("ordersFromListResponse", () => {
  test("uses canonical { orders } shape", () => {
    expect(ordersFromListResponse({ orders: sampleOrders, counts: {} })).toEqual(sampleOrders);
  });

  test("falls back to a raw array and never invents keys", () => {
    expect(ordersFromListResponse(sampleOrders)).toEqual(sampleOrders);
    expect(ordersFromListResponse({ items: sampleOrders })).toEqual([]);
    expect(ordersFromListResponse(null)).toEqual([]);
  });
});

describe("ordersLoadErrorMessage", () => {
  test("keeps 403 as an error string, not an empty list", () => {
    expect(ordersLoadErrorMessage({
      response: { status: 403, data: { detail: "Anda tidak punya permission" } },
    })).toBe("Anda tidak punya permission");
  });

  test("object FastAPI detail does not become a React child object", () => {
    const msg = ordersLoadErrorMessage({
      response: { status: 500, data: { detail: { message: "server busy" } } },
    });
    expect(typeof msg).toBe("string");
    expect(msg).toContain("server busy");
  });
});

describe("MobileOrdersPage", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.listOrders.mockReset();
    api.getOrder.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("initial loading state renders", async () => {
    let resolveList;
    api.listOrders.mockReturnValue(new Promise((resolve) => { resolveList = resolve; }));
    await act(async () => { root.render(<MobileOrdersPage />); });
    expect(container.textContent).toContain("Memuat pesanan...");
    expect(container.querySelector('[data-testid="mobile-orders-loading"]')).toBeTruthy();
    await act(async () => { resolveList({ orders: [] }); });
    await flush();
  });

  test("successful API response removes loading and renders PO cards", async () => {
    api.listOrders.mockResolvedValue({ orders: sampleOrders, counts: {}, stage_counts: {} });
    await act(async () => { root.render(<MobileOrdersPage />); });
    await flush();
    expect(container.textContent).not.toContain("Memuat pesanan...");
    expect(container.querySelector('[data-testid="mobile-po-PO-1001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-po-PO-1002"]')).toBeTruthy();
    expect(api.listOrders).toHaveBeenCalledWith("aktif");
    expect(api.getOrder).not.toHaveBeenCalled();
  });

  test("empty response shows Belum ada pesanan", async () => {
    api.listOrders.mockResolvedValue({ orders: [] });
    await act(async () => { root.render(<MobileOrdersPage />); });
    await flush();
    expect(container.textContent).toContain("Belum ada pesanan.");
    expect(container.textContent).not.toContain("Memuat pesanan...");
  });

  test("API reject shows Gagal memuat pesanan and not Memuat pesanan", async () => {
    api.listOrders.mockRejectedValue({
      response: { status: 500, data: { detail: { message: "upstream" } } },
    });
    await act(async () => { root.render(<MobileOrdersPage />); });
    await flush();
    expect(container.textContent).toContain("Gagal memuat pesanan.");
    expect(container.textContent).toContain("Coba Lagi");
    expect(container.textContent).not.toContain("Memuat pesanan...");
    expect(container.querySelector('[data-testid="mobile-orders-empty"]')).toBeFalsy();
  });

  test("403 stays an error, not an empty list", async () => {
    api.listOrders.mockRejectedValue({
      response: { status: 403, data: { detail: "Anda tidak punya permission untuk melihat PO ini" } },
    });
    await act(async () => { root.render(<MobileOrdersPage />); });
    await flush();
    expect(container.textContent).toContain("Gagal memuat pesanan.");
    expect(container.textContent).toContain("Anda tidak punya permission");
    expect(container.textContent).not.toContain("Belum ada pesanan.");
  });

  test("Coba Lagi invokes request again and successful retry renders data", async () => {
    api.listOrders
      .mockRejectedValueOnce({ response: { status: 500, data: { detail: "boom" } } })
      .mockResolvedValueOnce({ orders: sampleOrders });
    await act(async () => { root.render(<MobileOrdersPage />); });
    await flush();
    expect(container.textContent).toContain("Gagal memuat pesanan.");
    const retry = container.querySelector('[data-testid="mobile-orders-retry"]');
    await act(async () => { retry.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();
    expect(api.listOrders).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[data-testid="mobile-po-PO-1001"]')).toBeTruthy();
    expect(container.textContent).not.toContain("Memuat pesanan...");
  });

  test("search still works after data load", async () => {
    api.listOrders.mockResolvedValue({ orders: sampleOrders });
    await act(async () => { root.render(<MobileOrdersPage />); });
    await flush();
    const input = container.querySelector('[data-testid="mobile-orders-search"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, "Kopi");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mobile-po-PO-1001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-po-PO-1002"]')).toBeFalsy();
    expect(api.getOrder).not.toHaveBeenCalled();
  });
});
