import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { ProductionDashboardView, modelsFromSnapshot } from "./ProductionDashboard";
import { simulationBoard } from "../components/productionDashboard/productionSimulation";
import { layoutOverlaps } from "../components/productionDashboard/productionWidgetLayout";

jest.mock("react-router-dom", () => {
  const React = require("react");
  return {
    MemoryRouter: ({ children }) => children,
    Link: ({ children, to, ...rest }) => React.createElement("a", { href: to, ...rest }, children),
    useNavigate: () => jest.fn(),
    useSearchParams: () => [new URLSearchParams(""), jest.fn()],
  };
}, { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: () => false }),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

test("partial snapshot does not turn missing widgets into zero or throw", () => {
  for (const body of [undefined, null, {}, { po_aktif: { count: null } }]) {
    const models = modelsFromSnapshot(body);
    for (const key of ["po_aktif", "perlu_produksi", "po_urgent", "po_hold", "sisa_qty", "deadline", "approval", "prioritas"]) {
      expect(models[key].status).toBe("incomplete");
    }
  }
  const models = modelsFromSnapshot({ po_aktif: { count: 0 }, perlu_produksi: { count: 3 }, sisa_qty: { by_unit: {} } });
  expect(models.po_aktif.status).toBe("zero");
  expect(models.perlu_produksi.count).toBe(3);
  expect(models.sisa_qty.status).toBe("zero");
  expect(models.po_hold.status).toBe("incomplete");
});

if (typeof PointerEvent === "undefined") {
  global.PointerEvent = class PointerEvent extends MouseEvent {};
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function layout(widgets, version = 1) {
  return { version, source: "saved", widgets, ignored: [], withheld: [], dashboard_key: "produksi" };
}

function widget(id, x, y, w, h) {
  return { widget_id: id, x, y, w, h, settings: {} };
}

const statusPoDrill = {
  path: "/dashboard/rincian?dashboard=produksi&widget=po_aktif&basis=snapshot",
  any_of: ["nav.reports.status_po"],
  also_any_of: ["schedule.view"],
};

const snapshot = {
  po_aktif: { count: 5, drill: statusPoDrill },
  perlu_produksi: { count: 3, drill: null },
  po_urgent: { count: 1, drill: null },
  po_hold: { count: 1, drill: null },
  sisa_qty: { by_unit: { pcs: 193, pack: 5 }, value: null, unit: null, drill: null },
  deadline: { within_6h: 4, within_24h: 9, drill: null },
  approval: { design: 2, payment: 2, either_unique: 3, drill: null },
  prioritas: {
    rows: [{ order_id: "po-multi", line_idx: 0, po_number: "PO-MULTI", item: "Cup", unit: "pcs", qty_production_remaining: 85, labels: ["URGENT"] }],
    drill: { path: "/dashboard/rincian?dashboard=produksi&widget=prioritas", any_of: ["schedule.edit"] },
  },
};

async function renderPage(hasPerm, apis) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <MemoryRouter>
        <ProductionDashboardView
          hasPerm={hasPerm}
          layoutApi={apis.layoutApi}
          snapshotApi={apis.snapshotApi}
          analyticsApi={apis.analyticsApi || null}
          operatorAnalyticsApi={apis.operatorAnalyticsApi || null}
          branchOutputApi={apis.branchOutputApi || null}
          deliveryPlanQtyApi={apis.deliveryPlanQtyApi || null}
          poLateTrackingSummaryApi={apis.poLateTrackingSummaryApi || null}
          simulation={apis.simulation || null}
        />
      </MemoryRouter>,
    );
  });
  await act(async () => { await flush(); await flush(); });
  return {
    host,
    unmount() {
      act(() => root.unmount());
      host.remove();
    },
  };
}

function apisFor(saved) {
  const snapshotApi = jest.fn(async () => snapshot);
  const layoutApi = {
    load: jest.fn(async () => saved),
    save: jest.fn(async (body) => ({ ...body, version: body.version + 1, source: "saved", dashboard_key: "produksi" })),
    reset: jest.fn(async () => layout([], 0)),
  };
  return { snapshotApi, layoutApi };
}

test("forbidden widgets do not fetch the snapshot", async () => {
  const apis = apisFor(layout([widget("po_aktif", 0, 0, 3, 2)]));
  const view = await renderPage(() => false, apis);
  expect(view.host.querySelector("[data-testid='produksi-widget-po_aktif']").textContent).toContain("Tidak ada izin");
  expect(view.host.querySelector("[data-testid='produksi-widget-po_aktif']").textContent).not.toContain("5");
  expect(apis.snapshotApi).not.toHaveBeenCalled();
  view.unmount();
});

test("snapshot cards keep units and windows separate", async () => {
  const widgets = [
    widget("sisa_qty", 0, 0, 6, 3),
    widget("deadline", 6, 0, 6, 3),
    widget("approval", 0, 3, 4, 3),
    widget("prioritas", 4, 3, 8, 4),
    widget("po_urgent", 0, 7, 3, 2),
  ];
  const apis = apisFor(layout(widgets));
  const allow = (key) => key === "dashboard.production.view" || key === "schedule.edit";
  const view = await renderPage(allow, apis);
  const sisa = view.host.querySelector("[data-testid='produksi-widget-sisa_qty']").textContent;
  expect(sisa).toContain("pcs");
  expect(sisa).toContain("193");
  expect(sisa).toContain("pack");
  expect(sisa).toContain("5");
  expect(sisa).not.toContain("98");
  const deadline = view.host.querySelector("[data-testid='produksi-widget-deadline']").textContent;
  expect(deadline).toContain("≤ 6 jam");
  expect(deadline).toContain("≤ 24 jam");
  expect(deadline).toContain("4");
  expect(deadline).toContain("9");
  expect(deadline).not.toContain("13");
  const approval = view.host.querySelector("[data-testid='produksi-widget-approval']").textContent;
  expect(approval).toContain("Design");
  expect(approval).toContain("Payment");
  expect(approval).not.toContain("4");
  const priority = view.host.querySelector("[data-testid='produksi-widget-prioritas']").textContent;
  expect(priority).toContain("PO-MULTI");
  expect(priority).toContain("Line");
  expect(priority).toContain("Produk");
  expect(priority).toContain("85");
  expect(priority).toContain("URGENT");
  expect(view.host.querySelector("[data-testid='produksi-sim-banner']")).toBeNull();
  expect(view.host.textContent).not.toContain("Cup 12 oz");
  expect(view.host.querySelector("[data-testid='produksi-widget-prioritas'] a").getAttribute("href")).toBe("/dashboard/rincian?dashboard=produksi&widget=prioritas");
  expect(view.host.querySelector("[data-testid='produksi-widget-po_urgent'] a")).toBeNull();
  expect(apis.snapshotApi).toHaveBeenCalledTimes(1);
  view.unmount();
});

