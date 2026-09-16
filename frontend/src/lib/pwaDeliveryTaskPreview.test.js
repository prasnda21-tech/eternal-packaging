import {
  PWA_DELIVERY_TASK_PREVIEW_COUNT,
  PWA_DELIVERY_TASK_PREVIEW_IDS,
  PWA_DELIVERY_TASK_PREVIEW_PARAM,
  PWA_DELIVERY_TASK_PREVIEW_SHIPMENT_IDS,
  applyPreviewDeliveryMutation,
  getPwaDeliveryTaskPreviewOrder,
  getPwaDeliveryTaskPreviewOrders,
  isPwaDeliveryTaskPreviewEnabled,
  pwaDeliveryTaskPreviewPath,
} from "./pwaDeliveryTaskPreview";
import { pengirimanSjDisplayLabel, pengirimanSjOptions } from "./deliveryProof";

describe("PWA delivery task modal preview fixtures", () => {
  const origEnv = process.env.NODE_ENV;

  afterEach(() => {
    process.env.NODE_ENV = origEnv;
  });

  test("requires development plus query flag or localhost:3003", () => {
    process.env.NODE_ENV = "development";
    expect(isPwaDeliveryTaskPreviewEnabled(`?${PWA_DELIVERY_TASK_PREVIEW_PARAM}=1`)).toBe(true);
    expect(isPwaDeliveryTaskPreviewEnabled("?previewPwaDeliveryModal=0")).toBe(false);
    expect(isPwaDeliveryTaskPreviewEnabled("", { location: { hostname: "localhost", port: "3003", search: "" } })).toBe(true);
    expect(isPwaDeliveryTaskPreviewEnabled("", { location: { hostname: "localhost", port: "3002", search: "" } })).toBe(false);
    process.env.NODE_ENV = "production";
    expect(isPwaDeliveryTaskPreviewEnabled(`?${PWA_DELIVERY_TASK_PREVIEW_PARAM}=1`)).toBe(false);
    expect(isPwaDeliveryTaskPreviewEnabled("", { location: { hostname: "localhost", port: "3003", search: "" } })).toBe(false);
  });

  test("exposes exactly 3 dummy cases with hidden internal shipment ids", () => {
    process.env.NODE_ENV = "development";
    expect(PWA_DELIVERY_TASK_PREVIEW_COUNT).toBe(3);
    const orders = getPwaDeliveryTaskPreviewOrders();
    expect(orders).toHaveLength(3);
    expect(orders.map((o) => o.id)).toEqual([...PWA_DELIVERY_TASK_PREVIEW_IDS]);
    expect(orders[0].customer).toBe("SPECIAL LALAP");
    expect(orders[1].customer).toBe("BENTO KOPI KONSEP BARU");
    expect(orders[2].customer).toBe("ES TEH KAMPOENG SOLO");
    const labels = orders.flatMap((o) => pengirimanSjOptions(o).map((opt) => pengirimanSjDisplayLabel(opt)));
    expect(labels).toEqual(expect.arrayContaining([
      "SJ/2026/09/07/000031 1",
      "SJ/2026/09/14/000003 2",
      "SJ/2026/09/10/000020 3",
    ]));
    expect(labels.join(" ")).not.toContain(PWA_DELIVERY_TASK_PREVIEW_SHIPMENT_IDS.d1);
    const d1 = getPwaDeliveryTaskPreviewOrder(
      PWA_DELIVERY_TASK_PREVIEW_IDS[0],
      `?${PWA_DELIVERY_TASK_PREVIEW_PARAM}=1`,
    );
    expect(d1.shipments[0].id).toBe(PWA_DELIVERY_TASK_PREVIEW_SHIPMENT_IDS.d1);
    expect(d1.preview).toBe(true);
    expect(orders[1].delivery_driver_signature?.id).toBeTruthy();
    expect(orders[1].delivery_recipient_signature).toBeFalsy();
    expect(orders[1].delivery_proofs).toHaveLength(1);
    expect(orders[2].delivery_status).toBe("Terkirim");
    expect(pwaDeliveryTaskPreviewPath(PWA_DELIVERY_TASK_PREVIEW_IDS[0])).toContain("ws=delivery");
  });

  test("preview mutations stay in memory and never call a persistence API", () => {
    const [d1] = getPwaDeliveryTaskPreviewOrders();
    const afterPhoto = applyPreviewDeliveryMutation(d1, "photo", { shipmentId: d1.shipments[0].id });
    expect(afterPhoto.delivery_proofs).toHaveLength(1);
    expect(d1.delivery_proofs).toHaveLength(0);
    const afterRecv = applyPreviewDeliveryMutation(d1, "recipient-sig", { recipientName: "Budi Penerima" });
    expect(afterRecv.delivery_recipient_signature.recipient_name).toBe("Budi Penerima");
    const emptyRecv = applyPreviewDeliveryMutation(d1, "recipient-sig", { recipientName: "" });
    expect(emptyRecv.delivery_recipient_signature.recipient_name).toBe("");
    const namedThenConfirm = applyPreviewDeliveryMutation(afterRecv, "confirm", {
      shipmentId: d1.shipments[0].id,
      recipientName: "me time",
    });
    expect(namedThenConfirm.delivery_recipient_signature.recipient_name).toBe("me time");
    expect(namedThenConfirm.delivery_status).toBe("Terkirim");
    const live = { id: "real-order", preview: false, delivery_proofs: [] };
    expect(applyPreviewDeliveryMutation(live, "photo", { shipmentId: "x" })).toBe(live);
  });
});
