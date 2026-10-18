import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
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

function ReportTable({ report, layout, onResize, className = "" }) {
  const cols = report?.columns || [];
  const labelIndex = Math.max(0, cols.findIndex((c) => c.align !== "right"));
  return <article className={"erw-paper " + className}>
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
          {(report?.rows || []).map((row,i) => {
            if (row.kind === "group") return <tr className="erw-server-group" key={"group"+i}><th colSpan={cols.length || 1}>{row.label}</th></tr>;
            return <tr key={"row"+i} className={row.kind === "subtotal" ? "erw-server-subtotal" : ""}>
              {cols.map((c) => {
                const value = row.values?.[c.key];
                const shown = c.align === "right" ? format(value) : value ?? "—";
                return <td key={c.key} className={c.align === "right" ? "numeric" : ""}>
                  {c.key === "nomor" && row.drill?.href ? <Link to={row.drill.href}>{shown}</Link> : shown}
                </td>;
              })}
            </tr>;
          })}
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

export default function LaporanPenjualan() {
  const { hasPerm } = useAuth();
  const [catalog,setCatalog] = useState(null);
  const [selectedId,setSelectedId] = useState("");
  const [filters,setFilters] = useState(month);
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
  const resizeClean = useRef(null);
  const presetPending = useRef(null);
  const selected = useMemo(() => catalog?.reports?.find(r => r.id === selectedId),[catalog,selectedId]);
  const definitions = useMemo(() => selected?.columns || [],[selected]);
  const saved = catalog?.memorized || [];
  const canExport = hasPerm("reports.sales.export"); // Backend independently enforces export permission.

  useEffect(() => {
    let active=true;
    api.salesReportCatalog().then(data => {
      if(!active) return;
      setCatalog(data);
      const allowed=data?.branches || [];
      setFilters(prev => ({...prev,branch:allowed.some(x=>x.id===prev.branch)?prev.branch:(allowed[0]?.id || "semua")}));
      const first=(data?.reports || []).find(r=>r.implemented!==false);
      if(first) setSelectedId(first.id);
    }).catch(e=>{if(active)setError(errorText(e))});
    return ()=>{active=false};
  }, []);
  useEffect(() => {
    const pending=presetPending.current;
    presetPending.current=null;
    if(pending?.reportId === selectedId) {
      setLayout(normalizeReportColumns(definitions,{
        order: pending.layout?.columns || [],
        hidden: definitions.map(c=>c.key).filter(k=>!pending.layout?.columns?.includes(k))
      }));
      setAnalysisRow(pending.layout?.analysis_row || "nama_barang");
      setGroupCurrency(!!pending.layout?.group_currency);
    } else {
      setLayout(normalizeReportColumns(definitions));
      setAnalysisRow("nama_barang");setGroupCurrency(false);
    }
    setResult(null);setPrintResult(null);setPage(1);
  },[selectedId,definitions]);
  useEffect(() => ()=>{resizeClean.current?.()},[]);

  const currentPayload = useCallback((targetPage=1,includeAll=false) => {
    const keys=layout.order.filter(k=>!layout.hidden.includes(k));
    return {report_id:selectedId,date_from:filters.from,date_to:filters.to,
      branch:filters.branch,layout:{columns:keys,
        ...(selectedId==="penjualan-per-barang" ? {analysis_row:analysisRow}:{}),
        ...(selectedId==="rincian-penjualan-per-barang" ? {group_currency:groupCurrency}:{})},
      page:targetPage,page_size:pageSize,include_all:includeAll};
  },[layout,filters,selectedId,pageSize,analysisRow,groupCurrency]);

  async function show(target=1) {
    if(!selectedId) return;
    setBusy(true);setError("");
    try { const data=await api.salesReportShow(currentPayload(target));setResult(data);setPage(data.page||target); }
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
  async function memorize() {
    if(!savedName.trim()||!selectedId)return;setBusy(true);setError("");
    try{await api.salesReportMemorize({report_id:selectedId,name:savedName.trim(),
      layout:currentPayload().layout,description:"Tampilan laporan Eternal"});
      const updated=await api.salesReportCatalog();setCatalog(updated);setSavedName("");
    }catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  function loadPreset(p) {
    if(!p?.report_id)return;
    const spec=catalog?.reports?.find(r=>r.id===p.report_id);if(!spec)return;
    if(p.report_id !== selectedId) {
      presetPending.current={reportId:p.report_id,layout:p.layout};
      setSelectedId(p.report_id);
      return;
    }
    setLayout(normalizeReportColumns(spec.columns || [],{
      order:p.layout?.columns || [],hidden:(spec.columns||[]).map(c=>c.key).filter(k=>!p.layout?.columns?.includes(k))
    }));
    setAnalysisRow(p.layout?.analysis_row || "nama_barang");
    setGroupCurrency(!!p.layout?.group_currency);
    setResult(null);setPage(1);
  }
  const startResize=useCallback((event,key,startWidth)=>{
    event.preventDefault();resizeClean.current?.();
    const x=event.clientX;const move=(e)=>setLayout(old=>({...old,widths:{...old.widths,[key]:Math.max(100,Math.min(600,startWidth+e.clientX-x))}}));
    const stop=()=>{window.removeEventListener("pointermove",move);window.removeEventListener("pointerup",stop);resizeClean.current=null;};
    resizeClean.current=stop;window.addEventListener("pointermove",move);window.addEventListener("pointerup",stop);
  },[]);
  const visible=layout.order.filter(k=>!layout.hidden.includes(k));
  const settings=<div className="erw-report-settings">
    <h3>Kolom</h3><p>Pilih, susun ulang, dan atur lebar kolom.</p>
    {layout.order.map((key,i)=>{const def=definitions.find(c=>c.key===key);if(!def)return null;
      return <div className="erw-col-row" key={key}>
        <label><input type="checkbox" checked={!layout.hidden.includes(key)}
          onChange={()=>setLayout(old=>{
            const shown=old.order.filter(k=>!old.hidden.includes(k));
            if(shown.length===1 && shown[0]===key)return old;
            return {...old,hidden:old.hidden.includes(key)?old.hidden.filter(k=>k!==key):[...old.hidden,key]};
          })}/>{def.label}</label>
        <button type="button" aria-label={"Naikkan "+def.label} disabled={i===0}
          onClick={()=>setLayout(old=>({...old,order:moveReportColumn(old.order,key,old.order[i-1])}))}>↑</button>
        <button type="button" aria-label={"Turunkan "+def.label} disabled={i===layout.order.length-1}
          onClick={()=>setLayout(old=>({...old,order:moveReportColumn(old.order,old.order[i+1],key)}))}>↓</button>
      </div>;
    })}
    <button type="button" onClick={()=>setLayout(normalizeReportColumns(definitions))}>Reset Kolom</button>
    <h3>Pengelompokan</h3>
    {selectedId==="penjualan-per-barang" ? <label className="erw-setting-select">Analisa berdasarkan
      <select value={analysisRow} onChange={e=>setAnalysisRow(e.target.value)}>
        <option value="nama_barang">Nama Barang</option><option value="nama_kategori_barang">Kategori Barang</option>
      </select></label> : selectedId==="rincian-penjualan-per-barang" ? <label className="erw-setting-check">
        <input type="checkbox" checked={groupCurrency} onChange={e=>setGroupCurrency(e.target.checked)}/>Kelompokkan menurut Mata Uang
      </label> : <p>Pengelompokan tidak tersedia untuk laporan ini.</p>}
    <h3>Simpan Tampilan</h3>
    <input className="erw-preset-name" placeholder="Nama tampilan" aria-label="Nama tampilan"
      value={savedName} onChange={e=>setSavedName(e.target.value)}/>
    <button type="button" onClick={memorize} disabled={!savedName.trim()||busy}>Simpan</button>
    <h3>Tampilan Tersimpan</h3>
    {saved.filter(p=>p.report_id===selectedId).map(p=><div className="erw-saved" key={p.id}>
      <button type="button" onClick={()=>loadPreset(p)}>{p.name}</button>
    </div>)}
  </div>;
  const actions=<>
    <button type="button" className="erw-button" disabled={!result||busy} onClick={printAll}>Cetak Semua</button>
    <button type="button" className="erw-button" disabled={!result||busy||!canExport} onClick={exportExcel}>Ekspor Excel</button>
  </>;
  return <div className="erw-report-page">
    <ReportWorkspaceShell title="Laporan Penjualan" reports={catalog?.reports?.filter(r=>r.implemented!==false)||[]}
      selectedId={selectedId} onSelect={setSelectedId} filters={{...filters,branchOptions:catalog?.branches||[]}}
      onFiltersChange={next=>setFilters(old=>({...old,from:next.from,to:next.to,branch:next.branch}))}
      loading={busy} onRun={()=>show(1)} settings={settings} actions={actions} error={error}>
      {result ? <>
        {!!result.gap&&<p className="erw-report-warning" role="status">{result.gap}</p>}
        <ReportTable report={result} layout={layout} onResize={startResize}/>
        <div className="erw-report-pager">
          <span>Total dihitung dari seluruh hasil filter, bukan hanya halaman ini.</span>
          <label>Baris per halaman <select value={pageSize} onChange={e=>{setPageSize(Number(e.target.value));setResult(null);setPage(1);}}>
            {[10,25,50,100].map(n=><option key={n}>{n}</option>)}</select></label>
          <button type="button" disabled={busy||page<=1} onClick={()=>show(page-1)}>Sebelumnya</button>
          <span>Halaman {page} dari {result.page_count||1}</span>
          <button type="button" disabled={busy||page>=(result.page_count||1)} onClick={()=>show(page+1)}>Berikutnya</button>
        </div>
        {result.methodology && <details className="erw-method"><summary>Dasar perhitungan & sumber data</summary>
          <p>{Object.values(result.methodology).filter(Boolean).join(". ")}</p>
        </details>}
      </> : <div className="erw-report-empty">Pilih jenis laporan, periode, dan cabang, lalu klik <strong>Tampilkan</strong>.</div>}
    </ReportWorkspaceShell>
    <div className="erw-print-only">{printResult && <ReportTable report={printResult}/>}</div>
  </div>;
}
