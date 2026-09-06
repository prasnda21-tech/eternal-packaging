import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, Eye, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";
import { KbField, KbSection } from "../components/CashBankFormPrimitives";

const errText = (ex) => {
  const det = ex?.response?.data?.detail;
  if (typeof det === "string") return det;
  if (det && typeof det === "object" && det.message) return det.message;
  return ex?.message || "Permintaan gagal";
};

const STATUS_META = {
  draft: { className: "kb-status-draft", label: "Draft" },
  posted: { className: "kb-status-posted", label: "Posted" },
  reversed: { className: "kb-status-reversed", label: "Reversed" },
};

const formatMoney = (n) => {
  const v = Number(n || 0);
  return v.toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
};

const formatRp = (n) => `Rp ${formatMoney(n)}`;
const todayIso = () => new Date().toISOString().slice(0, 10);
const newLineId = () => `ln-${Math.random().toString(36).slice(2, 10)}`;

const emptyLine = () => ({
  id: newLineId(),
  account_id: "",
  description: "",
  debit: "",
  credit: "",
});

function StatusBadge({ status, testid }) {
  const meta = STATUS_META[status] || STATUS_META.draft;
  return (
    <span className={`pill xs ${meta.className}`} data-testid={testid}>
      {meta.label}
    </span>
  );
}

function lineMoney(raw) {
  const s = String(raw ?? "").trim();
  if (!s) return 0;
  const n = Number(s);
  return Number.isFinite(n) ? n : 0;
}

