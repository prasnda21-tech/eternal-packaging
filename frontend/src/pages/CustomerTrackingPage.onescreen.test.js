import { act } from "react";

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: jest.fn(),
}));
import { createRoot } from "react-dom/client";
import api, { publicBlobRequest } from "../lib/api";
import CustomerTrackingPage from "./CustomerTrackingPage";

const STAGES = [
  "Order Masuk", "Design", "Approval Design", "Payment", "Approval Payment",
  "Penjadwalan", "Screener", "Persiapan Helper", "Operator", "Konfirmasi Sample",
  "Cetak", "Packing", "Surat Jalan", "Loading", "Pengiriman",
];

const payload = {
  order_number: "PO-2025-00123",
  customer_name: "PT Sumber Makmur",
  summary: {
    items: [{ name: "Standing Pouch", quantity: 5000, unit: "pcs" }],
    deadline: "2025-09-25",
    order_date: "2025-09-01",
    outlet: "Outlet A",
  },
  design_approval: {
    status: "active",
    path: "/customer/design-approval/tok-public-aaaaaaaaaaaaaaaa",
    preview_url: "/api/public/tracking/tok/design-preview",
  },
  workflow: STAGES.map((stage, i) => ({
    stage,
    status: i < 2 ? "completed" : i === 2 ? "in_progress" : "waiting",
    label: i === 2 ? "Menunggu approval" : "",
  })),
  documents: {
    design_approved: [],
    surat_jalan: [],
  },
  delivery_proofs: [],
  delivery_history: [],
  shipments_evidence: [],
};

