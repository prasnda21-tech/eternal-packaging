import { legacyEstimateNotice } from "./legacyEstimateNotice";

test("shows notice only when legacy working estimate shifted", () => {
  const notice = legacyEstimateNotice({
    contract: "legacy_working_days",
    estimate_adjusted: true,
    estimate_from: "2026-10-25",
    estimate_to: "2026-10-26",
    date: "2026-10-26",
    countable: false,
  });
  expect(notice).toEqual({
    text: "Estimasi menyesuaikan hari kerja: 25 Okt → 26 Okt",
    title: expect.stringContaining("Bukan tanggal manual"),
  });
});

test("hides notice for same date, manual, urgent, and sla_v2", () => {
  expect(legacyEstimateNotice({
    contract: "legacy_working_days",
    estimate_adjusted: false,
    legacy_calendar_date: "2026-10-24",
    date: "2026-10-24",
  })).toBeNull();
  expect(legacyEstimateNotice({
    contract: "manual",
    date: "2026-11-02",
    estimate_adjusted: true,
    estimate_from: "2026-10-25",
    estimate_to: "2026-10-26",
  })).toBeNull();
  expect(legacyEstimateNotice({
    contract: "urgent",
    date: "2026-10-20",
  })).toBeNull();
  expect(legacyEstimateNotice({
    contract: "sla_v2",
    date: "2026-10-27",
    source: "otomatis",
  })).toBeNull();
  expect(legacyEstimateNotice(null)).toBeNull();
});
