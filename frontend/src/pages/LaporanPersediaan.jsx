import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowLeft, Columns3, Download, Printer, RefreshCw,
  Save, Settings2, ZoomIn, ZoomOut, StretchHorizontal,
} from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import ReportWorkspaceShell from "../components/reportWorkspace/ReportWorkspaceShell";
import { normalizeReportColumns } from "../components/reportWorkspace/ReportColumnSettings";
import "./LaporanPenjualan.css";

const format = (value) => {
  if (value === null || value === undefined || value === "") return "—";
  const raw = String(value);
  if (!/^-?\d+(\.\d+)?$/.test(raw)) return raw;
  const [integer, fractional] = raw.split(".");
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ".") + (fractional ? "," + fractional : "");
};
const month = () => {
  const now = new Date();
  const y = now.getFullYear(), m = String(now.getMonth() + 1).padStart(2, "0");
  return {
    from: y + "-" + m + "-01",
    to: y + "-" + m + "-" + String(new Date(y, now.getMonth() + 1, 0).getDate()).padStart(2, "0"),
    branch_id: "",
    warehouse_id: "",
    master_item_id: "",
    movement_type: "",
    reference_q: "",
  };
};
const errorText = (e) => e?.response?.data?.detail || e?.message || "Laporan belum bisa ditampilkan.";
const prefsKey = (user, id) => "eternal-report-layout-v1:" + String(user?.id || user?.email || "guest") + ":" + id;
const readPrefs = (key) => { try { return JSON.parse(window.localStorage.getItem(key) || "null"); } catch { return null; } };
const writePrefs = (key, value) => { try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ } };
const cloneLayout = (layout) => ({
  order: [...(layout?.order || [])],
  hidden: [...(layout?.hidden || [])],
  widths: { ...(layout?.widths || {}) },
});

