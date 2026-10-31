import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../lib/auth";
import api from "../lib/api";
import { jakartaDate, periodRange, shouldApplyDashboardResponse } from "../lib/financialDashboard";
import { formatSalesInvoiceDate } from "../lib/salesInvoiceDate";
import SalesInvoiceDateField from "../components/SalesInvoiceDateField";
import {
  failedModel,
  formatRange,
  loadingModel,
  mapCash,
  mapExpense,
  mapOmzet,
  mapProducts,
  mapProfit,
  mapPurchases,
  mapReconciliations,
  mapSales,
  mapTrend,
} from "../lib/widgetDashboardModel";
import FinanceWidgetBoard, { WidgetGlyph } from "../components/financeDashboard/FinanceWidgetBoard";
import {
  FINANCE_WIDGETS,
  PENDING_WIDGETS,
  financeWidgetAllowed,
  financeWidgetMeta,
} from "../components/financeDashboard/financeWidgetCatalog";
import { addWidget, defaultLayout } from "../components/financeDashboard/financeWidgetLayout";
import "../components/financeDashboard/financeDashboard.css";

const SAVE_LABEL = {
  idle: "",
  saving: "Menyimpan",
  saved: "Tersimpan",
  error: "Gagal",
  conflict: "Konflik",
};

function periodOf(widget, globalPreset, globalRange) {
  const meta = financeWidgetMeta(widget.widget_id);
  if (!meta?.period) return null;
  const mode = widget.settings?.period_mode || "global";
  if (mode === "global") return { ...globalRange, preset: globalPreset };
  if (mode === "custom") {
    return { start: widget.settings?.start_date || "", end: widget.settings?.end_date || "", preset: "custom" };
  }
  const range = periodRange(mode);
  return { ...range, preset: mode };
}

function groupKey(range) {
  return range ? `${range.preset}|${range.start}|${range.end}` : "";
}