test("catalog blocks pending widgets and edit mode saves a move", async () => {
  const apis = apisFor(layout([widget("po_aktif", 0, 0, 3, 2), widget("po_hold", 3, 0, 3, 2)]));
  const allow = (key) => key === "dashboard.production.view" || key === "schedule.edit";
  const view = await renderPage(allow, apis);
  await act(async () => { view.host.querySelector("[data-testid='produksi-add-widget']").click(); });
  const pending = view.host.querySelector("[data-testid='produksi-catalog-qc_reject']");
  expect(pending.textContent).toContain("Belum tersedia");
  expect(pending.querySelector("button")).toBeNull();
  const operatorOption = view.host.querySelector("[data-testid='produksi-catalog-output_operator']");
  expect(operatorOption.querySelector("button")).toBeNull();
  expect(operatorOption.textContent).toContain("Tidak memiliki akses");
  expect(view.host.querySelector("[data-testid='produksi-catalog-output_hari_ini'] button").getAttribute("aria-label")).toBe("Tambahkan Output Cetak Hari Ini");
  expect(view.host.querySelector("[data-testid='produksi-catalog-po_terlambat'] button").getAttribute("aria-label")).toBe("Tambahkan PO Terlambat Kirim");
  const add = view.host.querySelector("[data-testid='produksi-catalog-po_urgent'] button");
  expect(add.getAttribute("aria-label")).toBe("Tambahkan PO Urgent");
  await act(async () => { add.click(); });
  expect(view.host.querySelector("[data-testid='produksi-widget-po_urgent']")).toBeTruthy();

  await act(async () => { view.host.querySelector("[data-testid='produksi-edit-layout']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='produksi-menu-po_aktif']").click(); });
  await act(async () => { view.host.querySelector("[aria-label='Geser PO Aktif ke kanan']").click(); });
  await act(async () => { await wait(700); });
  expect(apis.layoutApi.save).toHaveBeenCalled();
  const saved = apis.layoutApi.save.mock.calls.at(-1)[0];
  expect(layoutOverlaps(saved.widgets)).toBe(false);
  expect(saved.widgets.find((item) => item.widget_id === "po_aktif").x).toBeGreaterThan(0);
  view.unmount();
});

test("simulation charts stay off the operational snapshot", async () => {
  const board = simulationBoard();
  const apis = apisFor(layout([widget("po_aktif", 0, 0, 3, 2)]));
  const view = await renderPage(() => true, { ...apis, simulation: board });
  expect(view.host.querySelector("[data-testid='produksi-sim-banner']").textContent).toContain("SIMULASI — bukan data operasional");
  expect(view.host.querySelector("[data-testid='produksi-widget-sim_output']").textContent).toContain("Output Produksi");
  expect(view.host.querySelector("[data-testid='produksi-widget-sim_output']").textContent).not.toContain("work_today");
  expect(view.host.querySelector("[data-testid='produksi-widget-sim_composition']").textContent).toContain("Cup 12 oz");
  expect(view.host.querySelector("[data-testid='produksi-widget-deadline']").textContent).toContain("mencakup");
  expect(view.host.querySelector("[data-testid='produksi-widget-deadline']").textContent).not.toContain("H-1");
  expect(apis.snapshotApi).not.toHaveBeenCalled();
  expect(apis.layoutApi.load).not.toHaveBeenCalled();
  view.unmount();
});

const plantBody = {
  today: { status: "complete", by_unit: { pcs: 10000 }, updated_at: "2026-10-23T07:30:00+07:00", note: "" },
  trend: {
    status: "complete",
    units: ["pcs"],
    series: {
      pcs: [
        { day: "2026-10-17", qty: 1000 },
        { day: "2026-10-18", qty: 0 },
        { day: "2026-10-19", qty: 0 },
        { day: "2026-10-20", qty: 0 },
        { day: "2026-10-21", qty: 0 },
        { day: "2026-10-22", qty: 0 },
        { day: "2026-10-23", qty: 10000 },
      ],
    },
    note: "",
  },
  workflow: {
    per_po: {
      production: { status: "complete", bars: [{ stage: "Cetak", count: 2 }], total: 2, note: "" },
      full: {
        status: "complete",
        bars: [{ stage: "HOLD", count: 1 }, { stage: "Menunggu Approval", count: 1 }, { stage: "Cetak", count: 2 }],
        total: 4,
        approval_detail: { design_waiting: 1, payment_waiting: 1, either_unique: 1 },
        note: "",
      },
    },
    per_line: { status: "unavailable", note: "Belum tersedia — menunggu sumber status per line yang valid." },
  },
};

const operatorBody = {
  performance: {
    status: "complete",
    note: "Target Awal dan Target Revisi belum tersedia. Persentase tidak ditampilkan.",
    target_available: false,
    units: ["pcs"],
    ranking: { pcs: [{ operator_id: "a", name: "Operator A", qty: 6000 }, { operator_id: "b", name: "Operator B", qty: 4000 }] },
  },
  workload: {
    status: "complete",
    operators: [{ operator_id: "a", name: "Operator A", remaining_by_unit: { pcs: 10 }, active_po: 1, active_lines: 2, lines_without_index: 0 }],
    note: "",
  },
};

