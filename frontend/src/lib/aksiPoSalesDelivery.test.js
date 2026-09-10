import { aksiPoSalesDeliveryState } from "./aksiPoSalesDelivery";

describe("aksiPoSalesDeliveryState — Aksi PO SJ completion", () => {
  test("remain 0 + active SJ covers full qty → all-qty completion shown", () => {
    const s = aksiPoSalesDeliveryState({
      total_quantity: 1000,
      surat_jalan_created: true,
      sales_delivery_summary: {
        total_ordered: 1000,
        total_shipped: 1000,
        total_remaining: 0,
        can_create_delivery: false,
      },
    });
    expect(s.showAllQtyDone).toBe(true);
    expect(s.showProcess).toBe(false);
  });

  test("last active SJ deleted + remain restored → completion gone, process returns", () => {
    const s = aksiPoSalesDeliveryState({
      total_quantity: 1000,
      surat_jalan_created: false,
      sales_delivery_summary: {
        total_ordered: 1000,
        total_shipped: 0,
        total_remaining: 1000,
        can_create_delivery: true,
      },
    });
    expect(s.showAllQtyDone).toBe(false);
    expect(s.showProcess).toBe(true);
    expect(s.remaining).toBe(1000);
  });

  test("partial active SJ + remain > 0 → process available", () => {
    const s = aksiPoSalesDeliveryState({
      total_quantity: 10000,
      sales_delivery_summary: {
        total_ordered: 10000,
        total_shipped: 7000,
        total_remaining: 3000,
        can_create_delivery: true,
      },
    });
    expect(s.showAllQtyDone).toBe(false);
    expect(s.showProcess).toBe(true);
  });

  test("multiple active SJ covering all qty → completion legitimate", () => {
    const s = aksiPoSalesDeliveryState({
      total_quantity: 10000,
      sales_delivery_summary: {
        total_ordered: 10000,
        total_shipped: 10000,
        total_remaining: 0,
        can_create_delivery: false,
      },
    });
    expect(s.showAllQtyDone).toBe(true);
    expect(s.showProcess).toBe(false);
  });

  test("delete one partial SJ causing remain > 0 → completion removed", () => {
    const before = aksiPoSalesDeliveryState({
      total_quantity: 10000,
      sales_delivery_summary: {
        total_ordered: 10000,
        total_shipped: 10000,
        total_remaining: 0,
        can_create_delivery: false,
      },
    });
    expect(before.showAllQtyDone).toBe(true);

    const after = aksiPoSalesDeliveryState({
      total_quantity: 10000,
      surat_jalan_created: true,
      sales_delivery_summary: {
        total_ordered: 10000,
        total_shipped: 4000,
        total_remaining: 6000,
        can_create_delivery: true,
      },
    });
    expect(after.showAllQtyDone).toBe(false);
    expect(after.showProcess).toBe(true);
    expect(after.remaining).toBe(6000);
  });

  test("deleted/history-only SJ (remain restored, no active allocation) does not count as complete", () => {
    const s = aksiPoSalesDeliveryState({
      total_quantity: 1000,
      surat_jalan_created: false,
      sj_files: [{ id: "old", is_deleted: true, has_file: false }],
      sales_delivery_summary: {
        total_ordered: 1000,
        total_shipped: 0,
        total_remaining: 1000,
        can_create_delivery: true,
      },
    });
    expect(s.showAllQtyDone).toBe(false);
    expect(s.showProcess).toBe(true);
  });

  test("canonical can_create_delivery false with remain 0 drives completion", () => {
    const s = aksiPoSalesDeliveryState({
      total_quantity: 500,
      sales_delivery_summary: {
        total_ordered: 500,
        total_shipped: 500,
        total_remaining: 0,
        can_create_delivery: false,
      },
    });
    expect(s.showProcess).toBe(false);
    expect(s.showAllQtyDone).toBe(true);
  });

  test("permission denied does not invent process button", () => {
    const s = aksiPoSalesDeliveryState({
      total_quantity: 1000,
      sales_delivery_summary: {
        total_ordered: 1000,
        total_shipped: 0,
        total_remaining: 1000,
        can_create_delivery: true,
      },
    }, { canCreateDelivery: false });
    expect(s.showProcess).toBe(false);
    expect(s.showAllQtyDone).toBe(false);
  });
});
