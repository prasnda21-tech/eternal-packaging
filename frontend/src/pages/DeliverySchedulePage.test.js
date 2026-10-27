import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import DeliverySchedulePage, { scheduleRowSjNumber, desktopDeliveryPoHref, desktopDeliveryKerjaHref } from "./DeliverySchedulePage";
import { wibTodayKey } from "../lib/deliverySchedule";
import { locationSetupStatusLabel } from "../lib/deliveryRouteBridge";
import { STATUS_LOKASI_SORT_DUMMY_ROWS } from "../lib/deliveryScheduleStatusLokasiFixtures";
import { desktopScheduleReviewHref } from "../lib/mobileDeliverySchedule";

const mockHasPerm = jest.fn(() => false);
const mockDeliverySchedule = jest.fn();
const mockNavigate = jest.fn();

const mockLocation = { pathname: "/produksi/jadwal-pengiriman", search: "" };

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, state, onClick, ...props }) => (
    <a href={typeof to === "string" ? to : "#"} onClick={onClick} {...props}>{children}</a>
  ),
  useLocation: () => mockLocation,
  useNavigate: () => mockNavigate,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ user: { id: "u-ds" }, hasPerm: (perm) => mockHasPerm(perm) }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    deliverySchedule: (...args) => mockDeliverySchedule(...args),
    listDeliveryOutlets: jest.fn(async () => ({ outlets: [] })),
  },
}));

jest.mock("react-day-picker", () => ({
  DayPicker: ({ onSelect }) => (
    <div data-testid="ds-daterange-calendar-mock">
      <button
        type="button"
        data-testid="mock-range-partial"
        onClick={() => onSelect({ from: new Date(2026, 9, 12), to: undefined })}
      >
        partial
      </button>
      <button
        type="button"
        data-testid="mock-range-12-15"
        onClick={() => onSelect({ from: new Date(2026, 9, 12), to: new Date(2026, 9, 15) })}
      >
        12-15
      </button>
      <button
        type="button"
        data-testid="mock-range-24-27"
        onClick={() => onSelect({ from: new Date(2026, 9, 24), to: new Date(2026, 9, 27) })}
      >
        24-27
      </button>
      <button
        type="button"
        data-testid="mock-range-26"
        onClick={() => onSelect({ from: new Date(2026, 9, 26), to: new Date(2026, 9, 26) })}
      >
        26
      </button>
      <button
        type="button"
        data-testid="mock-range-reverse"
        onClick={() => onSelect({ from: new Date(2026, 9, 15), to: new Date(2026, 9, 12) })}
      >
        reverse
      </button>
      <button
        type="button"
        data-testid="mock-range-cross-month"
        onClick={() => onSelect({ from: new Date(2026, 11, 28), to: new Date(2027, 0, 3) })}
      >
        cross
      </button>
    </div>
  ),
}));

jest.mock("react-day-picker/dist/style.css", () => ({}), { virtual: true });

jest.mock("../components/SuratJalanPreviewModal", () => ({
  __esModule: true,
  default: ({ open }) => (open ? <div data-testid="sj-preview-modal" /> : null),
}));