export default function SaldoAwal() {
  const { hasPerm } = useAuth();
  const canManage = hasPerm("opening_balance.manage");
  const canPost = hasPerm("opening_balance.post");
  const canReverse = hasPerm("opening_balance.reverse");

  const [items, setItems] = useState([]);
  const [coa, setCoa] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();

  const [mode, setMode] = useState("list"); // list | form | view
  const [editRow, setEditRow] = useState(null);
  const [viewRow, setViewRow] = useState(null);
  const [cutoffDate, setCutoffDate] = useState(todayIso());
  const [description, setDescription] = useState("");
  const [lines, setLines] = useState([emptyLine(), emptyLine()]);
  const [saving, setSaving] = useState(false);
  const [acting, setActing] = useState(false);
  const [formError, setFormError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [ob, accounts] = await Promise.all([
        api.openingBalances(),
        api.ledgerAccounts().catch(() => ({ items: [] })),
      ]);
      setItems(Array.isArray(ob?.items) ? ob.items : []);
      setCoa(Array.isArray(accounts?.items) ? accounts.items : []);
    } catch (ex) {
      setLoadError(errText(ex));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    const needle = String(q || "").trim().toLowerCase();
    return items.filter((row) => {
      if (statusFilter !== "all" && row.status !== statusFilter) return false;
      if (!needle) return true;
      const blob = [
        row?.number, row?.journal_number, row?.description, row?.cutoff_date, row?.status,
      ].join(" ").toLowerCase();
      return blob.includes(needle);
    });
  }, [items, q, statusFilter]);

  const tableView = liveSearchTableView({
    pending: searchPending,
    loading,
    itemCount: filtered.length,
  });

  const totals = useMemo(() => {
    let debit = 0;
    let credit = 0;
    lines.forEach((ln) => {
      debit += lineMoney(ln.debit);
      credit += lineMoney(ln.credit);
    });
    return {
      debit,
      credit,
      diff: Math.round((debit - credit) * 100) / 100,
      balanced: Math.abs(debit - credit) < 0.005 && debit > 0,
    };
  }, [lines]);

  const openCreate = () => {
    setEditRow(null);
    setCutoffDate(todayIso());
    setDescription("");
    setLines([emptyLine(), emptyLine()]);
    setFormError("");
    setMode("form");
  };

  const openEdit = (row) => {
    setEditRow(row);
    setCutoffDate(row.cutoff_date || todayIso());
    setDescription(row.description || "");
    const nextLines = Array.isArray(row.lines) && row.lines.length >= 2
      ? row.lines.map((ln) => ({
        id: ln.id || newLineId(),
        account_id: ln.account_id || "",
        description: ln.description || "",
        debit: Number(ln.debit || 0) > 0 ? String(ln.debit) : "",
        credit: Number(ln.credit || 0) > 0 ? String(ln.credit) : "",
      }))
      : [emptyLine(), emptyLine()];
    setLines(nextLines);
    setFormError("");
    setMode("form");
  };

  const openView = async (row) => {
    try {
      const detail = await api.getOpeningBalance(row.id);
      setViewRow(detail);
      setMode("view");
    } catch (ex) {
      setLoadError(errText(ex));
    }
  };

  const updateLine = (id, patch) => {
    setLines((prev) => prev.map((ln) => (ln.id === id ? { ...ln, ...patch } : ln)));
  };

  const addLine = () => setLines((prev) => [...prev, emptyLine()]);
  const removeLine = (id) => {
    setLines((prev) => (prev.length <= 2 ? prev : prev.filter((ln) => ln.id !== id)));
  };

  const buildPayload = () => ({
    cutoff_date: cutoffDate,
    description,
    lines: lines.map((ln) => ({
      id: ln.id,
      account_id: ln.account_id,
      description: ln.description || "",
      debit: lineMoney(ln.debit),
      credit: lineMoney(ln.credit),
    })),
  });

  const submitDraft = async (e) => {
    e?.preventDefault?.();
    if (!canManage || saving) return null;
    setSaving(true);
    setFormError("");
    try {
      const payload = buildPayload();
      let saved;
      if (editRow) {
        saved = await api.updateOpeningBalance(editRow.id, payload);
      } else {
        saved = await api.createOpeningBalance(payload);
      }
      setEditRow(saved);
      await load();
      return saved;
    } catch (ex) {
      setFormError(errText(ex));
      return null;
    } finally {
      setSaving(false);
    }
  };

  const saveAndStay = async (e) => {
    const saved = await submitDraft(e);
    if (saved) setMode("list");
  };

  const saveAndPost = async () => {
    if (!canPost || !totals.balanced) return;
    const saved = await submitDraft();
    if (!saved?.id) return;
    await doPost(saved.id);
  };

  const doPost = async (id) => {
    if (!canPost || acting) return;
    setActing(true);
    setFormError("");
    try {
      const posted = await api.postOpeningBalance(id);
      setViewRow(posted);
      setMode("view");
      await load();
    } catch (ex) {
      setFormError(errText(ex));
    } finally {
      setActing(false);
    }
  };

  const doReverse = async (id) => {
    if (!canReverse || acting) return;
    setActing(true);
    setFormError("");
    try {
      const rev = await api.reverseOpeningBalance(id);
      setViewRow(rev);
      setMode("view");
      await load();
    } catch (ex) {
      setFormError(errText(ex));
    } finally {
      setActing(false);
    }
  };

  const doDelete = async (id) => {
    if (!canManage || acting) return;
    setActing(true);
    try {
      await api.deleteOpeningBalance(id);
      setMode("list");
      await load();
    } catch (ex) {
      setLoadError(errText(ex));
    } finally {
      setActing(false);
    }
  };

  if (mode === "form") {
    const isDraftEdit = !editRow || editRow.status === "draft";
    return (
      <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="saldo-awal-page">
        <div className="kb-workspace-panel" data-testid="saldo-awal-form">
          <header className="kb-workspace-panel-head">
            <div className="kb-workspace-panel-title">
              <p className="eyebrow">Buku Besar</p>
              <h1>{editRow ? "Edit Saldo Awal" : "Tambah Saldo Awal"}</h1>
            </div>
            <button type="button" className="icon-button" onClick={() => !saving && setMode("list")} aria-label="Tutup">
              <X size={16} />
            </button>
          </header>
          <form className="kb-workspace-form emp-form" onSubmit={saveAndStay}>
            <div className="kb-workspace-form-body">
              <KbSection title="Header" description="Tanggal cut-off dan keterangan saldo awal.">
                <div className="kb-form-grid">
                  <KbField id="sa-cutoff" label="Tanggal Cut-off" required>
                    <input id="sa-cutoff" type="date" value={cutoffDate} onChange={(e) => setCutoffDate(e.target.value)} required disabled={!isDraftEdit} data-testid="saldo-awal-cutoff-date" />
                  </KbField>
                  <KbField id="sa-desc" label="Keterangan" className="kb-span-2">
                    <textarea id="sa-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="kb-textarea" disabled={!isDraftEdit} data-testid="saldo-awal-description" />
                  </KbField>
                </div>
              </KbSection>

              <KbSection title="Baris Saldo Awal" description="Semua COA legitimate. Minimal 2 baris. Debit XOR Kredit (pilih eksplisit).">
                <div className="prod-table-wrap kb-workspace-table-wrap">
                  <table className="prod-table kas-bank-table" data-testid="saldo-awal-lines-table">
                    <thead>
                      <tr>
                        <th className="col-text">Akun</th>
                        <th className="col-text">Keterangan</th>
                        <th className="col-num">Debit</th>
                        <th className="col-num">Kredit</th>
                        <th className="col-fit">Aksi</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lines.map((ln) => (
                        <tr key={ln.id} data-testid={`saldo-awal-line-${ln.id}`}>
                          <td>
                            <select
                              value={ln.account_id}
                              onChange={(e) => updateLine(ln.id, { account_id: e.target.value })}
                              required
                              disabled={!isDraftEdit}
                              data-testid={`saldo-awal-line-account-${ln.id}`}
                            >
                              <option value="">Pilih akun…</option>
                              {coa.map((a) => (
                                <option key={a.id} value={a.id}>
                                  {a.code} — {a.name}{a.account_type ? ` (${a.account_type})` : ""}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td>
                            <input
                              value={ln.description}
                              onChange={(e) => updateLine(ln.id, { description: e.target.value })}
                              disabled={!isDraftEdit}
                              data-testid={`saldo-awal-line-desc-${ln.id}`}
                            />
                          </td>
                          <td>
                            <input
                              className="kb-amount-input"
                              inputMode="decimal"
                              value={ln.debit}
                              onChange={(e) => updateLine(ln.id, { debit: e.target.value, credit: e.target.value ? "" : ln.credit })}
                              disabled={!isDraftEdit}
                              data-testid={`saldo-awal-line-debit-${ln.id}`}
                            />
                          </td>
                          <td>
                            <input
                              className="kb-amount-input"
                              inputMode="decimal"
                              value={ln.credit}
                              onChange={(e) => updateLine(ln.id, { credit: e.target.value, debit: e.target.value ? "" : ln.debit })}
                              disabled={!isDraftEdit}
                              data-testid={`saldo-awal-line-credit-${ln.id}`}
                            />
                          </td>
                          <td>
                            {isDraftEdit && (
                              <button type="button" className="ghost sm" onClick={() => removeLine(ln.id)} disabled={lines.length <= 2} data-testid={`saldo-awal-line-remove-${ln.id}`}>
                                <Trash2 size={12} />
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {isDraftEdit && (
                  <button type="button" className="ghost sm" onClick={addLine} data-testid="saldo-awal-add-line">
                    <Plus size={12} /> Tambah baris
                  </button>
                )}
                <dl className="kb-journal-totals" data-testid="saldo-awal-totals">
                  <dt>Total Debit</dt>
                  <dd data-testid="saldo-awal-total-debit">{formatRp(totals.debit)}</dd>
                  <dt>Total Kredit</dt>
                  <dd data-testid="saldo-awal-total-credit">{formatRp(totals.credit)}</dd>
                  <dt>Selisih</dt>
                  <dd data-testid="saldo-awal-diff">{formatRp(totals.diff)}</dd>
                </dl>
              </KbSection>
              {formError && <p className="error form-error" data-testid="saldo-awal-form-error">{formError}</p>}
            </div>
            <footer className="kb-workspace-form-foot emp-modal-foot">
              <div />
              <div className="emp-actions">
                <button type="button" className="ghost" disabled={saving} onClick={() => setMode("list")}>Batal</button>
                {isDraftEdit && canManage && (
                  <button type="submit" className="primary" disabled={saving} data-testid="saldo-awal-save-draft">
                    {saving ? "Menyimpan…" : "Simpan Draft"}
                  </button>
                )}
                {isDraftEdit && canPost && (
                  <button
                    type="button"
                    className="primary"
                    disabled={saving || acting || !totals.balanced}
                    onClick={saveAndPost}
                    data-testid="saldo-awal-post"
                    title={!totals.balanced ? "Seimbangkan debit dan kredit terlebih dahulu" : "Posting"}
                  >
                    Posting
                  </button>
                )}
              </div>
            </footer>
          </form>
        </div>
      </div>
    );
  }

  if (mode === "view" && viewRow) {
    const journal = viewRow.journal;
    return (
      <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="saldo-awal-page">
        <div className="kb-workspace-panel" data-testid="saldo-awal-view">
          <header className="kb-workspace-panel-head">
            <div className="kb-workspace-panel-title">
              <p className="eyebrow">Buku Besar · Detail</p>
              <div className="kb-view-head">
                <h1>{viewRow.number}</h1>
                <StatusBadge status={viewRow.status} testid="saldo-awal-view-status" />
              </div>
            </div>
            <button type="button" className="icon-button" onClick={() => setMode("list")} aria-label="Tutup">
              <X size={16} />
            </button>
          </header>
          <div className="kb-workspace-form-body">
            <KbSection title="Header">
              <dl className="kb-detail-grid">
                <div><dt>Tanggal Cut-off</dt><dd>{viewRow.cutoff_date}</dd></div>
                <div><dt>No. Voucher</dt><dd className="mono">{viewRow.number}</dd></div>
                <div><dt>No. Jurnal</dt><dd className="mono">{viewRow.journal_number || journal?.number || "—"}</dd></div>
                <div className="kb-span-2"><dt>Keterangan</dt><dd>{viewRow.description || "—"}</dd></div>
              </dl>
            </KbSection>
            <KbSection title="Baris">
              <div className="prod-table-wrap">
                <table className="prod-table kas-bank-table">
                  <thead>
                    <tr>
                      <th>Akun</th>
                      <th>Keterangan</th>
                      <th className="col-num">Debit</th>
                      <th className="col-num">Kredit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(viewRow.lines || []).map((ln) => (
                      <tr key={ln.id}>
                        <td>
                          <div className="kb-coa-stack">
                            <span className="mono kb-coa-code">{ln.account_code || "—"}</span>
                            <span className="kb-coa-name">{ln.account_name || ln.account_id}{ln.account_type ? ` · ${ln.account_type}` : ""}</span>
                          </div>
                        </td>
                        <td>{ln.description || "—"}</td>
                        <td className="num kb-money">{formatRp(ln.debit)}</td>
                        <td className="num kb-money">{formatRp(ln.credit)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <dl className="kb-journal-totals">
                <dt>Total Debit</dt>
                <dd>{formatRp(viewRow.total_debit)}</dd>
                <dt>Total Kredit</dt>
                <dd>{formatRp(viewRow.total_credit)}</dd>
              </dl>
            </KbSection>
            {formError && <p className="error form-error">{formError}</p>}
          </div>
          <footer className="kb-workspace-form-foot emp-modal-foot">
            <div />
            <div className="emp-actions">
              {viewRow.status === "draft" && canManage && (
                <button type="button" className="ghost" onClick={() => openEdit(viewRow)}>
                  <Pencil size={14} /> Edit
                </button>
              )}
              {viewRow.status === "draft" && canPost && (
                <button
                  type="button"
                  className="primary"
                  disabled={acting || Number(viewRow.total_debit) !== Number(viewRow.total_credit) || Number(viewRow.total_debit) <= 0}
                  onClick={() => doPost(viewRow.id)}
                  data-testid="saldo-awal-view-post"
                >
                  Posting
                </button>
              )}
              {viewRow.status === "posted" && canReverse && (
                <button type="button" className="ghost" disabled={acting} onClick={() => doReverse(viewRow.id)} data-testid="saldo-awal-view-reverse">
                  Reversal
                </button>
              )}
              {viewRow.status === "draft" && canManage && (
                <button type="button" className="ghost" disabled={acting} onClick={() => doDelete(viewRow.id)} data-testid="saldo-awal-view-delete">
                  <Trash2 size={14} /> Hapus
                </button>
              )}
              <button type="button" className="primary" onClick={() => setMode("list")}>Tutup</button>
            </div>
          </footer>
        </div>
      </div>
    );
  }

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="saldo-awal-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Buku Besar</p>
          <h1 data-testid="saldo-awal-title">Saldo Awal</h1>
          <p className="subhead">Cut-off · Draft → Posting → journal_core → Reversal</p>
        </div>
        {canManage && (
          <button type="button" className="primary" onClick={openCreate} data-testid="saldo-awal-add">
            <Plus size={14} /> Tambah
          </button>
        )}
      </div>

      {loadError && (
        <div className="signal-strip" role="alert">
          <div><AlertCircle size={16} /><span>{loadError}</span></div>
        </div>
      )}

      <div className="kb-toolbar" data-testid="saldo-awal-toolbar">
        <form className="search-inline" onSubmit={doSearch}>
          <Search size={14} />
          <input
            type="text"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Cari tanggal, nomor, atau keterangan"
            data-testid="saldo-awal-search"
            aria-label="Cari Saldo Awal"
          />
          {q && (
            <button type="button" className="ghost sm" onClick={clearQuery} aria-label="Bersihkan">
              <X size={12} />
            </button>
          )}
        </form>
        <select
          className="kb-toolbar-status"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          aria-label="Filter status"
          data-testid="saldo-awal-status-filter"
        >
          <option value="all">Semua status</option>
          <option value="draft">Draft</option>
          <option value="posted">Posted</option>
          <option value="reversed">Reversed</option>
        </select>
        <span className="kb-toolbar-count">{filtered.length} / {items.length}</span>
        <div className="kb-toolbar-spacer" />
        {canManage && (
          <button type="button" className="primary" onClick={openCreate} data-testid="saldo-awal-toolbar-add">
            <Plus size={14} /> Tambah
          </button>
        )}
      </div>

      <div className="prod-table-wrap kb-workspace-table-wrap">
        <table className="prod-table kas-bank-table" data-testid="saldo-awal-table">
          <thead>
            <tr>
              <th className="col-fit">Tanggal Cut-off</th>
              <th className="col-text">No. Jurnal</th>
              <th className="col-text">Keterangan</th>
              <th className="col-num">Total Debit</th>
              <th className="col-num">Total Kredit</th>
              <th className="col-fit">Status</th>
              <th className="col-fit">Aksi</th>
            </tr>
          </thead>
          <tbody data-search-state={tableView}>
            {tableView === "loading" && (
              <tr><td colSpan={7} className="muted small">Memuat Saldo Awal…</td></tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={7}>
                  <div className="empty-state" data-testid="saldo-awal-empty">
                    Belum ada Saldo Awal. Buat draft multi-COA lalu posting pada tanggal cut-off.
                  </div>
                </td>
              </tr>
            )}
            {tableView === "results" && filtered.map((row) => (
              <tr key={row.id} data-testid={`saldo-awal-row-${row.id}`}>
                <td className="mono">{row.cutoff_date}</td>
                <td>
                  <div className="kb-coa-stack">
                    <span className="kb-doc-number mono">{row.journal_number || "—"}</span>
                    <span className="kb-coa-name mono">{row.number}</span>
                  </div>
                </td>
                <td className="kb-ellipsis">{row.description || "—"}</td>
                <td className="num kb-money">{formatRp(row.total_debit)}</td>
                <td className="num kb-money">{formatRp(row.total_credit)}</td>
                <td><StatusBadge status={row.status} /></td>
                <td>
                  <div className="kb-actions">
                    <button type="button" className="ghost sm" onClick={() => openView(row)} data-testid={`saldo-awal-view-${row.id}`}>
                      <Eye size={12} /> Lihat
                    </button>
                    {row.status === "draft" && canManage && (
                      <button type="button" className="ghost sm" onClick={() => openEdit(row)} data-testid={`saldo-awal-edit-${row.id}`}>
                        <Pencil size={12} /> Edit
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
