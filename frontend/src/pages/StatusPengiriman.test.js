import fs from "fs";
import path from "path";

describe("Status Pengiriman header filters", () => {
  const src = fs.readFileSync(path.join(__dirname, "StatusPengiriman.jsx"), "utf8");

  test("has customer, po, order date, delivery date, refresh", () => {
    expect(src).toMatch(/ship-filter-customer/);
    expect(src).toMatch(/ship-filter-po/);
    expect(src).toMatch(/ship-filter-order-date/);
    expect(src).toMatch(/ship-filter-delivery-date/);
    expect(src).toMatch(/ship-refresh/);
    expect(src).toMatch(/ship-filter-cabang/);
    expect(src).toMatch(/ship-filter-gudang/);
    expect(src).toMatch(/matchesShipFilters/);
  });
});