test("analytics widgets fetch only the endpoints they are allowed to read", async () => {
  const widgets = [
    widget("output_hari_ini", 0, 0, 4, 3),
    widget("tren_output", 4, 0, 8, 4),
    widget("distribusi_tahap", 0, 4, 6, 4),
    widget("output_operator", 6, 4, 6, 4),
    widget("beban_operator", 0, 8, 6, 4),
  ];
  const apis = apisFor(layout(widgets));
  apis.analyticsApi = jest.fn(async () => plantBody);
  apis.operatorAnalyticsApi = jest.fn(async () => operatorBody);
  const viewOnly = await renderPage((key) => key === "dashboard.production.view", apis);
  expect(apis.snapshotApi).not.toHaveBeenCalled();
  expect(apis.analyticsApi).toHaveBeenCalledWith(7);
  expect(apis.operatorAnalyticsApi).not.toHaveBeenCalled();
  expect(viewOnly.host.querySelector("[data-testid='produksi-widget-output_hari_ini']").textContent).toContain("10.000");
  expect(viewOnly.host.querySelector("[data-testid='produksi-widget-output_hari_ini']").textContent).toContain("pcs");
  expect(viewOnly.host.querySelector("[data-testid='produksi-widget-tren_output']").textContent).toContain("23");
  expect(viewOnly.host.querySelector("[data-testid='produksi-widget-distribusi_tahap']").textContent).toContain("Cetak");
  expect(viewOnly.host.querySelector("[data-testid='produksi-widget-distribusi_tahap']").textContent).toContain("per line");
  expect(viewOnly.host.querySelector("[data-testid='produksi-widget-output_operator']").textContent).toContain("Tidak ada izin");
  expect(viewOnly.host.querySelector("[data-testid='produksi-widget-beban_operator']").textContent).toContain("Tidak ada izin");
  await act(async () => { viewOnly.host.querySelector("[data-testid='produksi-scope-semua']").click(); });
  const workflow = viewOnly.host.querySelector("[data-testid='produksi-widget-distribusi_tahap']").textContent;
  expect(workflow).toContain("HOLD");
  expect(workflow).toContain("Menunggu Approval");
  expect(workflow).toContain("Total 4");
  expect(workflow).not.toContain("Total 6");
  await act(async () => { viewOnly.host.querySelector("[data-testid='produksi-trend-days-30']").click(); });
  await act(async () => { await flush(); await flush(); });
  expect(apis.analyticsApi).toHaveBeenCalledWith(30);
  viewOnly.unmount();

  const allowed = apisFor(layout([widget("output_operator", 0, 0, 6, 4), widget("beban_operator", 6, 0, 6, 4)]));
  allowed.analyticsApi = jest.fn(async () => plantBody);
  allowed.operatorAnalyticsApi = jest.fn(async () => operatorBody);
  const view = await renderPage((key) => key === "dashboard.production.operator_analytics", allowed);
  expect(allowed.analyticsApi).not.toHaveBeenCalled();
  expect(allowed.operatorAnalyticsApi).toHaveBeenCalledWith(7);
  const performance = view.host.querySelector("[data-testid='produksi-widget-output_operator']").textContent;
  expect(performance).toContain("Operator A");
  expect(performance).toContain("6.000");
  expect(performance).toContain("Target Awal");
  expect(performance).not.toContain("%");
  expect(view.host.querySelector("[data-testid='produksi-widget-beban_operator']").textContent).toContain("PO");
  expect(view.host.querySelector("[data-testid='produksi-widget-beban_operator']").textContent).toContain("Line");
  view.unmount();
});

function httpStatus(status) {
  const error = new Error(`http ${status}`);
  error.response = { status };
  return error;
}

test("snapshot, plant, and operator map 403 to forbidden and other failures to error", async () => {
  const denied = apisFor(layout([widget("po_aktif", 0, 0, 3, 2), widget("tren_output", 3, 0, 6, 3)]));
  denied.snapshotApi = jest.fn(async () => { throw httpStatus(403); });
  denied.analyticsApi = jest.fn(async () => { throw httpStatus(403); });
  const forbiddenView = await renderPage((key) => key === "dashboard.production.view", denied);
  expect(forbiddenView.host.querySelector("[data-testid='produksi-widget-po_aktif']").textContent).toContain("Tidak ada izin");
  expect(forbiddenView.host.querySelector("[data-testid='produksi-widget-tren_output']").textContent).toContain("Tidak ada izin");
  forbiddenView.unmount();

  const operatorDenied = apisFor(layout([widget("output_operator", 0, 0, 6, 3)]));
  operatorDenied.operatorAnalyticsApi = jest.fn(async () => { throw httpStatus(403); });
  const operatorView = await renderPage((key) => key === "dashboard.production.operator_analytics", operatorDenied);
  expect(operatorView.host.querySelector("[data-testid='produksi-widget-output_operator']").textContent).toContain("Tidak ada izin");
  operatorView.unmount();

  const failed = apisFor(layout([widget("po_aktif", 0, 0, 3, 2)]));
  failed.snapshotApi = jest.fn(async () => { throw new Error("down"); });
  const errorView = await renderPage((key) => key === "dashboard.production.view", failed);
  expect(errorView.host.querySelector("[data-testid='produksi-widget-po_aktif']").textContent).toContain("Gagal memuat");
  errorView.unmount();
});

test("incomplete, empty, and failed analytics stay distinct", async () => {
  const incomplete = {
    today: { status: "incomplete", by_unit: {}, note: "Histori konfirmasi tidak tersedia.", updated_at: "" },
    trend: { status: "incomplete", units: [], series: {}, note: "Histori konfirmasi tidak tersedia." },
    workflow: { per_po: { production: { status: "zero", bars: [], total: 0, note: "Tidak ada PO pada cakupan ini." }, full: { status: "zero", bars: [], total: 0 } }, per_line: { note: "Belum tersedia — menunggu sumber status per line yang valid." } },
  };
  const apis = apisFor(layout([widget("output_hari_ini", 0, 0, 4, 3), widget("tren_output", 4, 0, 8, 4)]));
  apis.analyticsApi = jest.fn(async () => incomplete);
  const view = await renderPage((key) => key === "dashboard.production.view", apis);
  expect(view.host.querySelector("[data-testid='produksi-widget-output_hari_ini']").textContent).toContain("Histori konfirmasi tidak tersedia.");
  expect(view.host.querySelector("[data-testid='produksi-widget-output_hari_ini']").textContent).not.toContain("0");
  const trend = view.host.querySelector("[data-testid='produksi-widget-tren_output']");
  expect(trend.textContent).toContain("Data historis tidak lengkap");
  expect(trend.querySelector("[data-testid='produksi-trend-bar']")).toBeNull();
  expect(trend.querySelector("[data-testid='produksi-trend-zero']")).toBeNull();
  view.unmount();

  const failed = apisFor(layout([widget("tren_output", 0, 0, 8, 4)]));
  failed.analyticsApi = jest.fn(async () => { throw new Error("down"); });
  const errorView = await renderPage((key) => key === "dashboard.production.view", failed);
  expect(errorView.host.querySelector("[data-testid='produksi-widget-tren_output']").textContent).toContain("Gagal memuat");
  errorView.unmount();
});

test("simulation does not call production analytics", async () => {
  const board = simulationBoard();
  const apis = apisFor(layout([]));
  apis.analyticsApi = jest.fn(async () => plantBody);
  apis.operatorAnalyticsApi = jest.fn(async () => operatorBody);
  const view = await renderPage(() => true, { ...apis, simulation: board });
  expect(apis.analyticsApi).not.toHaveBeenCalled();
  expect(apis.operatorAnalyticsApi).not.toHaveBeenCalled();
  expect(apis.snapshotApi).not.toHaveBeenCalled();
  view.unmount();
});

