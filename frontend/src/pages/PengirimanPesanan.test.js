import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, __reset as resetRouter } from "react-router-dom";
import PengirimanPesanan, { recipientWhatsappDigits } from "./PengirimanPesanan";
import MobileDeliveryWorkspace from "../mobile/workspaces/MobileDeliveryWorkspace";

jest.mock("react-router-dom", () => {
  const router = require("../test/reactRouterMock");
  return {
    ...router,
    useNavigate: () => () => {},
  };
}, { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: jest.fn(() => ({
    user: { id: "u1", role: "admin" },
    hasPerm: () => true,
  })),
}));

jest.mock("../components/HeaderSelectFilter", () => () => null);
import {
  NAV_KEYS,
  NAV_MENU_FALLBACK,
  SALES_NAV_ROUTES,
  SALES_TILE_TESTIDS,
  SALES_TILE_TONES,
  salesMenuItems,
  salesNavVisible,
} from "../lib/navAccess";
import api from "../lib/api";

describe("Pengiriman Pesanan UI & Data Invariants", () => {
  const src = fs.readFileSync(path.join(__dirname, "PengirimanPesanan.jsx"), "utf8");
  const poDetailSrc = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const salesOrderSrc = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
  const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

  test("contains all required header fields for Pengiriman Pesanan", () => {
    expect(src).toMatch(/delivery_date/);
    expect(src).toMatch(/po_number/);
    expect(src).toMatch(/suggested_sj_number/);
    expect(src).toMatch(/customer/);
    expect(src).toMatch(/outlet/);
    expect(src).toMatch(/recipient/);
    expect(src).toMatch(/address/);
    expect(src).toMatch(/delivery_zone/);
    expect(src).toMatch(/sales/);
    expect(src).toMatch(/expedition/);
    expect(src).toMatch(/notes/);
    expect(src).toMatch(/SjDeliveryLocationPicker/);
    expect(src).toMatch(/Nomor SJ/);
    expect(src).not.toMatch(/Backend-Authoritative/);
    expect(src).toMatch(/sjOverridePayload/);
    expect(src).toMatch(/delivery_location/);
  });

  test("supports multi-line items with exact line identity and remaining quantities", () => {
    expect(src).toMatch(/line_qtys/);
    expect(src).toMatch(/line_notes/);
    expect(src).toMatch(/remaining_qty/);
    expect(src).toMatch(/ordered_qty/);
    expect(src).toMatch(/totalShippingQty/);
    expect(src).toMatch(/overshipmentErrors/);
    expect(src).toMatch(/melebihi sisa PO/);
  });

  test("implements backend-authoritative sales delivery API calls", () => {
    expect(src).toMatch(/api\.listSalesDeliveries/);
    expect(src).toMatch(/api\.getPoDeliverySummary/);
    expect(src).toMatch(/api\.createSalesDelivery/);
    expect(src).toMatch(/api\.getSalesDelivery/);
    expect(src).toMatch(/api\.salesDeliveryPdfUrl/);
  });

  test("create success refreshes list without inventing file metadata", () => {
    expect(src).toMatch(/loadDeliveries\(\)/);
    expect(src).not.toMatch(/SYSTEM_GENERATED/);
    expect(src).not.toMatch(/surat_jalan_files:\s*\[/);
  });

  test("includes both list, create form, and detail modal with PDF print", () => {
    expect(src).toMatch(/viewMode === "create"/);
    expect(src).toMatch(/viewMode === "list"/);
    expect(src).toMatch(/delivery-detail-modal/);
    expect(src).toMatch(/Cetak PDF/);
    expect(src).toMatch(/Riwayat Pengiriman untuk PO/);
  });

  test("registers /penjualan/pengiriman-pesanan in App.js router", () => {
    expect(appSrc).toMatch(/path="\/penjualan\/pengiriman-pesanan"/);
    expect(appSrc).toMatch(/<PengirimanPesanan \/>/);
    expect(appSrc).toMatch(/NAV_KEYS\.salesDeliveryOrder/);
  });

  test("SJ list has checkbox, visible-only select all, Batal Terpilih, and SJ number confirm", () => {
    expect(src).toMatch(/select-all-deliveries/);
    expect(src).toMatch(/delivery-checkbox-/);
    expect(src).toMatch(/btn-batal-terpilih/);
    expect(src).toMatch(/Batal Terpilih/);
    expect(src).toMatch(/batal-confirm-modal/);
    expect(src).toMatch(/sj_number/);
    expect(src).toMatch(/selectedIds\.size === deliveries\.length/);
  });

  test("Pengiriman row has Batal button with SJ business number confirmation and cancel wiring", () => {
    expect(src).toMatch(/btn-batal-delivery-/);
    expect(src).toMatch(/number: d\.sj_number/);
    expect(src).toMatch(/numbers: \[d\.sj_number\]/);
    expect(src).toMatch(/api\.deleteSalesDelivery/);
    expect(src).toMatch(/handleBulkDelete\(\) : handleDelete\(deleteConfirm\.id\)/);
    expect(src).toMatch(/batal-error|batal-success/);
    expect(src).toMatch(/loadDeliveries\(\)/);
    expect(src).toMatch(/if \(deleteLoading\) return/);
    expect(src).toMatch(/Tidak perlu mengisi alasan/);
  });

  test("renders required helper texts and read-only attributes for PO and SJ numbers", () => {
    expect(src).toMatch(/Data diisi otomatis dari Pesanan Penjualan dan dapat disesuaikan untuk pengiriman ini\./);
    expect(src).toMatch(/Terkunci berdasarkan PO dan urutan pengiriman\./);
    expect(src).toMatch(/data-testid="display-po-number"/);
    expect(src).toMatch(/data-testid="display-suggested-sj"/);
    expect(src).toMatch(/data-testid="display-customer"/);
    expect(src).toMatch(/data-testid="input-delivery-date"/);
    expect(src).toMatch(/data-testid="input-delivery-outlet"/);
    expect(src).toMatch(/data-testid="input-delivery-recipient"/);
    expect(src).toMatch(/data-testid="input-delivery-sales"/);
    expect(src).toMatch(/data-testid="input-delivery-expedition"/);
    expect(src).toMatch(/data-testid="input-delivery-zone"/);
    expect(src).toMatch(/data-testid="input-delivery-address"/);
    expect(src).toMatch(/data-testid="input-delivery-notes"/);
  });

  test("adds Proses ke Pengiriman Pesanan button and summary to PesananPenjualan", () => {
    expect(salesOrderSrc).toMatch(/Tanggal Kirim/);
    expect(salesOrderSrc).toMatch(/so-act-ship/);
    expect(salesOrderSrc).toMatch(/\/penjualan\/pengiriman-pesanan\?po_id=/);
  });

  test("SJ list has selectable Tgl Kirim / Dibuat Oleh header filters and Reset Filter", () => {
    expect(src).toMatch(/sj-th-filter-ship-date/);
    expect(src).toMatch(/sj-th-filter-created-by/);
    expect(src).toMatch(/delivery_date_preset/);
    expect(src).toMatch(/created_by/);
    expect(src).toMatch(/salesDeliveryFilterOptions/);
    expect(src).toMatch(/reset-delivery-filters/);
    expect(src).toMatch(/Reset Filter/);
    expect(src).toMatch(/filter-status-\$\{tab\.id\}/);
    expect(src).toMatch(/id: "SEBAGIAN"/);
    expect(src).not.toMatch(/sj-th-filter-status/);
  });

  test("implements SEBAGIAN and SELESAI status filters and badges without Dikirim/Terkirim", () => {
    // Filter tabs
    expect(src).toMatch(/id:\s*"SEBAGIAN",\s*label:\s*"Sebagian"/);
    expect(src).toMatch(/id:\s*"SELESAI",\s*label:\s*"Selesai"/);
    expect(src).not.toMatch(/id:\s*"Dikirim"/);
    expect(src).not.toMatch(/id:\s*"Terkirim"/);

    // Detail modal PO Progress Summary
    expect(src).toMatch(/data-testid="delivery-po-progress-summary"/);
    expect(src).toMatch(/TOTAL QTY PO/);
    expect(src).toMatch(/SUDAH DIBUAT SURAT JALAN/);
    expect(src).toMatch(/SISA/);
    expect(src).toMatch(/STATUS/);
    expect(src).toMatch(/detail-total-po-qty/);
    expect(src).toMatch(/detail-cumulative-sj-qty/);
    expect(src).toMatch(/detail-remaining-po-qty/);
    expect(src).toMatch(/detail-delivery-status/);
  });
});

describe("Pengiriman Pesanan Nav Integration", () => {
  const has = (...keys) => (k) => keys.includes(k);

  test("NAV_KEYS includes salesDeliveryOrder", () => {
    expect(NAV_KEYS.salesDeliveryOrder).toBe("nav.sales.delivery_order");
    expect(SALES_NAV_ROUTES[NAV_KEYS.salesDeliveryOrder]).toBe("/penjualan/pengiriman-pesanan");
    expect(SALES_TILE_TONES[NAV_KEYS.salesDeliveryOrder]).toBe("amber");
    expect(SALES_TILE_TESTIDS[NAV_KEYS.salesDeliveryOrder]).toBe("top-nav-sales-delivery-order");
  });

  test("salesNavVisible enables parent for salesDeliveryOrder", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, NAV_KEYS.salesDeliveryOrder));
    expect(v.parent).toBe(true);
    expect(v.salesDeliveryOrder).toBe(true);
  });

  test("salesMenuItems renders Pengiriman Pesanan tile", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesDeliveryOrder));
    expect(items).toContainEqual({
      key: NAV_KEYS.salesDeliveryOrder,
      label: "Pengiriman Pesanan",
      to: "/penjualan/pengiriman-pesanan",
      tone: "amber",
      testid: "top-nav-sales-delivery-order",
    });
  });

  test("api.js contains all sales delivery methods", () => {
    expect(typeof api.listSalesDeliveries).toBe("function");
    expect(typeof api.getSalesDelivery).toBe("function");
    expect(typeof api.getPoDeliverySummary).toBe("function");
    expect(typeof api.createSalesDelivery).toBe("function");
    expect(typeof api.salesDeliveryPdfUrl).toBe("function");
  });
});