jest.mock("../components/OutletLocationModal", () => ({
  __esModule: true,
  default: ({ open }) => (open ? <div data-testid="ds-outlet-modal" /> : null),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const sampleRows = [
  {
    id: "ord-a",
    order_number: "PO-100",
    customer_name: "PT Alpha",
    status: "terjadwal",
    status_label: "Terjadwal",
    effective_delivery_date: "2026-09-10",
    deadline: "2026-09-12",
    jadwal_manual: null,
    driver_name: "Budi",
    percent_delivered: 40,
    percent_ready_to_ship: 60,
    qty_delivered: 2000,
    qty_total: 5000,
    qty_printed_operator: 3000,
    print_qty_total: 5000,
    sj_files: [],
    sj_file_count: 0,
    lat: -6.2,
    lng: 106.8,
  },
  {
    id: "ord-b",
    order_number: "PO-200",
    customer_name: "PT Beta",
    status: "terjadwal",
    status_label: "Terjadwal",
    effective_delivery_date: "2026-09-11",
    deadline: "2026-09-13",
    jadwal_manual: null,
    driver_name: null,
    percent_delivered: 0,
    percent_ready_to_ship: 100,
    qty_delivered: 0,
    qty_total: 1000,
    qty_printed_operator: 1000,
    print_qty_total: 1000,
    sj_files: [],
    sj_file_count: 0,
    lat: null,
    lng: null,
    address: "Jl Hanya Teks",
  },
];

describe("delivery reschedule UI (static)", () => {
  const proof = fs.readFileSync(path.join(__dirname, "../components/DeliveryProofPanel.jsx"), "utf8");
  const modal = fs.readFileSync(path.join(__dirname, "../components/RescheduleDeliveryModal.jsx"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const schedule = fs.readFileSync(path.join(__dirname, "DeliverySchedulePage.jsx"), "utf8");
  const reschedule = fs.readFileSync(path.join(__dirname, "DeliveryReschedulePage.jsx"), "utf8");

  test("driver panel keeps upload confirm and adds reschedule", () => {
    expect(proof).toMatch(/Konfirmasi Terkirim/);
    expect(proof).toMatch(/confirm-delivered-button/);
    expect(proof).toMatch(/Jadwalkan Ulang/);
    expect(proof).toMatch(/reschedule-delivery-button/);
    expect(proof).toMatch(/Upload foto tidak menandai Terkirim/);
  });

  test("reschedule modal reason required and date permission", () => {
    expect(modal).toMatch(/JADWALKAN ULANG PENGIRIMAN/);
    expect(modal).toMatch(/reschedule-reason/);
    expect(modal).toMatch(/Alasan penjadwalan ulang wajib diisi/);
    expect(modal).toMatch(/canSetDate/);
    expect(modal).toMatch(/reschedule-new-date/);
    expect(modal).toMatch(/Batal/);
    expect(modal).toMatch(/rescheduleConfirmLabel/);
    expect(modal).toMatch(/newActionId/);
  });

  test("sidebar Produksi hierarchy without old labels", () => {
    expect(shell).toMatch(/produksi: "Produksi"/);
    expect(nav).toMatch(/Jadwal Produksi/);
    expect(nav).toMatch(/Jadwal Pengiriman Ulang/);
    expect(shell).toMatch(/Tugas Produksi/);
    expect(shell).not.toMatch(/Tugas per Divisi/);
    expect(nav).toMatch(/"\/produksi"/);
    expect(nav).toMatch(/jadwal-pengiriman-ulang/);
  });

  test("routes preserve jadwal produksi and add schedule pages", () => {
    expect(app).toMatch(/path="\/produksi"/);
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman"/);
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman-ulang"/);
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman\/buat"/);
    expect(app).toMatch(/Navigate to="\/produksi\/jadwal-pengiriman"/);
    expect(app).toMatch(/path="\/produksi\/rute-saya"/);
    expect(app).toMatch(/Navigate to="\/"/);
    expect(app).toMatch(/path="\/tasks\/delivery"/);
  });

  test("jadwal pengiriman table uses single Tanggal Pengiriman column", () => {
    expect(schedule).toMatch(/Tanggal Pengiriman/);
    expect(schedule).toMatch(/effective_delivery_date/);
    expect(schedule).not.toMatch(/>Deadline</);
    expect(schedule).not.toMatch(/Jadwal Manual/);
    expect(schedule).not.toMatch(/ds-sort-deadline/);
    expect(schedule).not.toMatch(/ds-sort-manual/);
    expect(schedule).toMatch(/DeliveryPlanDateRangeField/);
    expect(schedule).toMatch(/ds-period/);
    expect(schedule).toMatch(/ds-filter-apply/);
    expect(schedule).toMatch(/ds-filter-reset/);
    expect(schedule).not.toMatch(/Tanggal Mulai/);
    expect(schedule).not.toMatch(/Tanggal Akhir/);
    expect(schedule).not.toMatch(/Buat Jadwal Pengiriman/);
    expect(schedule).not.toMatch(/delivery.schedule.create/);
    expect(schedule).toMatch(/Atur Lokasi/);
    expect(schedule).toMatch(/OutletLocationModal/);
  });

  test("old Pengiriman Hari Ini route redirects to Jadwal Pengiriman", () => {
    expect(app).toMatch(/path="\/produksi\/pengiriman-hari-ini"/);
    expect(app).toMatch(/Navigate to="\/produksi\/jadwal-pengiriman\?status=hari_ini"/);
  });

  test("jadwal has progress columns and no shipment item", () => {
    expect(schedule).toMatch(/Terkirim/);
    expect(schedule).toMatch(/Dapat Dikirim/);
    expect(schedule).not.toMatch(/Shipment \/ Item/);
    expect(schedule).toMatch(/ds-filter-btn/);
    expect(schedule).toMatch(/DeliveryPlanAdvancedTable/);
    expect(schedule).toMatch(/customer_name/);
  });

  test("desktop Optimasi Rute uses desktop review href (not /m/...) and permissions", () => {
    expect(schedule).toMatch(/canOptimizeFromDeliverySchedule/);
    expect(schedule).toMatch(/desktopScheduleReviewHref/);
    expect(schedule).not.toMatch(/mobileScheduleReviewHref/);
    expect(schedule).toMatch(/locationSetupStatusLabel/);
    expect(schedule).toMatch(/ds-optimasi-rute/);
    expect(schedule).toMatch(/Status Lokasi/);
    expect(schedule).toMatch(/location_status_sort/);
    expect(schedule).toMatch(/navigate\(/);
    expect(schedule).not.toMatch(/window\.open/);
    expect(schedule).not.toMatch(/target=["']_blank["']/);
    expect(schedule).not.toMatch(/role\s*===\s*['"]ADMIN['"]/);
    expect(schedule).not.toMatch(/role\s*===\s*['"]DRIVER['"]/);
    expect(schedule).not.toMatch(/OPTIMIZE RUTE/);
    expect(schedule).not.toMatch(/Rute Saya/);
    expect(schedule).not.toMatch(/Buat Jadwal Pengiriman/);
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman\/review-optimasi"/);
    expect(app).toMatch(/DeliveryScheduleRouteReviewPage/);
  });

  test("reschedule page has reason history and set date action", () => {
    expect(reschedule).toMatch(/Alasan/);
    expect(reschedule).toMatch(/Tanggal Sebelumnya/);
    expect(reschedule).toMatch(/Atur Jadwal/);
    expect(reschedule).toMatch(/delivery.reschedule.set_date/);
    expect(reschedule).toMatch(/Menunggu Penjadwalan/);
  });
});

describe("DeliverySchedulePage Optimasi Rute + Status Lokasi", () => {
  let container;
  let root;

  beforeEach(() => {
    mockLocation.pathname = "/produksi/jadwal-pengiriman";
    mockLocation.search = "";
    sessionStorage.clear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockDeliverySchedule.mockReset();
    mockNavigate.mockReset();
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("authorized user sees Optimasi Rute action", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "route_optimizer.create" || p === "route_optimizer.optimize"
    ));
    await act(async () => {
      root.render(<DeliverySchedulePage />);
    });
    expect(container.querySelector('[data-testid="ds-optimasi-rute"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-select-all"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-checkbox-PO-100"]')).toBeTruthy();
  });

  test("unauthorized user does not see Optimasi Rute", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.location.manage");
    await act(async () => {
      root.render(<DeliverySchedulePage />);
    });
    expect(container.querySelector('[data-testid="ds-optimasi-rute"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-select-all"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-checkbox-PO-100"]')).toBeFalsy();
  });

  test("Optimasi Rute is disabled when no rows selected", async () => {
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "route_optimizer.manage"
    ));
    await act(async () => {
      root.render(<DeliverySchedulePage />);
    });
    const btn = container.querySelector('[data-testid="ds-optimasi-rute"]');
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(true);
  });

  test("clicking Optimasi Rute navigates to desktop review with selected canonical ids", async () => {
    const open = jest.fn();
    const origOpen = window.open;
    window.open = open;
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "route_optimizer.create" || p === "route_optimizer.optimize"
    ));
    await act(async () => {
      root.render(<DeliverySchedulePage />);
    });
    const boxA = container.querySelector('[data-testid="ds-checkbox-PO-100"]');
    const boxB = container.querySelector('[data-testid="ds-checkbox-PO-200"]');
    await act(async () => {
      boxA.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      boxB.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const btn = container.querySelector('[data-testid="ds-optimasi-rute"]');
    expect(btn.disabled).toBe(false);
    await act(async () => {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    // Newest-first default puts ord-b (later date) before ord-a in selection order.
    expect(mockNavigate).toHaveBeenCalledWith(desktopScheduleReviewHref(["ord-b", "ord-a"]));
    expect(mockNavigate.mock.calls[0][0]).toMatch(/\/produksi\/jadwal-pengiriman\/review-optimasi\?ids=/);
    expect(mockNavigate.mock.calls[0][0]).not.toMatch(/\/m\//);
    expect(mockNavigate.mock.calls[0][0]).toContain("ord-a");
    expect(mockNavigate.mock.calls[0][0]).toContain("ord-b");
    expect(mockNavigate.mock.calls[0][0]).toContain("ord-b");
    expect(mockNavigate.mock.calls[0][0]).not.toContain("PO-100");
    expect(open).not.toHaveBeenCalled();
    window.open = origOpen;
  });

  test("Status Lokasi uses canonical helper for valid and invalid coordinates", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        { ...sampleRows[0], lat: -6.2, lng: 106.8 },
        { ...sampleRows[1], lat: null, lng: null, address: "Jl Ada" },
        {
          ...sampleRows[0],
          id: "ord-zero",
          order_number: "PO-ZERO",
          lat: 0,
          lng: 0,
        },
        {
          ...sampleRows[0],
          id: "ord-bad",
          order_number: "PO-BAD",
          lat: "x",
          lng: "y",
        },
        {
          ...sampleRows[0],
          id: "ord-oor",
          order_number: "PO-OOR",
          lat: 95,
          lng: 106.8,
        },
      ],
    });
    await act(async () => {
      root.render(<DeliverySchedulePage />);
    });
    expect(container.querySelector('[data-testid="dp-sort-delivery-schedule-location_status_sort"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-status-lokasi-PO-100"]').textContent).toBe("Sudah Diatur");
    expect(container.querySelector('[data-testid="ds-status-lokasi-PO-200"]').textContent).toBe("Belum Diatur");
    expect(container.querySelector('[data-testid="ds-status-lokasi-PO-ZERO"]').textContent).toBe("Belum Diatur");
    expect(container.querySelector('[data-testid="ds-status-lokasi-PO-BAD"]').textContent).toBe("Belum Diatur");
    expect(container.querySelector('[data-testid="ds-status-lokasi-PO-OOR"]').textContent).toBe("Belum Diatur");
    // Address alone never flips helper
    expect(locationSetupStatusLabel({ address: "Jl Ada", lat: null, lng: null })).toBe("Belum Diatur");
  });

  test("Status Lokasi header sorts ASC then DESC using canonical coords and dummy POs", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    expect(STATUS_LOKASI_SORT_DUMMY_ROWS.length).toBeGreaterThanOrEqual(3);
    mockDeliverySchedule.mockResolvedValue({ rows: STATUS_LOKASI_SORT_DUMMY_ROWS });
    await act(async () => {
      root.render(<DeliverySchedulePage />);
    });

    const sortBtn = container.querySelector('[data-testid="dp-sort-delivery-schedule-location_status_sort"]');
    expect(sortBtn).toBeTruthy();

    const labelsInOrder = () => Array.from(
      container.querySelectorAll('[data-testid^="ds-status-lokasi-"]'),
    ).map((el) => el.textContent);

    const poInOrder = () => Array.from(
      container.querySelectorAll('tbody tr[data-testid^="ds-row-"]'),
    ).map((el) => el.getAttribute("data-testid").replace("ds-row-", ""));

    // ASC: Belum before Sudah; address-only DUMMY-PO-003 stays Belum
    await act(async () => {
      sortBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(labelsInOrder()).toEqual([
      "Belum Diatur",
      "Belum Diatur",
      "Sudah Diatur",
      "Sudah Diatur",
    ]);
    expect(poInOrder()).toEqual([
      "DUMMY-PO-001",
      "DUMMY-PO-003",
      "DUMMY-PO-002",
      "DUMMY-PO-004",
    ]);
    expect(container.querySelector('[data-testid="ds-status-lokasi-DUMMY-PO-003"]').textContent)
      .toBe("Belum Diatur");
    expect(container.querySelector('[data-testid="ds-status-lokasi-DUMMY-PO-002"]').textContent)
      .toBe("Sudah Diatur");

    // DESC: Sudah before Belum
    await act(async () => {
      sortBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(labelsInOrder()).toEqual([
      "Sudah Diatur",
      "Sudah Diatur",
      "Belum Diatur",
      "Belum Diatur",
    ]);
    expect(poInOrder()).toEqual([
      "DUMMY-PO-002",
      "DUMMY-PO-004",
      "DUMMY-PO-001",
      "DUMMY-PO-003",
    ]);

    // Customer sort still works after location sort
    const customerBtn = container.querySelector('[data-testid="dp-sort-delivery-schedule-customer_name"]');
    await act(async () => {
      customerBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(poInOrder()[0]).toBe("DUMMY-PO-003"); // "Dummy Customer Alamat Saja"
  });
});

function fireChange(el, value) {
  const proto = el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(el, value);
  else el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("scheduleRowSjNumber precedence", () => {
  test("prefers sj_label over latest_sj_number and sj_numbers", () => {
    expect(scheduleRowSjNumber({
      sj_label: "SJ/001",
      latest_sj_number: "SJ/999",
      sj_numbers: ["SJ/888"],
    })).toBe("SJ/001");
  });

  test("falls back to shipment-scoped latest_sj_number then same-row sj_numbers[0]", () => {
    expect(scheduleRowSjNumber({
      sj_label: "",
      latest_sj_number: "SJ/002",
      sj_numbers: ["SJ/003"],
    })).toBe("SJ/002");
    expect(scheduleRowSjNumber({
      sj_label: "  ",
      latest_sj_number: "",
      sj_numbers: ["SJ/003"],
    })).toBe("SJ/003");
  });

  test("empty canonical fields do not invent a number", () => {
    expect(scheduleRowSjNumber({
      sj_label: "",
      latest_sj_number: "",
      sj_numbers: [],
      sj_files: [{ id: "file-1", sj_number: "SJ/FAKE" }],
    })).toBe("");
    expect(scheduleRowSjNumber(null)).toBe("");
  });
});

describe("DeliverySchedulePage Surat Jalan column", () => {
  let container;
  let root;

  beforeEach(() => {
    mockLocation.pathname = "/produksi/jadwal-pengiriman";
    mockLocation.search = "";
    sessionStorage.clear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockDeliverySchedule.mockReset();
    mockNavigate.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    sessionStorage.clear();
  });

  test("renders shipment-specific SJ number without sj_files", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        shipment_id: "sh-1",
        sj_label: "SJ/001",
        latest_sj_number: "SJ/001",
        sj_numbers: ["SJ/001"],
        sj_files: [],
        sj_file_count: 0,
      }],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    const cell = container.querySelector('[data-testid="ds-sj-PO-100-sh-1"]');
    expect(cell).toBeTruthy();
    expect(cell.textContent).toBe("SJ/001");
    expect(container.querySelector('[data-testid="ds-sj-lihat-PO-100"]')).toBeFalsy();
  });

  test("falls back to latest_sj_number when sj_label is empty", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        shipment_id: "sh-2",
        sj_label: "",
        latest_sj_number: "SJ/002",
        sj_numbers: [],
        sj_files: undefined,
      }],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="ds-sj-PO-100-sh-2"]').textContent).toBe("SJ/002");
  });

  test("shows em dash when no canonical SJ number exists", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        sj_label: "",
        latest_sj_number: "",
        sj_numbers: [],
        sj_files: [],
      }],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="ds-sj-PO-100"]').textContent).toBe("—");
  });

  test("same PO two shipments keep isolated SJ numbers; later SJ does not overwrite earlier row", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          id: "po-x",
          order_number: "PO-X",
          shipment_id: "sh-a",
          effective_delivery_date: "2026-09-12",
          status: "hari_ini",
          status_label: "Hari Ini",
          sj_label: "SJ/001",
          latest_sj_number: "SJ/001",
          sj_numbers: ["SJ/001"],
          sj_files: [],
        },
        {
          ...sampleRows[0],
          id: "po-x",
          order_number: "PO-X",
          shipment_id: "sh-b",
          customer_name: "PT Alpha",
          effective_delivery_date: "2026-09-14",
          status: "terjadwal",
          status_label: "Terjadwal",
          sj_label: "SJ/002",
          latest_sj_number: "SJ/002",
          sj_numbers: ["SJ/002"],
          sj_files: [],
        },
      ],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="ds-sj-PO-X-sh-a"]').textContent).toBe("SJ/001");
    expect(container.querySelector('[data-testid="ds-sj-PO-X-sh-b"]').textContent).toBe("SJ/002");
    expect(container.querySelector('[data-testid="ds-row-PO-X-sh-a"]').textContent).not.toContain("SJ/002");
  });

  test("status=hari_ini keeps shipment-specific SJ on qualifying rows", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          id: "po-x",
          order_number: "PO-X",
          shipment_id: "sh-a",
          effective_delivery_date: wibTodayKey(),
          status: "hari_ini",
          status_label: "Hari Ini",
          sj_label: "SJ/001",
          latest_sj_number: "SJ/001",
        },
        {
          ...sampleRows[0],
          id: "po-x",
          order_number: "PO-X",
          shipment_id: "sh-b",
          effective_delivery_date: "2026-09-21",
          status: "terjadwal",
          status_label: "Terjadwal",
          sj_label: "SJ/002",
          latest_sj_number: "SJ/002",
        },
      ],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    const status = container.querySelector('[data-testid="ds-filter-status"]');
    await act(async () => { fireChange(status, "hari_ini"); });
    await act(async () => {
      container.querySelector('[data-testid="ds-filter-apply"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-sj-PO-X-sh-a"]').textContent).toBe("SJ/001");
    expect(container.querySelector('[data-testid="ds-row-PO-X-sh-b"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-sj-PO-X-sh-a"]').textContent).not.toBe("SJ/002");
  });

  test("PO search filter preserves SJ identity on remaining row", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          shipment_id: "sh-1",
          sj_label: "SJ/001",
          latest_sj_number: "SJ/001",
        },
        {
          ...sampleRows[1],
          shipment_id: "sh-9",
          sj_label: "SJ/BETA",
          latest_sj_number: "SJ/BETA",
        },
      ],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    const q = container.querySelector('[data-testid="ds-filter-q"]');
    await act(async () => { fireChange(q, "PO-100"); });
    await act(async () => {
      container.querySelector('[data-testid="ds-filter-apply"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-sj-PO-100-sh-1"]').textContent).toBe("SJ/001");
    expect(container.querySelector('[data-testid="ds-row-PO-200-sh-9"]')).toBeFalsy();
  });

  test("file button still requires delivery_note.view_file; number stays visible without it", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        shipment_id: "sh-1",
        sj_label: "SJ/001",
        latest_sj_number: "SJ/001",
        sj_files: [{ id: "file-1", file_id: "file-1", name: "SJ.pdf" }],
        sj_file_count: 1,
      }],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="ds-sj-PO-100-sh-1"]').textContent).toBe("SJ/001");
    expect(container.querySelector('[data-testid="ds-sj-lihat-PO-100"]')).toBeFalsy();
  });

  test("file button renders when files exist and delivery_note.view_file is granted", async () => {
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery_note.view_file");
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        shipment_id: "sh-1",
        sj_label: "SJ/001",
        latest_sj_number: "SJ/001",
        sj_files: [{ id: "file-1", file_id: "file-1", name: "SJ.pdf" }],
        sj_file_count: 1,
      }],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="ds-sj-PO-100-sh-1"]').textContent).toBe("SJ/001");
    const btn = container.querySelector('[data-testid="ds-sj-lihat-PO-100"]');
    expect(btn).toBeTruthy();
    expect(btn.textContent).toBe("Lihat");
  });
});

