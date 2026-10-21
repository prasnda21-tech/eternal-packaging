import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowLeft, Columns3, Download, ListTree, Printer, RefreshCw,
  Save, Settings2, ZoomIn, ZoomOut, StretchHorizontal,
} from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import ReportWorkspaceShell from "../components/reportWorkspace/ReportWorkspaceShell";
import { normalizeReportColumns, moveReportColumn } from "../components/reportWorkspace/ReportColumnSettings";
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
  return { from: y + "-" + m + "-01", to: y + "-" + m + "-" + String(new Date(y, now.getMonth()+1, 0).getDate()).padStart(2, "0"), branch: "semua" };
};
const errorText = (e) => e?.response?.data?.detail || e?.message || "Laporan belum bisa ditampilkan.";
const prefsKey = (user, id) => "eternal-report-layout-v1:" + String(user?.id || user?.email || "guest") + ":" + id;
const readPrefs = (key) => { try { return JSON.parse(window.localStorage.getItem(key) || "null"); } catch { return null; } };
const writePrefs = (key, value) => { try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ } };
const cloneLayout = (layout) => ({
  order: [...(layout?.order || [])],
  hidden: [...(layout?.hidden || [])],
  widths: { ...(layout?.widths || {}) },
});

function ReportTable({ report, layout, onResize, className = "", zoom = 1 }) {
  const [collapsed,setCollapsed] = useState({});
  useEffect(() => setCollapsed({}), [report]);
  const cols = report?.columns || [];
  const labelIndex = Math.max(0, cols.findIndex((c) => c.align !== "right"));
  return <article className={"erw-paper " + className} style={{ ["--erw-zoom"]: zoom }}>
    <div className="erw-report-title">
      <strong>{report.company_name}</strong>
      <h2>{report.name}</h2>
      <p>{report.period_label} · {report.branch_label}</p>
    </div>
    <div className="erw-report-scroller" role="region" tabIndex={0} aria-label="Hasil laporan dapat digeser horizontal">
      <table>
        <colgroup>{cols.map((c) => <col key={c.key} style={{width:layout?.widths?.[c.key] || 150}}/>)}</colgroup>
        <thead><tr>{cols.map((c) => <th key={c.key} className={c.align === "right" ? "numeric" : ""} title={c.label}>
          {c.label}
          {onResize && <span role="separator" tabIndex={0} className="erw-report-resize" aria-label={"Atur lebar " + c.label}
            onPointerDown={(e) => onResize(e,c.key,layout?.widths?.[c.key] || 150)} />}
        </th>)}</tr></thead>
        <tbody>
          {!report?.rows?.length && <tr><td className="erw-table-empty" colSpan={cols.length || 1}>Tidak ada hasil untuk periode dan cabang ini.</td></tr>}
          {(() => {
            let hiddenBelowLevel = null;
            return (report?.rows || []).map((row,i) => {
              if (row.kind === "group") {
                const level = Number(row.level) || 0;
                if (hiddenBelowLevel !== null && level > hiddenBelowLevel) return null;
                if (hiddenBelowLevel !== null && level <= hiddenBelowLevel) hiddenBelowLevel = null;
                const folded = !!collapsed[i];
                if (folded) hiddenBelowLevel = level;
                return <tr className="erw-server-group" key={"group"+i}><th colSpan={cols.length || 1}>
                  {onResize ? <button type="button" onClick={() => setCollapsed(old => ({...old,[i]:!old[i]}))}
                    aria-expanded={!folded} aria-label={(folded ? "Buka " : "Tutup ") + row.label}>
                    <span aria-hidden="true">{folded ? "▸" : "▾"}</span> {row.label}
                  </button> : row.label}
                </th></tr>;
              }
              if (hiddenBelowLevel !== null) return null;
              return <tr key={"row"+i} className={row.kind === "subtotal" ? "erw-server-subtotal" : ""}>
              {cols.map((c) => {
                const value = row.values?.[c.key];
                const shown = c.align === "right" ? format(value) : value ?? "—";
                return <td key={c.key} className={c.align === "right" ? "numeric" : ""}>
                  {c.key === "nomor" && row.drill?.href ? <Link to={row.drill.href}>{shown}</Link> : shown}
                </td>;
              })}
            </tr>;
            });
          })()}
        </tbody>
        {!!report?.rows?.length && <tfoot><tr>{cols.map((c,i) => {
          const raw = report.totals?.[c.key];
          return <th key={c.key} className={c.align === "right" ? "numeric" : ""}>
            {i===labelIndex ? "TOTAL" : raw == null || raw === "" ? "" : c.align === "right" ? format(raw) : raw}
          </th>;
        })}</tr></tfoot>}
      </table>
    </div>
  </article>;
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

export default function LaporanPenjualan() {
  const { hasPerm, user } = useAuth();
  const [searchParams] = useSearchParams();
  const reportFromUrl = String(searchParams.get("report") || "").trim();
  const memorizeFromUrl = String(searchParams.get("memorize") || "").trim();
  const [catalog,setCatalog] = useState(null);
  const [selectedId,setSelectedId] = useState("");
  const [filters,setFilters] = useState(month);
  const urlMemorizeApplied = useRef("");
  const [layout,setLayout] = useState({order:[],hidden:[],widths:{}});
  const [analysisRow,setAnalysisRow] = useState("nama_barang");
  const [groupCurrency,setGroupCurrency] = useState(false);
  const [result,setResult] = useState(null);
  const [printResult,setPrintResult] = useState(null);
  const [page,setPage] = useState(1);
  const [pageSize,setPageSize] = useState(25);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  const [savedName,setSavedName] = useState("");
  const [popover,setPopover] = useState(null); // 'filter' | 'columns' | 'group' | 'saved' | null
  const [draft,setDraft] = useState(null);
  const [zoom,setZoom] = useState(1);
  const resizeClean = useRef(null);
  const presetPending = useRef(null);
  const popoverRef = useRef(null);
  const popoverAnchorRef = useRef(null);
  const [readyPrefsKey,setReadyPrefsKey] = useState("");
  const selected = useMemo(() => catalog?.reports?.find(r => r.id === selectedId),[catalog,selectedId]);
  const definitions = useMemo(() => selected?.columns || [],[selected]);
  // Stable key so catalog refresh (e.g. after Simpan Tampilan) does not wipe results.
  const definitionKey = useMemo(() => definitions.map((c) => c.key).join("|"), [definitions]);
  const saved = catalog?.memorized || [];
  const canExport = hasPerm("reports.sales.export"); // Backend independently enforces export permission.
  const activePrefsKey = selectedId ? prefsKey(user,selectedId) : "";
  const viewerMode = !!result;
  const reports = catalog?.reports?.filter(r=>r.implemented!==false) || [];

  useEffect(() => {
    let active=true;
    api.salesReportCatalog().then(data => {
      if(!active) return;
      setCatalog(data);
      const allowed=data?.branches || [];
      setFilters(prev => ({...prev,branch:allowed.some(x=>x.id===prev.branch)?prev.branch:(allowed[0]?.id || "semua")}));
      const implemented=(data?.reports || []).filter(r=>r.implemented!==false);
      const fromUrl=implemented.find(r=>r.id===reportFromUrl);
      const first=fromUrl || implemented[0];
      if(first) setSelectedId(first.id);
    }).catch(e=>{if(active)setError(errorText(e))});
    return ()=>{active=false};
  }, [reportFromUrl]);

  useEffect(() => {
    setReadyPrefsKey("");
    const pending=presetPending.current;
    presetPending.current=null;
    const local=activePrefsKey ? readPrefs(activePrefsKey) : null;
    if(pending?.reportId === selectedId) {
      setLayout(normalizeReportColumns(definitions,{
        order: pending.layout?.columns || [],
        hidden: definitions.map(c=>c.key).filter(k=>!pending.layout?.columns?.includes(k)),
        widths:pending.layout?.widths || {}
      }));
      setAnalysisRow(pending.layout?.analysis_row || "nama_barang");
      setGroupCurrency(!!pending.layout?.group_currency);
    } else {
      const seed = local || {
        hidden: definitions.filter((c) => c && c.default === false).map((c) => c.key),
      };
      setLayout(normalizeReportColumns(definitions, seed));
      setAnalysisRow(local?.analysis_row || "nama_barang");setGroupCurrency(!!local?.group_currency);
    }
    setResult(null);setPrintResult(null);setPage(1);setPopover(null);setDraft(null);setZoom(1);
    setReadyPrefsKey(activePrefsKey);
    // definitionKey tracks column-key identity; avoid reset on catalog object churn.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- definitions intentionally keyed via definitionKey
  },[selectedId,definitionKey,activePrefsKey]);
  useEffect(() => {
    if (!activePrefsKey || readyPrefsKey !== activePrefsKey || !definitions.length) return;
    writePrefs(activePrefsKey,{...layout, analysis_row:analysisRow, group_currency:groupCurrency});
  },[layout,analysisRow,groupCurrency,definitions,activePrefsKey,readyPrefsKey]);
  useEffect(() => ()=>{resizeClean.current?.()},[]);

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

  const currentPayload = useCallback((targetPage=1,includeAll=false,override={}) => {
    const activeLayout = override.layout || layout;
    const keys=activeLayout.order.filter(k=>!activeLayout.hidden.includes(k));
    const activeFilters = override.filters || filters;
    const activeAnalysis = override.analysisRow ?? analysisRow;
    const activeGroupCurrency = override.groupCurrency ?? groupCurrency;
    return {report_id:selectedId,date_from:activeFilters.from,date_to:activeFilters.to,
      branch:activeFilters.branch,layout:{columns:keys,
        ...(selectedId==="penjualan-per-barang" ? {analysis_row:activeAnalysis}:{}),
        ...(selectedId==="rincian-penjualan-per-barang" ? {group_currency:activeGroupCurrency}:{}),
        widths:activeLayout.widths},
      page:targetPage,page_size:override.pageSize ?? pageSize,include_all:includeAll};
  },[layout,filters,selectedId,pageSize,analysisRow,groupCurrency]);

  async function show(target=1, override) {
    if(!selectedId) return;
    setBusy(true);setError("");
    try { const data=await api.salesReportShow(currentPayload(target,false,override||{}));setResult(data);setPage(data.page||target); }
    catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  async function exportExcel() {
    if(!selectedId)return;setBusy(true);setError("");
    try {const blob=await api.salesReportExport(currentPayload(1,true));
      const url=URL.createObjectURL(blob);const a=document.createElement("a");
      a.href=url;a.download=selectedId+".xlsx";document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
    }catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  async function printAll() {
    if(!selectedId)return;setBusy(true);setError("");
    try {const data=await api.salesReportShow(currentPayload(1,true));setPrintResult(data);
      setTimeout(()=>window.print(),120);
    }catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  async function memorize(name) {
    const label=(name ?? savedName).trim();
    if(!label||!selectedId)return;setBusy(true);setError("");
    try{await api.salesReportMemorize({report_id:selectedId,name:label,
      layout:currentPayload().layout,description:"Tampilan laporan Eternal"});
      const updated=await api.salesReportCatalog();setCatalog(updated);setSavedName("");
      if (draft) setDraft((d) => d ? { ...d, savedName: "" } : d);
    }catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  function loadPreset(p) {
    if(!p?.report_id)return;
    const spec=catalog?.reports?.find(r=>r.id===p.report_id);if(!spec)return;
    if(p.report_id !== selectedId) {
      presetPending.current={reportId:p.report_id,layout:p.layout};
      setSelectedId(p.report_id);
      setPopover(null);setDraft(null);
      return;
    }
    setLayout(normalizeReportColumns(spec.columns || [],{
      order:p.layout?.columns || [],hidden:(spec.columns||[]).map(c=>c.key).filter(k=>!p.layout?.columns?.includes(k)),
      widths:p.layout?.widths || {}
    }));
    setAnalysisRow(p.layout?.analysis_row || "nama_barang");
    setGroupCurrency(!!p.layout?.group_currency);
    setResult(null);setPage(1);setPopover(null);setDraft(null);
  }
  useEffect(() => {
    if (!catalog || !memorizeFromUrl || urlMemorizeApplied.current === memorizeFromUrl) return;
    const preset = (catalog.memorized || []).find((m) => String(m.id) === memorizeFromUrl);
    if (!preset) return;
    urlMemorizeApplied.current = memorizeFromUrl;
    loadPreset(preset);
    // One-shot hydrate from ?memorize=; loadPreset closes over latest catalog/selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, memorizeFromUrl, selectedId]);
  const startResize=useCallback((event,key,startWidth)=>{
    event.preventDefault();resizeClean.current?.();
    const x=event.clientX;const move=(e)=>setLayout(old=>({...old,widths:{...old.widths,[key]:Math.max(100,Math.min(600,startWidth+e.clientX-x))}}));
    const stop=()=>{window.removeEventListener("pointermove",move);window.removeEventListener("pointerup",stop);resizeClean.current=null;};
    resizeClean.current=stop;window.addEventListener("pointermove",move);window.addEventListener("pointerup",stop);
  },[]);

  function openPopover(tab) {
    if (popover === tab) { setPopover(null); setDraft(null); return; }
    setDraft({
      filters: { from: filters.from, to: filters.to, branch: filters.branch },
      layout: cloneLayout(layout),
      analysisRow,
      groupCurrency,
      savedName,
      pageSize,
    });
    setPopover(tab);
  }
  function cancelPopover() {
    setPopover(null);
    setDraft(null);
  }
  async function applyPopover() {
    if (!draft) { cancelPopover(); return; }
    const nextFilters = {
      ...filters,
      from: draft.filters.from,
      to: draft.filters.to,
      branch: draft.filters.branch,
    };
    const nextLayout = cloneLayout(draft.layout);
    const nextAnalysis = draft.analysisRow;
    const nextGroupCurrency = draft.groupCurrency;
    const nextPageSize = draft.pageSize || pageSize;
    setFilters(nextFilters);
    setLayout(nextLayout);
    setAnalysisRow(nextAnalysis);
    setGroupCurrency(nextGroupCurrency);
    setPageSize(nextPageSize);
    setSavedName(draft.savedName || "");
    setPopover(null);
    setDraft(null);
    await show(1, {
      filters: nextFilters,
      layout: nextLayout,
      analysisRow: nextAnalysis,
      groupCurrency: nextGroupCurrency,
      pageSize: nextPageSize,
    });
  }

  function updateDraftLayout(updater) {
    setDraft((d) => d ? { ...d, layout: typeof updater === "function" ? updater(d.layout) : updater } : d);
  }

  const draftLayout = draft?.layout || layout;
  const settingsPanel = draft && (
    <div
      className="erw-settings-popover"
      ref={popoverRef}
      role="dialog"
      aria-modal="false"
      aria-label="Pengaturan laporan"
      data-testid="erw-settings-popover"
    >
      <div className="erw-pop-tabs" role="tablist">
        {[
          ["filter", "Filter"],
          ["columns", "Kolom"],
          ["group", "Pengelompokan"],
          ["saved", "Tampilan Tersimpan"],
        ].map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={popover === id}
            className={popover === id ? "active" : ""}
            onClick={() => setPopover(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="erw-pop-body" role="tabpanel">
        {popover === "filter" && (
          <div className="erw-pop-section">
            <label>Dari Tanggal
              <input type="date" value={draft.filters.from || ""} onChange={(e)=>setDraft((d)=>({...d,filters:{...d.filters,from:e.target.value}}))}/>
            </label>
            <label>Sampai Tanggal
              <input type="date" value={draft.filters.to || ""} onChange={(e)=>setDraft((d)=>({...d,filters:{...d.filters,to:e.target.value}}))}/>
            </label>
            <label>Cabang
              <select value={draft.filters.branch || "semua"} onChange={(e)=>setDraft((d)=>({...d,filters:{...d.filters,branch:e.target.value}}))}>
                {(catalog?.branches || [{id:"semua",label:"Semua Cabang"}]).map((b)=>(
                  <option key={b.id} value={b.id}>{b.label}</option>
                ))}
              </select>
            </label>
            <label>Baris per halaman
              <select value={draft.pageSize} onChange={(e)=>setDraft((d)=>({...d,pageSize:Number(e.target.value)}))}>
                {[10,25,50,100].map((n)=><option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          </div>
        )}
        {popover === "columns" && (
          <div className="erw-pop-section">
            <p>Pilih, susun ulang, dan atur lebar kolom. Perubahan diterapkan setelah Terapkan.</p>
            {draftLayout.order.map((key,i)=>{
              const def=definitions.find(c=>c.key===key);if(!def)return null;
              return <div className="erw-col-row" key={key}>
                <label><input type="checkbox" checked={!draftLayout.hidden.includes(key)}
                  onChange={()=>updateDraftLayout((old)=>{
                    const shown=old.order.filter(k=>!old.hidden.includes(k));
                    if(shown.length===1 && shown[0]===key)return old;
                    return {...old,hidden:old.hidden.includes(key)?old.hidden.filter(k=>k!==key):[...old.hidden,key]};
                  })}/>{def.label}</label>
                <button type="button" aria-label={"Naikkan "+def.label} disabled={i===0}
                  onClick={()=>updateDraftLayout((old)=>({...old,order:moveReportColumn(old.order,key,old.order[i-1])}))}>↑</button>
                <button type="button" aria-label={"Turunkan "+def.label} disabled={i===draftLayout.order.length-1}
                  onClick={()=>updateDraftLayout((old)=>({...old,order:moveReportColumn(old.order,old.order[i+1],key)}))}>↓</button>
              </div>;
            })}
            <button type="button" onClick={()=>updateDraftLayout(normalizeReportColumns(definitions))}>Reset Kolom</button>
          </div>
        )}
        {popover === "group" && (
          <div className="erw-pop-section">
            {selectedId==="penjualan-per-barang" ? <label className="erw-setting-select">Analisa berdasarkan
              <select value={draft.analysisRow} onChange={(e)=>setDraft((d)=>({...d,analysisRow:e.target.value}))}>
                <option value="nama_barang">Nama Barang</option>
                <option value="nama_kategori_barang">Kategori Barang</option>
              </select>
            </label> : selectedId==="rincian-penjualan-per-barang" ? <label className="erw-setting-check">
              <input type="checkbox" checked={draft.groupCurrency} onChange={(e)=>setDraft((d)=>({...d,groupCurrency:e.target.checked}))}/>
              Kelompokkan menurut Mata Uang
            </label> : <p>Pengelompokan tidak tersedia untuk laporan ini.</p>}
          </div>
        )}
        {popover === "saved" && (
          <div className="erw-pop-section">
            <label>Nama tampilan
              <input className="erw-preset-name" placeholder="Nama tampilan" aria-label="Nama tampilan"
                value={draft.savedName} onChange={(e)=>setDraft((d)=>({...d,savedName:e.target.value}))}/>
            </label>
            <button type="button" onClick={()=>memorize(draft.savedName)} disabled={!draft.savedName.trim()||busy}>Simpan Tampilan</button>
            <h3>Tampilan tersimpan</h3>
            {saved.filter(p=>p.report_id===selectedId).map(p=><div className="erw-saved" key={p.id}>
              <button type="button" onClick={()=>loadPreset(p)}>{p.name}</button>
            </div>)}
            {!saved.filter(p=>p.report_id===selectedId).length && <p>Belum ada tampilan tersimpan.</p>}
          </div>
        )}
      </div>
      <div className="erw-pop-actions">
        <button type="button" className="erw-btn-ghost" onClick={cancelPopover}>Batal</button>
        <button type="button" className="erw-btn-primary" onClick={applyPopover} disabled={busy}>Terapkan</button>
      </div>
    </div>
  );

  const toolbar = (
    <div className="erw-icon-toolbar" ref={popoverAnchorRef} role="toolbar" aria-label="Aksi laporan">
      <IconBtn label="Kembali ke Daftar Laporan" testId="erw-back" onClick={()=>{setResult(null);setPrintResult(null);setPopover(null);setDraft(null);setZoom(1);}}>
        <ArrowLeft size={16} strokeWidth={2} aria-hidden="true" />
      </IconBtn>
      <span className="erw-icon-sep" aria-hidden="true" />
      <IconBtn label="Parameter Laporan" testId="erw-params" active={popover==="filter"} onClick={()=>openPopover("filter")}>
        <Settings2 size={16} strokeWidth={2} aria-hidden="true" />
      </IconBtn>
      <IconBtn label="Modifikasi Kolom" testId="erw-columns" active={popover==="columns"} onClick={()=>openPopover("columns")}>
        <Columns3 size={16} strokeWidth={2} aria-hidden="true" />
      </IconBtn>
      <IconBtn label="Pengelompokan" testId="erw-group" active={popover==="group"} onClick={()=>openPopover("group")}>
        <ListTree size={16} strokeWidth={2} aria-hidden="true" />
      </IconBtn>
      <IconBtn label="Refresh" testId="erw-refresh" disabled={busy} onClick={()=>show(page)}>
        <RefreshCw size={16} strokeWidth={2} aria-hidden="true" />
      </IconBtn>
      <span className="erw-icon-sep" aria-hidden="true" />
      <IconBtn label="Cetak" testId="erw-print" disabled={!result||busy} onClick={printAll}>
        <Printer size={16} strokeWidth={2} aria-hidden="true" />
      </IconBtn>
      <IconBtn label="Ekspor Excel" testId="erw-export" disabled={!result||busy||!canExport} onClick={exportExcel}>
        <Download size={16} strokeWidth={2} aria-hidden="true" />
      </IconBtn>
      <IconBtn label="Simpan Tampilan" testId="erw-save-view" active={popover==="saved"} onClick={()=>openPopover("saved")}>
        <Save size={16} strokeWidth={2} aria-hidden="true" />
      </IconBtn>
      <span className="erw-icon-sep" aria-hidden="true" />
      <IconBtn label="Perkecil" testId="erw-zoom-out" disabled={zoom<=0.7} onClick={()=>setZoom((z)=>Math.max(0.7, Math.round((z-0.1)*10)/10))}>
        <ZoomOut size={16} strokeWidth={2} aria-hidden="true" />
      </IconBtn>
      <IconBtn label="Perbesar" testId="erw-zoom-in" disabled={zoom>=1.4} onClick={()=>setZoom((z)=>Math.min(1.4, Math.round((z+0.1)*10)/10))}>
        <ZoomIn size={16} strokeWidth={2} aria-hidden="true" />
      </IconBtn>
      <IconBtn label="Fit Width" testId="erw-fit-width" active={zoom===1} onClick={()=>setZoom(1)}>
        <StretchHorizontal size={16} strokeWidth={2} aria-hidden="true" />
      </IconBtn>
      {settingsPanel}
    </div>
  );

  return <div className={"erw-report-page" + (viewerMode ? " is-viewer" : "")}>
    <ReportWorkspaceShell
      title="Laporan Penjualan"
      reports={reports}
      selectedId={selectedId}
      onSelect={setSelectedId}
      filters={{...filters,branchOptions:catalog?.branches||[]}}
      onFiltersChange={next=>setFilters(old=>({...old,from:next.from,to:next.to,branch:next.branch}))}
      loading={busy}
      onRun={()=>show(1)}
      error={error}
      viewerMode={viewerMode}
      toolbar={toolbar}
    >
      {viewerMode ? <>
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
        {!result.gap && result.methodology && (
          <details className="erw-method"><summary>Dasar perhitungan & sumber data</summary>
            <p>{Object.values(result.methodology).filter(Boolean).join(". ")}</p>
          </details>
        )}
        <div className="erw-viewer-stage">
          <ReportTable report={result} layout={layout} onResize={startResize} zoom={zoom}/>
        </div>
        <div className="erw-report-pager">
          <span>Total dihitung dari seluruh hasil filter, bukan hanya halaman ini.</span>
          <button type="button" disabled={busy||page<=1} onClick={()=>show(page-1)}>Sebelumnya</button>
          <span>Halaman {page} dari {result.page_count||1}</span>
          <button type="button" disabled={busy||page>=(result.page_count||1)} onClick={()=>show(page+1)}>Berikutnya</button>
        </div>
      </> : <div className="erw-report-empty">Pilih jenis laporan, periode, dan cabang, lalu klik <strong>Tampilkan</strong>.</div>}
    </ReportWorkspaceShell>
    <div className="erw-print-only">{printResult && <ReportTable report={printResult}/>}</div>
  </div>;
}
