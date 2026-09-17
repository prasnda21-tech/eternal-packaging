import {
  actionablePengirimanOptions,
  canonicalShipmentId,
  selectSingleActionableId,
  shipmentIsActive,
  shipmentIsInactive,
  sjFileIsActive,
} from "./sjIntegrity";
import { pengirimanSjOptions, selectedPengirimanShipmentId } from "./deliveryProof";
import { sjHistory } from "./suratJalan";
import { sjFilesForLoading } from "./loadingConfirm";

describe("sjIntegrity identity and active filters", () => {
  const sj1 = { id: "c992dfa4-8c67-496c-87e7-bf7eb22a0ea6", number: "SJ/2026/09/12/000002", lines: [{ qty: 1000 }] };
  const sj2 = {
    id: "ee1e3067-33f2-4623-8796-161c76db6690",
    number: "SJ/2026/09/12/000002_2",
    delivered_at: "t",
    delivery_status: "Terkirim",
    lines: [{ qty: 4000 }],
  };
  const sj3 = {
    id: "26c9e4f0-8616-4c06-ab68-c11c4bbca3c5",
    number: "SJ/2026/09/12/000002_3",
    status: "superseded",
    cancelled: true,
    superseded_by_shipment_id: "ee1e3067-33f2-4623-8796-161c76db6690",
    lines: [{ qty: 1000 }],
  };
  const order = {
    shipments: [sj1, sj2, sj3],
    surat_jalan_files: [
      { id: sj1.id, shipment_id: sj1.id, sj_number: sj1.number, name: "1.pdf", lines: [{ qty: 1000 }] },
      { id: sj2.id, shipment_id: sj2.id, sj_number: sj2.number, name: "2.pdf", lines: [{ qty: 4000 }] },
      {
        id: sj3.id, shipment_id: sj3.id, sj_number: sj3.number, name: "3.pdf",
        status: "superseded", cancelled: true, lines: [{ qty: 1000 }],
      },
    ],
    delivery_confirmations: [{ shipment_id: sj2.id, qty: 4000 }],
  };

  test("canonical id is shipment.id never filename", () => {
    expect(canonicalShipmentId(sj1)).toBe(sj1.id);
    expect(canonicalShipmentId({ name: "Surat Jalan.pdf" })).toBe("");
  });

  test("superseded SJ is inactive and excluded from loading/delivery tasks", () => {
    expect(shipmentIsInactive(sj3)).toBe(true);
    expect(shipmentIsActive(sj1)).toBe(true);
    expect(shipmentIsActive(sj2)).toBe(true);
    expect(sjFileIsActive(order.surat_jalan_files[2], order)).toBe(false);
    expect(sjFilesForLoading(order).map((f) => f.id)).toEqual([sj1.id, sj2.id]);
    const opts = pengirimanSjOptions(order);
    expect(opts.map((o) => o.id)).toEqual([sj1.id, sj2.id]);
    expect(opts.map((o) => o.number || o.label)).toEqual([sj1.number, sj2.number]);
    expect(opts.every((o) => !String(o.number).includes(o.id))).toBe(true);
    const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
    expect(opts.every((o) => !UUID_RE.test(String(o.number)))).toBe(true);
    expect(opts.find((o) => o.id === sj2.id).delivered).toBe(true);
    const actionable = actionablePengirimanOptions(opts);
    expect(actionable.map((o) => o.id)).toEqual([sj1.id]);
    expect(selectSingleActionableId(actionable)).toBe(sj1.id);
    expect(selectedPengirimanShipmentId(order, "")).toBe(sj1.id);
  });

  test("history still includes superseded SJ with status", () => {
    const hist = sjHistory(order);
    expect(hist.map((f) => f.id)).toEqual([sj1.id, sj2.id, sj3.id]);
    expect(hist[2].status).toBe("superseded");
  });
});
