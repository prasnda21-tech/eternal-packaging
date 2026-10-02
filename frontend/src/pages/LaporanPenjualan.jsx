import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import "./LaporanPenjualan.css";

const UNIMPLEMENTED_NOTE =
  "Nama laporan ini terlihat di daftar Accurate, tetapi kolom defaultnya belum terlihat di screenshot referensi sehingga angka tidak dihitung.";

function monthBounds(date = new Date()) {
  const year = date.getFullYear();
  const month = date.getMonth();
  const last = new Date(year, month + 1, 0).getDate();
  const pad = (value) => String(value).padStart(2, "0");
  return {
    from: `01/${pad(month + 1)}/${year}`,
    to: `${pad(last)}/${pad(month + 1)}/${year}`,
  };
}

function errorText(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (err?.message) return err.message;
  return "Laporan tidak dapat ditampilkan.";
}

function formatCell(value) {
  if (value === null || value === undefined || value === "") return "";
  const text = String(value);
  if (!/^-?\d+(\.\d+)?$/.test(text)) return text;
  const negative = text.startsWith("-");
  const [whole, frac] = text.replace("-", "").split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const shown = frac ? `${grouped},${frac}` : grouped;
  return negative ? `-${shown}` : shown;
}

function defaultColumns(report) {
  return (report?.columns || []).filter((col) => col.default).map((col) => col.key);
}

function layoutPayload(reportId, columnKeys, analysisOn, analysisRow, groupCurrency) {
  const layout = { columns: columnKeys };
  if (reportId === "penjualan-per-barang") {
    layout.analysis_row = analysisOn ? analysisRow : "nama_barang";
  }
  if (reportId === "rincian-penjualan-per-barang") {
    layout.group_currency = !!groupCurrency;
  }
  return layout;
}

function DocIcon() {
  return (
    <svg className="aol-ico" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="currentColor" d="M6 2h8l4 4v16H6V2zm8 1.5V7h3.5L14 3.5zM8 11h8v1.2H8V11zm0 3h8v1.2H8V14zm0 3h5v1.2H8V17z" />
    </svg>
  );
}

