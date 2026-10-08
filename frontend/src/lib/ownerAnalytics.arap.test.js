import {
  agingBarPercents,
  mapOwnerArapCard,
  OWNER_AGING_LABELS,
} from "./ownerAnalytics";

test("mapOwnerArapCard maps aging and overdue fields", () => {
  const mapped = mapOwnerArapCard({
    status: "complete",
    amount: 1000,
    overdue_amount: 400,
    not_due_amount: 600,
    aging: { not_due: 600, d1_30: 100, d31_60: 100, d61_90: 100, over_90: 100 },
    completeness: "complete",
    scope_label: "Saat Ini",
    label: "Piutang Usaha",
  });
  expect(mapped.amount).toBe(1000);
  expect(mapped.overdue_amount).toBe(400);
  expect(mapped.aging.d1_30).toBe(100);
  expect(mapped.scope_label).toBe("Saat Ini");
});

test("mapOwnerArapCard does not invent amounts on error", () => {
  const mapped = mapOwnerArapCard({ status: "error", amount: null, detail: "boom" });
  expect(mapped.amount).toBeNull();
  expect(mapped.aging).toBeNull();
  expect(mapped.detail).toBe("boom");
});

test("agingBarPercents keeps five categories and proportional bars", () => {
  expect(OWNER_AGING_LABELS).toHaveLength(5);
  const bars = agingBarPercents({
    not_due: 50,
    d1_30: 25,
    d31_60: 25,
    d61_90: 0,
    over_90: 0,
  });
  expect(bars).toHaveLength(5);
  expect(bars[0].label).toBe("Belum Jatuh Tempo");
  expect(bars[0].pct).toBe(50);
  expect(bars[1].pct).toBe(25);
  expect(bars[3].pct).toBe(0);
});

test("agingBarPercents zero state stays at zero width", () => {
  const bars = agingBarPercents({
    not_due: 0,
    d1_30: 0,
    d31_60: 0,
    d61_90: 0,
    over_90: 0,
  });
  expect(bars.every((row) => row.pct === 0)).toBe(true);
});
