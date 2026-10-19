import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CalendarDays, Download, Filter, RefreshCw, RotateCcw, Search, X } from "lucide-react";
import PaintAdvancedTable from "../components/paint/PaintAdvancedTable";
import { encodeHeaderFilters } from "../components/paint/paintTableLayout";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatStatusPoDate, parseStatusPoDateInput } from "../lib/statusPo";
import "./SentralisasiCat.css";

const TABS = [
  { id: "ringkasan", label: "Ringkasan", need: ["paint.view", "paint.stock.view", "paint.report.view"] },
  { id: "transaksi", label: "Transaksi Cat", need: ["paint.view", "paint.transaction.create"] },
  { id: "stok", label: "Stok Cat", need: ["paint.stock.view", "paint.view"] },
  { id: "laporan", label: "Laporan Penggunaan Cat", need: ["paint.report.view"] },
];

const TX_LABEL = {
  OPENING: "Stok Awal",
  ADD: "Tambah Stok",
  TAKE: "Ambil",
  RETURN: "Kembali",
  ADJUST: "Koreksi",
};

function newKey() {
  return globalThis.crypto?.randomUUID?.() || `paint-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function fmtGrams(n) {
  const v = Number(n) || 0;
  return `${v.toLocaleString("id-ID")} gr`;
}

function fmtNum(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function tabAllowed(tab, hasPerm) {
  return (tab.need || []).some((p) => hasPerm(p));
}

function branchLabel(branches, branchId) {
  const row = (branches || []).find((b) => b.id === branchId);
  if (!row) return branchId || "—";
  return row.code || row.name || branchId;
}

function formatWib(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16).replace("T", " ");
    return new Intl.DateTimeFormat("id-ID", {
      timeZone: "Asia/Jakarta",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(d).replace(",", "");
  } catch {
    return String(iso).slice(0, 16).replace("T", " ");
  }
}

function employeeMeta(row) {
  return [row?.role_label || row?.role, row?.division].filter(Boolean).join(" · ");
}

function isoTodayWib() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Jakarta",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const y = parts.find((p) => p.type === "year")?.value;
  const m = parts.find((p) => p.type === "month")?.value;
  const d = parts.find((p) => p.type === "day")?.value;
  return `${y}-${m}-${d}`;
}

function shiftIsoDays(iso, delta) {
  const [y, m, d] = String(iso).split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + delta);
  return dt.toISOString().slice(0, 10);
}

function PaintDateInput({ value, onChange, testId, ariaLabel }) {
  const [text, setText] = useState(() => (value ? formatStatusPoDate(value) : ""));
  useEffect(() => {
    setText(value ? formatStatusPoDate(value) : "");
  }, [value]);
  return (
    <input
      type="text"
      inputMode="numeric"
      placeholder="dd/mm/yyyy"
      autoComplete="off"
      spellCheck={false}
      value={text === "-" ? "" : text}
      onChange={(e) => {
        const raw = e.target.value;
        setText(raw);
        if (!raw.trim()) {
          onChange("");
          return;
        }
        const iso = parseStatusPoDateInput(raw);
        if (iso) onChange(iso);
      }}
      onBlur={() => setText(value ? formatStatusPoDate(value) : "")}
      aria-label={ariaLabel}
      data-testid={testId}
    />
  );
}

function periodSummary(dateFrom, dateTo) {
  const a = dateFrom ? formatStatusPoDate(dateFrom) : "";
  const b = dateTo ? formatStatusPoDate(dateTo) : "";
  if (!a && !b) return "Semua tanggal";
  if (a && b) return `${a} – ${b}`;
  return a || b;
}

function PeriodRangeControl({ dateFrom, dateTo, onApply, onClearPreset }) {
  const [open, setOpen] = useState(false);
  const [draftFrom, setDraftFrom] = useState(dateFrom || "");
  const [draftTo, setDraftTo] = useState(dateTo || "");
  const wrapRef = useRef(null);

  useEffect(() => {
    if (open) {
      setDraftFrom(dateFrom || "");
      setDraftTo(dateTo || "");
    }
  }, [open, dateFrom, dateTo]);

  useEffect(() => {
    if (!open) return undefined;
    function onDoc(e) {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = Boolean(dateFrom || dateTo);
  return (
    <div className="paint-period" ref={wrapRef} data-testid="paint-period">
      <button
        type="button"
        className={`paint-period-btn${active ? " is-active" : ""}`}
        aria-label={`Periode ${periodSummary(dateFrom, dateTo)}`}
        aria-expanded={open}
        data-testid="paint-period-trigger"
        onClick={() => setOpen((v) => !v)}
      >
        <CalendarDays size={14} aria-hidden />
        <span className="paint-period-label">Periode:</span>
        <span className="paint-period-value">{periodSummary(dateFrom, dateTo)}</span>
      </button>
      {open && (
        <div className="paint-period-panel" role="dialog" aria-label="Pilih periode" data-testid="paint-period-panel">
          <label>
            Dari
            <PaintDateInput value={draftFrom} onChange={setDraftFrom} testId="paint-filter-from" ariaLabel="Tanggal dari" />
          </label>
          <label>
            Sampai
            <PaintDateInput value={draftTo} onChange={setDraftTo} testId="paint-filter-to" ariaLabel="Tanggal sampai" />
          </label>
          <div className="paint-period-actions">
            <button
              type="button"
              className="ghost sm"
              data-testid="paint-period-clear"
              onClick={() => {
                setDraftFrom("");
                setDraftTo("");
                onApply("", "");
                onClearPreset?.();
                setOpen(false);
              }}
            >
              Hapus
            </button>
            <button
              type="button"
              className="paint-btn-primary sm"
              data-testid="paint-period-apply"
              onClick={() => {
                onApply(draftFrom, draftTo);
                onClearPreset?.();
                setOpen(false);
              }}
            >
              Terapkan
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function FilterMorePopover({ open, onOpenChange, activeCount = 0, children, testId = "paint-more-filters" }) {
  const wrapRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    function onDoc(e) {
      if (!wrapRef.current?.contains(e.target)) onOpenChange(false);
    }
    function onKey(e) {
      if (e.key === "Escape") onOpenChange(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onOpenChange]);

  return (
    <div className="paint-more-wrap" ref={wrapRef}>
      <button
        type="button"
        className={`paint-more-btn${open || activeCount > 0 ? " is-active" : ""}`}
        data-testid={testId}
        aria-expanded={open}
        aria-label={activeCount > 0 ? `Filter lainnya (${activeCount} aktif)` : "Filter lainnya"}
        onClick={() => onOpenChange(!open)}
      >
        <Filter size={14} aria-hidden />
        Filter Lainnya
        {activeCount > 0 ? <span className="paint-more-count">{activeCount}</span> : null}
      </button>
      {open && (
        <div className="paint-more-panel" role="dialog" aria-label="Filter lainnya" data-testid={`${testId}-panel`}>
          {children}
        </div>
      )}
    </div>
  );
}

function DatePresets({ datePreset, onPreset, testId = "paint-date-presets" }) {
  return (
    <div className="paint-preset-row" data-testid={testId} role="group" aria-label="Preset tanggal">
      {[
        ["today", "Hari Ini"],
        ["week", "Minggu Ini"],
        ["month", "Bulan Ini"],
      ].map(([id, label]) => (
        <button
          key={id}
          type="button"
          className={`paint-preset${datePreset === id ? " is-active" : ""}`}
          onClick={() => onPreset(id)}
          data-testid={`paint-preset-${id}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function EmployeePicker({ value, onChange, disabled }) {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef(null);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setQ(qInput.trim()), 250);
    return () => clearTimeout(debounceRef.current);
  }, [qInput]);

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setLoading(true);
    api.paintEligibleEmployees({ q, page: 1, page_size: 40 })
      .then((res) => {
        if (!cancelled) setItems(res.items || []);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [q, open]);

  useEffect(() => {
    function onDoc(e) {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  return (
    <div className="paint-emp-picker" ref={wrapRef} data-testid="paint-employee-picker">
      {value ? (
        <div className="paint-emp-selected" data-testid="paint-employee-selected">
          <div>
            <strong>{value.name}</strong>
            <span className="paint-emp-meta">{employeeMeta(value) || "—"}</span>
          </div>
          {!disabled && (
            <button type="button" className="ghost sm" onClick={() => onChange(null)} data-testid="paint-employee-clear">
              <X size={14} />
            </button>
          )}
        </div>
      ) : (
        <>
          <div className="paint-search-wrap">
            <Search size={14} />
            <input
              type="text"
              value={qInput}
              disabled={disabled}
              placeholder="Cari nama karyawan…"
              data-testid="paint-employee-search"
              onFocus={() => setOpen(true)}
              onChange={(e) => {
                setQInput(e.target.value);
                setOpen(true);
              }}
            />
          </div>
          {open && (
            <div className="paint-emp-dropdown" data-testid="paint-employee-dropdown">
              {loading && <p className="muted small">Memuat…</p>}
              {!loading && items.length === 0 && <p className="muted small">Tidak ada karyawan eligible</p>}
              {!loading && items.map((row) => (
                <button
                  key={row.employee_user_id}
                  type="button"
                  className="paint-emp-option"
                  data-testid={`paint-emp-option-${row.employee_user_id}`}
                  onClick={() => {
                    onChange(row);
                    setOpen(false);
                    setQInput("");
                  }}
                >
                  <strong>{row.name}</strong>
                  <span className="paint-emp-meta">
                    {[employeeMeta(row), row.email].filter(Boolean).join(" · ")}
                  </span>
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function TxFormModal({ open, onClose, onSaved, paints, branches, defaults, canFlags }) {
  const [type, setType] = useState("TAKE");
  const [employee, setEmployee] = useState(null);
  const [branchId, setBranchId] = useState("");
  const [paintId, setPaintId] = useState("");
  const [qty, setQty] = useState("");
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const wasOpen = useRef(false);

  // Reset ONLY when modal opens (false → true). Never on filter/permission rerenders.
  useEffect(() => {
    if (open && !wasOpen.current) {
      const initialType = canFlags.create
        ? "TAKE"
        : canFlags.add
          ? "ADD"
          : canFlags.opening
            ? "OPENING"
            : "ADJUST";
      setType(initialType);
      setEmployee(null);
      setBranchId(defaults?.branchId || "");
      setPaintId("");
      setQty("");
      setReference("");
      setNotes("");
      setErr("");
    }
    wasOpen.current = open;
    // Intentionally depend only on `open` — reading defaults/canFlags at open edge.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const needsEmployee = type === "TAKE" || type === "RETURN";
  const typeOptions = [
    canFlags.opening && { value: "OPENING", label: "Stok Awal" },
    canFlags.add && { value: "ADD", label: "Tambah Stok" },
    canFlags.create && { value: "TAKE", label: "Ambil" },
    canFlags.create && { value: "RETURN", label: "Kembali" },
    canFlags.correct && { value: "ADJUST", label: "Koreksi" },
  ].filter(Boolean);

  async function submit(e) {
    e.preventDefault();
    setErr("");
    const quantity = Number(qty);
    if (!branchId) return setErr("Cabang wajib diisi");
    if (!paintId) return setErr("Kode/warna cat wajib dipilih");
    if (!quantity || (type !== "ADJUST" && quantity <= 0)) return setErr("Jumlah (gr) tidak valid");
    if (needsEmployee && !employee?.employee_user_id) return setErr("Nama karyawan wajib dipilih");
    if (type === "ADJUST" && !String(notes || "").trim()) return setErr("Alasan koreksi wajib diisi");

    const payload = {
      type,
      paint_id: paintId,
      branch_id: branchId,
      quantity_grams: quantity,
      notes,
      reference,
      employee_user_id: employee?.employee_user_id,
      idempotency_key: newKey(),
    };
    setSaving(true);
    try {
      if (type === "OPENING") await api.paintOpening(payload);
      else if (type === "ADD") await api.paintAdd(payload);
      else if (type === "TAKE") await api.paintTake(payload);
      else if (type === "RETURN") await api.paintReturn(payload);
      else await api.paintAdjust(payload);
      onSaved();
      onClose();
    } catch (ex) {
      setErr(ex?.response?.data?.detail || "Gagal menyimpan transaksi");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="paint-modal-backdrop" data-testid="paint-tx-modal">
      <form className="paint-modal" onSubmit={submit}>
        <header>
          <h2>Transaksi Cat</h2>
          <button type="button" className="ghost sm" onClick={onClose} data-testid="paint-tx-close"><X size={16} /></button>
        </header>
        {err && <div className="signal-strip" role="alert" data-testid="paint-tx-error">{err}</div>}
        <div className="paint-form-grid">
          <label>
            Jenis transaksi
            <select value={type} onChange={(e) => setType(e.target.value)} data-testid="paint-tx-type">
              {typeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label>
            Cabang
            <select value={branchId} onChange={(e) => setBranchId(e.target.value)} data-testid="paint-tx-branch">
              <option value="">Pilih cabang…</option>
              {(branches || []).filter((b) => b?.active !== false).map((b) => (
                <option key={b.id} value={b.id}>{b.code || b.name || b.id}</option>
              ))}
            </select>
          </label>
          <label>
            Kode / Warna
            <select value={paintId} onChange={(e) => setPaintId(e.target.value)} data-testid="paint-tx-paint">
              <option value="">Pilih cat…</option>
              {(paints || []).map((p) => (
                <option key={p.paint_id} value={p.paint_id}>{p.code} — {p.name}</option>
              ))}
            </select>
          </label>
          <label>
            Jumlah (gr)
            <input
              type="number"
              step="1"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              data-testid="paint-tx-qty"
            />
          </label>
          {needsEmployee && (
            <label className="paint-span-2">
              Nama Karyawan
              <span className="paint-help">Wajib untuk Ambil / Kembali · bukan free-text</span>
              <EmployeePicker value={employee} onChange={setEmployee} />
            </label>
          )}
          <label className="paint-span-2">
            Referensi PO / Produksi <span className="paint-help">opsional</span>
            <input value={reference} onChange={(e) => setReference(e.target.value)} data-testid="paint-tx-ref" />
          </label>
          <label className="paint-span-2">
            Catatan{type === "ADJUST" ? " (wajib)" : ""}
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} data-testid="paint-tx-notes" style={{ minHeight: 48 }} />
          </label>
        </div>
        <footer>
          <button type="button" className="ghost" onClick={onClose} data-testid="paint-tx-cancel">Batal</button>
          <button type="submit" className="paint-btn-primary" disabled={saving} data-testid="paint-tx-submit">
            {saving ? "Menyimpan…" : "Simpan"}
          </button>
        </footer>
      </form>
    </div>
  );
}

export default function SentralisasiCat() {
  const { hasPerm, user } = useAuth();
  const userId = user?.id || user?.email || "anon";
  const canFlags = useMemo(() => ({
    create: hasPerm("paint.transaction.create"),
    opening: hasPerm("paint.stock.opening"),
    add: hasPerm("paint.stock.add"),
    correct: hasPerm("paint.transaction.correct"),
  }), [hasPerm]);

  const allowedTabs = useMemo(() => TABS.filter((t) => tabAllowed(t, hasPerm)), [hasPerm]);
  const [tab, setTab] = useState(() => allowedTabs[0]?.id || "ringkasan");
  const [branches, setBranches] = useState([]);
  const [branchId, setBranchId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [datePreset, setDatePreset] = useState("");
  const [summary, setSummary] = useState(null);
  const [stock, setStock] = useState(null);
  const [txs, setTxs] = useState(null);
  const [report, setReport] = useState(null);
  const [paints, setPaints] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [txPage, setTxPage] = useState(1);
  const [stockPage, setStockPage] = useState(1);
  const [reportPage, setReportPage] = useState(1);
  const [txSort, setTxSort] = useState({ by: "created_at", dir: "desc" });
  const [txColFilters, setTxColFilters] = useState({});
  const [stockSort, setStockSort] = useState({ by: "code", dir: "asc" });
  const [stockColFilters, setStockColFilters] = useState({});
  const [reportSort, setReportSort] = useState({ by: "employee_name", dir: "asc" });
  const [reportColFilters, setReportColFilters] = useState({});
  const [recentSort, setRecentSort] = useState({ by: "created_at", dir: "desc" });
  const [recentColFilters, setRecentColFilters] = useState({});
  const [stockQ, setStockQ] = useState("");
  const [stockStatus, setStockStatus] = useState("");
  const [txQ, setTxQ] = useState("");
  const [txType, setTxType] = useState("");
  const [reportEmployee, setReportEmployee] = useState(null);
  const [reportPaintId, setReportPaintId] = useState("");
  const [masterOpen, setMasterOpen] = useState(false);
  const [masterCode, setMasterCode] = useState("");
  const [masterName, setMasterName] = useState("");
  const [masterMin, setMasterMin] = useState("0");
  const [alertExpanded, setAlertExpanded] = useState(false);
  const [moreFiltersOpen, setMoreFiltersOpen] = useState(false);
  const [draftReportPaintId, setDraftReportPaintId] = useState("");

  useEffect(() => {
    if (moreFiltersOpen) setDraftReportPaintId(reportPaintId || "");
  }, [moreFiltersOpen, reportPaintId]);

  const canCreateTx = canFlags.create || canFlags.opening || canFlags.add || canFlags.correct;
  const canExport = hasPerm("paint.report.export");
  const canManageMaster = hasPerm("paint.settings.manage");

  useEffect(() => {
    if (!allowedTabs.find((t) => t.id === tab) && allowedTabs[0]) {
      setTab(allowedTabs[0].id);
    }
  }, [allowedTabs, tab]);

  useEffect(() => {
    let cancelled = false;
    // Paint-scoped branches (paint.view) — do not depend on org.branches.view.
    api.paintBranches()
      .then((res) => {
        if (cancelled) return;
        const rows = res.items || res || [];
        setBranches(Array.isArray(rows) ? rows : []);
      })
      .catch(() => {
        if (!cancelled) setBranches([]);
      });
    return () => { cancelled = true; };
  }, []);

  const loadPaints = useCallback(() => {
    api.paintMaster({ active: true, page_size: 200 })
      .then((res) => setPaints(res.items || []))
      .catch(() => setPaints([]));
  }, []);

  useEffect(() => { loadPaints(); }, [loadPaints]);

  const applyPreset = useCallback((preset) => {
    const today = isoTodayWib();
    setDatePreset(preset);
    if (preset === "today") {
      setDateFrom(today);
      setDateTo(today);
    } else if (preset === "week") {
      setDateFrom(shiftIsoDays(today, -6));
      setDateTo(today);
    } else if (preset === "month") {
      setDateFrom(`${today.slice(0, 8)}01`);
      setDateTo(today);
    }
  }, []);

  const applyPeriod = useCallback((from, to) => {
    setDateFrom(from || "");
    setDateTo(to || "");
    setDatePreset("");
    setTxPage(1);
    setReportPage(1);
  }, []);

  const resetFilters = useCallback(() => {
    setBranchId("");
    setDateFrom("");
    setDateTo("");
    setDatePreset("");
    setTxQ("");
    setTxType("");
    setStockQ("");
    setStockStatus("");
    setReportEmployee(null);
    setReportPaintId("");
    setDraftReportPaintId("");
    setTxPage(1);
    setStockPage(1);
    setReportPage(1);
    setMoreFiltersOpen(false);
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setErr("");
    const params = {};
    if (branchId) params.branch_id = branchId;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    try {
      if (tab === "ringkasan") {
        setSummary(await api.paintSummary(params));
      } else if (tab === "transaksi") {
        setTxs(await api.paintTransactions({
          ...params,
          q: txQ || undefined,
          type: txType || undefined,
          sort_by: txSort.by || undefined,
          sort_dir: txSort.dir || undefined,
          hf: encodeHeaderFilters(txColFilters).length ? encodeHeaderFilters(txColFilters) : undefined,
          page: txPage,
          page_size: 30,
        }));
      } else if (tab === "stok") {
        setStock(await api.paintStock({
          branch_id: branchId || undefined,
          q: stockQ || undefined,
          status: stockStatus || undefined,
          sort_by: stockSort.by || undefined,
          sort_dir: stockSort.dir || undefined,
          hf: encodeHeaderFilters(stockColFilters).length ? encodeHeaderFilters(stockColFilters) : undefined,
          page: stockPage,
          page_size: 50,
        }));
      } else if (tab === "laporan") {
        setReport(await api.paintUsageReport({
          ...params,
          employee_user_id: reportEmployee?.employee_user_id || undefined,
          paint_id: reportPaintId || undefined,
          sort_by: reportSort.by || undefined,
          sort_dir: reportSort.dir || undefined,
          hf: encodeHeaderFilters(reportColFilters).length ? encodeHeaderFilters(reportColFilters) : undefined,
          page: reportPage,
          page_size: 50,
        }));
      }
    } catch (ex) {
      setErr(ex?.response?.data?.detail || "Gagal memuat data Sentralisasi Cat");
    } finally {
      setLoading(false);
    }
  }, [
    tab, branchId, dateFrom, dateTo, txPage, stockPage, reportPage,
    stockQ, stockStatus, txQ, txType, reportEmployee, reportPaintId,
    txSort, txColFilters, stockSort, stockColFilters, reportSort, reportColFilters,
  ]);

  useEffect(() => { reload(); }, [reload]);

  async function saveMaster(e) {
    e.preventDefault();
    try {
      await api.paintMasterCreate({
        code: masterCode,
        name: masterName,
        minimum_stock_grams: Number(masterMin) || 0,
      });
      setMasterOpen(false);
      setMasterCode("");
      setMasterName("");
      setMasterMin("0");
      loadPaints();
      if (tab === "stok") reload();
    } catch (ex) {
      setErr(ex?.response?.data?.detail || "Gagal menyimpan master cat");
    }
  }

  async function doExport() {
    try {
      const blob = await api.paintUsageExport({
        branch_id: branchId || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        employee_user_id: reportEmployee?.employee_user_id || undefined,
        paint_id: reportPaintId || undefined,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "laporan-penggunaan-cat.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (ex) {
      setErr(ex?.response?.data?.detail || "Gagal mengekspor laporan");
    }
  }

  if (!allowedTabs.length) {
    return (
      <div className="prod-page paint-page" data-testid="paint-page-denied">
        <p className="muted">Anda tidak memiliki akses Sentralisasi Cat.</p>
      </div>
    );
  }

  return (
    <div className="prod-page paint-page" data-testid="paint-sentralisasi-page">
      <header className="page-header paint-header">
        <div>
          <p className="eyebrow">Produksi</p>
          <h1 data-testid="paint-title">Sentralisasi Cat</h1>
          <p className="subhead">Stok (gr) · Ambil / Kembali · Laporan</p>
        </div>
        <div className="paint-header-actions">
          {canManageMaster && (
            <button type="button" className="ghost" onClick={() => setMasterOpen(true)} data-testid="paint-master-open">
              Master Cat
            </button>
          )}
          {canCreateTx && (tab === "transaksi" || tab === "ringkasan" || tab === "stok") && (
            <button type="button" className="paint-btn-primary" onClick={() => setModalOpen(true)} data-testid="paint-tx-open">
              + Transaksi
            </button>
          )}
          {canExport && tab === "laporan" && (
            <button type="button" className="ghost" onClick={doExport} data-testid="paint-report-export">
              <Download size={14} aria-hidden /> Export
            </button>
          )}
          <button type="button" className="ghost" onClick={reload} data-testid="paint-reload" aria-label="Refresh" title="Refresh">
            <RefreshCw size={14} />
          </button>
        </div>
      </header>

      <div className="paint-tabs" data-testid="paint-tabs" role="tablist">
        {allowedTabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? "active" : ""}
            data-testid={`paint-tab-${t.id}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="paint-filters" data-testid="paint-filters">
        {tab === "laporan" && (
          <DatePresets datePreset={datePreset} onPreset={applyPreset} />
        )}

        <label className="paint-field">
          <span className="sr-only">Cabang</span>
          <select
            value={branchId}
            onChange={(e) => { setBranchId(e.target.value); setTxPage(1); setStockPage(1); setReportPage(1); }}
            data-testid="paint-filter-branch"
            aria-label="Cabang"
          >
            <option value="">Semua cabang</option>
            {(branches || []).filter((b) => b?.active !== false).map((b) => (
              <option key={b.id} value={b.id}>{b.code || b.name || b.id}</option>
            ))}
          </select>
        </label>

        {(tab === "ringkasan" || tab === "transaksi" || tab === "laporan") && (
          <PeriodRangeControl
            dateFrom={dateFrom}
            dateTo={dateTo}
            onApply={applyPeriod}
            onClearPreset={() => setDatePreset("")}
          />
        )}

        {tab === "ringkasan" && (
          <DatePresets datePreset={datePreset} onPreset={applyPreset} />
        )}

        {tab === "transaksi" && (
          <>
            <label className="paint-field">
              <span className="sr-only">Jenis transaksi</span>
              <select
                value={txType}
                onChange={(e) => { setTxType(e.target.value); setTxPage(1); }}
                data-testid="paint-filter-tx-type"
                aria-label="Jenis transaksi"
              >
                <option value="">Semua jenis</option>
                {Object.entries(TX_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            <div className="paint-search paint-grow">
              <Search size={14} aria-hidden />
              <input
                value={txQ}
                onChange={(e) => { setTxQ(e.target.value); setTxPage(1); }}
                data-testid="paint-tx-search"
                placeholder="Cari no / karyawan / warna…"
                aria-label="Cari transaksi"
              />
            </div>
          </>
        )}

        {tab === "stok" && (
          <>
            <div className="paint-search paint-grow">
              <Search size={14} aria-hidden />
              <input
                value={stockQ}
                onChange={(e) => { setStockQ(e.target.value); setStockPage(1); }}
                data-testid="paint-stock-search"
                placeholder="Cari kode / warna…"
                aria-label="Cari stok cat"
              />
            </div>
            <label className="paint-field">
              <span className="sr-only">Status stok</span>
              <select
                value={stockStatus}
                onChange={(e) => { setStockStatus(e.target.value); setStockPage(1); }}
                data-testid="paint-stock-status"
                aria-label="Status stok"
              >
                <option value="">Semua status</option>
                <option value="AMAN">AMAN</option>
                <option value="MENIPIS">MENIPIS</option>
                <option value="HABIS">HABIS</option>
              </select>
            </label>
          </>
        )}

        {tab === "laporan" && (
          <>
            <div className="paint-search paint-grow paint-search--emp">
              <EmployeePicker value={reportEmployee} onChange={(v) => { setReportEmployee(v); setReportPage(1); }} />
            </div>
            {reportPaintId ? (
              <span className="paint-filter-chip" data-testid="paint-report-paint-chip">
                Warna: {(paints || []).find((p) => p.paint_id === reportPaintId)?.code || reportPaintId}
                <button type="button" aria-label="Hapus filter warna" onClick={() => { setReportPaintId(""); setReportPage(1); }}>×</button>
              </span>
            ) : null}
            <FilterMorePopover
              open={moreFiltersOpen}
              onOpenChange={setMoreFiltersOpen}
              activeCount={reportPaintId ? 1 : 0}
              testId="paint-report-more-filters"
            >
              <label>
                Warna / Kode
                <select
                  value={draftReportPaintId}
                  onChange={(e) => setDraftReportPaintId(e.target.value)}
                  data-testid="paint-report-paint"
                >
                  <option value="">Semua warna</option>
                  {(paints || []).map((p) => (
                    <option key={p.paint_id} value={p.paint_id}>{p.code} — {p.name}</option>
                  ))}
                </select>
              </label>
              <div className="paint-period-actions">
                <button
                  type="button"
                  className="ghost sm"
                  data-testid="paint-more-reset"
                  onClick={() => {
                    setDraftReportPaintId("");
                    setReportPaintId("");
                    setReportPage(1);
                    setMoreFiltersOpen(false);
                  }}
                >
                  Reset
                </button>
                <button
                  type="button"
                  className="paint-btn-primary sm"
                  data-testid="paint-more-apply"
                  onClick={() => {
                    setReportPaintId(draftReportPaintId);
                    setReportPage(1);
                    setMoreFiltersOpen(false);
                  }}
                >
                  Terapkan
                </button>
              </div>
            </FilterMorePopover>
          </>
        )}

        <button
          type="button"
          className="paint-reset-btn"
          data-testid="paint-filter-reset"
          onClick={resetFilters}
          aria-label="Reset filter"
          title="Reset filter"
        >
          <RotateCcw size={14} aria-hidden />
          Reset
        </button>
      </div>

      {err && <div className="signal-strip" role="alert" data-testid="paint-error">{err}</div>}
      {loading && <p className="muted" data-testid="paint-loading">Memuat…</p>}

      {!loading && tab === "ringkasan" && summary && (
        <section data-testid="paint-ringkasan">
          <div className="paint-kpi-row">
            <article data-testid="paint-kpi-stock"><span>Total Stok (gr)</span><strong>{fmtNum(summary.total_stock_grams)}</strong></article>
            <article data-testid="paint-kpi-colors"><span>Warna Aktif</span><strong>{summary.active_colors}</strong></article>
            <article data-testid="paint-kpi-low"><span>Stok Rendah</span><strong>{summary.low_stock_count}</strong></article>
            <article data-testid="paint-kpi-usage"><span>Pemakaian Bersih (gr)</span><strong>{fmtNum(summary.net_usage_grams)}</strong></article>
          </div>
          {(summary.low_stock_alerts || []).length > 0 && (
            <div className="paint-alert" data-testid="paint-low-alert">
              <AlertTriangle size={14} />
              <div className="paint-alert-body">
                <span className="paint-alert-title">Stok Menipis / Habis ({summary.low_stock_alerts.length})</span>
                <div className="paint-alert-chips">
                  {(alertExpanded ? summary.low_stock_alerts : summary.low_stock_alerts.slice(0, 3)).map((a) => (
                    <span key={`${a.paint_id}-${a.branch_id}`} className="paint-alert-chip" title={`${a.code} — ${a.name}`}>
                      {a.code} · {branchLabel(branches, a.branch_id)}: {fmtGrams(a.quantity_grams)}
                      <span className={`paint-status paint-status--${a.status}`}>{a.status}</span>
                    </span>
                  ))}
                  {summary.low_stock_alerts.length > 3 && (
                    <button
                      type="button"
                      className="paint-alert-more"
                      data-testid="paint-alert-expand"
                      onClick={() => setAlertExpanded((v) => !v)}
                    >
                      {alertExpanded ? "Sembunyikan" : `+${summary.low_stock_alerts.length - 3} lainnya`}
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
          <h3 className="paint-section-title">Transaksi Terbaru</h3>
          <PaintAdvancedTable
            tableId="paint-recent"
            userId={userId}
            testId="paint-recent-table"
            mode="client"
            sortBy={recentSort.by}
            sortDir={recentSort.dir}
            filters={recentColFilters}
            onSortChange={(by, dir) => setRecentSort({ by, dir })}
            onFiltersChange={setRecentColFilters}
            rowKey={(r) => r.transaction_id}
            emptyLabel="Belum ada transaksi"
            rows={(summary.recent_transactions || []).map((row) => ({
              ...row,
              warna: `${row.paint_code || ""} — ${row.paint_name || ""}`,
              branch_label: branchLabel(branches, row.branch_id),
            }))}
            columns={[
              { key: "created_at", label: "Tanggal", type: "date", width: 130, render: (v) => formatWib(v) },
              { key: "type", label: "Jenis", type: "enum", width: 100, filterOptions: Object.entries(TX_LABEL).map(([value, label]) => ({ value, label })), render: (v) => <span className={`paint-badge paint-badge--${v}`}>{TX_LABEL[v] || v}</span> },
              { key: "warna", label: "Warna", type: "text", width: 150 },
              { key: "signed_quantity_grams", label: "Jumlah", type: "number", align: "right", width: 90, render: (v, row) => fmtGrams(v ?? row.quantity_grams) },
              { key: "employee_name_snapshot", label: "Karyawan", type: "text", width: 120, render: (v) => v || "—" },
              { key: "actor_name_snapshot", label: "Petugas", type: "text", width: 120, render: (v) => v || "—" },
              { key: "branch_label", label: "Cabang", type: "text", width: 80 },
              { key: "notes", label: "Catatan", type: "text", width: 140, render: (v) => <span className="paint-ellipsis" title={v || ""}>{v || "—"}</span> },
            ]}
          />
        </section>
      )}

      {!loading && tab === "transaksi" && (
        <section data-testid="paint-transaksi">
          <PaintAdvancedTable
            tableId="paint-tx"
            userId={userId}
            testId="paint-tx-table"
            mode="server"
            sortBy={txSort.by}
            sortDir={txSort.dir}
            filters={txColFilters}
            onSortChange={(by, dir) => { setTxSort({ by, dir }); setTxPage(1); }}
            onFiltersChange={(f) => { setTxColFilters(f); setTxPage(1); }}
            rowKey={(r) => r.transaction_id}
            emptyLabel="Belum ada transaksi"
            rows={txs?.items || []}
            columns={[
              { key: "created_at", label: "Tanggal", type: "date", width: 130, render: (v) => formatWib(v) },
              { key: "transaction_id", label: "No Transaksi", type: "text", width: 100, render: (v) => <span className="paint-mono" title={v}>{String(v || "").slice(0, 8)}</span> },
              { key: "type", label: "Jenis", type: "enum", width: 100, filterOptions: Object.entries(TX_LABEL).map(([value, label]) => ({ value, label })), render: (v) => <span className={`paint-badge paint-badge--${v}`}>{TX_LABEL[v] || v}</span> },
              { key: "employee_name_snapshot", label: "Nama Karyawan", type: "text", width: 120, render: (v) => v || "—" },
              { key: "paint_code", label: "Warna", type: "text", width: 150, render: (_v, row) => `${row.paint_code || ""} — ${row.paint_name || ""}` },
              { key: "signed_quantity_grams", label: "Jumlah (gr)", type: "number", align: "right", width: 96, render: (v, row) => fmtNum(v ?? row.quantity_grams) },
              { key: "actor_name_snapshot", label: "Petugas", type: "text", width: 120, render: (v) => v || "—" },
              { key: "branch_id", label: "Cabang", type: "text", width: 80, render: (v) => branchLabel(branches, v) },
              { key: "notes", label: "Catatan", type: "text", width: 140, render: (v) => <span className="paint-ellipsis" title={v || ""}>{v || "—"}</span> },
            ]}
          />
          <div className="paint-pager">
            <button type="button" className="ghost sm" disabled={txPage <= 1} onClick={() => setTxPage((p) => p - 1)}>Prev</button>
            <span className="muted small">Halaman {txPage} / {Math.max(1, Math.ceil((txs?.total || 0) / 30))}</span>
            <button type="button" className="ghost sm" disabled={(txs?.items || []).length < 30} onClick={() => setTxPage((p) => p + 1)}>Next</button>
          </div>
        </section>
      )}

      {!loading && tab === "stok" && (
        <section data-testid="paint-stok">
          <PaintAdvancedTable
            tableId="paint-stock"
            userId={userId}
            testId="paint-stock-table"
            mode="server"
            sortBy={stockSort.by}
            sortDir={stockSort.dir}
            filters={stockColFilters}
            onSortChange={(by, dir) => { setStockSort({ by, dir }); setStockPage(1); }}
            onFiltersChange={(f) => { setStockColFilters(f); setStockPage(1); }}
            rowKey={(r) => `${r.paint_id}-${r.branch_id}`}
            emptyLabel="Belum ada stok"
            rows={stock?.items || []}
            columns={[
              { key: "branch_label", label: "Cabang", type: "text", width: 80, render: (v, row) => v || branchLabel(branches, row.branch_id) },
              { key: "code", label: "Kode", type: "text", width: 80 },
              { key: "name", label: "Warna", type: "text", width: 130 },
              { key: "opening", label: "Opening", type: "number", align: "right", width: 80, render: (v) => fmtNum(v) },
              { key: "add", label: "Add", type: "number", align: "right", width: 70, render: (v) => fmtNum(v) },
              { key: "take", label: "Take", type: "number", align: "right", width: 70, render: (v) => fmtNum(v) },
              { key: "return", label: "Return", type: "number", align: "right", width: 70, render: (v) => fmtNum(v) },
              { key: "adjustment", label: "Adjust", type: "number", align: "right", width: 70, render: (v) => fmtNum(v) },
              { key: "quantity_grams", label: "Saldo", type: "number", align: "right", width: 80, render: (v) => <strong className="saldo">{fmtNum(v)}</strong> },
              { key: "minimum_stock_grams", label: "Min", type: "number", align: "right", width: 70, render: (v) => fmtNum(v) },
              { key: "status", label: "Status", type: "enum", width: 90, filterOptions: [{ value: "AMAN", label: "AMAN" }, { value: "MENIPIS", label: "MENIPIS" }, { value: "HABIS", label: "HABIS" }], render: (v) => <span className={`paint-status paint-status--${v}`}>{v}</span> },
            ]}
          />
        </section>
      )}

      {!loading && tab === "laporan" && (
        <section data-testid="paint-laporan">
          {report?.totals && (
            <div className="paint-kpi-row paint-kpi-row--compact" data-testid="paint-report-totals">
              <article><span>Total Diambil</span><strong>{fmtNum(report.totals.take_grams)}</strong></article>
              <article><span>Total Dikembalikan</span><strong>{fmtNum(report.totals.return_grams)}</strong></article>
              <article><span>Pemakaian Bersih</span><strong>{fmtNum(report.totals.net_usage_grams)}</strong></article>
            </div>
          )}
          <PaintAdvancedTable
            tableId="paint-report"
            userId={userId}
            testId="paint-report-table"
            mode="server"
            sortBy={reportSort.by}
            sortDir={reportSort.dir}
            filters={reportColFilters}
            onSortChange={(by, dir) => { setReportSort({ by, dir }); setReportPage(1); }}
            onFiltersChange={(f) => { setReportColFilters(f); setReportPage(1); }}
            rowKey={(r, idx) => `${r.employee_user_id}-${r.paint_id}-${idx}`}
            emptyLabel="Belum ada pemakaian"
            rows={report?.items || []}
            columns={[
              { key: "employee_name", label: "Nama Karyawan", type: "text", width: 130 },
              { key: "division", label: "Divisi / Cabang", type: "text", width: 130, render: (v, row) => [v, row.branch_label || branchLabel(branches, row.branch_id)].filter(Boolean).join(" · ") },
              { key: "paint_code", label: "Warna", type: "text", width: 140, render: (_v, row) => `${row.paint_code || ""} — ${row.paint_name || ""}` },
              { key: "period_from", label: "Periode", type: "text", width: 140, render: (_v, row) => [row.period_from, row.period_to].filter(Boolean).map((d) => formatStatusPoDate(d)).join(" → ") || "—" },
              { key: "take_grams", label: "Diambil", type: "number", align: "right", width: 80, render: (v) => fmtNum(v) },
              { key: "return_grams", label: "Dikembalikan", type: "number", align: "right", width: 90, render: (v) => fmtNum(v) },
              { key: "net_usage_grams", label: "Bersih", type: "number", align: "right", width: 80, render: (v) => <strong className="saldo">{fmtNum(v)}</strong> },
              { key: "transaction_count", label: "Jumlah TX", type: "number", align: "right", width: 80, render: (v) => fmtNum(v) },
              { key: "references", label: "Referensi", type: "text", width: 120, sortable: false, filterable: false, render: (_v, row) => (row.references || []).join(", ") || "—" },
            ]}
          />
          <div className="paint-pager">
            <button type="button" className="ghost sm" disabled={reportPage <= 1} onClick={() => setReportPage((p) => p - 1)}>Prev</button>
            <span className="muted small">Halaman {reportPage} / {Math.max(1, Math.ceil((report?.total || 0) / 50))}</span>
            <button type="button" className="ghost sm" disabled={(report?.items || []).length < 50} onClick={() => setReportPage((p) => p + 1)}>Next</button>
          </div>
        </section>
      )}

      <TxFormModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSaved={reload}
        paints={paints}
        branches={branches}
        defaults={{ branchId }}
        canFlags={canFlags}
      />

      {masterOpen && (
        <div className="paint-modal-backdrop" data-testid="paint-master-modal">
          <form className="paint-modal" onSubmit={saveMaster}>
            <header>
              <h2>Master Cat</h2>
              <button type="button" className="ghost sm" onClick={() => setMasterOpen(false)}><X size={16} /></button>
            </header>
            <div className="paint-form-grid">
              <label>
                Kode
                <input value={masterCode} onChange={(e) => setMasterCode(e.target.value)} required data-testid="paint-master-code" />
              </label>
              <label>
                Warna / Nama
                <input value={masterName} onChange={(e) => setMasterName(e.target.value)} required data-testid="paint-master-name" />
              </label>
              <label>
                Minimum stok (gr)
                <input type="number" value={masterMin} onChange={(e) => setMasterMin(e.target.value)} data-testid="paint-master-min" />
              </label>
            </div>
            <footer>
              <button type="button" className="ghost" onClick={() => setMasterOpen(false)}>Batal</button>
              <button type="submit" className="paint-btn-primary" data-testid="paint-master-save">Simpan</button>
            </footer>
          </form>
        </div>
      )}
    </div>
  );
}
