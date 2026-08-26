import fs from "fs";
import path from "path";

describe("Status PO header filters", () => {
  const src = fs.readFileSync(path.join(__dirname, "StatusPO.jsx"), "utf8");

  test("reuses Status Pengiriman toolbar fields plus status and date range", () => {
    expect(src).toMatch(/ship-status-toolbar/);
    expect(src).toMatch(/ship-filter-field/);
    expect(src).toMatch(/status-po-filter-bar/);
    expect(src).toMatch(/status-po-filter-po/);
    expect(src).toMatch(/status-po-filter-customer/);
    expect(src).toMatch(/status-po-filter-status/);
    expect(src).toMatch(/status-po-filter-date-from/);
    expect(src).toMatch(/status-po-filter-date-to/);
    expect(src).toMatch(/status-po-filter-reset/);
    expect(src).toMatch(/Reset Filter/);
    expect(src).toMatch(/matchesStatusPoFilters/);
    expect(src).toMatch(/EMPTY_STATUS_PO_FILTERS/);
  });

  test("G. dashboard view query still loads Status PO subset", () => {
    expect(src).toMatch(/params\.get\("view"\)/);
    expect(src).toMatch(/api\.statusPo\(view \? \{ view \} : \{\}\)/);
  });

  test("reset clears header filters not dashboard view", () => {
    expect(src).toMatch(/setFilters\(EMPTY_STATUS_PO_FILTERS\)/);
    expect(src).not.toMatch(/params\.delete\("view"\)/);
    expect(src).not.toMatch(/setSearchParams/);
  });

  test("no pagination to reset", () => {
    expect(src).not.toMatch(/setPage/);
    expect(src).not.toMatch(/page=/);
  });

  test("empty state preserved", () => {
    expect(src).toMatch(/Tidak ada PO/);
    expect(src).toMatch(/!visible\.length/);
  });
});
