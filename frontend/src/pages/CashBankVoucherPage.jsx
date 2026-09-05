import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle, ArrowRight, Eye, Pencil, Plus, Search, Trash2, X,
} from "lucide-react";
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

const fileToDataUrl = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result);
  reader.onerror = reject;
  reader.readAsDataURL(file);
});

function StatusBadge({ status, testid }) {
  const meta = STATUS_META[status] || STATUS_META.draft;
  return (
    <span className={`pill xs ${meta.className}`} data-testid={testid}>
      {meta.label}
    </span>
  );
}

function JournalReadOnly({ journal, accountLabels, title }) {
  if (!journal) return null;
  const lines = Array.isArray(journal.lines) ? journal.lines : [];
  const totalDebit = lines.reduce((s, ln) => s + Number(ln.debit || 0), 0);
  const totalCredit = lines.reduce((s, ln) => s + Number(ln.credit || 0), 0);
  return (
    <KbSection title={title || `Jurnal ${journal.number || ""}`} description="Read-only hasil posting.">
      <div className="kb-journal-block" data-testid="kas-bank-journal-readonly">
        <div className="kb-journal-meta">
          <span className="mono">{journal.number}</span>
          <StatusBadge status={journal.status === "reversed" ? "reversed" : "posted"} />
        </div>
        <div className="kb-journal-lines">
          {lines.map((ln) => {
            const label = accountLabels?.[ln.account_id] || ln.account_id;
            const isDr = Number(ln.debit || 0) > 0;
            const amt = isDr ? Number(ln.debit || 0) : Number(ln.credit || 0);
            return (
              <div key={ln.id} className={`kb-journal-line ${isDr ? "is-debit" : "is-credit"}`}>
                <span className="kb-journal-side">{isDr ? "Debit" : "Credit"}</span>
                <span className="kb-coa-name">{label}</span>
                <span className="kb-journal-amt">{formatRp(amt)}</span>
              </div>
            );
          })}
        </div>
        <dl className="kb-journal-totals">
          <dt>Total Debit</dt>
          <dd>{formatRp(totalDebit)}</dd>
          <dt>Total Credit</dt>
          <dd>{formatRp(totalCredit)}</dd>
        </dl>
      </div>
    </KbSection>
  );
}

/**
 * @param {"receipt"|"payment"|"transfer"} kind
 */
