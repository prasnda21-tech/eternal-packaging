import React, { useMemo, useState } from "react";
import "./ReportGroupedTable.css";

/**
 * Presentation-only grouping over already-authorized report rows.
 * Financial subtotals MUST come from the report engine. This component never
 * sums money, BPP, units, currencies, or margins in the browser.
 */
export function groupReportRows(rows = [], groupKey, labelKey) {
  if (!groupKey) return [];
  const groups = new Map();
  rows.forEach((row, index) => {
    const raw = row?.[groupKey];
    const key = raw == null || String(raw).trim() === "" ? "__missing__" : String(raw);
    if (!groups.has(key)) groups.set(key, { key, label: key === "__missing__" ? "Tanpa Kategori" : String(row?.[labelKey] ?? raw), rows: [], originalIndex: index });
    groups.get(key).rows.push(row);
  });
  return [...groups.values()];
}

function display(value, column) {
  if (value === null || value === undefined || value === "") return "—";
  if (column?.format) return column.format(value);
  return String(value);
}

/**
 * subtotalByGroup is server-supplied, keyed by normalized group key.
 * No subtotal is shown if the engine does not provide it.
 */
export default function ReportGroupedTable({
  rows = [], columns = [], groupOptions = [], groupBy = "",
  onGroupChange, subtotalByGroup = {}, grandTotals = null,
  loading = false, incompleteReason = "", emptyLabel = "Tidak ada data pada filter ini."
}) {
  const [collapsed, setCollapsed] = useState({});
  const groups = useMemo(() => groupReportRows(rows, groupBy), [rows, groupBy]);
  const visible = columns.filter((c) => c && c.key && c.visible !== false);
  const hasGrouping = !!groupBy && groupOptions.some((x) => x.key === groupBy);
  const toggle = (key) => setCollapsed((old) => ({ ...old, [key]: !old[key] }));
  return <section className="erw-grouped" aria-label="Analisis laporan">
    <div className="erw-group-toolbar">
      <label>Kelompokkan berdasarkan
        <select value={hasGrouping ? groupBy : ""} onChange={(e) => { setCollapsed({}); onGroupChange?.(e.target.value); }}>
          <option value="">Tanpa pengelompokan</option>
          {groupOptions.map((g) => <option key={g.key} value={g.key}>{g.label}</option>)}
        </select>
      </label>
      {hasGrouping && <span className="erw-group-hint">{groups.length} kelompok • Klik header untuk buka/tutup</span>}
    </div>
    {incompleteReason && <p role="status" className="erw-group-warning">Angka yang belum lengkap tidak ditampilkan sebagai nol. {incompleteReason}</p>}
    <div className="erw-group-scroll" role="region" tabIndex={0} aria-label="Tabel hasil laporan">
      <table>
        <thead><tr>{visible.map((c) => <th key={c.key} scope="col" style={{ textAlign: c.align || "left", minWidth: c.minWidth || 100 }}>{c.label}</th>)}</tr></thead>
        <tbody>
          {loading ? <tr><td colSpan={visible.length || 1}>Memuat laporan...</td></tr> : null}
          {!loading && !rows.length ? <tr><td className="erw-group-empty" colSpan={visible.length || 1}>{emptyLabel}</td></tr> : null}
          {!loading && !hasGrouping && rows.map((row, i) => <tr key={row.id || i}>{visible.map((c) => <td key={c.key} style={{ textAlign:c.align || "left" }}>{display(row[c.key], c)}</td>)}</tr>)}
          {!loading && hasGrouping && groups.map((g) => <React.Fragment key={g.key}>
            <tr className="erw-group-row"><th colSpan={visible.length || 1} scope="rowgroup">
              <button type="button" onClick={() => toggle(g.key)} aria-expanded={!collapsed[g.key]}>
                <span aria-hidden="true">{collapsed[g.key] ? "▸" : "▾"}</span>
                {g.label} <small>({g.rows.length} baris)</small>
              </button>
            </th></tr>
            {!collapsed[g.key] && g.rows.map((row,i) => <tr key={row.id || g.key+":"+i}>{visible.map((c) => <td key={c.key} style={{textAlign:c.align || "left"}}>{display(row[c.key],c)}</td>)}</tr>)}
            {!collapsed[g.key] && subtotalByGroup[g.key] && <tr className="erw-subtotal">
              {visible.map((c,i) => <td key={c.key} style={{textAlign:c.align || "left"}}>{Object.prototype.hasOwnProperty.call(subtotalByGroup[g.key],c.key) ? display(subtotalByGroup[g.key][c.key],c) : i===0 ? "Subtotal "+g.label : "—"}</td>)}
            </tr>}
          </React.Fragment>)}
        </tbody>
        {!loading && rows.length>0 && grandTotals && <tfoot><tr>{visible.map((c,i) => <th key={c.key} style={{textAlign:c.align || "left"}}>{Object.prototype.hasOwnProperty.call(grandTotals,c.key) ? display(grandTotals[c.key],c) : i===0 ? "Total semua data terfilter" : "—"}</th>)}</tr></tfoot>}
      </table>
    </div>
    <p className="erw-group-note">Subtotal dan total hanya berasal dari mesin laporan. Pengelompokan di sini tidak mengubah data, perhitungan, ekspor atau hak akses.</p>
  </section>;
}
