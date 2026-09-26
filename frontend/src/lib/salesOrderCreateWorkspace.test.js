jest.mock("../components/ui/popover", () => ({
  Popover: ({ children }) => children,
  PopoverTrigger: ({ children }) => children,
  PopoverContent: ({ children }) => children,
}));

jest.mock("react-router-dom", () => ({
  Link: ({ children }) => children,
  useNavigate: () => jest.fn(),
  useLocation: () => ({ pathname: "/", search: "", state: null }),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
  useParams: () => ({}),
}), { virtual: true });

import {
  SALES_ORDER_DRAWER_FIELD_IDS,
  SALES_ORDER_MAIN_FIELD_IDS,
  lineAfterEntryModeChange,
  lineEntryModeAfterRepeat,
  salesOrderFieldRegion,
  salesOrderIssuePlacement,
} from "./salesOrderCreateWorkspace";
import {
  buildSalesOrderCreatePayload,
  salesOrderProgress,
  salesOrderRequiredChecks,
  validateSalesOrderCreate,
} from "../pages/PesananPenjualan";

const SALES_ORDER_HEADER_PAYLOAD_KEYS = [
  "po_number", "order_date", "customer", "customer_id", "customer_phone", "customer_address",
  "customer_pic", "customer_email", "customer_city", "outlet", "recipient", "address",
  "delivery_zone", "payment_terms", "sales", "created_by_user", "expedition",
  "estimated_delivery", "deadline", "notes", "line_items",
];
const SALES_ORDER_LINE_PAYLOAD_KEYS = [
  "item_code", "name", "quantity", "unit", "color", "production_process", "notes",
  "accurate_item_id", "master_item_id",
];

function baseForm(overrides = {}) {
  return {
    po_number: " PO-1 ",
    order_date: "2026-09-23",
    customer: " PT A ",
    customer_id: "c1",
    customer_phone: "0812",
    customer_address: "Alamat master",
    customer_pic: "PIC",
    customer_email: "a@b.c",
    customer_city: "Jakarta",
    outlet: "Pusat",
    recipient: "Rina",
    address: "Jl. A",
    delivery_zone: "Jakarta Selatan",
    payment_terms: "Net 30",
    sales: "Vira",
    created_by_user: "Sales",
    expedition: "Internal fleet",
    estimated_delivery: "2026-09-26",
    deadline: "2026-09-26",
    notes: "Catatan",
    line_items: [
      {
        item_code: "BX",
        name: " Box ",
        quantity: "2",
        unit: " pcs ",
        color: "Merah",
        production_process: "print",
        notes: "n",
        accurate_item_id: "a1",
        master_item_id: "m1",
        entry_mode: "master",
      },
    ],
    ...overrides,
  };
}

