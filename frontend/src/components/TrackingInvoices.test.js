import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import TrackingInvoices from "./TrackingInvoices";
import { formatInvoiceMoney, safeInvoicePdfPath } from "../lib/trackingInvoices";

jest.mock("./TrackingInvoicePdfViewer", () => ({
  __esModule: true,
  default: ({ invoiceId, pdfPath }) => (
    <div data-testid="tracking-invoice-pdf-viewer" data-invoice-id={invoiceId} data-path={pdfPath} />
  ),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  publicApiUrl: (path) => (path ? `http://localhost:8000${path}` : ""),
  default: {
    publicTrackingInvoices: jest.fn(),
    publicTrackingInvoice: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const issued = {
  id: "inv-1",
  invoice_number: "INV/2026/09/01/0007",
  invoice_date: "2026-09-01",
  due_date: "2026-09-15",
  grand_total: 110000,
  outstanding_amount: 110000,
  paid_total: 0,
  payment_label: "Belum lunas",
  financial_status: "finalized",
  pdf_url: "/api/public/tracking/tok-public-aaaaaaaa/invoices/inv-1/pdf?disposition=attachment",
};

const detail = {
  ...issued,
  order_number: "PO/2026/09/01/0007",
  subtotal: 100000,
  discount_amount: 0,
  ppn_percent: 11,
  ppn_amount: 10000,
  lines: [
    { item_name: "Cup 8oz", quantity: 40, unit: "pcs", unit_price: 2500, line_subtotal: 100000 },
  ],
};

async function mount() {
  const host = document.createElement("div");
  host.style.width = "390px";
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(<TrackingInvoices token="tok-public-aaaaaaaa" />);
  });
  await flush();
  return { host, root };
}

function tile(host) {
  return host.querySelector('[data-testid="tracking-doc-tile-faktur"]');
}

describe("TrackingInvoices", () => {
  beforeEach(() => {
    api.publicTrackingInvoices.mockReset();
    api.publicTrackingInvoice.mockReset();
    document.body.innerHTML = "";
  });

  test("loading, empty, and load failure stay distinct on the document tile", async () => {
    let resolveList;
    api.publicTrackingInvoices.mockReturnValue(new Promise((resolve) => {
      resolveList = resolve;
    }));
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<TrackingInvoices token="tok-public-aaaaaaaa" />);
    });
    expect(host.querySelector('[data-testid="tracking-invoices-loading"]').textContent).toBe("Memuat...");
    expect(tile(host).disabled).toBe(true);
    expect(host.querySelector('[data-testid="tracking-invoices-empty"]')).toBeNull();
    expect(host.textContent).not.toMatch(/Belum tersedia/);
    expect(host.textContent).not.toMatch(/Rp/);

    await act(async () => {
      resolveList({ invoices: [] });
    });
    await flush();
    expect(host.querySelector('[data-testid="tracking-invoices-empty"]').textContent).toMatch(/Belum tersedia/);
    expect(tile(host).disabled).toBe(true);
    expect(tile(host).textContent).toMatch(/Faktur Penjualan/);
    expect(host.querySelector('[data-testid="tracking-invoices-error"]')).toBeNull();
    expect(document.querySelector('[data-testid="tracking-invoice-sheet"]')).toBeNull();

    await act(async () => { root.unmount(); });

    api.publicTrackingInvoices.mockRejectedValue({ response: { status: 502 } });
    const failed = await mount();
    expect(failed.host.querySelector('[data-testid="tracking-invoices-error"]').textContent).toBe("Gagal dimuat");
    expect(tile(failed.host).disabled).toBe(false);
    expect(failed.host.querySelector('[data-testid="tracking-invoices-retry"]').textContent).toBe("Coba lagi");
    expect(failed.host.textContent).not.toMatch(/Belum tersedia/);
    expect(failed.host.textContent).not.toMatch(/Lunas/);
    api.publicTrackingInvoices.mockReset();
    api.publicTrackingInvoices.mockResolvedValue({ invoices: [issued] });
    await act(async () => {
      tile(failed.host).click();
    });
    await flush();
    await flush();
    expect(tile(failed.host).textContent).toMatch(/1 faktur/);
    expect(tile(failed.host).disabled).toBe(false);
    expect(failed.host.querySelector('[data-testid="tracking-invoice-number-inv-1"]')).toBeNull();
    expect(document.querySelector('[data-testid="tracking-invoice-sheet"]')).toBeNull();
    await act(async () => { failed.root.unmount(); });
  });

  test("lihat opens a closable drawer with separate invoices, detail, and token pdf", async () => {
    api.publicTrackingInvoices.mockResolvedValue({
      invoices: [
        issued,
        {
          ...issued,
          id: "inv-2",
          invoice_number: "INV/2026/09/01/0007_2",
          grand_total: 60000,
          outstanding_amount: 20000,
          paid_total: 40000,
          payment_label: "Sebagian",
          pdf_url: "/api/public/tracking/tok-public-aaaaaaaa/invoices/inv-2/pdf?disposition=attachment",
        },
      ],
    });
    api.publicTrackingInvoice.mockImplementation(async (_token, id) => (
      id === "inv-2"
        ? { ...detail, id: "inv-2", invoice_number: "INV/2026/09/01/0007_2", payment_label: "Sebagian" }
        : detail
    ));
    const { host, root } = await mount();
    expect(tile(host).textContent).toMatch(/2 faktur/);
    expect(tile(host).textContent).not.toMatch(/Rp|INV\//);
    expect(document.querySelector('[data-testid="tracking-invoice-sheet"]')).toBeNull();

    await act(async () => {
      tile(host).click();
    });
    const sheet = document.querySelector('[data-testid="tracking-invoice-sheet"]');
    expect(sheet).toBeTruthy();
    expect(sheet.parentElement).toBe(document.body);
    expect(host.querySelector('[data-testid="tracking-invoice-sheet"]')).toBeNull();
    expect(document.querySelectorAll(".tracking-inv-row")).toHaveLength(2);
    expect(document.querySelector('[data-testid="tracking-invoice-status-inv-1"]').textContent).toBe("Belum lunas");
    expect(document.querySelector('[data-testid="tracking-invoice-status-inv-2"]').textContent).toBe("Sebagian");
    expect(document.querySelector('[data-testid="tracking-invoice-total-inv-1"]').textContent).toBe(formatInvoiceMoney(110000));
    expect(document.querySelector('[data-testid="tracking-invoice-outstanding-inv-2"]').textContent).toBe(formatInvoiceMoney(20000));
    expect(document.querySelector('[data-testid="tracking-invoice-pdf-inv-1"]').getAttribute("href"))
      .toBe("http://localhost:8000/api/public/tracking/tok-public-aaaaaaaa/invoices/inv-1/pdf?disposition=attachment");
    expect(document.querySelector('[data-testid="tracking-invoice-pdf-inv-1"]').getAttribute("download")).not.toBeNull();
    expect(document.querySelector('[data-testid="tracking-invoice-view-pdf-inv-1"]').textContent).toBe("Buka PDF");
    expect(document.querySelector('[data-testid="tracking-invoice-view-pdf-inv-2"]').textContent).toBe("Buka PDF");
    expect(document.querySelector('[data-testid="tracking-invoice-detail-inv-1"]')).toBeNull();

    await act(async () => {
      document.querySelector('[data-testid="tracking-invoice-open-inv-1"]').click();
    });
    await flush();
    const panel = document.querySelector('[data-testid="tracking-invoice-detail-inv-1"]');
    expect(panel).toBeTruthy();
    expect(panel.textContent).toMatch(/Cup 8oz/);
    expect(panel.textContent).toMatch(/Status pembayaran: Belum lunas/);
    expect(document.querySelector('[data-testid="tracking-invoice-detail-total"]').textContent).toBe(formatInvoiceMoney(110000));
    expect(api.publicTrackingInvoice).toHaveBeenCalledWith("tok-public-aaaaaaaa", "inv-1");
    expect(document.querySelector('[data-testid="tracking-invoice-detail-inv-2"]')).toBeNull();
    expect(document.querySelector('[data-testid="tracking-invoice-open-inv-1"]').textContent).toBe("Tutup detail");

    await act(async () => {
      document.querySelector('[data-testid="tracking-invoice-open-inv-1"]').click();
    });
    expect(document.querySelector('[data-testid="tracking-invoice-detail-inv-1"]')).toBeNull();
    expect(document.querySelector('[data-testid="tracking-invoice-open-inv-1"]').textContent).toBe("Detail");

    await act(async () => {
      document.querySelector('[data-testid="tracking-invoice-sheet-close"]').click();
    });
    expect(document.querySelector('[data-testid="tracking-invoice-sheet"]')).toBeNull();

    await act(async () => {
      tile(host).click();
    });
    expect(document.querySelector('[data-testid="tracking-invoice-sheet"]')).toBeTruthy();
    await act(async () => {
      document.querySelector('[data-testid="tracking-invoice-sheet"]').click();
    });
    expect(document.querySelector('[data-testid="tracking-invoice-sheet"]')).toBeNull();

    await act(async () => {
      tile(host).click();
    });
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(document.querySelector('[data-testid="tracking-invoice-sheet"]')).toBeNull();
    expect(host.scrollWidth).toBeLessThanOrEqual(host.clientWidth + 1);
    await act(async () => { root.unmount(); });
  });

  test("buka pdf replaces the drawer, then back restores detail and scroll", async () => {
    const openSpy = jest.spyOn(window, "open").mockImplementation(() => null);
    api.publicTrackingInvoices.mockResolvedValue({
      invoices: [
        issued,
        {
          ...issued,
          id: "inv-2",
          invoice_number: "INV/2026/09/01/0007_2",
          pdf_url: "/api/public/tracking/tok-public-aaaaaaaa/invoices/inv-2/pdf?disposition=attachment",
        },
      ],
    });
    api.publicTrackingInvoice.mockResolvedValue(detail);
    const { host, root } = await mount();
    await act(async () => { tile(host).click(); });
    await act(async () => {
      document.querySelector('[data-testid="tracking-invoice-open-inv-1"]').click();
    });
    await flush();
    const body = document.querySelector('[data-testid="tracking-invoice-sheet-body"]');
    body.scrollTop = 120;
    await act(async () => {
      document.querySelector('[data-testid="tracking-invoice-view-pdf-inv-1"]').click();
    });
    const viewer = document.querySelector('[data-testid="tracking-invoice-pdf-viewer"]');
    expect(viewer.getAttribute("data-invoice-id")).toBe("inv-1");
    expect(viewer.getAttribute("data-path")).toContain("/invoices/inv-1/pdf");
    expect(document.querySelector(".tracking-inv-list")).toBeNull();
    expect(document.querySelectorAll(".tracking-sheet-root")).toHaveLength(1);
    expect(document.querySelector('[data-testid="tracking-invoice-sheet-title"]').textContent).toBe("INV/2026/09/01/0007");
    expect(document.querySelector('[data-testid="tracking-invoice-pdf-back"]').textContent).toBe("Kembali");
    expect(openSpy).not.toHaveBeenCalled();

    await act(async () => {
      document.querySelector('[data-testid="tracking-invoice-pdf-back"]').click();
    });
    const restored = document.querySelector('[data-testid="tracking-invoice-sheet-body"]');
    expect(restored.querySelector(".tracking-inv-list")).toBeTruthy();
    expect(restored.scrollTop).toBe(120);
    expect(document.querySelector('[data-testid="tracking-invoice-detail-inv-1"]')).toBeTruthy();
    expect(document.querySelector('[data-testid="tracking-invoice-sheet-title"]').textContent).toBe("Faktur Penjualan");

    await act(async () => {
      document.querySelector('[data-testid="tracking-invoice-view-pdf-inv-2"]').click();
    });
    expect(document.querySelector('[data-testid="tracking-invoice-pdf-viewer"]').getAttribute("data-invoice-id")).toBe("inv-2");
    expect(document.querySelector('[data-testid="tracking-invoice-pdf-viewer"]').getAttribute("data-path")).toContain("/invoices/inv-2/pdf");
    await act(async () => {
      document.querySelector('[data-testid="tracking-invoice-sheet-close"]').click();
    });
    expect(document.querySelector('[data-testid="tracking-invoice-pdf-viewer"]')).toBeNull();
    expect(document.querySelector('[data-testid="tracking-invoice-sheet"]')).toBeNull();
    openSpy.mockRestore();
    await act(async () => { root.unmount(); });
  });

  test("detail failure is not shown as an empty invoice", async () => {
    api.publicTrackingInvoices.mockResolvedValue({ invoices: [issued] });
    api.publicTrackingInvoice.mockRejectedValue({ response: { status: 500 } });
    const { host, root } = await mount();
    await act(async () => {
      tile(host).click();
    });
    await act(async () => {
      document.querySelector('[data-testid="tracking-invoice-open-inv-1"]').click();
    });
    await flush();
    expect(document.querySelector('[data-testid="tracking-invoice-detail-error"]').textContent).toMatch(/Detail faktur gagal dimuat/);
    expect(document.querySelector('[data-testid="tracking-invoice-detail-inv-1"]').textContent).not.toMatch(/Belum tersedia/);
    api.publicTrackingInvoice.mockResolvedValueOnce(detail);
    await act(async () => {
      document.querySelector('[data-testid="tracking-invoice-detail-retry"]').click();
    });
    await flush();
    expect(document.querySelector('[data-testid="tracking-invoice-detail-status"]').textContent).toMatch(/Belum lunas/);
    await act(async () => { root.unmount(); });
  });

  test("pdf path rejects external and id-only links", () => {
    expect(safeInvoicePdfPath("https://files.example/inv.pdf")).toBe("");
    expect(safeInvoicePdfPath("/api/sales-invoices/inv-1/pdf")).toBe("");
    expect(safeInvoicePdfPath(issued.pdf_url)).toContain("/invoices/inv-1/pdf");
  });

  test("batal status uses its own label", async () => {
    api.publicTrackingInvoices.mockResolvedValue({
      invoices: [{ ...issued, payment_label: "Batal", financial_status: "cancelled", outstanding_amount: 0 }],
    });
    const { host, root } = await mount();
    await act(async () => {
      tile(host).click();
    });
    const status = document.querySelector('[data-testid="tracking-invoice-status-inv-1"]');
    expect(status.textContent).toBe("Batal");
    expect(status.className).toMatch(/is-void/);
    expect(document.querySelector('[data-testid="tracking-invoice-outstanding-inv-1"]').textContent).toBe("Rp 0");
    await act(async () => { root.unmount(); });
  });
});
