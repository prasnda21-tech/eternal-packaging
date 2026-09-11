import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import DeliverySchedulePage from "./DeliverySchedulePage";
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

jest.mock("../components/SuratJalanViewButton", () => ({
  __esModule: true,
  default: ({ label, testid }) => <button type="button" data-testid={testid}>{label}</button>,
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

  test("jadwal pengiriman table distinguishes deadline and manual", () => {
    expect(schedule).toMatch(/Deadline/);
    expect(schedule).toMatch(/Jadwal Manual/);
    expect(schedule).toMatch(/effective_delivery_date/);
    expect(schedule).toMatch(/Tanggal Mulai/);
    expect(schedule).toMatch(/ds-filter-reset/);
    expect(schedule).not.toMatch(/Buat Jadwal Pengiriman/);
    expect(schedule).not.toMatch(/delivery.schedule.create/);
    expect(schedule).toMatch(/Atur Lokasi/);
    expect(schedule).toMatch(/OutletLocationModal/);
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