describe("DeliverySchedulePage desktop exact-SJ hrefs", () => {
  test("helpers preserve exact shipment on PO and Kerjakan links", () => {
    expect(desktopDeliveryPoHref("ord-demo", "demo-multisj-photo-001"))
      .toBe("/po/ord-demo?shipment=demo-multisj-photo-001");
    expect(desktopDeliveryPoHref("ord-demo", "demo-multisj-photo-002"))
      .toBe("/po/ord-demo?shipment=demo-multisj-photo-002");
    expect(desktopDeliveryPoHref("ord-demo", "demo-multisj-photo-003"))
      .toBe("/po/ord-demo?shipment=demo-multisj-photo-003");
    expect(desktopDeliveryKerjaHref("ord-demo", "demo-multisj-photo-001"))
      .toBe("/po/ord-demo/kerja?divisi=terkirim&shipment=demo-multisj-photo-001");
    expect(desktopDeliveryKerjaHref("ord-demo", "demo-multisj-photo-002"))
      .toBe("/po/ord-demo/kerja?divisi=terkirim&shipment=demo-multisj-photo-002");
    expect(desktopDeliveryKerjaHref("ord-demo", "demo-multisj-photo-003"))
      .toBe("/po/ord-demo/kerja?divisi=terkirim&shipment=demo-multisj-photo-003");
  });

  test("helpers fail closed when shipment_id is missing — no invented identity", () => {
    expect(desktopDeliveryPoHref("ord-demo", "")).toBe("/po/ord-demo");
    expect(desktopDeliveryPoHref("ord-demo", null)).toBe("/po/ord-demo");
    expect(desktopDeliveryKerjaHref("ord-demo", "")).toBe("/po/ord-demo/kerja?divisi=terkirim");
    expect(desktopDeliveryKerjaHref("", "demo-multisj-photo-002")).toBe("");
  });

  test("SJ 001/002/003 rows keep shipment on PO and Kerjakan links", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          id: "order-demo-multisj-photo",
          order_number: "DEMO-MULTISJ-PHOTO-PO",
          shipment_id: "demo-multisj-photo-001",
          sj_label: "DEMO-MULTISJ-PHOTO-001",
          latest_sj_number: "DEMO-MULTISJ-PHOTO-001",
          sj_numbers: ["DEMO-MULTISJ-PHOTO-001"],
          sj_files: [],
        },
        {
          ...sampleRows[0],
          id: "order-demo-multisj-photo",
          order_number: "DEMO-MULTISJ-PHOTO-PO",
          shipment_id: "demo-multisj-photo-002",
          sj_label: "DEMO-MULTISJ-PHOTO-002",
          latest_sj_number: "DEMO-MULTISJ-PHOTO-002",
          sj_numbers: ["DEMO-MULTISJ-PHOTO-002"],
          sj_files: [],
        },
        {
          ...sampleRows[0],
          id: "order-demo-multisj-photo",
          order_number: "DEMO-MULTISJ-PHOTO-PO",
          shipment_id: "demo-multisj-photo-003",
          sj_label: "DEMO-MULTISJ-PHOTO-003",
          latest_sj_number: "DEMO-MULTISJ-PHOTO-003",
          sj_numbers: ["DEMO-MULTISJ-PHOTO-003"],
          sj_files: [],
        },
      ],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });

    const po001 = container.querySelector('[data-testid="ds-po-link-DEMO-MULTISJ-PHOTO-PO-demo-multisj-photo-001"]');
    const po002 = container.querySelector('[data-testid="ds-po-link-DEMO-MULTISJ-PHOTO-PO-demo-multisj-photo-002"]');
    const po003 = container.querySelector('[data-testid="ds-po-link-DEMO-MULTISJ-PHOTO-PO-demo-multisj-photo-003"]');
    const kerja001 = container.querySelector('[data-testid="ds-kerja-link-DEMO-MULTISJ-PHOTO-PO-demo-multisj-photo-001"]');
    const kerja002 = container.querySelector('[data-testid="ds-kerja-link-DEMO-MULTISJ-PHOTO-PO-demo-multisj-photo-002"]');
    const kerja003 = container.querySelector('[data-testid="ds-kerja-link-DEMO-MULTISJ-PHOTO-PO-demo-multisj-photo-003"]');

    expect(po001.getAttribute("href")).toContain("shipment=demo-multisj-photo-001");
    expect(po002.getAttribute("href")).toContain("shipment=demo-multisj-photo-002");
    expect(po003.getAttribute("href")).toContain("shipment=demo-multisj-photo-003");
    expect(kerja001.getAttribute("href")).toContain("shipment=demo-multisj-photo-001");
    expect(kerja002.getAttribute("href")).toContain("shipment=demo-multisj-photo-002");
    expect(kerja003.getAttribute("href")).toContain("shipment=demo-multisj-photo-003");
    expect(kerja001.getAttribute("href")).toContain("divisi=terkirim");
    expect(po002.getAttribute("href")).not.toContain("demo-multisj-photo-001");
    expect(kerja003.getAttribute("href")).not.toContain("demo-multisj-photo-001");

    act(() => root.unmount());
    container.remove();
  });
});

