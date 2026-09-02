import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");

describe("Settings sales activity tab", () => {
  test("registers Sales Activity settings tab without redesign", () => {
    expect(src).toMatch(/settings-tab-sales-activity/);
    expect(src).toMatch(/SalesActivitySettings/);
    expect(src).toMatch(/settings.sales_activity.manage/);
    expect(src).toMatch(/tab === "sales-activity"/);
  });
});
