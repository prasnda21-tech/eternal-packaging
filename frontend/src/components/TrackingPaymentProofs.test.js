import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import TrackingPaymentProofs from "./TrackingPaymentProofs";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    publicTrackingPaymentProofs: jest.fn(),
    publicTrackingPaymentProofFile: jest.fn(),
  },
}));

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: jest.fn(),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const proofs = [
  {
    receipt_id: "r1",
    receipt_number: "PAY-1",
    receipt_date: "2026-09-04",
    amount: 3000,
    status_label: "Sebagian",
    active: true,
    file_name: "a.png",
    content_type: "image/png",
  },
  {
    receipt_id: "r2",
    receipt_number: "PAY-2",
    receipt_date: "2026-09-05",
    amount: 2000,
    status_label: "Dibatalkan",
    active: false,
    file_name: "b.jpg",
    content_type: "image/jpeg",
  },
];

describe("TrackingPaymentProofs", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    api.publicTrackingPaymentProofs.mockReset();
    api.publicTrackingPaymentProofFile.mockReset();
    window.open = jest.fn();
    URL.createObjectURL = jest.fn(() => "blob:pay");
    URL.revokeObjectURL = jest.fn();
  });

  test("empty list stays unavailable and does not invent a proof", async () => {
    api.publicTrackingPaymentProofs.mockResolvedValue({ proofs: [] });
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => { root.render(<TrackingPaymentProofs token="tok" />); });
    await flush();
    const tile = host.querySelector('[data-testid="tracking-doc-tile-payment"]');
    expect(tile.disabled).toBe(true);
    expect(tile.textContent).toMatch(/Belum tersedia/);
    expect(tile.getAttribute("data-active-count")).toBe("0");
    expect(host.textContent).not.toMatch(/Selesai/);
    await act(async () => { root.unmount(); });
  });

  test("partial receipts stay separate and a reversal is not an active payment", async () => {
    api.publicTrackingPaymentProofs.mockResolvedValue({ proofs });
    api.publicTrackingPaymentProofFile.mockResolvedValue(new Blob([new Uint8Array([1])], { type: "image/png" }));
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => { root.render(<TrackingPaymentProofs token="tok" />); });
    await flush();
    const tile = host.querySelector('[data-testid="tracking-doc-tile-payment"]');
    expect(tile.disabled).toBe(false);
    expect(tile.getAttribute("data-active-count")).toBe("1");
    expect(tile.textContent).toMatch(/Bukti tersimpan/);
    expect(tile.textContent).not.toMatch(/Selesai/);
    await act(async () => { tile.click(); });
    const list = document.querySelector('[data-testid="tracking-payment-proof-list"]');
    expect(list.querySelectorAll("li")).toHaveLength(2);
    expect(list.textContent).toMatch(/PAY-1/);
    expect(list.textContent).toMatch(/PAY-2/);
    expect(list.textContent).toContain(Number(3000).toLocaleString("id-ID"));
    expect(list.textContent).toContain(Number(2000).toLocaleString("id-ID"));
    expect(list.textContent).toMatch(/Dibatalkan/);
    expect(list.textContent).not.toMatch(/storage/);
    expect(list.textContent).not.toMatch(/jurnal/i);
    await act(async () => {
      document.querySelector('[data-testid="tracking-payment-proof-open-r1"]').click();
    });
    await flush();
    expect(api.publicTrackingPaymentProofFile).toHaveBeenCalledWith(
      "tok",
      "r1",
      expect.objectContaining({ download: false, signal: expect.any(AbortSignal) }),
    );
    expect(document.querySelector('[data-testid="tracking-payment-proof-viewer"]')).toBeTruthy();
    expect(window.open).not.toHaveBeenCalled();
    await act(async () => { root.unmount(); });
  });

  test("component does not leak accounting or storage fields", () => {
    const src = fs.readFileSync(path.join(__dirname, "TrackingPaymentProofs.jsx"), "utf8");
    expect(src).not.toMatch(/accounting_status/);
    expect(src).not.toMatch(/storage_path/);
    expect(src).not.toMatch(/localStorage/);
    expect(src).not.toMatch(/window\.open/);
    expect(src).toMatch(/publicTrackingPaymentProofs/);
  });
});
