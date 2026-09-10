import {
  REPEAT_NO_PREVIOUS_MESSAGE,
  clearRepeatDerivedFormFields,
  mapRepeatSourceToFormPatch,
} from "../lib/salesOrderRepeat";
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
    expect(designFiles).toHaveLength(1);
    expect(designFiles[0].name).toBe("design-a.png");
    expect(sourcePoNumber).toBe("PO/10/09/26/001");
  });

  test("rejects mismatched customer_id (in-flight / stale guard)", () => {
    const result = mapRepeatSourceToFormPatch(sampleSource({ customer_id: "cust-old" }), {
      customer_id: "cust-new",
      customer: "New",
    });
    expect(result.formPatch).toBeNull();
    expect(result.designFiles).toEqual([]);
  });

  test("copies only design files from dto (non-design absent)", () => {
    const { designFiles } = mapRepeatSourceToFormPatch(sampleSource(), {
      customer_id: "cust-1",
      customer: "PT Repeat",
    });
    expect(designFiles.map((f) => f.name)).toEqual(["design-a.png"]);
    expect(designFiles.every((f) => f.data_url.startsWith("data:"))).toBe(true);
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

  test("repeat button gated on customer_id and confirmation before apply", () => {
    expect(src).toMatch(/data-testid="btn-repeat-order"/);
    expect(src).toMatch(/disabled=\{!formData\.customer_id/);
    expect(src).toMatch(/api\.salesOrderRepeatSource/);
    expect(src).toMatch(/data-testid="repeat-confirm-modal"/);
    expect(src).toMatch(/Salin detail dari PO terakhir ini\?/);
    expect(src).toMatch(/Gunakan Repeat Order/);
    expect(src).toMatch(/data-testid="btn-repeat-cancel"/);
    expect(src).toMatch(/data-testid="btn-repeat-confirm"/);
    expect(src).toMatch(/applyRepeatOrder/);
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

  test("submit uses canonical createSalesOrder and optional design_files only", () => {
    expect(src).toMatch(/api\.createSalesOrder\(payload\)/);
    expect(src).toMatch(/design_files: \(repeatDesignFiles/);
    expect(src).not.toMatch(/setForm\(source/);
    expect(src).not.toMatch(/setFormData\(source/);
    expect(apiSrc).toMatch(/salesOrderRepeatSource/);
    expect(apiSrc).toMatch(/\/sales-orders\/repeat-source/);
  });

  test("does not copy discount or workflow identity fields into form", () => {
    expect(src).not.toMatch(/discount_amount/);
    expect(src).not.toMatch(/packing_status/);
    expect(src).not.toMatch(/surat_jalan/);
    expect(src).not.toMatch(/delivery_proofs/);
    // apply path uses explicit mapper — never spreads source into form
    expect(src).toMatch(/mapRepeatSourceToFormPatch\(/);
    expect(src).toMatch(/po_number: prev\.po_number/);
    expect(src).toMatch(/customer_id: prev\.customer_id/);
    const mapped = mapRepeatSourceToFormPatch(
      {
        source_po_number: "PO/X",
        customer_id: "c1",
        customer: "A",
        current_stage: 12,
        packing_status: "Selesai",
        discount_amount: 99,
        line_items: [{ name: "X", quantity: 1, unit: "pcs", production_process: "print" }],
        design_files: [],
      },
      { customer_id: "c1", customer: "A" }
    );
    expect(mapped.formPatch.current_stage).toBeUndefined();
    expect(mapped.formPatch.packing_status).toBeUndefined();
    expect(mapped.formPatch.discount_amount).toBeUndefined();
    expect(mapped.formPatch.po_number).toBeUndefined();
  });
});
