/**
 * Task-owned dummy PO rows for Status Lokasi sort focused tests / local preview helpers.
 * Imported by tests only — never wire into production page load paths.
 */

function baseRow(overrides) {
  return {
    status: "terjadwal",
    status_label: "Terjadwal",
    effective_delivery_date: "2026-09-12",
    deadline: "2026-09-15",
    jadwal_manual: null,
    driver_name: null,
    percent_delivered: 0,
    percent_ready_to_ship: 50,
    qty_delivered: 0,
    qty_total: 1000,
    qty_printed_operator: 500,
    print_qty_total: 1000,
    sj_files: [],
    sj_file_count: 0,
    ...overrides,
  };
}

/** Minimum 3 cases: no coords / valid coords / address-only (still Belum Diatur). */
export const STATUS_LOKASI_SORT_DUMMY_ROWS = [
  baseRow({
    id: "dummy-ord-001",
    order_number: "DUMMY-PO-001",
    customer_name: "Dummy Customer Belum A",
    lat: null,
    lng: null,
  }),
  baseRow({
    id: "dummy-ord-002",
    order_number: "DUMMY-PO-002",
    customer_name: "Dummy Customer Sudah",
    lat: -6.2088,
    lng: 106.8456,
  }),
  baseRow({
    id: "dummy-ord-003",
    order_number: "DUMMY-PO-003",
    customer_name: "Dummy Customer Alamat Saja",
    address: "Jl Dummy Preview No. 3, Jakarta",
    lat: null,
    lng: null,
  }),
  baseRow({
    id: "dummy-ord-004",
    order_number: "DUMMY-PO-004",
    customer_name: "Dummy Customer Sudah B",
    lat: -6.9175,
    lng: 107.6191,
  }),
];
