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

  test("row checkboxes and select-all are scoped to visible filtered rows", () => {
    expect(src).toMatch(/ship-select-all/);
    expect(src).toMatch(/ship-check-\$\{r\.order_number\}/);
    expect(src).toMatch(/Pilih Semua hanya untuk baris yang sedang tampil/);
    expect(src).toMatch(/selectAllVisible\(visibleIds/);
    expect(src).toMatch(/pruneSelection/);
    expect(src).toMatch(/ship-selected-count/);
    expect(src).not.toMatch(/bulkCancel|bulkDelete|Hapus Permanen|Batalkan Terpilih/);
  });
});
