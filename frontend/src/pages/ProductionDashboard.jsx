import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../lib/auth";
import api from "../lib/api";
import ProductionWidgetBoard, { WidgetGlyph } from "../components/productionDashboard/ProductionWidgetBoard";
import {
  PENDING_WIDGETS,
  PRODUCTION_WIDGETS,
  productionWidgetAllowed,
} from "../components/productionDashboard/productionWidgetCatalog";
import { addWidget, defaultLayout } from "../components/productionDashboard/productionWidgetLayout";
import { mapDeliveryQty } from "../lib/widgetDashboardModel";
import { mapPoLateSummary } from "../lib/poLateTracking";
import {
  buildPoLateVisualSummary,
  isPoLateVisualFixtureEnabled,
} from "../lib/poLateTrackingVisualFixture";
import "../components/financeDashboard/financeDashboard.css";
import "../components/productionDashboard/productionDashboard.css";

const SAVE_LABEL = {
  idle: "",
  saving: "Menyimpan",
  saved: "Tersimpan",
  error: "Gagal",
  conflict: "Konflik",
};

function zeroOrComplete(value) {
  return value === 0 ? "zero" : "complete";
}

const SNAPSHOT_IDS = ["po_aktif", "perlu_produksi", "po_urgent", "po_hold", "sisa_qty", "deadline", "approval", "prioritas", "kesiapan", "target_produksi", "target_realisasi", "aging", "ketepatan", "bottleneck"];
const PLANT_IDS = ["output_hari_ini", "tren_output", "distribusi_tahap", "komposisi"];
const OPERATOR_IDS = ["output_operator", "beban_operator"];
const BRANCH_IDS = ["output_cabang"];
const DELIVERY_QTY_IDS = ["delivery_qty"];
const PO_LATE_IDS = ["po_terlambat"];

function mapToday(plant) {
  const today = plant?.today || {};
  return {
    status: today.status || "incomplete",
    byUnit: today.by_unit || {},
    note: today.note || "",
    updatedAt: today.updated_at || "",
  };
}

function mapTrend(plant, unitChoice) {
  const trend = plant?.trend || {};
  const units = trend.units || [];
  const unit = units.includes(unitChoice) ? unitChoice : (units[0] || "");
  return {
    status: trend.status || "incomplete",
    note: trend.note || "",
    unit,
    units,
    series: (trend.series || {})[unit] || [],
  };
}

function mapWorkflow(plant, scope) {
  const chosen = scope === "semua" ? "full" : "production";
  const block = plant?.workflow?.per_po?.[chosen] || {};
  return {
    status: block.status || "zero",
    note: block.note || "",
    scope: scope === "semua" ? "semua" : "produksi",
    bars: block.bars || [],
    total: block.total || 0,
    approval: block.approval_detail || null,
    perLine: plant?.workflow?.per_line?.note || "Belum tersedia — menunggu sumber status per line yang valid.",
  };
}

function mapPerformance(body, unitChoice) {
  const performance = body?.performance || {};
  const units = performance.units || [];
  const unit = units.includes(unitChoice) ? unitChoice : (units[0] || "");
  return {
    status: performance.status || "incomplete",
    note: performance.note || "Target Awal dan Target Revisi belum tersedia. Persentase tidak ditampilkan.",
    targetAvailable: Boolean(performance.target_available),
    unit,
    units,
    rows: (performance.ranking || {})[unit] || [],
  };
}

function mapWorkload(body) {
  const workload = body?.workload || {};
  return {
    status: workload.status || "zero",
    note: workload.note || "",
    rows: workload.operators || [],
  };
}

function mapKesiapan(body) {
  const block = body?.kesiapan;
  const siap = block?.siap_produksi;
  const cetak = block?.hasil_cetak;
  if (!block || !siap || !cetak) return { status: "incomplete" };
  if (block.status !== "complete" && block.status !== "zero") return { status: "incomplete" };
  return { status: block.status, siap, cetak };
}

function mapWaveBlock(body, key) {
  const wave = body?.wave2;
  if (!wave) return { status: "incomplete" };
  if (wave.status === "zero") return { status: "zero" };
  const block = wave[key];
  if (!block || (block.status !== "complete" && block.status !== "zero" && block.status !== "unlocked")) {
    return { status: "incomplete" };
  }
  return block;
}