test("output time and zero trend days use presentation values only", async () => {
  const apis = apisFor(layout([
    widget("output_hari_ini", 0, 0, 4, 3),
    widget("tren_output", 4, 0, 4, 3),
  ]));
  apis.analyticsApi = jest.fn(async () => plantBody);
  const view = await renderPage((key) => key === "dashboard.production.view", apis);
  const output = view.host.querySelector("[data-testid='produksi-widget-output_hari_ini']");
  expect(output.textContent).toContain("Diperbarui 23 Okt 2026, 07.30 WIB");
  expect(output.textContent).not.toContain("2026-10-23T");
  const trend = view.host.querySelector("[data-testid='produksi-widget-tren_output']");
  expect(trend.querySelectorAll("[data-testid='produksi-trend-bar']")).toHaveLength(2);
  expect(trend.querySelectorAll("[data-testid='produksi-trend-zero']")).toHaveLength(5);
  expect(trend.querySelector(".pd-bar-stack")).toBeNull();
  expect(trend.querySelector("[data-testid='produksi-trend-zero']").closest(".pd-bar-col").getAttribute("title")).toContain("Output 0");
  view.unmount();

  const invalid = { ...plantBody, today: { ...plantBody.today, updated_at: "bukan-tanggal" } };
  const bad = apisFor(layout([widget("output_hari_ini", 0, 0, 4, 3)]));
  bad.analyticsApi = jest.fn(async () => invalid);
  const badView = await renderPage((key) => key === "dashboard.production.view", bad);
  const card = badView.host.querySelector("[data-testid='produksi-widget-output_hari_ini']");
  expect(card.textContent).toContain("Waktu pembaruan tidak tersedia");
  expect(card.textContent).not.toContain("bukan-tanggal");
  badView.unmount();
});

test("trend switches units without drawing a bar for a real zero", async () => {
  const multi = {
    ...plantBody,
    trend: {
      status: "complete",
      units: ["pcs", "pack"],
      series: {
        pcs: plantBody.trend.series.pcs,
        pack: plantBody.trend.series.pcs.map((point) => ({ ...point, qty: point.day.endsWith("23") ? 2 : 0 })),
      },
      note: "Konversi historis belum terverifikasi. Qty ditampilkan dalam satuan asli.",
    },
  };
  const apis = apisFor(layout([widget("tren_output", 0, 0, 8, 4)]));
  apis.analyticsApi = jest.fn(async () => multi);
  const view = await renderPage((key) => key === "dashboard.production.view", apis);
  await act(async () => { view.host.querySelector("[data-testid='produksi-trend-unit-pack']").click(); });
  const trend = view.host.querySelector("[data-testid='produksi-widget-tren_output']");
  expect(trend.textContent).toContain("pack");
  expect(trend.textContent).toContain("belum terverifikasi");
  expect(trend.querySelectorAll("[data-testid='produksi-trend-bar']")).toHaveLength(1);
  expect(trend.querySelectorAll("[data-testid='produksi-trend-zero']")).toHaveLength(6);
  view.unmount();
});

test("a saved layout is kept and reset applies only the server payload", async () => {
  const apis = apisFor(layout([widget("po_hold", 3, 0, 3, 2)]));
  apis.layoutApi.reset = jest.fn(async () => layout([
    widget("perlu_produksi", 0, 0, 3, 2),
    widget("prioritas", 0, 8, 12, 4),
  ], 0));
  const view = await renderPage((key) => key === "dashboard.production.view", apis);
  expect(view.host.querySelector("[data-testid='produksi-widget-po_hold']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='produksi-widget-perlu_produksi']")).toBeNull();
  await act(async () => { view.host.querySelector("[data-testid='produksi-more']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='produksi-reset-layout']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='produksi-reset-confirm']").click(); });
  await act(async () => { await flush(); await flush(); });
  expect(apis.layoutApi.reset).toHaveBeenCalled();
  expect(view.host.querySelector("[data-testid='produksi-widget-perlu_produksi']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='produksi-widget-po_hold']")).toBeNull();
  expect(view.host.querySelector("[data-testid='produksi-widget-output_operator']")).toBeNull();
  view.unmount();
});

test("layout load failure uses the default that omits operator analytics", async () => {
  const apis = apisFor(layout([]));
  apis.layoutApi.load = jest.fn(async () => { throw new Error("down"); });
  const view = await renderPage((key) => key === "dashboard.production.view", apis);
  expect(view.host.querySelector("[data-testid='produksi-widget-output_hari_ini']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='produksi-widget-tren_output']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='produksi-widget-prioritas']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='produksi-widget-output_operator']")).toBeNull();
  expect(view.host.querySelector("[data-testid='produksi-widget-beban_operator']")).toBeNull();
  expect(view.host.querySelector("[data-testid='produksi-widget-kesiapan']")).toBeNull();
  view.unmount();
});

const kesiapanBody = {
  status: "complete",
  siap_produksi: {
    ready: 4,
    not_ready: 1,
    hold_ready: 2,
    note: "2 PO HOLD sudah lolos Design dan Payment. Ini bukan izin menjadwalkan.",
  },
  hasil_cetak: {
    complete: 2,
    partial: 1,
    none: 1,
    not_applicable: 1,
    note: "1 PO tidak masuk ketiga kategori karena tidak ada line cetak atau qty line tidak dapat ditentukan.",
  },
};

test("kesiapan can be added, moved, resized, and removed without changing a saved layout", async () => {
  const apis = apisFor(layout([widget("po_aktif", 0, 0, 3, 2)]));
  apis.snapshotApi = jest.fn(async () => ({ ...snapshot, kesiapan: kesiapanBody }));
  const allow = (key) => key === "dashboard.production.view";
  const view = await renderPage(allow, apis);
  expect(view.host.querySelector("[data-testid='produksi-widget-kesiapan']")).toBeNull();
  expect(view.host.querySelector("[data-testid='produksi-widget-po_aktif']")).toBeTruthy();

  await act(async () => { view.host.querySelector("[data-testid='produksi-add-widget']").click(); });
  const option = view.host.querySelector("[data-testid='produksi-catalog-kesiapan']");
  expect(option.textContent).toContain("Kesiapan Produksi & Pengiriman");
  expect(option.textContent).not.toContain("Belum tersedia");
  const add = option.querySelector("button");
  expect(add.getAttribute("aria-label")).toBe("Tambahkan Kesiapan Produksi & Pengiriman");
  await act(async () => { add.click(); });
  const card = view.host.querySelector("[data-testid='produksi-widget-kesiapan']");
  expect(card.textContent).toContain("Siap Produksi");
  expect(card.textContent).toContain("Hasil Cetak Tersedia");
  expect(card.textContent).not.toContain("Siap Dikirim");
  expect(card.querySelector("[data-testid='produksi-kesiapan-hold']").textContent).toContain("bukan izin menjadwalkan");
  expect(card.querySelector("[data-testid='produksi-kesiapan-hold']").textContent).toContain("HOLD");
  expect(card.querySelector("section").getAttribute("title")).toContain("Bukan izin menjadwalkan");
  expect(card.querySelectorAll("section")[1].getAttribute("title")).toContain("Bukan barang siap diberangkatkan");
  expect(card.textContent).toContain("4");
  expect(card.textContent).toContain("1");

  await act(async () => { view.host.querySelector("[data-testid='produksi-edit-layout']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='produksi-menu-kesiapan']").click(); });
  await act(async () => { view.host.querySelector("[aria-label='Geser Kesiapan Produksi & Pengiriman ke kanan']").click(); });
  await act(async () => { view.host.querySelector("[aria-label='Perlebar Kesiapan Produksi & Pengiriman']").click(); });
  await act(async () => { await wait(700); });
  expect(apis.layoutApi.save).toHaveBeenCalled();
  const saved = apis.layoutApi.save.mock.calls.at(-1)[0];
  const placed = saved.widgets.find((item) => item.widget_id === "kesiapan");
  expect(saved.version).toBe(1);
  expect(placed.x).toBeGreaterThan(0);
  expect(placed.w).toBeGreaterThan(8);
  expect(layoutOverlaps(saved.widgets)).toBe(false);
  expect(saved.widgets.some((item) => item.widget_id === "po_aktif")).toBe(true);

  if (!view.host.querySelector("[data-testid='produksi-remove-kesiapan']")) {
    await act(async () => { view.host.querySelector("[data-testid='produksi-menu-kesiapan']").click(); });
  }
  await act(async () => { view.host.querySelector("[data-testid='produksi-remove-kesiapan']").click(); });
  expect(view.host.querySelector("[data-testid='produksi-widget-kesiapan']")).toBeNull();
  expect(view.host.querySelector("[data-testid='produksi-widget-po_aktif']")).toBeTruthy();
  view.unmount();
});