describe("sales order create workspace contract", () => {
  test("required fields stay on the main page and optional fields go to the drawer", () => {
    expect(SALES_ORDER_MAIN_FIELD_IDS).toEqual([
      "po_number",
      "order_date",
      "customer",
      "customer_phone",
      "address",
    ]);
    expect(SALES_ORDER_DRAWER_FIELD_IDS).toEqual([
      "outlet",
      "sales",
      "created_by_user",
      "recipient",
      "delivery_zone",
      "estimated_delivery",
      "deadline",
      "payment_terms",
      "expedition",
      "notes",
    ]);
    SALES_ORDER_MAIN_FIELD_IDS.forEach((id) => {
      expect(salesOrderFieldRegion(id)).toBe("main");
    });
    SALES_ORDER_DRAWER_FIELD_IDS.forEach((id) => {
      expect(salesOrderFieldRegion(id)).toBe("drawer");
    });
    expect(SALES_ORDER_MAIN_FIELD_IDS.some((id) => SALES_ORDER_DRAWER_FIELD_IDS.includes(id))).toBe(false);
  });

  test("new-customer blocking checks are customer, lines, phone, and address", () => {
    const checks = salesOrderRequiredChecks({
      customer: "",
      customer_id: null,
      customer_phone: "",
      address: "",
      line_items: [{ name: "", quantity: 1 }],
    });
    expect(checks.map((item) => item.id)).toEqual([
      "customer",
      "line_items",
      "customer_phone",
      "address",
    ]);
    expect(salesOrderProgress({
      customer: "",
      customer_id: null,
      customer_phone: "",
      address: "",
      line_items: [{ name: "", quantity: 1 }],
    })).toMatchObject({ completed: 0, total: 4, label: "0/4 wajib", statusLabel: "Belum Lengkap" });
  });

  test("progress complete flag matches submit validation", () => {
    const cases = [
      { customer: "", customer_id: null, customer_phone: "", address: "", line_items: [{ name: "", quantity: 1 }] },
      { customer: "Baru", customer_id: null, customer_phone: "0812", address: "Jl", line_items: [{ name: "A", quantity: 1 }] },
      { customer: "Baru", customer_id: null, customer_phone: " ", address: "Jl", line_items: [{ name: "A", quantity: 1 }] },
      { customer: "PT A", customer_id: "c1", customer_phone: "", address: "", line_items: [{ name: "Box", quantity: 2 }] },
      { customer: "PT A", customer_id: "c1", customer_phone: "", address: "", line_items: [{ name: " ", quantity: 2 }] },
      { customer: "PT A", customer_id: "c1", line_items: [{ name: "Box", quantity: 0 }] },
    ];
    cases.forEach((form) => {
      const progress = salesOrderProgress(form);
      const error = validateSalesOrderCreate(form);
      expect(progress.complete).toBe(error === "");
      expect(progress.statusLabel).toBe(progress.complete ? "Lengkap" : "Belum Lengkap");
      expect(progress.completed === progress.total).toBe(progress.complete);
    });
    expect(salesOrderProgress({
      customer: "PT A",
      customer_id: "c1",
      line_items: [{ name: "", quantity: 1 }],
    }).label).toBe("1/2 pelanggan & barang");
  });

  test("registered customer drops phone and address from the blocking set", () => {
    const progress = salesOrderProgress({
      customer: "PT A",
      customer_id: "c1",
      line_items: [{ name: "Box", quantity: 2 }],
    });
    expect(progress).toMatchObject({
      completed: 2,
      total: 2,
      complete: true,
      customerAndGoodsOnly: true,
      label: "2/2 pelanggan & barang",
      statusLabel: "Lengkap",
    });
    expect(salesOrderProgress({
      customer: "PT A",
      customer_id: "c1",
      line_items: [{ name: "Box", quantity: 2, unit_price: "" }],
    }).statusLabel).toBe("Lengkap");
    expect(validateSalesOrderCreate({
      customer: "PT A",
      customer_id: "c1",
      customer_phone: "",
      address: "",
      line_items: [{ name: "Box", quantity: 2 }],
    })).toBe("");
  });

  test("validation messages stay the same", () => {
    expect(validateSalesOrderCreate({ customer: "  ", customer_id: null, line_items: [] })).toBe("Customer wajib diisi.");
    expect(validateSalesOrderCreate({
      customer: "Baru",
      customer_id: null,
      customer_phone: " ",
      address: "Jl",
      line_items: [{ name: "A", quantity: 1 }],
    })).toBe("Pelanggan baru terdeteksi: No. HP / WhatsApp pelanggan wajib diisi.");
    expect(validateSalesOrderCreate({
      customer: "Baru",
      customer_id: null,
      customer_phone: "0812",
      address: " ",
      line_items: [{ name: "A", quantity: 1 }],
    })).toBe("Pelanggan baru terdeteksi: Alamat lengkap pengiriman wajib diisi.");
    expect(validateSalesOrderCreate({
      customer: "Baru",
      customer_id: null,
      customer_phone: "0812",
      address: "Jl",
      line_items: [],
    })).toBe("Minimal harus ada 1 line item.");
    expect(validateSalesOrderCreate({
      customer: "Baru",
      customer_id: null,
      customer_phone: "0812",
      address: "Jl",
      line_items: [{ name: "  ", quantity: 1 }],
    })).toBe("Baris #1: Nama barang/jasa wajib diisi.");
    expect(validateSalesOrderCreate({
      customer: "Baru",
      customer_id: null,
      customer_phone: "0812",
      address: "Jl",
      line_items: [{ name: "A", quantity: 0 }],
    })).toBe("Baris #1: Quantity harus angka lebih besar dari 0.");
  });

  test("manual mode clears master identity and keeps the free name and code", () => {
    const next = lineAfterEntryModeChange({
      name: "Box Karton",
      item_code: "BX-01",
      unit: "pcs",
      accurate_item_id: "a1",
      master_item_id: "m1",
      quantity: 2,
    }, "manual");
    expect(next.accurate_item_id).toBeNull();
    expect(next.master_item_id).toBeNull();
    expect(next.name).toBe("Box Karton");
    expect(next.item_code).toBe("BX-01");
    expect(next.quantity).toBe(2);
    const back = lineAfterEntryModeChange({ ...next, name: "Nama Manual" }, "master");
    expect(back.name).toBe("");
    expect(back.item_code).toBe("");
    expect(back.accurate_item_id).toBeNull();
    expect(back.master_item_id).toBeNull();
    expect(back.quantity).toBe(2);
    expect(lineEntryModeAfterRepeat({ accurate_item_id: "a1" })).toBe("master");
    expect(lineEntryModeAfterRepeat({ name: "Bebas" })).toBe("manual");
  });

  test("payload matches the previous create contract and drops UI-only keys", () => {
    const payload = buildSalesOrderCreatePayload(baseForm({
      line_items: [
        ...baseForm().line_items,
        {
          item_code: "BX",
          name: " Box ",
          quantity: "3",
          unit: "pcs",
          color: "Merah",
          production_process: "non_print",
          notes: "",
          accurate_item_id: null,
          master_item_id: null,
        },
      ],
    }));
    SALES_ORDER_HEADER_PAYLOAD_KEYS.forEach((key) => {
      expect(payload).toHaveProperty(key);
    });
    expect(payload.po_number).toBe("PO-1");
    expect(payload.customer).toBe("PT A");
    expect(payload.customer_id).toBe("c1");
    expect(payload.outlet).toBe("Pusat");
    expect(payload.notes).toBe("Catatan");
    expect(payload.line_items).toHaveLength(2);
    expect(payload.line_items[0]).toEqual({
      item_code: "BX",
      name: "Box",
      quantity: 2,
      unit: "pcs",
      color: "Merah",
      production_process: "print",
      notes: "n",
      accurate_item_id: "a1",
      master_item_id: "m1",
      unit_price: null,
      price_source: null,
      price_review: false,
    });
    expect(payload.line_items[1].name).toBe("Box");
    expect(payload.line_items[1].quantity).toBe(3);
    expect(payload.line_items[1].production_process).toBe("non_print");
    expect(payload.line_items[1].accurate_item_id).toBeNull();
    payload.line_items.forEach((line) => {
      expect(line.entry_mode).toBeUndefined();
      SALES_ORDER_LINE_PAYLOAD_KEYS.forEach((key) => {
        expect(line).toHaveProperty(key);
      });
    });
    expect(payload.line_items.reduce((sum, li) => sum + li.quantity, 0)).toBe(5);
    const withUi = buildSalesOrderCreatePayload(baseForm({
      line_items: [{ ...baseForm().line_items[0], rowKey: "so-line-9", entry_mode: "manual" }],
    }));
    expect(withUi.line_items[0].rowKey).toBeUndefined();
    expect(withUi.line_items[0].entry_mode).toBeUndefined();
    expect(withUi.line_items[0].price_lock).toBeUndefined();
    expect(withUi.line_items[0].name).toBe("Box");
  });

  test("blank PO number stays an absent value and identical lines are not merged", () => {
    const payload = buildSalesOrderCreatePayload(baseForm({
      po_number: "   ",
      line_items: [
        { name: "Sama", quantity: 1, unit: "pcs", production_process: "print", item_code: "", color: "", notes: "", accurate_item_id: null, master_item_id: null },
        { name: "Sama", quantity: 1, unit: "pcs", production_process: "print", item_code: "", color: "", notes: "", accurate_item_id: null, master_item_id: null },
      ],
    }));
    expect(payload.po_number).toBeUndefined();
    expect(payload.line_items).toHaveLength(2);
    expect(payload.line_items[0].name).toBe("Sama");
    expect(payload.line_items[1].name).toBe("Sama");
  });

  test("drawer errors are discoverable and main errors stay on the main page", () => {
    expect(salesOrderIssuePlacement("Pelanggan baru terdeteksi: No. HP / WhatsApp pelanggan wajib diisi.")).toEqual({
      field: "customer_phone",
      region: "main",
    });
    expect(salesOrderIssuePlacement("Pelanggan baru terdeteksi: Alamat lengkap pengiriman wajib diisi.").region).toBe("main");
    expect(salesOrderIssuePlacement("Zona Kirim tidak dikenali")).toEqual({
      field: "delivery_zone",
      region: "drawer",
    });
    expect(salesOrderIssuePlacement("Baris #1: Nama barang/jasa wajib diisi.").region).toBe("lines");
    expect(salesOrderIssuePlacement("")).toBeNull();
  });
});
