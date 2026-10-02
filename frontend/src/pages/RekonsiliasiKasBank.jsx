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
const isoDay = (value) => {
  const text = String(value || "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : "";
};
const dateDeltaDays = (left, right) => {
  const a = isoDay(left);
  const b = isoDay(right);
  if (!a || !b) return null;
  return Math.abs(Math.round((Date.parse(`${a}T00:00:00Z`) - Date.parse(`${b}T00:00:00Z`)) / 86400000));
};
const referenceRank = (reasons) => {
  const text = (reasons || []).join(" ").toLowerCase();
  if (text.includes("referensi muncul")) return 0;
  if (text.includes("referensi tidak sama")) return 1;
  return 2;
};
const sortCandidateSuggestions = (items, bankRow) => [...(items || [])].sort((left, right) => {
  const leftDelta = dateDeltaDays(bankRow?.date, left?.date);
  const rightDelta = dateDeltaDays(bankRow?.date, right?.date);
  const deltaScore = (leftDelta == null ? Number.MAX_SAFE_INTEGER : leftDelta) - (rightDelta == null ? Number.MAX_SAFE_INTEGER : rightDelta);
  if (deltaScore) return deltaScore;
  const referenceScore = referenceRank(left?.reasons) - referenceRank(right?.reasons);
  if (referenceScore) return referenceScore;
  const bankAmount = Number(bankRow?.amount || 0);
  return Math.abs(Number(left?.amount || 0) - bankAmount) - Math.abs(Number(right?.amount || 0) - bankAmount);
});
const candidateNotes = (item, bankRow) => {
  const notes = [];
  const delta = dateDeltaDays(bankRow?.date, item?.date);
  if (delta === 0) notes.push("Tanggal sama");
  else if (delta != null) notes.push(`Beda tanggal ${delta} hari`);
  const reference = (item?.reasons || []).find((reason) => String(reason).toLowerCase().includes("referensi"));
  if (reference) notes.push(reference);
  else if (bankRow?.reference) notes.push(`Referensi ${bankRow.reference}`);
  return notes;
};
const reconDateTime = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("id-ID", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta" }).format(date);
};
const importOutcomeText = (row) => {
  if (!row || row.fully_accepted == null || row.new_count == null) return "—";
  const count = (value) => (value == null ? "—" : String(value));
  const lead = row.fully_accepted === false ? "Belum semua diterima" : "Semua baris diterima";
  return `${lead}. Baru ${count(row.new_count)} · Diperbarui ${count(row.updated_count)} · Terlindungi ${count(row.protected_count)} · Tidak berubah ${count(row.unchanged_count)} · Konflik ${count(row.conflict_count)}`;
};
const importResultView = (result) => {
  if (!result || typeof result !== "object") return null;
  if (result.fully_accepted == null || result.new_count == null) return null;
  const fully = result.fully_accepted === true && Number(result.conflict_count || 0) === 0;
  return {
    fully,
    lead: fully ? "Impor selesai." : "Impor belum menerima semua transaksi.",
    counts: [
      ["Baru", Number(result.new_count || 0)],
      ["Diperbarui", Number(result.updated_count || 0)],
      ["Terlindungi", Number(result.protected_count || 0)],
      ["Tidak berubah", Number(result.unchanged_count || 0)],
      ["Konflik", Number(result.conflict_count || 0)],
    ],
    conflicts: Array.isArray(result.conflicts) ? result.conflicts : [],
  };
};
const duplicateLineLabel = (index) => `Line ${index < 26 ? String.fromCharCode(65 + index) : index + 1}`;
const duplicateDirection = (value) => ({ in: "Masuk", out: "Keluar" }[value] || value || "—");
const duplicateStatusLabel = (value) => ({
  pending_review: "Menunggu Keputusan",
  kept: "Dipertahankan",
  excluded: "Dikecualikan",
  clear: "Bukan duplikat",
  definite_duplicate: "Duplikat pasti",
}[value] || value || "—");
const duplicateExcludeReason = (group, line) => {
  if (group?.solo) {
    if (line?.active_claim) return "Ada klaim pencocokan aktif.";
    if (line?.reconciliation_status === "active") return "Ada pencocokan aktif.";
    return "";
  }
  const lines = group?.lines || [];
  if (line?.active_claim || lines.some((item) => item.active_claim)) return "Ada klaim pencocokan aktif.";
  if (line?.reconciliation_status === "active" || lines.some((item) => item.reconciliation_status === "active")) return "Ada pencocokan aktif.";
  const files = new Set(lines.map((item) => item.import_id || ""));
  if (files.size <= 1) return "Kandidat berasal dari berkas impor yang sama.";
  if (lines.length > 2) return "Kandidat lebih dari dua.";
  return "";
};
const knownCount = (value) => (typeof value === "number" && Number.isFinite(value) ? value : null);
const emptyPendingAccess = {
  solo_lines: [], outside_period: [], unreachable: [], other_accounts: [],
  truncated: false, page: 1, page_size: 100, total: 0, pending_total: null, review_revision: "", stale: false,
};
const emptyGroupAccess = {
  groups: [], page: 1, page_size: 20, group_total: 0, truncated: false,
  pending_total: null, review_revision: "", stale: false,
};
const groupIdentity = (group) => group?.group_key || (group?.lines || []).map((line) => line?.statement_line_id).filter(Boolean).join("|");
const appendPendingLines = (current, incoming) => {
  const append = (key) => {
    const seen = new Set((current[key] || []).map((line) => line.statement_line_id));
    const rows = [...(current[key] || [])];
    (incoming[key] || []).forEach((line) => {
      const id = line?.statement_line_id;
      if (!id || seen.has(id)) return;
      seen.add(id);
      rows.push(line);
    });
    return rows;
  };
  return {
    ...emptyPendingAccess,
    ...incoming,
    solo_lines: append("solo_lines"),
    outside_period: append("outside_period"),
    unreachable: append("unreachable"),
    other_accounts: Array.isArray(incoming?.other_accounts) ? incoming.other_accounts : (current.other_accounts || []),
    page: Number(incoming?.page) || Number(current.page || 1) + 1,
    truncated: Boolean(incoming?.truncated),
    pending_total: knownCount(incoming?.pending_total) ?? knownCount(current.pending_total),
    review_revision: incoming?.review_revision || current.review_revision || "",
    stale: false,
  };
};
const appendGroups = (current, incoming) => {
  const seen = new Set((current.groups || []).map(groupIdentity));
  const groups = [...(current.groups || [])];
  (incoming?.groups || []).forEach((group) => {
    const id = groupIdentity(group);
    if (!id || seen.has(id)) return;
    seen.add(id);
    groups.push(group);
  });
  return {
    ...emptyGroupAccess,
    ...incoming,
    groups,
    pending_total: knownCount(incoming?.pending_total) ?? knownCount(current.pending_total),
    review_revision: incoming?.review_revision || current.review_revision || "",
    stale: false,
  };
};

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
  const [groupAccess, setGroupAccess] = useState(emptyGroupAccess);
  const [pendingAccess, setPendingAccess] = useState(emptyPendingAccess);
  const [duplicateMessage, setDuplicateMessage] = useState("");
  const [decisionReasons, setDecisionReasons] = useState({});
  const [phase, setPhase] = useState("loading");
  const [message, setMessage] = useState("");
  const [importResult, setImportResult] = useState(null);
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
  const [recommendationRows, setRecommendationRows] = useState([]);
  const requestRef = useRef(0);
  const decisionKeys = useRef({});
  const matchKey = useRef("");
  const cancelKeys = useRef({});
  const matchingRef = useRef(false);
  const suggestionGen = useRef(0);

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
    setRecommendationRows([]);
    matchKey.current = "";
    suggestionGen.current += 1;
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
      const [ws, history, review, pending] = await Promise.all([
        api.cashBankPairWorkspace({
          cash_bank_id: bankId,
          start_date: start,
          end_date: end,
          q: query || undefined,
          page: 1,
          page_size: 50,
        }),
        api.cashBankStatementImports({ cash_bank_id: bankId }),
        api.cashBankDuplicateGroups({
          cash_bank_id: bankId,
          start_date: start,
          end_date: end,
        }).then((body) => ({ ok: true, body })).catch((ex) => ({ ok: false, message: errPayload(ex).message })),
        api.cashBankPendingReview({
          cash_bank_id: bankId,
          start_date: start,
          end_date: end,
        }).then((body) => ({ ok: true, body })).catch((ex) => ({ ok: false, message: errPayload(ex).message })),
      ]);
      if (!shouldApplyResponse(token, requestRef.current)) return;
      setWorkspace(ws);
      setImports(Array.isArray(history?.items) ? history.items : []);
      setGroupAccess(review.ok && review.body
        ? { ...emptyGroupAccess, ...review.body, groups: Array.isArray(review.body.groups) ? review.body.groups : [] }
        : emptyGroupAccess);
      setPendingAccess(pending.ok && pending.body
        ? { ...emptyPendingAccess, ...pending.body, pending_total: knownCount(pending.body.pending_total) }
        : emptyPendingAccess);
      const reviewMessages = [...new Set([review.ok ? "" : review.message, pending.ok ? "" : pending.message].filter(Boolean))];
      setDuplicateMessage(reviewMessages.join(" "));
      setPhase("ready");
    } catch (ex) {
      if (!shouldApplyResponse(token, requestRef.current)) return;
      const parsed = errPayload(ex);
      setWorkspace(null);
      setImports([]);
      setGroupAccess(emptyGroupAccess);
      setPendingAccess(emptyPendingAccess);
      setDuplicateMessage("");
      if (parsed.status === 403) setPhase("forbidden");
      else if (parsed.status === 409) setPhase("conflict");
      else setPhase("error");
      setMessage(parsed.message);
    }
  }, []);

  const reloadAfterReviewChange = async (notice) => {
    const bankId = cashBankId;
    const start = startDate;
    const end = endDate;
    const query = q;
    await loadWorkspace(bankId, start, end, query);
    setDuplicateMessage(notice || "Daftar berubah. Muat ulang dari awal.");
  };

  const loadMorePending = async () => {
    if (!cashBankId || busy || !pendingAccess.truncated) return;
    const token = requestRef.current;
    const nextPage = Number(pendingAccess.page || 1) + 1;
    setBusy(true);
    try {
      const body = await api.cashBankPendingReview({
        cash_bank_id: cashBankId,
        start_date: startDate,
        end_date: endDate,
        page: nextPage,
        review_revision: pendingAccess.review_revision || undefined,
      });
      if (token !== requestRef.current) return;
      if (body?.stale) {
        await reloadAfterReviewChange(body.message);
        return;
      }
      setPendingAccess((current) => appendPendingLines(current, body || {}));
    } catch (ex) {
      if (token !== requestRef.current) return;
      setDuplicateMessage(errPayload(ex).message);
    } finally {
      setBusy(false);
    }
  };

  const loadMoreGroups = async () => {
    if (!cashBankId || busy || !groupAccess.truncated) return;
    const token = requestRef.current;
    const nextPage = Number(groupAccess.page || 1) + 1;
    setBusy(true);
    try {
      const body = await api.cashBankDuplicateGroups({
        cash_bank_id: cashBankId,
        start_date: startDate,
        end_date: endDate,
        page: nextPage,
        review_revision: groupAccess.review_revision || undefined,
      });
      if (token !== requestRef.current) return;
      if (body?.stale) {
        await reloadAfterReviewChange(body.message);
        return;
      }
      setGroupAccess((current) => appendGroups(current, body || {}));
    } catch (ex) {
      if (token !== requestRef.current) return;
      setDuplicateMessage(errPayload(ex).message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (mode === "pairs" && cashBankId) loadWorkspace(cashBankId, startDate, endDate, q);
  }, [mode, cashBankId, startDate, endDate, q, loadWorkspace]);

  useEffect(() => {
    const generation = ++suggestionGen.current;
    const inScope = phase === "ready" && workspace && (
      (workspace.cash_bank_id == null || workspace.cash_bank_id === cashBankId)
      && (workspace.start_date == null || workspace.start_date === startDate)
      && (workspace.end_date == null || workspace.end_date === endDate)
      && (workspace.search == null || String(workspace.search) === String(q || ""))
    );
    if (!inScope) {
      setRecommendationRows([]);
      return undefined;
    }
    const lines = (workspace.bank_lines || []).filter((row) => !row.claim_id && row.matchable);
    if (!lines.length) {
      setRecommendationRows([]);
      return undefined;
    }
    let cancelled = false;
    Promise.all(lines.map(async (row) => {
      try {
        const response = await api.cashBankPairSuggestions({
          statement_line_id: row.id,
          start_date: startDate,
          end_date: endDate,
        });
        const suggestionsForLine = Array.isArray(response?.suggestions) ? response.suggestions : [];
        return suggestionsForLine.length ? { statement_line_id: row.id, suggestions: suggestionsForLine, response } : null;
      } catch {
        return null;
      }
    })).then((entries) => {
      if (cancelled || generation !== suggestionGen.current) return;
      setRecommendationRows(entries.filter(Boolean));
    });
    return () => { cancelled = true; };
  }, [phase, workspace, cashBankId, startDate, endDate, q]);

  const chooseBank = (id) => {
    setCashBankId(id);
    setSelectedBank("");
    setSelectedBook("");
    setSuggestions(null);
    setRecommendationRows([]);
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
    setSelectedBook("");
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

  const selectRecommendation = (bankRow, item, response) => {
    if (!canReconcile || busy || matchingRef.current) return;
    setSelectedBank(bankRow.id);
    setSelectedBook(item.journal_line_id);
    matchKey.current = newOperationKey();
    setSuggestions(response || {
      statement_line_id: bankRow.id,
      ambiguous: false,
      applied: false,
      suggestions: [item],
    });
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
    setImportResult(null);
    try {
      const body = new FormData();
      body.append("cash_bank_id", cashBankId);
      body.append("file", file, file.name);
      body.append("fingerprint", bcaPreview.fingerprint);
      body.append("account_binding", bcaPreview.account_binding);
      const saved = await api.commitCashBankBcaStatement(body);
      bcaFile.current = null;
      setBcaPreview(null);
      setBcaOpen(false);
      const summary = importResultView(saved);
      setImportResult(summary);
      setMessage(summary ? "" : "Impor BCA selesai. Jurnal tidak dibuat.");
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
    setImportResult(null);
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
    setImportResult(null);
    try {
      const saved = await api.commitCashBankStatement(body);
      importFile.current = null;
      setPreview(null);
      setImportOpen(false);
      const summary = importResultView(saved);
      setImportResult(summary);
      setMessage(summary ? "" : "Impor selesai. Jurnal tidak dibuat.");
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
    if (!canReconcile || !selectedBank || !selectedBook || matchingRef.current) return;
    if (!matchKey.current) matchKey.current = newOperationKey();
    const idempotencyKey = matchKey.current;
    matchingRef.current = true;
    setBusy(true);
    setMessage("");
    try {
      const saved = await api.createCashBankPair({
        cash_bank_id: cashBankId,
        statement_line_id: selectedBank,
        journal_line_id: selectedBook,
        idempotency_key: idempotencyKey,
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
      setRecommendationRows([]);
      matchKey.current = "";
      suggestionGen.current += 1;
      await loadWorkspace(cashBankId, startDate, endDate, q);
    } catch (ex) {
      setMessage(errPayload(ex).message);
    } finally {
      matchingRef.current = false;
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
      setRecommendationRows([]);
      suggestionGen.current += 1;
      await loadWorkspace(cashBankId, startDate, endDate, q);
    } catch (ex) {
      setMessage(errPayload(ex).message);
    } finally {
      setBusy(false);
    }
  };

  const decideDuplicate = async (group, line, action) => {
    if (!canReconcile || busy || !line?.statement_line_id) return;
    const reason = (decisionReasons[line.statement_line_id] || "").trim();
    if (reason.length < 3) return;
    if (line.duplicate_status !== "pending_review") return;
    if (action === "keep" && line.active_claim) return;
    if (action === "exclude" && duplicateExcludeReason(group, line)) return;
    const slot = `${line.statement_line_id}|${action}|${reason}`;
    if (!decisionKeys.current[slot]) decisionKeys.current[slot] = newOperationKey();
    setBusy(true);
    setMessage("");
    try {
      await api.resolveCashBankStatementDuplicate(line.statement_line_id, {
        action,
        reason,
        idempotency_key: decisionKeys.current[slot],
      });
      delete decisionKeys.current[slot];
      setDecisionReasons((current) => ({ ...current, [line.statement_line_id]: "" }));
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
  const workspaceInScope = !workspace || (
    (workspace.cash_bank_id == null || workspace.cash_bank_id === cashBankId)
    && (workspace.start_date == null || workspace.start_date === startDate)
    && (workspace.end_date == null || workspace.end_date === endDate)
    && (workspace.search == null || String(workspace.search) === String(q || ""))
  );
  const selectedBankRow = workspaceInScope ? bankRows.find((row) => row.id === selectedBank) : undefined;
  const pageBookRow = workspaceInScope ? bookRows.find((row) => row.journal_line_id === selectedBook) : undefined;

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
  const duplicateGroups = groupAccess.groups || [];
  const pendingTotal = knownCount(pendingAccess.pending_total) ?? knownCount(groupAccess.pending_total);
  const shownPending = duplicateGroups.reduce((total, group) => total + (group.lines || []).filter((line) => line.duplicate_status === "pending_review").length, 0)
    + (pendingAccess.solo_lines || []).length
    + (pendingAccess.outside_period || []).length
    + (pendingAccess.unreachable || []).length;
  const pendingBadge = pendingTotal == null ? "—" : String(pendingTotal);
  const visibleBank = bankRows.filter((row) => !row.claim_id);
  const visibleBook = bookRows.filter((row) => !row.claim_id);
  const suggestionList = suggestions?.suggestions || [];
  const sortedSuggestions = sortCandidateSuggestions(suggestionList, selectedBankRow);
  const suggestionById = new Map(sortedSuggestions.map((item) => [item.journal_line_id, item]));
  const candidateIds = new Set(suggestionById.keys());
  const visibleBookIds = new Set(visibleBook.map((row) => row.journal_line_id));
  const offPageSuggestions = sortedSuggestions.filter((item) => !visibleBookIds.has(item.journal_line_id));
  const suggestionBookRow = (item) => ({
    journal_line_id: item.journal_line_id,
    journal_number: item.journal_number || "—",
    date: item.date,
    description: item.description || item.journal_number || "Kandidat pembukuan",
    direction: item.direction || selectedBankRow?.direction,
    absolute_amount: item.amount,
    currency_code: item.currency_code || selectedBankRow?.currency_code || currency,
    matchable: true,
    status: "posted",
  });
  const selectedBookRow = pageBookRow || sortedSuggestions.map(suggestionBookRow).find((row) => row.journal_line_id === selectedBook);
  const orderedBook = (() => {
    const candidates = [];
    const rest = [];
    visibleBook.forEach((row) => (candidateIds.has(row.journal_line_id) ? candidates : rest).push(row));
    candidates.sort((left, right) => sortedSuggestions.findIndex((item) => item.journal_line_id === left.journal_line_id) - sortedSuggestions.findIndex((item) => item.journal_line_id === right.journal_line_id));
    return [...candidates, ...rest];
  })();
  const selectedBankTotal = selectedBankRow ? Number(selectedBankRow.amount || 0) : 0;
  const selectedBookTotal = selectedBookRow ? Number(selectedBookRow.absolute_amount || 0) : 0;
  const selectionDiff = selectedBank && selectedBook ? selectedBankTotal - selectedBookTotal : null;
  const bankHasCandidate = sortedSuggestions.length > 0;
  const ready = phase === "ready";
  const canMatch = ready && !busy && workspace?.feature_ready !== false && selectedBankRow?.matchable && selectedBookRow?.matchable;
  const matchHint = (() => {
    if (!canReconcile) return "Tidak berizin mencocokkan";
    if (busy) return "Sedang memproses";
    if (!ready) return "Data rekonsiliasi belum siap";
    if (workspace?.feature_ready === false) return workspace.feature_message || "Pencocokan belum tersedia";
    if (!selectedBankRow) return "Belum memilih transaksi bank";
    if (!selectedBankRow.matchable) return /duplikat/i.test(selectedBankRow.block_reason || "") ? "Transaksi menunggu keputusan duplikat" : (selectedBankRow.block_reason || "Transaksi bank tidak dapat dicocokkan");
    if (!selectedBookRow) return "Belum memilih transaksi pembukuan";
    if (!selectedBookRow.matchable) return selectedBookRow.block_reason || "Transaksi pembukuan tidak dapat dicocokkan";
    return "Satu transaksi di setiap sisi";
  })();
  const matched = workspaceInScope ? (workspace?.matched_pairs || []) : [];
  const recommendationViews = (ready && workspaceInScope ? recommendationRows : []).flatMap((entry) => {
    const bankRow = visibleBank.find((row) => row.id === entry.statement_line_id && row.matchable);
    if (!bankRow) return [];
    return sortCandidateSuggestions(entry.suggestions, bankRow)
      .filter((item) => item?.journal_line_id)
      .map((item) => ({ bank: bankRow, item, response: entry.response }));
  });
  const selectionIsRecommendation = recommendationViews.some((row) => row.bank.id === selectedBank && row.item.journal_line_id === selectedBook);
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
  const bookDcMark = (row) => {
    const debit = Number(row?.debit);
    const credit = Number(row?.credit);
    const hasDebit = Number.isFinite(debit) && debit > 0;
    const hasCredit = Number.isFinite(credit) && credit > 0;
    if (hasDebit && !hasCredit) return "(Dr)";
    if (hasCredit && !hasDebit) return "(Cr)";
    return "";
  };
  const amountBits = (row, value, book) => (
    <span className="kb-single-amount">
      {book && bookDcMark(row) ? <span className="kb-dc">{bookDcMark(row)}</span> : null}
      <strong>{money(value, row?.currency_code || currency)}</strong>
      <small>{duplicateDirection(row?.direction)}</small>
    </span>
  );
  const runningLabel = (row) => {
    if (!row || row.running_balance == null || row.running_balance === "") return null;
    const value = Number(row.running_balance);
    if (!Number.isFinite(value)) return null;
    return `SALDO ${money(value, row.currency_code || currency)}`;
  };
  const matchControl = canReconcile ? (
    <button type="button" className="primary sm kb-row-match" disabled={!canMatch} onClick={onMatch} data-testid="kas-bank-pair-match"><Link2 size={15} />{busy ? "Memproses…" : "Cocokkan"}</button>
  ) : null;
  const hintNode = <span className="kb-match-hint" data-testid="kas-bank-match-hint">{matchHint}</span>;
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
        <div className="recon-period"><input aria-label="Tanggal awal" type="date" value={startDate} onChange={(e) => { setStartDate(e.target.value); setSelectedBank(""); setSelectedBook(""); setSuggestions(null); setRecommendationRows([]); matchKey.current = ""; }} disabled={busy} /><span>–</span><input aria-label="Tanggal akhir" type="date" value={endDate} onChange={(e) => { setEndDate(e.target.value); setSelectedBank(""); setSelectedBook(""); setSuggestions(null); setRecommendationRows([]); matchKey.current = ""; }} disabled={busy} /></div>
        <KbSearchField value={qInput} onChange={(e) => setQInput(e.target.value)} onSubmit={() => { setQ(qInput.trim()); setSelectedBank(""); setSelectedBook(""); setSuggestions(null); setRecommendationRows([]); matchKey.current = ""; }} onClear={() => { setQInput(""); setQ(""); setSelectedBank(""); setSelectedBook(""); setSuggestions(null); setRecommendationRows([]); matchKey.current = ""; }} placeholder="Cari uraian / referensi…" testid="kas-bank-recon-search" />
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
      {ready && <p className="recon-context" data-testid="kas-bank-period-close">{workspace?.period_close_note || "Penutupan periode rekonsiliasi belum disediakan."} {workspace?.pair_saved_note || "Pasangan tersimpan bukan rekonsiliasi saldo selesai."}</p>}
      {ready && duplicateMessage && <p className="recon-notice" data-testid="kas-bank-duplicate-message" role="status">{duplicateMessage}</p>}
      {ready && pendingTotal != null && <p className="recon-notice" data-testid="kas-bank-pending-shown" role="status">Ditampilkan {shownPending} dari {pendingTotal} baris menunggu keputusan.</p>}
      {ready && (pendingAccess.other_accounts || []).length > 0 && <p className="recon-notice" data-testid="kas-bank-pending-other-accounts" role="status">Rekening lain masih memiliki baris menunggu keputusan: {pendingAccess.other_accounts.map((item) => `${item.name || "Rekening tidak diketahui"} (${item.pending_count})`).join(", ")}. Badge rekening ini tidak menghitung rekening lain.</p>}
      {ready && groupAccess.truncated && <p className="recon-notice" role="status">Masih ada kelompok duplikat yang belum ditampilkan. <button type="button" className="ghost sm" data-testid="kas-bank-groups-more" onClick={loadMoreGroups} disabled={busy}>Muat kelompok berikutnya</button></p>}
      {ready && pendingAccess.truncated && <p className="recon-notice" role="status">Masih ada baris menunggu keputusan yang belum ditampilkan. <button type="button" className="ghost sm" data-testid="kas-bank-pending-more" onClick={loadMorePending} disabled={busy}>Muat baris berikutnya</button></p>}
      {ready && cashBankId && !currency && <div className="kb-pair-note recon-notice" role="status">
        Mata uang rekening belum ditetapkan. Impor dapat dipratinjau; pencocokan belum tersedia.
        {canManage && <span className="kb-currency-set"><input aria-label="Kode mata uang" value={currencyDraft} onChange={(e) => setCurrencyDraft(e.target.value.toUpperCase())} placeholder="Contoh: IDR" maxLength={3} /><button type="button" className="ghost sm" onClick={saveCurrency} disabled={busy}>Simpan mata uang</button></span>}
      </div>}
      {closing?.manual && <p className="kb-pair-note">Saldo manual {money(closing.manual.amount)} pada {reconDate(closing.manual.as_of)}. Input manual, bukan verifikasi bank.</p>}
      {workspace?.feature_ready === false && <div className="form-error" role="alert"><AlertCircle size={16} /> {workspace.feature_message}</div>}
      {message && !importOpen && <div className="form-error recon-message" role="alert"><AlertCircle size={16} /> {message}</div>}
      {importResult && !importOpen && <div className="kb-pair-note" data-testid="kas-bank-import-result" role={importResult.fully ? "status" : "alert"}>
        <p>{importResult.lead} Jurnal tidak dibuat.</p>
        <p>{importResult.counts.map(([label, count]) => `${label} ${count}`).join(" · ")}</p>
        {importResult.conflicts.map((row, index) => <p key={`${row.source_row || "baris"}-${row.bank_txn_id || index}`}>Baris {row.source_row}: {row.message}{row.bank_txn_id ? ` (${row.bank_txn_id})` : ""}</p>)}
      </div>}

      <div className="kb-pair-tabs" role="tablist" aria-label="Tampilan rekonsiliasi">
        {[["unmatched", "Belum Cocok"], ["matched", "Sudah Cocok"], ["import", "Riwayat Impor"], ["duplicates", `Review Duplikat (${pendingBadge})`]].map(([id, label]) => <button key={id} id={`recon-tab-${id}`} role="tab" aria-selected={tab === id} aria-controls={`recon-panel-${id}`} type="button" className={tab === id ? "is-active" : ""} onClick={() => setTab(id)}>{label}</button>)}
      </div>

      {tab === "unmatched" && <div className="recon-tab-body" role="tabpanel" id="recon-panel-unmatched" aria-labelledby="recon-tab-unmatched">
        <div className="kb-pane-switch" aria-label="Panel"><button type="button" aria-pressed={pane === "bank"} className={pane === "bank" ? "primary sm" : "ghost sm"} onClick={() => setPane("bank")}>Bank</button><button type="button" aria-pressed={pane === "book"} className={pane === "book" ? "primary sm" : "ghost sm"} onClick={() => setPane("book")}>Buku</button></div>
        {recommendationViews.length > 0 && <section className="kb-recommendations" data-testid="kas-bank-recommendations">
          <h2>Rekomendasi pasangan</h2>
          <p>Nominal sama bukan persetujuan. Pilih satu pasangan secara eksplisit sebelum mencocokkan.</p>
          {recommendationViews.map(({ bank: bankRow, item, response }) => {
            const pageBook = bookRows.find((row) => row.journal_line_id === item.journal_line_id);
            const bookAmount = item.amount != null && item.amount !== "" ? item.amount : pageBook?.absolute_amount;
            const bookDisplay = pageBook || {
              journal_number: item.journal_number || "—",
              date: item.date,
              description: item.description || item.journal_number || "Kandidat pembukuan",
              direction: item.direction || bankRow.direction,
              currency_code: item.currency_code || bankRow.currency_code || currency,
            };
            const selected = selectedBank === bankRow.id && selectedBook === item.journal_line_id;
            const notes = candidateNotes(item, bankRow);
            return <div key={`${bankRow.id}:${item.journal_line_id}`} className={`kb-matched-pair kb-recommendation${selected ? " is-selected" : ""}`} data-testid="kas-bank-recommendation">
              <div className="kb-matched-side kb-rec-bank">
                <input type="radio" name="recommendation-pair" checked={selected} onChange={() => selectRecommendation(bankRow, item, response)} disabled={!canReconcile || busy || !bankRow.matchable} aria-label={`Pilih rekomendasi ${bankRow.description || bankRow.id} ${item.journal_number || item.journal_line_id}`} />
                <div><span className="recon-date">{reconDate(bankRow.date)}</span><span className="recon-description">{bankRow.description || "—"}</span><small>{bankRow.reference || "—"}</small>{amountBits(bankRow, bankRow.amount, false)}{runningLabel(bankRow) && <small className="kb-running-balance">{runningLabel(bankRow)}</small>}</div>
              </div>
              <span className="kb-inline-arrow" aria-hidden="true">←</span>
              <div className="kb-matched-side kb-live-book">
                <div><span className="recon-date">{reconDate(bookDisplay.date)}</span><span className="recon-description">{bookDisplay.description || "—"}</span><small>{bookDisplay.journal_number || item.journal_number || "—"}</small>{amountBits(bookDisplay, bookAmount, true)}{notes.map((note) => <small key={note}>{note}</small>)}</div>
                {selected ? <div className="kb-live-action">{hintNode}{matchControl}</div> : null}
              </div>
            </div>;
          })}
        </section>}
        {selectedBankRow && <div className="kb-matched-pair kb-live-pair" data-testid="kas-bank-live-pair">
          <div className="kb-matched-side"><span className="recon-date">{reconDate(selectedBankRow.date)}</span><span className="recon-description">{selectedBankRow.description || "—"}</span><small>{selectedBankRow.reference || "—"}</small>{amountBits(selectedBankRow, selectedBankRow.amount, false)}{runningLabel(selectedBankRow) && <small className="kb-running-balance">{runningLabel(selectedBankRow)}</small>}</div>
          <span className="kb-inline-arrow" aria-hidden="true">←</span>
          <div className="kb-matched-side kb-live-book">{selectedBookRow ? <div><span className="recon-date">{reconDate(selectedBookRow.date)}</span><span className="recon-description">{selectedBookRow.description || "—"}</span><small>{selectedBookRow.journal_number || "—"}</small>{amountBits(selectedBookRow, selectedBookRow.absolute_amount, true)}</div> : <span className="recon-description">Belum memilih transaksi pembukuan</span>}{selectionIsRecommendation ? null : <div className="kb-live-action">{hintNode}{matchControl}</div>}</div>
        </div>}
        <div className={`kb-pair-columns${bankHasCandidate ? " has-link" : ""}`}>
          <section className={`kb-pane ${pane === "bank" ? "is-shown" : ""}`} aria-label="Mutasi bank">
            <div className="recon-pane-title">
              <div><h2>Mutasi Bank</h2><span className="kb-pane-balance">Saldo {ready ? money(closing?.amount) : "—"}</span></div>
              <span className={ready && Number(workspace?.unmatched_bank_count) > 0 ? "kb-unmatched-flag" : ""}>{ready ? (Number(workspace?.unmatched_bank_count) > 0 ? `${workspace.unmatched_bank_count} data belum cocok` : `${visibleBank.length} baris ditampilkan`) : ""}</span>
            </div>
            {stateMessage(bankView, "mutasi bank", visibleBank.length)}
            {bankView === "results" && visibleBank.length > 0 && <div className="kb-pair-scroll"><table className="prod-table kas-bank-table" data-testid="kas-bank-pair-bank-table">
              <thead><tr><th aria-label="Pilih satu baris" /><th>Tanggal</th><th>Uraian</th><th className="num">Nominal</th></tr></thead><tbody>
                {visibleBank.map((row) => {
                  const bankSelected = selectedBank === row.id;
                  const saldo = runningLabel(row);
                  return <tr key={row.id} className={bankSelected ? "kb-row-selected" : ""} onClick={() => { if (!busy && canReconcile) pickBank(row); }}>
                  <td><input type="radio" name="bank-line" checked={bankSelected} onChange={() => pickBank(row)} disabled={!row.matchable || busy || !canReconcile} aria-label={`Pilih bank ${row.description}`} /></td>
                  <td className="recon-date">{reconDate(row.date)}</td><td><span className="recon-description">{row.description || "—"}</span><small>{row.reference || "—"}</small>{bankSelected && <span className="recon-badge kb-selected-badge">Terpilih</span>}{row.original_date === "PEND" && row.date_status === "temporary" && <small>PEND - tanggal sementara</small>}{row.block_reason && <span className="recon-row-warning">{row.block_reason}</span>}{row.identity_guarantee === "tidak_dijamin" && <small>Identitas bank tidak dijamin</small>}{saldo && <small className="kb-running-balance">{saldo}</small>}</td>
                  <td className="num kb-money">{amountBits(row, row.amount, false)}</td>
                </tr>;
                })}
              </tbody></table></div>}
          </section>
          {bankHasCandidate && <div className={`kb-pair-link${selectedBank && selectedBook ? " is-paired" : ""}`} data-testid="kas-bank-pair-link" aria-hidden="true"><strong><span className="kb-link-visible">←</span><span className="kb-sr-keep">↔</span></strong></div>}
          <section className={`kb-pane ${pane === "book" ? "is-shown" : ""}`} aria-label="Pembukuan Eternal">
            <div className="recon-pane-title">
              <div><h2>Pembukuan Eternal</h2><span className="kb-pane-balance">Saldo {ready ? money(workspace?.book_ending_balance) : "—"}</span></div>
              <span>{ready ? `${visibleBook.length} baris ditampilkan` : ""}</span>
            </div>
            {offPageSuggestions.length > 0 && <div className="kb-offpage-candidates" data-testid="kas-bank-offpage-candidates">
              <p>{offPageSuggestions.length} kandidat di luar 50 baris ini. Pilihan bank tetap aktif.</p>
              <table className="prod-table kas-bank-table" data-testid="kas-bank-offpage-table">
                <tbody>
                  {offPageSuggestions.map((item) => {
                    const row = suggestionBookRow(item);
                    const notes = candidateNotes(item, selectedBankRow);
                    const bookSelected = selectedBook === row.journal_line_id;
                    return <tr key={row.journal_line_id} className={bookSelected ? "kb-row-selected" : "kb-row-candidate"}>
                      <td><input type="radio" name="book-line" checked={bookSelected} onChange={() => { setSelectedBook(row.journal_line_id); matchKey.current = newOperationKey(); }} disabled={busy || !canReconcile} aria-label={`Pilih buku ${row.journal_number}`} /></td>
                      <td className="recon-date">{reconDate(row.date)}</td>
                      <td><span className="recon-description">{row.description}</span><small>{row.journal_number}</small>{bookSelected ? <span className="recon-badge kb-selected-badge">Terpilih</span> : <span className="recon-badge kb-candidate-badge">Kandidat</span>}{notes.map((note) => <small key={note} className="kb-candidate-reason">{note}</small>)}</td>
                      <td className="num kb-money">{amountBits(row, row.absolute_amount, true)}</td>
                    </tr>;
                  })}
                </tbody>
              </table>
            </div>}
            {stateMessage(bookView, "pembukuan", visibleBook.length)}
            {bookView === "results" && visibleBook.length > 0 && <div className="kb-pair-scroll"><table className="prod-table kas-bank-table" data-testid="kas-bank-pair-book-table">
              <thead><tr><th aria-label="Pilih satu baris" /><th>Tanggal</th><th>Uraian</th><th className="num">Nominal</th></tr></thead><tbody>
                {orderedBook.map((row) => {
                  const suggestion = suggestionById.get(row.journal_line_id);
                  const isCandidate = Boolean(suggestion);
                  const bookSelected = selectedBook === row.journal_line_id;
                  const notes = isCandidate ? candidateNotes(suggestion, selectedBankRow) : [];
                  return <tr key={row.journal_line_id} className={bookSelected ? "kb-row-selected" : isCandidate ? "kb-row-candidate" : ""}>
                  <td><input type="radio" name="book-line" checked={bookSelected} onChange={() => { setSelectedBook(row.journal_line_id); matchKey.current = newOperationKey(); }} disabled={!row.matchable || busy || !canReconcile} aria-label={`Pilih buku ${row.journal_number}`} /></td>
                  <td className="recon-date">{reconDate(row.date)}</td><td><span className="recon-description">{row.description || "—"}</span><small>{row.source_path && row.source_id ? <Link to={`${row.source_path}?id=${encodeURIComponent(row.source_id)}`}>{row.journal_number}</Link> : row.journal_number}</small>{bookSelected && <span className="recon-badge kb-selected-badge">Terpilih</span>}{isCandidate && !bookSelected && <span className="recon-badge kb-candidate-badge">Kandidat</span>}{notes.map((note) => <small key={note} className="kb-candidate-reason">{note}</small>)}{row.block_reason && <span className="recon-row-warning">{row.block_reason}</span>}<small>{reconStatus(row.status)}</small></td>
                  <td className="num kb-money">{amountBits(row, row.absolute_amount, true)}</td>
                </tr>;
                })}
              </tbody></table></div>}
          </section>
        </div>
        {suggestions?.suggestions?.length > 0 && <details className="recon-suggestions" data-testid="kas-bank-pair-suggestions"><summary><AlertCircle size={15} />{suggestions.ambiguous ? "Beberapa kandidat bernominal sama. Periksa referensi sebelum mencocokkan." : "Ada saran pasangan. Periksa sebelum mencocokkan."}</summary><p>Saran tidak disimpan otomatis.</p><ul>{suggestions.suggestions.map((item) => <li key={item.journal_line_id}>{item.journal_number} · {item.reasons.join(", ")}</li>)}</ul></details>}
        <footer className="kb-pair-footer">
          <div className="kb-foot-side"><span>Pilihan bank</span><strong>{selectedBankRow ? money(selectedBankTotal, selectedBankRow.currency_code || currency) : "—"}</strong>{selectedBankRow && <small>{reconDate(selectedBankRow.date)} · {selectedBankRow.description || "—"}</small>}</div>
          <div className="kb-foot-arrow" aria-hidden="true">←</div>
          <div className="kb-foot-side"><span>Pilihan buku</span><strong>{selectedBookRow ? money(selectedBookTotal, selectedBookRow.currency_code || currency) : "—"}</strong>{selectedBookRow && <small>{reconDate(selectedBookRow.date)} · {selectedBookRow.journal_number || selectedBookRow.description || "—"}</small>}</div>
          <div><span>Selisih pilihan</span><strong>{selectionDiff == null ? "—" : money(selectionDiff)}</strong></div>
          {!selectedBankRow && <div className="recon-footer-action">{hintNode}{matchControl}{canOpenPayment && <Link to="/kas-bank/pembayaran">Buka Pembayaran</Link>}</div>}
          {selectedBankRow && canOpenPayment && <div className="recon-footer-action"><Link to="/kas-bank/pembayaran">Buka Pembayaran</Link></div>}
        </footer>
      </div>}

      {tab === "matched" && <section className="recon-tab-body recon-history" role="tabpanel" id="recon-panel-matched" aria-labelledby="recon-tab-matched">
        <div className="recon-pane-title"><h2>Pasangan Tersimpan</h2><span>Snapshot saat pencocokan, bukan saldo terkini</span></div>
        {!ready && <p className="recon-empty" role="status">{phase === "loading" ? "Memuat pasangan…" : "Pasangan tidak dapat dimuat."}</p>}
        {ready && <div className="kb-pair-scroll"><table className="prod-table kas-bank-table kb-matched-table" data-testid="kas-bank-pair-matched"><thead><tr><th>Pasangan</th><th>Status / Tinjauan</th><th>Aksi</th></tr></thead><tbody>
          {matched.map((row) => {
            const sourceBank = bankRows.find((bank) => bank.id === (row.statement_line_id || row.history_statement_line_id));
            const reconciled = row.status === "active" && row.review_state !== "perlu_tinjauan";
            const sourceBook = bookRows.find((book) => book.journal_line_id === (row.journal_line_id || row.history_journal_line_id));
            const bankViewRow = sourceBank || { direction: row.direction, currency_code: row.currency_code };
            const bookViewRow = sourceBook || { ...(row.book_snapshot || {}), direction: row.direction, currency_code: row.currency_code };
            const saldo = runningLabel(sourceBank);
            return <tr key={row.id} className={reconciled ? "kb-row-reconciled" : ""}><td><div className="kb-matched-pair"><div className="kb-matched-side"><span className="recon-date">{reconDate(sourceBank?.date)}</span><span className="recon-description">{sourceBank?.description || "Detail bank tidak tersedia"}</span><small>{sourceBank?.reference || row.statement_line_id || row.history_statement_line_id || "—"}</small>{amountBits(bankViewRow, row.amount, false)}{saldo && <small className="kb-running-balance">{saldo}</small>}</div><span className="kb-inline-arrow" aria-hidden="true">←</span><div className="kb-matched-side"><span className="recon-date">{reconDate(row.book_snapshot?.date)}</span><span className="recon-description">{row.book_snapshot?.description || "—"}</span><small>{row.book_snapshot?.journal_number || "—"}</small>{amountBits(bookViewRow, row.amount, true)}</div></div></td><td><span className={`recon-badge ${row.review_state === "perlu_tinjauan" ? "is-warning" : reconciled ? "is-matched" : ""}`}>{row.review_state === "perlu_tinjauan" ? "Perlu tinjauan" : reconciled ? "✓ Cocok" : reconStatus(row.status)}</span></td><td>{canReconcile && row.status === "active" && <button type="button" className="ghost sm" disabled={busy} onClick={() => onCancel(row.id)}>Batalkan pencocokan</button>}</td></tr>;
          })}
          {!matched.length && <tr><td colSpan={3}>Belum ada pencocokan baris pada tampilan ini.</td></tr>}
        </tbody></table></div>}
      </section>}

      {tab === "import" && <section className="recon-tab-body recon-history kb-pair-import" role="tabpanel" id="recon-panel-import" aria-labelledby="recon-tab-import" data-testid="kas-bank-pair-import-panel">
        <div className="recon-pane-title"><h2>Riwayat Impor</h2><span>Impor mutasi tidak membuat jurnal</span></div>
        {preview && <div className="recon-import-resume"><span>Pratinjau <strong>{preview.filename}</strong> belum dikonfirmasi.</span><button type="button" className="ghost sm" onClick={() => setImportOpen(true)}>Lanjutkan pratinjau</button></div>}
        {!ready && <p className="recon-empty" role="status">{phase === "loading" ? "Memuat riwayat…" : "Riwayat tidak dapat dimuat."}</p>}
        {ready && <div className="kb-pair-scroll"><table className="prod-table kas-bank-table"><thead><tr><th>Berkas</th><th>Status</th><th className="num">Baris tersimpan</th><th>Hasil</th><th>Cakupan</th></tr></thead><tbody>
          {!imports.length && <tr><td colSpan={5}>Belum ada impor.</td></tr>}
          {imports.map((row) => <tr key={row.id}><td>{row.filename}</td><td><span className="recon-badge">{reconStatus(row.status)}</span></td><td className="num">{row.stored_count ?? "—"}</td><td data-testid={`kas-bank-import-outcome-${row.id}`}>{importOutcomeText(row)}</td><td>{row.coverage_note || "Cakupan belum tersedia"}</td></tr>)}
        </tbody></table></div>}
      </section>}

      {tab === "duplicates" && <section className="recon-tab-body recon-history" role="tabpanel" id="recon-panel-duplicates" aria-labelledby="recon-tab-duplicates" data-testid="kas-bank-duplicate-review">
        <div className="recon-pane-title"><h2>Review Duplikat</h2><span>Keputusan manual. Baris yang dikecualikan tetap tersimpan.</span></div>
        {!ready && <p className="recon-empty" role="status">{phase === "loading" ? "Memuat kelompok duplikat…" : "Kelompok duplikat tidak dapat dimuat."}</p>}
        {ready && !duplicateMessage && !duplicateGroups.length && <p className="recon-empty" role="status">Tidak ada kelompok duplikat pada periode ini.</p>}
        {ready && duplicateGroups.map((group) => {
          const lines = group.lines || [];
          const lineCard = (line, index) => {
            const pending = line.duplicate_status === "pending_review";
            const excludeReason = duplicateExcludeReason(group, line);
            const keepBlocked = Boolean(line.active_claim);
            const reasonTooShort = (decisionReasons[line.statement_line_id] || "").trim().length < 3;
            const decided = line.resolution;
            return (
              <>
                <strong>{duplicateLineLabel(index)}</strong>
                <dl>
                  <dt>Status</dt><dd>{duplicateStatusLabel(line.duplicate_status)} · {line.reconciliation_status === "active" ? "sudah dicocokkan" : "belum dicocokkan"}</dd>
                  <dt>Tanggal</dt><dd>{reconDate(line.date)}</dd>
                  <dt>Nominal</dt><dd>{money(line.amount)}</dd>
                  <dt>Arah</dt><dd>{duplicateDirection(line.direction)}</dd>
                  <dt>Uraian</dt><dd>{line.description || group.description || "—"}</dd>
                  <dt>File impor</dt><dd>{line.filename || "Berkas tidak tersedia"}</dd>
                  <dt>Waktu impor</dt><dd>{reconDateTime(line.imported_at)}</dd>
                </dl>
                {decided && <span data-testid={`duplicate-resolution-${line.statement_line_id}`}>{line.duplicate_status === "excluded" ? "Riwayat pengecualian" : "Sudah dipertahankan"} · {decided.reason} · {decided.actor?.name || "—"} · {reconDateTime(decided.at)}</span>}
                {canReconcile && pending && <div className="kb-dup-actions">
                  <label>Alasan<input data-testid={`duplicate-reason-${line.statement_line_id}`} value={decisionReasons[line.statement_line_id] || ""} onChange={(e) => setDecisionReasons((current) => ({ ...current, [line.statement_line_id]: e.target.value }))} disabled={busy} /></label>
                  <button type="button" className="ghost sm" data-testid={`duplicate-keep-${line.statement_line_id}`} disabled={busy || keepBlocked || reasonTooShort} onClick={() => decideDuplicate(group, line, "keep")}>Pertahankan transaksi ini</button>
                  <button type="button" className="ghost sm" data-testid={`duplicate-exclude-${line.statement_line_id}`} disabled={busy || Boolean(excludeReason) || reasonTooShort} onClick={() => decideDuplicate(group, line, "exclude")}>Kecualikan sebagai duplikat</button>
                  {keepBlocked && <small>Baris sedang diklaim pencocokan.</small>}
                  {excludeReason && <small>{excludeReason}</small>}
                </div>}
              </>
            );
          };
          return (
            <article key={group.group_key || `${group.business_date}|${group.direction}|${group.amount}|${group.normalized_description}|${group.normalized_reference || ""}`} className="kb-dup-group" data-testid="kas-bank-duplicate-group">
              <header>
                <h3>{group.description || "—"}</h3>
                <p>{money(group.amount)} · {group.business_date === "PEND" ? "PEND" : reconDate(group.business_date)} · {duplicateDirection(group.direction)} · {group.candidate_count} kandidat</p>
              </header>
              {lines.length === 2 ? (
                <div className="kb-dup-pair" data-testid="kas-bank-duplicate-pair">
                  <div className="kb-dup-line" data-testid={`duplicate-line-${lines[0].statement_line_id}`}>{lineCard(lines[0], 0)}</div>
                  <div className="kb-dup-sep" aria-hidden="true">↕</div>
                  <div className="kb-dup-line" data-testid={`duplicate-line-${lines[1].statement_line_id}`}>{lineCard(lines[1], 1)}</div>
                </div>
              ) : (
                <ol>
                  {lines.map((line, index) => <li key={line.statement_line_id} className="kb-dup-line" data-testid={`duplicate-line-${line.statement_line_id}`}>{lineCard(line, index)}</li>)}
                </ol>
              )}
            </article>
          );
        })}
        {ready && [["solo_lines", "Baris tunggal menunggu keputusan", "kas-bank-pending-solo"], ["outside_period", "Di luar periode yang sedang dibuka", "kas-bank-pending-outside"], ["unreachable", "Tidak masuk kelompok periode", "kas-bank-pending-unreachable"]].map(([key, title, testid]) => {
          const lines = pendingAccess[key] || [];
          if (!lines.length) return null;
          return (
            <section key={key} data-testid={testid}>
              <h3>{title}</h3>
              <ol>
                {lines.map((line, index) => {
                  const group = { solo: true, description: line.description, lines: [line] };
                  const pending = line.duplicate_status === "pending_review";
                  const excludeReason = duplicateExcludeReason(group, line);
                  const keepBlocked = Boolean(line.active_claim);
                  const reasonTooShort = (decisionReasons[line.statement_line_id] || "").trim().length < 3;
                  return (
                    <li key={line.statement_line_id} className="kb-dup-line" data-testid={`duplicate-line-${line.statement_line_id}`}>
                      <strong>{duplicateLineLabel(index)}</strong>
                      <dl>
                        <dt>Status</dt><dd>{duplicateStatusLabel(line.duplicate_status)} · {line.reconciliation_status === "active" ? "sudah dicocokkan" : "belum dicocokkan"}</dd>
                        <dt>Tanggal</dt><dd>{reconDate(line.date)}</dd>
                        <dt>Nominal</dt><dd>{money(line.amount)}</dd>
                        <dt>Arah</dt><dd>{duplicateDirection(line.direction)}</dd>
                        <dt>Uraian</dt><dd>{line.description || "—"}</dd>
                        <dt>File impor</dt><dd>{line.filename || "Berkas tidak tersedia"}</dd>
                        <dt>Waktu impor</dt><dd>{reconDateTime(line.imported_at)}</dd>
                        <dt>Klaim</dt><dd>{line.active_claim ? "Aktif" : "Tidak ada"}</dd>
                        <dt>Pencocokan</dt><dd>{line.reconciliation_status === "active" ? (line.reconciliation_match_id || "Aktif") : "Tidak ada"}</dd>
                      </dl>
                      {line.screen_reason && <small>{line.screen_reason}</small>}
                      {canReconcile && pending && <div className="kb-dup-actions">
                        <label>Alasan<input data-testid={`duplicate-reason-${line.statement_line_id}`} value={decisionReasons[line.statement_line_id] || ""} onChange={(e) => setDecisionReasons((current) => ({ ...current, [line.statement_line_id]: e.target.value }))} disabled={busy} /></label>
                        <button type="button" className="ghost sm" data-testid={`duplicate-keep-${line.statement_line_id}`} disabled={busy || keepBlocked || reasonTooShort} onClick={() => decideDuplicate(group, line, "keep")}>Pertahankan transaksi ini</button>
                        <button type="button" className="ghost sm" data-testid={`duplicate-exclude-${line.statement_line_id}`} disabled={busy || Boolean(excludeReason) || reasonTooShort} onClick={() => decideDuplicate(group, line, "exclude")}>Kecualikan sebagai duplikat</button>
                        {keepBlocked && <small>Baris sedang diklaim pencocokan.</small>}
                        {excludeReason && <small>{excludeReason}</small>}
                      </div>}
                    </li>
                  );
                })}
              </ol>
            </section>
          );
        })}
      </section>}

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
