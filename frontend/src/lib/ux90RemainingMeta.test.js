import {
  deliveryBelumSjMeta,
  formatRemainingByUnit,
  invoiceBelumDifakturMeta,
  receiptBelumDibayarMeta,
} from "./ux90RemainingMeta";

describe("ux90RemainingMeta", () => {
  test("formatRemainingByUnit single and multi", () => {
    expect(formatRemainingByUnit([{ unit: "pcs", qty: 150 }])).toBe("150 pcs");
    expect(
      formatRemainingByUnit([
        { unit: "pcs", qty: 150 },
        { unit: "dus", qty: 2 },
      ]),
    ).toBe("150 pcs · 2 dus");
    expect(formatRemainingByUnit([], 2000)).toBe("2.000 pcs");
  });

  test("invoice meta never uses delivery_summary or total_quantity", () => {
    expect(
      invoiceBelumDifakturMeta({
        total_quantity: 9999,
        delivery_summary: { total_remaining: 1 },
        invoiceable_summary: {
          available: true,
          total_remaining_invoice_qty: 80,
          remaining_by_unit: [{ unit: "pcs", qty: 80 }],
        },
      }),
    ).toBe("Belum difaktur: 80 pcs");
    expect(
      invoiceBelumDifakturMeta({
        total_quantity: 9999,
        delivery_summary: { total_remaining: 1 },
      }),
    ).toBe("Belum difaktur: belum tersedia");
  });

  test("delivery meta never falls back to total_quantity", () => {
    expect(
      deliveryBelumSjMeta({
        total_quantity: 9999,
        delivery_summary: {
          available: true,
          total_remaining: 10,
          remaining_by_unit: [{ unit: "pcs", qty: 10 }],
        },
      }),
    ).toBe("Belum dibuat SJ: 10 pcs");
    expect(deliveryBelumSjMeta({ total_quantity: 9999 })).toBe(
      "Belum dibuat SJ: belum tersedia",
    );
  });

  test("receipt meta treats zero as zero and skips grand_total fallback", () => {
    const rp = (n) => `Rp${Number(n).toLocaleString("id-ID")}`;
    expect(receiptBelumDibayarMeta({ outstanding_amount: 0, grand_total: 5000 }, rp)).toBe(
      "Belum dibayar: Rp0",
    );
    expect(receiptBelumDibayarMeta({ outstanding_amount: 12000, grand_total: 50000 }, rp)).toBe(
      "Belum dibayar: Rp12.000",
    );
    expect(receiptBelumDibayarMeta({ grand_total: 50000 }, rp)).toBe(
      "Belum dibayar: belum tersedia",
    );
  });
});
