import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./ReportColumnSettings.css";

const MIN_WIDTH = 100;
const MAX_WIDTH = 600;
const isRecord = (v) => !!v && typeof v === "object" && !Array.isArray(v);
const cap = (w) => Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Number(w) || 150));
const safeName = (v) => typeof v === "string" && v.trim().length ? v.trim().slice(0, 80) : "";

export function normalizeReportColumns(definitions = [], saved = {}) {
  const catalog = definitions.filter((c) => c && typeof c.key === "string" && c.key);
  const byKey = new Map(catalog.map((c) => [c.key, c]));
  const seen = new Set();
  const input = Array.isArray(saved?.order) ? saved.order : [];
  const order = [...input, ...catalog.map((c) => c.key)].filter((k) => {
    if (!byKey.has(k) || seen.has(k)) return false;
    seen.add(k); return true;
  });
  const hidden = Array.isArray(saved?.hidden) ? saved.hidden.filter((k) => byKey.has(k)) : [];
  const widths = Object.fromEntries(catalog.map((c) => [c.key, cap(saved?.widths?.[c.key] ?? c.width ?? 150)]));
  if (order.every((k) => hidden.includes(k)) && order.length) hidden.splice(hidden.indexOf(order[0]), 1);
  return { order, hidden, widths };
}
export function moveReportColumn(order, from, to) {
  if (!order.includes(from) || !order.includes(to) || from === to) return order;
  const next = order.filter((x) => x !== from);
  next.splice(next.indexOf(to), 0, from);
  return next;
}
export function filterReportRows(rows = [], query = "", filters = {}) {
  const q = String(query).toLocaleLowerCase("id-ID").trim();
  return rows.filter((r) => {
    if (q && !Object.values(r).some((v) => String(v ?? "").toLocaleLowerCase("id-ID").includes(q))) return false;
    return Object.entries(filters).every(([key, val]) => val === "" || val == null || String(r[key] ?? "") === String(val));
  });
}
function read(key) {
  try { return JSON.parse(window.localStorage.getItem(key) || "null"); } catch { return null; }
}
function write(key, value) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
}
/** UI-only report column settings. Caller supplies authorized report rows; never performs API requests. */
export default function ReportColumnSettings({ reportId, columns = [], rows = [], userScope = "self", onViewChange }) {
  const key = "erw-report-layout-v1:" + userScope + ":" + reportId;
  const presetKey = key + ":presets";
  const defaults = useMemo(() => normalizeReportColumns(columns), [columns]);
  const [layout, setLayout] = useState(() => normalizeReportColumns(columns, read(key)));
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState({});
  const [presets, setPresets] = useState(() => Array.isArray(read(presetKey)) ? read(presetKey) : []);
  const [name, setName] = useState("");
  const [notice, setNotice] = useState("");
  const [dragging, setDragging] = useState(null);
  const resizeRef = useRef(null);
  useEffect(() => {
    setLayout(normalizeReportColumns(columns, read(key)));
    setPresets(Array.isArray(read(presetKey)) ? read(presetKey) : []);
    setQuery(""); setFilters({}); setNotice("");
  }, [key, columns]);
  useEffect(() => { write(key, layout); onViewChange?.(layout); }, [key, layout, onViewChange]);
  useEffect(() => {
    function move(e) {
      const current = resizeRef.current;
      if (current) setLayout((old) => ({ ...old, widths: { ...old.widths, [current.key]: cap(current.width + e.clientX - current.x) } }));
    }
    function end() { resizeRef.current = null; }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", end); };
  }, []);
  const visible = layout.order.filter((k) => !layout.hidden.includes(k));
  const definitions = useMemo(() => new Map(columns.map((c) => [c.key, c])), [columns]);
  const displayed = useMemo(() => filterReportRows(rows, query, filters), [rows, query, filters]);
  const update = useCallback((change) => setLayout((old) => normalizeReportColumns(columns, { ...old, ...change })), [columns]);
  const move = (a, b) => setLayout((old) => ({ ...old, order: moveReportColumn(old.order, a, b) }));
  const toggle = (k) => {
    if (visible.length === 1 && visible[0] === k) { setNotice("Minimal satu kolom harus terlihat."); return; }
    update({ hidden: layout.hidden.includes(k) ? layout.hidden.filter((x) => x !== k) : [...layout.hidden, k] });
  };
  const savePreset = () => {
    const label = safeName(name);
    if (!label) { setNotice("Isi nama tampilan terlebih dahulu."); return; }
    const next = [...presets.filter((p) => p.name !== label), { name: label, layout, filters, query }].slice(-15);
    setPresets(next); write(presetKey, next); setName(""); setNotice("Tampilan disimpan pada browser ini.");
  };
  const openPreset = (p) => { setLayout(normalizeReportColumns(columns, p.layout)); setFilters(isRecord(p.filters) ? p.filters : {}); setQuery(p.query || ""); setNotice("Tampilan dimuat."); };
  const dropPreset = (p) => { const next = presets.filter((x) => x.name !== p.name); setPresets(next); write(presetKey, next); };
  return <section className="erw-settings-workspace" aria-label="Pengaturan kolom dan filter laporan">
    <div className="erw-control-head"><h3>Pengaturan Kolom</h3><button type="button" onClick={() => {setLayout(defaults);setFilters({});setQuery("");setNotice("Tampilan default dipulihkan.");}}>Reset</button></div>
    <p className="erw-help">Geser nama kolom untuk mengurutkan; gunakan tombol panah melalui keyboard, dan tarik batas kanan header untuk mengubah lebar.</p>
    <div className="erw-column-choices">
      {layout.order.map((k, index) => {
        const col = definitions.get(k);
        if (!col) return null;
        return <div className="erw-column-item" key={k}>
          <input type="checkbox" aria-label={"Tampilkan " + col.label} checked={!layout.hidden.includes(k)} onChange={() => toggle(k)} />
          <span className="erw-column-label" draggable onDragStart={() => setDragging(k)} onDragEnd={() => setDragging(null)}
            onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (dragging) move(dragging, k); setDragging(null); }}
            title="Tarik untuk mengubah urutan">{col.label}</span>
          <button type="button" aria-label={"Pindahkan " + col.label + " ke kiri"} disabled={index === 0} onClick={() => move(k, layout.order[index-1])}>←</button>
          <button type="button" aria-label={"Pindahkan " + col.label + " ke kanan"} disabled={index === layout.order.length-1} onClick={() => move(layout.order[index+1], k)}>→</button>
        </div>;
      })}
    </div>
    <h3>Filter dan Pencarian</h3>
    <label className="erw-setting-field">Cari pada hasil laporan<input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Cari pada data yang ditampilkan"/></label>
    {columns.filter((c) => Array.isArray(c.filterOptions)).map((c) => <label key={c.key} className="erw-setting-field">{c.label}
      <select value={filters[c.key] || ""} onChange={(e) => setFilters((v) => ({ ...v, [c.key]: e.target.value }))}>
        <option value="">Semua</option>{c.filterOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select></label>)}
    <h3>Simpan Tampilan</h3>
    <div className="erw-save-view"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Nama tampilan"/><button type="button" onClick={savePreset}>Simpan</button></div>
    {presets.map((p) => <div className="erw-preset" key={p.name}><button type="button" onClick={() => openPreset(p)}>{p.name}</button><button type="button" aria-label={"Hapus " + p.name} onClick={() => dropPreset(p)}>×</button></div>)}
    {notice && <p role="status" className="erw-notice">{notice}</p>}
    <div className="erw-table-wrap" role="region" aria-label="Pratinjau tabel" tabIndex={0}>
      <table><colgroup>{visible.map((k) => <col key={k} style={{width:layout.widths[k]}} />)}</colgroup>
        <thead><tr>{visible.map((k) => <th key={k} title={definitions.get(k)?.label} style={{position:"relative"}}>
          {definitions.get(k)?.label}
          <span className="erw-resizer" role="separator" aria-label={"Ubah lebar " + definitions.get(k)?.label}
            onPointerDown={(e) => { e.preventDefault(); resizeRef.current={key:k,x:e.clientX,width:layout.widths[k]}; }}/>
        </th>)}</tr></thead>
        <tbody>{displayed.length ? displayed.map((r,i) => <tr key={r.id ?? i}>{visible.map((k) => <td key={k}>{String(r[k] ?? "—")}</td>)}</tr>) : <tr><td colSpan={visible.length || 1}>Tidak ada data yang sesuai.</td></tr>}</tbody>
      </table>
    </div>
    <p className="erw-help">Filter ini bekerja pada baris yang sudah dimuat, bukan seluruh data server. Untuk ekspor dan total, engine laporan wajib menerima filter yang sama.</p>
  </section>;
}
