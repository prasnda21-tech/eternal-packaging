import {
  CONFIDENCE_FILTERS,
  EMPTY_PREDIKSI_FILTERS,
  FOLLOWUP_STATUSES,
  PATTERN_FILTERS,
  dashGap,
  dashOmzet,
  dashValue,
  formatStat,
  kpiFilterForCard,
  statusTone,
} from "./orderPrediction";

describe("Prediksi Order display", () => {
  test("omzet null and empty stay dash never zero", () => {
    expect(dashOmzet(null)).toBe("-");
    expect(dashOmzet(undefined)).toBe("-");
    expect(dashOmzet("")).toBe("-");
    expect(dashOmzet(0)).toBe("-");
  });

  test("first order gap is dash", () => {
    expect(dashGap(null)).toBe("-");
    expect(dashGap(9)).toBe(9);
  });

  test("filters cover status pattern confidence", () => {
    expect(FOLLOWUP_STATUSES.map((s) => s.value)).toEqual([
      "", "waktu_follow_up", "jatuh_tempo", "terlambat", "risiko", "tidak_aktif", "data_belum_cukup",
    ]);
    expect(PATTERN_FILTERS.some((p) => p.label === "Mingguan")).toBe(true);
    expect(CONFIDENCE_FILTERS.map((c) => c.value)).toEqual(["", "tinggi", "sedang", "rendah"]);
    expect(EMPTY_PREDIKSI_FILTERS.status).toBe("");
  });

  test("kpi cards map to status filters", () => {
    expect(kpiFilterForCard("waktu_follow_up")).toBe("waktu_follow_up");
    expect(kpiFilterForCard("terlambat_risiko")).toBe("terlambat_risiko");
    expect(kpiFilterForCard("total")).toBe("");
  });

  test("status tone and stats", () => {
    expect(statusTone("RISIKO")).toBe("danger");
    expect(formatStat(null)).toBe("-");
    expect(formatStat(9)).toBe("9");
    expect(dashValue(null)).toBe("-");
  });
});