function ReportTable({ report, layout, onResize, className = "", zoom = 1 }) {
  const [collapsed, setCollapsed] = useState({});
  useEffect(() => setCollapsed({}), [report]);
  const cols = report?.columns || [];
  const labelIndex = Math.max(0, cols.findIndex((c) => c.align !== "right"));
  return (
    <article className={"erw-paper " + className} style={{ ["--erw-zoom"]: zoom }}>
      <div className="erw-report-title">
        <strong>{report.company_name}</strong>
        <h2>{report.name}</h2>
        <p>{report.period_label} · {report.branch_label}</p>
        {!!report.history_status_label && (
          <p className={"erw-history-badge erw-history-" + (report.history_status || "")} role="status">
            {report.history_status_label}
          </p>
        )}
      </div>
      <div className="erw-report-scroller" role="region" tabIndex={0} aria-label="Hasil laporan dapat digeser horizontal">
        <table>
          <colgroup>{cols.map((c) => <col key={c.key} style={{ width: layout?.widths?.[c.key] || 140 }} />)}</colgroup>
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c.key} className={c.align === "right" ? "numeric" : ""} title={c.label}>
                  {c.label}
                  {onResize && (
                    <span
                      role="separator"
                      tabIndex={0}
                      className="erw-report-resize"
                      aria-label={"Atur lebar " + c.label}
                      onPointerDown={(e) => onResize(e, c.key, layout?.widths?.[c.key] || 140)}
                    />
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {!report?.rows?.length && (
              <tr><td className="erw-table-empty" colSpan={cols.length || 1}>Tidak ada mutasi untuk filter ini.</td></tr>
            )}
            {(report?.rows || []).map((row, i) => {
              if (row.kind === "group") {
                return (
                  <tr className="erw-server-group" key={"g" + i}>
                    <th colSpan={cols.length || 1}>{row.label}</th>
                  </tr>
                );
              }
              return (
                <tr key={"r" + i}>
                  {cols.map((c) => {
                    const value = row.values?.[c.key];
                    const shown = c.align === "right" ? format(value) : value ?? "—";
                    return <td key={c.key} className={c.align === "right" ? "numeric" : ""}>{shown}</td>;
                  })}
                </tr>
              );
            })}
          </tbody>
          {!!report?.rows?.length && (
            <tfoot>
              <tr>
                {cols.map((c, i) => {
                  const raw = report.totals?.[c.key];
                  return (
                    <th key={c.key} className={c.align === "right" ? "numeric" : ""}>
                      {i === labelIndex ? "TOTAL" : raw == null || raw === "" ? "" : format(raw)}
                    </th>
                  );
                })}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </article>
  );
}

function IconBtn({ label, onClick, disabled, active, children, testId }) {
  return (
    <button
      type="button"
      className={"erw-icon-btn" + (active ? " is-active" : "")}
      aria-label={label}
      title={label}
      disabled={disabled}
      aria-pressed={active ? true : undefined}
      data-testid={testId}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export default function LaporanPersediaan() {
  const { hasPerm, user } = useAuth();
  const [searchParams] = useSearchParams();
  const reportFromUrl = String(searchParams.get("report") || "kartu-stok").trim();
  const [catalog, setCatalog] = useState(null);
  const [selectedId, setSelectedId] = useState("kartu-stok");
  const [filters, setFilters] = useState(month);
  const [layout, setLayout] = useState({ order: [], hidden: [], widths: {} });
  const [result, setResult] = useState(null);
  const [printResult, setPrintResult] = useState(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [savedName, setSavedName] = useState("");
  const [popover, setPopover] = useState(null);
  const [draft, setDraft] = useState(null);
  const [zoom, setZoom] = useState(1);
  const [itemQuery, setItemQuery] = useState("");
  const [itemOptions, setItemOptions] = useState([]);
  const resizeClean = useRef(null);
  const popoverRef = useRef(null);
  const popoverAnchorRef = useRef(null);
  const [readyPrefsKey, setReadyPrefsKey] = useState("");
  const selected = useMemo(() => catalog?.reports?.find((r) => r.id === selectedId), [catalog, selectedId]);
  const definitions = useMemo(() => selected?.columns || [], [selected]);
  const definitionKey = useMemo(() => definitions.map((c) => c.key).join("|"), [definitions]);
  const saved = catalog?.memorized || [];
  const canExport = hasPerm("current_stock.export");
  const activePrefsKey = selectedId ? prefsKey(user, selectedId) : "";
  useEffect(() => {
    let active = true;
    const req = api?.stockReportCatalog?.();
    if (!req || typeof req.then !== "function") {
      setError("Katalog persediaan tidak tersedia.");
      return () => { active = false; };
    }
    req.then((data) => {
      if (!active) return;
      setCatalog(data);
      const implemented = (data?.reports || []).filter((r) => r.implemented !== false);
      const fromUrl = implemented.find((r) => r.id === reportFromUrl);
      if (fromUrl) setSelectedId(fromUrl.id);
      else if (implemented[0]) setSelectedId(implemented[0].id);
    }).catch((e) => { if (active) setError(errorText(e)); });
    return () => { active = false; };
  }, [reportFromUrl]);

  useEffect(() => {
    setReadyPrefsKey("");
    const local = activePrefsKey ? readPrefs(activePrefsKey) : null;
    const seed = local || {
      hidden: definitions.filter((c) => c && c.default === false).map((c) => c.key),
    };
    setLayout(normalizeReportColumns(definitions, seed));
    setResult(null); setPrintResult(null); setPage(1); setPopover(null); setDraft(null); setZoom(1);
    setReadyPrefsKey(activePrefsKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, definitionKey, activePrefsKey]);

  useEffect(() => {
    if (!activePrefsKey || readyPrefsKey !== activePrefsKey || !definitions.length) return;
    writePrefs(activePrefsKey, { ...layout });
  }, [layout, definitions, activePrefsKey, readyPrefsKey]);

  useEffect(() => () => { resizeClean.current?.(); }, []);

  useEffect(() => {
    if (!popover) return undefined;
    const onKey = (e) => { if (e.key === "Escape") { setPopover(null); setDraft(null); } };
    const onPointer = (e) => {
      const panel = popoverRef.current;
      const anchor = popoverAnchorRef.current;
      if (panel && !panel.contains(e.target) && (!anchor || !anchor.contains(e.target))) {
        setPopover(null); setDraft(null);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [popover]);

  useEffect(() => {
    const q = itemQuery.trim();
    if (q.length < 2) { setItemOptions([]); return undefined; }
    let active = true;
    const t = setTimeout(() => {
      const req = typeof api.masterItems === "function" ? api.masterItems({ q, limit: 20 }) : null;
      if (!req || typeof req.then !== "function") return;
      req.then((data) => {
        if (!active) return;
        const rows = data?.items || data?.rows || data || [];
        setItemOptions(Array.isArray(rows) ? rows.slice(0, 20) : []);
      }).catch(() => { if (active) setItemOptions([]); });
    }, 220);
    return () => { active = false; clearTimeout(t); };
  }, [itemQuery]);

  const currentPayload = useCallback((targetPage = 1, includeAll = false, override = {}) => {
    const activeLayout = override.layout || layout;
    const keys = activeLayout.order.filter((k) => !activeLayout.hidden.includes(k));
    const activeFilters = override.filters || filters;
    return {
      report_id: selectedId,
      date_from: activeFilters.from,
      date_to: activeFilters.to,
      master_item_id: activeFilters.master_item_id || "",
      branch_id: activeFilters.branch_id || "",
      warehouse_id: activeFilters.warehouse_id || "",
      movement_type: activeFilters.movement_type || "",
      reference_q: activeFilters.reference_q || "",
      layout: { columns: keys, widths: activeLayout.widths },
      page: targetPage,
      page_size: override.pageSize ?? pageSize,
      include_all: includeAll,
    };
  }, [layout, filters, selectedId, pageSize]);

  async function show(target = 1, override) {
    if (!selectedId) return;
    setBusy(true); setError("");
    try {
      const data = await api.stockReportShow(currentPayload(target, false, override || {}));
      setResult(data); setPage(data.page || target);
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  }
  async function exportExcel() {
    if (!selectedId) return;
    setBusy(true); setError("");
    try {
      const blob = await api.stockReportExport(currentPayload(1, true));
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "kartu-stok.xlsx";
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  }
  async function printAll() {
    if (!selectedId) return;
    setBusy(true); setError("");
    try {
      const data = await api.stockReportShow(currentPayload(1, true));
      setPrintResult(data);
      setTimeout(() => window.print(), 120);
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  }
  async function memorize(name) {
    const label = (name ?? savedName).trim();
    if (!label || !selectedId) return;
    setBusy(true); setError("");
    try {
      await api.stockReportMemorize({
        report_id: selectedId, name: label,
        layout: currentPayload().layout, description: "Tampilan Kartu Stok Eternal",
      });
      const updated = await api.stockReportCatalog();
      setCatalog(updated); setSavedName("");
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  }

  const startResize = useCallback((event, key, startWidth) => {
    event.preventDefault(); resizeClean.current?.();
    const x = event.clientX;
    const move = (e) => setLayout((old) => ({
      ...old, widths: { ...old.widths, [key]: Math.max(100, Math.min(600, startWidth + e.clientX - x)) },
    }));
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      resizeClean.current = null;
    };
    resizeClean.current = stop;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }, []);

  function openPopover(tab) {
    if (popover === tab) { setPopover(null); setDraft(null); return; }
    setDraft({
      filters: { ...filters },
      layout: cloneLayout(layout),
      savedName,
      pageSize,
      itemQuery,
    });
    setPopover(tab);
  }
  function cancelPopover() {
    setPopover(null);
    setDraft(null);
  }

  function applyPopover() {
    if (!draft) return;
    const nextFilters = { ...draft.filters };
    const nextLayout = draft.layout;
    const nextPageSize = draft.pageSize || 25;
    setFilters(nextFilters);
    setLayout(nextLayout);
    setPageSize(nextPageSize);
    setSavedName(draft.savedName || "");
    if (typeof draft.itemQuery === "string") setItemQuery(draft.itemQuery);
    setPopover(null); setDraft(null);
    if (popover === "filter") {
      show(1, { filters: nextFilters, layout: nextLayout, pageSize: nextPageSize });
    }
  }

  const movementOptions = selected?.movement_types || [];
  const movementLabels = selected?.movement_labels || {};
  const draftLayout = draft?.layout || layout;

  const settingsPanel = draft && (
    <div
      className="erw-settings-popover erw-stock-filter-popover"
      ref={popoverRef}
      role="dialog"
      aria-modal="false"
      aria-label="Pengaturan Kartu Stok"
      data-testid="erw-settings-popover"
    >
      <div className="erw-pop-tabs" role="tablist">
        {[
          ["filter", "Filter"],
          ["columns", "Kolom"],
          ["saved", "Tampilan Tersimpan"],
        ].map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={popover === id}
            className={popover === id ? "active" : ""}
            onClick={() => setPopover(id)}
            data-testid={id === "filter" ? "stock-report-filter-tab" : undefined}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="erw-pop-body" role="tabpanel">
        {popover === "filter" && (
          <div className="erw-pop-section erw-stock-filter-grid" data-testid="stock-report-filter-panel">
            <label className="erw-stock-span-2">Barang (wajib)
              <input
                type="text"
                value={draft.itemQuery ?? itemQuery}
                placeholder="Ketik kode atau nama barang"
                onChange={(e) => {
                  const v = e.target.value;
                  setItemQuery(v);
                  setDraft((d) => ({
                    ...d,
                    itemQuery: v,
                    filters: {
                      ...d.filters,
                      master_item_id: v.trim() ? d.filters.master_item_id : "",
                    },
                  }));
                }}
                data-testid="stock-report-item-query"
                autoComplete="off"
              />
            </label>
            {(itemOptions.length > 0) && (
              <ul className="erw-item-suggest erw-stock-span-2" data-testid="stock-report-item-options">
                {itemOptions.map((it) => (
                  <li key={it.id}>
                    <button
                      type="button"
                      onClick={() => {
                        const label = (it.item_code || it.code || "") + " — " + (it.item_name || it.name || "");
                        setDraft((d) => ({
                          ...d,
                          filters: { ...d.filters, master_item_id: it.id },
                          itemQuery: label,
                        }));
                        setItemQuery(label);
                        setItemOptions([]);
                      }}
                    >
                      {(it.item_code || it.code || it.id) + " — " + (it.item_name || it.name || "")}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {!!draft.filters.master_item_id && (
              <p className="erw-stock-selected erw-stock-span-2">Item terpilih · {draft.filters.master_item_id}</p>
            )}
            <label>Dari Tanggal
              <input
                type="date"
                value={draft.filters.from || ""}
                onChange={(e) => setDraft((d) => ({ ...d, filters: { ...d.filters, from: e.target.value } }))}
              />
            </label>
            <label>Sampai Tanggal
              <input
                type="date"
                value={draft.filters.to || ""}
                onChange={(e) => setDraft((d) => ({ ...d, filters: { ...d.filters, to: e.target.value } }))}
              />
            </label>
            <label>Cabang
              <select
                value={draft.filters.branch_id || ""}
                onChange={(e) => setDraft((d) => ({
                  ...d,
                  filters: { ...d.filters, branch_id: e.target.value, warehouse_id: "" },
                }))}
                data-testid="stock-report-branch"
              >
                <option value="">Default diizinkan</option>
                {(catalog?.branches || []).map((b) => (
                  <option key={b.id} value={b.id}>{b.label || b.code || b.id}</option>
                ))}
              </select>
            </label>
            <label>Gudang
              <select
                value={draft.filters.warehouse_id || ""}
                onChange={(e) => setDraft((d) => ({ ...d, filters: { ...d.filters, warehouse_id: e.target.value } }))}
                data-testid="stock-report-warehouse"
              >
                <option value="">Semua gudang di cabang</option>
                {(catalog?.warehouses || [])
                  .filter((w) => !draft.filters.branch_id || w.branch_id === draft.filters.branch_id)
                  .map((w) => (
                    <option key={w.id} value={w.id}>{w.label || w.code || w.id}</option>
                  ))}
              </select>
            </label>
            <label>Jenis Mutasi
              <select
                value={draft.filters.movement_type || ""}
                onChange={(e) => setDraft((d) => ({ ...d, filters: { ...d.filters, movement_type: e.target.value } }))}
              >
                <option value="">Semua</option>
                {movementOptions.map((m) => (
                  <option key={m} value={m}>{movementLabels[m] || m}</option>
                ))}
              </select>
            </label>
            <label>Referensi
              <input
                type="text"
                value={draft.filters.reference_q || ""}
                onChange={(e) => setDraft((d) => ({ ...d, filters: { ...d.filters, reference_q: e.target.value } }))}
                placeholder="Nomor dokumen"
              />
            </label>
            <label>Baris / halaman
              <select value={draft.pageSize} onChange={(e) => setDraft((d) => ({ ...d, pageSize: Number(e.target.value) }))}>
                {[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          </div>
        )}
        {popover === "columns" && (
          <div className="erw-pop-section">
            <p>Pilih kolom yang ditampilkan. Perubahan diterapkan setelah Terapkan.</p>
            {draftLayout.order.map((key) => {
              const col = definitions.find((c) => c.key === key);
              if (!col) return null;
              const hidden = draftLayout.hidden.includes(key);
              return (
                <div className="erw-col-row" key={key}>
                  <label>
                    <input
                      type="checkbox"
                      checked={!hidden}
                      onChange={() => setDraft((d) => {
                        const hiddenSet = new Set(d.layout.hidden);
                        if (hiddenSet.has(key)) hiddenSet.delete(key);
                        else hiddenSet.add(key);
                        return { ...d, layout: { ...d.layout, hidden: [...hiddenSet] } };
                      })}
                    />
                    {col.label}
                  </label>
                </div>
              );
            })}
          </div>
        )}
        {popover === "saved" && (
          <div className="erw-pop-section">
            <label>Nama tampilan
              <input
                className="erw-preset-name"
                value={draft.savedName || ""}
                onChange={(e) => setDraft((d) => ({ ...d, savedName: e.target.value }))}
                placeholder="Nama tampilan"
              />
            </label>
            <button type="button" onClick={() => memorize(draft.savedName)} disabled={!String(draft.savedName || "").trim() || busy}>
              Simpan Tampilan
            </button>
            <h3>Tampilan tersimpan</h3>
            {saved.map((p) => <div className="erw-saved" key={p.id}><button type="button">{p.name}</button></div>)}
            {!saved.length && <p>Belum ada tampilan tersimpan.</p>}
          </div>
        )}
      </div>
      <div className="erw-pop-actions">
        <button type="button" className="erw-btn-ghost" onClick={cancelPopover}>Batal</button>
        <button
          type="button"
          className="erw-btn-primary"
          onClick={applyPopover}
          disabled={busy || (popover === "filter" && !draft.filters.master_item_id)}
          data-testid="stock-report-apply-filter"
        >
          {popover === "filter" ? "Terapkan & Tampilkan" : "Terapkan"}
        </button>
      </div>
    </div>
  );

  const toolbar = (
    <div className="erw-icon-toolbar" ref={popoverAnchorRef} role="toolbar" aria-label="Aksi Kartu Stok">
      <Link to="/laporan?category=persediaan" className="erw-icon-btn" aria-label="Kembali ke katalog" title="Kembali ke katalog">
        <ArrowLeft size={16} />
      </Link>
      <IconBtn label="Parameter Laporan" active={popover === "filter"} onClick={() => openPopover("filter")} testId="stock-report-filter">
        <Settings2 size={16} />
      </IconBtn>
      <IconBtn label="Modifikasi Kolom" active={popover === "columns"} onClick={() => openPopover("columns")} testId="stock-report-columns">
        <Columns3 size={16} />
      </IconBtn>
      <IconBtn label="Tampilkan" onClick={() => show(1)} disabled={busy || !filters.master_item_id} testId="stock-report-show">
        <RefreshCw size={16} />
      </IconBtn>
      <span className="erw-icon-sep" aria-hidden="true" />
      <IconBtn label="Cetak" onClick={printAll} disabled={busy || !result}><Printer size={16} /></IconBtn>
      <IconBtn label="Ekspor Excel" onClick={exportExcel} disabled={busy || !canExport || !result} testId="stock-report-export">
        <Download size={16} />
      </IconBtn>
      <IconBtn label="Zoom in" onClick={() => setZoom((z) => Math.min(1.4, +(z + 0.1).toFixed(1)))}><ZoomIn size={16} /></IconBtn>
      <IconBtn label="Zoom out" onClick={() => setZoom((z) => Math.max(0.7, +(z - 0.1).toFixed(1)))}><ZoomOut size={16} /></IconBtn>
      <IconBtn label="Reset zoom" onClick={() => setZoom(1)}><StretchHorizontal size={16} /></IconBtn>
      <IconBtn label="Simpan tampilan" active={popover === "saved"} onClick={() => openPopover("saved")}><Save size={16} /></IconBtn>
      {settingsPanel}
    </div>
  );

  return (
    <div className="erw-report-page is-viewer" data-testid="laporan-persediaan">
    <ReportWorkspaceShell
      title="Kartu Stok / Mutasi Barang"
      reports={catalog?.reports?.filter((r) => r.implemented !== false) || []}
      selectedId={selectedId}
      onSelect={setSelectedId}
      filters={filters}
      loading={busy}
      error={error}
      viewerMode
      toolbar={toolbar}
    >
      {!result && (
        <div className="erw-empty-hint">
          <p>Pilih barang, cabang/gudang, dan periode lewat Filter, lalu Terapkan &amp; Tampilkan.</p>
          <p className="erw-help">Saldo berasal dari physical_stock_ledger R2. Tidak ada nilai Rupiah.</p>
        </div>
      )}

      {!!result && (
        <>
          {!!result.gap && (
            <div className="erw-report-warning" role="status">
              <p className="erw-report-warning-summary">{result.gap}</p>
              {(result.gap_detail || result.methodology) && (
                <details className="erw-report-warning-detail">
                  <summary>Lihat detail perhitungan</summary>
                  {!!result.gap_detail && <p>{result.gap_detail}</p>}
                  {!!result.methodology && (
                    <p>{Object.values(result.methodology).filter(Boolean).join(". ")}</p>
                  )}
                </details>
              )}
            </div>
          )}
          <div className="erw-viewer-stage">
            <ReportTable report={result} layout={layout} onResize={startResize} zoom={zoom} />
          </div>
          <div className="erw-report-pager">
            <span>Total qty dihitung dari seluruh hasil filter, bukan hanya halaman ini. Saldo tidak dijumlah lintas lokasi.</span>
            <button type="button" disabled={busy || page <= 1} onClick={() => show(page - 1)}>Sebelumnya</button>
            <span>Halaman {page} dari {result.page_count || 1}</span>
            <button type="button" disabled={busy || page >= (result.page_count || 1)} onClick={() => show(page + 1)}>Berikutnya</button>
          </div>
        </>
      )}

      {printResult && (
        <div className="erw-print-only">
          <ReportTable report={printResult} layout={layout} zoom={1} />
        </div>
      )}
    </ReportWorkspaceShell>
    </div>
  );
}
