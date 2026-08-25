import { clampPercent, formatShipDate, terkirimTone } from "./shipmentStatus";

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
});
