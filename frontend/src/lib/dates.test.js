import { formatDate, formatDisplayDate, formatDisplayDateTime } from "./constants";

describe("display date formatter dd/mm/yy", () => {
  test("2026-08-26 → 26/08/26", () => {
    expect(formatDisplayDate("2026-08-26")).toBe("26/08/26");
    expect(formatDate("2026-08-26")).toBe("26/08/26");
  });

  test("ISO datetime keeps hours as dd/mm/yy HH:mm", () => {
    const out = formatDisplayDateTime("2026-08-26T10:35:00+07:00");
    expect(out).toMatch(/^26\/08\/26 \d{2}:\d{2}$/);
  });

  test("date-only is not shifted by timezone", () => {
    expect(formatDisplayDate("2026-08-26")).toBe("26/08/26");
    expect(formatDisplayDateTime("2026-08-26")).toBe("26/08/26");
  });

  test("empty is dash", () => {
    expect(formatDisplayDate(null)).toBe("-");
    expect(formatDisplayDateTime("")).toBe("-");
  });

  test("sorting stays chronological on ISO values", () => {
    const rows = ["2026-08-26", "2026-01-02", "2025-12-31"];
    const sorted = [...rows].sort();
    expect(sorted).toEqual(["2025-12-31", "2026-01-02", "2026-08-26"]);
    expect(sorted.map(formatDisplayDate)).toEqual(["31/12/25", "02/01/26", "26/08/26"]);
  });
});
