import fs from "fs";
import path from "path";
import {
  PRODUCTION_SCHEDULE_SCOPE_DEFAULT,
  normalizeProductionScheduleScope,
} from "../lib/productionScheduleFilters";
import { readListUiState, writeListUiState } from "../lib/poListReturn";

const root = path.join(__dirname);

describe("ProductionSchedule persist + scope contracts", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  test("source wires poListReturn capture/state and schedule scope control", () => {
    const src = fs.readFileSync(path.join(root, "ProductionSchedule.jsx"), "utf8");
    const cells = fs.readFileSync(path.join(root, "../components/ProductionScheduleSheetCells.jsx"), "utf8");
    expect(src).toMatch(/capturePoOpenFromList/);
    expect(src).toMatch(/poNavStateFromList/);
    expect(src).toMatch(/writeListUiState/);
    expect(src).toMatch(/readListUiState/);
    expect(src).toMatch(/prod-schedule-scope/);
    expect(src).toMatch(/PRODUCTION_SCHEDULE_SCOPE_DEFAULT/);
    expect(src).toMatch(/stage:\s*["']penjadwalan["']/);
    expect(cells).toMatch(/poLinkState/);
    expect(cells).toMatch(/onPoOpen/);
    expect(cells).toMatch(/state=\{poLinkState\}/);
  });

  test("legacy listUi without scheduleScope hydrates to default not all", () => {
    writeListUiState("u1", "production-schedule", {
      filters: { q: "outlet-a" },
      sortBy: "work_date",
      mode: "tabel",
      columnView: "lengkap",
    });
    const saved = readListUiState("u1", "production-schedule", {});
    expect(saved.filters.q).toBe("outlet-a");
    expect(Object.prototype.hasOwnProperty.call(saved.filters || {}, "scheduleScope")).toBe(false);
    expect(normalizeProductionScheduleScope(saved.filters?.scheduleScope)).toBe(
      PRODUCTION_SCHEDULE_SCOPE_DEFAULT,
    );
  });
});
