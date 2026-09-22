import {
  CUSTOMER_MASTER,
  CUSTOMER_NEW,
  EMPTY_FORM,
  PRODUCT_MASTER,
  PRODUCT_REQUESTED,
  clearCustomerMode,
  clearProductMode,
  requiredComplete,
  productRequestFormsEqual,
  selectMasterCustomer,
  selectMasterProduct,
  toPayload,
} from "./productRequest";

describe("product request form helpers", () => {
  test("mode switch clears stale customer id", () => {
    const selected = selectMasterCustomer(EMPTY_FORM, { id: "C1", name: "Renee", pic: "Budi", phone: "0812", city: "Bandung" });
    expect(selected.customer_id).toBe("C1");
    const switched = clearCustomerMode(selected, CUSTOMER_NEW);
    expect(switched.customer_id).toBe(null);
    expect(switched.customer_name).toBe("");
    expect(switched.customer_source).toBe(CUSTOMER_NEW);
  });

  test("mode switch clears stale product id", () => {
    const selected = selectMasterProduct(EMPTY_FORM, { id: "P1", item_code: "PC-1", item_name: "Cup" });
    expect(selected.product_id).toBe("P1");
    const switched = clearProductMode(selected, PRODUCT_REQUESTED);
    expect(switched.product_id).toBe(null);
    expect(switched.product_name).toBe("");
    expect(switched.product_source).toBe(PRODUCT_REQUESTED);
  });

  test("payload for new customer/product nulls master ids", () => {
    const payload = toPayload({
      ...EMPTY_FORM,
      customer_source: CUSTOMER_NEW,
      customer_id: "stale",
      customer_name: "Kedai",
      product_source: PRODUCT_REQUESTED,
      product_id: "stale-p",
      product_name: "Cup Baru",
      potential_qty: "10",
      frequency: "Bulanan",
      target_price: "850",
      needed_date: "2026-10-15",
    });
    expect(payload.customer_id).toBe(null);
    expect(payload.product_id).toBe(null);
    expect(payload.potential_qty).toBe(10);
    expect(payload.target_price).toBe(850);
    expect(payload.doc_version).toBeUndefined();
  });

  test("payload sends the doc_version last read", () => {
    const payload = toPayload({ ...EMPTY_FORM, doc_version: 3, customer_source: CUSTOMER_NEW, customer_name: "Kedai" });
    expect(payload.doc_version).toBe(3);
  });

  test("submit stays disabled until required fields are complete", () => {
    const incomplete = { ...EMPTY_FORM, customer_source: CUSTOMER_MASTER };
    expect(requiredComplete(incomplete)).toBe(false);
    const complete = {
      ...EMPTY_FORM,
      customer_source: CUSTOMER_MASTER,
      customer_id: "C1",
      product_source: PRODUCT_MASTER,
      product_id: "P1",
      potential_qty: 12,
      frequency: "Bulanan",
      target_price: 0,
      needed_date: "2026-10-15",
    };
    expect(requiredComplete(complete)).toBe(true);
  });

  test("saved snapshot matches numeric qty and empty notes", () => {
    const loaded = { ...EMPTY_FORM, potential_qty: 12, target_price: 0, request_note: "" };
    const typed = { ...EMPTY_FORM, potential_qty: "12", target_price: "0", request_note: null };
    expect(productRequestFormsEqual(loaded, typed)).toBe(true);
    expect(productRequestFormsEqual(loaded, { ...typed, request_note: "belum disimpan" })).toBe(false);
  });
});
