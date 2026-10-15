import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertCircle, Eye, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";
import { KbField, KbSection } from "../components/CashBankFormPrimitives";
import { listIdentityBranchId, listIdentityUserId } from "../lib/listIdentity";
import { ledgerListCache } from "../lib/smartListCaches";

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

export default function JurnalUmum() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, hasPerm } = useAuth();
  const listUserId = listIdentityUserId(user);
  const listBranchId = listIdentityBranchId(user);
  const canManage = hasPerm("general_journal.manage");
  const canPost = hasPerm("general_journal.post");
  const canReverse = hasPerm("general_journal.reverse");

  const [items, setItems] = useState([]);
  const [coa, setCoa] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get("status") || "all");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch(searchParams.get("q") || "");
  const requestSeq = useRef(0);
  const prevListUserRef = useRef(listUserId);

  const [mode, setMode] = useState("list"); // list | form | view
  const [editRow, setEditRow] = useState(null);
  const [viewRow, setViewRow] = useState(null);
  const [date, setDate] = useState(todayIso());
  const [description, setDescription] = useState("");
  const [lines, setLines] = useState([emptyLine(), emptyLine()]);
  const [saving, setSaving] = useState(false);
  const [acting, setActing] = useState(false);
  const [formError, setFormError] = useState("");

  // List display cache only — post/reverse/delete always hit the server and invalidate.
  const load = useCallback(async () => {
    const cacheKey = ledgerListCache.buildKey({
      u: listUserId,
      b: listBranchId,
      kind: "general_journals",
    });
    const cached = ledgerListCache.get(cacheKey);
    const hasCache = Boolean(cached?.data);
    if (hasCache) {
      setItems(Array.isArray(cached.data.items) ? cached.data.items : []);
      setCoa(Array.isArray(cached.data.coa) ? cached.data.coa : []);
      setRefreshing(true);
      setLoading(false);
    } else {
      setLoading(true);
      setRefreshing(false);
    }
    const seq = requestSeq.current + 1;
    requestSeq.current = seq;
    setLoadError("");
    try {
      const [gj, accounts] = await Promise.all([
        api.generalJournals(),
        api.ledgerAccounts().catch(() => ({ items: [] })),
      ]);
      if (seq !== requestSeq.current) return;
      const nextItems = Array.isArray(gj?.items) ? gj.items : [];
      const nextCoa = Array.isArray(accounts?.items) ? accounts.items : [];
      ledgerListCache.set(cacheKey, { items: nextItems, coa: nextCoa });
      setItems(nextItems);
      setCoa(nextCoa);
    } catch (ex) {
      if (seq !== requestSeq.current) return;
      setLoadError(errText(ex));
      if (!hasCache) setItems([]);
    } finally {
      if (seq === requestSeq.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [listBranchId, listUserId]);

  const reloadAuthoritative = useCallback(async () => {
    ledgerListCache.invalidate();
    await load();
  }, [load]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (mode !== "list") return;
    const next = new URLSearchParams();
    if (q.trim()) next.set("q", q.trim());
    if (statusFilter && statusFilter !== "all") next.set("status", statusFilter);
    if (next.toString() === searchParams.toString()) return;
    setSearchParams(next, { replace: true });
  }, [mode, q, statusFilter, searchParams, setSearchParams]);

  useEffect(() => {
    if (prevListUserRef.current === listUserId) return;
    ledgerListCache.clear();
    prevListUserRef.current = listUserId;
  }, [listUserId]);

  const filtered = useMemo(() => {
    const needle = String(q || "").trim().toLowerCase();
    return items.filter((row) => {
      if (statusFilter !== "all" && row.status !== statusFilter) return false;
      if (!needle) return true;
      const blob = [
        row?.number, row?.journal_number, row?.description, row?.date, row?.status,
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
    setDate(todayIso());
    setDescription("");
    setLines([emptyLine(), emptyLine()]);
    setFormError("");
    setMode("form");
  };

  const openEdit = (row) => {
    setEditRow(row);
    setDate(row.date || todayIso());
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
      const detail = await api.getGeneralJournal(row.id);
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
    date,
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
        saved = await api.updateGeneralJournal(editRow.id, payload);
      } else {
        saved = await api.createGeneralJournal(payload);
      }
      setEditRow(saved);
      await reloadAuthoritative();
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
      const posted = await api.postGeneralJournal(id);
      setViewRow(posted);
      setMode("view");
      await reloadAuthoritative();
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
      const rev = await api.reverseGeneralJournal(id);
      setViewRow(rev);
      setMode("view");
      await reloadAuthoritative();
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
      await api.deleteGeneralJournal(id);
      setMode("list");
      await reloadAuthoritative();
    } catch (ex) {
      setLoadError(errText(ex));
    } finally {
      setActing(false);
    }
  };

  if (mode === "form") {
    const isDraftEdit = !editRow || editRow.status === "draft";
    return (
      <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="jurnal-umum-page">
        <div className="kb-workspace-panel" data-testid="jurnal-umum-form">
          <header className="kb-workspace-panel-head">
            <div className="kb-workspace-panel-title">
              <p className="eyebrow">Buku Besar</p>
              <h1>{editRow ? "Edit Jurnal Umum" : "Tambah Jurnal Umum"}</h1>
            </div>
            <button type="button" className="icon-button" onClick={() => !saving && setMode("list")} aria-label="Tutup">
              <X size={16} />
            </button>
          </header>
          <form className="kb-workspace-form emp-form" onSubmit={saveAndStay}>
            <div className="kb-workspace-form-body">
              <KbSection title="Header" description="Tanggal dan keterangan jurnal.">
                <div className="kb-form-grid">
                  <KbField id="ju-date" label="Tanggal" required>
                    <input id="ju-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} required disabled={!isDraftEdit} data-testid="jurnal-umum-date" />
                  </KbField>
                  <KbField id="ju-desc" label="Keterangan" className="kb-span-2">
                    <textarea id="ju-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="kb-textarea" disabled={!isDraftEdit} data-testid="jurnal-umum-description" />
                  </KbField>
                </div>
              </KbSection>

              <KbSection title="Baris Jurnal" description="Semua COA legitimate. Minimal 2 baris. Debit XOR Kredit.">
                <div className="prod-table-wrap kb-workspace-table-wrap">
                  <table className="prod-table kas-bank-table" data-testid="jurnal-umum-lines-table">
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
                        <tr key={ln.id} data-testid={`jurnal-umum-line-${ln.id}`}>
                          <td>
                            <select
                              value={ln.account_id}
                              onChange={(e) => updateLine(ln.id, { account_id: e.target.value })}
                              required
                              disabled={!isDraftEdit}
                              data-testid={`jurnal-umum-line-account-${ln.id}`}
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
                              data-testid={`jurnal-umum-line-desc-${ln.id}`}
                            />
                          </td>
                          <td>
                            <input
                              className="kb-amount-input"
                              inputMode="decimal"
                              value={ln.debit}
                              onChange={(e) => updateLine(ln.id, { debit: e.target.value, credit: e.target.value ? "" : ln.credit })}
                              disabled={!isDraftEdit}
                              data-testid={`jurnal-umum-line-debit-${ln.id}`}
                            />
                          </td>
                          <td>
                            <input
                              className="kb-amount-input"
                              inputMode="decimal"
                              value={ln.credit}
                              onChange={(e) => updateLine(ln.id, { credit: e.target.value, debit: e.target.value ? "" : ln.debit })}
                              disabled={!isDraftEdit}
                              data-testid={`jurnal-umum-line-credit-${ln.id}`}
                            />
                          </td>
                          <td>
                            {isDraftEdit && (
                              <button type="button" className="ghost sm" onClick={() => removeLine(ln.id)} disabled={lines.length <= 2} data-testid={`jurnal-umum-line-remove-${ln.id}`}>
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
                  <button type="button" className="ghost sm" onClick={addLine} data-testid="jurnal-umum-add-line">
                    <Plus size={12} /> Tambah baris
                  </button>
                )}
                <dl className="kb-journal-totals" data-testid="jurnal-umum-totals">
                  <dt>Total Debit</dt>
                  <dd data-testid="jurnal-umum-total-debit">{formatRp(totals.debit)}</dd>
                  <dt>Total Kredit</dt>
                  <dd data-testid="jurnal-umum-total-credit">{formatRp(totals.credit)}</dd>
                  <dt>Selisih</dt>
                  <dd data-testid="jurnal-umum-diff">{formatRp(totals.diff)}</dd>
                </dl>
              </KbSection>
              {formError && <p className="error form-error" data-testid="jurnal-umum-form-error">{formError}</p>}
            </div>
            <footer className="kb-workspace-form-foot emp-modal-foot">
              <div />
              <div className="emp-actions">
                <button type="button" className="ghost" disabled={saving} onClick={() => setMode("list")}>Batal</button>
                {isDraftEdit && canManage && (
                  <button type="submit" className="primary" disabled={saving} data-testid="jurnal-umum-save-draft">
                    {saving ? "Menyimpan…" : "Simpan Draft"}
                  </button>
                )}
                {isDraftEdit && canPost && (
                  <button
                    type="button"
                    className="primary"
                    disabled={saving || acting || !totals.balanced}
                    onClick={saveAndPost}
                    data-testid="jurnal-umum-post"
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
      <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="jurnal-umum-page">
        <div className="kb-workspace-panel" data-testid="jurnal-umum-view">
          <header className="kb-workspace-panel-head">
            <div className="kb-workspace-panel-title">
              <p className="eyebrow">Buku Besar · Detail</p>
              <div className="kb-view-head">
                <h1>{viewRow.number}</h1>
                <StatusBadge status={viewRow.status} testid="jurnal-umum-view-status" />
              </div>
            </div>
            <button type="button" className="icon-button" onClick={() => setMode("list")} aria-label="Tutup">
              <X size={16} />
            </button>
          </header>
          <div className="kb-workspace-form-body">
            <KbSection title="Header">
              <dl className="kb-detail-grid">
                <div><dt>Tanggal</dt><dd>{viewRow.date}</dd></div>
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
                  data-testid="jurnal-umum-view-post"
                >
                  Posting
                </button>
              )}
              {viewRow.status === "posted" && canReverse && (
                <button type="button" className="ghost" disabled={acting} onClick={() => doReverse(viewRow.id)} data-testid="jurnal-umum-view-reverse">
                  Reversal
                </button>
              )}
              {viewRow.status === "draft" && canManage && (
                <button type="button" className="ghost" disabled={acting} onClick={() => doDelete(viewRow.id)} data-testid="jurnal-umum-view-delete">
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
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="jurnal-umum-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Buku Besar</p>
          <h1 data-testid="jurnal-umum-title">Jurnal Umum</h1>
          <p className="subhead">
            Draft → Posting → journal_core → Reversal
            {refreshing ? <span data-testid="jurnal-umum-refreshing"> · menyegarkan</span> : null}
          </p>
        </div>
        {canManage && (
          <button type="button" className="primary" onClick={openCreate} data-testid="jurnal-umum-add">
            <Plus size={14} /> Tambah
          </button>
        )}
      </div>

      {loadError && (
        <div className="signal-strip" role="alert">
          <div><AlertCircle size={16} /><span>{loadError}</span></div>
        </div>
      )}

      <div className="kb-toolbar" data-testid="jurnal-umum-toolbar">
        <form className="search-inline" onSubmit={doSearch}>
          <Search size={14} />
          <input
            type="text"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Cari nomor atau keterangan"
            data-testid="jurnal-umum-search"
            aria-label="Cari Jurnal Umum"
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
          data-testid="jurnal-umum-status-filter"
        >
          <option value="all">Semua status</option>
          <option value="draft">Draft</option>
          <option value="posted">Posted</option>
          <option value="reversed">Reversed</option>
        </select>
        <span className="kb-toolbar-count">{filtered.length} / {items.length}</span>
        <div className="kb-toolbar-spacer" />
        {canManage && (
          <button type="button" className="primary" onClick={openCreate} data-testid="jurnal-umum-toolbar-add">
            <Plus size={14} /> Tambah
          </button>
        )}
      </div>

      <div className="prod-table-wrap kb-workspace-table-wrap">
        <table className="prod-table kas-bank-table" data-testid="jurnal-umum-table">
          <thead>
            <tr>
              <th className="col-fit">Tanggal</th>
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
              <tr><td colSpan={7} className="muted small">Memuat Jurnal Umum…</td></tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={7}>
                  <div className="empty-state" data-testid="jurnal-umum-empty">
                    Belum ada Jurnal Umum. Buat draft multi-line lalu posting.
                  </div>
                </td>
              </tr>
            )}
            {tableView === "results" && filtered.map((row) => (
              <tr key={row.id} data-testid={`jurnal-umum-row-${row.id}`}>
                <td className="mono">{row.date}</td>
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
                    <button type="button" className="ghost sm" onClick={() => openView(row)} data-testid={`jurnal-umum-view-${row.id}`}>
                      <Eye size={12} /> Lihat
                    </button>
                    {row.status === "draft" && canManage && (
                      <button type="button" className="ghost sm" onClick={() => openEdit(row)} data-testid={`jurnal-umum-edit-${row.id}`}>
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
