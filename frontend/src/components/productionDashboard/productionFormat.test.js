import { formatProductionUpdatedAt, productionUpdatedLabel } from "./productionFormat";

test("WIB offset stays on the wall clock already stored", () => {
  expect(formatProductionUpdatedAt("2026-10-23T08:00:00+07:00")).toBe("23 Okt 2026, 08.00 WIB");
  expect(productionUpdatedLabel("2026-10-23T08:00:00+07:00")).toBe("Diperbarui 23 Okt 2026, 08.00 WIB");
});

test("UTC and Z timestamps convert once to Asia/Jakarta", () => {
  expect(formatProductionUpdatedAt("2026-10-23T01:00:00+00:00")).toBe("23 Okt 2026, 08.00 WIB");
  expect(formatProductionUpdatedAt("2026-10-22T17:30:00Z")).toBe("23 Okt 2026, 00.30 WIB");
  expect(formatProductionUpdatedAt("2026-10-23T01:00:00")).toBe("23 Okt 2026, 08.00 WIB");
});

test("empty and invalid timestamps stay honest", () => {
  expect(formatProductionUpdatedAt("")).toBe("");
  expect(formatProductionUpdatedAt(null)).toBe("");
  expect(formatProductionUpdatedAt("bukan-tanggal")).toBe("");
  expect(productionUpdatedLabel("")).toBe("Waktu pembaruan tidak tersedia");
  expect(productionUpdatedLabel("bukan-tanggal")).toBe("Waktu pembaruan tidak tersedia");
  expect(productionUpdatedLabel("bukan-tanggal")).not.toContain("bukan-tanggal");
});
