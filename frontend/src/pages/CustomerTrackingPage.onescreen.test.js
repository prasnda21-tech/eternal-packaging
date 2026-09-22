import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
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
  default: {
    publicTracking: jest.fn(),
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
    document.body.innerHTML = "";
  });

  test("renders compact dashboard PO summary approval map and documents", async () => {
    const { host, root } = await mountAt(390);
    expect(host.querySelector('[data-testid="customer-tracking-page"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="tracking-po-summary"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="tracking-order-number"]').textContent).toBe("PO-2025-00123");
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
    expect(host.querySelector('[data-testid="tracking-sj-sj-a1-open"]').getAttribute("href"))
      .toMatch(/\/surat-jalan\/sj-a1/);
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
