import {
  addSalesInvoiceCalendarDays,
  formatSalesInvoiceDate,
  parseSalesInvoiceDate,
  salesInvoiceDateToIso,
} from "./salesInvoiceDate";

test("identifies invoice date formats without swapping day and month", () => {
  expect(formatSalesInvoiceDate("2026-10-09")).toBe("09/10/2026");
  expect(formatSalesInvoiceDate("2026/10/09")).toBe("09/10/2026");
  expect(formatSalesInvoiceDate("09/10/2026")).toBe("09/10/2026");
  expect(formatSalesInvoiceDate("09-10-2026")).toBe("09/10/2026");
  expect(salesInvoiceDateToIso("09/10/2026")).toBe("2026-10-09");
  expect(salesInvoiceDateToIso("2026/10/09")).toBe("2026-10-09");
  expect(salesInvoiceDateToIso("2026-10-09T17:00:00-07:00")).toBe("2026-10-09");
  expect(parseSalesInvoiceDate("2026-10-09 anything")).toBeNull();
  expect(parseSalesInvoiceDate("2026-10-09 garbage")).toBeNull();
});

function localMidnightIso(iso, term, offsetMinutes) {
  const [year, month, day] = iso.split("-").map(Number);
  const localMidnight = Date.UTC(year, month - 1, day) - offsetMinutes * 60 * 1000;
  const shifted = new Date(localMidnight);
  shifted.setUTCDate(shifted.getUTCDate() + term);
  return shifted.toISOString().slice(0, 10);
}

test("due date preview stays on the same calendar day across timezones", () => {
  const zones = {
    UTC: 0,
    "Asia/Jakarta": 7 * 60,
    "America/Los_Angeles": -7 * 60,
    "Etc/GMT+12": -12 * 60,
  };
  const cases = [
    ["2026-10-09", 0, "2026-10-09", "09/10/2026"],
    ["2026-10-09", 14, "2026-10-23", "23/10/2026"],
    ["2026-01-31", 1, "2026-02-01", "01/02/2026"],
    ["2026-12-31", 1, "2027-01-01", "01/01/2027"],
    ["2024-02-28", 1, "2024-02-29", "29/02/2024"],
    ["2024-02-29", 1, "2024-03-01", "01/03/2024"],
    ["2023-02-28", 1, "2023-03-01", "01/03/2023"],
    ["1900-02-28", 1, "1900-03-01", "01/03/1900"],
  ];
  for (const [iso, term, expectedIso, expectedDisplay] of cases) {
    const preview = addSalesInvoiceCalendarDays(iso, term);
    expect(preview).toBe(expectedIso);
    expect(formatSalesInvoiceDate(preview)).toBe(expectedDisplay);
    for (const offset of Object.values(zones)) {
      expect(addSalesInvoiceCalendarDays(iso, term)).toBe(expectedIso);
      if (offset > 0 && term === 0) {
        expect(localMidnightIso(iso, term, offset)).not.toBe(expectedIso);
      }
    }
  }
  expect(addSalesInvoiceCalendarDays("", 7)).toBe("");
  expect(addSalesInvoiceCalendarDays("31/02/2026", 7)).toBe("");
});

test("rejects impossible dates and two-digit years", () => {
  expect(parseSalesInvoiceDate("29/02/2024")).toEqual({ year: 2024, month: 2, day: 29 });
  expect(parseSalesInvoiceDate("29/02/2000")).toEqual({ year: 2000, month: 2, day: 29 });
  for (const raw of ["31/02/2026", "29/02/2023", "29/02/1900", "09/10/26", "2026/13/01"]) {
    expect(parseSalesInvoiceDate(raw)).toBeNull();
    expect(salesInvoiceDateToIso(raw)).toBe("");
  }
});
