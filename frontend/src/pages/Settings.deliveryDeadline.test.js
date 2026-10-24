import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const start = src.indexOf("function DeliveryDeadlineSettingsTab");
const end = src.indexOf("\nfunction OperatorTargetSettingsTab", start);
const tab = src.slice(start, end);

test("delivery estimate choices are working days with a legacy calendar note", () => {
  expect(tab).toMatch(/hari kerja/);
  expect(tab).not.toMatch(/calendar days/);
  expect(tab).toMatch(/1, 3, atau 5 hari kerja/);
  expect(tab).toMatch(/dd-choice-\$\{d\}/);
  expect(tab).toMatch(/dd-legacy-note/);
  expect(tab).toMatch(/hari kalender/);
  expect(tab).toMatch(/state\.legacy\?\.unit === "calendar_days"/);
});