describe("SJ location override on create form", () => {
  const src = fs.readFileSync(path.join(__dirname, "PengirimanPesanan.jsx"), "utf8");
  const picker = fs.readFileSync(path.join(__dirname, "../components/SjDeliveryLocationPicker.jsx"), "utf8");
  const modal = fs.readFileSync(path.join(__dirname, "../components/OutletLocationModal.jsx"), "utf8");

  test("shows PO fallback and SJ override without saving outlet", () => {
    expect(src).toMatch(/poSummary\.delivery_location/);
    expect(src).toMatch(/sjLocationOverride/);
    expect(src).toMatch(/sjOverridePayload\(sjLocationOverride\)/);
    expect(src).toMatch(/if \(locationOverride\) payload\.delivery_location = locationOverride/);
    expect(src).not.toMatch(/saveDeliveryOutlet/);
    expect(picker).not.toMatch(/saveDeliveryOutlet/);
    expect(picker).not.toMatch(/buildSaveDeliveryOutletPayload/);
    expect(picker).toMatch(/Titik Lokasi Pengiriman/);
    expect(picker).toMatch(/Menggunakan titik lokasi PO \(fallback\)/);
    expect(picker).toMatch(/Menggunakan titik lokasi Surat Jalan/);
    expect(picker).toMatch(/sj-location-clear/);
    expect(picker).toMatch(/Hapus override — kembali ke lokasi PO/);
    expect(picker).toMatch(/OutletLocationMap/);
    expect(picker).toMatch(/searchDeliveryPlaces/);
    expect(picker).toMatch(/resolveDeliveryMapsLink/);
    expect(modal).toMatch(/saveDeliveryOutlet/);
  });
});