export default function FinanceDashboard() {
  const { hasPerm } = useAuth();
  const today = jakartaDate();
  const [preset, setPreset] = useState("month");
  const [custom, setCustom] = useState({ start: today, end: today });
  const globalRange = preset === "custom" ? custom : periodRange(preset);
  const [widgets, setWidgets] = useState([]);
  const [version, setVersion] = useState(0);
  const [editing, setEditing] = useState(false);
  const [keysHelp, setKeysHelp] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const [saveState, setSaveState] = useState("idle");
  const [notice, setNotice] = useState("");
  const [models, setModels] = useState({});
  const [moreOpen, setMoreOpen] = useState(false);
  const [catalogChip, setCatalogChip] = useState("all");
  const [reloadToken, setReloadToken] = useState(0);
  const versionRef = useRef(0);
  const dirtyRef = useRef(false);
  const conflictRef = useRef(false);
  const requestRef = useRef(0);
  const saveQueue = useRef(Promise.resolve());

  const allowedKey = FINANCE_WIDGETS.map((item) => `${item.id}:${financeWidgetAllowed(item.id, hasPerm) ? 1 : 0}`).join("|");

  function applyServer(body) {
    dirtyRef.current = false;
    conflictRef.current = false;
    versionRef.current = body.version || 0;
    setVersion(body.version || 0);
    setWidgets(Array.isArray(body.widgets) ? body.widgets : []);
    setSaveState("idle");
    const ignored = body.ignored?.length ? `${body.ignored.length} widget tersimpan tidak dikenali dan tidak ditimpa.` : "";
    const withheld = body.withheld?.length ? `${body.withheld.length} widget disembunyikan karena izin berubah.` : "";
    setNotice([ignored, withheld].filter(Boolean).join(" "));
  }

  async function loadLayout() {
    const body = await api.financeDashboardLayout();
    applyServer(body);
  }

  useEffect(() => {
    let live = true;
    api.financeDashboardLayout().then((body) => {
      if (live) applyServer(body);
    }).catch(() => {
      if (!live) return;
      setNotice("Tata letak gagal dimuat. Tata letak lokal tidak dipakai sebagai pengganti.");
      setWidgets(defaultLayout(hasPerm));
      versionRef.current = 0;
      setVersion(0);
    });
    return () => { live = false; };
    // Permission changes reload through allowedKey so a revoked card cannot keep old data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowedKey]);

  function queueSave(nextWidgets) {
    if (conflictRef.current) return;
    saveQueue.current = saveQueue.current.then(async () => {
      if (!dirtyRef.current || conflictRef.current) return;
      setSaveState("saving");
      try {
        const body = await api.saveFinanceDashboardLayout({
          version: versionRef.current,
          widgets: nextWidgets,
        });
        versionRef.current = body.version;
        setVersion(body.version);
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
    if (!dirtyRef.current || conflictRef.current) return undefined;
    const handle = setTimeout(() => queueSave(widgets), 600);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widgets]);

  function changeLayout(next) {
    if (conflictRef.current) return;
    dirtyRef.current = true;
    setWidgets(next);
  }

  function changeSettings(widgetId, settings) {
    changeLayout(widgets.map((item) => (item.widget_id === widgetId ? { ...item, settings } : item)));
  }

  const dataKey = useMemo(() => {
    const parts = widgets.map((widget) => {
      const range = periodOf(widget, preset, globalRange);
      return `${widget.widget_id}:${financeWidgetAllowed(widget.widget_id, hasPerm) ? 1 : 0}:${groupKey(range)}:${widget.settings?.visual || ""}`;
    });
    return `${reloadToken}|${parts.join("~")}`;
    // globalRange is a new object every render; preset and dates are the real inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widgets, preset, globalRange.start, globalRange.end, allowedKey, reloadToken]);

  useEffect(() => {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    const nextModels = {};
    widgets.forEach((widget) => {
      nextModels[widget.widget_id] = financeWidgetAllowed(widget.widget_id, hasPerm) ? (widget.widget_id === "active_users" ? { status: "complete" } : loadingModel()) : { status: "forbidden", detail: "Tidak ada izin." };
    });
    setModels(nextModels);

    const remember = (run) => {
      run().then((patch) => {
        if (!shouldApplyDashboardResponse(requestId, requestRef.current) || !patch) return;
        setModels((current) => ({ ...current, ...patch }));
      });
    };

    const salesGroups = new Map();
    const financeGroups = new Map();
    widgets.forEach((widget) => {
      if (!financeWidgetAllowed(widget.widget_id, hasPerm)) return;
      const range = periodOf(widget, preset, globalRange);
      if (["sales", "products", "sellers", "trend"].includes(widget.widget_id)) {
        const key = groupKey(range);
        const group = salesGroups.get(key) || { range, ids: [] };
        group.ids.push(widget.widget_id);
        salesGroups.set(key, group);
      }
      if (widget.widget_id === "cash" || widget.widget_id === "expense") {
        const key = groupKey(range);
        const group = financeGroups.get(key) || { range, ids: [] };
        group.ids.push(widget.widget_id);
        financeGroups.set(key, group);
      }
    });

    salesGroups.forEach((group) => {
      if (!group.range?.start || !group.range?.end) return;
      remember(async () => {
        try {
          const body = await api.salesDashboard({
            start_date: group.range.start,
            end_date: group.range.end,
            preset: group.range.preset,
          });
          const patch = {};
          if (group.ids.includes("sales")) {
            const sales = mapOmzet(body.omzet, formatRange(group.range.start, group.range.end));
            const trend = mapTrend(body.trend);
            if (trend.status === "complete" && trend.points.length > 1) {
              sales.series = trend.points;
              sales.grain = trend.grain;
            }
            const comparison = body.omzet?.comparison;
            if (comparison?.available && typeof comparison.percent === "number" && Number.isFinite(comparison.percent)) {
              sales.comparison = { percent: comparison.percent, direction: comparison.direction || "" };
            }
            patch.sales = sales;
          }
          if (group.ids.includes("products")) patch.products = mapProducts(body.products);
          if (group.ids.includes("sellers")) patch.sellers = mapSales(body.sales);
          if (group.ids.includes("trend")) patch.trend = mapTrend(body.trend);
          return patch;
        } catch (error) {
          const failed = failedModel(error);
          return Object.fromEntries(group.ids.map((id) => [id, failed]));
        }
      });
    });

    financeGroups.forEach((group) => {
      if (!group.range?.start || !group.range?.end) return;
      remember(async () => {
        try {
          const body = await api.financialDashboard({ start_date: group.range.start, end_date: group.range.end });
          const patch = {};
          if (group.ids.includes("cash")) patch.cash = mapCash(body.cash_movement);
          if (group.ids.includes("expense")) patch.expense = mapExpense(body.charts?.expense_composition);
          return patch;
        } catch (error) {
          const failed = failedModel(error);
          return Object.fromEntries(group.ids.map((id) => [id, failed]));
        }
      });
    });

    widgets.forEach((widget) => {
      if (!financeWidgetAllowed(widget.widget_id, hasPerm)) return;
      const range = periodOf(widget, preset, globalRange);
      if (widget.widget_id === "purchases" && range?.start && range?.end) {
        remember(async () => {
          try {
            return { purchases: mapPurchases(await api.purchasePeriodSummary({ start_date: range.start, end_date: range.end })) };
          } catch (error) {
            return { purchases: failedModel(error) };
          }
        });
      }
      if (widget.widget_id === "profit" && range?.start && range?.end) {
        const ytdStart = `${today.slice(0, 4)}-01-01`;
        remember(async () => {
          try {
            const [periodBody, ytdBody] = await Promise.all([
              api.widgetProfitLoss({ start_date: range.start, end_date: range.end }),
              api.widgetProfitLoss({ start_date: ytdStart, end_date: today }),
            ]);
            const ytdLabel = `${formatSalesInvoiceDate(ytdStart)} – ${formatSalesInvoiceDate(today)}`;
            return { profit: mapProfit(periodBody, ytdBody, ytdLabel) };
          } catch (error) {
            return { profit: failedModel(error) };
          }
        });
      }
      if (widget.widget_id === "recon") {
        remember(async () => {
          try {
            return { recon: mapReconciliations(await api.widgetReconciliations()) };
          } catch (error) {
            return { recon: failedModel(error) };
          }
        });
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataKey]);

  const needle = query.trim().toLowerCase();
  const chips = [
    { id: "all", label: "Semua" },
    { id: "Keuangan & Akuntansi", label: "Keuangan" },
    { id: "Penjualan & Pelanggan", label: "Penjualan" },
    { id: "Operasional & Persediaan", label: "Persediaan" },
    { id: "Aktivitas & Persetujuan", label: "Aktivitas" },
  ];
  const categories = chips.filter((chip) => chip.id === "all" || catalogChip === "all" || catalogChip === chip.id).map((chip) => chip.id).filter((id) => id !== "all");

  return (
    <div className="keuangan-board-page" data-testid="finance-dashboard-page">
      <header className="kb-head">
        <div className="kb-identity">
          <h1>Dashboard Keuangan</h1>
          <p>Ringkasan kondisi keuangan sesuai akses Anda</p>
        </div>
        <div className="kb-toolbar">
          <div className="kb-period">
            <button type="button" className={`kb-btn${preset === "today" ? " is-on" : ""}`} onClick={() => setPreset("today")}>Hari ini</button>
            <button type="button" className={`kb-btn${preset === "month" ? " is-on" : ""}`} onClick={() => setPreset("month")}>Bulan ini</button>
            <button type="button" className={`kb-btn${preset === "year" ? " is-on" : ""}`} onClick={() => setPreset("year")}>Tahun ini</button>
            <button type="button" className={`kb-btn${preset === "custom" ? " is-on" : ""}`} onClick={() => { if (preset !== "custom") setCustom({ start: globalRange.start, end: globalRange.end }); setPreset("custom"); }}>Periode kustom</button>
            <div className="kb-dates">
              <SalesInvoiceDateField
                valueIso={preset === "custom" ? custom.start : globalRange.start}
                onChange={(iso) => {
                  const base = preset === "custom" ? custom : globalRange;
                  setCustom({ ...base, start: iso });
                  setPreset("custom");
                }}
                calendarLabel="Kalender tanggal awal"
                testId="finance-period-start"
              />
              <span className="kb-sep">s.d.</span>
              <SalesInvoiceDateField
                valueIso={preset === "custom" ? custom.end : globalRange.end}
                onChange={(iso) => {
                  const base = preset === "custom" ? custom : globalRange;
                  setCustom({ ...base, end: iso });
                  setPreset("custom");
                }}
                calendarLabel="Kalender tanggal akhir"
                testId="finance-period-end"
              />
            </div>
          </div>
          <div className="kb-actions">
            <span className={`kb-save is-${saveState}`} data-testid="finance-save-state">{SAVE_LABEL[saveState]}</span>
            <button type="button" className="kb-btn is-primary" data-testid="finance-add-widget" onClick={() => setCatalogOpen(true)} disabled={conflictRef.current && saveState === "conflict"}>+ Tambah Widget</button>
            <button type="button" className={`kb-btn${editing ? " is-primary" : ""}`} data-testid="finance-edit-layout" onClick={() => setEditing((value) => !value)}>{editing ? "Selesai" : "Atur Tata Letak"}</button>
            <div className="kb-more">
              <button type="button" className="kb-icon-btn" aria-label="Tindakan lain" aria-expanded={moreOpen} data-testid="finance-more" onClick={() => setMoreOpen((value) => !value)}>⋯</button>
              {moreOpen ? (
                <div className="kb-pop" role="menu">
                  <button type="button" data-testid="finance-reset-layout" onClick={() => { setMoreOpen(false); setConfirmReset(true); }}>Reset Tata Letak</button>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </header>
      {editing ? (
        <div className="kb-edit-note">
          <p>Mode ubah tata letak aktif. Seret gagang kartu, atau tarik sudut kanan bawah.</p>
          <button type="button" className="kb-btn" aria-expanded={keysHelp} aria-controls="finance-keys-help" onClick={() => setKeysHelp((value) => !value)}>Bantuan papan ketik</button>
          {keysHelp ? (
            <p id="finance-keys-help" className="kb-keys-help">Buka menu titik tiga pada kartu. Tombol geser dan ubah ukuran ada di dalam menu itu, dan dapat dijangkau dengan keyboard.</p>
          ) : null}
        </div>
      ) : null}
      {notice ? <p className="kb-banner" data-testid="finance-notice">{notice}</p> : null}
      {saveState === "conflict" ? (
        <p className="kb-banner">
          <button type="button" className="kb-btn" data-testid="finance-reload-layout" onClick={() => loadLayout().catch(() => setNotice("Gagal memuat ulang tata letak."))}>Muat tata letak server</button>
        </p>
      ) : null}
      {confirmReset ? (
        <div className="kb-confirm">
          <p>Reset menghapus tata letak pribadi dan mengembalikan susunan awal sesuai izin Anda.</p>
          <button type="button" className="kb-btn is-primary" data-testid="finance-reset-confirm" onClick={async () => {
            const body = await api.resetFinanceDashboardLayout();
            applyServer(body);
            setConfirmReset(false);
            setEditing(false);
          }}>Reset</button>
          <button type="button" className="kb-btn" onClick={() => setConfirmReset(false)}>Batal</button>
        </div>
      ) : null}
      <div className="kb-scroll">
        <FinanceWidgetBoard
          widgets={widgets}
          models={models}
          editing={editing}
          hasPerm={hasPerm}
          globalPreset={preset}
          periodForWidget={(widget) => periodOf(widget, preset, globalRange)}
          onLayout={changeLayout}
          onSettings={changeSettings}
          onRetry={() => setReloadToken((value) => value + 1)}
        />
      </div>
      {catalogOpen ? (
        <div className="kb-overlay" onMouseDown={() => setCatalogOpen(false)}>
          <aside className="kb-drawer" data-testid="finance-catalog" onMouseDown={(event) => event.stopPropagation()}>
            <header className="kb-drawer-head">
              <h2>Tambah Widget</h2>
              <button type="button" className="kb-drawer-close" aria-label="Tutup" onClick={() => setCatalogOpen(false)}>×</button>
            </header>
            <div className="kb-drawer-body">
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Cari widget..." data-testid="finance-catalog-search" />
              <div className="kb-chips">
                {chips.map((chip) => (
                  <button type="button" key={chip.id} className={`kb-btn${catalogChip === chip.id ? " is-on" : ""}`} onClick={() => setCatalogChip(chip.id)}>{chip.label}</button>
                ))}
              </div>
              {(() => {
                const catalogItems = categories.flatMap((category) => {
                  const ready = FINANCE_WIDGETS.filter((item) => item.category === category && (!needle || item.title.toLowerCase().includes(needle)));
                  const pending = PENDING_WIDGETS.filter((item) => item.category === category && (!needle || item.title.toLowerCase().includes(needle)));
                  return [...ready.map((item) => {
                    const permitted = financeWidgetAllowed(item.id, hasPerm);
                    const placed = widgets.some((widget) => widget.widget_id === item.id);
                    const canAdd = permitted && !placed && saveState !== "conflict";
                    return (
                      <article key={item.id} className="kb-option" data-testid={`finance-catalog-${item.id}`}>
                        <WidgetGlyph id={item.id} />
                        <div>
                          <strong>{item.title}</strong>
                          <p>{item.summary}</p>
                        </div>
                        {canAdd ? null : <span className="kb-badge is-off">{!permitted ? "Tidak memiliki akses" : "Terpasang"}</span>}
                        {canAdd ? (
                          <button
                            type="button"
                            className="kb-plus"
                            aria-label={`Tambahkan ${item.title}`}
                            onClick={() => changeLayout(addWidget(widgets, item.id))}
                          >
                            Tambah
                          </button>
                        ) : null}
                      </article>
                    );
                  }), ...pending.map((item) => (
                    <article key={item.id} className="kb-option" data-testid={`finance-catalog-${item.id}`}>
                      <WidgetGlyph id={item.id} />
                      <div>
                        <strong>{item.title}</strong>
                        <p>{item.summary}</p>
                      </div>
                      <span className="kb-badge is-off">Belum tersedia</span>
                    </article>
                  ))];
                });
                return catalogItems.length ? catalogItems : <p className="kb-empty-search" data-testid="finance-catalog-empty">Tidak ada widget yang cocok.</p>;
              })()}
            </div>
          </aside>
        </div>
      ) : null}
    </div>
  );
}

