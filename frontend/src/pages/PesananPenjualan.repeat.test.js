import {
  REPEAT_NO_PREVIOUS_MESSAGE,
  REPEAT_ORDER_QUERY_PARAM,
  clearRepeatDerivedFormFields,
  mapRepeatSourceToFormPatch,
  resolveRepeatMasterCustomer,
} from "../lib/salesOrderRepeat";
import {
  PREVIEW_CUSTOMER_BAKSO_ROSSO_ID,
  PREVIEW_CUSTOMER_BAKSO_ROSSO_UTARA_ID,
  PREVIEW_CUSTOMER_INACTIVE_ID,
  PREVIEW_CUSTOMER_NO_HISTORY_ID,
  PREVIEW_REPEAT_ORDER_PARAM,
  PREVIEW_REPEAT_ORDER_VALUE,
  getPreviewMasterCustomers,
  getPreviewRepeatSource,
  isSalesOrderRepeatPreviewActive,
} from "../lib/salesOrderRepeatPreviewFixtures";
import {
  filterActiveMasterCustomers,
} from "../components/CustomerSearchSelect";
import fs from "fs";
import path from "path";

const TINY_PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

function sampleSource(overrides = {}) {
  return {
    source_po_number: "PO/10/09/26/001",
    source_created_at: "2026-09-10T10:00:00+00:00",
    source_order_date: "2026-09-09T00:00:00+00:00",
    customer: "PT Repeat",
    customer_id: "cust-1",
    customer_phone: "0812",
    outlet: "Pusat",
    recipient: "Budi",
    address: "Jl. A",
    delivery_zone: "Jakarta",
    payment_terms: "Net 30",
    sales: "Vira",
    expedition: "Internal fleet",
    estimated_delivery: "2026-09-12",
    deadline: "2026-09-12",
    notes: "Catatan",
    order_date: "2026-09-09",
    line_count: 2,
    design_file_count: 1,
    line_items: [
      {
        item_code: "PC-12",
        name: "Paper Cup 12oz",
        quantity: 1000,
        unit: "pcs",
        color: "Navy",
        production_process: "print",
        notes: "A",
        accurate_item_id: "acc-1",
        master_item_id: "mst-1",
      },
      {
        item_code: "PC-12",
        name: "Paper Cup 12oz",
        quantity: 500,
        unit: "pcs",
        color: "Merah",
        production_process: "print",
        notes: "B",
        accurate_item_id: "acc-1",
        master_item_id: "mst-1",
      },
    ],
    design_files: [
      { name: "design-a.png", data_url: TINY_PNG, content_type: "image/png" },
    ],
    ...overrides,
  };
}

