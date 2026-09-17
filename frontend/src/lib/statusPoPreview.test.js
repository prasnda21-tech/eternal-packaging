import { isStatusPoMatrixPreviewEnabled, buildStatusPoMatrixPreviewRows } from "./statusPoPreview";

describe("Status PO isolated preview rows", () => {
  const original = process.env.NODE_ENV;

  afterEach(() => {
    process.env.NODE_ENV = original;
  });

  test("preview host is development query flag only", () => {
    process.env.NODE_ENV = "development";
    expect(isStatusPoMatrixPreviewEnabled("previewStatusPoMatrix=1")).toBe(true);
    expect(isStatusPoMatrixPreviewEnabled("", { location: { hostname: "localhost", port: "3003", search: "" } })).toBe(false);
    expect(isStatusPoMatrixPreviewEnabled("", { location: { hostname: "127.0.0.1", port: "3003", search: "" } })).toBe(false);
    process.env.NODE_ENV = "production";
    expect(isStatusPoMatrixPreviewEnabled("previewStatusPoMatrix=1")).toBe(false);
  });

  test("preview covers the ten representative workflow states", () => {
    const rows = buildStatusPoMatrixPreviewRows();
    expect(rows.map((r) => r.po_number)).toEqual([
      "PO-PACK", "PO-CETAK", "PO-LOAD", "PO-JADWAL", "PO-DESIGN",
      "PO-PAY", "PO-SCREEN", "PO-SAMPLE", "PO-CANCEL", "PO-KIRIM", "PO-GAP",
    ]);
    expect(rows[0].item_lines).toEqual(["Cup 16oz", "Lid 98 mm", "Add On Warna"]);
    expect(rows[10].workflow.design).toBe("pending");
    expect(rows[10].workflow.packing).toBe("active");
    expect(rows[10].workflow.kirim).toBe("unknown");
    expect(rows.map((r) => r.status)).toEqual([
      "Packing", "Cetak berjalan", "Loading", "Penjadwalan", "Design",
      "Payment", "Screener", "Konfirmasi Sample", "Cancelled", "Terkirim", "Packing",
    ]);
  });
});
