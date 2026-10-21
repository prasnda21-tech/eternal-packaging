/**
 * Wave 2 polish: default-hidden identitas_barang + warning disclosure contract.
 */
import { normalizeReportColumns } from "../components/reportWorkspace/ReportColumnSettings";

describe("Wave 2 polish — Pembelian per Barang columns", () => {
  const itemColumns = [
    { key: "kode_barang", label: "Kode Barang", align: "left", default: true },
    { key: "nama_barang", label: "Nama Barang", align: "left", default: true },
    { key: "identitas_barang", label: "Identitas Barang", align: "left", default: false },
    { key: "satuan", label: "Satuan", align: "left", default: true },
    { key: "nilai_pembelian", label: "Nilai Pembelian", align: "right", default: true },
    { key: "kelengkapan", label: "Kelengkapan", align: "left", default: true },
  ];

  test("identitas_barang is hidden by default seed and can be re-enabled", () => {
    const seed = {
      hidden: itemColumns.filter((c) => c.default === false).map((c) => c.key),
    };
    const layout = normalizeReportColumns(itemColumns, seed);
    expect(layout.hidden).toContain("identitas_barang");
    expect(layout.order).toContain("identitas_barang");
    const visible = layout.order.filter((k) => !layout.hidden.includes(k));
    expect(visible).not.toContain("identitas_barang");
    expect(visible).toContain("kode_barang");

    const shown = {
      ...layout,
      hidden: layout.hidden.filter((k) => k !== "identitas_barang"),
    };
    expect(shown.order.filter((k) => !shown.hidden.includes(k))).toContain("identitas_barang");
  });

  test("other report defaults are not altered by identitas seed", () => {
    const invoiceColumns = [
      { key: "nomor", label: "Nomor", align: "left", default: true },
      { key: "subtotal", label: "Subtotal", align: "right", default: true },
    ];
    const layout = normalizeReportColumns(invoiceColumns, {
      hidden: invoiceColumns.filter((c) => c.default === false).map((c) => c.key),
    });
    expect(layout.hidden).toEqual([]);
    expect(layout.order).toEqual(["nomor", "subtotal"]);
  });
});
