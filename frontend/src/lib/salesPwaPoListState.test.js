import { readFileSync } from "fs";
import path from "path";
import {
  SALES_PWA_PO_FILTER_KEY,
  SALES_PWA_PO_SCROLL_KEY,
  clearSalesPwaPoListState,
  readSalesPwaPoListState,
  salesPwaPoFiltersFromContext,
  writeSalesPwaPoListState,
} from "./salesPwaPoListState";

describe("sales PWA PO list session state", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  test("stores filters and scroll in separate login-session keys", () => {
    writeSalesPwaPoListState({
      q: "Kopi",
      customer_q: "PO-001",
      status: "hold",
      stage: "Packing",
      date_from: "2026-09-01",
      date_to: "2026-09-30",
      progress_min: "50",
      buffer: "ya",
    }, 320);
    expect(JSON.parse(sessionStorage.getItem(SALES_PWA_PO_FILTER_KEY))).toMatchObject({
      q: "Kopi",
      customer_q: "PO-001",
      status: "hold",
      stage: "Packing",
    });
    expect(sessionStorage.getItem(SALES_PWA_PO_SCROLL_KEY)).toBe("320");
    expect(readSalesPwaPoListState().scrollY).toBe(320);

    writeSalesPwaPoListState({ q: "A", status: "nope", buffer: "maybe" }, -4);
    expect(readSalesPwaPoListState().filters.status).toBe("semua");
    expect(readSalesPwaPoListState().filters.buffer).toBe("");
    expect(readSalesPwaPoListState().scrollY).toBe(0);
  });

  test("logout clears sales_pwa_po_filter and sales_pwa_po_scroll", () => {
    writeSalesPwaPoListState({ q: "Kopi", status: "hold" }, 180);
    clearSalesPwaPoListState();
    expect(sessionStorage.getItem(SALES_PWA_PO_FILTER_KEY)).toBeNull();
    expect(sessionStorage.getItem(SALES_PWA_PO_SCROLL_KEY)).toBeNull();
    expect(readSalesPwaPoListState()).toEqual({
      filters: {
        q: "",
        customer_q: "",
        status: "semua",
        stage: "",
        date_from: "",
        date_to: "",
        progress_min: "",
        buffer: "",
      },
      scrollY: 0,
    });

    const auth = readFileSync(path.join(__dirname, "auth.jsx"), "utf8");
    expect(auth).toMatch(/clearSalesPwaPoListState\(\)/);
  });

  test("maps dashboard card context onto the existing PO filters", () => {
    expect(salesPwaPoFiltersFromContext({ active: true })).toMatchObject({
      status: "berjalan",
      q: "",
      stage: "",
      date_from: "",
      date_to: "",
    });
    expect(salesPwaPoFiltersFromContext({ status: "selesai", periode: "2026-02" })).toMatchObject({
      status: "selesai",
      date_from: "2026-02-01",
      date_to: "2026-02-28",
    });
    expect(salesPwaPoFiltersFromContext({ status: "selesai" })).toBeNull();
    expect(salesPwaPoFiltersFromContext(null)).toBeNull();
  });
});