export default function CashBankVoucherPage({ kind }) {
  const { hasPerm } = useAuth();
  const canManage = hasPerm("cash_bank.manage");
  const canPost = hasPerm("cash_bank.post");
  const canReverse = hasPerm("cash_bank.reverse");

  const meta = useMemo(() => {
    if (kind === "receipt") {
      return {
        title: "Penerimaan",
        eyebrow: "Kas & Bank",
        testid: "kas-bank-penerimaan",
        cashLabel: "Kas/Bank Tujuan",
        counterLabel: "Akun Lawan",
        fromCol: "Kas/Bank Tujuan",
        toCol: "Akun Lawan",
        emptyHint: "Belum ada penerimaan. Buat draft lalu posting untuk mencatat jurnal.",
        list: api.cashBankReceipts,
        get: api.getCashBankReceipt,
        create: api.createCashBankReceipt,
        update: api.updateCashBankReceipt,
        remove: api.deleteCashBankReceipt,
        post: api.postCashBankReceipt,
        reverse: api.reverseCashBankReceipt,
      };
    }
    if (kind === "payment") {
      return {
        title: "Pembayaran",
        eyebrow: "Kas & Bank",
        testid: "kas-bank-pembayaran",
        cashLabel: "Kas/Bank Sumber",
        counterLabel: "Akun Lawan",
        fromCol: "Kas/Bank Sumber",
        toCol: "Akun Lawan",
        emptyHint: "Belum ada pembayaran. Buat draft lalu posting untuk mencatat jurnal.",
        list: api.cashBankPayments,
        get: api.getCashBankPayment,
        create: api.createCashBankPayment,
        update: api.updateCashBankPayment,
        remove: api.deleteCashBankPayment,
        post: api.postCashBankPayment,
        reverse: api.reverseCashBankPayment,
      };
    }
    return {
      title: "Transfer",
      eyebrow: "Kas & Bank",
      testid: "kas-bank-transfer",
      cashLabel: "Dari Kas/Bank",
      counterLabel: "Ke Kas/Bank",
      fromCol: "Dari Kas/Bank",
      toCol: "Ke Kas/Bank",
      emptyHint: "Belum ada transfer antar kas/bank.",
      list: api.cashBankTransfers,
      get: api.getCashBankTransfer,
      create: api.createCashBankTransfer,
      update: api.updateCashBankTransfer,
      remove: api.deleteCashBankTransfer,
      post: api.postCashBankTransfer,
      reverse: api.reverseCashBankTransfer,
    };
  }, [kind]);

  const [items, setItems] = useState([]);
  const [cashBanks, setCashBanks] = useState([]);
  const [coa, setCoa] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [viewOpen, setViewOpen] = useState(false);
  const [editRow, setEditRow] = useState(null);
  const [viewRow, setViewRow] = useState(null);
  const [saving, setSaving] = useState(false);
  const [actionBusy, setActionBusy] = useState("");
  const [formError, setFormError] = useState("");
  const [actionError, setActionError] = useState("");

  const [date, setDate] = useState(todayIso());
  const [cashBankId, setCashBankId] = useState("");
  const [counterAccountId, setCounterAccountId] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [destId, setDestId] = useState("");
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [reference, setReference] = useState("");
  const [attachmentFile, setAttachmentFile] = useState(null);
  const [statusFilter, setStatusFilter] = useState("all");

  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();

  const accountLabels = useMemo(() => {
    const map = {};
    cashBanks.forEach((b) => {
      if (b.account_id) {
        const code = b.account?.code || b.account_code || "";
        const name = b.account?.name || b.account_name || b.name || "";
        map[b.account_id] = code ? `${code} — ${name}` : (name || b.account_id);
      }
    });
    coa.forEach((a) => {
      map[a.id] = `${a.code} — ${a.name}`;
    });
    return map;
  }, [cashBanks, coa]);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [listRes, cbRes, coaRes] = await Promise.all([
        meta.list(),
        api.cashBankAccounts().catch(() => ({ items: [] })),
        kind !== "transfer" ? api.ledgerAccounts().catch(() => ({ items: [] })) : Promise.resolve({ items: [] }),
      ]);
      setItems(Array.isArray(listRes?.items) ? listRes.items : []);
      setCashBanks(Array.isArray(cbRes?.items) ? cbRes.items : []);
      setCoa(Array.isArray(coaRes?.items) ? coaRes.items : []);
    } catch (ex) {
      setLoadError(errText(ex));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [meta, kind]);

  useEffect(() => { load(); }, [load]);

  const activeCashBanks = useMemo(
    () => cashBanks.filter((b) => b.active !== false),
    [cashBanks],
  );

  const filtered = useMemo(() => {
    const needle = String(q || "").trim().toLowerCase();
    return items.filter((row) => {
      if (statusFilter !== "all" && row?.status !== statusFilter) return false;
      if (!needle) return true;
      const blob = [
        row?.number,
        row?.description,
        row?.reference,
        row?.cash_bank_name,
        row?.source_cash_bank_name,
        row?.dest_cash_bank_name,
        row?.counter_account_name,
        row?.counter_account_code,
        row?.status,
      ].join(" ").toLowerCase();
      return blob.includes(needle);
    });
  }, [items, q, statusFilter]);

  const tableView = liveSearchTableView({
    pending: searchPending,
    loading,
    itemCount: filtered.length,
  });

  const resetForm = () => {
    setDate(todayIso());
    setCashBankId("");
    setCounterAccountId("");
    setSourceId("");
    setDestId("");
    setAmount("");
    setDescription("");
    setReference("");
    setAttachmentFile(null);
    setFormError("");
    setEditRow(null);
  };

  const openCreate = () => {
    resetForm();
    setFormOpen(true);
  };

  const openEdit = (row) => {
    if (row.status !== "draft") return;
    setEditRow(row);
    setDate(row.date || todayIso());
    setCashBankId(row.cash_bank_id || "");
    setCounterAccountId(row.counter_account_id || "");
    setSourceId(row.source_cash_bank_id || "");
    setDestId(row.dest_cash_bank_id || "");
    setAmount(String(row.amount ?? ""));
    setDescription(row.description || "");
    setReference(row.reference || "");
    setAttachmentFile(null);
    setFormError("");
    setViewOpen(false);
    setFormOpen(true);
  };

  const openView = async (row) => {
    setActionError("");
    try {
      const full = await meta.get(row.id);
      setViewRow(full);
      setViewOpen(true);
    } catch (ex) {
      setActionError(errText(ex));
    }
  };

  const buildPayload = async () => {
    const payload = {
      date,
      amount: Number(String(amount).replace(",", ".")),
      description,
      reference,
    };
    if (kind === "transfer") {
      payload.source_cash_bank_id = sourceId;
      payload.dest_cash_bank_id = destId;
    } else {
      payload.cash_bank_id = cashBankId;
      payload.counter_account_id = counterAccountId;
      if (attachmentFile) {
        payload.attachment_data_url = await fileToDataUrl(attachmentFile);
        payload.attachment_filename = attachmentFile.name;
      }
    }
    return payload;
  };

  const submit = async (e) => {
    e?.preventDefault?.();
    if (!canManage || saving) return;
    setSaving(true);
    setFormError("");
    try {
      const payload = await buildPayload();
      if (editRow) {
        await meta.update(editRow.id, payload);
      } else {
        await meta.create(payload);
      }
      setFormOpen(false);
      await load();
    } catch (ex) {
      setFormError(errText(ex));
    } finally {
      setSaving(false);
    }
  };

  const runAction = async (action, row) => {
    if (actionBusy) return;
    setActionBusy(`${action}-${row.id}`);
    setActionError("");
    try {
      if (action === "post") await meta.post(row.id);
      if (action === "reverse") await meta.reverse(row.id);
      if (action === "delete") await meta.remove(row.id);
      if (viewOpen) setViewOpen(false);
      await load();
    } catch (ex) {
      setActionError(errText(ex));
    } finally {
      setActionBusy("");
    }
  };

  const cashCol = (row) => {
    if (kind === "transfer") return row.source_cash_bank_name || "—";
    return row.cash_bank_name || "—";
  };

  const counterCol = (row) => {
    if (kind === "transfer") return row.dest_cash_bank_name || "—";
    const code = row.counter_account_code || "";
    const name = row.counter_account_name || "";
    return code ? `${code} — ${name}` : (name || "—");
  };

  const viewAccountLabels = useMemo(() => {
    const map = { ...accountLabels };
    if (viewRow) {
      if (viewRow.cash_bank_account_id || viewRow.cash_bank_id) {
        const aid = viewRow.cash_bank_account_id;
        if (aid) map[aid] = viewRow.cash_bank_name || map[aid] || aid;
      }
      if (viewRow.counter_account_id) {
        const code = viewRow.counter_account_code || "";
        const name = viewRow.counter_account_name || "";
        map[viewRow.counter_account_id] = code ? `${code} — ${name}` : (name || map[viewRow.counter_account_id]);
      }
      if (viewRow.source_account_id) {
        map[viewRow.source_account_id] = viewRow.source_cash_bank_name || map[viewRow.source_account_id];
      }
      if (viewRow.dest_account_id) {
        map[viewRow.dest_account_id] = viewRow.dest_cash_bank_name || map[viewRow.dest_account_id];
      }
    }
    return map;
  }, [accountLabels, viewRow]);

  if (formOpen) {
    return (
      <div className="prod-page kas-bank-page kas-bank-workspace" data-testid={`${meta.testid}-page`}>
        <div className="kb-workspace-panel" data-testid={`${meta.testid}-form-modal`}>
          <header className="kb-workspace-panel-head">
            <div className="kb-workspace-panel-title">
              <p className="eyebrow">{meta.eyebrow}</p>
              <h1>{editRow ? `Edit ${meta.title}` : `Tambah ${meta.title}`}</h1>
            </div>
            <button type="button" className="icon-button" onClick={() => !saving && setFormOpen(false)} aria-label="Tutup">
              <X size={16} />
            </button>
          </header>
          <form className="kb-workspace-form emp-form" onSubmit={submit}>
            <div className="kb-workspace-form-body">
              <KbSection title="Informasi Transaksi">
                <div className="kb-form-grid">
                  <KbField id={`${meta.testid}-date`} label="Tanggal" required>
                    <input id={`${meta.testid}-date`} type="date" value={date} onChange={(e) => setDate(e.target.value)} required data-testid={`${meta.testid}-form-date`} />
                  </KbField>
                  <KbField id={`${meta.testid}-amount`} label="Nominal (Rp)" required>
                    <input
                      id={`${meta.testid}-amount`}
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                      required
                      className="kb-amount-input"
                      data-testid={`${meta.testid}-form-amount`}
                    />
                  </KbField>
                  <KbField id={`${meta.testid}-ref`} label="Referensi">
                    <input id={`${meta.testid}-ref`} value={reference} onChange={(e) => setReference(e.target.value)} />
                  </KbField>
                  <KbField id={`${meta.testid}-meta`} label={editRow ? "Nomor" : "Status"}>
                    <div className="kb-meta-readonly">
                      {editRow ? (
                        <span className="mono">{editRow.number}</span>
                      ) : (
                        <StatusBadge status="draft" />
                      )}
                    </div>
                  </KbField>
                  {kind !== "transfer" && (
                    <>
                      <KbField id={`${meta.testid}-cash`} label={meta.cashLabel} required>
                        <select id={`${meta.testid}-cash`} value={cashBankId} onChange={(e) => setCashBankId(e.target.value)} required data-testid={`${meta.testid}-form-cash`}>
                          <option value="">Pilih…</option>
                          {activeCashBanks.map((b) => (
                            <option key={b.id} value={b.id}>{b.name}</option>
                          ))}
                        </select>
                      </KbField>
                      <KbField id={`${meta.testid}-counter`} label={meta.counterLabel} required>
                        <select id={`${meta.testid}-counter`} value={counterAccountId} onChange={(e) => setCounterAccountId(e.target.value)} required data-testid={`${meta.testid}-form-counter`}>
                          <option value="">Pilih akun…</option>
                          {coa.map((a) => (
                            <option key={a.id} value={a.id}>{a.code} — {a.name}</option>
                          ))}
                        </select>
                      </KbField>
                      <KbField id={`${meta.testid}-desc`} label="Keterangan" className="kb-span-2">
                        <textarea id={`${meta.testid}-desc`} value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className="kb-textarea" />
                      </KbField>
                      <KbField id={`${meta.testid}-file`} label="Lampiran" className="kb-span-2" hint="Opsional · JPG, PNG, atau PDF">
                        <input
                          id={`${meta.testid}-file`}
                          type="file"
                          accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
                          onChange={(e) => setAttachmentFile(e.target.files?.[0] || null)}
                          data-testid={`${meta.testid}-form-attachment`}
                        />
                      </KbField>
                    </>
                  )}
                </div>
              </KbSection>

              {kind === "transfer" && (
                <>
                  <KbSection title="Dari → Ke" description="Sumber dan tujuan tidak boleh sama.">
                    <div className="kb-transfer-flow">
                      <KbField id={`${meta.testid}-source`} label="Dari Kas/Bank" required>
                        <select id={`${meta.testid}-source`} value={sourceId} onChange={(e) => setSourceId(e.target.value)} required data-testid={`${meta.testid}-form-source`}>
                          <option value="">Pilih…</option>
                          {activeCashBanks.map((b) => (
                            <option key={b.id} value={b.id}>{b.name}</option>
                          ))}
                        </select>
                      </KbField>
                      <div className="kb-transfer-arrow" aria-hidden="true">
                        <ArrowRight size={16} />
                      </div>
                      <KbField id={`${meta.testid}-dest`} label="Ke Kas/Bank" required>
                        <select id={`${meta.testid}-dest`} value={destId} onChange={(e) => setDestId(e.target.value)} required data-testid={`${meta.testid}-form-dest`}>
                          <option value="">Pilih…</option>
                          {activeCashBanks.map((b) => (
                            <option key={b.id} value={b.id}>{b.name}</option>
                          ))}
                        </select>
                      </KbField>
                    </div>
                  </KbSection>
                  <div className="kb-form-grid">
                    <KbField id={`${meta.testid}-desc`} label="Keterangan" className="kb-span-2">
                      <textarea id={`${meta.testid}-desc`} value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className="kb-textarea" />
                    </KbField>
                  </div>
                </>
              )}

              {formError && <p className="error form-error" data-testid={`${meta.testid}-form-error`}>{formError}</p>}
            </div>
            <footer className="kb-workspace-form-foot emp-modal-foot">
              <div className="muted small">Disimpan sebagai Draft. Posting dilakukan dari list atau detail.</div>
              <div className="emp-actions">
                <button type="button" className="ghost" disabled={saving} onClick={() => setFormOpen(false)}>Batal</button>
                <button type="submit" className="primary" disabled={saving || !canManage} data-testid={`${meta.testid}-form-submit`}>
                  {saving ? "Menyimpan…" : "Simpan Draft"}
                </button>
              </div>
            </footer>
          </form>
        </div>
      </div>
    );
  }

  if (viewOpen && viewRow) {
    return (
      <div className="prod-page kas-bank-page kas-bank-workspace" data-testid={`${meta.testid}-page`}>
        <div className="kb-workspace-panel" data-testid={`${meta.testid}-view-modal`}>
          <header className="kb-workspace-panel-head">
            <div className="kb-view-head kb-workspace-panel-title">
              <p className="eyebrow">{meta.eyebrow} · Detail</p>
              <div className="kb-view-head">
                <h1 className="mono">{viewRow.number}</h1>
                <StatusBadge status={viewRow.status} />
              </div>
            </div>
            <button type="button" className="icon-button" onClick={() => setViewOpen(false)} aria-label="Tutup">
              <X size={16} />
            </button>
          </header>
          <div className="kb-workspace-form-body">
            <KbSection title="Ringkasan Transaksi">
              <dl className="kb-detail-grid">
                <div><dt>Tanggal</dt><dd>{viewRow.date}</dd></div>
                <div><dt>Nominal</dt><dd className="num kb-money">{formatRp(viewRow.amount)}</dd></div>
                {kind === "transfer" ? (
                  <>
                    <div><dt>Dari Kas/Bank</dt><dd>{viewRow.source_cash_bank_name || "—"}</dd></div>
                    <div><dt>Ke Kas/Bank</dt><dd>{viewRow.dest_cash_bank_name || "—"}</dd></div>
                  </>
                ) : (
                  <>
                    <div><dt>{meta.cashLabel}</dt><dd>{viewRow.cash_bank_name || "—"}</dd></div>
                    <div>
                      <dt>{meta.counterLabel}</dt>
                      <dd>
                        <span className="mono">{viewRow.counter_account_code || ""}</span>
                        {viewRow.counter_account_code ? " — " : ""}
                        {viewRow.counter_account_name || "—"}
                      </dd>
                    </div>
                  </>
                )}
                <div><dt>Referensi</dt><dd>{viewRow.reference || "—"}</dd></div>
                <div>
                  <dt>Lampiran</dt>
                  <dd>{viewRow.attachment?.name || (kind === "transfer" ? "—" : "Tidak ada")}</dd>
                </div>
                <div className="kb-span-2"><dt>Keterangan</dt><dd>{viewRow.description || "—"}</dd></div>
              </dl>
            </KbSection>

            <JournalReadOnly
              journal={viewRow.journal}
              accountLabels={viewAccountLabels}
              title={viewRow.journal ? `Jurnal ${viewRow.journal.number}` : "Jurnal"}
            />
            <JournalReadOnly
              journal={viewRow.reversal_journal}
              accountLabels={viewAccountLabels}
              title={viewRow.reversal_journal ? `Jurnal Reversal ${viewRow.reversal_journal.number}` : "Jurnal Reversal"}
            />
          </div>
          <footer className="kb-workspace-form-foot emp-modal-foot">
            <div className="kb-actions">
              {viewRow.status === "draft" && canManage && (
                <button type="button" className="ghost" onClick={() => openEdit(viewRow)}>
                  <Pencil size={14} /> Edit
                </button>
              )}
            </div>
            <div className="emp-actions">
              {viewRow.status === "draft" && canManage && canPost && (
                <button
                  type="button"
                  className="primary"
                  disabled={!!actionBusy}
                  onClick={() => runAction("post", viewRow)}
                  data-testid={`${meta.testid}-view-post`}
                >
                  Posting
                </button>
              )}
              {viewRow.status === "posted" && canReverse && (
                <button
                  type="button"
                  className="ghost danger"
                  disabled={!!actionBusy}
                  onClick={() => runAction("reverse", viewRow)}
                  data-testid={`${meta.testid}-view-reverse`}
                >
                  Batalkan / Reversal
                </button>
              )}
              <button type="button" className="ghost" onClick={() => setViewOpen(false)}>Tutup</button>
            </div>
          </footer>
        </div>
      </div>
    );
  }

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid={`${meta.testid}-page`}>
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">{meta.eyebrow}</p>
          <h1 data-testid={`${meta.testid}-title`}>{meta.title}</h1>
          <p className="subhead">Draft dapat diedit; Posted hanya bisa dibatalkan via Reversal</p>
        </div>
        {canManage && (
          <button type="button" className="primary" onClick={openCreate} data-testid={`${meta.testid}-add`}>
            <Plus size={14} /> Tambah {meta.title}
          </button>
        )}
      </div>

      {(loadError || actionError) && (
        <div className="signal-strip" role="alert" data-testid={`${meta.testid}-error`}>
          <div><AlertCircle size={16} /><span>{loadError || actionError}</span></div>
        </div>
      )}

      <div className="kb-toolbar" data-testid={`${meta.testid}-toolbar`}>
        <form className="search-inline" onSubmit={doSearch}>
          <Search size={14} />
          <input
            type="text"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Cari nomor, keterangan, akun…"
            data-testid={`${meta.testid}-search`}
            aria-label={`Cari ${meta.title}`}
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
          data-testid={`${meta.testid}-status-filter`}
        >
          <option value="all">Semua status</option>
          <option value="draft">Draft</option>
          <option value="posted">Posted</option>
          <option value="reversed">Reversed</option>
        </select>
        <span className="kb-toolbar-count">{filtered.length} / {items.length}</span>
        <div className="kb-toolbar-spacer" />
        {canManage && (
          <button type="button" className="primary" onClick={openCreate} data-testid={`${meta.testid}-toolbar-add`}>
            <Plus size={14} /> Tambah
          </button>
        )}
      </div>

      <div className="prod-table-wrap kb-workspace-table-wrap">
        <table className="prod-table kas-bank-table" data-testid={`${meta.testid}-table`}>
          <thead>
            <tr>
              <th className="col-fit">Nomor</th>
              <th className="col-fit">Tanggal</th>
              <th className="col-text">{kind === "transfer" ? "Dari" : meta.fromCol}</th>
              <th className="col-text">{kind === "transfer" ? "Ke" : meta.toCol}</th>
              <th className="num col-fit">Nominal</th>
              <th className="col-fit kb-col-status">Status</th>
              <th className="col-fit kb-col-actions">Aksi</th>
            </tr>
          </thead>
          <tbody data-search-state={tableView}>
            {tableView === "loading" && (
              <tr data-testid={`${meta.testid}-loading`}>
                <td colSpan={7} className="muted small">Memuat {meta.title.toLowerCase()}…</td>
              </tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={7}>
                  <div className="empty-state" data-testid={`${meta.testid}-empty`}>
                    {meta.emptyHint}
                  </div>
                </td>
              </tr>
            )}
            {tableView === "results" && filtered.map((row) => (
              <tr key={row.id} data-testid={`${meta.testid}-row-${row.id}`}>
                <td className="mono col-fit kb-doc-number">{row.number}</td>
                <td className="col-fit muted small">{row.date}</td>
                <td className="col-text">{cashCol(row)}</td>
                <td className="col-text">
                  {kind === "transfer" ? (
                    counterCol(row)
                  ) : (
                    <div className="kb-coa-stack">
                      <span className="mono kb-coa-code">{row.counter_account_code || "—"}</span>
                      <span className="kb-coa-name">{row.counter_account_name || ""}</span>
                    </div>
                  )}
                </td>
                <td className="num col-fit kb-money">{formatRp(row.amount)}</td>
                <td className="col-fit kb-col-status">
                  <StatusBadge status={row.status} testid={`${meta.testid}-status-${row.id}`} />
                </td>
                <td className="col-fit kb-col-actions">
                  <div className="kb-actions">
                    <button type="button" className="ghost sm" onClick={() => openView(row)} title="Lihat">
                      <Eye size={12} /> Lihat
                    </button>
                    {row.status === "draft" && canManage && (
                      <>
                        <button type="button" className="ghost sm" onClick={() => openEdit(row)} title="Edit">
                          <Pencil size={12} /> Edit
                        </button>
                        {canPost && (
                          <button
                            type="button"
                            className="ghost sm kb-action-primary"
                            disabled={!!actionBusy}
                            onClick={() => runAction("post", row)}
                            data-testid={`${meta.testid}-post-${row.id}`}
                          >
                            Posting
                          </button>
                        )}
                        <button
                          type="button"
                          className="ghost sm danger"
                          disabled={!!actionBusy}
                          onClick={() => runAction("delete", row)}
                          title="Hapus"
                        >
                          <Trash2 size={12} /> Hapus
                        </button>
                      </>
                    )}
                    {row.status === "posted" && canReverse && (
                      <button
                        type="button"
                        className="ghost sm"
                        disabled={!!actionBusy}
                        onClick={() => runAction("reverse", row)}
                        data-testid={`${meta.testid}-reverse-${row.id}`}
                      >
                        Reversal
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
