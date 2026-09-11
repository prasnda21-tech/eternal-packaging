/**
 * Task-owned dummy PO rows for Status Lokasi sort focused tests / local preview helpers.
 * Loaded by DeliverySchedulePage ONLY in development when ?previewStatusLokasi=1.
 * Never written to API/DB. Production builds eliminate the NODE_ENV branch.
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

/**
 * Minimum 4 cases: no coords / valid coords / address-only (still Belum Diatur) / valid coords B.
 * Progress values vary for ops-list chrome preview (bars + pills).
 */
export const STATUS_LOKASI_SORT_DUMMY_ROWS = [
  baseRow({
    id: "dummy-ord-001",
    order_number: "DUMMY-PO-001",
    customer_name: "Dummy Customer Belum A",
    lat: null,
    lng: null,
    percent_delivered: 0,
    percent_ready_to_ship: 25,
    qty_delivered: 0,
    qty_printed_operator: 250,
  }),
  baseRow({
    id: "dummy-ord-002",
    order_number: "DUMMY-PO-002",
    customer_name: "Dummy Customer Sudah",
    lat: -6.2088,
    lng: 106.8456,
    percent_delivered: 40,
    percent_ready_to_ship: 100,
    qty_delivered: 400,
    qty_printed_operator: 1000,
    driver_name: "Dummy Driver",
  }),
  baseRow({
    id: "dummy-ord-003",
    order_number: "DUMMY-PO-003",
    customer_name: "Dummy Customer Alamat Saja",
    address: "Jl Dummy Preview No. 3, Jakarta",
    lat: null,
    lng: null,
    percent_delivered: 10,
    percent_ready_to_ship: 60,
    qty_delivered: 100,
    qty_printed_operator: 600,
  }),
  baseRow({
    id: "dummy-ord-004",
    order_number: "DUMMY-PO-004",
    customer_name: "Dummy Customer Sudah B",
    lat: -6.9175,
    lng: 107.6191,
    percent_delivered: 100,
    percent_ready_to_ship: 100,
    qty_delivered: 1000,
    qty_printed_operator: 1000,
    driver_name: "Dummy Driver B",
  }),
];