test("kesiapan stays on the loading state until the snapshot returns", async () => {
  let release;
  const apis = apisFor(layout([widget("kesiapan", 0, 0, 8, 4)]));
  apis.snapshotApi = jest.fn(() => new Promise((resolve) => { release = () => resolve({ ...snapshot, kesiapan: kesiapanBody }); }));
  const view = await renderPage((key) => key === "dashboard.production.view", apis);
  const card = view.host.querySelector("[data-testid='produksi-widget-kesiapan']");
  expect(card.getAttribute("data-status")).toBe("loading");
  expect(card.querySelector("[aria-label='Memuat']")).toBeTruthy();
  expect(card.querySelector(".pd-metric")).toBeNull();
  await act(async () => { release(); await flush(); await flush(); });
  expect(view.host.querySelector("[data-testid='produksi-widget-kesiapan']").textContent).toContain("Siap Produksi");
  view.unmount();
});

test("kesiapan hides numbers when the snapshot is empty, incomplete, or failed", async () => {
  const allow = (key) => key === "dashboard.production.view";
  const zeroApis = apisFor(layout([widget("kesiapan", 0, 0, 8, 4)]));
  zeroApis.snapshotApi = jest.fn(async () => ({
    ...snapshot,
    kesiapan: {
      status: "zero",
      siap_produksi: { ready: 0, not_ready: 0, hold_ready: 0, note: "" },
      hasil_cetak: { complete: 0, partial: 0, none: 0, not_applicable: 0, note: "" },
    },
  }));
  const zeroView = await renderPage(allow, zeroApis);
  const zeroCard = zeroView.host.querySelector("[data-testid='produksi-widget-kesiapan']");
  expect(zeroCard.textContent).toContain("Tidak ada PO aktif");
  expect(zeroCard.textContent).not.toContain("Siap Produksi");
  expect(zeroCard.textContent).not.toContain("Hasil Cetak Tersedia");
  zeroView.unmount();

  const missing = apisFor(layout([widget("kesiapan", 0, 0, 8, 4)]));
  const missingView = await renderPage(allow, missing);
  const missingCard = missingView.host.querySelector("[data-testid='produksi-widget-kesiapan']");
  expect(missingCard.textContent).toContain("Angka tidak ditampilkan");
  expect(missingCard.textContent).not.toContain("Siap Produksi");
  missingView.unmount();

  const failed = apisFor(layout([widget("kesiapan", 0, 0, 8, 4)]));
  failed.snapshotApi = jest.fn(async () => { throw new Error("down"); });
  const failedView = await renderPage(allow, failed);
  const failedCard = failedView.host.querySelector("[data-testid='produksi-widget-kesiapan']");
  expect(failedCard.textContent).toContain("Gagal memuat");
  expect(failedCard.textContent).not.toContain("Siap Produksi");
  expect(failedCard.querySelector(".pd-metric")).toBeNull();
  failedView.unmount();
});

test("kesiapan autosave keeps the layout version and stops on conflict", async () => {
  const apis = apisFor(layout([widget("kesiapan", 0, 0, 8, 4)], 3));
  apis.snapshotApi = jest.fn(async () => ({ ...snapshot, kesiapan: kesiapanBody }));
  const view = await renderPage((key) => key === "dashboard.production.view", apis);
  await act(async () => { view.host.querySelector("[data-testid='produksi-edit-layout']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='produksi-menu-kesiapan']").click(); });
  await act(async () => { view.host.querySelector("[aria-label='Geser Kesiapan Produksi & Pengiriman ke kanan']").click(); });
  await act(async () => { await wait(700); });
  expect(apis.layoutApi.save.mock.calls[0][0].version).toBe(3);
  await act(async () => { view.host.querySelector("[aria-label='Geser Kesiapan Produksi & Pengiriman ke kanan']").click(); });
  await act(async () => { await wait(700); });
  expect(apis.layoutApi.save.mock.calls.at(-1)[0].version).toBe(4);

  apis.layoutApi.save.mockRejectedValueOnce({ response: { status: 409, data: { code: "LAYOUT_CONFLICT" } } });
  await act(async () => { view.host.querySelector("[aria-label='Geser Kesiapan Produksi & Pengiriman ke kanan']").click(); });
  await act(async () => { await wait(700); });
  expect(view.host.textContent).toContain("Perubahan di tab lain terdeteksi");
  expect(view.host.querySelector("[data-testid='produksi-widget-kesiapan']")).toBeTruthy();
  view.unmount();
});

