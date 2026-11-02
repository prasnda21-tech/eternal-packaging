import {
  STOCK_OPNAME_TZ_LABEL,
  combineWibDateAndTime,
  formatWibDateTime,
  formatWibPeriod,
  isoToWibLocalInput,
  splitWibLocalInput,
  wibLocalInputToIso,
} from "./stockOpnameTime";

describe("stockOpnameTime WIB conversion", () => {
  test("isoToWibLocalInput shifts UTC to WIB wall clock", () => {
    // 2026-10-12T03:00:00Z → 10:00 WIB
    expect(isoToWibLocalInput("2026-10-12T03:00:00.000Z")).toBe("2026-10-12T10:00");
    expect(isoToWibLocalInput("")).toBe("");
    expect(isoToWibLocalInput(null)).toBe("");
  });

  test("wibLocalInputToIso converts WIB local back to UTC ISO", () => {
    const iso = wibLocalInputToIso("2026-10-12T10:00");
    expect(iso).toBe("2026-10-12T03:00:00.000Z");
    expect(wibLocalInputToIso("")).toBeNull();
    expect(wibLocalInputToIso("not-a-date")).toBeNull();
  });

  test("round-trip preserves the WIB minute", () => {
    const local = "2026-01-15T14:30";
    expect(isoToWibLocalInput(wibLocalInputToIso(local))).toBe(local);
  });

  test("format and timezone label are explicit DD/MM/YYYY 24h", () => {
    expect(STOCK_OPNAME_TZ_LABEL).toMatch(/WIB/);
    expect(formatWibDateTime("2026-10-12T03:00:00.000Z")).toBe("12/10/2026 10:00 WIB");
    expect(formatWibDateTime("")).toBe("—");
  });

  test("combine/split date+time and period formatting", () => {
    expect(combineWibDateAndTime("2026-10-30", "18:00")).toBe("2026-10-30T11:00:00.000Z");
    expect(combineWibDateAndTime("2026-10-30", "")).toBeNull();
    expect(splitWibLocalInput("2026-10-30T18:00")).toEqual({ date: "2026-10-30", time: "18:00" });
    expect(formatWibPeriod("2026-10-30T01:00:00.000Z", "2026-10-30T11:00:00.000Z")).toBe(
      "30/10/2026 08:00 WIB – 30/10/2026 18:00 WIB",
    );
    // Legacy: no invented start
    expect(formatWibPeriod(null, "2026-10-30T11:00:00.000Z")).toBe("s/d 30/10/2026 18:00 WIB");
    expect(formatWibPeriod(null, null)).toBe("—");
  });
});
