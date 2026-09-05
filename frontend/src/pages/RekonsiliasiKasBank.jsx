import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckSquare, Eye, Plus, Trash2, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";
import { KbField, KbSection, KbSearchField } from "../components/CashBankFormPrimitives";

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

export default function RekonsiliasiKasBank() {
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
                {tableView === "empty" && (
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
