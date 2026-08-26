import { deliveryHistoryEntries, displayOrDash, historyTimeLabel, terkirimQtySummary } from "./deliveryHistory";

describe("delivery history helpers", () => {
  test("uses append-only delivery_history rows", () => {
    const rows = deliveryHistoryEntries({
      delivery_history: [
        { driver_name: "Budi", item: "Item A", qty: 2000, time: "2026-08-27T14:30:00+07:00", vehicle_plate: "B 1234 XYZ", proof_ids: ["p1", "p2"] },
        { driver_name: "Andi", item: "Item A", qty: 1000, time: "2026-08-28T09:15:00+07:00", vehicle_plate: "B 5678 ABC", proof_ids: ["p3", "p4"] },
      ],
    });
    expect(rows).toHaveLength(2);
    expect(rows[0].driver_name).toBe("Budi");
    expect(rows[0].qty).toBe(2000);
    expect(rows[1].driver_name).toBe("Andi");
    expect(rows[1].qty).toBe(1000);
  });

  test("missing historical fields become dash", () => {
    expect(displayOrDash(null)).toBe("—");
    expect(displayOrDash("")).toBe("—");
    expect(displayOrDash("Budi")).toBe("Budi");
  });

  test("time format is dd/mm/yy HH:mm from evidence timestamp", () => {
    expect(historyTimeLabel("2026-08-27T14:30:00+07:00")).toBe("27/08/26 14:30");
  });

  test("terkirim summary remaining is max(total-delivered, 0)", () => {
    expect(terkirimQtySummary({ packing_required_total: 10000, qty_delivered: 6000 })).toEqual({
      total: 10000, delivered: 6000, remaining: 4000,
    });
    expect(terkirimQtySummary({ packing_required_total: 8000, qty_delivered: 6000 })).toEqual({
      total: 8000, delivered: 6000, remaining: 2000,
    });
    expect(terkirimQtySummary({ packing_required_total: 5000, qty_delivered: 7000 })).toEqual({
      total: 5000, delivered: 7000, remaining: 0,
    });
  });
});
