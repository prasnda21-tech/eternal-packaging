import {
  applyPraPoSalesChoice,
  buildPraPoPayload,
  emptyPraPoForm,
  emptyPraPoLine,
  filterPraPoRows,
  isPraPoStoredSalesOption,
  praPoDocToForm,
  praPoLineQtyLabel,
  praPoSalesOptionLabel,
  praPoSalesOptions,
  praPoSalesSelectValue,
  validatePraPoDraft,
  validatePraPoSubmit,
} from "./praPoForm";
import { lineSellingSubtotal } from "./salesOrderLinePrice";

describe("Pra PO validation helpers", () => {
  test("draft allows incomplete master and price", () => {
    const form = emptyPraPoForm();
    form.customer = "Toko Draft";
    form.line_items = [emptyPraPoLine()];
    expect(validatePraPoDraft(form)).toBe("");
    expect(validatePraPoSubmit(form)).toMatch(/HP|Master|Harga|Alamat/);
  });

  test("new customer requires phone and address on submit", () => {
    const form = emptyPraPoForm();
    form.customer = "Pelanggan Baru";
    form.line_items = [{
      ...emptyPraPoLine(),
      name: "Box",
      master_item_id: "m1",
      unit_price: "1000",
      quantity: 2,
    }];
    expect(validatePraPoSubmit(form)).toMatch(/HP|WhatsApp/);
    form.customer_phone = "0812";
    expect(validatePraPoSubmit(form)).toMatch(/Alamat/);
    form.address = "Jl. A";
    expect(validatePraPoSubmit(form)).toBe("");
  });

  test("existing customer skips phone/address requirement", () => {
    const form = emptyPraPoForm();
    form.customer = "Toko Master";
    form.customer_id = "cust-1";
    form.line_items = [{
      ...emptyPraPoLine(),
      name: "Box",
      master_item_id: "m1",
      unit_price: "2500",
      quantity: 3,
      unit: "pcs",
    }];
    expect(validatePraPoSubmit(form)).toBe("");
  });

  test("submit rejects missing master and invalid price", () => {
    const form = emptyPraPoForm();
    form.customer = "X";
    form.customer_id = "c1";
    form.line_items = [{
      ...emptyPraPoLine(),
      name: "Item",
      quantity: 1,
      unit: "pcs",
      unit_price: "abc",
    }];
    expect(validatePraPoSubmit(form)).toMatch(/Master Produk/);
    form.line_items[0].master_item_id = "m1";
    expect(validatePraPoSubmit(form)).toMatch(/Harga jual/);
    form.line_items[0].unit_price = "0";
    expect(validatePraPoSubmit(form)).toMatch(/Harga jual/);
    form.line_items[0].unit_price = "-10";
    expect(validatePraPoSubmit(form)).toMatch(/Harga jual/);
    form.line_items[0].unit_price = "1500.5";
    expect(validatePraPoSubmit(form)).toBe("");
  });

  test("qty × unit_price subtotal matches sales-order helper", () => {
    expect(lineSellingSubtotal(3, "1500")).toBe(4500);
    expect(lineSellingSubtotal(2, "1500.55")).toBe(3001.1);
  });

  test("payload keeps canonical ids and numeric price", () => {
    const form = emptyPraPoForm();
    form.customer = "Toko";
    form.customer_id = "cust-1";
    form.line_items = [{
      ...emptyPraPoLine(),
      name: "Lid",
      master_item_id: "mid-9",
      accurate_item_id: "acc-9",
      unit_price: "1200",
      quantity: 4,
    }];
    const payload = buildPraPoPayload(form);
    expect(payload.line_items[0].master_item_id).toBe("mid-9");
    expect(payload.line_items[0].unit_price).toBe(1200);
    expect(payload.line_items[0].name).toBe("Lid");
  });

  test("praPoDocToForm maps returned document for line editing", () => {
    const form = praPoDocToForm({
      customer: "Toko",
      status: "returned",
      return_reason: "Perbaiki harga",
      line_items: [{
        line_id: "ln-1",
        name: "Box",
        quantity: 5,
        unit: "pcs",
        master_item_id: "m1",
        unit_price: 900,
        color: "Merah",
        production_process: "non_print",
      }],
    });
    expect(form.line_items[0].unit_price).toBe("900");
    expect(form.line_items[0].color).toBe("Merah");
    expect(form.line_items[0].production_process).toBe("non_print");
    expect(validatePraPoSubmit({
      ...form,
      customer_id: "cust-1",
    })).toBe("");
  });
});

