import fs from "fs";
import path from "path";
import { STAGES } from "./constants";
import {
  selectedImportRows,
  toggleSelectAll,
  importCounts,
} from "./importPreview";
import {
  canDeleteLoadingConfirmation,
  loadingConfirmations,
  deliveredQtyForLoadingConfirmation,
} from "./loadingConfirm";

describe("stage label Pengiriman", () => {
  test("15 stages and last is Pengiriman", () => {
    expect(STAGES).toHaveLength(15);
    expect(STAGES[14]).toBe("Pengiriman");
  });

  test("Konfirmasi Terkirim action preserved", () => {
    const proof = fs.readFileSync(path.join(__dirname, "../components/DeliveryProofPanel.jsx"), "utf8");
    expect(proof).toMatch(/Konfirmasi Terkirim/);
    expect(proof).toMatch(/confirm-delivered-button/);
  });
});

describe("stock import UI", () => {
  test("Stock page has import button and modal", () => {
    const src = fs.readFileSync(path.join(__dirname, "../pages/StockSaatIni.jsx"), "utf8");
    expect(src).toMatch(/stock-import-button/);
    expect(src).toMatch(/Impor Stock/);
    expect(src).toMatch(/stock-import-modal/);
    expect(src).toMatch(/Sesuaikan Stock Terpilih/);
    expect(src).toMatch(/current_stock\.import/);
  });

  test("checkbox helpers select only selectable", () => {
    const rows = [
      { row_id: "r0", selectable: true, selected: false, status: "VALID" },
      { row_id: "r1", selectable: false, selected: false, status: "INVALID" },
    ];
    const all = toggleSelectAll(rows, true);
    expect(all[0].selected).toBe(true);
    expect(all[1].selected).toBe(false);
    expect(selectedImportRows(all)).toHaveLength(1);
    expect(importCounts(all).selected).toBe(1);
  });
});

describe("loading delete UI", () => {
  test("LoadingPanel has Hapus control", () => {
    const src = fs.readFileSync(path.join(__dirname, "../components/LoadingPanel.jsx"), "utf8");
    expect(src).toMatch(/loading-delete-/);
    expect(src).toMatch(/Hapus data Loading ini\?/);
    expect(src).toMatch(/loading\.delete/);
  });

  test("eligibility blocks delivered confirmation", () => {
    const order = {
      delivery_status: "Sebagian Terkirim",
      qty_delivered: 400,
      total_quantity: 1000,
      loading_confirmations: [
        { id: "c1", lines: [{ line_idx: 0, qty: 400 }] },
        { id: "c2", lines: [{ line_idx: 0, qty: 200 }] },
      ],
    };
    expect(deliveredQtyForLoadingConfirmation(order, "c1")).toBe(400);
    expect(canDeleteLoadingConfirmation(order, order.loading_confirmations[0])).toBe(false);
    expect(canDeleteLoadingConfirmation(order, order.loading_confirmations[1])).toBe(true);
  });

  test("cancelled confirmations filtered from active list", () => {
    const order = {
      loading_confirmations: [
        { id: "c1", cancelled: true, lines: [] },
        { id: "c2", lines: [{ qty: 10 }] },
      ],
    };
    expect(loadingConfirmations(order).map((c) => c.id)).toEqual(["c2"]);
  });
});

describe("prediksi import UI", () => {
  test("PrediksiOrder has import histori controls", () => {
    const src = fs.readFileSync(path.join(__dirname, "../pages/PrediksiOrder.jsx"), "utf8");
    expect(src).toMatch(/prediksi-import-button/);
    expect(src).toMatch(/Impor Histori Order/);
    expect(src).toMatch(/prediksi-import-modal/);
    expect(src).toMatch(/reports\.order_prediction\.import/);
    expect(src).toMatch(/prediksi-source-/);
    expect(src).toMatch(/label: "Sales"/);
    expect(src).toMatch(/label: "Omzet"/);
    expect(src).toMatch(/r\.omzet == null/);
  });

  test("ImportPreviewModal surfaces server detail errors", () => {
    const src = fs.readFileSync(path.join(__dirname, "../components/ImportPreviewModal.jsx"), "utf8");
    expect(src).toMatch(/Gagal mem-preview file/);
    expect(src).toMatch(/d\.message/);
    expect(src).toMatch(/Tidak terhubung ke server/);
  });
});
