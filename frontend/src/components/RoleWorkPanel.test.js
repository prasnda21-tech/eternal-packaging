import fs from "fs";
import path from "path";

describe("Card Screener remain label", () => {
  const role = fs.readFileSync(path.join(__dirname, "RoleWorkPanel.jsx"), "utf8");
  const mobile = fs.readFileSync(
    path.join(__dirname, "../../../mobile/app/(app)/po/[id].tsx"),
    "utf8"
  );

  test("web screener card hides remain metric", () => {
    expect(role).toMatch(/model\.hideRemain/);
    expect(role).toMatch(/!model\.hideRemain && \(/);
  });

  test("mobile screener card hides remain metric", () => {
    expect(mobile).toMatch(/testID="screener-panel"/);
    expect(mobile).toMatch(/showRemain=\{false\}/);
  });
});
