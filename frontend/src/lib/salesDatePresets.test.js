import {
  invoiceDatePresetRange,
  shippingDatePresetBounds,
  wibTodayKey,
  wibWeekBounds,
} from "./salesDatePresets";

describe("salesDatePresets", () => {
  test("wibTodayKey is independent of host local offset shape", () => {
    // 2026-10-06 17:00 UTC = 2026-10-07 00:00 WIB
    expect(wibTodayKey(new Date("2026-10-06T17:00:00.000Z"))).toBe("2026-10-07");
  });

  test("week bounds are Monday–Sunday", () => {
    // 2026-10-07 is Wednesday
    expect(wibWeekBounds("2026-10-07")).toEqual({ from: "2026-10-05", to: "2026-10-11" });
  });

  test("shipping presets inclusive bounds", () => {
    const today = "2026-10-06";
    expect(shippingDatePresetBounds("today", today)).toEqual({
      kind: "range", from: "2026-10-06", to: "2026-10-06",
    });
    expect(shippingDatePresetBounds("tomorrow", today)).toEqual({
      kind: "range", from: "2026-10-07", to: "2026-10-07",
    });
    expect(shippingDatePresetBounds("next_3", today)).toEqual({
      kind: "range", from: "2026-10-06", to: "2026-10-08",
    });
    expect(shippingDatePresetBounds("next_7", today)).toEqual({
      kind: "range", from: "2026-10-06", to: "2026-10-12",
    });
    expect(shippingDatePresetBounds("overdue", today)).toEqual({
      kind: "overdue", before: "2026-10-06",
    });
    expect(shippingDatePresetBounds("unset", today)).toEqual({ kind: "unset" });
  });

  test("invoice presets map to from/to", () => {
    expect(invoiceDatePresetRange("today", "2026-10-06")).toEqual({
      date_from: "2026-10-06", date_to: "2026-10-06",
    });
    expect(invoiceDatePresetRange("this_month", "2026-10-06")).toEqual({
      date_from: "2026-10-01", date_to: "2026-10-31",
    });
    expect(invoiceDatePresetRange("last_month", "2026-10-06")).toEqual({
      date_from: "2026-09-01", date_to: "2026-09-30",
    });
    expect(invoiceDatePresetRange("all", "2026-10-06")).toEqual({
      date_from: "", date_to: "",
    });
  });
});
