import fs from "fs";
import path from "path";

describe("Jadwal Produksi PO click", () => {
  test("uses canonical Detail PO href helper", () => {
    const src = fs.readFileSync(path.join(__dirname, "ProductionSchedule.jsx"), "utf8");
    const cells = fs.readFileSync(path.join(__dirname, "../components/ProductionScheduleSheetCells.jsx"), "utf8");
    expect(src).toMatch(/productionSchedulePoHref/);
    expect(cells).toMatch(/prod-po-link/);
    expect(src).toMatch(/prod-card-/);
    expect(src).not.toMatch(/schedulePoHref/);
    expect(src).not.toMatch(/status-pengiriman/);
    expect(src).not.toMatch(/role === ["']operator["']/);
  });
});
