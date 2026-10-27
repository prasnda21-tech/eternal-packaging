import { buildTransactionUomOptions, defaultTransactionUom, normalizeUom } from "./transactionUomOptions";

describe("transactionUomOptions", () => {
  test("normalizeUom uppercases and defaults PCS", () => {
    expect(normalizeUom("crt")).toBe("CRT");
    expect(normalizeUom("")).toBe("PCS");
  });

  test("builds PCS base plus CRT alternate", () => {
    const opts = buildTransactionUomOptions(
      {
        base_uom: "PCS",
        alternate_uoms: [{ uom: "CRT", factor_to_base: 500 }],
      },
      { unit: "PCS" },
    );
    expect(opts.map((r) => r.uom)).toEqual(["PCS", "CRT"]);
    expect(opts[1].factor_to_base).toBe(500);
    expect(defaultTransactionUom(opts, "CRT")).toBe("CRT");
    expect(defaultTransactionUom(opts, "BOX")).toBe("PCS");
  });

  test("legacy transaction_uom pair without alternate_uoms", () => {
    const opts = buildTransactionUomOptions(
      { base_uom: "PCS", transaction_uom: "PACK", conversion_factor: 50 },
      {},
    );
    expect(opts.map((r) => r.uom)).toEqual(["PCS", "PACK"]);
  });

  test("dedupes alternate equal to base", () => {
    const opts = buildTransactionUomOptions(
      {
        base_uom: "PCS",
        alternate_uoms: [{ uom: "pcs", factor_to_base: 1 }, { uom: "BOX", factor_to_base: 12 }],
      },
      {},
    );
    expect(opts.map((r) => r.uom)).toEqual(["PCS", "BOX"]);
  });
});
