import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertCircle, ArrowRight, CheckCircle2, Cloud, Copy, Download, FileSpreadsheet, FileText, Plus, RefreshCw, Search, Shield, Trash2, Upload, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate, formatDeadline } from "../lib/constants";

const statusMeta = {
  success: { label: "Berhasil", cls: "log-success", icon: CheckCircle2 },
  duplicate: { label: "Duplikat", cls: "log-duplicate", icon: Copy },
  failed: { label: "Gagal", cls: "log-failed", icon: AlertCircle },
};

const TEMPLATE_HEADERS = [
  "po_number", "order_date", "customer", "outlet", "recipient", "address",
  "payment_terms", "sales", "created_by_user", "expedition", "delivery_zone",
  "estimated_delivery", "notes",
  "item_code", "item_name", "quantity", "unit", "color", "item_notes",
];

function downloadTemplate() {
  const rows = [
    TEMPLATE_HEADERS.join(","),
    ["PO-CONTOH-001", "2026-02-20", "Nama Customer", "Outlet A", "Bu Rina", "Jl. Contoh 12", "Net 14",
     "Vira", "Rina (Sales Admin)", "Internal fleet", "Jakarta Selatan", "2026-02-24", "Cetak logo tengah",
     "PC-12", "Paper cup 12oz", "900", "pcs", "Navy / putih", ""].join(","),
    ["PO-CONTOH-001", "", "", "", "", "", "", "", "", "", "", "", "",
     "PC-16", "Paper cup 16oz", "500", "pcs", "Merah / putih", "Ukuran font kecil"].join(","),
  ];
  const blob = new Blob([rows.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "template-import-po.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export default function ImportPO() {
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const canAccurate = hasPerm("accurate.import");
  const canAccurateBulk = hasPerm("accurate.import.bulk");
  const canPdf = hasPerm("po.import.pdf");
  const canSpreadsheet = hasPerm("po.import.spreadsheet");
  const initialMode = canAccurate ? "accurate" : canSpreadsheet ? "file" : canPdf ? "pdf" : "accurate";
  const [mode, setMode] = useState(initialMode);
  const [soNumber, setSoNumber] = useState("");
  const [file, setFile] = useState(null);
  const fileRef = useRef(null);
  const [pdfFile, setPdfFile] = useState(null);
  const pdfRef = useRef(null);
  const [preview, setPreview] = useState(null);      // {preview, duplicate, existing_po_id, source}
  const [editable, setEditable] = useState(null);    // mutable copy for PDF edits
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [status, setStatus] = useState(null);
  const [available, setAvailable] = useState([]);
  const [logs, setLogs] = useState([]);
  const [importedMsg, setImportedMsg] = useState("");
  const [importedPoId, setImportedPoId] = useState(null);
  // Live SO list (Accurate tab)
  const [recentList, setRecentList] = useState(null); // {rows, total, page, pageSize, pageCount, mode, cached, stale}
  const [recentLoading, setRecentLoading] = useState(false);
  const [recentErr, setRecentErr] = useState("");
  const [pageSize, setPageSize] = useState(20);
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [keywordInput, setKeywordInput] = useState("");
  // Multi-select + bulk
  const [selected, setSelected] = useState(new Set()); // set of SO numbers
  const [bulkPreview, setBulkPreview] = useState(null); // response from preview-bulk
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkResult, setBulkResult] = useState(null); // response from confirm-bulk

  const loadStatus = () => api.accurateStatus().then(setStatus).catch(() => {});
  const loadAvailable = () => api.accurateImportAvailable().then((d) => setAvailable(d.available || [])).catch(() => {});
  const loadLogs = () => api.accurateImportLogs().then((d) => setLogs(d.logs || [])).catch(() => {});
  const loadRecent = async (opts = {}) => {
    setRecentLoading(true);
    setRecentErr("");
    try {
      const _limit = opts.limit ?? pageSize;
      const _page = opts.page ?? page;
      const _kw = opts.keyword ?? keyword;
      const d = await api.accurateList(_limit, opts.refresh === true, _page, _kw);
      setRecentList(d);
      if (d?.error) setRecentErr(d.message || "Daftar Sales Order gagal dimuat. Pencarian nomor SO tetap dapat digunakan.");
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setRecentErr((typeof det === "object" ? det.message : det) || "Daftar Sales Order gagal dimuat. Pencarian nomor SO tetap dapat digunakan.");
    } finally {
      setRecentLoading(false);
    }
  };
  useEffect(() => { loadStatus(); loadAvailable(); loadLogs(); }, []);
  // Auto-fetch recent list when Accurate tab opens / params change
  useEffect(() => {
    if (mode === "accurate") loadRecent({ limit: pageSize, page, keyword });
    // eslint-disable-next-line
  }, [mode, pageSize, page, keyword]);

  const toggleSelected = (num) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(num)) next.delete(num); else next.add(num);
    return next;
  });
  const clearSelection = () => setSelected(new Set());
  const toggleSelectAll = () => {
    const rows = recentList?.rows || [];
    const selectable = rows.filter((r) => r.import_status !== "imported").map((r) => r.number);
    setSelected((prev) => {
      const allSelected = selectable.length > 0 && selectable.every((n) => prev.has(n));
      if (allSelected) {
        const next = new Set(prev);
        selectable.forEach((n) => next.delete(n));
        return next;
      }
      const next = new Set(prev);
      selectable.forEach((n) => next.add(n));
      return next;
    });
  };
  const submitSearch = (e) => {
    e?.preventDefault?.();
    setPage(1);
    setKeyword(keywordInput.trim());
  };
  const clearSearch = () => {
    setKeywordInput("");
    setKeyword("");
    setPage(1);
  };

  const resetPreview = () => { setPreview(null); setEditable(null); setErr(""); setImportedMsg(""); setImportedPoId(null); };

  // ---------- Accurate ----------
  const doAccuratePreview = async (e) => {
    e?.preventDefault?.();
    resetPreview();
    if (!soNumber.trim()) { setErr("Isi nomor Sales Order / PO terlebih dahulu."); return; }
    setBusy(true);
    try {
      const d = await api.accurateImportPreview(soNumber.trim());
      setPreview({ ...d, source: "accurate" });
    } catch (ex) { setErr(ex.response?.data?.detail || "Gagal mengambil preview"); }
    finally { setBusy(false); }
  };
  const doAccurateConfirm = async () => {
    setBusy(true); setErr(""); setImportedMsg("");
    try {
      const d = await api.accurateImportConfirm(preview.preview.po_number || soNumber.trim());
      setImportedMsg(d.message);
      setPreview(null); setSoNumber("");
      await loadLogs();
      setTimeout(() => navigate(`/po/${d.order.id}`), 800);
    } catch (ex) {
      const detail = ex.response?.data?.detail;
      if (typeof detail === "object" && detail?.message) {
        setErr(detail.message + (detail.existing_po_number ? ` (${detail.existing_po_number})` : ""));
      } else { setErr(detail || "Import gagal"); }
      await loadLogs();
    } finally { setBusy(false); }
  };

  // Trigger preview via row/click — sets input and reuses existing flow
  const doPreviewByNumber = async (soNumberValue) => {
    setSoNumber(soNumberValue);
    resetPreview();
    setBusy(true);
    try {
      const d = await api.accurateImportPreview(soNumberValue);
      setPreview({ ...d, source: "accurate" });
      // Scroll preview into view
      setTimeout(() => {
        const el = document.querySelector('[data-testid="preview-panel"]');
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 60);
    } catch (ex) { setErr(ex.response?.data?.detail || "Gagal mengambil preview"); }
    finally { setBusy(false); }
  };

  // ---------- Bulk (multi-select) ----------
  const doPreviewSelected = async () => {
    if (selected.size === 0) return;
    setBulkBusy(true);
    setErr("");
    setBulkResult(null);
    try {
      const d = await api.accurateImportPreviewBulk(Array.from(selected));
      setBulkPreview(d);
    } catch (ex) {
      const detail = ex.response?.data?.detail;
      setErr(typeof detail === "string" ? detail : (detail?.message || "Gagal preview bulk"));
    } finally {
      setBulkBusy(false);
    }
  };
  const doConfirmSelected = async () => {
    if (!bulkPreview) return;
    const numbers = bulkPreview.items.filter((i) => i.ok && !i.duplicate).map((i) => i.so_number);
    if (numbers.length === 0) return;
    setBulkBusy(true);
    setErr("");
    try {
      const d = await api.accurateImportConfirmBulk(numbers);
      setBulkResult(d);
      // Refresh list to reflect imported badges + logs
      await loadRecent({ refresh: true });
      await loadLogs();
      clearSelection();
    } catch (ex) {
      const detail = ex.response?.data?.detail;
      setErr(typeof detail === "string" ? detail : (detail?.message || "Import bulk gagal"));
    } finally {
      setBulkBusy(false);
    }
  };
  const closeBulk = () => { setBulkPreview(null); setBulkResult(null); };

  // ---------- File ----------
  const pickFile = (f) => {
    resetPreview();
    if (!f) { setFile(null); return; }
    const ok = /\.(xlsx|csv|txt)$/i.test(f.name);
    if (!ok) { setErr("Format tidak didukung. Gunakan .xlsx atau .csv."); return; }
    setFile(f);
  };
  const onFileChange = (e) => pickFile(e.target.files?.[0]);
  const onDrop = (e) => { e.preventDefault(); pickFile(e.dataTransfer.files?.[0]); };
  const doFilePreview = async () => {
    resetPreview();
    if (!file) { setErr("Pilih file XLSX atau CSV terlebih dahulu."); return; }
    setBusy(true);
    try {
      const d = await api.importFilePreview(file);
      setPreview({ ...d, source: "file" });
    } catch (ex) { setErr(ex.response?.data?.detail || "Gagal parsing file"); }
    finally { setBusy(false); }
  };
  const doFileConfirm = async () => {
    if (!preview) return;
    setBusy(true); setErr(""); setImportedMsg("");
    try {
      const d = await api.importFileConfirm(preview.preview);
      setImportedMsg(d.message);
      setPreview(null); setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      await loadLogs();
      setTimeout(() => navigate(`/po/${d.order.id}`), 800);
    } catch (ex) {
      const detail = ex.response?.data?.detail;
      if (typeof detail === "object" && detail?.message) {
        setErr(detail.message + (detail.existing_po_number ? ` (${detail.existing_po_number})` : ""));
      } else { setErr(detail || "Import gagal"); }
      await loadLogs();
    } finally { setBusy(false); }
  };

  // ---------- PDF ----------
  const pickPdf = (f) => {
    resetPreview();
    if (!f) { setPdfFile(null); return; }
    if (!/\.pdf$/i.test(f.name)) { setErr("Format tidak didukung. Hanya PDF."); return; }
    if (f.size > 8 * 1024 * 1024) { setErr("Ukuran file maksimum 8 MB."); return; }
    setPdfFile(f);
  };
  const onPdfChange = (e) => pickPdf(e.target.files?.[0]);
  const onPdfDrop = (e) => { e.preventDefault(); pickPdf(e.dataTransfer.files?.[0]); };
  const doPdfPreview = async () => {
    resetPreview();
    if (!pdfFile) { setErr("Pilih file PDF terlebih dahulu."); return; }
    setBusy(true);
    try {
      const d = await api.importPdfPreview(pdfFile);
      setPreview({ ...d, source: "pdf" });
      setEditable(JSON.parse(JSON.stringify(d.preview)));
    } catch (ex) { setErr(ex.response?.data?.detail || "Gagal parsing PDF"); }
    finally { setBusy(false); }
  };
  const validatePdfPreview = (p) => {
    if (!(p.po_number || "").trim()) return "Nomor PO wajib diisi.";
    if (!(p.customer || "").trim()) return "Customer wajib diisi.";
    if (!p.line_items?.length) return "Minimal 1 line item.";
    for (let i = 0; i < p.line_items.length; i++) {
      const li = p.line_items[i];
      if (!(li.name || "").trim()) return `Line item #${i + 1}: nama barang wajib.`;
      const q = Number(li.quantity);
      if (!Number.isFinite(q) || q <= 0) return `Line item #${i + 1}: quantity harus > 0.`;
    }
    return null;
  };
  const doPdfConfirm = async () => {
    if (!editable) return;
    const vErr = validatePdfPreview(editable);
    if (vErr) { setErr(vErr); return; }
    setBusy(true); setErr(""); setImportedMsg("");
    try {
      const cleaned = { ...editable };
      delete cleaned._parse_confidence;
      cleaned.line_items = cleaned.line_items.map((li) => ({ ...li, quantity: Number(li.quantity) }));
      const d = await api.importFileConfirm(cleaned, "pdf");
      setImportedMsg(d.message);
      setImportedPoId(d.order.id);
      setPreview(null); setEditable(null); setPdfFile(null);
      if (pdfRef.current) pdfRef.current.value = "";
      await loadLogs();
    } catch (ex) {
      const detail = ex.response?.data?.detail;
      if (typeof detail === "object" && detail?.message) {
        setErr(detail.message + (detail.existing_po_number ? ` (${detail.existing_po_number})` : ""));
      } else { setErr(detail || "Import gagal"); }
      await loadLogs();
    } finally { setBusy(false); }
  };
  const updateField = (k, v) => setEditable((p) => ({ ...p, [k]: v }));
  const updateItem = (i, k, v) => setEditable((p) => {
    const items = [...p.line_items];
    items[i] = { ...items[i], [k]: k === "quantity" ? Number(v) || 0 : v };
    return { ...p, line_items: items };
  });
  const addItem = () => setEditable((p) => ({ ...p, line_items: [...(p.line_items || []), { item_code: "", name: "", quantity: 1, unit: "pcs", color: "", notes: "" }] }));
  const removeItem = (i) => setEditable((p) => ({ ...p, line_items: p.line_items.filter((_, idx) => idx !== i) }));

  const isDup = !!preview?.duplicate;

  return (
    <div className="import-page" data-testid="import-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Admin · Integrasi &amp; Import PO</p>
          <h1 data-testid="import-title">Import PO</h1>
          <p className="subhead">Dari Accurate Online (read-only) atau dari file XLSX / CSV</p>
        </div>
        <button className="icon-button" onClick={() => { loadStatus(); loadAvailable(); loadLogs(); }} data-testid="import-refresh-button" aria-label="Muat ulang">
          <RefreshCw size={19} className={busy ? "spin" : ""} />
        </button>
      </div>

      {status && (
        <section className="accurate-banner" data-testid="import-status-banner">
          <div>
            <Cloud size={16} />
            <div>
              <strong>Accurate mode: {status.mode === "live" ? "LIVE" : "MOCK"}</strong>
              <p>{status.message}</p>
            </div>
          </div>
          <span className="pill" data-testid="import-readonly-badge"><Shield size={12} /> READ-ONLY</span>
        </section>
      )}

      {/* Mode tabs — only show tabs the user has permission for */}
      <div className="import-tabs" role="tablist">
        {canAccurate && (
          <button role="tab" className={mode === "accurate" ? "active" : ""} onClick={() => { setMode("accurate"); resetPreview(); }} data-testid="import-mode-accurate">
            <Cloud size={14}/> Accurate Online
          </button>
        )}
        {canSpreadsheet && (
          <button role="tab" className={mode === "file" ? "active" : ""} onClick={() => { setMode("file"); resetPreview(); }} data-testid="import-mode-file">
            <FileSpreadsheet size={14}/> File XLSX / CSV
          </button>
        )}
        {canPdf && (
          <button role="tab" className={mode === "pdf" ? "active" : ""} onClick={() => { setMode("pdf"); resetPreview(); }} data-testid="import-mode-pdf">
            <FileText size={14}/> PDF PO
          </button>
        )}
      </div>

      {!canAccurate && !canSpreadsheet && !canPdf && (
        <div className="analytics-note" data-testid="import-no-source-perm" style={{marginTop:12,padding:"12px 14px",background:"#fef3c7",borderRadius:8,color:"#92400e"}}>
          <AlertCircle size={14}/> Anda tidak memiliki izin untuk menggunakan sumber import PO manapun. Hubungi Admin untuk mengaktifkan izin di Pengaturan → Role &amp; Hak Akses.
        </div>
      )}

      {mode === "accurate" && canAccurate && (
        <section className="panel" data-testid="search-panel">
          <header className="panel-head"><h3>Cari Sales Order Accurate</h3></header>
          <form className="import-search" onSubmit={doAccuratePreview}>
            <div className="search-row">
              <Search size={16} />
              <input type="text" value={soNumber} onChange={(e) => setSoNumber(e.target.value)}
                placeholder="Ketik nomor Sales Order / PO Accurate (mis. PO-2001)"
                data-testid="so-number-input" disabled={busy} autoFocus />
              <button type="submit" className="primary" disabled={busy || !soNumber.trim()} data-testid="preview-button">
                Preview <ArrowRight size={14} />
              </button>
            </div>
          </form>
          {status?.mode === "mock" && available.length > 0 && (
            <div className="mock-suggestions" data-testid="mock-suggestions">
              <p className="muted small">Contoh SO tersedia di mode mock:</p>
              <div className="suggestion-chips">
                {available.map((s) => (
                  <button key={s.accurate_id} type="button" className="ghost sm"
                    onClick={() => setSoNumber(s.po_number)} data-testid={`suggest-${s.po_number}`}
                  >{s.po_number} · {s.customer}</button>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* Recent SO list (auto-loaded on tab open) */}
      {mode === "accurate" && canAccurate && (
        <section className="panel" data-testid="recent-so-panel">
          <header className="panel-head" style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:12,flexWrap:"wrap"}}>
            <div>
              <h3 style={{margin:0}}>Sales Order dari Accurate</h3>
              {recentList && !recentList.error && (
                <p className="muted small" data-testid="recent-so-total" style={{margin:"3px 0 0"}}>
                  {recentList.total ? `${recentList.total} Sales Order` : "0 Sales Order"} · halaman {recentList.page || 1} dari {recentList.pageCount || 1}
                  {recentList.cached && recentList.stale ? " · data cache (Accurate sementara tidak tersedia)" : recentList.cached ? " · cache" : ""}
                </p>
              )}
            </div>
            <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
              <form onSubmit={submitSearch} className="search-row" style={{margin:0,minWidth:260}}>
                <Search size={14} />
                <input
                  type="text"
                  value={keywordInput}
                  onChange={(e) => setKeywordInput(e.target.value)}
                  placeholder="Cari nomor SO / Customer"
                  data-testid="recent-so-search-input"
                  disabled={recentLoading}
                />
                {keyword && (
                  <button type="button" className="ghost sm" onClick={clearSearch} data-testid="recent-so-search-clear" title="Bersihkan pencarian">
                    <X size={12}/>
                  </button>
                )}
                <button type="submit" className="ghost sm" data-testid="recent-so-search-submit" disabled={recentLoading}>Cari</button>
              </form>
              <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }} data-testid="recent-so-page-size" disabled={recentLoading}>
                <option value={20}>20 / halaman</option>
                <option value={50}>50 / halaman</option>
                <option value={100}>100 / halaman</option>
              </select>
              <button className="ghost sm" onClick={() => loadRecent({ refresh: true })} disabled={recentLoading} data-testid="recent-so-refresh">
                <RefreshCw size={13} className={recentLoading ? "spin" : ""}/> Refresh
              </button>
            </div>
          </header>

          {selected.size > 0 && canAccurateBulk && (
            <div className="bulk-bar" data-testid="recent-so-bulk-bar" style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:12,padding:"10px 18px",borderBottom:"1px solid var(--border, #e5e7eb)",background:"var(--surface-2, #f9fafb)"}}>
              <span data-testid="recent-so-selected-count"><strong>{selected.size}</strong> Sales Order dipilih</span>
              <div style={{display:"flex",gap:8}}>
                <button className="ghost sm" onClick={clearSelection} data-testid="recent-so-clear-selection">Batalkan</button>
                <button className="primary" onClick={doPreviewSelected} disabled={bulkBusy} data-testid="recent-so-preview-selected">
                  Preview {selected.size} Terpilih <ArrowRight size={13}/>
                </button>
              </div>
            </div>
          )}

          <div style={{padding:"12px 18px"}}>
            {recentErr && (
              <div className="error" data-testid="recent-so-error" style={{marginBottom:10}}>
                {recentErr} <span className="muted small">— Anda tetap bisa mencari nomor SO tertentu di atas.</span>
              </div>
            )}
            {recentLoading && !recentList && <p className="muted small" data-testid="recent-so-loading">Memuat daftar dari Accurate...</p>}
            {recentList && recentList.rows && recentList.rows.length > 0 && (
              <div className="recent-so-table-wrap" data-testid="recent-so-table">
                <table className="recent-so-table">
                  <thead>
                    <tr>
                      {canAccurateBulk && (
                        <th style={{width:32}}>
                          <input
                            type="checkbox"
                            data-testid="recent-so-select-all"
                            checked={(() => {
                              const rows = recentList.rows || [];
                              const selectable = rows.filter((r) => r.import_status !== "imported").map((r) => r.number);
                              return selectable.length > 0 && selectable.every((n) => selected.has(n));
                            })()}
                            onChange={toggleSelectAll}
                          />
                        </th>
                      )}
                      <th>Nomor SO</th>
                      <th>Tanggal</th>
                      <th>Customer</th>
                      <th className="num">Total</th>
                      <th>Status</th>
                      <th>Status Import</th>
                      <th className="num">Aksi</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentList.rows.map((r) => {
                      const imported = r.import_status === "imported";
                      return (
                      <tr key={r.accurate_id || r.number}
                          data-testid={`recent-so-row-${r.number}`}
                          className={imported ? "" : "clickable-row"}
                          onClick={() => { if (!imported) doPreviewByNumber(r.number); }}>
                        {canAccurateBulk && (
                          <td onClick={(e) => e.stopPropagation()}>
                            <input
                              type="checkbox"
                              data-testid={`recent-so-check-${r.number}`}
                              checked={selected.has(r.number)}
                              onChange={() => toggleSelected(r.number)}
                              disabled={imported}
                              aria-label={imported ? "Sudah diimport, tidak bisa dipilih" : `Pilih ${r.number}`}
                            />
                          </td>
                        )}
                        <td className="mono"><strong>{r.number}</strong></td>
                        <td className="mono small">{r.transDate || "-"}</td>
                        <td>{r.customer || "-"}</td>
                        <td className="num">{r.totalAmount != null ? `Rp ${Number(r.totalAmount).toLocaleString("id-ID")}` : "-"}</td>
                        <td>{r.statusName ? <span className="pill sm">{r.statusName}</span> : <span className="muted small">-</span>}</td>
                        <td>
                          {imported ? (
                            <span className="pill sm" data-testid={`recent-so-imported-${r.number}`} style={{background:"#d1fae5",color:"#065f46"}}>
                              <CheckCircle2 size={11}/> Sudah Diimport
                            </span>
                          ) : (
                            <span className="pill sm" data-testid={`recent-so-notimported-${r.number}`} style={{background:"#f3f4f6",color:"#6b7280"}}>Belum Diimport</span>
                          )}
                        </td>
                        <td className="num" onClick={(e) => e.stopPropagation()}>
                          {imported ? (
                            <Link to={`/po/${r.existing_po_id}`} className="ghost sm" data-testid={`recent-so-view-${r.number}`}>
                              Lihat PO <ArrowRight size={12}/>
                            </Link>
                          ) : (
                            <button className="ghost sm" onClick={() => doPreviewByNumber(r.number)} data-testid={`recent-so-preview-${r.number}`} disabled={busy}>
                              Preview <ArrowRight size={12}/>
                            </button>
                          )}
                        </td>
                      </tr>
                    );})}
                  </tbody>
                </table>
              </div>
            )}
            {recentList && recentList.rows && recentList.rows.length === 0 && !recentErr && (
              <p className="muted small" data-testid="recent-so-empty">Tidak ada Sales Order ditemukan{keyword ? ` untuk "${keyword}"` : ""}.</p>
            )}

            {recentList && recentList.pageCount > 1 && (
              <div className="pagination" data-testid="recent-so-pagination" style={{display:"flex",justifyContent:"center",alignItems:"center",gap:12,marginTop:14}}>
                <button className="ghost sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={recentLoading || (recentList.page || 1) <= 1} data-testid="recent-so-prev">
                  ← Sebelumnya
                </button>
                <span data-testid="recent-so-page-indicator" className="muted small">
                  Halaman <strong>{recentList.page || 1}</strong> dari <strong>{recentList.pageCount || 1}</strong>
                </span>
                <button className="ghost sm" onClick={() => setPage((p) => Math.min(recentList.pageCount || 1, p + 1))} disabled={recentLoading || (recentList.page || 1) >= (recentList.pageCount || 1)} data-testid="recent-so-next">
                  Berikutnya →
                </button>
              </div>
            )}
          </div>
        </section>
      )}

      {mode === "file" && canSpreadsheet && (
        <section className="panel" data-testid="file-panel">
          <header className="panel-head">
            <h3>Upload File PO (XLSX / CSV)</h3>
            <button type="button" className="ghost sm" onClick={downloadTemplate} data-testid="file-template-download">
              <Download size={13}/> Unduh Template
            </button>
          </header>
          <div className="file-dropzone"
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop}
            data-testid="file-dropzone">
            <Upload size={28}/>
            {file ? (
              <>
                <strong data-testid="file-picked-name">{file.name}</strong>
                <span className="muted small">{(file.size / 1024).toFixed(1)} KB</span>
                <div className="file-actions">
                  <button className="primary" onClick={doFilePreview} disabled={busy} data-testid="file-preview-button"><ArrowRight size={13}/> Preview</button>
                  <button className="ghost sm" onClick={() => { pickFile(null); if (fileRef.current) fileRef.current.value = ""; }} data-testid="file-clear"><X size={13}/> Hapus</button>
                </div>
              </>
            ) : (
              <>
                <strong>Drag &amp; drop file di sini</strong>
                <span className="muted small">atau pilih file XLSX / CSV dari komputer (maks 5 MB)</span>
                <button className="ghost" onClick={() => fileRef.current?.click()} data-testid="file-pick-button">Pilih file</button>
              </>
            )}
            <input ref={fileRef} type="file" accept=".xlsx,.csv,.txt" onChange={onFileChange} style={{ display: "none" }} data-testid="file-input"/>
          </div>
          <p className="muted small">Kolom wajib: <b>po_number</b>, <b>customer</b>, <b>item_name</b> (atau item_code), <b>quantity</b>. File hanya boleh berisi <b>satu</b> PO (baris ganda dipakai untuk multi line item).</p>
        </section>
      )}

      {mode === "pdf" && canPdf && (
        <section className="panel" data-testid="pdf-panel">
          <header className="panel-head">
            <h3>Upload PDF PO</h3>
            <span className="muted small">Membaca text layer PDF (tanpa OCR)</span>
          </header>
          <div className="file-dropzone"
            onDragOver={(e) => e.preventDefault()}
            onDrop={onPdfDrop}
            data-testid="pdf-dropzone">
            <Upload size={28}/>
            {pdfFile ? (
              <>
                <strong data-testid="pdf-picked-name">{pdfFile.name}</strong>
                <span className="muted small">{(pdfFile.size / 1024).toFixed(1)} KB</span>
                <div className="file-actions">
                  <button className="primary" onClick={doPdfPreview} disabled={busy} data-testid="pdf-preview-button"><ArrowRight size={13}/> Preview</button>
                  <button className="ghost sm" onClick={() => { pickPdf(null); if (pdfRef.current) pdfRef.current.value = ""; }} data-testid="pdf-clear"><X size={13}/> Hapus</button>
                </div>
              </>
            ) : (
              <>
                <strong>Drag &amp; drop PDF di sini</strong>
                <span className="muted small">atau pilih 1 file PDF PO (maks 8 MB, text-based)</span>
                <button className="ghost" onClick={() => pdfRef.current?.click()} data-testid="pdf-pick-button">Pilih file PDF</button>
              </>
            )}
            <input ref={pdfRef} type="file" accept=".pdf,application/pdf" onChange={onPdfChange} style={{ display: "none" }} data-testid="pdf-input"/>
          </div>
          <p className="muted small">PDF scan/image tanpa text layer akan ditolak. Field yang tidak terbaca akan ditandai <em>Perlu verifikasi</em> — Anda dapat mengoreksinya sebelum import.</p>
        </section>
      )}

      {err && <div className="error" data-testid="import-error">{err}</div>}
      {importedMsg && (
        <div className="import-ok" data-testid="import-ok">
          <CheckCircle2 size={14} /> {importedMsg}
          {importedPoId && (
            <Link to={`/po/${importedPoId}`} className="ghost sm" data-testid="view-imported-po" style={{ marginLeft: 12 }}>
              Lihat Detail PO <ArrowRight size={13}/>
            </Link>
          )}
        </div>
      )}

      {/* PDF Editable Preview */}
      {preview?.source === "pdf" && editable && (
        <section className="panel pdf-preview-panel" data-testid="pdf-preview-panel">
          <header className="panel-head">
            <h3>Preview Import PO (dari PDF · dapat dikoreksi)</h3>
            <div className="preview-actions">
              {preview.duplicate ? (
                <>
                  <span className="urgent-badge" data-testid="preview-duplicate-badge">PO SUDAH PERNAH DIIMPORT</span>
                  {preview.existing_po_id && (
                    <Link to={`/po/${preview.existing_po_id}`} className="ghost sm" data-testid="view-existing-po">
                      Buka PO yang ada <ArrowRight size={14} />
                    </Link>
                  )}
                </>
              ) : (
                <>
                  <button className="ghost" onClick={resetPreview} disabled={busy} data-testid="pdf-cancel"><X size={14}/> Batal</button>
                  <button className="primary" onClick={doPdfConfirm} disabled={busy} data-testid="import-confirm-button"><Download size={14}/> Import PO</button>
                </>
              )}
            </div>
          </header>

          {preview.duplicate && preview.previous_imported_by && (
            <div className="analytics-note" data-testid="dup-details">
              <AlertCircle size={14}/>
              <p>
                PO <b>{preview.existing_po_number}</b> sebelumnya diimport oleh <b>{preview.previous_imported_by}</b>
                {preview.previous_imported_at && <> pada {formatDate(preview.previous_imported_at)}</>}
                {preview.previous_customer && <> untuk <b>{preview.previous_customer}</b></>}.
              </p>
            </div>
          )}

          <div className="pdf-form-grid">
            <label>Nomor PO <span className="req">*</span>
              <input value={editable.po_number || ""} onChange={(e) => updateField("po_number", e.target.value)} data-testid="pdf-field-po_number" placeholder="Perlu verifikasi" required/>
            </label>
            <label>Customer <span className="req">*</span>
              <input value={editable.customer || ""} onChange={(e) => updateField("customer", e.target.value)} data-testid="pdf-field-customer" placeholder="Perlu verifikasi" required/>
            </label>
            <label>Tanggal PO
              <input type="date" value={(editable.order_date || "").slice(0, 10)} onChange={(e) => updateField("order_date", e.target.value ? new Date(e.target.value).toISOString() : "")} data-testid="pdf-field-order_date"/>
            </label>
            <label>Estimasi kirim
              <input type="date" value={(editable.estimated_delivery || "").slice(0, 10)} onChange={(e) => updateField("estimated_delivery", e.target.value ? new Date(e.target.value).toISOString() : "")} data-testid="pdf-field-estimated_delivery"/>
            </label>
            <label>Outlet
              <input value={editable.outlet || ""} onChange={(e) => updateField("outlet", e.target.value)} data-testid="pdf-field-outlet" placeholder="Perlu verifikasi"/>
            </label>
            <label>Penerima
              <input value={editable.recipient || ""} onChange={(e) => updateField("recipient", e.target.value)} data-testid="pdf-field-recipient" placeholder="Perlu verifikasi"/>
            </label>
            <label className="span-2">Alamat
              <textarea rows="2" value={editable.address || ""} onChange={(e) => updateField("address", e.target.value)} data-testid="pdf-field-address" placeholder="Perlu verifikasi"/>
            </label>
            <label>Syarat pembayaran
              <input value={editable.payment_terms || ""} onChange={(e) => updateField("payment_terms", e.target.value)} data-testid="pdf-field-payment_terms" placeholder="Perlu verifikasi"/>
            </label>
            <label>Ekspedisi
              <input value={editable.expedition || ""} onChange={(e) => updateField("expedition", e.target.value)} data-testid="pdf-field-expedition" placeholder="Perlu verifikasi"/>
            </label>
            <label>Zona kirim
              <input value={editable.delivery_zone || ""} onChange={(e) => updateField("delivery_zone", e.target.value)} data-testid="pdf-field-delivery_zone" placeholder="Perlu verifikasi"/>
            </label>
            <label>Sales / Penjual
              <input value={editable.sales || ""} onChange={(e) => updateField("sales", e.target.value)} data-testid="pdf-field-sales" placeholder="Perlu verifikasi"/>
            </label>
            <label>User / Pembuat PO
              <input value={editable.created_by_user || ""} onChange={(e) => updateField("created_by_user", e.target.value)} data-testid="pdf-field-created_by_user" placeholder="Perlu verifikasi"/>
            </label>
            <label className="span-2">Catatan / Keterangan
              <textarea rows="2" value={editable.notes || ""} onChange={(e) => updateField("notes", e.target.value)} data-testid="pdf-field-notes" placeholder="(kosong)"/>
            </label>
          </div>

          <div className="pdf-items">
            <div className="pdf-items-head">
              <span>Line Item ({editable.line_items?.length || 0})</span>
              <button type="button" className="ghost sm" onClick={addItem} data-testid="pdf-add-item"><Plus size={12}/> Tambah item</button>
            </div>
            <div className="pdf-items-thead">
              <span>Kode</span><span>Nama barang *</span><span>Qty *</span><span>Satuan</span><span>Warna</span><span>Catatan</span><span></span>
            </div>
            {(editable.line_items || []).map((li, i) => (
              <div key={i} className="pdf-item-row" data-testid={`pdf-item-${i}`}>
                <input value={li.item_code || ""} onChange={(e) => updateItem(i, "item_code", e.target.value)} data-testid={`pdf-item-${i}-code`} placeholder="-"/>
                <input value={li.name || ""} onChange={(e) => updateItem(i, "name", e.target.value)} data-testid={`pdf-item-${i}-name`} required/>
                <input type="number" min="1" value={li.quantity || ""} onChange={(e) => updateItem(i, "quantity", e.target.value)} data-testid={`pdf-item-${i}-qty`} required/>
                <input value={li.unit || "pcs"} onChange={(e) => updateItem(i, "unit", e.target.value)} data-testid={`pdf-item-${i}-unit`}/>
                <input value={li.color || ""} onChange={(e) => updateItem(i, "color", e.target.value)} data-testid={`pdf-item-${i}-color`} placeholder="-"/>
                <input value={li.notes || ""} onChange={(e) => updateItem(i, "notes", e.target.value)} data-testid={`pdf-item-${i}-notes`} placeholder="-"/>
                <button type="button" className="ghost sm danger" onClick={() => removeItem(i)} data-testid={`pdf-item-${i}-remove`} title="Hapus"><Trash2 size={12}/></button>
              </div>
            ))}
            <div className="pdf-total muted small" data-testid="pdf-total">
              Total kuantitas: <b>{(editable.line_items || []).reduce((s, li) => s + (Number(li.quantity) || 0), 0).toLocaleString("id-ID")}</b> · Jumlah jenis barang: <b>{editable.line_items?.length || 0}</b>
            </div>
          </div>

          <p className="muted small">Setelah Import PO ditekan, PO akan masuk workflow standar mulai dari Order Masuk. File PDF asli akan disimpan sebagai attachment PO untuk keperluan audit.</p>
        </section>
      )}

      {preview && preview.source !== "pdf" && (
        <section className="panel" data-testid="preview-panel">
          <header className="panel-head">
            <h3>Preview Import PO {preview.source === "file" ? "(dari File)" : "(dari Accurate)"}</h3>
            <div className="preview-actions">
              {isDup ? (
                <>
                  <span className="urgent-badge" data-testid="preview-duplicate-badge">PO SUDAH PERNAH DIIMPORT</span>
                  {preview.existing_po_id && (
                    <Link to={`/po/${preview.existing_po_id}`} className="ghost sm" data-testid="view-existing-po">
                      Buka PO yang ada <ArrowRight size={14} />
                    </Link>
                  )}
                </>
              ) : (
                <button className="primary" onClick={preview.source === "file" ? doFileConfirm : doAccurateConfirm} disabled={busy} data-testid="import-confirm-button">
                  <Download size={14} /> Import PO
                </button>
              )}
            </div>
          </header>

          <div className="info-grid">
            {preview.preview.accurate_id && <div><span className="lbl">Accurate ID</span><strong className="mono">{preview.preview.accurate_id}</strong></div>}
            <div><span className="lbl">Nomor PO / SO</span><strong className="mono">{preview.preview.po_number}</strong></div>
            <div><span className="lbl">Tanggal PO</span><strong>{formatDate(preview.preview.order_date)}</strong></div>
            <div><span className="lbl">Customer</span><strong>{preview.preview.customer || "-"}</strong></div>
            <div><span className="lbl">Outlet</span><strong>{preview.preview.outlet || "-"}</strong></div>
            <div><span className="lbl">Penerima</span><strong>{preview.preview.recipient || "-"}</strong></div>
            <div className="span-2"><span className="lbl">Alamat</span><strong>{preview.preview.address || "-"}</strong></div>
            <div>
              <span className="lbl">Sales <small className="muted">(tenaga penjual)</small></span>
              <strong data-testid="preview-sales">
                {preview.preview.sales || <em className="muted">Perlu verifikasi</em>}
              </strong>
            </div>
            <div>
              <span className="lbl">User / Pembuat PO</span>
              <strong data-testid="preview-created-by-user">
                {preview.preview.created_by_user || <em className="muted">Perlu verifikasi</em>}
              </strong>
            </div>
            <div><span className="lbl">Ekspedisi</span><strong>{preview.preview.expedition || "-"}</strong></div>
            <div><span className="lbl">Zona kirim</span><strong>{preview.preview.delivery_zone || "-"}</strong></div>
            <div><span className="lbl">Termin bayar</span><strong>{preview.preview.payment_terms || "-"}</strong></div>
            <div><span className="lbl">Estimasi kirim</span><strong>{formatDeadline(preview.preview.estimated_delivery)}</strong></div>
            <div><span className="lbl">Total kuantitas</span><strong className="mono">{(preview.preview.total_quantity || 0).toLocaleString("id-ID")} pcs</strong></div>
          </div>

          <div className="preview-items" data-testid="preview-items">
            <div className="preview-items-head">Line Item ({preview.preview.item_count})</div>
            {preview.preview.line_items.map((li, i) => (
              <div key={i} className="preview-item" data-testid={`preview-item-${i}`}>
                <span className="mono">{li.item_code || "-"}</span>
                <strong>{li.name}</strong>
                <span className="mono">{Number(li.quantity).toLocaleString("id-ID")} {li.unit || "pcs"}</span>
                <span className="muted" data-testid={`preview-item-${i}-color`}>
                  Warna:{" "}
                  {li.color ? (
                    <strong>{li.color}</strong>
                  ) : li.color_needs_verify ? (
                    <em style={{color:"#b45309"}}>Perlu verifikasi{li.color_candidates?.length ? ` (${li.color_candidates.join(" / ")})` : ""}</em>
                  ) : "-"}
                </span>
                {li.notes && <p className="li-notes">Catatan: {li.notes}</p>}
              </div>
            ))}
          </div>

          <p className="muted small">Setelah Import PO ditekan, entitas ini masuk workflow standar Eternal (mulai dari Order Masuk). Data sumber tidak diubah.</p>
        </section>
      )}

      <section className="panel" data-testid="import-logs-panel">
        <header className="panel-head">
          <h3>Riwayat Import</h3>
          <span className="muted small">{logs.length} entri</span>
        </header>
        {logs.length === 0 ? (
          <p className="empty">Belum ada import.</p>
        ) : (
          <div className="log-table">
            <div className="log-head">
              <span>Waktu</span><span>SO / PO</span><span>Sumber</span><span>Admin</span><span>Status</span><span>Pesan</span>
            </div>
            {logs.map((l) => {
              const meta = statusMeta[l.status] || statusMeta.failed;
              const Icon = meta.icon;
              return (
                <div key={l.id} className={`log-row ${meta.cls}`} data-testid={`log-row-${l.id}`}>
                  <span className="mono">{formatDate(l.imported_at)}</span>
                  <span className="mono">{l.po_number || l.so_number}</span>
                  <span className="mono">{l.mode || "accurate"}</span>
                  <span>{l.imported_by_name || l.imported_by}</span>
                  <span><Icon size={12} /> {meta.label}</span>
                  <span className="muted">{l.message}</span>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Bulk preview modal */}
      {bulkPreview && !bulkResult && (
        <div className="modal-overlay" data-testid="bulk-preview-modal" onClick={closeBulk}
          style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.55)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:200,padding:16}}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}
            style={{background:"var(--surface,#fff)",borderRadius:12,maxWidth:900,width:"100%",maxHeight:"90vh",overflow:"auto",boxShadow:"0 20px 40px rgba(0,0,0,0.25)"}}>
            <header style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"16px 20px",borderBottom:"1px solid var(--border,#e5e7eb)"}}>
              <div>
                <h3 style={{margin:0}}>Preview Import Multi Sales Order</h3>
                <p className="muted small" style={{margin:"3px 0 0"}}>Data dari Accurate — tinjau sebelum konfirmasi.</p>
              </div>
              <button className="icon-button" onClick={closeBulk} data-testid="bulk-preview-close" aria-label="Tutup"><X size={18}/></button>
            </header>
            <div style={{padding:"16px 20px"}}>
              <div className="info-grid" data-testid="bulk-preview-summary" style={{marginBottom:14}}>
                <div><span className="lbl">Total dipilih</span><strong>{bulkPreview.summary.total}</strong></div>
                <div><span className="lbl">Siap diimport</span><strong style={{color:"#059669"}}>{bulkPreview.summary.ready}</strong></div>
                <div><span className="lbl">Duplikat</span><strong style={{color:"#b45309"}}>{bulkPreview.summary.duplicates}</strong></div>
                <div><span className="lbl">Gagal fetch</span><strong style={{color:"#dc2626"}}>{bulkPreview.summary.errors}</strong></div>
                <div><span className="lbl">Total qty</span><strong className="mono">{Number(bulkPreview.summary.total_qty).toLocaleString("id-ID")}</strong></div>
                <div><span className="lbl">Line item</span><strong className="mono">{bulkPreview.summary.total_items}</strong></div>
              </div>

              {bulkPreview.summary.needs_verify?.length > 0 && (
                <div className="analytics-note" data-testid="bulk-preview-verify-warning" style={{marginBottom:12,padding:"10px 12px",background:"#fef3c7",borderRadius:8,color:"#92400e"}}>
                  <AlertCircle size={14}/> {bulkPreview.summary.needs_verify.length} PO memiliki item warna yang perlu verifikasi setelah import.
                </div>
              )}

              <div className="log-table" data-testid="bulk-preview-items" style={{marginBottom:14}}>
                <div className="log-head" style={{gridTemplateColumns:"1.2fr 1.6fr 0.6fr 0.8fr 0.6fr 1fr"}}>
                  <span>Nomor SO</span><span>Customer</span><span>Qty</span><span>Item</span><span>Warna</span><span>Status</span>
                </div>
                {bulkPreview.items.map((it) => {
                  const p = it.preview || {};
                  const cls = it.duplicate ? "log-duplicate" : it.ok ? "log-success" : "log-failed";
                  const colorSummary = (() => {
                    const items = p.line_items || [];
                    if (items.length === 0) return "-";
                    const uniq = Array.from(new Set(items.map((li) => li.color || (li.color_needs_verify ? "?" : "-"))));
                    return uniq.join(" · ");
                  })();
                  return (
                    <div key={it.so_number} className={`log-row ${cls}`} data-testid={`bulk-preview-row-${it.so_number}`} style={{gridTemplateColumns:"1.2fr 1.6fr 0.6fr 0.8fr 0.6fr 1fr"}}>
                      <span className="mono"><strong>{it.so_number}</strong></span>
                      <span>{p.customer || "-"}</span>
                      <span className="mono">{p.total_quantity != null ? Number(p.total_quantity).toLocaleString("id-ID") : "-"}</span>
                      <span className="mono">{p.item_count || 0}</span>
                      <span className="mono small">{colorSummary}</span>
                      <span>
                        {!it.ok && <span className="muted small">Gagal: {it.error}</span>}
                        {it.ok && it.duplicate && (
                          <>Duplikat{it.existing_po_id && <> · <Link to={`/po/${it.existing_po_id}`} data-testid={`bulk-preview-existing-${it.so_number}`}>Lihat PO</Link></>}</>
                        )}
                        {it.ok && !it.duplicate && (it.has_verify ? "Siap · warna perlu verifikasi" : "Siap")}
                      </span>
                    </div>
                  );
                })}
              </div>

              <p className="muted small">Sales, User/Pembuat PO, dan Imported By disimpan sebagai field terpisah. Duplicate protection aktif — SO yang sudah pernah diimport tidak akan dibuat ulang.</p>
            </div>
            <footer style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"14px 20px",borderTop:"1px solid var(--border,#e5e7eb)"}}>
              <button className="ghost" onClick={closeBulk} data-testid="bulk-preview-cancel" disabled={bulkBusy}>
                <X size={13}/> Batal
              </button>
              <button
                className="primary"
                onClick={doConfirmSelected}
                disabled={bulkBusy || bulkPreview.summary.ready === 0}
                data-testid="bulk-preview-confirm">
                <Download size={13}/> Import {bulkPreview.summary.ready} PO
              </button>
            </footer>
          </div>
        </div>
      )}

      {/* Bulk result modal */}
      {bulkResult && (
        <div className="modal-overlay" data-testid="bulk-result-modal"
          style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.55)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:200,padding:16}}>
          <div className="modal-card"
            style={{background:"var(--surface,#fff)",borderRadius:12,maxWidth:700,width:"100%",maxHeight:"90vh",overflow:"auto",boxShadow:"0 20px 40px rgba(0,0,0,0.25)"}}>
            <header style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"16px 20px",borderBottom:"1px solid var(--border,#e5e7eb)"}}>
              <div>
                <h3 style={{margin:0}}>Hasil Import Bulk</h3>
                <p className="muted small" style={{margin:"3px 0 0"}} data-testid="bulk-result-summary">
                  {bulkResult.summary.imported} berhasil · {bulkResult.summary.duplicates} duplikat · {bulkResult.summary.failed} gagal
                </p>
              </div>
              <button className="icon-button" onClick={closeBulk} data-testid="bulk-result-close" aria-label="Tutup"><X size={18}/></button>
            </header>
            <div style={{padding:"16px 20px"}}>
              <div className="log-table" data-testid="bulk-result-items">
                <div className="log-head" style={{gridTemplateColumns:"1.5fr 0.8fr 1.5fr"}}>
                  <span>SO</span><span>Status</span><span>Detail</span>
                </div>
                {bulkResult.results.map((r) => {
                  const cls = r.status === "success" ? "log-success" : r.status === "duplicate" ? "log-duplicate" : "log-failed";
                  return (
                    <div key={r.so_number} className={`log-row ${cls}`} data-testid={`bulk-result-row-${r.so_number}`} style={{gridTemplateColumns:"1.5fr 0.8fr 1.5fr"}}>
                      <span className="mono"><strong>{r.so_number}</strong></span>
                      <span>{r.status === "success" ? "Berhasil" : r.status === "duplicate" ? "Duplikat" : "Gagal"}</span>
                      <span className="muted small">
                        {r.status === "success" && r.po_id && (<Link to={`/po/${r.po_id}`} data-testid={`bulk-result-view-${r.so_number}`}>Lihat PO {r.po_number} <ArrowRight size={11}/></Link>)}
                        {r.status === "duplicate" && r.existing_po_id && (<Link to={`/po/${r.existing_po_id}`} data-testid={`bulk-result-existing-${r.so_number}`}>PO existing {r.existing_po_number} <ArrowRight size={11}/></Link>)}
                        {r.status === "failed" && (r.message || "-")}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
            <footer style={{display:"flex",justifyContent:"flex-end",padding:"14px 20px",borderTop:"1px solid var(--border,#e5e7eb)"}}>
              <button className="primary" onClick={closeBulk} data-testid="bulk-result-done">Selesai</button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
