import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, __reset as resetRouter } from "react-router-dom";
import PengirimanPesanan, {
  recipientWhatsappDigits,
  deliveryListSalesLabel,
  computeDeliveryStatusCounts,
  deliveryStatusCountsFromResponse,
  deliveryListRangeLabel,
  DELIVERY_DEFAULT_PAGE_SIZE,
  DELIVERY_PAGE_SIZES,
} from "./PengirimanPesanan";
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
    expect(src).toMatch(/sd-delivery-detail-head/);
    expect(src).toMatch(/sd-delivery-detail-body/);
    expect(src).toMatch(/sd-delivery-detail-foot/);
    expect(src).toMatch(/data-testid="delivery-detail-body"/);
    expect(src).toMatch(/Cetak PDF/);
    expect(src).toMatch(/Riwayat Pengiriman untuk PO/);
  });

  test("registers /penjualan/pengiriman-pesanan in App.js router", () => {
    expect(appSrc).toMatch(/path="\/penjualan\/pengiriman-pesanan"/);
    expect(appSrc).toMatch(/<PengirimanPesanan \/>/);
    expect(appSrc).toMatch(/anyOf=\{\["sales\.delivery\.view"\]\}/);
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

  test("after soft-cancel SJ, shows related Loading follow-up and Hapus CTA gated by loading.delete", () => {
    expect(src).toMatch(/deletableLoadingConfirmationsForCancelledSj/);
    expect(src).toMatch(/refreshLoadingFollowUp/);
    expect(src).toMatch(/loading-followup-modal/);
    expect(src).toMatch(/btn-hapus-loading-followup-/);
    expect(src).toMatch(/Hapus Loading terkait/);
    expect(src).toMatch(/LOADING_DELETE_PERM/);
    expect(src).toMatch(/canDeleteLoading/);
    expect(src).toMatch(/api\.deleteLoadingConfirm/);
    expect(src).toMatch(/if \(loadingFollowUpBusyId\) return/);
    expect(src).toMatch(/payload\?\.order \|\| payload/);
    expect(src).toMatch(/loading-followup-delete-confirm/);
    expect(src).toMatch(/requestDeleteRelatedLoading/);
    expect(src).toMatch(/if \(loadingFollowUpDeleteConfirm\) return/);
    expect(src).toMatch(/btn-confirm-loading-followup-delete/);
    expect(src).toMatch(/Hapus konfirmasi Loading/);
    expect(src).toMatch(/formatLoadingDeleteError/);
    expect(src).toMatch(/status === 403/);
    expect(src).toMatch(/status === 404/);
    expect(src).toMatch(/status === 409/);
    expect(src).toMatch(/MSG_LOADING_DELETE_SJ_UNRESOLVED/);
    expect(src).toMatch(/clearLoadingFollowUpUi/);
    expect(src).toMatch(/formatDesktopListDate/);
    expect(src).not.toMatch(/formatDisplayDate\(/);
  });

  test("renders required helper texts and read-only attributes for PO and SJ numbers", () => {
    expect(src).toMatch(/ux90-pj-delivery/);
    expect(src).toMatch(/Alokasi Qty Surat Jalan/);
    expect(src).toMatch(/Satu PO dapat memiliki beberapa Surat Jalan/);
    expect(src).toMatch(/data-testid="display-po-number"/);
    expect(src).toMatch(/data-testid="display-suggested-sj"/);
    expect(src).toMatch(/data-testid="display-customer"/);
    expect(src).toMatch(/SalesInvoiceDateField/);
    expect(src).toMatch(/testId="input-delivery-date"/);
    expect(src).toMatch(/data-testid="input-delivery-outlet"/);
    expect(src).toMatch(/data-testid="input-delivery-recipient"/);
    expect(src).toMatch(/data-testid="input-delivery-sales"/);
    expect(src).toMatch(/readOnly[\s\S]*data-testid="input-delivery-sales"|data-testid="input-delivery-sales"[\s\S]*readOnly/);
    expect(src).not.toMatch(/onChange=\{\(e\) => setFormData\(\(p\) => \(\{ \.\.\.p, sales: e\.target\.value \}\)\)\}/);
    expect(src).toMatch(/authoritativeSalesLabel|operationalSalesSnapshotLabel/);
    expect(src).toMatch(/data-testid="input-delivery-expedition"/);
    expect(src).toMatch(/data-testid="input-delivery-zone"/);
    expect(src).toMatch(/data-testid="input-delivery-address"/);
    expect(src).toMatch(/data-testid="input-delivery-notes"/);
  });

  test("CREATE SJ Rincian table remains scrollable under multi-PO header (layout regression)", () => {
    // Multi-PO searchable picker uses CSS-capped class (not inline maxHeight:160 that starved the body).
    expect(src).toMatch(/data-testid="multi-po-picker"/);
    expect(src).toMatch(/className="ux90-multi-po-picker"/);
    expect(src).toMatch(/LookupMultiSearchSelect/);
    expect(src).toMatch(/testId="multi-po-lookup"/);
    expect(src).not.toMatch(/\.slice\(0,\s*40\)/);
    expect(src).toMatch(/ux90-table-scroll/);
    expect(src).toMatch(/ux90-delivery-doc-header/);
    expect(src).toMatch(/ux90-delivery-doc-body/);
    expect(src).toMatch(/ux90-pj-delivery-host/);
    expect(src).not.toMatch(/maxHeight:\s*160/);

    const css = fs.readFileSync(path.join(__dirname, "ux90SalesCreate.css"), "utf8");
    // Host table-wrap must shrink (min-height 0) so overflow:hidden ancestors do not clip tbody.
    expect(css).toMatch(
      /\.ux90-pj-delivery-host[\s\S]*?\[data-form-tab="items"\][\s\S]*?delivery-lines[\s\S]*?min-height:\s*0\s*!important/
    );
    // Legacy non-host min-height 220 must not win over host shrink rule.
    expect(css).toMatch(/\.ux90-pj-delivery\s+\.ux90-table-wrap\[data-ux90-region="delivery-lines"\]\s*\{[^}]*min-height:\s*220px/);
    // Multi-PO picker + header capped so Rincian/footer stay reachable.
    expect(css).toMatch(/\.ux90-pj-delivery-host\s+\.ux90-multi-po-picker\s*\{[^}]*max-height:\s*min\(112px,\s*14vh\)\s*!important/);
    expect(css).toMatch(/\.ux90-pj-delivery-host\s+\.ux90-delivery-doc-header\s*\{[^}]*max-height:\s*min\(42vh,\s*280px\)\s*!important/);
    expect(css).toMatch(/\.ux90-pj-delivery-host\s+\.ux90-table-scroll\s*\{[^}]*overflow:\s*auto\s*!important/);
    expect(css).toMatch(/\.ux90-pj-delivery-host\s+\.ux90-pj-delivery\s+\.ux90-table\s+thead\s+th\s*\{[^}]*position:\s*sticky/);
    expect(css).toMatch(/\.ux90-pj-delivery-host\s+\.ux90-doc-summary\.is-delivery-compact\s*\{[^}]*flex:\s*0\s+0\s+auto\s*!important/);
    // Footer stays in flex flow (not overlaying table rows).
    expect(css).toMatch(
      /\.ux90-pj-delivery-host\s+\.ux90-pj-delivery\s+\.ux90-footer[\s\S]*?flex:\s*0\s+0\s+auto\s*!important/
    );
  });

  test("multi-PO create uses searchable select + authoritative qty summary + customer_id gate", () => {
    expect(src).toMatch(/fetchMultiPoOptions/);
    expect(src).toMatch(/applyMultiPoSelection/);
    expect(src).toMatch(/multiPoQtySummary/);
    expect(src).toMatch(/summarizeMultiPoDeliveryQty/);
    expect(src).toMatch(/anchorMultiCustomerId/);
    expect(src).toMatch(/customer_id/);
    expect(src).toMatch(/data-testid="multi-po-total-ordered"/);
    expect(src).toMatch(/data-testid="multi-po-total-shipped"/);
    expect(src).toMatch(/data-testid="multi-po-remaining-after"/);
    expect(src).toMatch(/status:\s*"aktif"/);
    expect(src).toMatch(/MULTI_PO_LOOKUP_PAGE_SIZE\s*=\s*40/);
    expect(src).toMatch(/page_size:\s*pageSize|MULTI_PO_LOOKUP_PAGE_SIZE/);
    expect(src).toMatch(/do not preload all aktif POs/);
    expect(src).not.toMatch(/listSalesOrders\(\{\s*status:\s*"aktif"\s*\}\)/);
    expect(src).not.toMatch(/multi-po-check-/);
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
    expect(src).toMatch(/id:\s*"all",\s*label:\s*"Semua"/);
    expect(src).toMatch(/id:\s*"SEBAGIAN",\s*label:\s*"Sebagian"/);
    expect(src).toMatch(/id:\s*"SELESAI",\s*label:\s*"Selesai"/);
    expect(src).not.toMatch(/id:\s*"Dikirim"/);
    expect(src).not.toMatch(/id:\s*"Terkirim"/);
    expect(src).toMatch(/computeDeliveryStatusCounts/);
    expect(src).toMatch(/filter-status-count-/);
    expect(src).toMatch(/delivery-sales-/);
    expect(src).toMatch(/deliveryListSalesLabel/);
    expect(src).toMatch(/sd-act-pdf/);
    expect(src).toMatch(/sd-actions-main/);
    expect(src).toMatch(/sd-delivery-list/);
    expect(src).toMatch(/sd-delivery-tabs/);
    expect(src).toMatch(/refresh-delivery-btn/);
    expect(src).toMatch(/reloadDeliveryListAuthoritative/);
    expect(src).toMatch(/page_size/);
    expect(src).toMatch(/delivery-page-size/);
    expect(src).toMatch(/delivery-page-prev/);
    expect(src).toMatch(/deliveryStatusCountsFromResponse/);
    expect(src).not.toMatch(/deliveryListSalesLabel\(d\) \|\| d\.created_by/);

    // Detail modal SJ KPI (canonical multi-PO qty) + legacy testid aliases
    expect(src).toMatch(/data-testid="delivery-sj-kpi-summary"/);
    expect(src).toMatch(/TOTAL QTY SJ/);
    expect(src).toMatch(/JUMLAH PO SUMBER/);
    expect(src).toMatch(/QTY DITERIMA/);
    expect(src).toMatch(/LOADING \/ POD/);
    expect(src).toMatch(/detail-total-sj-qty/);
    expect(src).toMatch(/sj-source-alloc-table/);
    expect(src).toMatch(/data-testid="delivery-po-progress-summary"/);
    expect(src).toMatch(/detail-total-po-qty/);
    expect(src).toMatch(/detail-cumulative-sj-qty/);
    expect(src).toMatch(/detail-remaining-po-qty/);
    expect(src).toMatch(/detail-delivery-status/);
    expect(src).toMatch(/sj-loading-confirm-all/);
    expect(src).toMatch(/sj-confirm-delivered-all/);
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
    const v = salesNavVisible(has(NAV_KEYS.sales, NAV_KEYS.salesDeliveryOrder, "sales.delivery.view"));
    expect(v.parent).toBe(true);
    expect(v.salesDeliveryOrder).toBe(true);
  });

  test("salesMenuItems renders Pengiriman Pesanan tile", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesDeliveryOrder, "sales.delivery.view"));
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
    expect(src).toMatch(/compact/);
    expect(src).not.toMatch(/saveDeliveryOutlet/);
    expect(picker).not.toMatch(/saveDeliveryOutlet/);
    expect(picker).not.toMatch(/buildSaveDeliveryOutletPayload/);
    expect(picker).toMatch(/Titik Lokasi Pengiriman/);
    expect(picker).toMatch(/Menggunakan titik lokasi PO \(fallback\)/);
    expect(picker).toMatch(/Menggunakan titik lokasi Surat Jalan/);
    expect(picker).toMatch(/sj-location-clear/);
    expect(picker).toMatch(/Hapus override — kembali ke lokasi PO/);
    expect(picker).toMatch(/OutletLocationMap/);
    expect(picker).toMatch(/compact/);
    expect(picker).toMatch(/Alamat Terpilih/);
    expect(picker).toMatch(/sj-loc-maps-link/);
    expect(picker).toMatch(/sj-loc-use-maps-link/);
    expect(picker).toMatch(/searchDeliveryPlaces/);
    expect(picker).toMatch(/resolveDeliveryMapsLink/);
    expect(modal).toMatch(/saveDeliveryOutlet/);
  });

  test("create form uses compact picker without Alamat Terpilih panel or map preview", () => {
    expect(src).toMatch(/<SjDeliveryLocationPicker[\s\S]*compact/);
    expect(picker).toMatch(/data-compact=\{compact \? "true" : "false"\}/);
    // Compact path keeps short selected line; full "Alamat Terpilih" label only when !compact
    expect(picker).toMatch(/compact \? \(/);
    expect(picker).toMatch(/!compact|compact \?/);
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

describe("Delivery list desktop UX 9.0 — Sales, actions, status counts", () => {
  test("deliveryListSalesLabel prefers sales fields and never invents created_by", () => {
    expect(deliveryListSalesLabel({ sales: "Vira", sales_name: "Vira Sales", created_by_name: "Admin" })).toBe("Vira Sales");
    expect(deliveryListSalesLabel({ sales: "Vira", created_by_name: "Admin" })).toBe("Vira");
    expect(deliveryListSalesLabel({ created_by_name: "Admin", created_by: "u1" })).toBe("");
    expect(deliveryListSalesLabel({})).toBe("");
  });

  test("computeDeliveryStatusCounts totals filter-context rows without guessing", () => {
    expect(computeDeliveryStatusCounts([
      { status: "SEBAGIAN" },
      { status: "SELESAI" },
      { status: "SELESAI" },
      { status: "sebagian" },
    ])).toEqual({ all: 4, SEBAGIAN: 2, SELESAI: 2 });
    expect(computeDeliveryStatusCounts([])).toEqual({ all: 0, SEBAGIAN: 0, SELESAI: 0 });
    expect(computeDeliveryStatusCounts(null)).toEqual({ all: 0, SEBAGIAN: 0, SELESAI: 0 });
  });

  test("delivery pagination helpers match Pesanan-style defaults", () => {
    expect(DELIVERY_DEFAULT_PAGE_SIZE).toBe(50);
    expect(DELIVERY_PAGE_SIZES).toEqual([10, 25, 50, 100]);
    expect(deliveryListRangeLabel(120, 2, 50)).toBe("Menampilkan 51–100 dari 120 pengiriman");
    expect(deliveryStatusCountsFromResponse({
      counts: { all: 4, SEBAGIAN: 2, SELESAI: 2 },
    })).toEqual({ all: 4, SEBAGIAN: 2, SELESAI: 2 });
    expect(deliveryStatusCountsFromResponse({})).toBeNull();
  });

  let container;
  let root;

  beforeEach(() => {
    jest.spyOn(api, "salesDeliveryFilterOptions").mockResolvedValue({ created_by_options: [] });
    jest.spyOn(api, "listSalesOrders").mockResolvedValue({ orders: [] });
    require("../lib/auth").useAuth.mockReturnValue({
      user: { id: "u1", role: "sales" },
      hasPerm: (k) => (
        k === "sales.delivery.view"
        || k === "sales.delivery.print"
        || k === "sales_order.view"
      ),
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

  async function renderList() {
    resetRouter("");
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/penjualan/pengiriman-pesanan"]}>
          <PengirimanPesanan />
        </MemoryRouter>,
      );
    });
    await flush();
    await flush();
  }

  test("shows Sales from list payload, status counts, and keeps PDF secondary without Batal when unauthorized", async () => {
    const listSpy = jest.spyOn(api, "listSalesDeliveries").mockResolvedValue({
      deliveries: [
        {
          id: "d1",
          sj_number: "SJ-1",
          po_id: "po-1",
          po_number: "PO-1",
          customer: "PT A",
          outlet: "Outlet A",
          sales: "Vira",
          sales_name: "Vira",
          created_by_name: "Admin Creator",
          delivery_date: "2026-10-16",
          total_qty: 10,
          status: "SEBAGIAN",
        },
        {
          id: "d2",
          sj_number: "SJ-2",
          po_id: "po-2",
          po_number: "PO-2",
          customer: "PT B",
          outlet: "Outlet B",
          sales: "",
          created_by_name: "Admin Creator",
          delivery_date: "2026-10-16",
          total_qty: 5,
          status: "SELESAI",
        },
      ],
      count: 2,
      total: 2,
      page: 1,
      page_size: 50,
      total_pages: 1,
      counts: { all: 2, SEBAGIAN: 1, SELESAI: 1 },
    });

    await renderList();

    expect(container.querySelector('[data-testid="delivery-sales-d1"]').textContent).toMatch(/Sales · Vira/);
    expect(container.querySelector('[data-testid="delivery-sales-d1"]').textContent).not.toMatch(/Admin Creator/);
    expect(container.querySelector('[data-testid="delivery-sales-d2"]').textContent).toMatch(/Sales · —/);
    expect(container.querySelector('[data-testid="filter-status-count-all"]').textContent).toBe("2");
    expect(container.querySelector('[data-testid="filter-status-count-SEBAGIAN"]').textContent).toBe("1");
    expect(container.querySelector('[data-testid="filter-status-count-SELESAI"]').textContent).toBe("1");
    expect(container.querySelector('[data-testid="refresh-delivery-btn"]')).toBeTruthy();
    expect(container.querySelector(".sd-delivery-tabs")).toBeTruthy();
    expect(container.querySelector('[data-testid="delivery-page-size"]').value).toBe("50");
    expect(container.querySelector('[data-testid="delivery-list-range"]').textContent).toMatch(/Menampilkan 1–2 dari 2 pengiriman/);
    expect(container.querySelector('[data-testid="btn-view-delivery-d1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="btn-print-delivery-d1"]')?.className).toMatch(/sd-act-pdf/);
    expect(container.querySelector('[data-testid="btn-batal-delivery-d1"]')).toBeNull();
    expect(listSpy).toHaveBeenCalled();
    expect(listSpy.mock.calls[0][0]).toMatchObject({ page: 1, page_size: 50 });
    expect(listSpy.mock.calls[0][0]).not.toHaveProperty("status");
  });

  test("when status filter is active, one list call returns page rows and filter-context counts", async () => {
    const listSpy = jest.spyOn(api, "listSalesDeliveries").mockResolvedValue({
      deliveries: [{
        id: "d1",
        sj_number: "SJ-1",
        po_id: "po-1",
        po_number: "PO-1",
        customer: "PT A",
        sales: "Vira",
        delivery_date: "2026-10-16",
        total_qty: 10,
        status: "SEBAGIAN",
      }],
      count: 1,
      total: 1,
      page: 1,
      page_size: 50,
      total_pages: 1,
      counts: { all: 2, SEBAGIAN: 1, SELESAI: 1 },
    });

    resetRouter("status=SEBAGIAN");
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/penjualan/pengiriman-pesanan?status=SEBAGIAN"]}>
          <PengirimanPesanan />
        </MemoryRouter>,
      );
    });
    await flush();
    await flush();

    expect(listSpy).toHaveBeenCalled();
    expect(listSpy.mock.calls.every((c) => c[0]?.status === "SEBAGIAN")).toBe(true);
    expect(listSpy.mock.calls.some((c) => !c[0]?.status)).toBe(false);
    expect(container.querySelector('[data-testid="filter-status-count-all"]').textContent).toBe("2");
    expect(container.querySelector('[data-testid="filter-status-count-SEBAGIAN"]').textContent).toBe("1");
    expect(container.querySelector('[data-testid="filter-status-count-SELESAI"]').textContent).toBe("1");
    expect(container.querySelectorAll('[data-testid^="delivery-row-"]').length).toBe(1);
  });

  test("continuous typing in list search keeps focus", async () => {
    jest.spyOn(api, "listSalesDeliveries").mockResolvedValue({
      deliveries: [],
      count: 0,
      total: 0,
      page: 1,
      page_size: 50,
      counts: { all: 0, SEBAGIAN: 0, SELESAI: 0 },
    });
    await renderList();
    const input = container.querySelector('[data-testid="search-deliveries-input"]');
    expect(input).toBeTruthy();
    await typeInto(input, "ABCDE123");
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe("ABCDE123");
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

describe("Pengiriman CREATE layout scroll ownership (multi-PO safe)", () => {
  const cssSrc = fs.readFileSync(path.join(__dirname, "ux90SalesCreate.css"), "utf8");
  const pageSrc = fs.readFileSync(path.join(__dirname, "PengirimanPesanan.jsx"), "utf8");

  test("create shell keeps footer outside clipped workspace and owns table scroll", () => {
    expect(pageSrc).toMatch(/ux90-pj-delivery-host/);
    expect(pageSrc).toMatch(/data-testid="delivery-lines-scroll"/);
    expect(pageSrc).toMatch(/className="ux90-multi-po-picker"/);
    expect(pageSrc).toMatch(/data-testid="multi-po-picker"/);
    expect(pageSrc).toMatch(/data-testid="btn-cancel-delivery"/);
    expect(pageSrc).toMatch(/data-testid="btn-submit-delivery-order"/);
    expect(pageSrc).toMatch(/ux90-footer sd-form-footer/);
    // Footer must remain a sibling of workspace (not nested inside overflow:hidden body)
    const createForm = pageSrc.slice(
      pageSrc.indexOf('viewMode === "create" &&'),
      pageSrc.indexOf('{/* VIEW: LIST DELIVERIES */}'),
    );
    expect(createForm.indexOf("ux90-workspace")).toBeGreaterThan(-1);
    expect(createForm.indexOf("ux90-footer sd-form-footer")).toBeGreaterThan(
      createForm.indexOf("</div>\n          </div>\n\n          <div className=\"ux90-footer"),
    );
  });

  test("host CSS caps multi-PO header and allows table flex shrink (no tbody clip)", () => {
    expect(cssSrc).toMatch(/\.ux90-pj-delivery-host \.ux90-multi-po-picker/);
    expect(cssSrc).toMatch(/max-height:\s*min\(112px,\s*14vh\)/);
    expect(cssSrc).toMatch(/\.ux90-pj-delivery-host \.ux90-delivery-doc-header/);
    expect(cssSrc).toMatch(/max-height:\s*min\(42vh,\s*280px\)/);
    // Critical regression guard: host items-tab table wrap must shrink (min-height: 0)
    expect(cssSrc).toContain(
      '.ux90-pj-delivery-host .ux90-pj-delivery[data-form-tab="items"] .ux90-table-wrap[data-ux90-region="delivery-lines"]',
    );
    const itemsTableBlock = cssSrc.slice(
      cssSrc.indexOf(
        '.ux90-pj-delivery-host .ux90-pj-delivery[data-form-tab="items"] .ux90-table-wrap[data-ux90-region="delivery-lines"]',
      ),
      cssSrc.indexOf('.ux90-pj-delivery-host .ux90-table-wrap[data-ux90-region="delivery-lines"][hidden]'),
    );
    expect(itemsTableBlock).toMatch(/min-height:\s*0\s*!important/);
    expect(itemsTableBlock).not.toMatch(/min-height:\s*180px/);
    expect(cssSrc).toContain(".ux90-pj-delivery-host .ux90-table-scroll");
    expect(cssSrc).toMatch(/\.ux90-pj-delivery-host \.ux90-table-scroll\s*\{[^}]*overflow:\s*auto\s*!important/s);
    expect(cssSrc).toMatch(
      /\.ux90-pj-delivery-host \.ux90-pj-delivery \.ux90-table thead th\s*\{[^}]*position:\s*sticky\s*!important/s,
    );
    expect(cssSrc).toMatch(
      /\.ux90-pj-delivery-host \.ux90-doc-summary\.is-delivery-compact\s*\{[^}]*flex:\s*0 0 auto\s*!important/s,
    );
  });

  test("multi-PO submit is not gated on single-PO poSummary null", () => {
    expect(pageSrc).toContain("multiPoReadyForSubmit");
    expect(pageSrc).toContain("multiPoSummariesReady");
    expect(pageSrc).toMatch(/multiPoMode\s*\?\s*!multiPoReadyForSubmit/);
    expect(pageSrc).toMatch(/Ringkasan PO belum lengkap/);
    expect(pageSrc).not.toContain(
      "disabled={formBusy || !poSummary || totalShippingQty <= 0 || overshipmentErrors.length > 0}",
    );
  });

  test("multi-PO customer safety: incomplete summary / BE reject preserve selection", () => {
    expect(pageSrc).toMatch(/multiPoReadyForSubmit/);
    expect(pageSrc).toMatch(/validateMultiSourceSelection\(poSummariesById\)/);
    expect(pageSrc).toMatch(/customer_id kanonik|customer_id\)/);
    // Create failure only sets formErr — does not clear selectedPoIds / line_qtys.
    const catchIdx = pageSrc.indexOf("} catch (err) {");
    const finallyIdx = pageSrc.indexOf("} finally {", catchIdx);
    expect(catchIdx).toBeGreaterThan(-1);
    expect(finallyIdx).toBeGreaterThan(catchIdx);
    const catchBlock = pageSrc.slice(catchIdx, finallyIdx);
    expect(catchBlock).toMatch(/setFormErr/);
    expect(catchBlock).toMatch(/Gagal membuat Pengiriman Pesanan/);
    expect(catchBlock).not.toMatch(/setSelectedPoIds/);
    expect(catchBlock).not.toMatch(/setPoSummariesById/);
    expect(catchBlock).not.toMatch(/line_qtys/);
    expect(pageSrc).not.toMatch(/\.slice\(0,\s*40\)/);
    expect(pageSrc).toMatch(/LookupMultiSearchSelect/);
  });
});