describe("salesOrderRepeat mapping", () => {
  test("maps reusable fields and keeps selected customer authoritative", () => {
    const { formPatch, designFiles, sourcePoNumber } = mapRepeatSourceToFormPatch(
      sampleSource(),
      { customer_id: "cust-1", customer: "Selected Name" }
    );
    expect(formPatch.customer).toBe("Selected Name");
    expect(formPatch.customer_id).toBe("cust-1");
    expect(formPatch.outlet).toBe("Pusat");
    expect(formPatch.recipient).toBe("Budi");
    expect(formPatch.line_items).toHaveLength(2);
    expect(formPatch.line_items[0].name).toBe("Paper Cup 12oz");
    expect(formPatch.line_items[1].name).toBe("Paper Cup 12oz");
    expect(formPatch.line_items[0].color).toBe("Navy");
    expect(formPatch.line_items[1].color).toBe("Merah");
    expect(formPatch.line_items[0].id).toBeUndefined();
    expect(formPatch.po_number).toBeUndefined();
    expect(formPatch.current_stage).toBeUndefined();
    expect(formPatch.discount).toBeUndefined();
    expect(formPatch.discount_amount).toBeUndefined();
    expect(designFiles).toEqual([]);
    expect(sourcePoNumber).toBe("PO/10/09/26/001");
  });

  test("does not copy customer_phone or old dates", () => {
    const { formPatch } = mapRepeatSourceToFormPatch(sampleSource(), {
      customer_id: "cust-1",
      customer: "Selected Name",
    });
    expect(formPatch.customer_phone).toBeUndefined();
    expect(formPatch.order_date).toBeUndefined();
    expect(formPatch.estimated_delivery).toBeUndefined();
    expect(formPatch.deadline).toBeUndefined();
  });

  test("rejects mismatched customer_id (in-flight / stale guard)", () => {
    const result = mapRepeatSourceToFormPatch(sampleSource({ customer_id: "cust-old" }), {
      customer_id: "cust-new",
      customer: "New",
    });
    expect(result.formPatch).toBeNull();
    expect(result.designFiles).toEqual([]);
  });

  test("does not copy artwork files", () => {
    const { designFiles, formPatch } = mapRepeatSourceToFormPatch(sampleSource(), {
      customer_id: "cust-1",
      customer: "PT Repeat",
    });
    expect(designFiles).toEqual([]);
    expect(formPatch.design_files).toBeUndefined();
  });

  test("same-name lines stay independent", () => {
    const { formPatch } = mapRepeatSourceToFormPatch(sampleSource(), {
      customer_id: "cust-1",
      customer: "PT Repeat",
    });
    expect(formPatch.line_items).toHaveLength(2);
    expect(formPatch.line_items[0].quantity).toBe(1000);
    expect(formPatch.line_items[1].quantity).toBe(500);
  });

  test("unlinked source can apply onto selected canonical customer", () => {
    const { formPatch } = mapRepeatSourceToFormPatch(
      sampleSource({ customer_id: null }),
      { customer_id: "cust-1", customer: "Selected Name" }
    );
    expect(formPatch.customer_id).toBe("cust-1");
    expect(formPatch.line_items).toHaveLength(2);
  });

  test("resolveRepeatMasterCustomer matches exact id then unique normalized name", () => {
    const masters = [
      { id: "cust-1", name: "PT Repeat" },
      { id: "cust-2", name: "PT Lain" },
    ];
    expect(resolveRepeatMasterCustomer(sampleSource(), masters).id).toBe("cust-1");
    expect(
      resolveRepeatMasterCustomer({ customer: "pt  repeat", customer_id: "" }, masters).id
    ).toBe("cust-1");
    expect(
      resolveRepeatMasterCustomer({ customer: "Unknown Co", customer_id: "" }, masters)
    ).toBeNull();
    expect(REPEAT_ORDER_QUERY_PARAM).toBe("repeat_order_id");
  });

  test("clearRepeatDerivedFormFields resets commercial fields", () => {
    const cleared = clearRepeatDerivedFormFields({
      customer: "A",
      customer_id: "cust-1",
      outlet: "X",
      notes: "old",
      line_items: [{ name: "Old", quantity: 9 }],
      po_number: "PO/KEEP",
      expedition: "Old fleet",
    });
    expect(cleared.customer).toBe("A");
    expect(cleared.customer_id).toBe("cust-1");
    expect(cleared.outlet).toBe("");
    expect(cleared.notes).toBe("");
    expect(cleared.line_items).toHaveLength(1);
    expect(cleared.line_items[0].name).toBe("");
    expect(cleared.expedition).toBe("Internal fleet");
    expect(cleared.po_number).toBe("PO/KEEP");
  });
});

