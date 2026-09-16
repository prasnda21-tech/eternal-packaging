import fs from "fs";
import path from "path";

describe("Status PO header filters", () => {
  const src = fs.readFileSync(path.join(__dirname, "StatusPO.jsx"), "utf8");
  const table = fs.readFileSync(path.join(__dirname, "../components/StatusPoWorkflowTable.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

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
    expect(src).toMatch(/>\s*Reset\s*</);
    expect(src).toMatch(/matchesStatusPoFilters/);
    expect(src).toMatch(/EMPTY_STATUS_PO_FILTERS/);
  });

  test("G. dashboard view query still loads Status PO subset", () => {
    expect(src).toMatch(/params\.get\("view"\)/);
    expect(src).toMatch(/api\.statusPo\(view \? \{ view \} : \{\}\)/);
    expect(src).toMatch(/loadInFlight\.current/);
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
    expect(table).toMatch(/Tidak ada PO/);
    expect(src).toMatch(/visible\.length/);
  });

  test("row checkboxes and select-all are scoped to visible filtered rows", () => {
    expect(table).toMatch(/status-po-select-all/);
    expect(table).toMatch(/status-po-check-\$\{r\.po_number\}/);
    expect(src).toMatch(/Pilih Semua hanya untuk baris yang sedang tampil/);
    expect(table).toMatch(/selectAllVisible\(visibleIds/);
    expect(src).toMatch(/pruneSelection/);
    expect(src).toMatch(/status-po-selected-count/);
  });

  test("bulk cancel and permanent delete reuse existing APIs and permissions", () => {
    expect(src).toMatch(/hasPerm\("po.cancel"\)/);
    expect(src).toMatch(/hasPerm\("po.delete"\)/);
    expect(src).toMatch(/api\.bulkCancel/);
    expect(src).toMatch(/api\.bulkDelete/);
    expect(src).toMatch(/Batalkan Terpilih/);
    expect(src).toMatch(/Hapus Permanen Terpilih/);
    expect(src).toMatch(/status-po-bulk-count/);
    expect(src).toMatch(/inFlight\.current/);
    expect(src).toMatch(/removeSucceeded/);
  });

  test("pengiriman hari ini bookmarks redirect to Jadwal Pengiriman", () => {
    expect(app).toMatch(/path="\/produksi\/pengiriman-hari-ini"/);
    expect(app).toMatch(/Navigate to="\/produksi\/jadwal-pengiriman\?status=hari_ini"/);
    const routeChunk = app.slice(
      app.indexOf('path="/produksi/pengiriman-hari-ini"'),
      app.indexOf('path="/produksi/pengiriman-hari-ini"') + 180,
    );
    expect(routeChunk).not.toMatch(/StatusPO/);
  });
});
