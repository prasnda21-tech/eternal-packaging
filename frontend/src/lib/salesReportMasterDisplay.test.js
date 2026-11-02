/**
 * Stage D FE contract: optional Master columns are additive; Sales attribution columns stay.
 */
const fs = require("fs");
const path = require("path");

describe("sales report master display contract", () => {
  test("backend SALES_COLUMNS keep financial Sales keys and add optional Master keys", () => {
    const src = fs.readFileSync(
      path.join(__dirname, "../../../backend/services/sales_reports.py"),
      "utf8",
    );
    expect(src).toMatch(/\{"key": "sales", "label": "Sales"/);
    expect(src).toMatch(/\{"key": "attribution_status"/);
    expect(src).toMatch(/\{"key": "sales_master_name", "label": "Master Sales"/);
    expect(src).toMatch(/\{"key": "sales_master_id"/);
    expect(src).toMatch(/\{"key": "sales_master_match_status"/);
    expect(src).toMatch(/"default": False/);
    expect(src).toMatch(/attach_master_display_to_sales_rows/);
  });

  test("SalesDashboard shows Master as secondary metadata only when matched", () => {
    const src = fs.readFileSync(
      path.join(__dirname, "../pages/SalesDashboard.jsx"),
      "utf8",
    );
    expect(src).toMatch(/sales_master_match_status === "matched"/);
    expect(src).toMatch(/Master: \{row\.sales_master_name\}/);
    expect(src).toMatch(/row\.label/);
  });
});
