import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, CheckSquare, Eye, Plus, Trash2, X, RefreshCw, Upload, Landmark, BookOpen, ArrowLeftRight, Link2 } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { newOperationKey, reconViewState, shouldApplyResponse } from "../lib/cashBankReconView";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";
import { KbField, KbSection, KbSearchField } from "../components/CashBankFormPrimitives";

import "./RekonsiliasiKasBank.css";

const errText = (ex) => {
  const det = ex?.response?.data?.detail;
  if (typeof det === "string") return det;
  if (det && typeof det === "object" && det.message) return det.message;
  return ex?.message || "Permintaan gagal";
};

const formatMoney = (n) => {
  const v = Number(n || 0);
  return v.toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
};
const formatRp = (n) => `Rp ${formatMoney(n)}`;
const todayIso = () => new Date().toISOString().slice(0, 10);

const STATUS_META = {
  draft: { className: "kb-status-draft", label: "Draft" },
  finalized: { className: "kb-status-posted", label: "Finalized" },
};

export function LegacyRekonsiliasiKasBank() {
  const { hasPerm } = useAuth();
  const canReconcile = hasPerm("cash_bank.reconcile");
  const [banks, setBanks] = useState([]);
  const [list, setList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [panel, setPanel] = useState(null); // 'create' | 'edit' | 'view'
  const [editRow, setEditRow] = useState(null);
  const [cashBankId, setCashBankId] = useState("");
  const [reconDate, setReconDate] = useState(todayIso);
  const [statementBalance, setStatementBalance] = useState("");
  const [notes, setNotes] = useState("");
  const [movements, setMovements] = useState([]);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [bookBalance, setBookBalance] = useState(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();

  const loadList = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [cb, recon] = await Promise.all([
        api.cashBankAccounts({ active_only: true }),
        api.cashBankReconciliations(),
      ]);
      setBanks(Array.isArray(cb?.items) ? cb.items : []);
      setList(Array.isArray(recon?.items) ? recon.items : []);
    } catch (ex) {
      setLoadError(errText(ex));
      setList([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadList(); }, [loadList]);

  const filtered = useMemo(() => {
    const needle = String(q || "").trim().toLowerCase();
    return list.filter((row) => {
      if (statusFilter !== "all" && row.status !== statusFilter) return false;
      if (!needle) return true;
      const blob = [row.number, row.cash_bank_name, row.notes, row.reconciliation_date].join(" ").toLowerCase();
      return blob.includes(needle);
    });
  }, [list, q, statusFilter]);

  const tableView = liveSearchTableView({
    pending: searchPending,
    loading,
    itemCount: filtered.length,
  });

  const difference = useMemo(() => {
    if (statementBalance === "" || statementBalance == null || bookBalance == null) return null;
    const stmt = Number(String(statementBalance).replace(",", "."));
    if (Number.isNaN(stmt)) return null;
    return stmt - Number(bookBalance || 0);
  }, [statementBalance, bookBalance]);

  const refreshWorkspace = useCallback(async (opts = {}) => {
    const bankId = opts.cashBankId ?? cashBankId;
    const date = opts.reconDate ?? reconDate;
    const reconId = opts.reconciliationId ?? editRow?.id;
    const stmt = opts.statementBalance ?? statementBalance;
    if (!bankId || !date) {
      setMovements([]);
      setBookBalance(null);
      return;
    }
    try {
      const ws = await api.cashBankReconciliationWorkspace({
        cash_bank_id: bankId,
        reconciliation_date: date,
        reconciliation_id: reconId || undefined,
        statement_balance: stmt !== "" && stmt != null ? stmt : undefined,
      });
      setBookBalance(ws.book_balance);
      setMovements(Array.isArray(ws.movements) ? ws.movements : []);
      if (opts.syncSelected) {
        setSelectedIds(new Set((ws.movements || []).filter((m) => m.matched).map((m) => m.journal_line_id)));
      }
    } catch (ex) {
      setFormError(errText(ex));
    }
  }, [cashBankId, reconDate, editRow, statementBalance]);

  const openCreate = () => {
    const first = banks[0]?.id || "";
    setEditRow(null);
    setCashBankId(first);
    setReconDate(todayIso());
    setStatementBalance("");
    setNotes("");
    setSelectedIds(new Set());
    setMovements([]);
    setBookBalance(null);
    setFormError("");
    setPanel("create");
  };

  const openEdit = async (row) => {
    setFormError("");
    try {
      const detail = await api.getCashBankReconciliation(row.id);
      setEditRow(detail);
      setCashBankId(detail.cash_bank_id);
      setReconDate(detail.reconciliation_date);
      setStatementBalance(String(detail.statement_balance ?? ""));
      setNotes(detail.notes || "");
      setBookBalance(detail.book_balance);
      const matched = new Set((detail.matches || []).map((m) => m.journal_line_id));
      setSelectedIds(matched);
      setPanel(detail.status === "finalized" ? "view" : "edit");
      await refreshWorkspace({
        cashBankId: detail.cash_bank_id,
        reconDate: detail.reconciliation_date,
        reconciliationId: detail.id,
        statementBalance: detail.statement_balance,
        syncSelected: true,
      });
    } catch (ex) {
      setLoadError(errText(ex));
    }
  };

  useEffect(() => {
    if (panel === "create" || panel === "edit") {
      refreshWorkspace({ syncSelected: panel === "edit" && selectedIds.size === 0 });
    }
    // Intentionally only when bank/date change in form
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panel, cashBankId, reconDate]);

  const toggleMatch = (lineId) => {
    if (panel === "view" || editRow?.status === "finalized") return;
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(lineId)) next.delete(lineId);
      else next.add(lineId);
      return next;
    });
  };

  const closePanel = () => {
    setPanel(null);
    setEditRow(null);
    setFormError("");
  };

  const submitDraft = async (e) => {
    e?.preventDefault?.();
    if (!canReconcile || saving) return;
    setSaving(true);
    setFormError("");
    try {
      const payload = {
        cash_bank_id: cashBankId,
        reconciliation_date: reconDate,
        statement_balance: statementBalance,
        notes,
        journal_line_ids: Array.from(selectedIds),
      };
      let saved;
      if (editRow?.id) {
        saved = await api.updateCashBankReconciliation(editRow.id, payload);
      } else {
        saved = await api.createCashBankReconciliation(payload);
      }
      setEditRow(saved);
      setPanel("edit");
      await loadList();
      await refreshWorkspace({
        reconciliationId: saved.id,
        cashBankId: saved.cash_bank_id,
        reconDate: saved.reconciliation_date,
        statementBalance: saved.statement_balance,
        syncSelected: true,
      });
    } catch (ex) {
      setFormError(errText(ex));
    } finally {
      setSaving(false);
    }
  };

  const doFinalize = async () => {
    if (!canReconcile || !editRow?.id || saving) return;
    if (!window.confirm("Finalisasi rekonsiliasi? Setelah finalized, matching tidak dapat diubah.")) return;
    setSaving(true);
    setFormError("");
    try {
      // Persist latest matches first
      await api.updateCashBankReconciliation(editRow.id, {
        statement_balance: statementBalance,
        notes,
        journal_line_ids: Array.from(selectedIds),
      });
      const fin = await api.finalizeCashBankReconciliation(editRow.id);
      setEditRow(fin);
      setPanel("view");
      await loadList();
    } catch (ex) {
      setFormError(errText(ex));
    } finally {
      setSaving(false);
    }
  };

  const doDelete = async () => {
    if (!canReconcile || !editRow?.id || editRow.status !== "draft") return;
    if (!window.confirm("Hapus draft rekonsiliasi ini?")) return;
    setSaving(true);
    try {
      await api.deleteCashBankReconciliation(editRow.id);
      closePanel();
      await loadList();
    } catch (ex) {
      setFormError(errText(ex));
    } finally {
      setSaving(false);
    }
  };

  const isFinal = editRow?.status === "finalized" || panel === "view";

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="kas-bank-rekonsiliasi-page">
      <div className="page-heading">
        <div>
          <h1>Rekonsiliasi Bank</h1>
          <p className="muted">Matching manual movement jurnal vs saldo rekening koran. Tidak membuat jurnal.</p>
        </div>
        {canReconcile && !panel && (
          <button type="button" className="primary" onClick={openCreate} data-testid="kas-bank-recon-create">
            <Plus size={16} /> Buat Rekonsiliasi
          </button>
        )}
      </div>

      {loadError && (
        <div className="form-error" role="alert"><AlertCircle size={16} /> {loadError}</div>
      )}

      {!panel && (
        <>
          <div className="kb-toolbar kb-recon-toolbar" data-testid="kas-bank-recon-toolbar">
            <select
              className="kb-toolbar-status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              aria-label="Filter status"
            >
              <option value="all">Semua status</option>
              <option value="draft">Draft</option>
              <option value="finalized">Finalized</option>
            </select>
            <KbSearchField
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              onSubmit={doSearch}
              onClear={clearQuery}
              placeholder="Cari nomor / kas / catatan…"
              testid="kas-bank-recon-search"
              ariaLabel="Cari nomor, kas, atau catatan"
              className="kb-recon-search"
            />
          </div>

          <div className="table-wrap">
            <table className="prod-table kas-bank-table" data-testid="kas-bank-recon-list">
              <thead>
                <tr>
                  <th>Nomor</th>
                  <th>Tanggal</th>
                  <th>Kas/Bank</th>
                  <th className="num">Saldo Buku</th>
                  <th className="num">Saldo Koran</th>
                  <th className="num">Selisih</th>
                  <th>Status</th>
                  <th>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {tableView === "loading" && (
                  <tr><td colSpan={8} className="empty-state">Memuat…</td></tr>
                )}
                {loadError && (
                  <tr><td colSpan={8} className="empty-state">Daftar dokumen lama tidak dimuat.</td></tr>
                )}
                {tableView === "empty" && !loadError && (
                  <tr><td colSpan={8} className="empty-state">Belum ada rekonsiliasi.</td></tr>
                )}
                {tableView === "results" && filtered.map((row) => {
                  const st = STATUS_META[row.status] || STATUS_META.draft;
                  return (
                    <tr key={row.id}>
                      <td className="kb-doc-number">{row.number}</td>
                      <td>{row.reconciliation_date}</td>
                      <td>{row.cash_bank_name}</td>
                      <td className="num kb-money">{formatRp(row.book_balance)}</td>
                      <td className="num kb-money">{formatRp(row.statement_balance)}</td>
                      <td className="num kb-money">{formatRp(row.difference)}</td>
                      <td><span className={`pill ${st.className}`}>{st.label}</span></td>
                      <td>
                        <button type="button" className="ghost sm" onClick={() => openEdit(row)}>
                          <Eye size={14} /> {row.status === "draft" && canReconcile ? "Edit" : "Lihat"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {panel && (
        <div className="kb-workspace-panel" data-testid="kas-bank-recon-panel">
          <div className="kb-view-head">
            <h3>{isFinal ? "Detail Rekonsiliasi" : (editRow ? "Edit Draft" : "Buat Rekonsiliasi")}</h3>
            {editRow && (
              <span className={`pill ${(STATUS_META[editRow.status] || STATUS_META.draft).className}`}>
                {(STATUS_META[editRow.status] || STATUS_META.draft).label}
              </span>
            )}
            <button type="button" className="ghost sm" onClick={closePanel} aria-label="Tutup"><X size={16} /></button>
          </div>

          {formError && (
            <div className="form-error" role="alert"><AlertCircle size={16} /> {formError}</div>
          )}

          <form className="kb-workspace-form emp-form" onSubmit={submitDraft}>
            <div className="kb-workspace-form-body">
              <KbSection title="Header Rekonsiliasi">
                <div className="kb-form-grid">
                  <KbField label="Kas/Bank" required>
                    <select
                      value={cashBankId}
                      onChange={(e) => setCashBankId(e.target.value)}
                      disabled={isFinal}
                      data-testid="kas-bank-recon-bank"
                    >
                      <option value="">— Pilih —</option>
                      {banks.map((b) => (
                        <option key={b.id} value={b.id}>{b.name}</option>
                      ))}
                      {editRow?.cash_bank_id && !banks.some((b) => b.id === editRow.cash_bank_id) && (
                        <option value={editRow.cash_bank_id}>{editRow.cash_bank_name || editRow.cash_bank_id}</option>
                      )}
                    </select>
                  </KbField>
                  <KbField label="Tanggal Rekonsiliasi" required>
                    <input
                      type="date"
                      value={reconDate}
                      onChange={(e) => setReconDate(e.target.value)}
                      disabled={isFinal}
                      data-testid="kas-bank-recon-date"
                    />
                  </KbField>
                  <KbField label="Saldo Rekening Koran" required>
                    <input
                      type="number"
                      step="0.01"
                      className="kb-amount-input"
                      value={statementBalance}
                      onChange={(e) => setStatementBalance(e.target.value)}
                      disabled={isFinal}
                      data-testid="kas-bank-recon-statement"
                    />
                  </KbField>
                  <KbField label="Status">
                    <div className="kb-meta-readonly">
                      {(STATUS_META[editRow?.status || "draft"] || STATUS_META.draft).label}
                    </div>
                  </KbField>
                  <KbField label="Catatan" className="kb-span-2">
                    <textarea
                      className="kb-textarea"
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      disabled={isFinal}
                      rows={2}
                    />
                  </KbField>
                </div>
              </KbSection>

              <div className="kb-summary-strip" data-testid="kas-bank-recon-summary">
                <div className="kb-summary-card">
                  <span>Saldo Buku Eternal</span>
                  <strong className="num kb-money">{bookBalance == null ? "—" : formatRp(bookBalance)}</strong>
                </div>
                <div className="kb-summary-card">
                  <span>Saldo Rekening Koran</span>
                  <strong className="num kb-money">{statementBalance === "" ? "—" : formatRp(statementBalance)}</strong>
                </div>
                <div className={`kb-summary-card ${difference != null && difference !== 0 ? "kb-summary-diff" : "kb-summary-emphasis"}`}>
                  <span>Selisih</span>
                  <strong className="num kb-money">{difference == null ? "—" : formatRp(difference)}</strong>
                </div>
              </div>

              <KbSection title="Movement belum / sudah dicocokkan" subtitle="Centang exact journal movement — bukan berdasarkan nominal saja.">
                <div className="table-wrap">
                  <table className="prod-table kas-bank-table" data-testid="kas-bank-recon-movements">
                    <thead>
                      <tr>
                        <th className="col-fit">Match</th>
                        <th>Tanggal</th>
                        <th>Nomor</th>
                        <th>Keterangan</th>
                        <th className="num">Debit</th>
                        <th className="num">Kredit</th>
                        <th className="num">Amount</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {!movements.length && (
                        <tr><td colSpan={8} className="empty-state">Tidak ada movement hingga tanggal ini.</td></tr>
                      )}
                      {movements.map((m) => {
                        const checked = selectedIds.has(m.journal_line_id);
                        return (
                          <tr
                            key={m.journal_line_id}
                            className={checked ? "kb-row-matched" : "kb-row-unmatched"}
                            data-testid={`kas-bank-recon-move-${m.journal_line_id}`}
                          >
                            <td className="col-fit">
                              <input
                                type="checkbox"
                                checked={checked}
                                disabled={isFinal}
                                onChange={() => toggleMatch(m.journal_line_id)}
                                aria-label={`Match ${m.transaction_number || m.journal_number}`}
                              />
                            </td>
                            <td>{m.date}</td>
                            <td className="kb-doc-number">{m.transaction_number || m.journal_number}</td>
                            <td className="kb-ellipsis" title={m.description || ""}>{m.description || "—"}</td>
                            <td className="num kb-money">{m.debit ? formatRp(m.debit) : ""}</td>
                            <td className="num kb-money">{m.credit ? formatRp(m.credit) : ""}</td>
                            <td className="num kb-money">{formatRp(m.amount)}</td>
                            <td>
                              <span className={`pill ${checked ? "kb-status-posted" : "kb-status-draft"}`}>
                                {checked ? "Matched" : "Unmatched"}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </KbSection>
            </div>

            <div className="emp-modal-foot">
              <button type="button" className="ghost" onClick={closePanel}>Tutup</button>
              {!isFinal && canReconcile && (
                <>
                  {editRow?.status === "draft" && (
                    <button type="button" className="ghost" onClick={doDelete} disabled={saving}>
                      <Trash2 size={14} /> Hapus Draft
                    </button>
                  )}
                  <button type="submit" className="ghost" disabled={saving} data-testid="kas-bank-recon-save">
                    Simpan Draft
                  </button>
                  {editRow?.id && (
                    <button type="button" className="primary" onClick={doFinalize} disabled={saving} data-testid="kas-bank-recon-finalize">
                      <CheckSquare size={14} /> Finalisasi
                    </button>
                  )}
                </>
              )}
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

const monthStart = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
};

function errPayload(ex) {
  const det = ex?.response?.data?.detail;
  if (typeof det === "string") return { message: det, errors: [], status: ex?.response?.status };
  if (det && typeof det === "object") {
    return { message: det.message || errText(ex), errors: det.errors || [], status: ex?.response?.status };
  }
  return { message: errText(ex), errors: [], status: ex?.response?.status };
}

// Presentation only: missing values stay missing; currency is supplied by the API.
const reconMoney = (value, currency) => {
  if (value == null || value === "" || !Number.isFinite(Number(value))) return "Belum tersedia";
  return `${currency === "IDR" ? "Rp" : currency || "Mata uang belum ditetapkan"} ${formatMoney(value)}`;
};
const reconDate = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("id-ID", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Jakarta" }).format(date);
};
const reconStatus = (value) => ({ active: "Aktif", cancelled: "Dibatalkan", complete: "Selesai diimpor", importing: "Sedang diimpor", failed: "Impor gagal", posted: "Diposting" }[value] || value || "—");

export default function RekonsiliasiKasBank() {
  const { hasPerm } = useAuth();
  const canReconcile = hasPerm("cash_bank.reconcile");
  const canManage = hasPerm("cash_bank.manage");
  const canOpenPayment = hasPerm("cash_bank.view") || hasPerm("cash_bank.manage");
  const [mode, setMode] = useState("pairs");
  const [banks, setBanks] = useState([]);
  const [cashBankId, setCashBankId] = useState("");
  const [startDate, setStartDate] = useState(monthStart);
  const [endDate, setEndDate] = useState(todayIso);
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [tab, setTab] = useState("unmatched");
  const [pane, setPane] = useState("bank");
  const [workspace, setWorkspace] = useState(null);
  const [imports, setImports] = useState([]);
  const [phase, setPhase] = useState("loading");
  const [message, setMessage] = useState("");
  const [rowErrors, setRowErrors] = useState([]);
  const [selectedBank, setSelectedBank] = useState("");
  const [selectedBook, setSelectedBook] = useState("");
  const [suggestions, setSuggestions] = useState(null);
  const [preview, setPreview] = useState(null);
  const [importOpen, setImportOpen] = useState(false);
  const [bcaOpen, setBcaOpen] = useState(false);
  const [bcaPreview, setBcaPreview] = useState(null);
  const [bcaFilename, setBcaFilename] = useState("");
  const [bcaMessage, setBcaMessage] = useState("");
  const importDialog = useRef(null);
  const bcaDialog = useRef(null);
  const importFile = useRef(null);
  const uploadInput = useRef(null);
  const bcaInput = useRef(null);
  const bcaFile = useRef(null);
  const importButton = useRef(null);
  const [mapping, setMapping] = useState({
    header_row: 0,
    date_column: 0,
    date_format: "YYYY-MM-DD",
    description_column: 1,
    reference_column: "",
    bank_txn_id_column: "",
    amount_mode: "in_out",
    in_column: 2,
    out_column: 3,
    balance_column: "",
    currency_column: "",
    file_currency: "",
    decimal_mode: "comma_decimal",
    sign_convention: "",
    amount_column: "",
    operator_coverage_confirmed: false,
  });
  const [currencyDraft, setCurrencyDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const requestRef = useRef(0);
  const matchKey = useRef("");
  const cancelKeys = useRef({});

  useEffect(() => {
    if (importOpen && importDialog.current && !importDialog.current.open) importDialog.current.showModal();
    if (!importOpen && importDialog.current?.open) importDialog.current.close();
  }, [importOpen]);

  useEffect(() => {
    if (bcaOpen && bcaDialog.current && !bcaDialog.current.open) bcaDialog.current.showModal();
    if (!bcaOpen && bcaDialog.current?.open) bcaDialog.current.close();
  }, [bcaOpen]);

  useEffect(() => {
    setSelectedBank("");
    setSelectedBook("");
    setSuggestions(null);
    matchKey.current = "";
  }, [cashBankId, startDate, endDate, q]);


  const loadBanks = useCallback(async () => {
    const res = await api.cashBankAccounts({ active_only: true });
    const items = Array.isArray(res?.items) ? res.items : [];
    setBanks(items);
    setCashBankId((current) => current || items[0]?.id || "");
  }, []);

  useEffect(() => { loadBanks().catch((ex) => { setPhase("error"); setMessage(errPayload(ex).message); }); }, [loadBanks]);

  const loadWorkspace = useCallback(async (bankId, start, end, query) => {
    if (!bankId) return;
    const token = ++requestRef.current;
    setPhase("loading");
    setMessage("");
    setRowErrors([]);
    try {
      const [ws, history] = await Promise.all([
        api.cashBankPairWorkspace({
          cash_bank_id: bankId,
          start_date: start,
          end_date: end,
          q: query || undefined,
          page: 1,
          page_size: 50,
        }),
        api.cashBankStatementImports({ cash_bank_id: bankId }),
      ]);
      if (!shouldApplyResponse(token, requestRef.current)) return;
      setWorkspace(ws);
      setImports(Array.isArray(history?.items) ? history.items : []);
      setPhase("ready");
    } catch (ex) {
      if (!shouldApplyResponse(token, requestRef.current)) return;
      const parsed = errPayload(ex);
      setWorkspace(null);
      setImports([]);
      if (parsed.status === 403) setPhase("forbidden");
      else if (parsed.status === 409) setPhase("conflict");
      else setPhase("error");
      setMessage(parsed.message);
    }
  }, []);

  useEffect(() => {
    if (mode === "pairs" && cashBankId) loadWorkspace(cashBankId, startDate, endDate, q);
  }, [mode, cashBankId, startDate, endDate, q, loadWorkspace]);

  const chooseBank = (id) => {
    setCashBankId(id);
    setSelectedBank("");
    setSelectedBook("");
    setSuggestions(null);
    setPreview(null);
    importFile.current = null;
    setBcaPreview(null);
    setBcaMessage("");
    bcaFile.current = null;
    setBcaOpen(false);
    matchKey.current = "";
  };

  const bankRows = workspace?.bank_lines || [];
  const bookRows = workspace?.book_lines || [];
  const bankView = reconViewState({
    loading: phase === "loading",
    error: phase === "error" ? message : "",
    forbidden: phase === "forbidden",
    conflict: phase === "conflict",
    count: phase === "ready" ? (workspace?.visible_bank_count || 0) : 0,
  });
  const bookView = reconViewState({
    loading: phase === "loading",
    error: phase === "error" ? message : "",
    forbidden: phase === "forbidden",
    conflict: phase === "conflict",
    count: phase === "ready" ? (workspace?.visible_book_count || 0) : 0,
  });

  const pickBank = async (row) => {
    setSelectedBank(row.id);
    matchKey.current = newOperationKey();
    if (!row.matchable) {
      setSuggestions(null);
      return;
    }
    try {
      const res = await api.cashBankPairSuggestions({
        statement_line_id: row.id,
        start_date: startDate,
        end_date: endDate,
      });
      setSuggestions(res);
    } catch (ex) {
      setSuggestions(null);
      setMessage(errPayload(ex).message);
    }
  };

  const mappingPayload = () => {
    const num = (value) => (value === "" || value == null ? null : Number(value));
    return {
      header_row: Number(mapping.header_row || 0),
      date_column: Number(mapping.date_column),
      date_format: mapping.date_format,
      description_column: Number(mapping.description_column),
      reference_column: num(mapping.reference_column),
      bank_txn_id_column: num(mapping.bank_txn_id_column),
      amount_mode: mapping.amount_mode,
      amount_column: mapping.amount_mode === "signed" ? num(mapping.amount_column) : null,
      sign_convention: mapping.amount_mode === "signed" ? (mapping.sign_convention || null) : null,
      in_column: num(mapping.in_column),
      out_column: num(mapping.out_column),
      balance_column: num(mapping.balance_column),
      currency_column: num(mapping.currency_column),
      file_currency: mapping.file_currency || null,
      decimal_mode: mapping.decimal_mode,
      operator_coverage_confirmed: Boolean(mapping.operator_coverage_confirmed),
    };
  };

  const statementForm = (includeFingerprint) => {
    const file = importFile.current;
    if (!file || !preview?.fingerprint || !preview?.account_binding) return null;
    const body = new FormData();
    body.append("cash_bank_id", cashBankId);
    body.append("file", file, file.name);
    body.append("mapping", JSON.stringify(mappingPayload()));
    body.append("account_binding", preview.account_binding);
    if (includeFingerprint) body.append("fingerprint", preview.fingerprint);
    return body;
  };

  const onBcaUpload = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !canReconcile || !cashBankId) return;
    setBusy(true);
    setBcaMessage("");
    setBcaFilename(file.name);
    try {
      const body = new FormData();
      body.append("cash_bank_id", cashBankId);
      body.append("file", file, file.name);
      const res = await api.previewCashBankBcaStatement(body);
      bcaFile.current = file;
      setBcaPreview(res);
      setBcaOpen(true);
    } catch (ex) {
      bcaFile.current = null;
      setBcaPreview(null);
      setBcaMessage(errPayload(ex).message);
      setBcaOpen(true);
    } finally {
      setBusy(false);
    }
  };

  const bcaImportReady = Boolean(bcaPreview?.fingerprint && bcaPreview?.account_binding);
  const onBcaImport = async () => {
    const file = bcaFile.current;
    if (!file || !canReconcile || !bcaImportReady) {
      setBcaMessage("Berkas asli belum siap diimpor.");
      return;
    }
    setBusy(true);
    setBcaMessage("");
    try {
      const body = new FormData();
      body.append("cash_bank_id", cashBankId);
      body.append("file", file, file.name);
      body.append("fingerprint", bcaPreview.fingerprint);
      body.append("account_binding", bcaPreview.account_binding);
      await api.commitCashBankBcaStatement(body);
      bcaFile.current = null;
      setBcaPreview(null);
      setBcaOpen(false);
      setMessage("Impor BCA selesai. Jurnal tidak dibuat.");
      await loadWorkspace(cashBankId, startDate, endDate, q);
    } catch (ex) {
      const parsed = errPayload(ex);
      setBcaMessage(parsed.message);
    } finally {
      setBusy(false);
    }
  };

  const onUpload = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !cashBankId || !canReconcile) return;
    setBusy(true);
    setRowErrors([]);
    setMessage("");
    try {
      const body = new FormData();
      body.append("cash_bank_id", cashBankId);
      body.append("file", file, file.name);
      const res = await api.previewCashBankStatement(body);
      importFile.current = file;
      setPreview(res);
      setImportOpen(true);
      setTab("import");
    } catch (ex) {
      importFile.current = null;
      setMessage(errPayload(ex).message);
    } finally {
      setBusy(false);
    }
  };

  const onValidate = async () => {
    const body = statementForm(true);
    if (!body) {
      setMessage("Berkas asli tidak ada di halaman ini. Unggah kembali.");
      return;
    }
    setBusy(true);
    try {
      const res = await api.validateCashBankStatement(body);
      setPreview((current) => ({ ...current, validation: res }));
      setRowErrors(res.errors || []);
      setMessage(res.ok ? "Validasi pratinjau selesai. Belum ada baris mutasi yang disimpan." : "Ada baris yang tidak valid.");
    } catch (ex) {
      const parsed = errPayload(ex);
      setRowErrors(parsed.errors);
      setMessage(parsed.message);
    } finally {
      setBusy(false);
    }
  };

  const onCommit = async () => {
    const body = statementForm(true);
    if (!body || !canReconcile) {
      if (!body) setMessage("Berkas asli tidak ada di halaman ini. Unggah kembali.");
      return;
    }
    setBusy(true);
    try {
      await api.commitCashBankStatement(body);
      importFile.current = null;
      setPreview(null);
      setImportOpen(false);
      setMessage("Impor selesai. Jurnal tidak dibuat.");
      await loadWorkspace(cashBankId, startDate, endDate, q);
      setTab("unmatched");
    } catch (ex) {
      const parsed = errPayload(ex);
      setRowErrors(parsed.errors);
      setMessage(parsed.message);
    } finally {
      setBusy(false);
    }
  };

  const onMatch = async () => {
    if (!canReconcile || !selectedBank || !selectedBook) return;
    if (!matchKey.current) matchKey.current = newOperationKey();
    setBusy(true);
    setMessage("");
    try {
      const saved = await api.createCashBankPair({
        cash_bank_id: cashBankId,
        statement_line_id: selectedBank,
        journal_line_id: selectedBook,
        idempotency_key: matchKey.current,
        start_date: startDate,
        end_date: endDate,
      });
      if (saved.status !== "active" || saved.review_state === "perlu_tinjauan") {
        setMessage(saved.review_state === "perlu_tinjauan"
          ? "Pencocokan tersimpan tetapi sumber perlu tinjauan."
          : "Operasi sebelumnya tidak diaktifkan kembali.");
      }
      setSelectedBank("");
      setSelectedBook("");
      setSuggestions(null);
      matchKey.current = "";
      await loadWorkspace(cashBankId, startDate, endDate, q);
    } catch (ex) {
      const parsed = errPayload(ex);
      if (parsed.status === 409) setMessage(parsed.message);
      else setMessage(parsed.message);
    } finally {
      setBusy(false);
    }
  };

  const onCancel = async (matchId) => {
    if (!canReconcile) return;
    if (!cancelKeys.current[matchId]) cancelKeys.current[matchId] = newOperationKey();
    setBusy(true);
    try {
      await api.cancelCashBankPair(matchId, {
        idempotency_key: cancelKeys.current[matchId],
        reason: "Batalkan pencocokan",
      });
      await loadWorkspace(cashBankId, startDate, endDate, q);
    } catch (ex) {
      setMessage(errPayload(ex).message);
    } finally {
      setBusy(false);
    }
  };

  const saveCurrency = async () => {
    if (!canManage || !cashBankId) return;
    setBusy(true);
    try {
      await api.updateCashBankAccount(cashBankId, { currency_code: currencyDraft });
      await loadBanks();
      await loadWorkspace(cashBankId, startDate, endDate, q);
      setMessage("Mata uang rekening disimpan. Tidak ada pengisian massal.");
    } catch (ex) {
      setMessage(errPayload(ex).message);
    } finally {
      setBusy(false);
    }
  };

  const closing = workspace?.statement_closing;
  const selectedBankRow = bankRows.find((row) => row.id === selectedBank);
  const selectedBookRow = bookRows.find((row) => row.journal_line_id === selectedBook);
  const selectedBankTotal = selectedBankRow ? Number(selectedBankRow.amount || 0) : 0;
  const selectedBookTotal = selectedBookRow ? Number(selectedBookRow.absolute_amount || 0) : 0;
  const selectionDiff = selectedBank && selectedBook ? selectedBankTotal - selectedBookTotal : null;

  if (mode === "legacy") {
    return (
      <div>
        <div className="kb-pair-toolbar">
          <button type="button" className="ghost sm" onClick={() => setMode("pairs")}>Kembali ke pencocokan baris</button>
        </div>
        <LegacyRekonsiliasiKasBank />
      </div>
    );
  }

  const currency = workspace?.currency_code;
  const money = (value, code = currency) => reconMoney(value, code);
  const visibleBank = bankRows.filter((row) => !row.claim_id);
  const visibleBook = bookRows.filter((row) => !row.claim_id);
  const ready = phase === "ready";
  const canMatch = ready && !busy && workspace?.feature_ready !== false && selectedBankRow?.matchable && selectedBookRow?.matchable;
  const matched = workspace?.matched_pairs || [];
  const sample = preview?.sample_rows || [];
  const headers = sample[Number(mapping.header_row) || 0] || [];
  const columnCount = Math.max(0, ...sample.map((row) => row.length));
  const columnSelect = (label, key, optional = false) => (
    <label key={key}>{label}
      <select value={mapping[key]} onChange={(e) => setMapping({ ...mapping, [key]: e.target.value })} disabled={busy}>
        <option value="" disabled={!optional}>{optional ? "Tidak digunakan" : "Pilih kolom"}</option>
        {Array.from({ length: columnCount }, (_, index) => {
          const example = sample[(Number(mapping.header_row) || 0) + 1]?.[index];
          return <option key={index} value={index}>{index + 1}. {headers[index] || `Kolom ${index + 1}`}{example == null || example === "" ? "" : ` · ${String(example).slice(0, 36)}`}</option>;
        })}
      </select>
    </label>
  );
  const amountCell = (row, direction, value) => row.direction === direction ? money(value, row.currency_code || currency) : "—";
  const stateMessage = (state, label, count) => {
    if (state === "loading") return <p className="recon-empty" role="status">Memuat {label}…</p>;
    if (state === "forbidden") return <p className="recon-empty" role="alert">Tidak berizin melihat {label}.</p>;
    if (state === "error" || state === "conflict") return <p className="recon-empty" role="alert">{state === "error" ? "Gagal memuat" : "Konflik data"} {label}.</p>;
    if (ready && !count) return <p className="recon-empty">Tidak ada baris belum cocok pada tampilan ini.</p>;
    return null;
  };

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace kb-pair-shell recon-workspace" data-testid="kas-bank-rekonsiliasi-page">
      <header className="recon-heading">
        <div><p>Kas &amp; Bank / Rekonsiliasi</p><h1>Rekonsiliasi Bank</h1></div>
        <button type="button" className="ghost sm" onClick={() => setMode("legacy")} disabled={busy}>Dokumen saldo lama</button>
      </header>
      <div className="kb-pair-toolbar kb-recon-toolbar" data-testid="kas-bank-recon-toolbar">
        <select aria-label="Rekening" value={cashBankId} onChange={(e) => chooseBank(e.target.value)} disabled={busy} data-testid="kas-bank-pair-bank">
          <option value="">— Rekening —</option>
          {banks.map((bank) => <option key={bank.id} value={bank.id}>{bank.name}{bank.currency_code ? ` · ${bank.currency_code}` : " · mata uang belum ditetapkan"}</option>)}
        </select>
        <div className="recon-period"><input aria-label="Tanggal awal" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} disabled={busy} /><span>–</span><input aria-label="Tanggal akhir" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} disabled={busy} /></div>
        <KbSearchField value={qInput} onChange={(e) => setQInput(e.target.value)} onSubmit={() => setQ(qInput.trim())} onClear={() => { setQInput(""); setQ(""); }} placeholder="Cari uraian / referensi…" testid="kas-bank-recon-search" />
        <button type="button" className="ghost sm recon-refresh" aria-label="Muat ulang" title="Muat ulang" onClick={() => loadWorkspace(cashBankId, startDate, endDate, q)} disabled={busy || !cashBankId}><RefreshCw size={17} /></button>
        {canReconcile && <>
          <input ref={uploadInput} className="recon-file-input" aria-label="Berkas mutasi" type="file" accept=".csv,.txt,.xlsx" onChange={onUpload} data-testid="kas-bank-pair-import" disabled={busy || !cashBankId} />
          <button ref={importButton} type="button" className="ghost sm recon-upload" onClick={() => preview ? setImportOpen(true) : uploadInput.current?.click()} disabled={busy || !cashBankId}><Upload size={16} />{preview ? "Lanjutkan impor" : "Impor Mutasi"}</button>
          <input ref={bcaInput} className="recon-file-input" aria-label="Berkas CSV BCA" type="file" accept=".csv,text/csv" onChange={onBcaUpload} data-testid="kas-bank-bca-file" disabled={busy || !cashBankId} />
          <button type="button" className="ghost sm recon-upload" onClick={() => bcaInput.current?.click()} disabled={busy || !cashBankId} data-testid="kas-bank-bca-open"><Upload size={16} />Pratinjau CSV BCA</button>
        </>}
      </div>

      <div className="kb-pair-summary" data-testid="kas-bank-pair-summary" aria-busy={!ready}>
        <div className="recon-metric recon-metric-book"><BookOpen size={21} /><div><span>Saldo buku</span><strong>{ready ? money(workspace?.book_ending_balance) : "—"}</strong><small>Saldo awal {ready ? money(workspace?.book_opening_balance) : "—"}</small></div></div>
        <div className="recon-metric recon-metric-bank"><Landmark size={21} /><div><span>Saldo rekening koran</span><strong>{ready ? money(closing?.amount) : "—"}</strong><small>{closing?.amount == null ? "Belum tersedia" : `${closing.label || "Saldo koran"} · ${reconDate(closing.as_of)}`}</small></div></div>
        <div className="recon-metric recon-metric-diff"><ArrowLeftRight size={21} /><div><span>Selisih saldo</span><strong>{ready ? money(workspace?.difference) : "—"}</strong><small>Saldo sesuai sumber laporan</small></div></div>
        <div className="recon-metric"><CheckSquare size={21} /><div><span>Belum cocok</span><strong>Bank {ready ? workspace?.unmatched_bank_count ?? "—" : "—"} · Buku {ready ? workspace?.unmatched_book_count ?? "—" : "—"}</strong><small>Jumlah dari seluruh workspace</small></div></div>
      </div>
      <p className="recon-context">Buku Besar sampai {reconDate(endDate)}. Pencocokan transaksi tidak menyatakan saldo selesai direkonsiliasi.</p>
      {ready && cashBankId && !currency && <div className="kb-pair-note recon-notice" role="status">
        Mata uang rekening belum ditetapkan. Impor dapat dipratinjau; pencocokan belum tersedia.
        {canManage && <span className="kb-currency-set"><input aria-label="Kode mata uang" value={currencyDraft} onChange={(e) => setCurrencyDraft(e.target.value.toUpperCase())} placeholder="Contoh: IDR" maxLength={3} /><button type="button" className="ghost sm" onClick={saveCurrency} disabled={busy}>Simpan mata uang</button></span>}
      </div>}
      {closing?.manual && <p className="kb-pair-note">Saldo manual {money(closing.manual.amount)} pada {reconDate(closing.manual.as_of)}. Input manual, bukan verifikasi bank.</p>}
      {workspace?.feature_ready === false && <div className="form-error" role="alert"><AlertCircle size={16} /> {workspace.feature_message}</div>}
      {message && !importOpen && <div className="form-error recon-message" role="alert"><AlertCircle size={16} /> {message}</div>}

      <div className="kb-pair-tabs" role="tablist" aria-label="Tampilan rekonsiliasi">
        {[["unmatched", "Belum Cocok"], ["matched", "Sudah Cocok"], ["import", "Riwayat Impor"]].map(([id, label]) => <button key={id} id={`recon-tab-${id}`} role="tab" aria-selected={tab === id} aria-controls={`recon-panel-${id}`} type="button" className={tab === id ? "is-active" : ""} onClick={() => setTab(id)}>{label}</button>)}
      </div>

      {tab === "unmatched" && <div className="recon-tab-body" role="tabpanel" id="recon-panel-unmatched" aria-labelledby="recon-tab-unmatched">
        <div className="kb-pane-switch" aria-label="Panel"><button type="button" aria-pressed={pane === "bank"} className={pane === "bank" ? "primary sm" : "ghost sm"} onClick={() => setPane("bank")}>Bank</button><button type="button" aria-pressed={pane === "book"} className={pane === "book" ? "primary sm" : "ghost sm"} onClick={() => setPane("book")}>Buku</button></div>
        <div className="kb-pair-columns">
          <section className={`kb-pane ${pane === "bank" ? "is-shown" : ""}`} aria-label="Mutasi bank">
            <div className="recon-pane-title"><h2>Mutasi Bank</h2><span>{ready ? `${visibleBank.length} baris ditampilkan` : ""}</span></div>
            {stateMessage(bankView, "mutasi bank", visibleBank.length)}
            {bankView === "results" && visibleBank.length > 0 && <div className="kb-pair-scroll"><table className="prod-table kas-bank-table" data-testid="kas-bank-pair-bank-table">
              <thead><tr><th aria-label="Pilih satu baris" /><th>Tanggal</th><th>Uraian / Referensi</th><th className="num">Masuk</th><th className="num">Keluar</th></tr></thead><tbody>
                {visibleBank.map((row) => <tr key={row.id} className={selectedBank === row.id ? "kb-row-matched" : ""}>
                  <td><input type="radio" name="bank-line" checked={selectedBank === row.id} onChange={() => pickBank(row)} disabled={!row.matchable || busy || !canReconcile} aria-label={`Pilih bank ${row.description}`} /></td>
                  <td className="recon-date">{reconDate(row.date)}</td><td><span className="recon-description">{row.description || "—"}</span><small>{row.reference || "—"}</small>{row.original_date === "PEND" && row.date_status === "temporary" && <small>PEND - tanggal sementara</small>}{row.block_reason && <span className="recon-row-warning">{row.block_reason}</span>}{row.identity_guarantee === "tidak_dijamin" && <small>Identitas bank tidak dijamin</small>}</td>
                  <td className="num kb-money">{amountCell(row, "in", row.amount)}</td><td className="num kb-money">{amountCell(row, "out", row.amount)}</td>
                </tr>)}
              </tbody></table></div>}
          </section>
          <section className={`kb-pane ${pane === "book" ? "is-shown" : ""}`} aria-label="Pembukuan Eternal">
            <div className="recon-pane-title"><h2>Pembukuan Eternal</h2><span>{ready ? `${visibleBook.length} baris ditampilkan` : ""}</span></div>
            {stateMessage(bookView, "pembukuan", visibleBook.length)}
            {bookView === "results" && visibleBook.length > 0 && <div className="kb-pair-scroll"><table className="prod-table kas-bank-table" data-testid="kas-bank-pair-book-table">
              <thead><tr><th aria-label="Pilih satu baris" /><th>Tanggal</th><th>Uraian / Dokumen</th><th className="num">Masuk</th><th className="num">Keluar</th></tr></thead><tbody>
                {visibleBook.map((row) => <tr key={row.journal_line_id} className={selectedBook === row.journal_line_id ? "kb-row-matched" : ""}>
                  <td><input type="radio" name="book-line" checked={selectedBook === row.journal_line_id} onChange={() => { setSelectedBook(row.journal_line_id); matchKey.current = newOperationKey(); }} disabled={!row.matchable || busy || !canReconcile} aria-label={`Pilih buku ${row.journal_number}`} /></td>
                  <td className="recon-date">{reconDate(row.date)}</td><td><span className="recon-description">{row.description || "—"}</span><small>{row.source_path && row.source_id ? <Link to={`${row.source_path}?id=${encodeURIComponent(row.source_id)}`}>{row.journal_number}</Link> : row.journal_number}</small>{row.block_reason && <span className="recon-row-warning">{row.block_reason}</span>}<small>{reconStatus(row.status)}</small></td>
                  <td className="num kb-money">{amountCell(row, "in", row.absolute_amount)}</td><td className="num kb-money">{amountCell(row, "out", row.absolute_amount)}</td>
                </tr>)}
              </tbody></table></div>}
          </section>
        </div>
        {suggestions?.suggestions?.length > 0 && <details className="recon-suggestions" data-testid="kas-bank-pair-suggestions"><summary><AlertCircle size={15} />{suggestions.ambiguous ? "Beberapa kandidat bernominal sama. Periksa referensi sebelum mencocokkan." : "Ada saran pasangan. Periksa sebelum mencocokkan."}</summary><p>Saran tidak disimpan otomatis.</p><ul>{suggestions.suggestions.map((item) => <li key={item.journal_line_id}>{item.journal_number} · {item.reasons.join(", ")}</li>)}</ul></details>}
      </div>}

      {tab === "matched" && <section className="recon-tab-body recon-history" role="tabpanel" id="recon-panel-matched" aria-labelledby="recon-tab-matched">
        <div className="recon-pane-title"><h2>Pasangan Tersimpan</h2><span>Snapshot saat pencocokan, bukan saldo terkini</span></div>
        {!ready && <p className="recon-empty" role="status">{phase === "loading" ? "Memuat pasangan…" : "Pasangan tidak dapat dimuat."}</p>}
        {ready && <div className="kb-pair-scroll"><table className="prod-table kas-bank-table" data-testid="kas-bank-pair-matched"><thead><tr><th>Tanggal buku</th><th>Baris bank</th><th>Dokumen buku</th><th className="num">Nominal</th><th>Status / Tinjauan</th><th>Aksi</th></tr></thead><tbody>
          {matched.map((row) => {
            const sourceBank = bankRows.find((bank) => bank.id === (row.statement_line_id || row.history_statement_line_id));
            const reconciled = row.status === "active" && row.review_state !== "perlu_tinjauan";
            return <tr key={row.id} className={reconciled ? "kb-row-reconciled" : ""}><td>{reconDate(row.book_snapshot?.date)}</td><td>{sourceBank?.description || "Detail bank tidak tersedia"}<small>{sourceBank?.reference || row.statement_line_id || row.history_statement_line_id || "—"}</small></td><td>{row.book_snapshot?.journal_number || "—"}<small>{row.book_snapshot?.description || "—"}</small></td><td className="num kb-money">{money(row.amount, row.currency_code || currency)}</td><td><span className={`recon-badge ${row.review_state === "perlu_tinjauan" ? "is-warning" : reconciled ? "is-matched" : ""}`}>{row.review_state === "perlu_tinjauan" ? "Perlu tinjauan" : reconciled ? "✓ Cocok" : reconStatus(row.status)}</span></td><td>{canReconcile && row.status === "active" && <button type="button" className="ghost sm" disabled={busy} onClick={() => onCancel(row.id)}>Batalkan pencocokan</button>}</td></tr>;
          })}
          {!matched.length && <tr><td colSpan={6}>Belum ada pencocokan baris pada tampilan ini.</td></tr>}
        </tbody></table></div>}
      </section>}

      {tab === "import" && <section className="recon-tab-body recon-history kb-pair-import" role="tabpanel" id="recon-panel-import" aria-labelledby="recon-tab-import" data-testid="kas-bank-pair-import-panel">
        <div className="recon-pane-title"><h2>Riwayat Impor</h2><span>Impor mutasi tidak membuat jurnal</span></div>
        {preview && <div className="recon-import-resume"><span>Pratinjau <strong>{preview.filename}</strong> belum dikonfirmasi.</span><button type="button" className="ghost sm" onClick={() => setImportOpen(true)}>Lanjutkan pratinjau</button></div>}
        {!ready && <p className="recon-empty" role="status">{phase === "loading" ? "Memuat riwayat…" : "Riwayat tidak dapat dimuat."}</p>}
        {ready && <div className="kb-pair-scroll"><table className="prod-table kas-bank-table"><thead><tr><th>Berkas</th><th>Status</th><th className="num">Baris tersimpan</th><th>Cakupan</th></tr></thead><tbody>
          {!imports.length && <tr><td colSpan={4}>Belum ada impor.</td></tr>}
          {imports.map((row) => <tr key={row.id}><td>{row.filename}</td><td><span className="recon-badge">{reconStatus(row.status)}</span></td><td className="num">{row.stored_count ?? "—"}</td><td>{row.coverage_note || "Cakupan belum tersedia"}</td></tr>)}
        </tbody></table></div>}
      </section>}

      {tab === "unmatched" && <footer className="kb-pair-footer">
        <div><span>Pilihan bank</span><strong>{selectedBankRow ? money(selectedBankTotal, selectedBankRow.currency_code || currency) : "—"}</strong></div>
        <div><span>Pilihan buku</span><strong>{selectedBookRow ? money(selectedBookTotal, selectedBookRow.currency_code || currency) : "—"}</strong></div>
        <div><span>Selisih pilihan</span><strong>{selectionDiff == null ? "—" : money(selectionDiff)}</strong></div>
        <div className="recon-footer-action"><small>Satu transaksi di setiap sisi</small>{canReconcile && <button type="button" className="primary" disabled={!canMatch} onClick={onMatch} data-testid="kas-bank-pair-match"><Link2 size={17} />{busy ? "Memproses…" : "Cocokkan"}</button>}{canOpenPayment && <Link to="/kas-bank/pembayaran">Buka Pembayaran</Link>}</div>
      </footer>}

      <dialog ref={importDialog} className="recon-import-dialog" aria-labelledby="recon-import-title" onCancel={(event) => { if (busy) event.preventDefault(); else setImportOpen(false); }} onClose={() => { setImportOpen(false); importButton.current?.focus(); }}>
        <div className="recon-drawer-heading"><div><p>CSV / XLSX · Pemetaan kolom</p><h2 id="recon-import-title">Impor Mutasi Bank</h2></div><button type="button" className="ghost sm" aria-label="Tutup pratinjau impor" onClick={() => setImportOpen(false)} disabled={busy}><X size={20} /></button></div>
        {preview && <>
          <div className="recon-drawer-content">
            <div className="recon-file-info"><strong>{preview.filename}</strong><span>Pratinjau hanya ada selama halaman ini terbuka. Belum menyimpan mutasi.</span></div>
            <p className="recon-context">Pilih kolom berdasarkan nama dan contoh isinya. Referensi bukan ID transaksi bank yang terjamin unik.</p>
            <fieldset className="kb-pair-mapping" disabled={busy}><legend>Pemetaan kolom</legend>
              {columnSelect("Tanggal", "date_column")}{columnSelect("Uraian", "description_column")}{columnSelect("Referensi", "reference_column", true)}
              {mapping.amount_mode === "in_out" ? <>{columnSelect("Uang masuk", "in_column", true)}{columnSelect("Uang keluar", "out_column", true)}</> : columnSelect("Nominal bertanda", "amount_column")}
              {columnSelect("Saldo", "balance_column", true)}{columnSelect("ID transaksi bank", "bank_txn_id_column", true)}
              <label>Format tanggal<select value={mapping.date_format} onChange={(e) => setMapping({ ...mapping, date_format: e.target.value })}><option>YYYY-MM-DD</option><option>DD/MM/YYYY</option><option>MM/DD/YYYY</option><option>DD-MM-YYYY</option></select></label>
              <label>Desimal<select value={mapping.decimal_mode} onChange={(e) => setMapping({ ...mapping, decimal_mode: e.target.value })}><option value="comma_decimal">Koma desimal</option><option value="dot_decimal">Titik desimal</option></select></label>
              <label>Mode nominal<select value={mapping.amount_mode} onChange={(e) => setMapping({ ...mapping, amount_mode: e.target.value })}><option value="in_out">Kolom masuk dan keluar</option><option value="signed">Nominal bertanda</option></select></label>
              {mapping.amount_mode === "signed" && <label>Tanda negatif<select value={mapping.sign_convention} onChange={(e) => setMapping({ ...mapping, sign_convention: e.target.value })}><option value="">Pilih</option><option value="negative_out">Negatif = keluar</option><option value="negative_in">Negatif = masuk</option></select></label>}
              <label>Mata uang berkas<input value={mapping.file_currency} onChange={(e) => setMapping({ ...mapping, file_currency: e.target.value.toUpperCase() })} placeholder="Kosong jika tidak ada" maxLength={3} /></label>
              <label className="recon-coverage"><input type="checkbox" checked={mapping.operator_coverage_confirmed} onChange={(e) => setMapping({ ...mapping, operator_coverage_confirmed: e.target.checked })} />Cakupan periode dikonfirmasi operator, bukan verifikasi bank</label>
            </fieldset>
            <h3>Contoh isi berkas</h3><div className="recon-sample-scroll"><table className="prod-table kas-bank-table"><tbody>{sample.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>)}</tbody></table></div>
            {message && <p className="recon-notice" role="status">{message}</p>}
            {!!rowErrors.length && <ul className="recon-validation-errors" role="alert">{rowErrors.map((err) => <li key={`${err.row}-${err.message}`}>Baris {err.row}: {err.message}</li>)}</ul>}
            {preview.validation && <p>Diterima {preview.validation.accepted_count}. Masuk {money(preview.validation.total_in, preview.currency_code)}. Keluar {money(preview.validation.total_out, preview.currency_code)}.</p>}
          </div>
          <footer className="recon-drawer-footer"><span>Validasi dan penyimpanan tetap diperiksa server.</span><button type="button" className="ghost sm" onClick={onValidate} disabled={busy}>Validasi</button><button type="button" className="primary sm" onClick={onCommit} disabled={busy || !canReconcile}>Konfirmasi impor</button></footer>
        </>}
      </dialog>

      <dialog ref={bcaDialog} className="recon-import-dialog recon-bca-dialog" aria-labelledby="recon-bca-title" data-testid="kas-bank-bca-preview" onCancel={(event) => { if (busy) event.preventDefault(); else setBcaOpen(false); }} onClose={() => setBcaOpen(false)}>
        <div className="recon-drawer-heading"><div><p>CSV BCA · Pratinjau parser</p><h2 id="recon-bca-title">Pratinjau CSV BCA</h2></div><button type="button" className="ghost sm" aria-label="Tutup pratinjau CSV BCA" onClick={() => setBcaOpen(false)} disabled={busy}><X size={20} /></button></div>
        <div className="recon-drawer-content">
          <div className="recon-file-info"><strong>{bcaFilename || "CSV BCA"}</strong><span>Pratinjau hasil pembacaan. Belum menyimpan mutasi dan belum membuat jurnal.</span></div>
          {bcaMessage && <p className="recon-notice" role="alert">{bcaMessage}</p>}
          {bcaPreview && <>
            <p className="recon-context">Bank {bcaPreview.bank || "BCA"}. Debit ditampilkan sebagai keluar dan kredit sebagai masuk.</p>
            <div className="recon-bca-table-wrap"><table className="prod-table kas-bank-table"><thead><tr><th>Tanggal transaksi</th><th>Keterangan</th><th className="num">Debit</th><th className="num">Kredit</th><th className="num">Saldo</th></tr></thead><tbody>
              {(bcaPreview.rows || []).map((row) => <tr key={`${row.source_row}-${row.tanggal}-${row.keterangan}`}><td className="recon-date">{reconDate(row.tanggal)}</td><td>{row.keterangan}{row.original_date === "PEND" && <small>PEND - tanggal sementara</small>}</td><td className="num kb-money">{row.debit == null ? "—" : formatMoney(row.debit)}</td><td className="num kb-money">{row.kredit == null ? "—" : formatMoney(row.kredit)}</td><td className="num kb-money">{row.saldo == null ? "—" : formatMoney(row.saldo)}</td></tr>)}
              {!(bcaPreview.rows || []).length && <tr><td colSpan={5}>Tidak ada baris valid.</td></tr>}
            </tbody></table></div>
            {!!(bcaPreview.errors || []).length && <ul className="recon-validation-errors" role="alert">{bcaPreview.errors.map((err) => <li key={`${err.row}-${err.message}`}>Baris {err.row}: {err.message}</li>)}</ul>}
          </>}
        </div>
        <footer className="recon-drawer-footer"><span>Baris PEND memakai tanggal impor WIB dan tetap masuk rekonsiliasi.</span>{canReconcile && <button type="button" className="primary sm" data-testid="kas-bank-bca-import" disabled={busy || !bcaImportReady} onClick={onBcaImport}>Impor mutasi BCA</button>}</footer>
      </dialog>
    </div>
  );
}
