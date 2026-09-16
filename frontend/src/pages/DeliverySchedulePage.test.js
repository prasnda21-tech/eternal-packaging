import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import DeliverySchedulePage, { scheduleRowSjNumber, desktopDeliveryPoHref, desktopDeliveryKerjaHref } from "./DeliverySchedulePage";
import { locationSetupStatusLabel } from "../lib/deliveryRouteBridge";
import { STATUS_LOKASI_SORT_DUMMY_ROWS } from "../lib/deliveryScheduleStatusLokasiFixtures";
import { desktopScheduleReviewHref } from "../lib/mobileDeliverySchedule";

const mockHasPerm = jest.fn(() => false);
const mockDeliverySchedule = jest.fn();
const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, state, onClick, ...props }) => (
    <a href={typeof to === "string" ? to : "#"} onClick={onClick} {...props}>{children}</a>
  ),
  useLocation: () => ({ pathname: "/produksi/jadwal-pengiriman", search: "" }),
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
    expect(shell).toMatch(/title="Produksi"/);
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
    expect(schedule).toMatch(/Tanggal Mulai/);
    expect(schedule).toMatch(/ds-filter-reset/);
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
    expect(schedule).toMatch(/ds-sort-customer/);
  });

  test("desktop Optimasi Rute uses desktop review href (not /m/...) and permissions", () => {
    expect(schedule).toMatch(/canOptimizeFromDeliverySchedule/);
    expect(schedule).toMatch(/desktopScheduleReviewHref/);
    expect(schedule).not.toMatch(/mobileScheduleReviewHref/);
    expect(schedule).toMatch(/locationSetupStatusLabel/);
    expect(schedule).toMatch(/ds-optimasi-rute/);
    expect(schedule).toMatch(/Status Lokasi/);
    expect(schedule).toMatch(/ds-sort-status-lokasi/);
    expect(schedule).toMatch(/DELIVERY_SORT_KEYS\.location_status/);
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
    expect(mockNavigate).toHaveBeenCalledWith(desktopScheduleReviewHref(["ord-a", "ord-b"]));
    expect(mockNavigate.mock.calls[0][0]).toMatch(/\/produksi\/jadwal-pengiriman\/review-optimasi\?ids=/);
    expect(mockNavigate.mock.calls[0][0]).not.toMatch(/\/m\//);
    expect(mockNavigate.mock.calls[0][0]).toContain("ord-a");
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
    expect(container.querySelector('[data-testid="ds-col-status-lokasi"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ds-sort-status-lokasi"]')).toBeTruthy();
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

    const sortBtn = container.querySelector('[data-testid="ds-sort-status-lokasi"]');
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
    const customerBtn = container.querySelector('[data-testid="ds-sort-customer"]');
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
    const po = container.querySelector('[data-testid="ds-filter-po"]');
    await act(async () => { fireChange(po, "PO-100"); });
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

