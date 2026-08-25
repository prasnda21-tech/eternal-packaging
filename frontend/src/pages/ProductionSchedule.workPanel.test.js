import fs from "fs";
import path from "path";

describe("Jadwal Produksi PO click", () => {
  test("uses work panel href helper", () => {
    const src = fs.readFileSync(path.join(__dirname, "ProductionSchedule.jsx"), "utf8");
    expect(src).toMatch(/schedulePoHref/);
    expect(src).toMatch(/prod-po-link/);
    expect(src).not.toMatch(/role === ["']operator["']/);
  });
});
