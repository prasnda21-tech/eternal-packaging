import {
  buildKpiCards,
  formatCellValue,
  formatIdDate,
  formatIdQty,
  formatIdRupiah,
  formatSalesDashboardRange,
  periodLabelId,
  presentationKind,
} from "./dashboardRincianPresentation";

test("KPI labels are contextual and keep PO unique separate from line count", () => {
  const po = buildKpiCards("po_aktif", { count: 8, grain: "po", label: "PO unik" });
  expect(po[0].label).toBe("Total PO Aktif");
  expect(po[0].value).toBe("8 PO");

  const line = buildKpiCards("perlu_produksi", {
    count: 3,
    line_count: 12,
    grain: "line",
    label: "3 PO unik · 12 baris item",
  });
  expect(line[0].label).toBe("Total PO Perlu Produksi");
  expect(line[0].value).toBe("3 PO");
  expect(line[1].label).toBe("Jumlah Baris Item");
  expect(line[1].value).toBe("12");
});

test("incomplete omzet is not rendered as Rp0", () => {
  const cards = buildKpiCards("omzet", {
    count: null,
    amount: null,
    status: "error",
    grain: "invoice",
    basis: "after_discount",
    label: "Faktur finalized",
    detail: "Ada faktur finalized dengan after_discount yang tidak dapat dibaca.",
  });
  expect(cards).toHaveLength(1);
  expect(cards[0].value).toBe("Data tidak lengkap");
  expect(cards[0].value).not.toBe("Rp0");
  expect(cards[0].testId).toBe("dr-kpi-incomplete");
});

test("numeric zero omzet stays Rp0", () => {
  const cards = buildKpiCards("omzet", {
    count: 1,
    amount: 0,
    status: "zero",
    grain: "invoice",
  });
  expect(cards[0].value).toBe("Rp0");
  expect(cards[1].value).toBe("1 faktur");
});

test("money KPI keeps rupiah primary and faktur count secondary", () => {
  const cards = buildKpiCards("omzet", {
    count: 2,
    amount: 7500,
    grain: "invoice",
    basis: "after_discount",
    label: "Faktur finalized",
  });
  expect(cards[0].label).toBe("Total Omzet");
  expect(cards[0].value).toBe("Rp7.500");
  expect(cards[1].label).toBe("Jumlah Faktur");
  expect(cards[1].value).toBe("2 faktur");
});

test("qty rencana keeps unit and does not invent pcs", () => {
  const withUnit = buildKpiCards("delivery_qty_today", {
    count: 4,
    qty_remaining: 12500,
    unit: "PCS",
    grain: "po",
  });
  expect(withUnit[0].label).toBe("Total Qty Rencana");
  expect(withUnit[0].value).toBe("12.500 PCS");
  expect(withUnit[1].value).toBe("4 PO");

  const noUnit = buildKpiCards("delivery_qty_overdue", {
    count: 1,
    qty_remaining: 100,
    grain: "po",
  });
  expect(noUnit[0].value).toBe("100");
});

test("formatters use id-ID and preserve zero vs null", () => {
  expect(formatIdDate("2026-10-01")).toBe("01/10/2026");
  expect(formatIdRupiah(7500)).toBe("Rp7.500");
  expect(formatIdRupiah(2500)).toBe("Rp2.500");
  expect(formatIdRupiah(0)).toBe("Rp0");
  expect(formatIdRupiah(null)).toBe("—");
  expect(formatIdRupiah(-1500)).toBe("-Rp1.500");
  expect(formatIdQty(12500)).toBe("12.500");
  expect(formatIdQty(12500, "DUS")).toBe("12.500 DUS");
  expect(formatIdQty(null)).toBe("—");
  expect(periodLabelId({ start_date: "2026-10-01", end_date: "2026-10-27" })).toBe(
    "01/10/2026 – 27/10/2026 (WIB)",
  );
  expect(formatSalesDashboardRange("2026-03-01", "2026-03-31")).toBe("01/03/2026–31/03/2026");
  expect(formatSalesDashboardRange("2026-03-01", "2026-03-01")).toBe("01/03/2026");
  expect(formatSalesDashboardRange("31/04/2026", "2026-03-01")).toBe("01/03/2026");
  expect(formatSalesDashboardRange("", "")).toBe("");
  expect(formatIdDate("bukan-tanggal")).toBe("bukan-tanggal");
});

test("presentation kind prefers column type then known keys", () => {
  expect(presentationKind({ key: "invoice_date", type: "date" })).toBe("date");
  expect(presentationKind({ key: "grand_total", type: "number" })).toBe("currency");
  expect(presentationKind({ key: "qty_remaining", type: "number" })).toBe("quantity");
  expect(presentationKind({ key: "row_no", type: "number" })).toBe("number");
  expect(presentationKind({ key: "status_label", type: "text" })).toBe("status");
  expect(formatCellValue("currency", 2500)).toBe("Rp2.500");
  expect(formatCellValue("date", "2026-10-15")).toBe("15/10/2026");
});
