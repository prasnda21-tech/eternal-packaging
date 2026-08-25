import { clampPercent, formatShipDate, matchesShipFilters, terkirimTone } from "./shipmentStatus";

describe("Status Pengiriman display", () => {
  test("50% terkirim is mid/yellow band", () => {
    expect(clampPercent(50)).toBe(50);
    expect(terkirimTone(50)).toBe("mid");
  });

  test("75% ready uses clamp", () => {
    expect(clampPercent(75)).toBe(75);
  });

  test("0% stays empty", () => {
    expect(clampPercent(0)).toBe(0);
    expect(terkirimTone(0)).toBe("empty");
  });

  test("over 100 is capped visually", () => {
    expect(clampPercent(120)).toBe(100);
    expect(terkirimTone(120)).toBe("full");
  });

  test("missing date is dash", () => {
    expect(formatShipDate(null)).toBe("-");
    expect(formatShipDate("")).toBe("-");
  });

  test("order date displays dd/mm/yy", () => {
    expect(formatShipDate("2026-08-26")).toBe("26/08/26");
  });
});

describe("Status Pengiriman header filters", () => {
  const row = {
    customer_name: "Kedai Kopi Senja",
    order_number: "PO-7788",
    order_date: "2026-08-20",
    delivery_date: "2026-08-26",
  };

  test("customer partial case-insensitive", () => {
    expect(matchesShipFilters(row, { customer: "kopi" })).toBe(true);
    expect(matchesShipFilters(row, { customer: "XYZ" })).toBe(false);
  });

  test("no PO partial", () => {
    expect(matchesShipFilters(row, { po: "778" })).toBe(true);
    expect(matchesShipFilters(row, { po: "999" })).toBe(false);
  });

  test("order date and delivery date combine", () => {
    expect(matchesShipFilters(row, { customer: "senja", po: "PO-77", orderDate: "2026-08-20", deliveryDate: "2026-08-26" })).toBe(true);
    expect(matchesShipFilters(row, { orderDate: "2026-08-21" })).toBe(false);
    expect(matchesShipFilters(row, { deliveryDate: "2026-08-25" })).toBe(false);
  });
});
