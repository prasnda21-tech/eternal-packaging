import { deliveredPercentLabel, effectivePoStatus, STATUS_PO_COLUMNS } from "./statusPo";

describe("Status PO", () => {
  test("columns exact", () => {
    expect(STATUS_PO_COLUMNS).toEqual(["No PO", "Tanggal", "Pelanggan", "Status", "Status Terkirim"]);
  });

  test("partial delivery 50% remains 50%", () => {
    expect(deliveredPercentLabel(50)).toBe("50%");
    expect(deliveredPercentLabel(50.4)).toBe("50%");
  });

  test("status uses backend derived field when present", () => {
    expect(effectivePoStatus({ status: "Parsial Terkirim", current_stage: 14 })).toBe("Parsial Terkirim");
  });

  test("does not treat stage index alone as fully delivered", () => {
    const row = {
      current_stage: 14,
      terkirim_confirmed: false,
      delivery_status: "Sebagian Terkirim",
      qty_delivered: 5000,
      packing_required_total: 10000,
      status: "Parsial Terkirim",
    };
    expect(effectivePoStatus(row)).toBe("Parsial Terkirim");
  });
});