describe("Pra PO sales master payload", () => {
  const catalog = [
    { id: "sm-euis", sales_name: "Euis Risnawati", active: true },
    { id: "sm-ayub", sales_name: "Sholahudin Al - Ayubi", active: true },
  ];

  test("active catalog rows are the selectable choices", () => {
    const options = praPoSalesOptions(catalog, emptyPraPoForm());
    expect(options.map((row) => row.id)).toEqual(["sm-euis", "sm-ayub"]);
    expect(options.some(isPraPoStoredSalesOption)).toBe(false);
  });

  test("new choice stores master id and canonical sales_name", () => {
    const form = applyPraPoSalesChoice(emptyPraPoForm(), "sm-euis", catalog[0]);
    const payload = buildPraPoPayload(form);
    expect(payload.sales_master_id).toBe("sm-euis");
    expect(payload.sales).toBe("Euis Risnawati");
    expect(praPoSalesSelectValue(form)).toBe("sm-euis");
  });

  test("empty sales stays allowed and does not send a null master id", () => {
    const payload = buildPraPoPayload(emptyPraPoForm());
    expect(payload.sales).toBeNull();
    expect(payload).not.toHaveProperty("sales_master_id");
    expect(validatePraPoSubmit({
      ...emptyPraPoForm(),
      customer: "Toko",
      customer_id: "cust-1",
      line_items: [{
        ...emptyPraPoLine(),
        name: "Box",
        master_item_id: "m1",
        unit_price: "1000",
        quantity: 1,
        unit: "pcs",
      }],
    })).toBe("");
  });

  test("reopen keeps stored id and label, including a renamed catalog row", () => {
    const form = praPoDocToForm({
      customer: "Toko",
      sales: "Euis Lama",
      sales_master_id: "sm-euis",
      line_items: [],
    });
    expect(form.sales).toBe("Euis Lama");
    expect(form.sales_master_id).toBe("sm-euis");
    expect(praPoSalesOptionLabel(catalog[0], form)).toBe("Euis Lama");
    const payload = buildPraPoPayload(form);
    expect(payload.sales).toBe("Euis Lama");
    expect(payload.sales_master_id).toBe("sm-euis");
  });

  test("historical free text and inactive id stay visible and are not auto-replaced", () => {
    const legacy = praPoDocToForm({ customer: "Toko", sales: "Sales Catatan Lama", line_items: [] });
    const options = praPoSalesOptions(catalog, legacy);
    expect(options[0].stored).toBe(true);
    expect(options[0].sales_name).toBe("Sales Catatan Lama");
    expect(isPraPoStoredSalesOption(options[0])).toBe(true);
    expect(praPoSalesSelectValue(legacy)).not.toBe("sm-euis");
    expect(applyPraPoSalesChoice(legacy, options[0].id, options[0])).toBe(legacy);
    const payload = buildPraPoPayload(legacy);
    expect(payload.sales).toBe("Sales Catatan Lama");
    expect(payload).not.toHaveProperty("sales_master_id");

    const inactive = praPoDocToForm({
      customer: "Toko",
      sales: "Vira",
      sales_master_id: "sm-inactive",
      line_items: [],
    });
    const inactiveOptions = praPoSalesOptions(catalog, inactive);
    expect(inactiveOptions.some((row) => row.id === "sm-inactive" && row.stored)).toBe(true);
    expect(inactiveOptions.filter((row) => !row.stored).map((row) => row.id)).toEqual(["sm-euis", "sm-ayub"]);
  });

  test("explicit clear sends null master id; catalog failure does not", () => {
    const saved = praPoDocToForm({
      customer: "Toko",
      sales: "Euis Risnawati",
      sales_master_id: "sm-euis",
      line_items: [],
    });
    const cleared = applyPraPoSalesChoice(saved, "", null);
    expect(buildPraPoPayload(cleared).sales).toBeNull();
    expect(buildPraPoPayload(cleared).sales_master_id).toBeNull();
    expect(buildPraPoPayload(saved).sales_master_id).toBe("sm-euis");
  });
});

describe("Pra PO list filters", () => {
  const rows = [
    { id: "1", number: "PRA-1", customer: "Alpha", status: "draft", item_count: 1, total_quantity: 2 },
    { id: "2", number: "PRA-2", customer: "Beta", status: "submitted", item_count: 2, total_quantity: 10 },
    { id: "3", number: "PRA-3", customer: "Alpha Shop", status: "returned", item_count: 1, total_quantity: 1 },
    { id: "4", number: "PRA-4", customer: "Gamma", status: "verified", sales_order_po_number: "PO/1", item_count: 3, total_quantity: 9 },
    { id: "5", number: "PRA-5", customer: "Delta", status: "resubmitted", item_count: 1, total_quantity: 4 },
  ];

  test("search by number or customer", () => {
    expect(filterPraPoRows(rows, { q: "alpha", status: "all" }).map((r) => r.id)).toEqual(["1", "3"]);
    expect(filterPraPoRows(rows, { q: "PRA-4", status: "all" }).map((r) => r.id)).toEqual(["4"]);
  });

  test("status filters include queue for submitted+resubmitted", () => {
    expect(filterPraPoRows(rows, { status: "queue" }).map((r) => r.id)).toEqual(["2", "5"]);
    expect(filterPraPoRows(rows, { status: "draft" }).map((r) => r.id)).toEqual(["1"]);
  });

  test("qty label", () => {
    expect(praPoLineQtyLabel(rows[1])).toBe("2 jenis · 10 qty");
  });
});
