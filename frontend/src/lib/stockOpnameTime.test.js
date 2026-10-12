import {
  STOCK_OPNAME_TZ_LABEL,
  formatWibDateTime,
  isoToWibLocalInput,
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

  test("format and timezone label are explicit", () => {
    expect(STOCK_OPNAME_TZ_LABEL).toMatch(/WIB/);
    expect(formatWibDateTime("2026-10-12T03:00:00.000Z")).toBe("12/10/2026 10:00 WIB");
    expect(formatWibDateTime("")).toBe("—");
  });
});
