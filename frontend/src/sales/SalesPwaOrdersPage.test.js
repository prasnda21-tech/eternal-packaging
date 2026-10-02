import { readFileSync } from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { SALES_PWA_PO_FILTER_KEY, SALES_PWA_PO_SCROLL_KEY, clearSalesPwaPoListState, writeSalesPwaPoListState } from "../lib/salesPwaPoListState";
import SalesPwaOrdersPage from "./SalesPwaOrdersPage";

const mockRouter = { state: null };

jest.mock("react-router-dom", () => ({
  useLocation: () => ({ pathname: "/sales/po", search: "", hash: "", key: "test", state: mockRouter.state }),
  Link: ({ to, children, onClick, ...props }) => (
    <a
      href={typeof to === "string" ? to : ""}
      {...props}
      onClick={(event) => {
        event.preventDefault();
        if (onClick) onClick(event);
      }}
    >
      {children}
    </a>
  ),
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesPwaOrders: jest.fn(),
    salesPwaDocuments: jest.fn(),
  },
  publicApiUrl: (p) => p,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const sample = {
  orders: Array.from({ length: 8 }, (_, i) => ({
    id: `po-${i + 1}`,
    po_number: `PO-A${String(i + 1).padStart(3, "0")}`,
    customer: `Pelanggan ${i + 1}`,
    status: i === 1 ? "HOLD" : "Packing",
    hold: i === 1,
    order_date: "2026-09-18",
    buffer_stock: i === 0,
    tanggal_kirim_overdue: false,
    progress_percent: 40,
    workflow: { packing: "active", order: "completed" },
    documents: { total: i % 3, po: 1, sj: 0, invoice: 0, bukti_kirim: 0 },
  })),
  counts: { semua: 8, berjalan: 7, hold: 1, parsial: 0, selesai: 0 },
  total: 8,
};

function setInput(input, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function mockNarrow(matches) {
  window.matchMedia = (query) => ({
    matches,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
  });
}

function storedFilters() {
  return JSON.parse(sessionStorage.getItem(SALES_PWA_PO_FILTER_KEY) || "{}");
}

function click(el) {
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
}

async function mount(container) {
  const root = createRoot(container);
  await act(async () => {
    root.render(<SalesPwaOrdersPage />);
  });
  await flush();
  return root;
}

describe("SalesPwaOrdersPage", () => {
  beforeEach(() => {
    sessionStorage.clear();
    mockRouter.state = null;
    delete window.matchMedia;
    api.salesPwaOrders.mockReset();
    api.salesPwaDocuments.mockReset();
    api.salesPwaOrders.mockResolvedValue(sample);
  });

  test("keeps the desktop table layout, including column filters", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = await mount(container);
    expect(api.salesPwaOrders).toHaveBeenCalled();
    const params = api.salesPwaOrders.mock.calls[0][0] || {};
    expect(params.sales_user_id).toBeUndefined();
    expect(params.customer_q).toBeUndefined();
    expect(params.po_q).toBeUndefined();
    expect(params.limit).toBe(120);
    expect(container.querySelector('[data-testid="sales-po-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="sales-po-cards"]')).toBeNull();
    expect(container.querySelector('[data-testid="sales-filter-open"]')).toBeNull();
    expect(container.querySelectorAll("tbody tr").length).toBe(8);
    expect(container.querySelector("tbody td").getAttribute("colspan")).toBe("5");
    expect(container.querySelector(".spwa-linkrow")).toBeTruthy();
    expect(container.textContent).toContain("Filter rinci tersedia langsung pada header tabel.");
    expect(container.querySelector('[aria-label="Filter pelanggan atau PO"]')).toBeTruthy();
    expect(container.querySelector('[aria-label="Filter tahap"]')).toBeTruthy();
    expect(container.querySelector('[aria-label="Filter buffer stock"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="sales-pwa-nav"]')).toBeNull();
    act(() => root.unmount());
    container.remove();
  });

  test("clears the previous list while a desktop column filter loads and reports rows left out by the limit", async () => {
    let resolveNext;
    api.salesPwaOrders.mockReset();
    api.salesPwaOrders
      .mockResolvedValueOnce({ ...sample, total: 8 })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNext = resolve; }));
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = await mount(container);
    expect(container.textContent).toContain("Semua 8");

    const input = container.querySelector('[aria-label="Filter pelanggan atau PO"]');
    await act(async () => {
      setInput(input, "PO-A001");
    });
    expect(container.textContent).not.toContain("Semua 8");
    expect(container.textContent).toContain("Memuat...");
    const params = api.salesPwaOrders.mock.calls.at(-1)[0];
    expect(params.q).toBe("PO-A001");
    expect(params.customer_q).toBeUndefined();
    expect(params.po_q).toBeUndefined();
    expect(params.limit).toBe(120);

    await act(async () => {
      resolveNext({
        ...sample,
        orders: sample.orders.slice(0, 1),
        total: 4,
        counts: { semua: 1, berjalan: 1, hold: 0, parsial: 0, selesai: 0 },
      });
    });
    expect(container.textContent).toContain("Semua 1");
    expect(container.querySelector('[data-testid="sales-po-remainder"]').textContent).toContain("Menampilkan 1 dari 4 PO");
    act(() => root.unmount());
    container.remove();
  });

  test("debounces the search box and can clear it without showing stale rows", async () => {
    jest.useFakeTimers();
    api.salesPwaOrders.mockReset();
    api.salesPwaOrders.mockResolvedValue(sample);
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => {
        root.render(<SalesPwaOrdersPage />);
      });
      await act(async () => {
        await Promise.resolve();
      });
      expect(container.textContent).toContain("Semua 8");
      const input = container.querySelector('[data-testid="sales-orders-search"]');
      await act(async () => {
        setInput(input, "PO-A001");
      });
      expect(container.textContent).not.toContain("Semua 8");
      expect(container.querySelector('[data-testid="sales-po-loading"]')).toBeTruthy();
      expect(api.salesPwaOrders).toHaveBeenCalledTimes(1);
      await act(async () => {
        jest.advanceTimersByTime(300);
      });
      expect(api.salesPwaOrders.mock.calls.at(-1)[0].q).toBe("PO-A001");
      await act(async () => {
        await Promise.resolve();
      });
      await act(async () => {
        container.querySelector('[data-testid="sales-orders-search-clear"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      expect(container.querySelector('[data-testid="sales-orders-search"]').value).toBe("");
      expect(container.textContent).not.toContain("Semua 8");
    } finally {
      act(() => root.unmount());
      container.remove();
      jest.useRealTimers();
    }
  });

  test("restores filters after the list remounts from detail", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const first = await mount(container);
    const stage = container.querySelector('[aria-label="Filter tahap"]');
    await act(async () => {
      setInput(stage, "Packing");
    });
    const buffer = container.querySelector('[aria-label="Filter buffer stock"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set;
      setter.call(buffer, "ya");
      buffer.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
    expect(storedFilters()).toMatchObject({ stage: "Packing", buffer: "ya", status: "semua" });
    act(() => first.unmount());

    api.salesPwaOrders.mockClear();
    const second = await mount(container);
    expect(container.querySelector('[aria-label="Filter tahap"]').value).toBe("Packing");
    expect(container.querySelector('[aria-label="Filter buffer stock"]').value).toBe("ya");
    expect(api.salesPwaOrders.mock.calls.at(-1)[0]).toMatchObject({
      stage: "Packing",
      buffer: "ya",
      status: "semua",
    });
    act(() => second.unmount());
    container.remove();
  });

  test("logout clears stored filters so the next mount does not restore them", async () => {
    sessionStorage.setItem(SALES_PWA_PO_FILTER_KEY, JSON.stringify({
      q: "Kopi",
      customer_q: "PO-9",
      status: "hold",
      stage: "Packing",
      date_from: "2026-09-01",
      date_to: "2026-09-30",
      progress_min: "50",
      buffer: "ya",
    }));
    sessionStorage.setItem(SALES_PWA_PO_SCROLL_KEY, "640");
    clearSalesPwaPoListState();
    expect(sessionStorage.getItem(SALES_PWA_PO_FILTER_KEY)).toBeNull();
    expect(sessionStorage.getItem(SALES_PWA_PO_SCROLL_KEY)).toBeNull();

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = await mount(container);
    const params = api.salesPwaOrders.mock.calls.at(-1)[0];
    expect(params.q).toBe("");
    expect(params.status).toBe("semua");
    expect(params.stage).toBe("");
    expect(params.buffer).toBe("");
    expect(container.querySelector('[data-testid="sales-orders-search"]').value).toBe("");
    act(() => root.unmount());
    container.remove();
  });

  test("restores scroll after returning from detail, invoice, and attachment", async () => {
    api.salesPwaDocuments.mockResolvedValue({
      po_number: "PO-A001",
      customer: "Pelanggan 1",
      po: { available: true, filename: "po.pdf" },
      sj: [],
      invoices: [{ id: "inv-1", number: "INV-9", available: true, date: "2026-09-01" }],
      bukti_kirim: [{ id: "proof-1", storage_path: "proof.jpg", uploaded_at: "2026-09-02" }],
      counts: { total: 3 },
    });
    const scrollTo = jest.spyOn(window, "scrollTo").mockImplementation(() => {});
    const container = document.createElement("div");
    document.body.appendChild(container);
    const first = await mount(container);
    await act(async () => {
      click(container.querySelector('[data-testid="sales-filter-hold"]'));
    });
    await flush();
    await act(async () => {
      click(container.querySelector('[data-testid="sales-docs-po-1"]'));
    });
    await flush();
    const invoice = container.querySelector('a[href="/sales/po/po-1/dokumen/invoice/inv-1"]');
    const proof = container.querySelector('a[href="/sales/po/po-1/dokumen/proof/proof-1"]');
    const detail = container.querySelector('a[href="/sales/po/po-1"]');
    expect(invoice).toBeTruthy();
    expect(proof).toBeTruthy();
    expect(detail).toBeTruthy();

    Object.defineProperty(window, "scrollY", { configurable: true, value: 740 });
    await act(async () => {
      click(invoice);
    });
    expect(sessionStorage.getItem(SALES_PWA_PO_SCROLL_KEY)).toBe("740");
    expect(storedFilters().status).toBe("hold");

    Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });
    act(() => first.unmount());
    expect(sessionStorage.getItem(SALES_PWA_PO_SCROLL_KEY)).toBe("740");

    api.salesPwaOrders.mockClear();
    const second = await mount(container);
    expect(scrollTo).toHaveBeenCalledWith(0, 740);
    expect(api.salesPwaOrders.mock.calls.at(-1)[0].status).toBe("hold");
    expect(container.querySelector('[data-testid="sales-filter-hold"]').className).toContain("is-on");
    act(() => second.unmount());
    container.remove();
    scrollTo.mockRestore();
  });

  test("uses cards instead of the desktop table on a narrow screen", async () => {
    mockNarrow(true);
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = await mount(container);
    expect(container.querySelector('[data-testid="sales-po-table"]')).toBeNull();
    expect(container.querySelector(".spwa-column-filters")).toBeNull();
    expect(container.textContent).not.toContain("Filter rinci tersedia langsung pada header tabel.");
    const card = container.querySelector('[data-testid="sales-po-card-PO-A001"]');
    expect(card).toBeTruthy();
    expect(card.textContent).toContain("Pelanggan 1");
    expect(card.textContent).toContain("PO-A001");
    expect(card.querySelector(".spwa-po-card-status").className).toContain("is-running");
    expect(card.textContent).toContain("Packing");
    const held = container.querySelector('[data-testid="sales-po-card-PO-A002"] .spwa-po-card-status');
    expect(held.className).toContain("is-hold");
    expect(held.className).not.toContain("is-running");
    expect(card.textContent).toContain("18/09/26");
    expect(card.textContent).toContain("Progress 40%");
    expect(card.textContent).toContain("Buffer Stock");
    expect(card.textContent).toContain("0 dokumen");
    expect(card.querySelector('[data-testid="sales-po-detail-po-1"]').getAttribute("href")).toBe("/sales/po/po-1");
    expect(container.querySelector('[data-testid="sales-filter-open"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="sales-filter-open"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="sales-filter-sheet"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="sales-filter-sheet"] input[aria-label="Filter tahap"]')).toBeNull();
    expect(container.querySelectorAll('[aria-label="Filter tahap"]').length).toBe(1);
    await act(async () => {
      container.querySelector('[data-testid="sales-stage-open"]').dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    const sheet = container.querySelector('[data-testid="sales-stage-sheet"]');
    const stageLabels = Array.from(sheet.querySelectorAll("label"));
    expect(stageLabels[0].textContent).toContain("Semua Tahap");
    expect(sheet.textContent).toContain("Approval");
    expect(sheet.textContent).toContain("Produksi");
    expect(sheet.textContent).toContain("Packing");
    expect(sheet.textContent).toContain("Delivery");
    expect(sheet.textContent).not.toContain("Prioritas");
    const choice = Array.from(sheet.querySelectorAll("label")).find((label) => label.textContent.includes("Approval Design"));
    await act(async () => {
      choice.querySelector("input").dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      choice.querySelector("input").dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="sales-stage-apply"]').dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(api.salesPwaOrders.mock.calls.at(-1)[0].stage).toBe("Approval Design");
    expect(storedFilters().stage).toBe("Approval Design");
    expect(readFileSync(path.join(__dirname, "salesPwa.css"), "utf8")).toMatch(/\.spwa-filters\s*\{[^}]*flex-wrap:\s*nowrap/);
    act(() => root.unmount());
    container.remove();
  });

  test("colors partial and selesai cards from existing row fields", async () => {
    mockNarrow(true);
    api.salesPwaOrders.mockResolvedValue({
      orders: [
        { id: "p1", po_number: "PO-P", customer: "Parsial", status: "Parsial Terkirim", partial_delivery: true, progress_percent: 80, hold: false, order_date: "2026-09-01", buffer_stock: false, documents: { total: 0 } },
        { id: "d1", po_number: "PO-D", customer: "Selesai", status: "Terkirim", partial_delivery: false, progress_percent: 100, hold: false, order_date: "2026-09-01", buffer_stock: false, documents: { total: 0 } },
      ],
      counts: { semua: 2, berjalan: 0, hold: 0, parsial: 1, selesai: 1 },
      total: 2,
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = await mount(container);
    expect(container.querySelector('[data-testid="sales-po-card-PO-P"] .spwa-po-card-status').className).toContain("is-partial");
    expect(container.querySelector('[data-testid="sales-po-card-PO-D"] .spwa-po-card-status').className).toContain("is-done");
    act(() => root.unmount());
    container.remove();
  });

  test("reads an active dashboard context as the existing berjalan filter", async () => {
    writeSalesPwaPoListState({
      q: "lama",
      status: "hold",
      stage: "Packing",
      date_from: "2026-01-01",
      date_to: "2026-01-31",
      buffer: "ya",
    }, 480);
    mockRouter.state = { salesPwaPoContext: { active: true } };
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = await mount(container);
    const params = api.salesPwaOrders.mock.calls.at(-1)[0];
    expect(params.status).toBe("berjalan");
    expect(params.stage).toBe("");
    expect(params.date_from).toBe("");
    expect(params.date_to).toBe("");
    expect(params.buffer).toBe("");
    expect(container.querySelector('[data-testid="sales-filter-berjalan"]').className).toContain("is-on");
    expect(storedFilters()).toMatchObject({ status: "berjalan", q: "", stage: "", date_from: "", date_to: "" });
    expect(sessionStorage.getItem(SALES_PWA_PO_SCROLL_KEY)).toBe("0");
    act(() => root.unmount());
    container.remove();
  });

  test("reads a selesai dashboard context for the selected month", async () => {
    mockRouter.state = { salesPwaPoContext: { status: "selesai", periode: "2026-09" } };
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = await mount(container);
    const params = api.salesPwaOrders.mock.calls.at(-1)[0];
    expect(params.status).toBe("selesai");
    expect(params.date_from).toBe("2026-09-01");
    expect(params.date_to).toBe("2026-09-30");
    expect(container.querySelector('[data-testid="sales-filter-selesai"]').className).toContain("is-on");
    expect(storedFilters()).toMatchObject({ status: "selesai", date_from: "2026-09-01", date_to: "2026-09-30" });
    act(() => root.unmount());
    container.remove();
  });

  test("shows the stored design approval on the matching phone card only", async () => {
    mockNarrow(true);
    api.salesPwaOrders.mockResolvedValue({
      orders: [
        {
          id: "po-link",
          po_number: "PO-LINK",
          customer: "Ada Link",
          status: "Approval Design",
          progress_percent: 20,
          hold: false,
          order_date: "2026-09-18",
          buffer_stock: false,
          documents: { total: 0 },
          design_approvals: [
            { id: "des-a", status: "invalidated", status_label: "Design diperbarui (tidak berlaku)", design_name: "desain-a.pdf" },
            { id: "des-b", status: "active", status_label: "Aktif · Menunggu customer", design_name: "desain-b.pdf", items: [{ idx: 0, label: "Gelas · Biru" }, { idx: 1, label: "Gelas · Hijau" }], path: "/customer/design-approval/tok-b" },
          ],
        },
        {
          id: "po-none",
          po_number: "PO-NONE",
          customer: "Tanpa Link",
          status: "Approval Design",
          progress_percent: 10,
          hold: false,
          order_date: "2026-09-18",
          buffer_stock: false,
          documents: { total: 0 },
          design_approvals: [],
        },
      ],
      counts: { semua: 2, berjalan: 2, hold: 0, parsial: 0, selesai: 0 },
      total: 2,
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = await mount(container);
    const linked = container.querySelector('[data-testid="sales-po-card-PO-LINK"]');
    const open = linked.querySelector('[data-testid="sales-approval-open-des-b"]');
    const other = linked.querySelector('[data-testid="sales-approval-row-des-a"]');
    expect(open.getAttribute("href")).toBe("/customer/design-approval/tok-b");
    expect(open.textContent).toBe("Buka Approval Desain");
    expect(open.parentElement.textContent).toContain("desain-b.pdf");
    expect(open.parentElement.textContent).toContain("Gelas · Biru");
    expect(open.parentElement.textContent).toContain("Gelas · Hijau");
    expect(other.querySelector("a, button")).toBeNull();
    expect(other.textContent).toContain("desain-a.pdf");
    expect(other.textContent).toContain("Link approval belum tersedia");
    expect(other.textContent).not.toContain("desain-b.pdf");
    const empty = container.querySelector('[data-testid="sales-po-card-PO-NONE"]');
    expect(empty.querySelector('[data-testid="sales-approval-missing-po-none"]').textContent).toBe("Link approval belum tersedia");
    expect(empty.querySelector("a.spwa-approval-open, button.spwa-approval-open")).toBeNull();
    act(() => root.unmount());
    container.remove();
  });
});
