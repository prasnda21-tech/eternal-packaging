import fs from "fs";
import path from "path";
import {
  formatReplaceApiError,
  isLineProductReplaceAllowed,
  lockReasonForLine,
  replaceEligibilityForLine,
} from "../components/LineProductReplaceControl";

const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
const ctrl = fs.readFileSync(
  path.join(__dirname, "../components/LineProductReplaceControl.jsx"),
  "utf8"
);
const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
const search = fs.readFileSync(
  path.join(__dirname, "../components/MasterItemSearchSelect.jsx"),
  "utf8"
);

describe("Imported line Ganti Barang UI", () => {
  test("PODetail line panel mounts replace control with po.edit", () => {
    expect(detail).toMatch(/LineProductReplaceControl/);
    expect(detail).toMatch(/canReplaceLineProduct/);
    expect(detail).toMatch(/hasPerm\("po\.edit"\)/);
    expect(detail).not.toMatch(/canReplaceLineProduct = hasPerm\("production\.route\.edit"\)/);
  });

  test("editable control shows Ganti Barang and current item", () => {
    expect(ctrl).toMatch(/Ganti Barang/);
    expect(ctrl).toMatch(/Barang Saat Ini/);
    expect(ctrl).toMatch(/Cari Barang Pengganti/);
    expect(ctrl).toMatch(/Simpan Perubahan/);
    expect(ctrl).toMatch(/ganti-barang-btn-/);
    expect(ctrl).toMatch(/ganti-barang-current-/);
    expect(ctrl).toMatch(/ganti-barang-save-/);
  });

  test("reuses MasterItemSearchSelect live search (no duplicate catalog)", () => {
    expect(ctrl).toMatch(/MasterItemSearchSelect/);
    expect(ctrl).not.toMatch(/fetch\(.*catalog/);
    expect(api).toMatch(/replaceSalesOrderLineProduct/);
    expect(api).toMatch(/\/sales-orders\/\$\{orderId\}\/lines\/\$\{lineIdx\}\/product/);
    expect(search).toMatch(/MASTER_ITEM_SEARCH_DEBOUNCE_MS/);
    expect(search).toMatch(/listboxViewState/);
  });

  test("explicit save only — open/search/select do not write", () => {
    expect(ctrl).toMatch(/if \(saving \|\| !target\) return;/);
    expect(ctrl).toMatch(/api\.replaceSalesOrderLineProduct/);
    // Opening editor clears target; save gated on target
    expect(ctrl).toMatch(/setTarget\(null\)/);
    expect(ctrl).toMatch(/disabled=\{saving \|\| !target\}/);
  });

  test("locked UI shows reason, not active control", () => {
    expect(ctrl).toMatch(/ganti-barang-locked-/);
    expect(ctrl).toMatch(/Tidak dapat diganti — sudah masuk Sampel Cetak/);
    expect(ctrl).toMatch(/Tidak dapat diganti — sudah dipakai pada proses berikutnya/);
  });

  test("eligibility helpers: sample locks print only; downstream is exact line_idx", () => {
    const order = {
      sample_files: [{ id: "s1" }],
      line_items: [
        { name: "A", production_process: "print" },
        { name: "B", production_process: "non_print" },
      ],
      line_product_replace: [
        {
          line_idx: 0,
          allowed: false,
          reason_code: "ITEM_SUDAH_MASUK_SAMPEL_CETAK",
          reason_message: "Barang tidak dapat diganti karena item sudah memasuki tahap Sampel Cetak.",
        },
        { line_idx: 1, allowed: true, reason_code: null, reason_message: null },
      ],
    };
    expect(isLineProductReplaceAllowed(order, 0)).toBe(false);
    expect(isLineProductReplaceAllowed(order, 1)).toBe(true);
    expect(lockReasonForLine(order, 0)).toMatch(/Sampel Cetak/);
    expect(replaceEligibilityForLine(order, 1).allowed).toBe(true);

    const sjLocked = {
      sample_files: [],
      line_items: [
        { name: "X", production_process: "print" },
        { name: "X", production_process: "non_print" },
      ],
      shipments: [{ lines: [{ line_idx: 0, name: "X" }] }],
      line_product_replace: [
        { line_idx: 0, allowed: false, reason_code: "ITEM_SUDAH_DIPAKAI_DI_PROSES_LANJUTAN", reason_message: "SJ" },
        { line_idx: 1, allowed: true },
      ],
    };
    expect(isLineProductReplaceAllowed(sjLocked, 0)).toBe(false);
    expect(isLineProductReplaceAllowed(sjLocked, 1)).toBe(true);
  });

  test("fallback without eligibility uses sample + embedded downstream, not broad PO lock", () => {
    const mixed = {
      sample_files: [{ id: "s" }],
      line_items: [
        { name: "P", production_process: "print" },
        { name: "N", production_process: "non_print" },
      ],
      shipments: [],
    };
    expect(isLineProductReplaceAllowed(mixed, 0)).toBe(false);
    expect(isLineProductReplaceAllowed(mixed, 1)).toBe(true);

    const withSjOn1 = {
      sample_files: [],
      line_items: [
        { name: "A", production_process: "non_print" },
        { name: "B", production_process: "non_print" },
      ],
      shipments: [{ lines: [{ line_idx: 1, qty: 1 }] }],
    };
    expect(isLineProductReplaceAllowed(withSjOn1, 0)).toBe(true);
    expect(isLineProductReplaceAllowed(withSjOn1, 1)).toBe(false);
  });

  test("backend conflict detail message extracted factually", () => {
    expect(
      formatReplaceApiError({
        response: { data: { detail: { message: "Barang tidak dapat diganti karena item sudah memasuki tahap Sampel Cetak.", code: "ITEM_SUDAH_MASUK_SAMPEL_CETAK" } } },
      })
    ).toMatch(/Sampel Cetak/);
    expect(formatReplaceApiError({ response: { data: { detail: "plain" } } })).toBe("plain");
  });

  test("same-name lines remain independent in UI (per lineIdx control)", () => {
    expect(ctrl).toMatch(/lineIdx/);
    expect(ctrl).toMatch(/ganti-barang-btn-\$\{lineIdx\}/);
    expect(ctrl).toMatch(/replaceSalesOrderLineProduct\(order\.id, lineIdx/);
  });
});