test("wave 2 widgets render locked targets and stay off a saved layout", async () => {
  const saved = layout([widget("po_aktif", 0, 0, 3, 2)]);
  const apis = apisFor(saved);
  const wave = {
    status: "complete",
    target_produksi: {
      status: "complete",
      note: "Realisasi cohort mengikuti tanggal jadwal.",
      series: [{ day: "2026-10-23", unit: "pcs", awal: 10000, revisi: null, realisasi: 7000, persen_awal: 70, persen_revisi: null }],
    },
    aging: { status: "complete", produksi: { aktif: 1, selesai: 0, belum: 0, histori: 0 }, total: { aktif: 1, selesai: 0, belum: 0, histori: 0 }, note: "Aging" },
    ketepatan: { status: "complete", produksi: { tepat: 0, terlambat: 0, berjalan: 1, tidak_tersedia: 0 }, pengiriman: { tepat: 0, terlambat: 0, berjalan: 0, tidak_tersedia: 1 }, note: "Terpisah" },
    bottleneck: { status: "complete", po: 1, lines: 1, by_unit: { pcs: 3 }, pengiriman_belum_selesai: 0, rows: [], note: "HOLD bukan bottleneck." },
  };
  apis.snapshotApi = jest.fn(async () => ({ ...snapshot, wave2: wave }));
  const allow = (key) => key === "dashboard.production.view";
  const hidden = await renderPage(allow, apis);
  expect(hidden.host.querySelector("[data-testid='produksi-widget-target_produksi']")).toBeNull();
  expect(hidden.host.textContent).not.toContain("10000");
  hidden.unmount();

  const shownApis = apisFor(layout([
    widget("target_produksi", 0, 0, 6, 4),
    widget("po_terlambat", 6, 0, 6, 3),
    widget("aging", 0, 4, 6, 3),
    widget("ketepatan", 6, 4, 6, 3),
    widget("bottleneck", 0, 7, 12, 4),
  ]));
  shownApis.snapshotApi = jest.fn(async () => ({ ...snapshot, wave2: wave }));
  shownApis.poLateTrackingSummaryApi = jest.fn(async () => ({
    status: "complete",
    total: 3,
    counts: { lewat_1_hari: 1, terlambat: 1, kritis: 1 },
    note: "PO aktif melewati tanggal pengiriman.",
  }));
  const view = await renderPage(allow, shownApis);
  expect(view.host.querySelector("[data-testid='produksi-target']").textContent).toContain("10.000");
  expect(view.host.querySelector("[data-testid='produksi-target']").textContent).toContain("Belum ada revisi");
  expect(view.host.querySelector("[data-testid='produksi-target']").textContent).toContain("7.000");
  expect(view.host.querySelector("[data-testid='produksi-terlambat-total']").textContent).toContain("3");
  expect(view.host.querySelector("[data-testid='produksi-terlambat']").textContent).toContain("Lewat 1 Hari");
  expect(view.host.querySelector("[data-testid='produksi-terlambat']").textContent).toContain("Kritis");
  expect(view.host.querySelector("[data-testid='produksi-aging']").textContent).toContain("Produksi");
  expect(view.host.querySelector("[data-testid='produksi-ketepatan']").textContent).toContain("Pengiriman");
  expect(view.host.querySelector("[data-testid='produksi-bottleneck']").textContent).toContain("HOLD bukan bottleneck");
  expect(view.host.textContent).not.toContain("SIMULASI");
  view.unmount();
});

test("target versus realization stays per unit and does not invent a percent", async () => {
  const allow = (key) => key === "dashboard.production.view";
  const one = {
    status: "complete",
    note: "Satu cohort.",
    series: [{ day: "2026-10-27", unit: "pcs", awal: 10000, revisi: 8000, realisasi: 6400, persen_awal: 64, persen_revisi: 80 }],
  };
  const apis = apisFor(layout([widget("target_realisasi", 0, 0, 6, 4)]));
  apis.snapshotApi = jest.fn(async () => ({ ...snapshot, wave2: { status: "complete", target_realisasi: one } }));
  const view = await renderPage(allow, apis);
  const card = view.host.querySelector("[data-testid='produksi-realisasi']");
  expect(card.textContent).toContain("10.000");
  expect(card.textContent).toContain("8.000");
  expect(card.textContent).toContain("6.400");
  expect(card.textContent).toContain("64% dari awal");
  expect(card.textContent).toContain("80% dari revisi");
  expect(card.querySelector(".pd-compare-bar.is-awal")).toBeTruthy();
  expect(card.querySelector(".pd-compare-bar.is-revisi")).toBeTruthy();
  expect(card.querySelector(".pd-compare-bar.is-realisasi")).toBeTruthy();
  view.unmount();

  const many = {
    status: "complete",
    series: [
      { day: "2026-10-27", unit: "pcs", awal: 100, revisi: null, realisasi: 0, persen_awal: 0, persen_revisi: null },
      { day: "2026-10-28", unit: "pcs", awal: 50, revisi: 40, realisasi: 10, persen_awal: 20, persen_revisi: 25 },
      { day: "2026-10-27", unit: "kg", awal: 2, revisi: null, realisasi: null, persen_awal: null, persen_revisi: null },
    ],
  };
  const multiApis = apisFor(layout([widget("target_realisasi", 0, 0, 8, 4)]));
  multiApis.snapshotApi = jest.fn(async () => ({ ...snapshot, wave2: { status: "complete", target_realisasi: many } }));
  const multi = await renderPage(allow, multiApis);
  const pcs = multi.host.querySelector("[data-testid='produksi-realisasi-unit-pcs']");
  const kg = multi.host.querySelector("[data-testid='produksi-realisasi-unit-kg']");
  const first = pcs.querySelector("[data-testid='produksi-realisasi-day-2026-10-27-pcs']");
  const second = pcs.querySelector("[data-testid='produksi-realisasi-day-2026-10-28-pcs']");
  expect(first.textContent).toContain("Belum ada revisi");
  expect(first.textContent).toContain("0% dari awal");
  expect(first.textContent).not.toContain("150");
  expect(second.textContent).toContain("40");
  expect(second.textContent).toContain("25% dari revisi");
  expect(kg.textContent).toContain("Belum ada bukti");
  expect(kg.textContent).not.toContain("%");
  expect(kg.querySelector("[data-testid='produksi-realisasi-day-2026-10-27-kg']").textContent).not.toContain("100");
  multi.unmount();

  const lockedApis = apisFor(layout([widget("target_realisasi", 0, 0, 6, 3)]));
  lockedApis.snapshotApi = jest.fn(async () => ({
    ...snapshot,
    wave2: { status: "complete", target_realisasi: { status: "unlocked", note: "Target belum dikunci.", series: [] } },
  }));
  const locked = await renderPage(allow, lockedApis);
  expect(locked.host.textContent).toContain("Target belum dikunci");
  expect(locked.host.querySelector(".pd-compare-bar")).toBeNull();
  locked.unmount();
});

