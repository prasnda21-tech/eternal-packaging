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

  test("operator progress sends the work date and retries the same action", () => {
    expect(role).toMatch(/work_date: confirm\.workDate/);
    expect(role).toMatch(/action_id: confirm\.actionId/);
    expect(role).not.toMatch(/work_date: new Date/);
    const execute = role.slice(role.indexOf("const execute = async"), role.indexOf("return (", role.indexOf("const execute = async")));
    expect(execute.indexOf("await api.updateOperatorProgress")).toBeGreaterThan(-1);
    expect(execute.indexOf("setConfirm(null)")).toBeGreaterThan(execute.indexOf("await api.updateOperatorProgress"));
  });
});
