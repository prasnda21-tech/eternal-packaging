import fs from "fs";
import path from "path";

describe("operational holiday calendar", () => {
  const source = fs.readFileSync(path.join(__dirname, "OperationalHolidaySettings.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "OperationalHolidaySettings.css"), "utf8");
  const settings = fs.readFileSync(path.join(__dirname, "../pages/Settings.jsx"), "utf8");

  test("desktop page has the operational controls and delete confirmation", () => {
    expect(source).toMatch(/Kalender Libur Operasional/);
    expect(source).toMatch(/data-testid="holiday-date"/);
    expect(source).toMatch(/data-testid="holiday-note"/);
    expect(source).toMatch(/Tambah Libur/);
    expect(source).toMatch(/data-testid=\{`holiday-edit-\$\{row\.date\}`\}/);
    expect(source).toMatch(/data-testid="holiday-delete-confirm"/);
    expect(source).toMatch(/data-testid="holiday-empty"/);
    expect(source).toMatch(/data-testid="holiday-loading"/);
    expect(source).toMatch(/data-testid="holiday-error"/);
    expect(source).toMatch(/toLocaleDateString\("id-ID"/);
    expect(source).toMatch(/canManage/);
    expect(source).toMatch(/OperationalHolidayReadonly/);
  });

  test("settings hides mutation controls without the calendar permission", () => {
    expect(settings).toMatch(/operational-calendar" && hasPerm\("settings\.operational_calendar\.manage"\)/);
    expect(settings).toMatch(/OperationalHolidayReadonly/);
    expect(css).toMatch(/\.ops-holiday-card/);
    expect(css).toMatch(/gap: 16px/);
    expect(css).toMatch(/gap: 12px/);
    expect(css).toMatch(/gap: 8px/);
  });
});
