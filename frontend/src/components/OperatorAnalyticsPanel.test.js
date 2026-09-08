import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "OperatorAnalyticsPanel.jsx"), "utf8");

describe("Analitik Output customer + driver reporting", () => {
  test("operator helper screener driver columns are Customer not Outlet / Customer", () => {
    expect(src).toMatch(/key: "operator"/);
    expect(src).toMatch(/key: "helper"/);
    expect(src).toMatch(/key: "screener"/);
    expect(src).toMatch(/key: "driver"/);
    expect(src).toMatch(/label: "Customer"/);
    expect(src).not.toMatch(/Outlet \/ Customer/);
    expect(src).toMatch(/key: "driver"/);
    expect(src).toMatch(/data-testid=\{`op-an-tab-\$\{s\.key\}`\}/);
    expect(src).toMatch(/op-an-driver-table/);
    expect(src).toMatch(/op-an-driver-people/);
    expect(src).toMatch(/Summary per Driver/);
    expect(src).toMatch(/Qty Terkirim/);
    expect(src).toMatch(/Jumlah Customer/);
    expect(src).toMatch(/analyticsDriver/);
  });
});
