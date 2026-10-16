import {
  canOfferPurchaseReceive,
  mergePurchaseOrderIntoList,
} from "./purchaseUi";

describe("PO receive UI refresh helpers", () => {
  test("mergePurchaseOrderIntoList replaces the same id with API snapshot", () => {
    const stale = {
      id: "po-1",
      po_number: "PB-1",
      receiving_status: "BELUM DITERIMA",
      receiving_progress: { ordered_qty: 20, received_qty: 0, remaining_qty: 20 },
      lines: [{ cumulative_received_qty: 0, remaining_receivable_qty: 20 }],
    };
    const fresh = {
      id: "po-1",
      po_number: "PB-1",
      receiving_status: "DITERIMA",
      receiving_progress: { ordered_qty: 20, received_qty: 20, remaining_qty: 0 },
      lines: [{ cumulative_received_qty: 20, remaining_receivable_qty: 0 }],
    };
    const other = { id: "po-2", po_number: "PB-2" };
    const merged = mergePurchaseOrderIntoList([stale, other], fresh);
    expect(merged).toHaveLength(2);
    expect(merged[0]).toEqual(fresh);
    expect(merged[0].receiving_status).toBe("DITERIMA");
    expect(merged[0].receiving_progress.received_qty).toBe(20);
    expect(merged[0].receiving_progress.remaining_qty).toBe(0);
    expect(merged[1]).toEqual(other);
  });

  test("canOfferPurchaseReceive is false when remaining is 0", () => {
    expect(canOfferPurchaseReceive({
      receiving_progress: { remaining_qty: 0 },
    })).toBe(false);
    expect(canOfferPurchaseReceive({
      receiving_progress: { remaining_qty: 20 },
    })).toBe(true);
    expect(canOfferPurchaseReceive(null)).toBe(false);
  });
});
