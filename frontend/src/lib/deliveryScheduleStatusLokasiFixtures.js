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

/**
 * Batch 1B table-UI preview rows (long customer text, mixed progress / pills).
 * Concatenated after STATUS_LOKASI_SORT_DUMMY_ROWS in the same dev-only preview path.
 * Kept separate so Status Lokasi ASC/DESC contract tests stay on the original four IDs.
 */
export const TABLE_UI_PREVIEW_DUMMY_ROWS = [
  baseRow({
    id: "dummy-table-ord-001",
    order_number: "DUMMY-TABLE-PO-001",
    customer_name:
      "PT Eternal Packaging Nusantara Cabang Industri Timur — Outlet Cikarang Blok F12 Long Name Preview",
    lat: null,
    lng: null,
    percent_delivered: 5,
    percent_ready_to_ship: 35,
    qty_delivered: 50,
    qty_printed_operator: 350,
    status_label: "Belum Dijadwalkan",
    status: "belum_dijadwalkan",
  }),
  baseRow({
    id: "dummy-table-ord-002",
    order_number: "DUMMY-TABLE-PO-002",
    customer_name: "CV Sumber Makmur Abadi Sejahtera Cabang Bekasi Utara Preview Ellipsis",
    lat: -6.2383,
    lng: 106.9756,
    percent_delivered: 55,
    percent_ready_to_ship: 80,
    qty_delivered: 550,
    qty_printed_operator: 800,
    driver_name: "Driver Preview 2",
  }),
  baseRow({
    id: "dummy-table-ord-003",
    order_number: "DUMMY-TABLE-PO-003",
    customer_name: "UD Maju Jaya Packindo",
    address: "Jl Preview Tanpa Koordinat No. 99",
    lat: null,
    lng: null,
    percent_delivered: 0,
    percent_ready_to_ship: 0,
    qty_delivered: 0,
    qty_printed_operator: 0,
  }),
  baseRow({
    id: "dummy-table-ord-004",
    order_number: "DUMMY-TABLE-PO-004",
    customer_name: "PT Indo Box Multipack International Distribution Center Serpong",
    lat: -6.3015,
    lng: 106.6527,
    percent_delivered: 90,
    percent_ready_to_ship: 100,
    qty_delivered: 900,
    qty_printed_operator: 1000,
    driver_name: "Driver Preview 4",
    status_label: "Terkirim Sebagian",
    status: "terkirim_sebagian",
  }),
  baseRow({
    id: "dummy-table-ord-005",
    order_number: "DUMMY-TABLE-PO-005",
    customer_name: "Toko Packaging Ringkas",
    lat: -6.1754,
    lng: 106.8272,
    percent_delivered: 100,
    percent_ready_to_ship: 100,
    qty_delivered: 1000,
    qty_printed_operator: 1000,
    driver_name: "Driver Preview 5",
    status_label: "Terkirim",
    status: "terkirim",
  }),
];
