import fs from "fs";
import path from "path";
import {
  NAV_KEYS,
  NAV_MENU_FALLBACK,
  SALES_NAV_ROUTES,
  SALES_TILE_TESTIDS,
  SALES_TILE_TONES,
  salesMenuItems,
  salesNavVisible,
} from "../lib/navAccess";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => jest.fn(),
  useParams: () => ({}),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { name: "Test User", email: "test@example.com", role: "admin" },
    hasPerm: () => true,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesOrderOptions: jest.fn().mockResolvedValue({}),
    listSalesOrders: jest.fn().mockResolvedValue({ orders: [] }),
    createSalesOrder: jest.fn().mockResolvedValue({}),
  },
}));

jest.mock("../components/ui/popover", () => ({
  Popover: ({ children }) => <div>{children}</div>,
  PopoverTrigger: ({ children }) => children,
  PopoverContent: ({ children }) => <div>{children}</div>,
}));

describe("Pesanan Penjualan UI & Data Invariants", () => {
  const src = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
  const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

  test("contains all required header fields matching Import PO model", () => {
    expect(src).toMatch(/po_number/);
    expect(src).toMatch(/order_date/);
    expect(src).toMatch(/customer/);
    expect(src).toMatch(/outlet/);
    expect(src).toMatch(/sales/);
    expect(src).toMatch(/created_by_user/);
    expect(src).toMatch(/recipient/);
    expect(src).toMatch(/address/);
    expect(src).toMatch(/delivery_zone/);
    expect(src).toMatch(/estimated_delivery/);
    expect(src).toMatch(/deadline/);
    expect(src).toMatch(/payment_terms/);
    expect(src).toMatch(/expedition/);
    expect(src).toMatch(/notes/);
  });

  test("supports multi-line items with exact line identity (no auto-merge)", () => {
    expect(src).toMatch(/line_items/);
    expect(src).toMatch(/addLineItem/);
    expect(src).toMatch(/removeLineItem/);
    expect(src).toMatch(/production_process/);
    expect(src).toMatch(/quantity/);
    expect(src).toMatch(/unit/);
    expect(src).toMatch(/color/);
    expect(src).toMatch(/item_code/);
  });

  test("implements authoritative validation and create sales order API call", () => {
    expect(src).toMatch(/api\.createSalesOrder/);
    expect(src).toMatch(/api\.salesOrderOptions/);
    expect(src).toMatch(/api\.listSalesOrders/);
    expect(src).toMatch(/Customer wajib diisi/);
    expect(src).toMatch(/Quantity harus angka lebih besar dari 0/);
  });

  test("defers options fetch until create view and aborts duplicate list fetches", () => {
    expect(src).toMatch(/if \(viewMode === "create"\) \{\s*loadOptions\(\);/);
    expect(src).toMatch(/new AbortController/);
    expect(src).toMatch(/signal: ac\.signal/);
    expect(src).toMatch(/ERR_CANCELED/);
    expect(src).toMatch(/loadOrdersAbortRef\.current\?\.abort\(\)/);
  });

  test("list search uses shared debounce hook instead of raw searchQuery state", () => {
    expect(src).toMatch(/useDebouncedLiveSearch/);
    expect(src).toMatch(/q: searchQuery/);
    expect(src).toMatch(/data-testid="search-sales-orders-input"/);
  });

  test("includes both list and create views with Lihat PO link", () => {
    expect(src).toMatch(/viewMode === "create"/);
    expect(src).toMatch(/viewMode === "list"/);
    expect(src).toMatch(/to=\{`\/po\/\$\{o\.id\}`\}/);
    expect(src).toMatch(/state=\{poListNavState\(\{ po_number: o\.po_number, customer: o\.customer \}\)\}/);
    expect(src).toMatch(/Lihat PO/);
  });

  test("registers /penjualan/pesanan-penjualan in App.js router", () => {
    expect(appSrc).toMatch(/path="\/penjualan\/pesanan-penjualan"/);
    expect(appSrc).toMatch(/<PesananPenjualan \/>/);
    expect(appSrc).toMatch(/path="\/penjualan"/);
  });

  test("PO list has checkbox, visible-only select all, Hapus Terpilih, and PO number confirm", () => {
    expect(src).toMatch(/select-all-orders/);
    expect(src).toMatch(/order-checkbox-/);
    expect(src).toMatch(/btn-hapus-terpilih/);
    expect(src).toMatch(/Hapus Terpilih/);
    expect(src).toMatch(/delete-confirm-modal/);
    expect(src).toMatch(/po_number/);
    expect(src).toMatch(/selectedIds\.size === displayedOrders\.length/);
  });

  test("list header is title-only and table columns have selectable header filters", () => {
    expect(src).not.toMatch(/Penjualan · Pesanan Penjualan/);
    expect(src).not.toMatch(/Daftar PO Penjualan dan pembuatan Sales Order baru/);
    expect(src).toMatch(/data-testid="sales-order-title"/);
    expect(src).not.toMatch(/Daftar pesanan penjualan dari pelanggan/);
    expect(src).toMatch(/so-th-filter-ship-date/);
    expect(src).toMatch(/so-th-filter-delivery/);
    expect(src).toMatch(/so-th-filter-sales/);
    expect(src).toMatch(/so-th-filter-stage/);
    expect(src).toMatch(/HeaderSelectFilter/);
    expect(src).toMatch(/tanggalKirimPreset/);
    expect(src).toMatch(/Reset Filter/);
    expect(src).not.toMatch(/so-filter-customer/);
    expect(src).not.toMatch(/so-filter-qty-min/);
    expect(src).toMatch(/sales-order-refresh-button/);
    expect(src).toMatch(/create-sales-order-btn/);
  });

  test("Barang/Jasa live search uses shared picker with 5-row list capacity", () => {
    const picker = fs.readFileSync(
      path.join(__dirname, "../components/MasterItemSearchSelect.jsx"),
      "utf8",
    );
    const pickerCss = fs.readFileSync(
      path.join(__dirname, "../components/masterItemSearchSelect.css"),
      "utf8",
    );
    expect(src).toMatch(/MasterItemSearchSelect/);
    expect(src).toMatch(/line-item-search-\$\{originalIndex\}/);
    expect(picker).toMatch(/MASTER_ITEM_LIST_VISIBLE_ROWS = 5/);
    expect(picker).toMatch(/createPortal/);
    expect(picker).toMatch(/onClick=\{\(\) => selectItem\(item\)\}/);
    expect(pickerCss).toMatch(/--mis-list-max:\s*calc\(\(var\(--mis-option-height\) \* 5\)/);
    expect(picker).not.toMatch(/Array\.from\(\s*\{\s*length:\s*5/);
  });
});


describe("Pesanan Penjualan customer live search contracts", () => {
  const src = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
  const searchSrc = fs.readFileSync(
    path.join(__dirname, "../components/CustomerSearchSelect.jsx"),
    "utf8"
  );

  test("create form uses CustomerSearchSelect instead of datalist", () => {
    expect(src).toMatch(/CustomerSearchSelect/);
    expect(src).toMatch(/handleCustomerSelect/);
    expect(src).toMatch(/handleCustomerTextChange/);
    expect(src).toMatch(/handleCustomerClear/);
    expect(src).not.toMatch(/list="customer-suggestions"/);
    expect(src).not.toMatch(/<datalist id="customer-suggestions"/);
  });

  test("CustomerSearchSelect filters active masters and requires explicit select", () => {
    expect(searchSrc).toMatch(/filterActiveMasterCustomers/);
    expect(searchSrc).toMatch(/isActiveMasterCustomer/);
    expect(searchSrc).toMatch(/onSelectCustomer/);
    expect(searchSrc).toMatch(/onTextChange/);
    expect(searchSrc).toMatch(/data-testid=\{testId\}/);
    expect(searchSrc).toMatch(/masterItemSearchSelect\.css/);
  });

  test("registered badge and repeat gate follow customer_id only", () => {
    expect(src).toMatch(/data-testid="badge-customer-registered"/);
    expect(src).toMatch(/data-testid="badge-customer-new"/);
    expect(src).toMatch(/\{formData\.customer_id \?/);
    expect(src).toMatch(/disabled=\{!formData\.customer_id/);
  });
});

describe("Pesanan Penjualan Nav Integration", () => {
  const has = (...keys) => (k) => keys.includes(k);

  test("NAV_KEYS includes salesOrder", () => {
    expect(NAV_KEYS.salesOrder).toBe("nav.sales.sales_order");
    expect(SALES_NAV_ROUTES[NAV_KEYS.salesOrder]).toBe("/penjualan/pesanan-penjualan");
    expect(SALES_TILE_TONES[NAV_KEYS.salesOrder]).toBe("blue");
    expect(SALES_TILE_TESTIDS[NAV_KEYS.salesOrder]).toBe("top-nav-sales-order");
  });

  test("salesNavVisible enables parent for salesOrder", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, NAV_KEYS.salesOrder));
    expect(v.parent).toBe(true);
    expect(v.salesOrder).toBe(true);
  });

  test("salesMenuItems renders Pesanan Penjualan tile", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesOrder));
    expect(items).toEqual([
      {
        key: NAV_KEYS.salesOrder,
        label: "Pesanan Penjualan",
        to: "/penjualan/pesanan-penjualan",
        tone: "blue",
        testid: "top-nav-sales-order",
      },
    ]);
  });
});

