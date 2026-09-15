/**
 * Development-only dummy orders for PWA Pengiriman color-modal preview.
 *
 * Activation requires:
 *   - process.env.NODE_ENV === "development"
 *   - localhost:3003 (live preview) OR query param previewPwaDeliveryModal=1 (tests)
 *   - order id matching one of the 3 dummy preview ids
 *
 * Never writes backend/DB/localStorage/sessionStorage/IndexedDB.
 * Dummy shipment UUIDs are internal only and must not be shown to operators.
 */

export const PWA_DELIVERY_TASK_PREVIEW_PARAM = "previewPwaDeliveryModal";
export const PWA_DELIVERY_TASK_PREVIEW_COUNT = 3;

export const PWA_DELIVERY_TASK_PREVIEW_IDS = Object.freeze([
  "preview-pengiriman-1",
  "preview-pengiriman-2",
  "preview-pengiriman-3",
]);

export const PWA_DELIVERY_TASK_PREVIEW_SHIPMENT_IDS = Object.freeze({
  d1: "3ab43353-5705-476b-97fd-f3beb1b1314c",
  d1b: "7c91e2a0-4b18-4d6f-9c22-0e8f1a2b3c4d",
  d2: "b6e4c8d1-2f90-4a17-8c55-1d2e3f4a5b6c",
  d3: "c0ffee00-10aa-4bbb-8ccc-ddddeeeeffff",
});

export function isPwaDeliveryTaskPreviewEnabled(search, win = (typeof window !== "undefined" ? window : undefined)) {
  if (process.env.NODE_ENV !== "development") return false;
  const raw = String(search || (win?.location?.search || ""));
  const normalized = raw.startsWith("?") ? raw.slice(1) : raw;
  const params = new URLSearchParams(normalized);
  if (params.get(PWA_DELIVERY_TASK_PREVIEW_PARAM) === "1") return true;
  if (!win || !win.location) return false;
  return win.location.hostname === "localhost" && String(win.location.port) === "3003";
}

export function isPwaDeliveryTaskPreviewId(orderId) {
  return PWA_DELIVERY_TASK_PREVIEW_IDS.includes(String(orderId || "").trim());
}

function line(name, qty, lineId, lineIdx, extra = {}) {
  return { name, quantity: qty, line_id: lineId, production_process: "print", ...extra };
}

function shipment({ id, number, date, lines, delivered = false }) {
  const total = lines.reduce((s, ln) => s + (Number(ln.qty) || 0), 0);
  return {
    id,
    number,
    sj_number: number,
    delivery_date: date,
    lines,
    total_qty: total,
    ...(delivered ? { delivered_at: `${date}T16:00:00+07:00`, delivery_status: "Terkirim" } : {}),
  };
}

function loaded(shipmentId, lines) {
  return {
    id: `load-${shipmentId}`,
    surat_jalan_id: shipmentId,
    lines: lines.map((ln) => ({ line_idx: ln.line_idx, qty: ln.qty })),
  };
}

function baseOrder({
  id,
  po,
  customer,
  address,
  date,
  shipments,
  loading,
  proofs = [],
  driverSig = null,
  recipientSig = null,
  delivered = false,
}) {
  const first = shipments[0];
  const allLines = first?.lines || [];
  const lineItems = allLines.map((ln) => line(ln.name, ln.qty, ln.line_id, ln.line_idx));
  const packed = lineItems.map((li, idx) => ({ line_idx: idx, packed_qty: Number(li.quantity) || 0 }));
  const packedTotal = packed.reduce((s, r) => s + r.packed_qty, 0);
  return {
    id,
    preview: true,
    po_number: po,
    customer,
    customer_name: customer,
    address,
    tanggal_pengiriman: date,
    scheduled_delivery_date: date,
    delivery_status: delivered ? "Terkirim" : "Belum Terkirim",
    terkirim_confirmed: delivered,
    eligible_delivery_qty: packedTotal,
    line_items: lineItems,
    packing_info: { qty_packed: packedTotal, lines: packed },
    shipments,
    loading_confirmations: loading,
    delivery_proofs: proofs,
    delivery_confirmations: delivered
      ? [{ id: `conf-${id}`, shipment_id: first.id, qty: packedTotal }]
      : [],
    delivery_driver_signature: driverSig,
    delivery_recipient_signature: recipientSig,
    loading_checklist: driverSig?.driver_name ? { driver_name: driverSig.driver_name } : {},
  };
}