test("aging and bottleneck keep the four statuses and separate counts", async () => {
  const allow = (key) => key === "dashboard.production.view";
  const apis = apisFor(layout([
    widget("aging", 0, 0, 6, 3),
    widget("bottleneck", 6, 0, 6, 4),
  ], 4));
  apis.snapshotApi = jest.fn(async () => ({
    ...snapshot,
    wave2: {
      status: "complete",
      aging: {
        status: "complete",
        produksi: { aktif: 6, selesai: 4, belum: 1, histori: 2 },
        total: { aktif: 7, selesai: 3, belum: 1, histori: 2 },
        note: "Empat status.",
      },
      bottleneck: {
        status: "complete",
        po: 2,
        lines: 3,
        by_unit: { pcs: 4000 },
        pengiriman_belum_selesai: 1,
        rows: [{ order_id: "po-1", po_number: "PO-PREVIEW", target: "2026-10-27", by_unit: { pcs: 4000 } }],
        note: "HOLD dan approval yang belum selesai bukan bottleneck produksi.",
      },
    },
  }));
  const view = await renderPage(allow, apis);
  const aging = view.host.querySelector("[data-testid='produksi-aging']");
  expect(aging.querySelector("[data-testid='produksi-aging-produksi']").textContent).toContain("Aging Produksi");
  expect(aging.querySelector("[data-testid='produksi-aging-total']").textContent).toContain("Aging Total PO");
  expect(aging.textContent).toContain("Aktif");
  expect(aging.textContent).toContain("Selesai");
  expect(aging.textContent).toContain("Belum dapat dihitung");
  expect(aging.textContent).toContain("Histori tidak lengkap");
  expect(aging.textContent).not.toMatch(/rata-rata|hari rata/i);
  const bottleneck = view.host.querySelector("[data-testid='produksi-bottleneck']");
  expect(bottleneck.textContent).toContain("PO Bottleneck");
  expect(bottleneck.textContent).toContain("Line Bottleneck");
  expect(bottleneck.textContent).toContain("Pengiriman belum selesai");
  expect(bottleneck.textContent).toContain("Sisa 4.000 pcs");
  expect(bottleneck.querySelector("[data-testid='produksi-bottleneck-row']").textContent).toContain("PO-PREVIEW");
  expect(bottleneck.textContent).toContain("Bukan otomatis kesalahan operator");
  expect(apis.layoutApi.save).not.toHaveBeenCalled();
  view.unmount();
});

test("composition and branch widgets keep units, history, and permission apart", async () => {
  const composition = {
    status: "partial",
    note: "Event tanpa snapshot tetap terpisah.",
    units: ["pcs", "pack"],
    products: { pcs: [{ key: "m1", name: "Cup A · PC-A", qty: 80 }, { key: "m2", name: "Cup A · PC-B", qty: 20 }] },
    unverified_by_unit: { pcs: 5 },
    unmapped_by_unit: { pcs: 2 },
    categories: {
      pcs: { status: "partial", slices: [{ key: "c1", name: "Paper Cup", qty: 80 }], uncategorized_qty: 20, excluded_historical_qty: 5 },
      pack: { status: "incomplete", slices: [], uncategorized_qty: 0, excluded_historical_qty: 3 },
    },
    history_status: "incomplete",
  };
  const branch = {
    status: "complete",
    units: ["pcs"],
    company_total: { pcs: 100 },
    actual: { pcs: [{ branch_id: "br-a", name: "Yogyakarta", qty: 70 }, { branch_id: null, name: "Belum Teratribusi", qty: 30 }] },
    origin: { pcs: [{ branch_id: "br-b", name: "Semarang", qty: 100 }] },
  };
  const apis = apisFor(layout([
    widget("komposisi", 0, 0, 6, 5),
    widget("output_cabang", 6, 0, 6, 5),
  ]));
  apis.analyticsApi = jest.fn(async () => ({ composition, today: { by_unit: {} }, trend: { units: [], series: {} }, workflow: { per_po: { full: { bars: [] }, production: { bars: [] } } } }));
  apis.branchOutputApi = jest.fn(async () => branch);
  const allow = (key) => key === "dashboard.production.view" || key === "dashboard.production.branch_output";
  const view = await renderPage(allow, { ...apis, branchOutputApi: apis.branchOutputApi });
  const card = view.host.querySelector("[data-testid='produksi-komposisi']");
  expect(card.textContent).toContain("Paper Cup");
  expect(card.textContent).toContain("Histori tanpa snapshot kategori");
  await act(async () => {
    view.host.querySelector("[data-testid='produksi-komposisi-produk']").click();
  });
  expect(view.host.textContent).toContain("Cup A · PC-A");
  expect(view.host.textContent).toContain("Cup A · PC-B");
  expect(view.host.textContent).toContain("Identitas historis tidak terverifikasi");
  expect(view.host.querySelector("[data-testid='produksi-cabang-aktual']").className).toContain("is-on");
  expect(view.host.textContent).toContain("Yogyakarta");
  expect(view.host.textContent).toContain("Belum Teratribusi");
  await act(async () => {
    view.host.querySelector("[data-testid='produksi-cabang-asal']").click();
  });
  expect(view.host.textContent).toContain("Semarang");
  expect(apis.branchOutputApi).toHaveBeenCalled();
  view.unmount();

  const denied = apisFor(layout([widget("output_cabang", 0, 0, 6, 5)]));
  denied.branchOutputApi = jest.fn(async () => branch);
  const hidden = await renderPage((key) => key === "dashboard.production.view", { ...denied, branchOutputApi: denied.branchOutputApi });
  expect(hidden.host.querySelector("[data-testid='produksi-widget-output_cabang']").textContent).toContain("Tidak ada izin");
  expect(denied.branchOutputApi).not.toHaveBeenCalled();
  hidden.unmount();
});

