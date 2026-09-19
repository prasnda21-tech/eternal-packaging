import fs from "fs";
import path from "path";

describe("loading performance contracts", () => {
  const app = fs.readFileSync(path.join(__dirname, "App.js"), "utf8");
  const indexJs = fs.readFileSync(path.join(__dirname, "index.js"), "utf8");
  const indexCss = fs.readFileSync(path.join(__dirname, "index.css"), "utf8");
  const html = fs.readFileSync(path.join(__dirname, "../public/index.html"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "lib/api.js"), "utf8");
  const schedule = fs.readFileSync(path.join(__dirname, "pages/ProductionSchedule.jsx"), "utf8");
  const mobileSchedule = fs.readFileSync(path.join(__dirname, "mobile/MobileProductionSchedulePage.jsx"), "utf8");
  const mobileDelivery = fs.readFileSync(path.join(__dirname, "mobile/MobileDeliverySchedulePage.jsx"), "utf8");

  test("production sheet CSS is not in the initial App graph", () => {
    expect(app).not.toMatch(/productionSheet\.css/);
    expect(schedule).toMatch(/productionSheet\.css/);
    expect(mobileSchedule).toMatch(/productionSheet\.css/);
    expect(mobileDelivery).toMatch(/productionSheet\.css/);
  });

  test("entry does not mount unused React Query", () => {
    expect(indexJs).not.toMatch(/@tanstack\/react-query/);
    expect(indexJs).not.toMatch(/QueryClient/);
    expect(indexJs).toMatch(/registerServiceWorker/);
  });

  test("brand fonts load once from HTML, not via blocking CSS @import", () => {
    expect(indexCss).not.toMatch(/fonts\.googleapis\.com/);
    expect(html).toMatch(/family=IBM\+Plex\+Sans/);
    expect(html).toMatch(/family=IBM\+Plex\+Mono/);
    expect(html).not.toMatch(/family=Inter/);
    expect(html).toMatch(/display=swap/);
  });

  test("getOrder and listSalesReceipts collapse concurrent identical GETs", () => {
    expect(api).toMatch(/getOrder:\s*dedupeInflight\(/);
    expect(api).toMatch(/listSalesReceipts:\s*dedupeInflight\(/);
    expect(api).toMatch(/getOrder:\$\{id\}/);
    expect(api).toMatch(/me:\s*dedupeInflight\(/);
  });
});
