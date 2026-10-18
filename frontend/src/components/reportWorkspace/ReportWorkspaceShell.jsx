import React, { useState } from "react";
import "./ReportWorkspaceShell.css";

/** UI-only shell. Caller owns authorization, reporting calculations and API calls. */
export default function ReportWorkspaceShell({
  title = "Laporan Penjualan", reports = [], selectedId, onSelect,
  filters = {}, onFiltersChange, onRun, loading = false, actions,
  settings, children, error
}) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const selected = reports.find((r) => r.id === selectedId);
  const change = (key, value) => onFiltersChange?.({ ...filters, [key]: value });
  return <section className="erw" aria-label="Ruang kerja laporan">
    <header className="erw-header">
      <div><small>ETERNAL PACKAGING / LAPORAN</small><h1>{title}</h1></div>
      <div className="erw-actions">{actions}
        <button type="button" onClick={() => setSettingsOpen(!settingsOpen)}
          aria-controls="erw-settings" aria-expanded={settingsOpen}>
          {settingsOpen ? "Tutup Pengaturan" : "Atur Tampilan"}
        </button>
      </div>
    </header>
    <div className="erw-layout">
      <nav className="erw-catalog" aria-label="Pilih laporan">
        <strong>Daftar Laporan</strong>
        {reports.map((r) => <button key={r.id} type="button"
          className={r.id === selectedId ? "active" : ""}
          aria-current={r.id === selectedId ? "page" : undefined}
          onClick={() => onSelect?.(r.id)}>{r.name}</button>)}
        {!reports.length && <p>Belum ada laporan tersedia.</p>}
      </nav>
      <main className="erw-main">
        <form className="erw-toolbar" onSubmit={(e) => { e.preventDefault(); onRun?.(); }}>
          <label>Dari Tanggal<input type="date" value={filters.from || ""}
            onChange={(e) => change("from", e.target.value)}/></label>
          <label>Sampai Tanggal<input type="date" value={filters.to || ""}
            onChange={(e) => change("to", e.target.value)}/></label>
          <label>Cabang<select value={filters.branch || "semua"}
            onChange={(e) => change("branch", e.target.value)}>
            {(filters.branchOptions || [{ id: "semua", label: "Semua Cabang" }])
              .map((b) => <option value={b.id} key={b.id}>{b.label}</option>)}
          </select></label>
          <button className="erw-run" disabled={!selected || loading} type="submit">
            {loading ? "Memuat..." : "Tampilkan"}
          </button>
        </form>
        <div className="erw-result" aria-live="polite">
          <h2>{selected?.name || "Pilih Laporan"}</h2>
          {error && <p className="erw-error" role="alert">{error}</p>}
          <div className="erw-sheet">{children || <p>Hasil laporan akan ditampilkan di sini.</p>}</div>
        </div>
      </main>
      {settingsOpen && <aside id="erw-settings" className="erw-settings" aria-label="Pengaturan laporan">
        <div className="erw-settings-title"><strong>Pengaturan Laporan</strong>
          <button type="button" onClick={() => setSettingsOpen(false)} aria-label="Tutup">×</button></div>
        {settings || <p>Pengaturan tersedia setelah laporan dipilih.</p>}
      </aside>}
    </div>
  </section>;
}