function summaryFor(id, phone, remaining = 10) {
  return {
    po_id: id,
    po_number: id.toUpperCase(),
    customer: id === "po-2" ? "PT B" : "PT A",
    customer_phone: phone,
    recipient: "Budi",
    outlet: "Utama",
    address: "Jl. Melati 1",
    delivery_zone: "Barat",
    sales: "Vira",
    expedition: "Internal fleet",
    lines: [{
      line_idx: 0,
      line_id: `${id}-l0`,
      name: "Cup",
      unit: "pcs",
      remaining_qty: remaining,
      ordered_qty: remaining,
      shipped_qty: 0,
    }],
    can_create_delivery: true,
    total_remaining: remaining,
    suggested_sj_number: `SJ/${id}`,
    next_shipment_index: 1,
  };
}

async function typeInto(el, value) {
  await act(async () => {
    el.focus();
    const proto = el instanceof HTMLTextAreaElement
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("Recipient WhatsApp on Buat Pengiriman", () => {
  let container;
  let root;

  beforeEach(() => {
    jest.spyOn(api, "listSalesDeliveries").mockResolvedValue({ deliveries: [] });
    jest.spyOn(api, "salesDeliveryFilterOptions").mockResolvedValue({ created_by_options: [] });
    jest.spyOn(api, "listSalesOrders").mockResolvedValue({
      orders: [
        { id: "po-1", po_number: "PO-1", customer: "PT A", delivery_summary: { total_remaining: 10 }, total_quantity: 10 },
        { id: "po-2", po_number: "PO-2", customer: "PT B", delivery_summary: { total_remaining: 8 }, total_quantity: 8 },
      ],
    });
    jest.spyOn(api, "getPoDeliverySummary").mockImplementation(async (id) => (
      id === "po-2" ? summaryFor("po-2", "081222222222", 8) : summaryFor(id, id === "po-empty" ? "" : "081111111111")
    ));
    jest.spyOn(api, "createSalesDelivery").mockResolvedValue({ delivery: { id: "d-new" }, message: "ok" });
    jest.spyOn(api, "getSalesDelivery").mockResolvedValue({ delivery: { id: "d-new" } });
    require("../lib/auth").useAuth.mockReturnValue({
      user: { id: "u1", role: "admin" },
      hasPerm: () => true,
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.restoreAllMocks();
  });

  async function renderCreate(entry) {
    const query = String(entry || "").includes("?") ? String(entry).split("?")[1] : "";
    resetRouter(query);
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[entry]}>
          <PengirimanPesanan />
        </MemoryRouter>,
      );
    });
    await flush();
    await flush();
  }

  test("normalizes Indonesian WhatsApp digits the same way as the backend helper", () => {
    expect(recipientWhatsappDigits("0812-3456-7890")).toBe("6281234567890");
    expect(recipientWhatsappDigits("+62 812 3456 7890")).toBe("6281234567890");
    expect(recipientWhatsappDigits("6281234567890")).toBe("6281234567890");
    expect(recipientWhatsappDigits("")).toBe("");
    expect(recipientWhatsappDigits("123")).toBe("");
    expect(recipientWhatsappDigits("-")).toBe("");
  });

  test("customer mode is read-only, manual mode accepts typing, and invalid blocks submit", async () => {
    await renderCreate("/penjualan/pengiriman-pesanan?action=create&po_id=po-1");
    const customerRadio = container.querySelector('[data-testid="phone-source-customer"]');
    const manualRadio = container.querySelector('[data-testid="phone-source-manual"]');
    expect(customerRadio.checked).toBe(true);
    expect(customerRadio.disabled).toBe(false);
    expect(container.querySelector('[data-testid="display-customer-phone"]').value).toBe("081111111111");
    expect(container.querySelector('[data-testid="display-customer-phone"]').readOnly).toBe(true);
    api.createSalesDelivery.mockRejectedValueOnce({ response: { data: { detail: "tahan di form" } } });
    await act(async () => {
      container.querySelector('button[type="submit"]').click();
    });
    await flush();
    expect(api.createSalesDelivery.mock.calls[0][0].recipient_phone_source).toBe("customer");
    expect(api.createSalesDelivery.mock.calls[0][0].recipient_phone).toBeUndefined();
    expect(api.createSalesDelivery.mock.calls[0][0].line_items[0].qty).toBe(10);
    api.createSalesDelivery.mockResolvedValue({ delivery: { id: "d-new" }, message: "ok" });

    await act(async () => {
      manualRadio.click();
    });
    await flush();
    const input = container.querySelector('[data-testid="input-recipient-phone"]');
    expect(document.activeElement === input || input).toBeTruthy();
    await typeInto(input, "0812");
    expect(input.value).toBe("0812");
    expect(document.activeElement).toBe(input);
    await typeInto(input, "081234567890");
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe("081234567890");

    await act(async () => {
      customerRadio.click();
    });
    await flush();
    expect(container.querySelector('[data-testid="display-customer-phone"]').value).toBe("081111111111");
    await act(async () => {
      manualRadio.click();
    });
    await flush();
    expect(container.querySelector('[data-testid="input-recipient-phone"]').value).toBe("081234567890");

    api.createSalesDelivery.mockClear();
    await typeInto(container.querySelector('[data-testid="input-recipient-phone"]'), "12");
    await act(async () => {
      container.querySelector('button[type="submit"]').click();
    });
    await flush();
    expect(api.createSalesDelivery).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="recipient-phone-error"]').textContent).toMatch(/tidak valid/);

    await typeInto(container.querySelector('[data-testid="input-recipient-phone"]'), "");
    await act(async () => {
      container.querySelector('button[type="submit"]').click();
    });
    await flush();
    const payload = api.createSalesDelivery.mock.calls[0][0];
    expect(payload.recipient_phone_source).toBe("manual");
    expect(payload.recipient_phone).toBe("");
    expect(payload.line_items).toEqual([
      expect.objectContaining({ line_idx: 0, line_id: "po-1-l0", qty: 10 }),
    ]);
  });

  test("empty customer phone disables customer mode", async () => {
    await renderCreate("/penjualan/pengiriman-pesanan?action=create&po_id=po-empty");
    const customerRadio = container.querySelector('[data-testid="phone-source-customer"]');
    expect(customerRadio.disabled).toBe(true);
    expect(customerRadio.checked).toBe(false);
    expect(container.querySelector('[data-testid="input-recipient-phone"]')).toBeTruthy();
  });

  test("changing PO resets the manual number", async () => {
    await renderCreate("/penjualan/pengiriman-pesanan?action=create&po_id=po-1");
    await act(async () => {
      container.querySelector('[data-testid="phone-source-manual"]').click();
    });
    await flush();
    await typeInto(container.querySelector('[data-testid="input-recipient-phone"]'), "081999999999");
    expect(container.querySelector('[data-testid="input-recipient-phone"]').value).toBe("081999999999");
    await act(async () => {
      resetRouter("action=create&po_id=po-2");
    });
    await flush();
    await flush();
    expect(container.querySelector('[data-testid="input-recipient-phone"]')).toBeNull();
    expect(container.querySelector('[data-testid="display-customer-phone"]').value).toBe("081222222222");
    expect(container.querySelector('[data-testid="phone-source-customer"]').checked).toBe(true);
  });
});