test("qty rencana pengiriman stays on production with separate today and overdue", async () => {
  const payload = {
    status: "complete",
    days: 7,
    today: "2026-10-24",
    series: [
      {
        unit: "PCS",
        today_qty: 60,
        today_po_count: 2,
        overdue_qty: 15,
        overdue_po_count: 1,
        points: [
          { date: "2026-10-24", qty: 60, po_count: 2 },
          { date: "2026-10-25", qty: 0, po_count: 0 },
          { date: "2026-10-26", qty: 12, po_count: 1 },
          { date: "2026-10-27", qty: 0, po_count: 0 },
          { date: "2026-10-28", qty: 8, po_count: 1 },
          { date: "2026-10-29", qty: 0, po_count: 0 },
          { date: "2026-10-30", qty: 10, po_count: 1 },
        ],
      },
      {
        unit: "DUS",
        today_qty: 4,
        today_po_count: 1,
        overdue_qty: 0,
        overdue_po_count: 0,
        points: [
          { date: "2026-10-24", qty: 4, po_count: 1 },
          { date: "2026-10-25", qty: 0, po_count: 0 },
          { date: "2026-10-26", qty: 0, po_count: 0 },
          { date: "2026-10-27", qty: 2, po_count: 1 },
          { date: "2026-10-28", qty: 0, po_count: 0 },
          { date: "2026-10-29", qty: 0, po_count: 0 },
          { date: "2026-10-30", qty: 1, po_count: 1 },
        ],
      },
    ],
    notes: ["PO dengan lebih dari satu satuan ditampilkan terpisah sebagai campuran dan tidak dijumlahkan ke satuan lain."],
  };
  const apis = apisFor(layout([
    widget("po_aktif", 0, 0, 3, 2),
    widget("delivery_qty", 3, 0, 7, 7),
  ]));
  apis.deliveryPlanQtyApi = jest.fn(async () => payload);
  const allow = (key) => key === "dashboard.production.view" || key === "delivery.schedule.view";
  const view = await renderPage(allow, apis);
  const card = view.host.querySelector("[data-testid='produksi-widget-delivery_qty']");
  expect(card.textContent).toContain("Qty Rencana Pengiriman");
  expect(card.textContent).toContain("Monitoring sisa qty berdasarkan tanggal rencana");
  expect(card.textContent).toContain("Rencana Hari Ini");
  expect(card.textContent).toContain("Lewat Rencana");
  expect(card.textContent).toContain("Rencana per Hari");
  const todayKpi = card.querySelector("[data-testid='produksi-delivery-qty-today']");
  const overdueKpi = card.querySelector("[data-testid='produksi-delivery-qty-overdue']");
  expect(todayKpi.textContent).toContain("60");
  expect(todayKpi.textContent).toContain("2 PO aktif");
  expect(todayKpi.textContent).toContain("24 Okt 2026");
  expect(overdueKpi.textContent).toContain("15");
  expect(overdueKpi.textContent).toContain("1 PO masih bersisa");
  expect(`${todayKpi.textContent}${overdueKpi.textContent}`).not.toMatch(/\b75\b/);
  expect(card.textContent).toContain("Menampilkan total qty sisa per tanggal rencana");
  const tipCol = [...card.querySelectorAll("[data-testid='produksi-delivery-qty-bar-col']")]
    .find((node) => node.getAttribute("aria-label")?.includes("26 Oktober 2026"));
  expect(tipCol).toBeTruthy();
  expect(tipCol.getAttribute("aria-label")).toContain("Rencana 12 pcs");
  expect(tipCol.getAttribute("aria-label")).toContain("Jumlah PO 1");
  expect(tipCol.querySelector("[data-testid='produksi-delivery-qty-tip']").textContent).toContain("Senin, 26 Oktober 2026");
  expect(tipCol.querySelector("[data-testid='produksi-delivery-qty-tip']").textContent).toContain("Jumlah PO: 1");
  expect(view.host.querySelector("[data-testid='produksi-widget-po_aktif']").textContent).toContain("5");
  expect(apis.deliveryPlanQtyApi).toHaveBeenCalledWith(7);
  await act(async () => { view.host.querySelector("[data-testid='produksi-delivery-qty-unit-DUS']").click(); });
  expect(card.textContent).toContain("4");
  expect(card.textContent).toContain("dus");
  await act(async () => { view.host.querySelector("[data-testid='produksi-delivery-qty-days-30']").click(); });
  await act(async () => { await flush(); await flush(); });
  expect(apis.deliveryPlanQtyApi).toHaveBeenCalledWith(30);
  await act(async () => { view.host.querySelector("[data-testid='produksi-menu-delivery_qty']").click(); });
  const openPlan = view.host.querySelector("[data-testid='produksi-open-delivery_qty']");
  expect(openPlan).toBeTruthy();
  expect(openPlan.getAttribute("href")).toBe("/produksi/rencana-jadwal-kirim");
  expect(openPlan.textContent).toContain("Lihat Rencana Pengiriman");
  expect(view.host.querySelector("[data-testid='produksi-info-delivery_qty']")).toBeTruthy();
  expect(card.textContent).not.toContain("Download Data");
  await act(async () => { view.host.querySelector("[data-testid='produksi-remove-delivery_qty']").click(); });
  await act(async () => { await wait(700); });
  expect(view.host.querySelector("[data-testid='produksi-widget-delivery_qty']")).toBeNull();
  expect(view.host.querySelector("[data-testid='produksi-widget-po_aktif']")).toBeTruthy();
  await act(async () => { view.host.querySelector("[data-testid='produksi-add-widget']").click(); });
  expect(view.host.querySelector("[data-testid='produksi-catalog-delivery_qty']").textContent).toContain("Qty Rencana Pengiriman");
  view.unmount();

  const denied = apisFor(layout([widget("delivery_qty", 0, 0, 6, 5)]));
  denied.deliveryPlanQtyApi = jest.fn(async () => payload);
  const blocked = await renderPage((key) => key === "dashboard.production.view", denied);
  expect(blocked.host.querySelector("[data-testid='produksi-widget-delivery_qty']").textContent).toContain("Tidak ada izin");
  expect(denied.deliveryPlanQtyApi).not.toHaveBeenCalled();
  blocked.unmount();
});

test("PO Aktif Rincian shows in view mode and hides in edit mode without navigating", async () => {
  const apis = apisFor(layout([widget("po_aktif", 0, 0, 3, 2)]));
  const allow = (key) => (
    key === "dashboard.production.view"
    || key === "nav.reports.status_po"
    || key === "schedule.view"
    || key === "schedule.edit"
  );
  const view = await renderPage(allow, apis);
  const card = view.host.querySelector("[data-testid='produksi-widget-po_aktif']");
  expect(card.getAttribute("data-drill")).toBe("ready");
  expect(card.className).toContain("is-drillable");
  const rincian = [...card.querySelectorAll("a")].find((node) => node.textContent.toLowerCase().includes("rincian"));
  expect(rincian).toBeTruthy();
  expect(rincian.getAttribute("href")).toContain("/dashboard/rincian?");
  expect(rincian.getAttribute("href")).toContain("widget=po_aktif");
  expect(rincian.getAttribute("href")).toContain("dashboard=produksi");

  await act(async () => { view.host.querySelector("[data-testid='produksi-edit-layout']").click(); });
  const editingCard = view.host.querySelector("[data-testid='produksi-widget-po_aktif']");
  expect(editingCard.className).toContain("is-edit");
  expect(editingCard.className).not.toContain("is-drillable");
  expect(editingCard.getAttribute("data-drill")).toBeNull();
  expect([...editingCard.querySelectorAll("a")].some((node) => node.textContent.toLowerCase().includes("rincian"))).toBe(false);
  expect(view.host.querySelector("[data-testid='produksi-drag-po_aktif']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='produksi-menu-po_aktif']")).toBeTruthy();
  await act(async () => { editingCard.click(); });
  expect(view.host.querySelector("[data-testid='produksi-widget-po_aktif']")).toBeTruthy();
  view.unmount();
});

