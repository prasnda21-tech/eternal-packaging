/** Contract tests for Prediksi Order import preview UI helpers + API wiring. */
import fs from "fs";
import path from "path";
import {
  importCounts,
  selectedImportRows,
  toggleRowSelected,
  toggleSelectAll,
} from "./importPreview";
import { formatApiError } from "./errors";
import { dashOmzet } from "./orderPrediction";

describe("prediction import preview contract", () => {
  const rows = [
    { row_id: "r0", status: "VALID", selectable: true, selected: true, omzet: 1000 },
    { row_id: "r1", status: "DUPLIKAT", selectable: false, selected: false, omzet: null },
    { row_id: "r2", status: "INVALID", selectable: false, selected: false, omzet: "" },
    { row_id: "r3", status: "VALID", selectable: true, selected: false, omzet: null },
  ];

  test("counts + select all only VALID", () => {
    const c = importCounts(rows);
    expect(c.total).toBe(4);
    expect(c.valid).toBe(2);
    expect(c.duplicate).toBe(1);
    expect(c.invalid).toBe(1);
    expect(c.selected).toBe(1);

    const all = toggleSelectAll(rows, true);
    expect(all.filter((r) => r.selected).map((r) => r.row_id)).toEqual(["r0", "r3"]);
    expect(all.find((r) => r.row_id === "r1").selected).toBe(false);
    expect(all.find((r) => r.row_id === "r2").selected).toBe(false);

    const selected = selectedImportRows(all);
    expect(selected).toHaveLength(2);
  });

  test("duplicate and invalid stay disabled via selectable", () => {
    const next = toggleRowSelected(rows, "r1", true);
    expect(next.find((r) => r.row_id === "r1").selected).toBe(false);
  });

  test("omzet null/empty displays as dash", () => {
    expect(dashOmzet(null)).toBe("-");
    expect(dashOmzet("")).toBe("-");
    expect(dashOmzet(1500)).toBe(1500);
  });

  test("backend error detail string surfaces", () => {
    const err = { response: { data: { detail: "Sheet histori order tidak ditemukan." } } };
    expect(formatApiError(err, "Gagal mem-preview file")).toBe("Sheet histori order tidak ditemukan.");
  });

  test("fastapi validation array does not fall back to opaque generic only", () => {
    const err = {
      response: {
        data: {
          detail: [{ loc: ["body", "file"], msg: "Field required", type: "missing" }],
        },
      },
    };
    const msg = formatApiError(err, "Gagal mem-preview file");
    expect(msg).toContain("Field required");
  });

  test("api helper sends FormData without manual multipart Content-Type", () => {
    const apiPath = path.join(__dirname, "api.js");
    const src = fs.readFileSync(apiPath, "utf8");
    const block = src.slice(
      src.indexOf("orderPredictionImportPreview"),
      src.indexOf("orderPredictionImportApply"),
    );
    expect(block).toMatch(/FormData/);
    expect(block).toMatch(/form\.append\(\s*["']file["']/);
    expect(block).toMatch(/\/reports\/order-prediction\/import\/preview/);
    expect(block).not.toMatch(/headers:\s*\{\s*["']Content-Type["']\s*:\s*["']multipart\/form-data["']/);
  });

  test("ImportPreviewModal uses formatApiError for preview failures", () => {
    const modalPath = path.join(__dirname, "..", "components", "ImportPreviewModal.jsx");
    const src = fs.readFileSync(modalPath, "utf8");
    expect(src).toMatch(/formatApiError/);
    expect(src).toMatch(/Gagal mem-preview file/);
  });
});
