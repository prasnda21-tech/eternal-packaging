import { act } from "react";
import { createRoot } from "react-dom/client";
import { api } from "../lib/api";
import LaporanPenjualan from "./LaporanPenjualan";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children }) => <a href={to}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  api: {
    salesReportCatalog: jest.fn(),
    salesReportShow: jest.fn(),
    salesReportExport: jest.fn(),
    salesReportMemorize: jest.fn(),
    salesReportForget: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const catalog = {
  group: "Penjualan",
  branches: [
    { id: "jogja", label: "Jogja" },
    { id: "semarang", label: "Semarang" },
    { id: "semua", label: "[Semua Cabang]" },
  ],
  memorized: [],
  reports: [
    {
      id: "penjualan-per-barang",
      name: "Penjualan per Barang",
      description: "Menampilkan daftar nilai penjualan per barang",
      implemented: true,
      columns: [
        { key: "nama_barang", label: "Nama Barang", align: "left", default: true },
        { key: "satuan", label: "Satuan", align: "left", default: true },
        { key: "kuantitas", label: "Kuantitas", align: "right", default: true },
        { key: "penjualan", label: "Penjualan", align: "right", default: true },
        { key: "nama_kategori", label: "Nama Kategori Barang Barang & Jasa", align: "left", default: false },
      ],
    },
    {
      id: "rincian-penjualan-per-barang",
      name: "Rincian Penjualan per Barang",
      description: "Menampilkan rincian nilai penjualan per barang",
      implemented: true,
      columns: [
        { key: "nomor", label: "Nomor #", align: "left", default: true },
        { key: "penjualan", label: "Penjualan", align: "right", default: true },
        { key: "bpp", label: "BPP", align: "right", default: false },
        { key: "laba", label: "Laba", align: "right", default: false },
      ],
    },
    {
      id: "retur-penjualan-per-barang",
      name: "Retur Penjualan per Barang",
      description: "Menampilkan nilai retur penjualan per barang",
      implemented: false,
      gap: "ERP belum menyimpan dokumen retur penjualan. Faktur batal tidak dijadikan retur.",
      reference: "https://help.accurate.id/example",
    },
  ],
};

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

function clickButton(container, label) {
  const button = Array.from(container.querySelectorAll("button")).find((node) => node.textContent.includes(label));
  if (!button) throw new Error(`missing button ${label}`);
  act(() => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  return button;
}

describe("LaporanPenjualan", () => {
  let container;
  let root;

  beforeEach(() => {
    api.salesReportCatalog.mockResolvedValue(catalog);
    api.salesReportShow.mockResolvedValue({
      report_id: "penjualan-per-barang",
      name: "Penjualan per Barang",
      company_name: "Eternal Packaging",
      period_label: "Dari 01 Okt 2026 s/d 31 Okt 2026",
      branch_label: "Cabang : [Semua Cabang]",
      columns: [
        { key: "nama_barang", label: "Nama Barang", align: "left" },
        { key: "kuantitas", label: "Kuantitas", align: "right" },
        { key: "penjualan", label: "Penjualan", align: "right" },
      ],
      rows: [{ kind: "line", values: { nama_barang: "Snack", kuantitas: "2", penjualan: "1000.00" } }],
      totals: { kuantitas: "40", penjualan: "376000.00", row_count: 30 },
      totals_scope: "seluruh hasil filter, bukan halaman ini saja",
      page: 1,
      page_count: 2,
      methodology: {
        source: "sales_invoices.financial_status = finalized",
        date: "invoice_date inklusif",
        discount: "diskon header dialokasikan",
        penjualan: "setelah diskon barang",
        bpp: "hanya HPP posted",
        returns: "dokumen retur penjualan tidak ada di ERP",
      },
    });
    api.salesReportExport.mockResolvedValue(new Blob(["xlsx"]));
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.clearAllMocks();
  });

  test("lists verified names and shows filter totals from the API", async () => {
    await act(async () => {
      root.render(<LaporanPenjualan />);
    });
    await flush();
    expect(container.textContent).toContain("Penjualan per Barang");
    expect(container.textContent).toContain("Rincian Penjualan per Barang");
    clickButton(container, "Penjualan per Barang");
    expect(container.textContent).toContain("Parameter Laporan");
    const branch = container.querySelector("[aria-label='Cabang']");
    expect(Array.from(branch.options).map((option) => option.textContent)).toEqual(["Jogja", "Semarang", "[Semua Cabang]"]);
    clickButton(container, "Tampilkan");
    await flush();
    expect(api.salesReportShow).toHaveBeenCalledWith(expect.objectContaining({
      report_id: "penjualan-per-barang",
      branch: "semua",
      layout: expect.objectContaining({ analysis_row: "nama_barang" }),
    }));
    expect(container.textContent).toContain("376.000,00");
    expect(container.querySelector(".total").textContent).toContain("376.000,00");
    expect(container.querySelector(".total").textContent).toContain("40");
  });

  test("sends category analysis from modifikasi and does not invent retur numbers", async () => {
    await act(async () => {
      root.render(<LaporanPenjualan />);
    });
    await flush();
    clickButton(container, "Retur Penjualan per Barang");
    expect(container.textContent).toContain("ERP belum menyimpan dokumen retur penjualan");
    expect(api.salesReportShow).not.toHaveBeenCalled();
    clickButton(container, "Tutup");

    const itemButton = Array.from(container.querySelectorAll("button")).find((node) => node.textContent.includes("Penjualan per Barang") && !node.textContent.includes("Retur"));
    act(() => itemButton.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    clickButton(container, "Kolom");
    const category = Array.from(container.querySelectorAll("option")).find((node) => node.textContent.includes("Nama Kategori"));
    const select = container.querySelector("[aria-label='Baris Analisa']");
    act(() => {
      select.value = category.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    clickButton(container, "Tampilkan");
    await flush();
    expect(api.salesReportShow).toHaveBeenCalledWith(expect.objectContaining({
      layout: expect.objectContaining({ analysis_row: "nama_kategori_barang" }),
    }));
  });

  test("export uses the current filter", async () => {
    await act(async () => {
      root.render(<LaporanPenjualan />);
    });
    await flush();
    clickButton(container, "Penjualan per Barang");
    clickButton(container, "Tampilkan");
    await flush();
    clickButton(container, "Ekspor");
    await flush();
    expect(api.salesReportExport).toHaveBeenCalledWith(expect.objectContaining({
      report_id: "penjualan-per-barang",
      date_from: expect.any(String),
      date_to: expect.any(String),
      branch: "semua",
    }));
  });
});