export default function LaporanPenjualan() {
  const bounds = useMemo(() => monthBounds(), []);
  const [catalog, setCatalog] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [side, setSide] = useState("penjualan");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [paramOpen, setParamOpen] = useState(false);
  const [paramTab, setParamTab] = useState("umum");
  const [dateFrom, setDateFrom] = useState(bounds.from);
  const [dateTo, setDateTo] = useState(bounds.to);
  const [branch, setBranch] = useState("semua");
  const [columnKeys, setColumnKeys] = useState([]);
  const [analysisOn, setAnalysisOn] = useState(true);
  const [analysisRow, setAnalysisRow] = useState("nama_barang");
  const [groupCurrency, setGroupCurrency] = useState(false);
  const [report, setReport] = useState(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState("");
  const [gapReport, setGapReport] = useState(null);
  const [memorizeOpen, setMemorizeOpen] = useState(false);
  const [memorizeName, setMemorizeName] = useState("");
  const [memorizeDescription, setMemorizeDescription] = useState("");

  useEffect(() => {
    let live = true;
    api.salesReportCatalog()
      .then((data) => {
        if (!live) return;
        setCatalog(data);
        const first = (data?.branches || [])[0]?.id || "semua";
        setBranch((current) => (data?.branches || []).some((row) => row.id === current) ? current : first);
      })
      .catch((err) => {
        if (live) setLoadError(errorText(err));
      });
    return () => {
      live = false;
    };
  }, []);

  const reports = catalog?.reports || [];
  const branches = catalog?.branches || [];
  const memorized = catalog?.memorized || [];
  const filtered = reports.filter((row) => {
    const blob = `${row.name} ${row.description}`.toLowerCase();
    return blob.includes(query.trim().toLowerCase());
  });

  const openReport = (row, layout) => {
    setBanner("");
    if (!row.implemented) {
      setGapReport(row);
      return;
    }
    setSelected(row);
    setColumnKeys(Array.isArray(layout?.columns) && layout.columns.length ? layout.columns : defaultColumns(row));
    setAnalysisOn(true);
    setAnalysisRow(layout?.analysis_row || "nama_barang");
    setGroupCurrency(!!layout?.group_currency);
    setParamTab("umum");
    setParamOpen(true);
    setReport(null);
  };

  const openMemorized = (item) => {
    const row = reports.find((reportRow) => reportRow.id === item.report_id);
    if (!row) return;
    openReport(row, item.layout || {});
  };

  const currentLayout = useCallback(() => layoutPayload(
    selected?.id,
    columnKeys,
    analysisOn,
    analysisRow,
    groupCurrency,
  ), [selected, columnKeys, analysisOn, analysisRow, groupCurrency]);

  const showReport = async (nextPage = 1) => {
    if (!selected) return;
    setBusy(true);
    setBanner("");
    try {
      const data = await api.salesReportShow({
        report_id: selected.id,
        date_from: dateFrom,
        date_to: dateTo,
        branch,
        layout: currentLayout(),
        page: nextPage,
        page_size: 25,
      });
      setReport(data);
      setPage(data.page || nextPage);
      setParamOpen(false);
    } catch (err) {
      setBanner(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const refresh = () => {
    if (report) showReport(page);
  };

  const exportReport = async () => {
    if (!selected) return;
    setBusy(true);
    setBanner("");
    try {
      const blob = await api.salesReportExport({
        report_id: selected.id,
        date_from: dateFrom,
        date_to: dateTo,
        branch,
        layout: currentLayout(),
        page: 1,
        page_size: 25,
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${selected.id}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setBanner(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const saveMemorized = async () => {
    if (!selected) return;
    setBusy(true);
    setBanner("");
    try {
      await api.salesReportMemorize({
        report_id: selected.id,
        name: memorizeName,
        description: memorizeDescription,
        layout: currentLayout(),
      });
      const data = await api.salesReportCatalog();
      setCatalog(data);
      setMemorizeOpen(false);
      setMemorizeName("");
      setMemorizeDescription("");
    } catch (err) {
      setBanner(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const forgetMemorized = async (item) => {
    setBusy(true);
    setBanner("");
    try {
      await api.salesReportForget(item.id);
      const data = await api.salesReportCatalog();
      setCatalog(data);
    } catch (err) {
      setBanner(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const toggleColumn = (key) => {
    setColumnKeys((current) => (
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
    ));
  };

  const totalFor = (key) => {
    if (!report?.totals || !(key in report.totals)) return "";
    const value = report.totals[key];
    if (value === null || value === undefined) return "";
    const column = (report.columns || []).find((col) => col.key === key);
    return column?.align === "right" ? formatCell(value) : String(value);
  };

  return (
    <div className="aol-page">
      <aside className="aol-side">
        <button type="button" className={side === "penjualan" && !report ? "is-active" : ""} onClick={() => { setSide("penjualan"); setReport(null); }}>
          Penjualan
        </button>
        <button type="button" className={side === "memorize" && !report ? "is-active" : ""} onClick={() => { setSide("memorize"); setReport(null); }}>
          Memorize
        </button>
      </aside>
      {!report ? (
        <main className="aol-main">
          <div className="aol-main-head">
            <h1>{side === "memorize" ? "Memorize" : "Penjualan"}</h1>
            {side === "penjualan" ? (
              <input
                className="aol-search"
                aria-label="Cari"
                placeholder="Cari"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            ) : null}
          </div>
          {loadError ? <p className="aol-error">{loadError}</p> : null}
          {banner ? <p className="aol-error">{banner}</p> : null}
          {side === "penjualan" ? (
            <div className="aol-grid">
              {filtered.map((row) => (
                <button key={row.id} type="button" className="aol-report" onClick={() => openReport(row)}>
                  <DocIcon />
                  <span>
                    <strong>{row.name}</strong>
                    <span>{row.description}</span>
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <div className="aol-grid">
              {memorized.length === 0 ? <p className="aol-empty">Belum ada format laporan yang disimpan.</p> : null}
              {memorized.map((item) => (
                <div key={item.id}>
                  <button type="button" className="aol-report" onClick={() => openMemorized(item)}>
                    <DocIcon />
                    <span>
                      <strong>{item.name}</strong>
                      <span>{item.description || item.report_id}</span>
                    </span>
                  </button>
                  <button type="button" onClick={() => forgetMemorized(item)}>Hapus</button>
                </div>
              ))}
            </div>
          )}
        </main>
      ) : (
        <section className="aol-stage">
          <div className="aol-toolbar">
            <button type="button" onClick={() => setReport(null)}>Daftar Laporan</button>
            <button type="button" onClick={() => { setParamTab("kolom"); setParamOpen(true); }}>Modifikasi</button>
            <button type="button" onClick={refresh} disabled={busy}>Segarkan</button>
            <button type="button" onClick={() => window.print()}>Cetak</button>
            <button type="button" onClick={exportReport} disabled={busy}>Ekspor</button>
            <button type="button" onClick={() => { setMemorizeName(report.name || ""); setMemorizeOpen(true); }}>Simpan Format</button>
          </div>
          {banner ? <p className="aol-error">{banner}</p> : null}
          {report.gap ? <p className="aol-note">{report.gap}</p> : null}
          <div className="aol-paper-wrap">
            <article className="aol-paper">
              <p className="aol-company">{report.company_name}</p>
              <h2 className="aol-title">{report.name}</h2>
              <p className="aol-period">{report.period_label}</p>
              <p className="aol-branch">{report.branch_label}</p>
              <table className="aol-table">
                <thead>
                  <tr>
                    {(report.columns || []).map((col) => (
                      <th key={col.key} className={col.align === "right" ? "num" : undefined}>{col.label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(report.rows || []).length === 0 ? (
                    <tr>
                      <td className="aol-empty" colSpan={(report.columns || []).length || 1}>Tidak ada data pada periode dan cabang ini.</td>
                    </tr>
                  ) : null}
                  {(report.rows || []).map((row, index) => {
                    if (row.kind === "group") {
                      return (
                        <tr key={`g-${index}`} className="group">
                          <td colSpan={(report.columns || []).length || 1}>{row.label}</td>
                        </tr>
                      );
                    }
                    const className = row.kind === "subtotal" ? "subtotal" : undefined;
                    return (
                      <tr key={`r-${index}`} className={className}>
                        {(report.columns || []).map((col) => {
                          const raw = row.values?.[col.key];
                          const shown = col.align === "right" ? formatCell(raw) : (raw ?? "");
                          const drill = col.key === "nomor" && row.drill?.href;
                          return (
                            <td key={col.key} className={col.align === "right" ? "num" : undefined}>
                              {drill ? <Link to={row.drill.href}>{shown}</Link> : shown}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                  {(report.rows || []).length > 0 ? (
                    <tr className="total">
                      {(report.columns || []).map((col, index) => {
                        const labelAt = (report.columns || []).findIndex((item) => item.align !== "right");
                        const showLabel = index === (labelAt === -1 ? 0 : labelAt);
                        return (
                          <td key={col.key} className={col.align === "right" ? "num" : undefined}>
                            {showLabel ? "Total" : totalFor(col.key)}
                          </td>
                        );
                      })}
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </article>
          </div>
          <div className="aol-pager">
            <button type="button" onClick={() => showReport(page - 1)} disabled={busy || page <= 1}>Sebelumnya</button>
            <span>Halaman {report.page} dari {report.page_count}</span>
            <button type="button" onClick={() => showReport(page + 1)} disabled={busy || page >= (report.page_count || 1)}>Berikutnya</button>
            <span>{report.totals_scope}</span>
          </div>
          {report.methodology ? (
            <p className="aol-note">
              Sumber {report.methodology.source}. Tanggal {report.methodology.date}. {report.methodology.discount}. {report.methodology.penjualan}. {report.methodology.bpp}. {report.methodology.returns}.
            </p>
          ) : null}
        </section>
      )}

      {paramOpen && selected ? (
        <div className="aol-mask" role="presentation">
          <div className="aol-modal" role="dialog" aria-label="Parameter Laporan">
            <header>
              Parameter Laporan
              <button type="button" aria-label="Tutup" onClick={() => setParamOpen(false)}>Tutup</button>
            </header>
            <div className="aol-tabs">
              <button type="button" className={paramTab === "umum" ? "is-active" : ""} onClick={() => setParamTab("umum")}>Umum</button>
              <button type="button" className={paramTab === "kolom" ? "is-active" : ""} onClick={() => setParamTab("kolom")}>Kolom</button>
            </div>
            <div className="aol-form">
              <h2>{selected.name}</h2>
              {paramTab === "umum" ? (
                <>
                  <label className="aol-row">
                    Dari
                    <input aria-label="Dari" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
                  </label>
                  <label className="aol-row">
                    s/d
                    <input aria-label="s/d" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
                  </label>
                  <label className="aol-row">
                    Cabang
                    <select aria-label="Cabang" value={branch} onChange={(event) => setBranch(event.target.value)}>
                      {branches.map((row) => (
                        <option key={row.id} value={row.id}>{row.label}</option>
                      ))}
                    </select>
                  </label>
                </>
              ) : (
                <>
                  <div className="aol-columns">
                    {(selected.columns || []).map((col) => (
                      <label key={col.key} className="aol-check">
                        <input
                          type="checkbox"
                          checked={columnKeys.includes(col.key)}
                          onChange={() => toggleColumn(col.key)}
                        />
                        {col.label}
                      </label>
                    ))}
                  </div>
                  {selected.id === "penjualan-per-barang" ? (
                    <>
                      <label className="aol-check">
                        <input type="checkbox" checked={analysisOn} onChange={(event) => setAnalysisOn(event.target.checked)} />
                        Tampilkan sebagai laporan analisa
                      </label>
                      <label className="aol-row">
                        Baris Analisa
                        <select aria-label="Baris Analisa" value={analysisRow} onChange={(event) => setAnalysisRow(event.target.value)} disabled={!analysisOn}>
                          <option value="nama_barang">Nama Barang</option>
                          <option value="nama_kategori_barang">Nama Kategori Barang Barang & Jasa</option>
                        </select>
                      </label>
                      <p>Data Analisa: Kuantitas (Total Nilai), Penjualan (Total Nilai).</p>
                    </>
                  ) : null}
                  {selected.id === "rincian-penjualan-per-barang" ? (
                    <label className="aol-check">
                      <input type="checkbox" checked={groupCurrency} onChange={(event) => setGroupCurrency(event.target.checked)} />
                      Kelompokkan menurut Negara/Nama Mata Uang
                    </label>
                  ) : null}
                </>
              )}
              <div className="aol-actions">
                <button type="button" className="primary" onClick={() => showReport(1)} disabled={busy}>Tampilkan</button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {gapReport ? (
        <div className="aol-mask" role="presentation">
          <div className="aol-modal" role="dialog" aria-label={gapReport.name}>
            <header>
              {gapReport.name}
              <button type="button" onClick={() => setGapReport(null)}>Tutup</button>
            </header>
            <div className="aol-form">
              <p>{gapReport.gap || UNIMPLEMENTED_NOTE}</p>
              <p>Referensi: {gapReport.reference}</p>
              <div className="aol-actions">
                <button type="button" className="primary" onClick={() => setGapReport(null)}>Tutup</button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {memorizeOpen ? (
        <div className="aol-mask" role="presentation">
          <div className="aol-modal" role="dialog" aria-label="Simpan Format">
            <header>
              Simpan Format
              <button type="button" onClick={() => setMemorizeOpen(false)}>Tutup</button>
            </header>
            <div className="aol-form">
              <label className="aol-row">
                Nama
                <input aria-label="Nama format" value={memorizeName} onChange={(event) => setMemorizeName(event.target.value)} />
              </label>
              <label className="aol-row">
                Keterangan
                <textarea aria-label="Keterangan format" value={memorizeDescription} onChange={(event) => setMemorizeDescription(event.target.value)} />
              </label>
              <div className="aol-actions">
                <button type="button" className="primary" onClick={saveMemorized} disabled={busy}>Simpan</button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