describe("DeliverySchedulePage table without Driver column", () => {
  const scheduleSource = fs.readFileSync(
    path.join(__dirname, "DeliverySchedulePage.jsx"),
    "utf8",
  );
  let container;
  let root;

  const setupDom = () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  };

  const renderSchedule = async ({ canOptimize = true, rows = sampleRows } = {}) => {
    const allowed = new Set([
      "schedule.view",
      "route_optimizer.create",
      "route_optimizer.optimize",
      "delivery.location.manage",
    ]);
    mockHasPerm.mockImplementation((permission) => (
      canOptimize ? allowed.has(permission) : permission === "schedule.view"
    ));
    mockDeliverySchedule.mockResolvedValue({ rows });
    await act(async () => {
      root.render(<DeliverySchedulePage />);
    });
  };

  beforeEach(() => {
    mockLocation.pathname = "/produksi/jadwal-pengiriman";
    mockLocation.search = "";
    sessionStorage.clear();
    mockHasPerm.mockReset();
    mockDeliverySchedule.mockReset();
    mockNavigate.mockReset();
    setupDom();
  });

  afterEach(() => {
    if (root) act(() => root.unmount());
    if (container?.isConnected) container.remove();
    sessionStorage.clear();
  });

  test("static: table header and row no longer render the Driver column", () => {
    expect(scheduleSource).not.toMatch(/data-testid="ds-sort-driver"/);
    expect(scheduleSource).not.toMatch(/<td>{r\.driver_name \|\| "—"}<\/td>/);
    expect(scheduleSource).toMatch(/data-testid="ds-adv-driver"/);
    expect(scheduleSource).toMatch(/DeliveryPlanAdvancedTable/);
    expect(scheduleSource).toMatch(/delivery-schedule-pager/);
  });

  test("Status Lokasi is followed directly by Terkirim", async () => {
    await renderSchedule();
    const headers = Array.from(container.querySelectorAll("thead th"));
    const labels = headers.map((header) => header.textContent.replace(/\s+/g, " ").trim());
    expect(labels.some((l) => l.includes("Driver"))).toBe(false);
    const lokasiIdx = labels.findIndex((l) => l.includes("Status Lokasi"));
    const terkirimIdx = labels.findIndex((l) => l.includes("Terkirim"));
    expect(lokasiIdx).toBeGreaterThanOrEqual(0);
    expect(terkirimIdx).toBe(lokasiIdx + 1);
    expect(headers).toHaveLength(10);
  });

  test("user without route optimization sees nine headers and no Driver", async () => {
    await renderSchedule({ canOptimize: false });
    const headers = Array.from(container.querySelectorAll("thead th"));
    expect(headers).toHaveLength(9);
    expect(headers.map((header) => header.textContent).join(" ")).not.toMatch(/Driver/);
    expect(container.querySelector('[data-testid="ds-select-all"]')).toBeNull();
  });

  test("authorized row has ten cells and does not render driver name", async () => {
    await renderSchedule();
    const row = container.querySelector('[data-testid="ds-row-PO-100"]');
    expect(row).toBeTruthy();
    expect(row.querySelectorAll("td")).toHaveLength(10);
    expect(row.textContent).not.toContain("Budi");
    expect(row.querySelector('[data-testid="ds-terkirim-PO-100"]')).toBeTruthy();
    expect(row.querySelector('[data-testid="ds-dapat-PO-100"]')).toBeTruthy();
  });

  test("row without route optimization has exactly nine cells", async () => {
    await renderSchedule({ canOptimize: false });
    const row = container.querySelector('[data-testid="ds-row-PO-100"]');
    expect(row).toBeTruthy();
    expect(row.querySelectorAll("td")).toHaveLength(9);
    expect(row.textContent).not.toContain("Budi");
  });

  test("empty-state colSpan matches both permission variants", async () => {
    await renderSchedule({ rows: [] });
    expect(container.querySelector('[data-testid="delivery-schedule-table-empty"]').colSpan).toBe(10);

    act(() => root.unmount());
    container.remove();
    setupDom();
    await renderSchedule({ canOptimize: false, rows: [] });
    expect(container.querySelector('[data-testid="delivery-schedule-table-empty"]').colSpan).toBe(9);
  });

  test("driver filter data and existing row actions remain available", async () => {
    await renderSchedule();
    const filterButton = container.querySelector('[data-testid="ds-filter-btn"]');
    await act(async () => {
      filterButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-adv-driver"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-kerja-link-PO-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-atur-lokasi-PO-100"]')).toBeTruthy();
  });
});

