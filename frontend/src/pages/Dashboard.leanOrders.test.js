import fs from "fs";
import path from "path";

describe("Dashboard lean orders view", () => {
  const src = fs.readFileSync(path.join(__dirname, "Dashboard.jsx"), "utf8");

  test("shop-floor Dashboard opts into GET /orders?view=dashboard", () => {
    expect(src).toMatch(/api\.listOrders\(status,\s*\{\s*view:\s*"dashboard"\s*\}\)/);
    expect(src).not.toMatch(/api\.listOrders\(status\)\s*,/);
  });

  test("does not use mobile summary view", () => {
    expect(src).not.toMatch(/view:\s*"summary"/);
  });
});