jest.mock("react-router-dom", () => ({
  useParams: () => ({ token: "tok-public-aaaaaaaaaaaaaaaa" }),
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  publicApiUrl: (path) => (path ? `http://localhost:8000${path}` : ""),
  publicBlobRequest: jest.fn(),
  default: {
    publicTracking: jest.fn(),
    publicTrackingInvoices: jest.fn(),
    publicTrackingInvoice: jest.fn(),
    publicTrackingPaymentProofs: jest.fn(),
    publicTrackingPaymentProofFile: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

async function mountAt(width) {
  const host = document.createElement("div");
  host.style.width = `${width}px`;
  host.style.overflow = "visible";
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(<CustomerTrackingPage />);
  });
  await flush();
  return { host, root };
}

describe("Customer tracking one-screen dashboard", () => {
  beforeEach(() => {
    api.publicTracking.mockReset();
    api.publicTracking.mockResolvedValue(payload);
    api.publicTrackingInvoices.mockReset();
    api.publicTrackingInvoices.mockResolvedValue({ invoices: [] });
    api.publicTrackingInvoice.mockReset();
    api.publicTrackingPaymentProofs.mockReset();
    api.publicTrackingPaymentProofs.mockResolvedValue({ proofs: [] });
    document.body.innerHTML = "";
  });

  test("renders compact dashboard PO summary approval map and documents", async () => {
    const { host, root } = await mountAt(390);
    expect(host.querySelector('[data-testid="customer-tracking-page"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="tracking-po-summary"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="tracking-order-number"]').textContent).toBe("PO-2025-00123");
    expect(host.querySelector('[data-testid="tracking-deadline"]').textContent).toContain("25/09/2025");
    expect(host.textContent).not.toMatch(/Invalid Date/);
    expect(host.querySelector('[data-testid="tracking-approval-cta"]').getAttribute("href"))
      .toBe("/customer/design-approval/tok-public-aaaaaaaaaaaaaaaa");
    expect(host.querySelector('[data-testid="tracking-workflow"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="tracking-workflow-rail"]')).toBeTruthy();
    expect(host.querySelectorAll(".tracking-map-node")).toHaveLength(15);
    expect(host.querySelector('[data-testid="tracking-workflow-rail"]').getAttribute("data-branched")).toBe("0");
    expect(host.querySelector('[data-rail-index="6"]').getAttribute("data-testid")).toBe("tracking-stage-Screener");
    expect(host.querySelector('[data-rail-index="7"]').getAttribute("data-testid")).toBe("tracking-stage-Persiapan Helper");
    expect(host.querySelector('[data-rail-index="8"]').getAttribute("data-testid")).toBe("tracking-stage-Operator");
    expect(Number(host.querySelector('[data-testid="tracking-stage-Screener"]').getAttribute("data-rail-index")))
      .toBeLessThan(Number(host.querySelector('[data-testid="tracking-stage-Persiapan Helper"]').getAttribute("data-rail-index")));
    expect(host.querySelector('[data-testid="tracking-stage-Design"]').className).toMatch(/is-done|is-completed/);
    expect(host.querySelector('[data-testid="tracking-stage-Approval Design"]').className).toMatch(/is-run|is-in_progress/);
    expect(host.querySelector('[data-testid="tracking-stage-Pengiriman"]').className).toMatch(/is-wait|is-waiting/);
    expect(host.querySelector(".tracking-rail-svg")).toBeTruthy();
    expect(host.querySelectorAll(".rail-seg").length).toBeGreaterThan(8);
    expect(host.querySelector('[data-testid="tracking-doc-tile-design"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="tracking-doc-tile-payment"]').disabled).toBe(true);
    expect(host.querySelector('[data-testid="tracking-doc-tile-sj"]').disabled).toBe(true);
    const docs = host.querySelector('[data-testid="tracking-documents"]');
    const faktur = docs.querySelector('[data-testid="tracking-doc-tile-faktur"]');
    expect(faktur).toBeTruthy();
    expect(faktur.disabled).toBe(true);
    expect(faktur.textContent).toMatch(/Belum tersedia/);
    expect(faktur.textContent).not.toMatch(/Rp/);
    expect(host.querySelector('[data-testid="tracking-invoices"]')).toBeNull();
    expect(host.textContent).not.toMatch(/Tagihan/);
    const workflow = host.querySelector('[data-testid="tracking-workflow"]');
    expect(host.querySelector('[data-testid="tracking-po-summary"]').compareDocumentPosition(workflow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(workflow.compareDocumentPosition(docs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const gridIds = [...docs.querySelector(".tracking-doc-grid").querySelectorAll("[data-testid^='tracking-doc-tile-']")]
      .map((node) => node.getAttribute("data-testid"));
    expect(gridIds).toEqual([
      "tracking-doc-tile-design",
      "tracking-doc-tile-payment",
      "tracking-doc-tile-faktur",
      "tracking-doc-tile-sj",
      "tracking-doc-tile-ship",
    ]);
    await act(async () => { root.unmount(); });
  });

  test("detail and timeline sheets open and close", async () => {
    const { host, root } = await mountAt(375);
    await act(async () => {
      host.querySelector('[data-testid="tracking-detail-toggle"]').click();
    });
    expect(host.querySelector('[data-testid="tracking-detail"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="tracking-detail-body"]').textContent).toMatch(/PT Sumber Makmur/);
    await act(async () => {
      host.querySelector('[data-testid="tracking-detail-close"]').click();
    });
    expect(host.querySelector('[data-testid="tracking-detail"]')).toBeFalsy();

    await act(async () => {
      host.querySelector('[data-testid="tracking-open-timeline"]').click();
    });
    expect(host.querySelector('[data-testid="tracking-timeline"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="tracking-timeline"]').textContent).toMatch(/Approval Design/);
    await act(async () => {
      host.querySelector('[data-testid="tracking-timeline-sheet-close"]').click();
    });
    expect(host.querySelector('[data-testid="tracking-timeline-sheet"]')).toBeFalsy();
    await act(async () => { root.unmount(); });
  });

  test("available design tile uses existing approval link and unavailable tiles stay closed", async () => {
    const { host, root } = await mountAt(414);
    const design = host.querySelector('[data-testid="tracking-doc-tile-design"]');
    expect(design.getAttribute("href")).toBe("/customer/design-approval/tok-public-aaaaaaaaaaaaaaaa");
    expect(host.textContent).not.toMatch(/tok-public-aaaaaaaaaaaaaaaa/);
    await act(async () => {
      host.querySelector('[data-testid="tracking-doc-tile-sj"]').click();
    });
    expect(host.querySelector('[data-testid="tracking-lightbox"]')).toBeFalsy();
    expect(host.querySelector('[data-testid="tracking-doc-tile-payment"]').disabled).toBe(true);
    await act(async () => { root.unmount(); });
  });

  test.each([375, 390, 430])("mobile %ipx layout has no horizontal overflow", async (width) => {
    const { host, root } = await mountAt(width);
    const page = host.querySelector('[data-testid="customer-tracking-page"]');
    expect(page.scrollWidth).toBeLessThanOrEqual(Math.max(page.clientWidth, width) + 1);
    expect(host.scrollWidth).toBeLessThanOrEqual(width + 1);
    await act(async () => { root.unmount(); });
  });

  test("unsigned surat jalan stays on the tile and is not listed as signed", async () => {
    api.publicTracking.mockResolvedValue({
      ...payload,
      documents: {
        design_approved: [],
        surat_jalan: [{
          id: "sj-open",
          sj_number: "SJ/OPEN",
          shipment_id: "ship-open",
          has_signed: false,
          preview_url: "/api/public/tracking/tok/surat-jalan/sj-open?disposition=inline",
          download_url: "/api/public/tracking/tok/surat-jalan/sj-open?disposition=attachment",
        }],
      },
    });
    const { host, root } = await mountAt(390);
    const tile = host.querySelector('[data-testid="tracking-doc-tile-sj"]');
    expect(tile.disabled).toBe(false);
    expect(tile.textContent).toMatch(/Tersedia/);
    expect(tile.textContent).not.toMatch(/Bertanda tangan/);
    expect(host.querySelector('[data-testid="tracking-surat-jalan"]')).toBeFalsy();
    expect(host.querySelector('[data-testid="tracking-sj-sj-open"]')).toBeFalsy();
    publicBlobRequest.mockRejectedValueOnce(Object.assign(new Error("denied"), { status: 404 }));
    const opened = jest.spyOn(window, "open").mockImplementation(() => null);
    await act(async () => { tile.click(); });
    await flush();
    expect(opened).not.toHaveBeenCalled();
    expect(host.querySelector('[data-testid="tracking-sj-viewer"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="tracking-sj-viewer"]').getAttribute("data-document-key"))
      .toBe("sj:sj-open");
    opened.mockRestore();
    expect(api.publicTracking).toHaveBeenCalledTimes(1);
    await act(async () => { root.unmount(); });
  });

  test("signed surat jalan appears once with identity and signature status", async () => {
    api.publicTracking.mockResolvedValue({
      ...payload,
      order_number: "PO-2025-00123",
      documents: {
        design_approved: [],
        surat_jalan: [
          {
            id: "sj-a1",
            name: "SJ-A1.pdf",
            sj_number: "SJ/A1",
            shipment_id: "ship-a",
            has_signed: true,
            signed_at: "2026-09-01T10:07:00+00:00",
            recipient_name: "Andi Penerima",
            preview_url: "/api/public/tracking/tok/surat-jalan/sj-a1?disposition=inline",
            download_url: "/api/public/tracking/tok/surat-jalan/sj-a1?disposition=attachment",
          },
          {
            id: "sj-b",
            name: "SJ-B.pdf",
            sj_number: "SJ/B",
            shipment_id: "ship-b",
            has_signed: false,
            preview_url: "/api/public/tracking/tok/surat-jalan/sj-b?disposition=inline",
            download_url: "/api/public/tracking/tok/surat-jalan/sj-b?disposition=attachment",
          },
        ],
      },
    });
    const { host, root } = await mountAt(390);
    const section = host.querySelector('[data-testid="tracking-surat-jalan"]');
    expect(section).toBeTruthy();
    const rows = section.querySelectorAll(":scope > article");
    expect(rows).toHaveLength(1);
    const row = host.querySelector('[data-testid="tracking-sj-sj-a1"]');
    expect(row.textContent).toMatch(/SJ\/A1/);
    expect(row.textContent).toMatch(/Bertanda tangan/);
    expect(row.textContent).toMatch(/Andi Penerima/);
    expect(row.getAttribute("data-shipment-id")).toBe("ship-a");
    expect(row.textContent).not.toMatch(/SJ\/B/);
    expect(host.querySelector('[data-testid="tracking-sj-sj-b"]')).toBeFalsy();
    expect(host.querySelectorAll('[data-testid="tracking-sj-sj-a1"]')).toHaveLength(1);
    const open = host.querySelector('[data-testid="tracking-sj-sj-a1-open"]');
    expect(open.tagName).toBe("BUTTON");
    expect(open.getAttribute("href")).toBeNull();
    expect(open.getAttribute("target")).toBeNull();
    const download = host.querySelector('[data-testid="tracking-sj-sj-a1-download"]');
    expect(download.tagName).toBe("A");
    expect(download.getAttribute("href")).toMatch(/\/surat-jalan\/sj-a1\?disposition=attachment/);
    expect(download.getAttribute("download")).toBe("SJ-A1.pdf");
    publicBlobRequest.mockRejectedValueOnce(Object.assign(new Error("denied"), { status: 404 }));
    const opened = jest.spyOn(window, "open").mockImplementation(() => null);
    await act(async () => { open.click(); });
    await flush();
    expect(opened).not.toHaveBeenCalled();
    expect(host.querySelector('[data-testid="tracking-sj-viewer"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="tracking-sj-viewer"]').getAttribute("data-document-key"))
      .toBe("sj:sj-a1");
    opened.mockRestore();
    expect(host.querySelector('[data-testid="tracking-order-number"]').textContent).toBe("PO-2025-00123");
    expect(host.querySelector('[data-testid="tracking-doc-tile-sj"]').textContent).toMatch(/Bertanda tangan/);
    expect(api.publicTracking).toHaveBeenCalledTimes(1);
    expect(api.publicTracking).toHaveBeenCalledWith("tok-public-aaaaaaaaaaaaaaaa");
    await act(async () => { root.unmount(); });
  });

  test("public tracking fetch stays GET and token is not rewritten", async () => {
    const { root } = await mountAt(390);
    expect(api.publicTracking).toHaveBeenCalledWith("tok-public-aaaaaaaaaaaaaaaa");
    expect(api.publicTracking).toHaveBeenCalledTimes(1);
    await act(async () => { root.unmount(); });
  });
});