describe("DeliverySchedulePage draft/applied filters + date range", () => {
  let container;
  let root;

  const click = async (testid) => {
    await act(async () => {
      container.querySelector(`[data-testid="${testid}"]`)
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  };

  beforeEach(() => {
    mockLocation.pathname = "/produksi/jadwal-pengiriman";
    mockLocation.search = "";
    sessionStorage.clear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockDeliverySchedule.mockReset();
    mockNavigate.mockReset();
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    sessionStorage.clear();
  });

  test("toolbar has Plan-parity header, combined search, date range, Terapkan/Reset", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector(".dp-header")).toBeTruthy();
    expect(container.textContent).toMatch(/Produksi · Pengiriman/);
    expect(container.querySelector('[data-testid="ds-filter-q"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-filter-po"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-filter-customer"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-period-trigger"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-filter-apply"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-filter-reset"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-refresh"]')).toBeTruthy();
    const status = container.querySelector('[data-testid="ds-filter-status"]');
    const labels = Array.from(status.options).map((o) => o.textContent);
    expect(labels).toContain("Tanggal Hari Ini");
    expect(labels).not.toContain("Belum Dijadwalkan");
    expect(Array.from(status.options).map((o) => o.value)).not.toContain("belum_dijadwalkan");
  });

  test("draft edits do not filter until Terapkan; Reset clears applied + draft", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="ds-row-PO-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-row-PO-200"]')).toBeTruthy();

    await act(async () => { fireChange(container.querySelector('[data-testid="ds-filter-q"]'), "PO-100"); });
    expect(container.querySelector('[data-testid="ds-row-PO-200"]')).toBeTruthy();

    await click("ds-filter-apply");
    expect(container.querySelector('[data-testid="ds-row-PO-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-row-PO-200"]')).toBeFalsy();

    await click("ds-filter-reset");
    expect(container.querySelector('[data-testid="ds-filter-q"]').value).toBe("");
    expect(container.querySelector('[data-testid="ds-row-PO-200"]')).toBeTruthy();
  });

  test("combined search matches customer OR PO after Terapkan", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await act(async () => { fireChange(container.querySelector('[data-testid="ds-filter-q"]'), "Beta"); });
    await click("ds-filter-apply");
    expect(container.querySelector('[data-testid="ds-row-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-row-PO-200"]')).toBeTruthy();

    await click("ds-filter-reset");
    await act(async () => { fireChange(container.querySelector('[data-testid="ds-filter-q"]'), "PO-100"); });
    await click("ds-filter-apply");
    expect(container.querySelector('[data-testid="ds-row-PO-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-row-PO-200"]')).toBeFalsy();
  });

  test("date range two-click applies to draft then Terapkan filters inclusively; reverse sorts", async () => {
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        { ...sampleRows[0], effective_delivery_date: "2026-10-12" },
        { ...sampleRows[1], order_number: "PO-mid", id: "ord-mid", effective_delivery_date: "2026-10-13" },
        {
          ...sampleRows[1],
          id: "ord-out",
          order_number: "PO-out",
          effective_delivery_date: "2026-10-20",
        },
      ],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await click("ds-period-trigger");
    expect(container.querySelector('[data-testid="ds-period-panel"]')).toBeTruthy();
    await click("mock-range-12-15");
    expect(container.querySelector('[data-testid="ds-period-value"]').textContent).toMatch(/12 Okt 2026/);
    expect(container.querySelector('[data-testid="ds-row-PO-out"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-filter-pending"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-filter-pending"]').textContent).toMatch(/Belum diterapkan/);
    expect(container.querySelector('[data-testid="ds-draft-date-label"]').textContent).toMatch(/12 Okt 2026/);
    expect(container.querySelector('[data-testid="ds-applied-date-label"]').textContent).toBe("Semua tanggal");
    await click("ds-filter-apply");
    expect(container.querySelector('[data-testid="ds-filter-pending"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-row-PO-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-row-PO-mid"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-row-PO-out"]')).toBeFalsy();

    await click("ds-filter-reset");
    await click("ds-period-trigger");
    await click("mock-range-reverse");
    expect(container.querySelector('[data-testid="ds-period-value"]').textContent).toBe("12 Okt 2026 – 15 Okt 2026");
  });

  test("dismiss incomplete range does not apply partial dates", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await click("ds-period-trigger");
    await click("mock-range-partial");
    expect(container.querySelector('[data-testid="ds-period-panel"]')).toBeTruthy();
    await click("ds-period-dismiss");
    expect(container.querySelector('[data-testid="ds-period-panel"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-period-value"]').textContent).toBe("Semua tanggal");
  });

  test("Tanggal Hari Ini includes delivered SJ dated today and excludes other dates", async () => {
    const today = wibTodayKey();
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          order_number: "PO-DELIVERED-TODAY",
          id: "ord-dt",
          effective_delivery_date: today,
          status: "terkirim",
          status_label: "Terkirim",
        },
        {
          ...sampleRows[1],
          order_number: "PO-OTHER-DAY",
          id: "ord-od",
          effective_delivery_date: "2020-01-01",
          status: "terjadwal",
          status_label: "Terjadwal",
        },
      ],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await act(async () => { fireChange(container.querySelector('[data-testid="ds-filter-status"]'), "hari_ini"); });
    await click("ds-filter-apply");
    expect(container.querySelector('[data-testid="ds-row-PO-DELIVERED-TODAY"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-row-PO-OTHER-DAY"]')).toBeFalsy();
  });

  test("advanced modal shares draft with toolbar Terapkan", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await click("ds-filter-btn");
    expect(container.querySelector('[data-testid="ds-filter-panel"]')).toBeTruthy();
    await act(async () => {
      fireChange(container.querySelector('[data-testid="ds-adv-q"]'), "Beta");
    });
    expect(container.querySelector('[data-testid="ds-filter-q"]').value).toBe("Beta");
    await click("ds-adv-apply");
    expect(container.querySelector('[data-testid="ds-row-PO-100"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-row-PO-200"]')).toBeTruthy();
  });

  test("?status=hari_ini bookmark applies without Terapkan; legacy belum_dijadwalkan clears", async () => {
    mockLocation.search = "?status=hari_ini";
    const today = wibTodayKey();
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        { ...sampleRows[0], effective_delivery_date: today, status: "terkirim", status_label: "Terkirim" },
        { ...sampleRows[1], effective_delivery_date: "2019-01-01", status: "terjadwal" },
      ],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="ds-filter-status"]').value).toBe("hari_ini");
    expect(container.querySelector('[data-testid="ds-row-PO-100"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-row-PO-200"]')).toBeFalsy();

    act(() => root.unmount());
    container.remove();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockNavigate.mockClear();
    mockLocation.search = "?status=belum_dijadwalkan";
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(mockNavigate).toHaveBeenCalled();
    expect(mockNavigate.mock.calls[0][0]).toEqual({
      pathname: "/produksi/jadwal-pengiriman",
      search: "",
    });
  });

  test("refresh keeps applied filters; session restore normalizes legacy status", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await act(async () => { fireChange(container.querySelector('[data-testid="ds-filter-q"]'), "PO-100"); });
    await click("ds-filter-apply");
    expect(mockDeliverySchedule).toHaveBeenCalledTimes(1);
    await click("ds-refresh");
    expect(mockDeliverySchedule).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[data-testid="ds-filter-q"]').value).toBe("PO-100");
    expect(container.querySelector('[data-testid="ds-row-PO-200"]')).toBeFalsy();

    act(() => root.unmount());
    container.remove();
    const key = Object.keys(sessionStorage).find((k) => k.includes("delivery-schedule"));
    expect(key).toBeTruthy();
    const raw = JSON.parse(sessionStorage.getItem(key));
    raw.filters = { ...raw.filters, status: "belum_dijadwalkan" };
    sessionStorage.setItem(key, JSON.stringify(raw));
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="ds-filter-status"]').value).toBe("semua");
  });

  test("cross-year range label and driver+date AND compose after apply", async () => {
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        {
          ...sampleRows[0],
          order_number: "PO-DRV",
          id: "ord-drv",
          driver_name: "Budi",
          effective_delivery_date: "2026-12-30",
        },
        {
          ...sampleRows[1],
          order_number: "PO-OTHER",
          id: "ord-other",
          driver_name: "Andi",
          effective_delivery_date: "2026-12-30",
        },
      ],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await click("ds-period-trigger");
    await click("mock-range-cross-month");
    expect(container.querySelector('[data-testid="ds-period-value"]').textContent)
      .toBe("28 Des 2026 – 03 Jan 2027");
    await click("ds-filter-btn");
    await act(async () => {
      fireChange(container.querySelector('[data-testid="ds-adv-driver"]'), "bud");
    });
    await click("ds-adv-apply");
    expect(container.querySelector('[data-testid="ds-row-PO-DRV"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-row-PO-OTHER"]')).toBeFalsy();
  });
});