describe("PesananPenjualan Repeat Order UI contracts", () => {
  const src = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
  const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const searchSrc = fs.readFileSync(
    path.join(__dirname, "../components/CustomerSearchSelect.jsx"),
    "utf8"
  );

  test("repeat button gated on customer_id and confirmation before apply", () => {
    expect(src).toMatch(/data-testid="btn-repeat-order"/);
    expect(src).toMatch(/disabled=\{!formData\.customer_id/);
    expect(src).toMatch(/api\.salesOrderRepeatSource/);
    expect(src).toMatch(/data-testid="repeat-confirm-modal"/);
    expect(src).toMatch(/Salin detail dari PO terakhir ini\?/);
    expect(src).toMatch(/Salin detail dari PO ini\?/);
    expect(src).toMatch(/Gunakan Repeat Order/);
    expect(src).toMatch(/data-testid="btn-repeat-cancel"/);
    expect(src).toMatch(/data-testid="btn-repeat-confirm"/);
    expect(src).toMatch(/handleRepeatSpecificPo/);
    expect(src).toMatch(/btn-repeat-order-\$\{o\.id\}/);
    expect(src).toMatch(/REPEAT_ORDER_QUERY_PARAM/);
    expect(src).toMatch(/mapRepeatSourceToFormPatch/);
  });

  test("source indicator and empty message present", () => {
    expect(src).toMatch(/data-testid="repeat-source-indicator"/);
    expect(src).toMatch(/Repeat dari PO:/);
    expect(src).toMatch(/data-testid="repeat-empty-message"/);
    expect(src).toMatch(/REPEAT_NO_PREVIOUS_MESSAGE/);
    expect(REPEAT_NO_PREVIOUS_MESSAGE).toMatch(/Tidak ada pesanan sebelumnya/);
  });

  test("customer change clears repeat state and guards in-flight responses", () => {
    expect(src).toMatch(/clearRepeatState\(\)/);
    expect(src).toMatch(/hadRepeat/);
    expect(src).toMatch(/selectedCustomerIdRef/);
    expect(src).toMatch(/repeatRequestCustomerIdRef/);
    expect(src).toMatch(/AbortController/);
  });

  test("submit uses canonical createSalesOrder and does not send artwork", () => {
    expect(src).toMatch(/api\.createSalesOrder\(payload\)/);
    expect(src).not.toMatch(/design_files: \(repeatDesignFiles/);
    expect(src).not.toMatch(/repeatDesignFiles/);
    expect(src).not.toMatch(/setForm\(source/);
    expect(src).not.toMatch(/setFormData\(source/);
    expect(apiSrc).toMatch(/salesOrderRepeatSource/);
    expect(apiSrc).toMatch(/salesOrderRepeatSourceByOrder/);
    expect(apiSrc).toMatch(/\/sales-orders\/repeat-source/);
  });

  test("does not copy discount or workflow identity fields into form", () => {
    expect(src).not.toMatch(/discount_amount/);
    expect(src).not.toMatch(/packing_status/);
    expect(src).not.toMatch(/surat_jalan/);
    expect(src).not.toMatch(/delivery_proofs/);
    expect(src).toMatch(/mapRepeatSourceToFormPatch\(/);
    expect(src).toMatch(/po_number: prev\.po_number/);
    expect(src).toMatch(/order_date: prev\.order_date/);
    expect(src).toMatch(/estimated_delivery: prev\.estimated_delivery/);
    expect(src).toMatch(/deadline: prev\.deadline/);
    expect(src).toMatch(/customer_phone: masterPatch\.customer_phone \|\| prev\.customer_phone/);
    const mapped = mapRepeatSourceToFormPatch(
      {
        source_po_number: "PO/X",
        customer_id: "c1",
        customer: "A",
        current_stage: 12,
        packing_status: "Selesai",
        discount_amount: 99,
        customer_phone: "000",
        order_date: "2020-01-01",
        estimated_delivery: "2020-01-02",
        deadline: "2020-01-03",
        line_items: [{ name: "X", quantity: 1, unit: "pcs", production_process: "print" }],
        design_files: [],
      },
      { customer_id: "c1", customer: "A" }
    );
    expect(mapped.formPatch.current_stage).toBeUndefined();
    expect(mapped.formPatch.packing_status).toBeUndefined();
    expect(mapped.formPatch.discount_amount).toBeUndefined();
    expect(mapped.formPatch.po_number).toBeUndefined();
    expect(mapped.formPatch.customer_phone).toBeUndefined();
    expect(mapped.formPatch.order_date).toBeUndefined();
    expect(mapped.formPatch.estimated_delivery).toBeUndefined();
    expect(mapped.formPatch.deadline).toBeUndefined();
    expect(mapped.designFiles).toEqual([]);
  });

  test("customer live search requires explicit selection for customer_id", () => {
    expect(src).toMatch(/CustomerSearchSelect/);
    expect(src).toMatch(/handleCustomerSelect/);
    expect(src).toMatch(/handleCustomerTextChange/);
    expect(src).not.toMatch(/list="customer-suggestions"/);
    expect(src).not.toMatch(/<datalist id="customer-suggestions"/);
    expect(searchSrc).toMatch(/onSelectCustomer/);
    expect(searchSrc).toMatch(/filterActiveMasterCustomers/);
    expect(searchSrc).toMatch(/isActiveMasterCustomer/);
    expect(searchSrc).toMatch(/data-testid=\{testId\}/);
  });
});

describe("salesOrderRepeatPreviewFixtures (dev-only)", () => {
  test("activation requires development env and exact query param", () => {
    const params = new URLSearchParams({ [PREVIEW_REPEAT_ORDER_PARAM]: PREVIEW_REPEAT_ORDER_VALUE });
    expect(isSalesOrderRepeatPreviewActive({ searchParams: params, nodeEnv: "development" })).toBe(true);
    expect(isSalesOrderRepeatPreviewActive({ searchParams: params, nodeEnv: "production" })).toBe(false);
    expect(isSalesOrderRepeatPreviewActive({ searchParams: params, nodeEnv: "test" })).toBe(false);
    expect(
      isSalesOrderRepeatPreviewActive({
        searchParams: new URLSearchParams({ previewRepeatOrder: "true" }),
        nodeEnv: "development",
      })
    ).toBe(false);
    expect(
      isSalesOrderRepeatPreviewActive({
        searchParams: new URLSearchParams(),
        nodeEnv: "development",
      })
    ).toBe(false);
  });

  test("fixture stays inactive outside development regardless of query", () => {
    const params = new URLSearchParams("previewRepeatOrder=1");
    expect(isSalesOrderRepeatPreviewActive({ searchParams: params, nodeEnv: "production" })).toBe(false);
    expect(isSalesOrderRepeatPreviewActive({ searchParams: params, nodeEnv: "" })).toBe(false);
  });

  test("preview customers expose similar active names and exclude inactive", () => {
    const all = getPreviewMasterCustomers();
    expect(all.some((c) => c.id === PREVIEW_CUSTOMER_INACTIVE_ID && c.is_active === false)).toBe(true);
    const filtered = filterActiveMasterCustomers(all, "BAKSO");
    const ids = filtered.map((c) => c.id);
    expect(ids).toContain(PREVIEW_CUSTOMER_BAKSO_ROSSO_ID);
    expect(ids).toContain(PREVIEW_CUSTOMER_BAKSO_ROSSO_UTARA_ID);
    expect(ids).not.toContain(PREVIEW_CUSTOMER_INACTIVE_ID);
    expect(filtered.find((c) => c.id === PREVIEW_CUSTOMER_BAKSO_ROSSO_ID).name).toBe("BAKSO ROSSO");
    expect(filtered.find((c) => c.id === PREVIEW_CUSTOMER_BAKSO_ROSSO_UTARA_ID).name).toBe(
      "BAKSO ROSSO CABANG UTARA"
    );
  });

  test("repeat source for BAKSO ROSSO maps without phone/dates/history identity", () => {
    const { found, source } = getPreviewRepeatSource(PREVIEW_CUSTOMER_BAKSO_ROSSO_ID);
    expect(found).toBe(true);
    expect(source.customer_phone).toBe("0812-OLD-PHONE");
    expect(source.line_items.filter((l) => l.name === "Paper Cup 8oz")).toHaveLength(2);

    const master = getPreviewMasterCustomers().find((c) => c.id === PREVIEW_CUSTOMER_BAKSO_ROSSO_ID);
    const { formPatch } = mapRepeatSourceToFormPatch(source, {
      customer_id: master.id,
      customer: master.name,
    });
    expect(formPatch.customer).toBe("BAKSO ROSSO");
    expect(formPatch.customer_id).toBe(PREVIEW_CUSTOMER_BAKSO_ROSSO_ID);
    expect(formPatch.customer_phone).toBeUndefined();
    expect(formPatch.order_date).toBeUndefined();
    expect(formPatch.estimated_delivery).toBeUndefined();
    expect(formPatch.deadline).toBeUndefined();
    expect(formPatch.id).toBeUndefined();
    expect(formPatch.current_stage).toBeUndefined();
    expect(formPatch.packing_status).toBeUndefined();
    expect(formPatch.line_items).toHaveLength(3);
    expect(formPatch.line_items[0].name).toBe("Paper Cup 8oz");
    expect(formPatch.line_items[1].name).toBe("Paper Cup 8oz");
    expect(formPatch.line_items[0].quantity).toBe(1000);
    expect(formPatch.line_items[1].quantity).toBe(500);
    expect(formPatch.line_items[0].color).toBe("Navy");
    expect(formPatch.line_items[1].color).toBe("Merah");
    expect(formPatch.line_items[0].id).toBeUndefined();
    expect(formPatch.line_items[0].line_id).toBeUndefined();
    expect(formPatch.line_items[0].master_item_id).toBe("mst-preview-1");
    expect(formPatch.line_items[1].master_item_id).toBe("mst-preview-2");
  });

  test("customer without prior PO returns safe empty found=false", () => {
    expect(getPreviewRepeatSource(PREVIEW_CUSTOMER_NO_HISTORY_ID)).toEqual({
      found: false,
      customer_id: PREVIEW_CUSTOMER_NO_HISTORY_ID,
      source: null,
    });
    expect(getPreviewRepeatSource(PREVIEW_CUSTOMER_BAKSO_ROSSO_UTARA_ID).found).toBe(false);
  });

  test("page wires preview gate without altering normal API path when inactive", () => {
    const src = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
    expect(src).toMatch(/isSalesOrderRepeatPreviewActive/);
    expect(src).toMatch(/getPreviewMasterCustomers/);
    expect(src).toMatch(/getPreviewRepeatSource/);
    expect(src).toMatch(/previewRepeatOrderActive/);
    expect(src).toMatch(/if \(previewRepeatOrderActive\)/);
    expect(src).toMatch(/api\.salesOrderRepeatSource\(customerId/);
    expect(src).not.toMatch(/localStorage/);
    expect(src).not.toMatch(/sessionStorage/);
    expect(src).not.toMatch(/indexedDB/);
  });
});
