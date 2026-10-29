/** Development-only visual fixture for Tracking PO Terlambat Kirim.
 * Never persisted. Never returned by production APIs.
 */

export const VISUAL_FIXTURE_PARAM = "visualFixture";

export function isPoLateVisualFixtureEnabled(searchParams) {
  if (process.env.NODE_ENV === "production") return false;
  if (!searchParams || typeof searchParams.get !== "function") return false;
  return searchParams.get(VISUAL_FIXTURE_PARAM) === "1";
}

export function buildPoLateVisualFixtureRows() {
  const base = [
    {
      id: "fx-1",
      order_id: "fx-1",
      po_number: "PO-FX-0001",
      order_date: "2026-09-01",
      customer_name: "CV Nusantara Packing Sejahtera Abadi Yogyakarta",
      status_saat_ini: "Cetak",
      umur_po_days: 58,
      tanggal_pengiriman: "2026-10-28",
      days_late: 1,
      category: "lewat_1_hari",
      category_label: "Lewat 1 Hari",
      branch: "Yogyakarta",
      branch_id: "br-ygy",
      qty_remaining: 120,
    },
    {
      id: "fx-2",
      order_id: "fx-2",
      po_number: "PO-FX-0002",
      order_date: "2026-09-15",
      customer_name: "Toko Berkah",
      status_saat_ini: "Packing",
      umur_po_days: 44,
      tanggal_pengiriman: "2026-10-27",
      days_late: 2,
      category: "terlambat",
      category_label: "Terlambat",
      branch: "Semarang",
      branch_id: "br-smg",
      qty_remaining: 40,
    },
    {
      id: "fx-3",
      order_id: "fx-3",
      po_number: "PO-FX-0003-MULTI-SJ",
      order_date: "2026-08-20",
      customer_name: "PT Industri Kemasan Panjang Nama Customer Untuk Uji Truncation Kolom",
      status_saat_ini: "Loading",
      umur_po_days: 70,
      tanggal_pengiriman: "2026-10-26",
      days_late: 3,
      category: "terlambat",
      category_label: "Terlambat",
      branch: "Surabaya",
      branch_id: "br-sby",
      qty_remaining: 15,
    },
    {
      id: "fx-4",
      order_id: "fx-4",
      po_number: "PO-FX-0004",
      order_date: "2026-07-01",
      customer_name: "UD Maju Jaya",
      status_saat_ini: "Pengiriman",
      umur_po_days: 120,
      tanggal_pengiriman: "2026-10-20",
      days_late: 9,
      category: "kritis",
      category_label: "Kritis",
      branch: "Yogyakarta",
      branch_id: "br-ygy",
      qty_remaining: 2500,
    },
    {
      id: "fx-5",
      order_id: "fx-5",
      po_number: "PO-FX-0005-NO-SJ",
      order_date: "2026-10-01",
      customer_name: "Outlet Solo Baru",
      status_saat_ini: "Desain",
      umur_po_days: 28,
      tanggal_pengiriman: "2026-10-25",
      days_late: 4,
      category: "kritis",
      category_label: "Kritis",
      branch: "Solo",
      branch_id: "br-solo",
      qty_remaining: 88,
    },
  ];
  // Pad to exercise pagination / density without DB writes.
  const rows = [...base];
  for (let i = 6; i <= 24; i += 1) {
    const days = (i % 7) + 1;
    const category = days === 1 ? "lewat_1_hari" : days <= 3 ? "terlambat" : "kritis";
    const labels = {
      lewat_1_hari: "Lewat 1 Hari",
      terlambat: "Terlambat",
      kritis: "Kritis",
    };
    rows.push({
      id: `fx-${i}`,
      order_id: `fx-${i}`,
      po_number: `PO-FX-${String(i).padStart(4, "0")}`,
      order_date: "2026-09-10",
      customer_name: i % 2 === 0
        ? `Pelanggan Panjang ${i} Nama Outlet Cabang Regional`
        : `Customer ${i}`,
      status_saat_ini: ["Desain", "Cetak", "Packing", "Loading"][i % 4],
      umur_po_days: 20 + i,
      tanggal_pengiriman: `2026-10-${String(29 - Math.min(days, 9)).padStart(2, "0")}`,
      days_late: days,
      category,
      category_label: labels[category],
      branch: ["Yogyakarta", "Semarang", "Surabaya", "Solo"][i % 4],
      branch_id: ["br-ygy", "br-smg", "br-sby", "br-solo"][i % 4],
      qty_remaining: (i * 17) % 500,
    });
  }
  return rows;
}

function fixtureBranchOptions(rows) {
  const seen = new Map();
  rows.forEach((row) => {
    const id = String(row.branch_id || "").trim();
    if (!id || seen.has(id)) return;
    seen.set(id, { id, name: String(row.branch || id) });
  });
  return Array.from(seen.values()).sort((a, b) => String(a.name).localeCompare(String(b.name), "id"));
}

export function buildPoLateVisualFixturePayload({
  page = 1,
  pageSize = 50,
  q = "",
  category = "",
  branchId = "",
  sortBy = "days_late",
  sortDir = "desc",
} = {}) {
  const universe = buildPoLateVisualFixtureRows();
  let rows = universe;
  const needle = String(q || "").trim().toLowerCase();
  if (needle) {
    rows = rows.filter((row) => (
      String(row.po_number || "").toLowerCase().includes(needle)
      || String(row.customer_name || "").toLowerCase().includes(needle)
    ));
  }
  const cat = String(category || "").trim().toLowerCase();
  if (cat) rows = rows.filter((row) => row.category === cat);
  const bid = String(branchId || "").trim();
  if (bid) rows = rows.filter((row) => String(row.branch_id || "") === bid);
  const dir = String(sortDir || "desc").toLowerCase() === "asc" ? 1 : -1;
  const field = sortBy || "days_late";
  rows = [...rows].sort((a, b) => {
    const av = a[field];
    const bv = b[field];
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
    return String(av || "").localeCompare(String(bv || "")) * dir;
  });
  const counts = { lewat_1_hari: 0, terlambat: 0, kritis: 0 };
  universe.forEach((row) => {
    if (counts[row.category] != null) counts[row.category] += 1;
  });
  const filteredCounts = { lewat_1_hari: 0, terlambat: 0, kritis: 0 };
  rows.forEach((row) => {
    if (filteredCounts[row.category] != null) filteredCounts[row.category] += 1;
  });
  const total = rows.length;
  const size = Math.max(1, Number(pageSize) || 50);
  const totalPages = Math.max(1, Math.ceil(total / size));
  const pageNum = Math.min(Math.max(1, Number(page) || 1), totalPages);
  const start = (pageNum - 1) * size;
  const pageRows = rows.slice(start, start + size);
  return {
    rows: pageRows,
    page: pageNum,
    page_size: size,
    total,
    total_pages: totalPages,
    filtered_total: total,
    active_late_total: universe.length,
    active_late_counts: counts,
    counts: filteredCounts,
    branch_options: fixtureBranchOptions(universe),
    grain: "po",
    visual_fixture: true,
  };
}

export function buildPoLateVisualSummary() {
  const rows = buildPoLateVisualFixtureRows();
  const counts = { lewat_1_hari: 0, terlambat: 0, kritis: 0 };
  rows.forEach((row) => {
    if (counts[row.category] != null) counts[row.category] += 1;
  });
  return {
    status: "complete",
    total: rows.length,
    counts,
    note: "PO aktif melewati tanggal pengiriman. (visual fixture)",
    visual_fixture: true,
  };
}