function modelsFromSnapshot(body) {
  const sisaUnits = body?.sisa_qty?.by_unit || {};
  const sisaKeys = Object.keys(sisaUnits);
  const priorityRows = body?.prioritas?.rows || [];
  return {
    po_aktif: { status: zeroOrComplete(body.po_aktif.count), count: body.po_aktif.count, drill: body.po_aktif.drill },
    perlu_produksi: { status: zeroOrComplete(body.perlu_produksi.count), count: body.perlu_produksi.count, drill: body.perlu_produksi.drill },
    po_urgent: { status: zeroOrComplete(body.po_urgent.count), count: body.po_urgent.count, drill: body.po_urgent.drill },
    po_hold: { status: zeroOrComplete(body.po_hold.count), count: body.po_hold.count, drill: body.po_hold.drill },
    sisa_qty: {
      status: sisaKeys.length ? "complete" : "zero",
      byUnit: sisaUnits,
      value: body.sisa_qty.value,
      unit: body.sisa_qty.unit,
      drill: body.sisa_qty.drill,
    },
    deadline: {
      status: body.deadline.within_6h || body.deadline.within_24h ? "complete" : "zero",
      within6h: body.deadline.within_6h,
      within24h: body.deadline.within_24h,
      drill: body.deadline.drill,
      drill6h: body.deadline.drill_6h,
      drill24h: body.deadline.drill_24h,
    },
    approval: {
      status: body.approval.design || body.approval.payment ? "complete" : "zero",
      design: body.approval.design,
      payment: body.approval.payment,
      drill: body.approval.drill,
      drillDesign: body.approval.drill_design,
      drillPayment: body.approval.drill_payment,
    },
    prioritas: {
      status: priorityRows.length ? "complete" : "empty",
      rows: priorityRows,
      drill: body.prioritas.drill,
    },
    kesiapan: mapKesiapan(body),
    target_produksi: mapWaveBlock(body, "target_produksi"),
    target_realisasi: mapWaveBlock(body, "target_realisasi"),
    aging: mapWaveBlock(body, "aging"),
    ketepatan: mapWaveBlock(body, "ketepatan"),
    bottleneck: mapWaveBlock(body, "bottleneck"),
  };
}