describe("PWA WhatsApp follows the selected Surat Jalan", () => {
  function renderDelivery(order, shipmentId) {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(
        <MobileDeliveryWorkspace order={order} canEdit={false} shipmentId={shipmentId} onUpdated={() => {}} />,
      );
    });
    return { container, root };
  }

  const order = {
    id: "po-wa",
    po_number: "PO-WA",
    customer: "PT ABC",
    customer_phone: "089999999999",
    address: "Jl. Melati 1",
    delivery_status: "Belum Terkirim",
    line_items: [{ name: "Cup", quantity: 10, line_id: "l0" }],
    shipments: [
      { id: "sj-a", number: "SJ-A", lines: [{ line_idx: 0, qty: 4 }], recipient_phone_wa: "628111111111" },
      { id: "sj-b", number: "SJ-B", lines: [{ line_idx: 0, qty: 6 }], recipient_phone_wa: "628222222222" },
    ],
    loading_confirmations: [],
    delivery_proofs: [],
  };

  beforeEach(() => {
    require("../lib/auth").useAuth.mockReturnValue({
      user: { id: "driver-1", role: "driver" },
      hasPerm: () => true,
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  test("SJ A and SJ B use their own numbers, and an unmatched id shows no link", () => {
    const first = renderDelivery(order, "sj-a");
    const linkA = first.container.querySelector('[data-testid="mobile-delivery-whatsapp"]');
    expect(linkA.getAttribute("href")).toBe("https://wa.me/628111111111");
    expect(linkA.getAttribute("target")).toBe("_blank");
    expect(linkA.getAttribute("rel")).toBe("noopener noreferrer");
    expect(linkA.getAttribute("href")).not.toContain("089999999999");
    const maps = first.container.querySelector(".mop-delivery-maps");
    expect(maps.textContent).toBe("Buka Maps");
    expect(maps.getAttribute("href")).toContain("maps");
    expect(maps.compareDocumentPosition(linkA) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    act(() => first.root.unmount());
    first.container.remove();

    const second = renderDelivery(order, "sj-b");
    expect(second.container.querySelector('[data-testid="mobile-delivery-whatsapp"]').getAttribute("href"))
      .toBe("https://wa.me/628222222222");
    act(() => second.root.unmount());
    second.container.remove();

    const missing = renderDelivery(order, "sj-missing");
    expect(missing.container.querySelector('[data-testid="mobile-delivery-whatsapp"]')).toBeNull();
    expect(missing.container.querySelector(".mop-delivery-maps")).toBeTruthy();
    act(() => missing.root.unmount());
    missing.container.remove();

    const empty = renderDelivery({
      ...order,
      shipments: [{ id: "sj-a", number: "SJ-A", lines: [{ line_idx: 0, qty: 10 }] }],
    }, "sj-a");
    expect(empty.container.querySelector('[data-testid="mobile-delivery-whatsapp"]')).toBeNull();
    expect(empty.container.querySelector(".mop-delivery-maps").getAttribute("href")).toContain("Melati");
    act(() => empty.root.unmount());
    empty.container.remove();
  });
});
