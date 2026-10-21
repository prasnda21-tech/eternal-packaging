import { addWibCalendarDays, getWibTodayPlusDays, getWibTodayString } from "./wibCalendarDate";

describe("wibCalendarDate", () => {
  test("getWibTodayString uses Asia/Jakarta calendar day near UTC midnight boundary", () => {
    // 2026-10-21 17:30 UTC = 2026-10-22 00:30 WIB
    const justAfterWibMidnight = new Date("2026-10-21T17:30:00.000Z");
    expect(getWibTodayString(justAfterWibMidnight)).toBe("2026-10-22");
    // UTC date prefix would wrongly still be 2026-10-21
    expect(justAfterWibMidnight.toISOString().slice(0, 10)).toBe("2026-10-21");
  });

  test("getWibTodayString stays on the same WIB day before Jakarta midnight", () => {
    // 2026-10-21 16:59 UTC = 2026-10-21 23:59 WIB
    const beforeWibMidnight = new Date("2026-10-21T16:59:00.000Z");
    expect(getWibTodayString(beforeWibMidnight)).toBe("2026-10-21");
  });

  test("addWibCalendarDays adds whole calendar days without UTC day shift", () => {
    expect(addWibCalendarDays("2026-10-21", 3)).toBe("2026-10-24");
    expect(addWibCalendarDays("2026-01-30", 2)).toBe("2026-02-01");
  });

  test("getWibTodayPlusDays matches today WIB + N", () => {
    const now = new Date("2026-10-21T17:30:00.000Z");
    expect(getWibTodayPlusDays(3, now)).toBe("2026-10-25");
  });
});
