import fs from "fs";
import path from "path";

describe("web floating bell auth boundary", () => {
  test("public customer approval page is outside AppShell", () => {
    const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
    expect(app).toMatch(/path="\/customer\/design-approval\/:token"/);
    expect(app).toMatch(/element=\{<PublicCustomerApproval \/>\}/);
    const publicSlice = app.slice(
      app.indexOf('path="/customer/design-approval/:token"'),
      app.indexOf('path="/customer/design-approval/:token"') + 180
    );
    expect(publicSlice).not.toMatch(/AppShell/);
    expect(app).toMatch(/<Route element=\{<Protected><AppShell \/><\/Protected>\}>/);
  });

  test("mobile notifications route remains available", () => {
    const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
    expect(app).toMatch(/path="\/m\/notifications"/);
    expect(app).toMatch(/MobileNotificationsPage/);
  });

  test("web bell navigates via desktop productionSchedulePoHref (/po/:id), not mobile deep_link", () => {
    const bell = fs.readFileSync(path.join(__dirname, "FloatingNotificationBell.jsx"), "utf8");
    const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
    expect(app).toMatch(/path="\/po\/:id"/);
    expect(bell).toMatch(/productionSchedulePoHref/);
    expect(bell).toMatch(/item\.order_id/);
    expect(bell).not.toMatch(/navigate\(.*deep_link/);
    const mobile = fs.readFileSync(path.join(__dirname, "../mobile/MobileNotificationsPage.jsx"), "utf8");
    expect(mobile).toMatch(/deep_link/);
    expect(mobile).toMatch(/\/m\/orders\//);
  });
});