describe("Pesanan Penjualan Line Items UX & Live Search", () => {
  const { filterPoLineItems } = require("./PesananPenjualan");
  const src = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");

  const sampleItems = [
    {
      item_code: "BX-01",
      name: "Box Karton 20x20",
      quantity: 10,
      unit: "pcs",
      color: "Coklat",
      production_process: "print",
      notes: "Cetak logo",
      accurate_item_id: "AID-101",
      master_item_id: "MID-101",
    },
    {
      item_code: "PB-02",
      name: "Plastik Bubble Wrap",
      quantity: 5,
      unit: "roll",
      color: "Bening",
      production_process: "non_print",
      notes: "Tebal",
      accurate_item_id: "AID-102",
      master_item_id: "MID-102",
    },
    {
      item_code: "BX-02",
      name: "Box Karton 30x30",
      quantity: 20,
      unit: "pcs",
      color: "Putih",
      production_process: "print",
      notes: "Handle with care",
      accurate_item_id: "AID-103",
      master_item_id: "MID-103",
    },
  ];

  test("1. item search lives on each row, not as a second global search", () => {
    expect(src).not.toMatch(/data-testid="line-items-search-input"/);
    expect(src).not.toMatch(/placeholder="Cari barang \/ jasa\.\.\."/);
    expect(src).not.toMatch(/btn-search-line-items/);
    expect(src).toMatch(/testId=\{`line-item-search-\$\{originalIndex\}`\}/);
    expect(src).toMatch(/placeholder="Cari nama \/ kode barang…"/);
  });

  test("2. full item list visible when search empty", () => {
    const results = filterPoLineItems(sampleItems, "");
    expect(results).toHaveLength(3);
    expect(results.map((r) => r.originalIndex)).toEqual([0, 1, 2]);
    expect(results.map((r) => r.line.item_code)).toEqual(["BX-01", "PB-02", "BX-02"]);
  });

  test("3. typing name filters immediately with partial match", () => {
    const results = filterPoLineItems(sampleItems, "bubble");
    expect(results).toHaveLength(1);
    expect(results[0].originalIndex).toBe(1);
    expect(results[0].line.name).toBe("Plastik Bubble Wrap");
  });

  test("4. typing item code filters if code exists", () => {
    const results = filterPoLineItems(sampleItems, "BX-01");
    expect(results).toHaveLength(1);
    expect(results[0].originalIndex).toBe(0);
    expect(results[0].line.item_code).toBe("BX-01");
  });

  test("5. search is case-insensitive and trims whitespace", () => {
    const resultsUpper = filterPoLineItems(sampleItems, "  BOX  ");
    expect(resultsUpper).toHaveLength(2);
    expect(resultsUpper.map((r) => r.line.item_code)).toEqual(["BX-01", "BX-02"]);

    const resultsCodeLower = filterPoLineItems(sampleItems, "bx-02");
    expect(resultsCodeLower).toHaveLength(1);
    expect(resultsCodeLower[0].line.name).toBe("Box Karton 30x30");
  });

  test("6. clearing search restores full list", () => {
    let query = "bubble";
    let filtered = filterPoLineItems(sampleItems, query);
    expect(filtered).toHaveLength(1);

    query = "";
    filtered = filterPoLineItems(sampleItems, query);
    expect(filtered).toHaveLength(3);
  });

  test("7. no-match returns empty array and template contains empty state message", () => {
    const results = filterPoLineItems(sampleItems, "barang-tidak-ada-xyz");
    expect(results).toHaveLength(0);
    expect(src).toMatch(/Tidak ada barang\/jasa yang cocok\./);
    expect(src).toMatch(/data-testid="line-items-empty-state"/);
  });

  test("8. selecting filtered item updates exact item identity by original index", () => {
    const lines = [
      { name: "Dus A", item_code: "D-01", quantity: 1, accurate_item_id: "A1" },
      { name: "Box Karton", item_code: "BX-01", quantity: 10, accurate_item_id: "A2" },
      { name: "Dus B", item_code: "D-02", quantity: 2, accurate_item_id: "A3" },
      { name: "Box Karton", item_code: "BX-02", quantity: 20, accurate_item_id: "A4" },
    ];

    const filtered = filterPoLineItems(lines, "BX-02");
    expect(filtered).toHaveLength(1);
    const target = filtered[0];
    expect(target.originalIndex).toBe(3);
    expect(target.line.item_code).toBe("BX-02");

    const updatedLines = [...lines];
    updatedLines[target.originalIndex] = {
      ...updatedLines[target.originalIndex],
      quantity: 99,
      notes: "Updated row",
    };

    expect(updatedLines[3].quantity).toBe(99);
    expect(updatedLines[3].notes).toBe("Updated row");
    expect(updatedLines[1].quantity).toBe(10);
    expect(updatedLines[0].name).toBe("Dus A");
  });

  test("9. create form scrolls as one body and item rows follow their content", () => {
    const css = fs.readFileSync(path.join(__dirname, "pesananPenjualan.css"), "utf8");
    expect(src).toMatch(/data-testid="line-items-table-container"/);
    expect(src).toMatch(/data-scroll-owner="form"/);
    expect(src).not.toMatch(/data-scroll-owner="line-items"/);
    expect(src).toMatch(/data-scroll-owner=\{viewMode === "create" \? "workspace" : "list"\}/);
    expect(src).not.toMatch(/minHeight:\s*"380px"/);
    expect(src).not.toMatch(/maxHeight:\s*"560px"/);
    expect(css).toMatch(/\.so-create-stage\s*\{[^}]*overflow-y:\s*auto/);
    expect(css).toMatch(/\.so-create-lines-scroll\s*\{[^}]*overflow:\s*visible/);
    expect(css).toMatch(/\.so-create-lines-scroll\s*\{[^}]*height:\s*auto/);
    expect(css).toMatch(/\.so-create-lines-scroll\s*\{[^}]*max-height:\s*none/);
    expect(css).not.toMatch(/\.so-create-lines-scroll\s*\{[^}]*overflow-y:\s*auto/);
    expect(css).toMatch(/\.so-thumb\s*\{[^}]*object-fit:\s*contain/);
    expect(css).not.toMatch(/\.so-create-lines-scroll\s*\{[^}]*max-height:\s*\d+vh/);
  });
});

describe("Pesanan Penjualan previewRepeatOrder fixture gate", () => {
  const src = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
  const fixSrc = fs.readFileSync(
    path.join(__dirname, "../lib/salesOrderRepeatPreviewFixtures.js"),
    "utf8"
  );

  test("preview helpers require development + previewRepeatOrder=1", () => {
    expect(fixSrc).toMatch(/String\(nodeEnv\) !== "development"/);
    expect(fixSrc).toMatch(/PREVIEW_REPEAT_ORDER_PARAM/);
    expect(fixSrc).toMatch(/previewRepeatOrder/);
    expect(src).toMatch(/isSalesOrderRepeatPreviewActive/);
    expect(src).toMatch(/process\.env\.NODE_ENV/);
  });

  test("normal mode still calls real repeat-source API", () => {
    expect(src).toMatch(/api\.salesOrderRepeatSource/);
    expect(src).toMatch(/previewRepeatOrderActive/);
    expect(src).toMatch(/getPreviewRepeatSource\(customerId\)/);
  });
});