export function ProductionDashboardView({
  hasPerm,
  layoutApi,
  snapshotApi,
  analyticsApi = null,
  operatorAnalyticsApi = null,
  branchOutputApi = null,
  deliveryPlanQtyApi = null,
  poLateTrackingSummaryApi = null,
  simulation = null,
}) {
  const [widgets, setWidgets] = useState([]);
  const [simWidgets, setSimWidgets] = useState(simulation?.widgets || []);
  useEffect(() => {
    if (simulation?.widgets) setSimWidgets(simulation.widgets);
  }, [simulation]);
  const [editing, setEditing] = useState(false);
  const [keysHelp, setKeysHelp] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const [saveState, setSaveState] = useState("idle");
  const [notice, setNotice] = useState("");
  const [models, setModels] = useState({});
  const [plant, setPlant] = useState(null);
  const [operatorBody, setOperatorBody] = useState(null);
  const [branchBody, setBranchBody] = useState(null);
  const [days, setDays] = useState(7);
  const [scope, setScope] = useState("produksi");
  const [trendUnit, setTrendUnit] = useState("");
  const [operatorUnit, setOperatorUnit] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const [catalogChip, setCatalogChip] = useState("all");
  const [reloadToken, setReloadToken] = useState(0);
  const versionRef = useRef(0);
  const dirtyRef = useRef(false);
  const conflictRef = useRef(false);
  const requestRef = useRef(0);
  const saveQueue = useRef(Promise.resolve());

  const allowedKey = PRODUCTION_WIDGETS.map((item) => `${item.id}:${productionWidgetAllowed(item.id, hasPerm) ? 1 : 0}`).join("|");
  const canRead = PRODUCTION_WIDGETS.some((item) => productionWidgetAllowed(item.id, hasPerm));

  function applyServer(body) {
    dirtyRef.current = false;
    conflictRef.current = false;
    versionRef.current = body.version || 0;
    setWidgets(Array.isArray(body.widgets) ? body.widgets : []);
    setSaveState("idle");
    const ignored = body.ignored?.length ? `${body.ignored.length} widget tersimpan tidak dikenali dan tidak ditimpa.` : "";
    const withheld = body.withheld?.length ? `${body.withheld.length} widget disembunyikan karena izin berubah.` : "";
    setNotice([ignored, withheld].filter(Boolean).join(" "));
  }

  async function loadLayout() {
    applyServer(await layoutApi.load());
  }

  useEffect(() => {
    if (simulation) return undefined;
    let live = true;
    layoutApi.load().then((body) => {
      if (live) applyServer(body);
    }).catch(() => {
      if (!live) return;
      setNotice("Tata letak gagal dimuat. Tata letak lokal tidak dipakai sebagai pengganti.");
      setWidgets(defaultLayout(hasPerm));
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowedKey, simulation]);

  function queueSave(nextWidgets) {
    if (conflictRef.current) return;
    saveQueue.current = saveQueue.current.then(async () => {
      if (!dirtyRef.current || conflictRef.current) return;
      setSaveState("saving");
      try {
        const body = await layoutApi.save({ version: versionRef.current, widgets: nextWidgets });
        versionRef.current = body.version;
        dirtyRef.current = false;
        setSaveState("saved");
      } catch (error) {
        const status = error?.response?.status;
        const code = error?.response?.data?.detail?.code || error?.response?.data?.code;
        if (status === 409 || code === "LAYOUT_CONFLICT") {
          conflictRef.current = true;
          setSaveState("conflict");
          setNotice("Perubahan di tab lain terdeteksi. Tata letak di layar ini tetap ada dan belum ditulis.");
          return;
        }
        setSaveState("error");
        setNotice("Penyimpanan gagal. Perubahan di layar ini tetap ada.");
      }
    });
  }

  useEffect(() => {
    if (simulation || !dirtyRef.current || conflictRef.current) return undefined;
    const handle = setTimeout(() => queueSave(widgets), 600);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widgets]);

  function changeLayout(next) {
    if (simulation) {
      setSimWidgets(next);
      return;
    }
    if (conflictRef.current) return;
    dirtyRef.current = true;
    setWidgets(next);
  }

  const dataKey = useMemo(
    () => `${reloadToken}|${days}|${widgets.map((widget) => `${widget.widget_id}:${productionWidgetAllowed(widget.widget_id, hasPerm) ? 1 : 0}`).join("~")}|${allowedKey}`,
    [widgets, allowedKey, reloadToken, hasPerm, days],
  );

  useEffect(() => {
    if (simulation) {
      setModels(simulation.models || {});
      setPlant(null);
      setOperatorBody(null);
      setBranchBody(null);
      return undefined;
    }
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    const next = {};
    widgets.forEach((widget) => {
      next[widget.widget_id] = productionWidgetAllowed(widget.widget_id, hasPerm)
        ? { status: "loading" }
        : { status: "forbidden" };
    });
    setModels(next);
    const allowed = (id) => widgets.some((widget) => widget.widget_id === id && productionWidgetAllowed(id, hasPerm));
    const needSnapshot = SNAPSHOT_IDS.some(allowed);
    const needPlant = PLANT_IDS.some(allowed);
    const needOperator = OPERATOR_IDS.some(allowed);
    const needBranch = BRANCH_IDS.some(allowed);
    const needDeliveryQty = DELIVERY_QTY_IDS.some(allowed);
    const needPoLate = PO_LATE_IDS.some(allowed);
    if (!needSnapshot && !needPlant && !needOperator && !needBranch && !needDeliveryQty && !needPoLate) return undefined;
    let live = true;
    const jobs = [];
    if (needSnapshot) {
      jobs.push(snapshotApi().then((body) => ({ kind: "snapshot", body })).catch(() => ({ kind: "snapshot", error: true })));
    }
    if (needPlant) {
      jobs.push((analyticsApi ? analyticsApi(days) : Promise.reject(new Error("missing")))
        .then((body) => ({ kind: "plant", body }))
        .catch(() => ({ kind: "plant", error: true })));
    }
    if (needOperator) {
      jobs.push((operatorAnalyticsApi ? operatorAnalyticsApi(days) : Promise.reject(new Error("missing")))
        .then((body) => ({ kind: "operator", body }))
        .catch(() => ({ kind: "operator", error: true })));
    }
    if (needBranch) {
      jobs.push((branchOutputApi ? branchOutputApi(days) : Promise.reject(new Error("missing")))
        .then((body) => ({ kind: "branch", body }))
        .catch((error) => ({ kind: "branch", error: true, forbidden: error?.response?.status === 403 })));
    }
    if (needDeliveryQty) {
      jobs.push((deliveryPlanQtyApi ? deliveryPlanQtyApi(days) : Promise.reject(new Error("missing")))
        .then((body) => ({ kind: "delivery_qty", body }))
        .catch((error) => ({ kind: "delivery_qty", error: true, forbidden: error?.response?.status === 403 })));
    }
    if (needPoLate) {
      const wantVisualFixture = typeof window !== "undefined"
        && isPoLateVisualFixtureEnabled(new URLSearchParams(window.location.search));
      jobs.push((wantVisualFixture
        ? Promise.resolve(buildPoLateVisualSummary())
        : (poLateTrackingSummaryApi ? poLateTrackingSummaryApi() : Promise.reject(new Error("missing"))))
        .then((body) => ({ kind: "po_late", body }))
        .catch((error) => ({ kind: "po_late", error: true, forbidden: error?.response?.status === 403 })));
    }
    Promise.all(jobs).then((results) => {
      if (!live || requestId !== requestRef.current) return;
      const snapshot = results.find((item) => item.kind === "snapshot");
      const plantResult = results.find((item) => item.kind === "plant");
      const operatorResult = results.find((item) => item.kind === "operator");
      const branchResult = results.find((item) => item.kind === "branch");
      const deliveryResult = results.find((item) => item.kind === "delivery_qty");
      const poLateResult = results.find((item) => item.kind === "po_late");
      const mapped = snapshot && !snapshot.error ? modelsFromSnapshot(snapshot.body) : null;
      setPlant(plantResult && !plantResult.error ? plantResult.body : null);
      setOperatorBody(operatorResult && !operatorResult.error ? operatorResult.body : null);
      setBranchBody(branchResult && !branchResult.error ? branchResult.body : null);
      setModels((current) => {
        const patch = { ...current };
        widgets.forEach((widget) => {
          if (!productionWidgetAllowed(widget.widget_id, hasPerm)) return;
          if (SNAPSHOT_IDS.includes(widget.widget_id)) {
            patch[widget.widget_id] = mapped ? (mapped[widget.widget_id] || { status: "incomplete" }) : { status: "error" };
          }
          if (PLANT_IDS.includes(widget.widget_id)) {
            patch[widget.widget_id] = plantResult && !plantResult.error
              ? (widget.widget_id === "komposisi" ? { status: "ready", ...(plantResult.body.composition || {}) } : { status: "ready" })
              : { status: "error" };
          }
          if (BRANCH_IDS.includes(widget.widget_id)) {
            patch[widget.widget_id] = branchResult && !branchResult.error
              ? { status: "ready", ...branchResult.body }
              : { status: branchResult && branchResult.forbidden ? "forbidden" : "error" };
          }
          if (OPERATOR_IDS.includes(widget.widget_id)) {
            patch[widget.widget_id] = operatorResult && !operatorResult.error ? { status: "ready" } : { status: "error" };
          }
          if (DELIVERY_QTY_IDS.includes(widget.widget_id)) {
            patch[widget.widget_id] = deliveryResult && !deliveryResult.error
              ? mapDeliveryQty(deliveryResult.body)
              : { status: deliveryResult && deliveryResult.forbidden ? "forbidden" : "error" };
          }
          if (PO_LATE_IDS.includes(widget.widget_id)) {
            patch[widget.widget_id] = poLateResult && !poLateResult.error
              ? mapPoLateSummary(poLateResult.body)
              : { status: poLateResult && poLateResult.forbidden ? "forbidden" : "error" };
          }
        });
        return patch;
      });
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataKey, simulation]);

  const boardModels = useMemo(() => {
    if (simulation) return models;
    const patch = { ...models };
    const fill = (id, model) => {
      if (!patch[id] || patch[id].status === "forbidden" || patch[id].status === "loading" || patch[id].status === "error") return;
      patch[id] = model;
    };
    if (plant) {
      fill("output_hari_ini", mapToday(plant));
      fill("tren_output", mapTrend(plant, trendUnit));
      fill("distribusi_tahap", mapWorkflow(plant, scope));
      if (plant.composition) fill("komposisi", { status: plant.composition.status || "ready", ...plant.composition });
    }
    if (branchBody) fill("output_cabang", { status: branchBody.status || "ready", ...branchBody });
    if (operatorBody) {
      fill("output_operator", mapPerformance(operatorBody, operatorUnit));
      fill("beban_operator", mapWorkload(operatorBody));
    }
    return patch;
  }, [models, plant, operatorBody, branchBody, scope, trendUnit, operatorUnit, simulation]);

  const needle = query.trim().toLowerCase();
  const chips = [
    { id: "all", label: "Semua" },
    { id: "Snapshot", label: "Snapshot" },
    { id: "Monitoring", label: "Monitoring" },
    { id: "Analitik", label: "Analitik" },
  ];
  const categories = ["Snapshot", "Monitoring", "Analitik"].filter((id) => catalogChip === "all" || catalogChip === id);

  return (
    <div className="keuangan-board-page produksi-board-page" data-testid="produksi-dashboard-page" data-mode={simulation ? "simulasi" : "operasional"}>
      <header className="kb-head">
        <div className="kb-identity">
          <h1>Dashboard Produksi</h1>
          <p>Pantau sisa pekerjaan, urgent, HOLD, deadline, dan prioritas tindakan.</p>
        </div>
        <div className="kb-toolbar">
          <div className="kb-actions">
            <span className="pd-scope">Snapshot saat ini · Asia/Jakarta</span>
            <button type="button" className="kb-btn" data-testid="produksi-refresh" onClick={() => setReloadToken((value) => value + 1)}>Muat ulang</button>
            <span className={`kb-save is-${saveState}`} data-testid="produksi-save-state">{SAVE_LABEL[saveState]}</span>
            <button type="button" className="kb-btn is-primary" data-testid="produksi-add-widget" onClick={() => setCatalogOpen(true)} disabled={saveState === "conflict"}>+ Tambah Widget</button>
            <button type="button" className={`kb-btn${editing ? " is-primary" : ""}`} data-testid="produksi-edit-layout" onClick={() => setEditing((value) => !value)}>{editing ? "Selesai" : "Atur Tata Letak"}</button>
            <div className="kb-more">
              <button type="button" className="kb-icon-btn" aria-label="Tindakan lain" aria-expanded={moreOpen} data-testid="produksi-more" onClick={() => setMoreOpen((value) => !value)}>⋯</button>
              {moreOpen ? (
                <div className="kb-pop" role="menu">
                  <button type="button" data-testid="produksi-reset-layout" onClick={() => { setMoreOpen(false); setConfirmReset(true); }}>Reset Tata Letak</button>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </header>
      {simulation ? <p className="pd-sim-banner" data-testid="produksi-sim-banner">SIMULASI — bukan data operasional</p> : null}
      {editing ? (
        <div className="kb-edit-note">
          <p>Mode ubah tata letak aktif. Seret gagang kartu, atau tarik sudut kanan bawah.</p>
          <button type="button" className="kb-btn" aria-expanded={keysHelp} onClick={() => setKeysHelp((value) => !value)}>Bantuan papan ketik</button>
          {keysHelp ? <p className="kb-keys-help">Buka menu titik tiga pada kartu. Tombol geser dan ubah ukuran ada di dalam menu itu.</p> : null}
        </div>
      ) : null}
      {notice ? <p className="kb-banner" data-testid="produksi-notice">{notice}</p> : null}
      {!canRead ? <p className="kb-banner">Dashboard Produksi membutuhkan izin lihat produksi.</p> : null}
      {saveState === "conflict" ? (
        <p className="kb-banner">
          <button type="button" className="kb-btn" data-testid="produksi-reload-layout" onClick={() => loadLayout().catch(() => setNotice("Gagal memuat ulang tata letak."))}>Muat tata letak server</button>
        </p>
      ) : null}
      {confirmReset ? (
        <div className="kb-confirm">
          <p>Reset menghapus tata letak pribadi dan mengembalikan susunan awal sesuai izin Anda.</p>
          <button type="button" className="kb-btn is-primary" data-testid="produksi-reset-confirm" onClick={async () => {
            applyServer(await layoutApi.reset());
            setConfirmReset(false);
            setEditing(false);
          }}>Reset</button>
          <button type="button" className="kb-btn" onClick={() => setConfirmReset(false)}>Batal</button>
        </div>
      ) : null}
      <div className="kb-scroll">
        <ProductionWidgetBoard
          widgets={simulation ? simWidgets : widgets}
          models={boardModels}
          editing={editing}
          hasPerm={hasPerm}
          onLayout={changeLayout}
          onRetry={() => setReloadToken((value) => value + 1)}
          controls={{ days, setDays, scope, setScope, setTrendUnit, setOperatorUnit }}
        />
      </div>
      {catalogOpen ? (
        <div className="kb-overlay" onMouseDown={() => setCatalogOpen(false)}>
          <aside className="kb-drawer" data-testid="produksi-catalog" onMouseDown={(event) => event.stopPropagation()}>
            <header className="kb-drawer-head">
              <h2>Tambah Widget</h2>
              <button type="button" className="kb-drawer-close" aria-label="Tutup" onClick={() => setCatalogOpen(false)}>×</button>
            </header>
            <div className="kb-drawer-body">
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Cari widget..." data-testid="produksi-catalog-search" />
              <div className="kb-chips">
                {chips.map((chip) => (
                  <button type="button" key={chip.id} className={`kb-btn${catalogChip === chip.id ? " is-on" : ""}`} onClick={() => setCatalogChip(chip.id)}>{chip.label}</button>
                ))}
              </div>
              {categories.flatMap((category) => {
                const ready = PRODUCTION_WIDGETS.filter((item) => item.category === category && (!needle || item.title.toLowerCase().includes(needle)));
                const pending = PENDING_WIDGETS.filter((item) => item.category === category && (!needle || item.title.toLowerCase().includes(needle)));
                return [
                  ...ready.map((item) => {
                    const permitted = productionWidgetAllowed(item.id, hasPerm);
                    const placed = widgets.some((widget) => widget.widget_id === item.id);
                    const canAdd = permitted && !placed && saveState !== "conflict";
                    return (
                      <article key={item.id} className="kb-option" data-testid={`produksi-catalog-${item.id}`}>
                        <WidgetGlyph id={item.id} />
                        <div>
                          <strong>{item.title}</strong>
                          <p>{item.summary}</p>
                        </div>
                        {canAdd ? null : <span className="kb-badge is-off">{!permitted ? "Tidak memiliki akses" : "Terpasang"}</span>}
                        {canAdd ? (
                          <button type="button" className="kb-plus" aria-label={`Tambahkan ${item.title}`} onClick={() => changeLayout(addWidget(widgets, item.id))}>Tambah</button>
                        ) : null}
                      </article>
                    );
                  }),
                  ...pending.map((item) => (
                    <article key={item.id} className="kb-option" data-testid={`produksi-catalog-${item.id}`}>
                      <WidgetGlyph id={item.id} />
                      <div>
                        <strong>{item.title}</strong>
                        <p>{item.summary}</p>
                      </div>
                      <span className="kb-badge is-off">Belum tersedia</span>
                    </article>
                  )),
                ];
              })}
            </div>
          </aside>
        </div>
      ) : null}
    </div>
  );
}

function RoutedProductionDashboard() {
  const { hasPerm } = useAuth();
  const wantsSimulation = process.env.NODE_ENV === "development"
    && new URLSearchParams(window.location.search).get("simulasi") === "1";
  const [simulation, setSimulation] = useState(null);
  const [ready, setReady] = useState(!wantsSimulation);
  useEffect(() => {
    if (!wantsSimulation) return undefined;
    let live = true;
    import("../components/productionDashboard/productionSimulation").then((mod) => {
      if (!live) return;
      setSimulation(mod.simulationBoard());
      setReady(true);
    });
    return () => { live = false; };
  }, [wantsSimulation]);
  if (!ready) return null;
  return (
    <ProductionDashboardView
      hasPerm={hasPerm}
      simulation={simulation}
      layoutApi={{
        load: () => api.productionDashboardLayout(),
        save: (body) => api.saveProductionDashboardLayout(body),
        reset: () => api.resetProductionDashboardLayout(),
      }}
      snapshotApi={() => api.productionSnapshot()}
      analyticsApi={(period) => api.productionAnalytics(period)}
      operatorAnalyticsApi={(period) => api.productionOperatorAnalytics(period)}
      branchOutputApi={(period) => api.productionBranchOutput(period)}
      deliveryPlanQtyApi={(period) => api.deliveryPlanQty({ days: period })}
      poLateTrackingSummaryApi={() => api.poLateTrackingSummary()}
    />
  );
}

export default function ProductionDashboard() {
  return <RoutedProductionDashboard />;
}