describe("DeliverySchedulePage Advanced Table parity", () => {
  let container;
  let root;

  const manyRows = Array.from({ length: 30 }, (_, i) => ({
    ...sampleRows[0],
    id: `ord-${i}`,
    order_number: `PO-${String(i).padStart(3, "0")}`,
    customer_name: i % 2 === 0 ? "Alpha Co" : "Beta Co",
    status: i % 3 === 0 ? "terkirim" : "terjadwal",
    status_label: i % 3 === 0 ? "Terkirim" : "Terjadwal",
    effective_delivery_date: `2026-10-${String((i % 28) + 1).padStart(2, "0")}`,
    percent_delivered: i % 3 === 0 ? 100 : 10,
    shipment_id: `sh-${i}`,
    sj_label: `SJ-${i}`,
    latest_sj_number: `SJ-${i}`,
    lat: i % 2 === 0 ? -6.2 : null,
    lng: i % 2 === 0 ? 106.8 : null,
  }));

  beforeEach(() => {
    mockLocation.pathname = "/produksi/jadwal-pengiriman";
    mockLocation.search = "";
    sessionStorage.clear();
    localStorage.clear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockDeliverySchedule.mockReset();
    mockNavigate.mockReset();
    mockHasPerm.mockImplementation((p) => (
      p === "schedule.view" || p === "route_optimizer.create" || p === "route_optimizer.optimize"
    ));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    sessionStorage.clear();
    localStorage.clear();
  });

  test("renders Advanced Table, Reset Kolom, and pager", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="delivery-schedule-table"]')).toBeTruthy();
    const resetCols = container.querySelector('[data-testid="dp-reset-cols-delivery-schedule"]');
    expect(resetCols).toBeTruthy();
    // Reset Kolom lives in the filter toolbar — not above the table tools strip.
    expect(container.querySelector('[data-testid="delivery-schedule-filters"]')
      .contains(resetCols)).toBe(true);
    expect(container.querySelector(".dp-adv-tools")).toBeFalsy();
    expect(container.querySelector('[data-testid="delivery-schedule-pager"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/2 SJ/);
    expect(container.querySelector('[data-testid="ds-page-size"]')).toBeTruthy();
  });

  test("Reset Kolom restores layout without clearing applied business filters", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await act(async () => {
      fireChange(container.querySelector('[data-testid="ds-filter-q"]'), "PO-100");
    });
    await act(async () => {
      container.querySelector('[data-testid="ds-filter-apply"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/1 SJ/);
    await act(async () => {
      container.querySelector('[data-testid="dp-reset-cols-delivery-schedule"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-filter-q"]').value).toBe("PO-100");
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/1 SJ/);
  });

  test("SLA PO note stacks under SJ date when dates differ; long PO stays in cell", async () => {
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        order_number: "PO/2026/10/24/000011",
        shipment_id: "sh-sla",
        sj_label: "SJ/2026/10/24/000011",
        effective_delivery_date: "2026-10-27",
        sj_delivery_date: "2026-10-27",
        po_sla_date: "2026-10-14",
      }],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    const sla = container.querySelector('[data-testid="ds-sla-PO/2026/10/24/000011"]');
    expect(sla).toBeTruthy();
    expect(sla.textContent).toMatch(/SLA PO/);
    expect(container.querySelector(".ds-date-cell")).toBeTruthy();
    const po = container.querySelector('[data-testid="ds-po-link-PO/2026/10/24/000011-sh-sla"]');
    expect(po).toBeTruthy();
    expect(po.textContent).toBe("PO/2026/10/24/000011");
    expect(po.getAttribute("title")).toBe("PO/2026/10/24/000011");
  });

  test("legacy narrow persisted widths are lifted to column minWidth without Reset Kolom", async () => {
    // Schema key matches deliveryPlanTableLayout.dpLayoutKey for tableId=delivery-schedule
    const key = "delivery-plan-table-layout-v2:u-ds:delivery-schedule";
    localStorage.setItem(key, JSON.stringify({
      order: ["effective_delivery_date", "order_number", "customer_name", "status"],
      hidden: [],
      widths: {
        effective_delivery_date: 100,
        order_number: 110,
        customer_name: 140,
        status: 110,
      },
    }));
    mockDeliverySchedule.mockResolvedValue({ rows: sampleRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await act(async () => { await Promise.resolve(); });
    const saved = JSON.parse(localStorage.getItem(key));
    expect(saved.widths.order_number).toBeGreaterThanOrEqual(220);
    expect(saved.widths.effective_delivery_date).toBeGreaterThanOrEqual(148);
    // Reorder preference preserved
    expect(saved.order[0]).toBe("effective_delivery_date");
    const poCol = container.querySelector('colgroup col');
    expect(poCol).toBeTruthy();
  });

  test("Rincian modal exposes horizontal scroll wrap and Status column", async () => {
    mockDeliverySchedule.mockResolvedValue({
      rows: [{
        ...sampleRows[0],
        order_number: "PO/2026/10/24/000011",
        shipment_id: "sh-r",
        sj_label: "SJ/2026/10/24/000011",
        qty_sj: 50000,
        sj_item_lines: [{ qty: 50000, unit: "pcs" }],
        status_label: "Terjadwal",
      }],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await act(async () => {
      container.querySelector('[data-testid="ds-rincian-pengiriman"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-rincian-modal"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-rincian-table-wrap"]')).toBeTruthy();
    const headers = Array.from(container.querySelectorAll(".ds-rincian-table th")).map((th) => th.textContent);
    expect(headers).toEqual([
      "Tanggal Pengiriman",
      "Nomor SJ",
      "Nomor PO",
      "Customer",
      "Qty Pengiriman",
      "Status",
    ]);
    expect(container.querySelector(".ds-rincian-table td.num")?.textContent).toMatch(/50\.000/);
  });

  test("column filter composes with toolbar and resets page", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: manyRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/30 SJ/);
    await act(async () => {
      fireChange(container.querySelector('[data-testid="ds-page-size"]'), "25");
    });
    await act(async () => {
      container.querySelector('[data-testid="ds-page-next"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-page-indicator"]').textContent).toMatch(/Halaman 2/);

    const filterBtn = container.querySelector('[data-testid="dp-filter-delivery-schedule-customer_name"]');
    await act(async () => { filterBtn.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const pop = container.querySelector(".dp-adv-filter-pop input");
    expect(pop).toBeTruthy();
    await act(async () => { fireChange(pop, "Beta"); });
    await act(async () => {
      Array.from(container.querySelectorAll(".dp-adv-filter-actions button"))
        .find((b) => b.textContent.includes("Apply"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-page-indicator"]').textContent).toMatch(/Halaman 1/);
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/15 SJ/);
    expect(container.querySelector('[data-testid^="ds-row-PO-001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid^="ds-row-PO-000"]')).toBeFalsy();
  });

  test("select-all is current page only; selection survives page change until filtered out", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: manyRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await act(async () => {
      fireChange(container.querySelector('[data-testid="ds-page-size"]'), "25");
    });
    await act(async () => {
      container.querySelector('[data-testid="ds-select-all"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-optimasi-rute"]').disabled).toBe(false);
    expect(container.querySelector('[data-testid="ds-selected-count"]').textContent).toMatch(/25/);

    await act(async () => {
      container.querySelector('[data-testid="ds-page-next"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    // Page 2 still keeps prior page selection (eligible universe); bulk bar remains.
    expect(container.querySelector('[data-testid="ds-selected-count"]').textContent).toMatch(/25/);

    await act(async () => {
      container.querySelector('[data-testid="ds-optimasi-rute"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalled();
    const href = mockNavigate.mock.calls[0][0];
    expect(href).toMatch(/review-optimasi\?ids=/);
    // Default date DESC: page 1 holds newest days (ord-27 …), not oldest ord-0.
    expect(href).toContain("ord-27");
    expect(href).not.toContain("ord-0");
    expect(href).not.toContain("ord-29");
  });

  test("numeric percent sort and date sort use Advanced Table headers", async () => {
    mockDeliverySchedule.mockResolvedValue({
      rows: [
        { ...sampleRows[0], id: "a", order_number: "PO-A", percent_delivered: 10, effective_delivery_date: "2026-10-20", shipment_id: "s1" },
        { ...sampleRows[0], id: "b", order_number: "PO-B", percent_delivered: 90, effective_delivery_date: "2026-10-10", shipment_id: "s2" },
      ],
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    const pctSort = container.querySelector('[data-testid="dp-sort-delivery-schedule-percent_delivered"]');
    await act(async () => { pctSort.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    let pos = Array.from(container.querySelectorAll('tbody tr[data-testid^="ds-row-"]'))
      .map((el) => el.getAttribute("data-testid"));
    expect(pos[0]).toContain("PO-A");
    await act(async () => { pctSort.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    pos = Array.from(container.querySelectorAll('tbody tr[data-testid^="ds-row-"]'))
      .map((el) => el.getAttribute("data-testid"));
    expect(pos[0]).toContain("PO-B");

    const dateSort = container.querySelector('[data-testid="dp-sort-delivery-schedule-effective_delivery_date"]');
    await act(async () => { dateSort.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    pos = Array.from(container.querySelectorAll('tbody tr[data-testid^="ds-row-"]'))
      .map((el) => el.getAttribute("data-testid"));
    expect(pos[0]).toContain("PO-B");
  });
});

describe("DeliverySchedulePage Rincian Pengiriman + multi-SJ selection", () => {
  let container;
  let root;

  const multiSjRows = [
    {
      ...sampleRows[0],
      id: "ord-multi",
      order_number: "PO-MULTI",
      customer_name: "PT Multi",
      shipment_id: "sh-a",
      sj_label: "SJ-001",
      latest_sj_number: "SJ-001",
      effective_delivery_date: "2026-10-23",
      status: "terjadwal",
      status_label: "Terjadwal",
      qty_sj: 50000,
      qty_total: 80000,
      sj_item_lines: [{ qty: 50000, unit: "pcs" }],
    },
    {
      ...sampleRows[0],
      id: "ord-multi",
      order_number: "PO-MULTI",
      customer_name: "PT Multi",
      shipment_id: "sh-b",
      sj_label: "SJ-002",
      latest_sj_number: "SJ-002",
      effective_delivery_date: "2026-10-23",
      status: "terjadwal",
      status_label: "Terjadwal",
      qty_sj: 30000,
      qty_total: 80000,
      sj_item_lines: [{ qty: 30000, unit: "pcs" }],
    },
    {
      ...sampleRows[0],
      id: "ord-d2",
      order_number: "PO-D2",
      customer_name: "PT Day2",
      shipment_id: "sh-c",
      sj_label: "SJ-003",
      latest_sj_number: "SJ-003",
      effective_delivery_date: "2026-10-24",
      status: "terkirim",
      status_label: "Terkirim",
      qty_sj: 10000,
      qty_total: 10000,
      sj_item_lines: [{ qty: 10000, unit: "pcs" }],
    },
  ];

  beforeEach(async () => {
    localStorage.clear();
    sessionStorage.clear();
    mockNavigate.mockReset();
    mockHasPerm.mockImplementation(() => true);
    mockDeliverySchedule.mockReset();
    mockDeliverySchedule.mockResolvedValue({ rows: multiSjRows });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    jest.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    console.error.mockRestore?.();
  });

  test("multi-SJ selection is independent; Optimasi gets unique order of selected SJ only", async () => {
    await act(async () => { root.render(<DeliverySchedulePage />); });
    const boxA = container.querySelector('[data-testid="ds-checkbox-PO-MULTI-sh-a"]');
    const boxB = container.querySelector('[data-testid="ds-checkbox-PO-MULTI-sh-b"]');
    expect(boxA).toBeTruthy();
    expect(boxB).toBeTruthy();
    await act(async () => { boxA.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(boxA.checked).toBe(true);
    expect(boxB.checked).toBe(false);
    expect(container.querySelector('[data-testid="ds-selected-count"]').textContent).toMatch(/1/);
    await act(async () => { boxB.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(boxA.checked).toBe(true);
    expect(boxB.checked).toBe(true);
    expect(container.querySelector('[data-testid="ds-selected-count"]').textContent).toMatch(/2/);
    await act(async () => { boxB.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(boxA.checked).toBe(true);
    expect(boxB.checked).toBe(false);
    await act(async () => {
      container.querySelector('[data-testid="ds-optimasi-rute"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalledWith(desktopScheduleReviewHref(["ord-multi"]));
  });

  test("Rincian Pengiriman uses applied filters and full filtered set, not page slice", async () => {
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await act(async () => {
      fireChange(container.querySelector('[data-testid="ds-filter-status"]'), "terjadwal");
    });
    // Draft only — modal must still see unapplied full set until Terapkan.
    await act(async () => {
      container.querySelector('[data-testid="ds-rincian-pengiriman"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-rincian-sj-count"]').textContent).toBe("3");
    await act(async () => {
      container.querySelector('[data-testid="ds-rincian-close"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="ds-filter-apply"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="ds-rincian-pengiriman"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-rincian-modal"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-rincian-sj-count"]').textContent).toBe("2");
    expect(container.querySelector('[data-testid="ds-rincian-total-qty"]').textContent).toMatch(/80\.000/);
    expect(container.querySelector('[data-testid="ds-rincian-day-2026-10-23"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-rincian-day-2026-10-24"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-rincian-grand-total"]').textContent).toMatch(/80\.000/);
  });

  test("Rincian multi-day grouping and empty after impossible filter", async () => {
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await act(async () => {
      container.querySelector('[data-testid="ds-rincian-pengiriman"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-rincian-day-2026-10-23"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-rincian-day-2026-10-24"]')).toBeTruthy();
    const dayOrder = Array.from(container.querySelectorAll('[data-testid^="ds-rincian-day-"]'))
      .map((el) => el.getAttribute("data-testid"))
      .filter((id) => id && !id.includes("total"));
    expect(dayOrder.indexOf("ds-rincian-day-2026-10-24")).toBeLessThan(dayOrder.indexOf("ds-rincian-day-2026-10-23"));
    expect(container.querySelector('[data-testid="ds-rincian-day-total-2026-10-23"]').textContent).toMatch(/80\.000/);
    expect(container.querySelector('[data-testid="ds-rincian-day-total-2026-10-24"]').textContent).toMatch(/10\.000/);
    await act(async () => {
      container.querySelector('[data-testid="ds-rincian-close"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      fireChange(container.querySelector('[data-testid="ds-filter-q"]'), "NO-SUCH-PO");
    });
    await act(async () => {
      container.querySelector('[data-testid="ds-filter-apply"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="ds-rincian-pengiriman"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-rincian-empty"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-rincian-sj-count"]').textContent).toBe("0");
  });

  test("Rincian ignores pagination page — totals use all filtered SJ", async () => {
    const pageRows = Array.from({ length: 30 }, (_, i) => ({
      ...multiSjRows[0],
      id: `ord-p-${i}`,
      order_number: `PO-P-${i}`,
      shipment_id: `shp-${i}`,
      sj_label: `SJ-P-${i}`,
      qty_sj: 1000,
      sj_item_lines: [{ qty: 1000, unit: "pcs" }],
      effective_delivery_date: "2026-10-23",
      status: "terjadwal",
      status_label: "Terjadwal",
    }));
    mockDeliverySchedule.mockResolvedValue({ rows: pageRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await act(async () => {
      fireChange(container.querySelector('[data-testid="ds-page-size"]'), "25");
    });
    expect(container.querySelectorAll('tbody tr[data-testid^="ds-row-"]').length).toBe(25);
    await act(async () => {
      container.querySelector('[data-testid="ds-rincian-pengiriman"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-rincian-sj-count"]').textContent).toBe("30");
    expect(container.querySelector('[data-testid="ds-rincian-total-qty"]').textContent).toMatch(/30\.000/);
  });
});

describe("DeliverySchedulePage filter completeness", () => {
  let container;
  let root;

  const click = async (testid) => {
    await act(async () => {
      container.querySelector(`[data-testid="${testid}"]`)
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  };

  const octRangeRows = [
    {
      ...sampleRows[0],
      id: "ord-24",
      order_number: "PO-24",
      shipment_id: "sh-24",
      sj_label: "SJ-24",
      sj_delivery_date: "2026-10-24",
      effective_delivery_date: "2026-10-24",
      buffer_stock: true,
      status: "terjadwal",
      status_label: "Terjadwal",
    },
    {
      ...sampleRows[0],
      id: "ord-25",
      order_number: "PO-25",
      shipment_id: "sh-25",
      sj_label: "SJ-25",
      sj_delivery_date: "2026-10-25",
      effective_delivery_date: "2026-10-20",
      status: "terjadwal",
      status_label: "Terjadwal",
    },
    {
      ...sampleRows[0],
      id: "ord-26a",
      order_number: "PO-MULTI",
      shipment_id: "sh-26a",
      sj_label: "SJ-26A",
      sj_delivery_date: "2026-10-26",
      effective_delivery_date: "2026-10-26",
      status: "terjadwal",
      status_label: "Terjadwal",
    },
    {
      ...sampleRows[0],
      id: "ord-26a",
      order_number: "PO-MULTI",
      shipment_id: "sh-26b",
      sj_label: "SJ-26B",
      sj_delivery_date: "2026-10-26",
      effective_delivery_date: "2026-10-26",
      status: "terkirim",
      status_label: "Terkirim",
    },
    {
      ...sampleRows[0],
      id: "ord-27",
      order_number: "PO-27",
      shipment_id: "sh-27",
      sj_label: "SJ-27",
      sj_delivery_date: "2026-10-27",
      effective_delivery_date: "2026-10-27",
      status: "hari_ini",
      status_label: "Hari Ini",
    },
    {
      ...sampleRows[0],
      id: "ord-out",
      order_number: "PO-OUT",
      shipment_id: "sh-out",
      sj_label: "SJ-OUT",
      sj_delivery_date: "2026-10-23",
      effective_delivery_date: "2026-10-23",
      status: "terjadwal",
      status_label: "Terjadwal",
    },
  ];

  beforeEach(() => {
    mockLocation.pathname = "/produksi/jadwal-pengiriman";
    mockLocation.search = "";
    sessionStorage.clear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockReset();
    mockDeliverySchedule.mockReset();
    mockNavigate.mockReset();
    mockHasPerm.mockImplementation((p) => p === "schedule.view" || p === "delivery.schedule.optimize");
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    sessionStorage.clear();
  });

  test("date 24–27 inclusive shows all eligible SJ including multi-SJ and buffer", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: octRangeRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await click("ds-period-trigger");
    await click("mock-range-24-27");
    expect(container.querySelector('[data-testid="ds-filter-pending"]')).toBeTruthy();
    expect(container.querySelector('[data-testid^="ds-row-PO-OUT"]')).toBeTruthy();
    await click("ds-filter-apply");
    expect(container.querySelector('[data-testid="ds-filter-pending"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/5 SJ/);
    expect(container.querySelector('[data-testid^="ds-row-PO-24"]')).toBeTruthy();
    expect(container.querySelector('[data-testid^="ds-row-PO-25"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-row-PO-MULTI-sh-26a"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-row-PO-MULTI-sh-26b"]')).toBeTruthy();
    expect(container.querySelector('[data-testid^="ds-row-PO-27"]')).toBeTruthy();
    expect(container.querySelector('[data-testid^="ds-row-PO-OUT"]')).toBeFalsy();
  });

  test("draft date differs from applied — Belum diterapkan until Terapkan", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: octRangeRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await click("ds-period-trigger");
    await click("mock-range-26");
    await click("ds-filter-apply");
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/2 SJ/);

    await click("ds-period-trigger");
    await click("mock-range-24-27");
    expect(container.querySelector('[data-testid="ds-filter-pending"]').textContent).toMatch(/Belum diterapkan/);
    expect(container.querySelector('[data-testid="ds-draft-date-label"]').textContent).toMatch(/24 Okt 2026/);
    expect(container.querySelector('[data-testid="ds-applied-date-label"]').textContent).toMatch(/26 Okt 2026/);
    // Table still on applied 26 only.
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/2 SJ/);
    expect(container.querySelector('[data-testid^="ds-row-PO-24"]')).toBeFalsy();

    await click("ds-filter-apply");
    expect(container.querySelector('[data-testid="ds-filter-pending"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/5 SJ/);
    expect(container.querySelector('[data-testid^="ds-row-PO-24"]')).toBeTruthy();
  });

  test("Reset clears toolbar + advanced + column filters; Reset Kolom keeps business filters", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: octRangeRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await click("ds-period-trigger");
    await click("mock-range-24-27");
    await click("ds-filter-apply");
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/5 SJ/);

    const filterBtn = container.querySelector('[data-testid="dp-filter-delivery-schedule-order_number"]');
    await act(async () => { filterBtn.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const pop = container.querySelector(".dp-adv-filter-pop input");
    await act(async () => { fireChange(pop, "MULTI"); });
    await act(async () => {
      Array.from(container.querySelectorAll(".dp-adv-filter-actions button"))
        .find((b) => b.textContent.includes("Apply"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-col-filter-bar"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-col-filter-count"]').textContent).toMatch(/Filter kolom aktif \(1\)/);
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/2 SJ/);

    // Reset Kolom must not clear business / column filters.
    await click("dp-reset-cols-delivery-schedule");
    expect(container.querySelector('[data-testid="ds-col-filter-bar"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/2 SJ/);
    expect(container.querySelector('[data-testid="ds-period-value"]').textContent).toMatch(/24 Okt 2026/);

    await click("ds-filter-reset");
    expect(container.querySelector('[data-testid="ds-col-filter-bar"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-filter-pending"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-period-value"]').textContent).toBe("Semua tanggal");
    expect(container.querySelector('[data-testid="ds-filter-status"]').value).toBe("semua");
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/6 SJ/);
  });

  test("Hapus filter kolom clears only column filters", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: octRangeRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await click("ds-period-trigger");
    await click("mock-range-24-27");
    await click("ds-filter-apply");
    const filterBtn = container.querySelector('[data-testid="dp-filter-delivery-schedule-order_number"]');
    await act(async () => { filterBtn.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { fireChange(container.querySelector(".dp-adv-filter-pop input"), "MULTI"); });
    await act(async () => {
      Array.from(container.querySelectorAll(".dp-adv-filter-actions button"))
        .find((b) => b.textContent.includes("Apply"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/2 SJ/);
    await click("ds-col-filter-clear");
    expect(container.querySelector('[data-testid="ds-col-filter-bar"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/5 SJ/);
    expect(container.querySelector('[data-testid="ds-period-value"]').textContent).toMatch(/24 Okt 2026/);
  });

  test("Rincian totals follow full filtered set after date apply", async () => {
    mockDeliverySchedule.mockResolvedValue({
      rows: octRangeRows.map((r) => ({
        ...r,
        qty_sj: 1000,
        sj_item_lines: [{ qty: 1000, unit: "pcs" }],
      })),
    });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await click("ds-period-trigger");
    await click("mock-range-24-27");
    await click("ds-filter-apply");
    await act(async () => {
      container.querySelector('[data-testid="ds-rincian-pengiriman"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="ds-rincian-sj-count"]').textContent).toBe("5");
    expect(container.querySelector('[data-testid="ds-rincian-total-qty"]').textContent).toMatch(/5\.000/);
  });

  test("persisted filters restore on remount; URL status still applies", async () => {
    mockDeliverySchedule.mockResolvedValue({ rows: octRangeRows });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    await click("ds-period-trigger");
    await click("mock-range-24-27");
    await click("ds-filter-apply");
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/5 SJ/);

    act(() => root.unmount());
    container.remove();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="ds-period-value"]').textContent).toMatch(/24 Okt 2026/);
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/5 SJ/);

    mockLocation.search = "?status=hari_ini";
    act(() => root.unmount());
    container.remove();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => { root.render(<DeliverySchedulePage />); });
    // Session date + URL hari_ini: only SJ dated today key among restored range — use delivery date.
    // With restored 24–27 and hari_ini, only rows whose delivery date == today WIB remain.
    const today = wibTodayKey();
    const expectTodayOnly = octRangeRows.filter((r) => (r.sj_delivery_date || r.effective_delivery_date) === today).length;
    // If today is outside fixture dates, count may be 0 — assert status applied without Terapkan.
    expect(container.querySelector('[data-testid="ds-filter-status"]').value).toBe("hari_ini");
    expect(Number(container.querySelector('[data-testid="ds-total-count"]').textContent.match(/(\d+)/)?.[1] || 0))
      .toBe(expectTodayOnly);
  });
});

describe("DeliverySchedulePage table vertical scroll layout", () => {
  test("Schedule CSS scopes vertical scroll viewport without changing shared Plan table", () => {
    const css = fs.readFileSync(path.join(__dirname, "DeliverySchedulePage.css"), "utf8");
    const shared = fs.readFileSync(
      path.join(__dirname, "../components/deliveryPlan/DeliveryPlanAdvancedTable.css"),
      "utf8",
    );
    expect(css).toMatch(/\[data-testid="delivery-schedule-table-wrap"\]/);
    expect(css).toMatch(/\.dp-adv-scroll\s*\{[^}]*overflow-y:\s*auto/s);
    expect(css).toMatch(/\.dp-adv-scroll\s*\{[^}]*overflow-x:\s*auto/s);
    expect(css).toMatch(/min-height:\s*0/);
    expect(css).toMatch(/\.dp-pager\s*\{[^}]*flex:\s*0\s+0\s+auto/s);
    // Shared Advanced Table stays overflow-x only (Plan unchanged).
    expect(shared).toMatch(/\.dp-adv-scroll\s*\{[^}]*overflow-x:\s*auto/s);
    expect(shared).not.toMatch(/\.dp-adv-scroll\s*\{[^}]*overflow-y:\s*auto/s);
  });

  test("Schedule CSS keeps UX 9.0 compact row density scoped away from Plan table", () => {
    const css = fs.readFileSync(path.join(__dirname, "DeliverySchedulePage.css"), "utf8");
    const shared = fs.readFileSync(
      path.join(__dirname, "../components/deliveryPlan/DeliveryPlanAdvancedTable.css"),
      "utf8",
    );
    expect(css).toMatch(/Eternal UX 9\.0/);
    expect(css).toMatch(/\.dp-adv-table tbody td\s*\{[^}]*height:\s*40px/s);
    expect(css).toMatch(/min-height:\s*38px/);
    expect(css).toMatch(/max-height:\s*42px/);
    expect(css).toMatch(/\.ship-bar\s*\{[^}]*height:\s*14px/s);
    // Plan shared defaults remain for non-Schedule hosts.
    expect(shared).toMatch(/\.dp-adv-table tbody td\s*\{[^}]*height:\s*31px/s);
  });

  test("pageRows keeps all 24 SJ on page 1 when pageSize=50", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    mockLocation.pathname = "/produksi/jadwal-pengiriman";
    mockLocation.search = "";
    sessionStorage.clear();
    mockHasPerm.mockImplementation((p) => p === "schedule.view");
    const rows24 = Array.from({ length: 24 }, (_, i) => ({
      ...sampleRows[0],
      id: `ord-scroll-${i}`,
      order_number: `PO-SCROLL-${i}`,
      shipment_id: `shp-scroll-${i}`,
      sj_label: `SJ-SCROLL-${i}`,
      effective_delivery_date: "2026-10-24",
      sj_delivery_date: "2026-10-24",
      status: "terjadwal",
      status_label: "Terjadwal",
    }));
    mockDeliverySchedule.mockResolvedValue({ rows: rows24 });
    await act(async () => { root.render(<DeliverySchedulePage />); });
    expect(container.querySelector('[data-testid="ds-total-count"]').textContent).toMatch(/24/);
    expect(container.querySelector('[data-testid="ds-page-size"]').value).toBe("50");
    expect(container.querySelectorAll('tbody tr[data-testid^="ds-row-"]').length).toBe(24);
    act(() => root.unmount());
    container.remove();
  });
});

