import { patchScheduleRowsForDeliveredShipment } from "./deliveryScheduleRowPatch";

function rows() {
  return [
    {
      id: "po",
      order_number: "PO-1",
      shipment_id: "sh-1",
      latest_sj_number: "SJ-1",
      qty_delivered_sj: 0,
      qty_sj: 400,
      packing_status: "Berjalan",
      delivery_status: "Belum Terkirim",
    },
    {
      id: "po",
      order_number: "PO-1",
      shipment_id: "sh-2",
      latest_sj_number: "SJ-2",
      qty_delivered_sj: 0,
      qty_sj: 400,
      packing_status: "Berjalan",
      delivery_status: "Belum Terkirim",
    },
  ];
}

function order(extra = {}) {
  return {
    id: "po",
    po_number: "PO-1",
    delivery_status: "Terkirim",
    qty_delivered: 800,
    shipments: [
      { id: "sh-1", number: "SJ-1", lines: [{ qty: 400 }], ...extra.sh1 },
      { id: "sh-2", number: "SJ-2", lines: [{ qty: 400 }], ...extra.sh2 },
    ],
  };
}

test("one delivered SJ paints only that row and leaves the sibling and other stages", () => {
  const next = patchScheduleRowsForDeliveredShipment(
    rows(),
    order({ sh1: { delivery_status: "Terkirim", delivered_at: "t" } }),
    "sh-1",
  );
  expect(next[0].qty_delivered_sj).toBe(400);
  expect(next[0].packing_status).toBe("Berjalan");
  expect(next[0].delivery_status).toBe("Belum Terkirim");
  expect(next[1]).toEqual(rows()[1]);
});

test("a shipment that is not delivered does not change any row", () => {
  const current = rows();
  expect(patchScheduleRowsForDeliveredShipment(current, order(), "sh-1")).toBe(current);
});

test("SJ number matches only a row that has no shipment id", () => {
  const current = rows();
  current[0] = { ...current[0], shipment_id: "" };
  const next = patchScheduleRowsForDeliveredShipment(
    current,
    order({ sh1: { delivery_status: "Terkirim" } }),
    "sh-1",
  );
  expect(next[0].qty_delivered_sj).toBe(400);
  expect(next[1].qty_delivered_sj).toBe(0);
});
