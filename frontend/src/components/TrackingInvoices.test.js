import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import TrackingInvoices from "./TrackingInvoices";
import { formatInvoiceMoney, safeInvoicePdfPath } from "../lib/trackingInvoices";

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

describe("TrackingInvoices", () => {
  beforeEach(() => {
    api.publicTrackingInvoices.mockReset();
    api.publicTrackingInvoice.mockReset();
    document.body.innerHTML = "";
  });

  test("loading, empty, and load failure stay distinct", async () => {
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
    expect(host.querySelector('[data-testid="tracking-invoices-loading"]').textContent).toBe("Memuat tagihan...");
    expect(host.querySelector('[data-testid="tracking-invoices-empty"]')).toBeNull();
    expect(host.textContent).not.toMatch(/Faktur belum tersedia/);

    await act(async () => {
      resolveList({ invoices: [] });
    });
    await flush();
    expect(host.querySelector('[data-testid="tracking-invoices-empty"]').textContent).toBe("Faktur belum tersedia");
    expect(host.querySelector('[data-testid="tracking-invoices-error"]')).toBeNull();

    api.publicTrackingInvoices.mockRejectedValueOnce({ response: { status: 500 } });
    await act(async () => {
      host.querySelector('[data-testid="tracking-invoices-retry"]')
        ? host.querySelector('[data-testid="tracking-invoices-retry"]').click()
        : null;
    });
    api.publicTrackingInvoices.mockRejectedValueOnce({ response: { status: 500 } });
    await act(async () => { root.unmount(); });

    api.publicTrackingInvoices.mockRejectedValue({ response: { status: 502 } });
    const failed = await mount();
    expect(failed.host.querySelector('[data-testid="tracking-invoices-error"]').textContent).toMatch(/Tagihan gagal dimuat/);
    expect(failed.host.textContent).not.toMatch(/Faktur belum tersedia/);
    expect(failed.host.textContent).not.toMatch(/Lunas/);
    api.publicTrackingInvoices.mockReset();
    api.publicTrackingInvoices.mockResolvedValue({ invoices: [issued] });
    await act(async () => {
      failed.host.querySelector('[data-testid="tracking-invoices-retry"]').click();
    });
    await flush();
    await flush();
    expect(failed.host.querySelector('[data-testid="tracking-invoice-number-inv-1"]').textContent)
      .toBe("INV/2026/09/01/0007");
    expect(failed.host.querySelector('[data-testid="tracking-invoices-empty"]')).toBeNull();
    await act(async () => { failed.root.unmount(); });
  });

  test("lihat faktur opens the matching detail and pdf link is token scoped", async () => {
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
      id === "inv-2" ? { ...detail, id: "inv-2", invoice_number: "INV/2026/09/01/0007_2", payment_label: "Sebagian" } : detail
    ));
    const { host, root } = await mount();
    expect(host.querySelectorAll(".tracking-inv-row")).toHaveLength(2);
    expect(host.querySelector('[data-testid="tracking-invoice-status-inv-1"]').textContent).toBe("Belum lunas");
    expect(host.querySelector('[data-testid="tracking-invoice-status-inv-2"]').textContent).toBe("Sebagian");
    expect(host.querySelector('[data-testid="tracking-invoice-pdf-inv-1"]').getAttribute("href"))
      .toBe("http://localhost:8000/api/public/tracking/tok-public-aaaaaaaa/invoices/inv-1/pdf?disposition=attachment");
    expect(host.querySelector('[data-testid="tracking-invoice-detail-inv-1"]')).toBeNull();

    await act(async () => {
      host.querySelector('[data-testid="tracking-invoice-open-inv-1"]').click();
    });
    await flush();
    const panel = host.querySelector('[data-testid="tracking-invoice-detail-inv-1"]');
    expect(panel).toBeTruthy();
    expect(panel.textContent).toMatch(/Cup 8oz/);
    expect(panel.textContent).toMatch(/Status pembayaran: Belum lunas/);
    expect(host.querySelector('[data-testid="tracking-invoice-detail-total"]').textContent).toBe(formatInvoiceMoney(110000));
    expect(api.publicTrackingInvoice).toHaveBeenCalledWith("tok-public-aaaaaaaa", "inv-1");
    expect(host.querySelector('[data-testid="tracking-invoice-detail-inv-2"]')).toBeNull();

    await act(async () => {
      host.querySelector('[data-testid="tracking-invoice-open-inv-1"]').click();
    });
    expect(host.querySelector('[data-testid="tracking-invoice-detail-inv-1"]')).toBeNull();
    expect(host.querySelector('[data-testid="tracking-invoice-open-inv-1"]').textContent).toBe("Lihat Faktur");
    expect(host.scrollWidth).toBeLessThanOrEqual(host.clientWidth + 1);
    await act(async () => { root.unmount(); });
  });

  test("detail failure is not shown as an empty invoice", async () => {
    api.publicTrackingInvoices.mockResolvedValue({ invoices: [issued] });
    api.publicTrackingInvoice.mockRejectedValue({ response: { status: 500 } });
    const { host, root } = await mount();
    await act(async () => {
      host.querySelector('[data-testid="tracking-invoice-open-inv-1"]').click();
    });
    await flush();
    expect(host.querySelector('[data-testid="tracking-invoice-detail-error"]').textContent).toMatch(/Detail faktur gagal dimuat/);
    expect(host.querySelector('[data-testid="tracking-invoice-detail-inv-1"]').textContent).not.toMatch(/Faktur belum tersedia/);
    api.publicTrackingInvoice.mockResolvedValueOnce(detail);
    await act(async () => {
      host.querySelector('[data-testid="tracking-invoice-detail-retry"]').click();
    });
    await flush();
    expect(host.querySelector('[data-testid="tracking-invoice-detail-status"]').textContent).toMatch(/Belum lunas/);
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
    const status = host.querySelector('[data-testid="tracking-invoice-status-inv-1"]');
    expect(status.textContent).toBe("Batal");
    expect(status.className).toMatch(/is-void/);
    expect(host.querySelector('[data-testid="tracking-invoice-outstanding-inv-1"]').textContent).toBe("Rp 0");
    await act(async () => { root.unmount(); });
  });
});
