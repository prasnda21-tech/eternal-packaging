import { act } from "react";

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: jest.fn(),
}));

const routeParams = { token: "", code: "", customerSlug: "", poSlug: "" };
jest.mock("react-router-dom", () => ({
  useParams: () => routeParams,
}), { virtual: true });

import { createRoot } from "react-dom/client";
import api from "../lib/api";
import CustomerTrackingPage from "./CustomerTrackingPage";

const CODE = "fixture-code-cafe-nio";

const payload = {
  order_number: "PO/2026/10/02/000012",
  customer_name: "Café Niño & Co.",
  summary: {
    items: [{ name: "Cup", quantity: 12, unit: "pcs" }],
    order_date: "2026-10-02",
    deadline: "2026-10-02",
  },
  workflow: [],
  documents: { design_approved: [], surat_jalan: [] },
  delivery_proofs: [],
  delivery_history: [],
  shipments_evidence: [],
};

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

function setRoute(next) {
  Object.keys(routeParams).forEach((key) => { delete routeParams[key]; });
  Object.assign(routeParams, next);
}

async function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<CustomerTrackingPage />); });
  await flush();
  return { host, root };
}

describe("pretty and legacy Live Tracking routes", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    api.publicTracking.mockReset();
    api.publicTracking.mockResolvedValue(payload);
    api.publicTrackingInvoices.mockResolvedValue({ invoices: [] });
    api.publicTrackingPaymentProofs.mockResolvedValue({ proofs: [] });
  });

  test("a different slug still opens the tracking selected by the code", async () => {
    setRoute({ customerSlug: "pelanggan-lain", poSlug: "PO-SALAH", code: CODE });
    const { host, root } = await mount();
    expect(api.publicTracking).toHaveBeenCalledWith(CODE);
    expect(host.querySelector('[data-testid="tracking-customer-name"]').textContent).toBe("Café Niño & Co.");
    expect(host.querySelector('[data-testid="tracking-order-number"]').textContent).toBe("PO/2026/10/02/000012");
    expect(host.querySelector('[data-testid="tracking-deadline"]').textContent).toContain("02/10/2026");
    expect(host.textContent).not.toMatch(/pelanggan-lain/);
    expect(host.textContent).not.toMatch(/PO-SALAH/);
    expect(host.textContent).not.toMatch(/Invalid Date/);
    await act(async () => {
      host.querySelector('[data-testid="tracking-detail-toggle"]').click();
    });
    const detail = host.querySelector('[data-testid="tracking-detail-body"]').textContent;
    expect(detail).toContain("02/10/2026");
    expect(detail).toContain("PO/2026/10/02/000012");
    expect(detail).toContain("Café Niño & Co.");
    await act(async () => { root.unmount(); });
  });

  test("the legacy token route opens the same payload", async () => {
    setRoute({ token: CODE });
    const { host, root } = await mount();
    expect(api.publicTracking).toHaveBeenCalledWith(CODE);
    expect(host.querySelector('[data-testid="tracking-customer-name"]').textContent).toBe("Café Niño & Co.");
    expect(host.querySelector('[data-testid="tracking-deadline"]').textContent).toContain("02/10/2026");
    await act(async () => { root.unmount(); });
  });

  test("an invalid code is rejected and an empty date is not Invalid Date", async () => {
    api.publicTracking.mockRejectedValue({ response: { status: 404 } });
    setRoute({ customerSlug: "frutavor", poSlug: "PO-2026-10-02-000012", code: "short" });
    const rejected = await mount();
    expect(api.publicTracking).toHaveBeenCalledWith("short");
    expect(rejected.host.querySelector('[data-testid="tracking-error"]').textContent).toMatch(/tidak ditemukan/i);
    await act(async () => { rejected.root.unmount(); });

    api.publicTracking.mockResolvedValue({
      ...payload,
      summary: { ...payload.summary, deadline: "", order_date: "" },
    });
    setRoute({ token: CODE });
    const { host, root } = await mount();
    expect(host.querySelector('[data-testid="tracking-deadline"]').textContent).toContain("—");
    expect(host.textContent).not.toMatch(/Invalid Date/);
    await act(async () => { root.unmount(); });
  });
});