export function getPwaDeliveryTaskPreviewOrders() {
  const sid1 = PWA_DELIVERY_TASK_PREVIEW_SHIPMENT_IDS.d1;
  const sid1b = PWA_DELIVERY_TASK_PREVIEW_SHIPMENT_IDS.d1b;
  const sid2 = PWA_DELIVERY_TASK_PREVIEW_SHIPMENT_IDS.d2;
  const sid3 = PWA_DELIVERY_TASK_PREVIEW_SHIPMENT_IDS.d3;

  const d1LinesA = [{ line_idx: 0, line_id: "l0", name: "Cup 16oz Datar", qty: 2000 }];
  const d1LinesB = [{ line_idx: 0, line_id: "l0", name: "Cup 16oz Datar", qty: 1000 }];
  const d2Lines = [
    { line_idx: 0, line_id: "b0", name: "Cup 22oz Gelas", qty: 1500 },
    { line_idx: 1, line_id: "b1", name: "Lid 22oz Gelas", qty: 1500 },
  ];
  const d3Lines = [
    { line_idx: 0, line_id: "e0", name: "Cup 9oz Datar", qty: 4000 },
    { line_idx: 1, line_id: "e1", name: "Lid 9oz Datar", qty: 4000 },
  ];

  return [
    baseOrder({
      id: PWA_DELIVERY_TASK_PREVIEW_IDS[0],
      po: "PO-PREVIEW-PENGIRIMAN-1",
      customer: "SPECIAL LALAP",
      address: "Jl. Raya Lalap No. 31, Kelurahan Cibiru, Bandung 40614",
      date: "2026-09-15",
      shipments: [
        shipment({ id: sid1, number: "SJ/2026/09/07/000031 1", date: "2026-09-15", lines: d1LinesA }),
        shipment({ id: sid1b, number: "SJ/2026/09/07/000031 2", date: "2026-09-15", lines: d1LinesB }),
      ],
      loading: [loaded(sid1, d1LinesA), loaded(sid1b, d1LinesB)],
    }),
    baseOrder({
      id: PWA_DELIVERY_TASK_PREVIEW_IDS[1],
      po: "PO-PREVIEW-PENGIRIMAN-2",
      customer: "BENTO KOPI KONSEP BARU",
      address: "Jl. Cihampelas No. 14, Bandung Wetan, Kota Bandung 40131",
      date: "2026-09-14",
      shipments: [
        shipment({ id: sid2, number: "SJ/2026/09/14/000003 2", date: "2026-09-14", lines: d2Lines }),
      ],
      loading: [loaded(sid2, d2Lines)],
      proofs: [{ id: "preview-photo-2a", shipment_id: sid2 }],
      driverSig: {
        id: "preview-driver-sig-2",
        signature_storage_path: "preview/driver-sig-2.png",
        driver_signed_at: "2026-09-14T09:40:00+07:00",
        driver_name: "Driver Preview",
      },
    }),
    baseOrder({
      id: PWA_DELIVERY_TASK_PREVIEW_IDS[2],
      po: "PO-PREVIEW-PENGIRIMAN-3",
      customer: "ES TEH KAMPOENG SOLO",
      address: "Jl. Slamet Riyadi No. 20, Kampung Solo, Surakarta 57131",
      date: "2026-09-10",
      shipments: [
        shipment({
          id: sid3,
          number: "SJ/2026/09/10/000020 3",
          date: "2026-09-10",
          lines: d3Lines,
          delivered: true,
        }),
      ],
      loading: [loaded(sid3, d3Lines)],
      proofs: [
        { id: "preview-photo-3a", shipment_id: sid3 },
        { id: "preview-photo-3b", shipment_id: sid3 },
      ],
      driverSig: {
        id: "preview-driver-sig-3",
        signature_storage_path: "preview/driver-sig-3.png",
        driver_signed_at: "2026-09-10T11:00:00+07:00",
        driver_name: "Driver Preview",
      },
      recipientSig: {
        id: "preview-recv-sig-3",
        signature_storage_path: "preview/recv-sig-3.png",
        recipient_signed_at: "2026-09-10T15:10:00+07:00",
        recipient_name: "Penerima Solo",
      },
      delivered: true,
    }),
  ];
}

export function getPwaDeliveryTaskPreviewOrder(orderId, search, win) {
  if (!isPwaDeliveryTaskPreviewEnabled(search, win)) return null;
  if (!isPwaDeliveryTaskPreviewId(orderId)) return null;
  return getPwaDeliveryTaskPreviewOrders().find((o) => o.id === String(orderId).trim()) || null;
}

export function pwaDeliveryTaskPreviewPath(orderId, extra = "") {
  const q = extra ? `&${extra.replace(/^[?&]/, "")}` : "";
  return `/m/orders/${encodeURIComponent(orderId)}?ws=delivery&${PWA_DELIVERY_TASK_PREVIEW_PARAM}=1${q}`;
}

export function applyPreviewDeliveryMutation(order, kind, payload = {}) {
  if (!order?.preview) return order;
  const next = {
    ...order,
    delivery_proofs: [...(order.delivery_proofs || [])],
  };
  if (kind === "photo") {
    next.delivery_proofs = [
      ...next.delivery_proofs,
      {
        id: payload.id || `preview-photo-${Date.now()}`,
        shipment_id: payload.shipmentId || "",
      },
    ];
    return next;
  }
  if (kind === "photo-delete") {
    next.delivery_proofs = next.delivery_proofs.filter((p) => p.id !== payload.proofId);
    return next;
  }
  if (kind === "driver-sig") {
    next.delivery_driver_signature = {
      id: "preview-driver-sig-local",
      signature_storage_path: "preview/local-driver.png",
      driver_signed_at: new Date().toISOString(),
      driver_name: order.loading_checklist?.driver_name || "Driver Preview",
    };
    return next;
  }
  if (kind === "recipient-sig") {
    next.delivery_recipient_signature = {
      id: "preview-recv-sig-local",
      signature_storage_path: "preview/local-recv.png",
      recipient_signed_at: new Date().toISOString(),
      recipient_name: payload.recipientName || "Penerima",
    };
    return next;
  }
  if (kind === "confirm") {
    const sid = String(payload.shipmentId || "");
    next.delivery_status = "Terkirim";
    next.terkirim_confirmed = true;
    next.shipments = (order.shipments || []).map((sh) => (
      String(sh.id) === sid
        ? { ...sh, delivered_at: new Date().toISOString(), delivery_status: "Terkirim" }
        : sh
    ));
    return next;
  }
  return next;
}
