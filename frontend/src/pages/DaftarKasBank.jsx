import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, Eye, Pencil, Plus, Search, X } from "lucide-react";
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

const coaLabel = (row) => ({
  code: row?.account?.code || row?.account_code || "—",
  name: row?.account?.name || row?.account_name || "",
});

export default function DaftarKasBank() {
  const { hasPerm } = useAuth();
  const canManage = hasPerm("cash_bank.manage");
  const [items, setItems] = useState([]);
  const [coa, setCoa] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [viewOpen, setViewOpen] = useState(false);
  const [editRow, setEditRow] = useState(null);
  const [viewRow, setViewRow] = useState(null);
  const [name, setName] = useState("");
  const [accountId, setAccountId] = useState("");
  const [description, setDescription] = useState("");
  const [active, setActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [activeFilter, setActiveFilter] = useState("all");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();

  const usedAccountIds = useMemo(() => {
    const set = new Set(items.map((r) => r.account_id).filter(Boolean));
    if (editRow?.account_id) set.delete(editRow.account_id);
    return set;
  }, [items, editRow]);

  const availableCoa = useMemo(
    () => coa.filter((a) => !usedAccountIds.has(a.id) || a.id === accountId),
    [coa, usedAccountIds, accountId],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [cb, accounts] = await Promise.all([
        api.cashBankAccounts(),
        api.ledgerAccounts().catch(() => ({ items: [] })),
      ]);
      setItems(Array.isArray(cb?.items) ? cb.items : []);
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
      const isActive = row.active !== false;
      if (activeFilter === "active" && !isActive) return false;
      if (activeFilter === "inactive" && isActive) return false;
      if (!needle) return true;
      const { code, name: an } = coaLabel(row);
      const blob = [row?.name, row?.description, code, an].join(" ").toLowerCase();
      return blob.includes(needle);
    });
  }, [items, q, activeFilter]);

  const tableView = liveSearchTableView({
    pending: searchPending,
    loading,
    itemCount: filtered.length,
  });

  const openCreate = () => {
    setEditRow(null);
    setName("");
    setAccountId("");
    setDescription("");
    setActive(true);
    setFormError("");
    setFormOpen(true);
  };

  const openEdit = (row) => {
    setEditRow(row);
    setName(row.name || "");
    setAccountId(row.account_id || "");
    setDescription(row.description || "");
    setActive(row.active !== false);
    setFormError("");
    setViewOpen(false);
    setFormOpen(true);
  };

  const openView = (row) => {
    setViewRow(row);
    setViewOpen(true);
  };

  const submit = async (e) => {
    e?.preventDefault?.();
    if (!canManage || saving) return;
    setSaving(true);
    setFormError("");
    try {
      if (editRow) {
        await api.updateCashBankAccount(editRow.id, { name, description, active });
      } else {
        await api.createCashBankAccount({ name, account_id: accountId, description, active });
      }
      setFormOpen(false);
      await load();
    } catch (ex) {
      setFormError(errText(ex));
    } finally {
      setSaving(false);
    }
  };

  if (formOpen) {
    return (
      <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="kas-bank-daftar-page">
        <div className="kb-workspace-panel" data-testid="kas-bank-daftar-form-modal">
          <header className="kb-workspace-panel-head">
            <div className="kb-workspace-panel-title">
              <p className="eyebrow">Kas & Bank</p>
              <h1>{editRow ? "Edit Kas/Bank" : "Tambah Kas/Bank"}</h1>
            </div>
            <button type="button" className="icon-button" onClick={() => !saving && setFormOpen(false)} aria-label="Tutup">
              <X size={16} />
            </button>
          </header>
          <form className="kb-workspace-form emp-form" onSubmit={submit}>
            <div className="kb-workspace-form-body">
              <KbSection title="Informasi Kas/Bank" description="Identitas master dan tautan Akun Perkiraan.">
                <div className="kb-form-grid">
                  <KbField id="kb-name" label="Nama Kas / Bank" required>
                    <input id="kb-name" value={name} onChange={(e) => setName(e.target.value)} required autoFocus data-testid="kas-bank-form-name" />
                  </KbField>
                  <KbField
                    id="kb-account"
                    label="Akun Perkiraan"
                    required
                    hint={editRow ? "Akun tidak dapat diubah setelah dibuat." : "Satu COA hanya untuk satu Kas/Bank."}
                  >
                    <select
                      id="kb-account"
                      value={accountId}
                      onChange={(e) => setAccountId(e.target.value)}
                      required
                      disabled={!!editRow}
                      data-testid="kas-bank-form-account"
                    >
                      <option value="">Pilih akun…</option>
                      {availableCoa.map((a) => (
                        <option key={a.id} value={a.id}>{a.code} — {a.name}</option>
                      ))}
                    </select>
                  </KbField>
                  <KbField id="kb-desc" label="Keterangan" className="kb-span-2">
                    <textarea id="kb-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className="kb-textarea" data-testid="kas-bank-form-description" />
                  </KbField>
                  <label className="kb-check-row kb-span-2" data-testid="kas-bank-form-active-wrap">
                    <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} data-testid="kas-bank-form-active" />
                    <span>Aktif</span>
                  </label>
                </div>
              </KbSection>
              {formError && <p className="error form-error" data-testid="kas-bank-form-error">{formError}</p>}
            </div>
            <footer className="kb-workspace-form-foot emp-modal-foot">
              <div />
              <div className="emp-actions">
                <button type="button" className="ghost" disabled={saving} onClick={() => setFormOpen(false)}>Batal</button>
                <button type="submit" className="primary" disabled={saving} data-testid="kas-bank-form-submit">
                  {saving ? "Menyimpan…" : "Simpan"}
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
      <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="kas-bank-daftar-page">
        <div className="kb-workspace-panel" data-testid="kas-bank-daftar-view-modal">
          <header className="kb-workspace-panel-head">
            <div className="kb-workspace-panel-title">
              <p className="eyebrow">Kas & Bank · Detail</p>
              <h1>{viewRow.name}</h1>
            </div>
            <button type="button" className="icon-button" onClick={() => setViewOpen(false)} aria-label="Tutup">
              <X size={16} />
            </button>
          </header>
          <div className="kb-workspace-form-body">
            <KbSection title="Detail Master">
              <dl className="kb-detail-grid">
                <div><dt>Nama Kas/Bank</dt><dd>{viewRow.name}</dd></div>
                <div><dt>Kode Akun</dt><dd className="mono">{coaLabel(viewRow).code}</dd></div>
                <div><dt>Nama Akun</dt><dd>{coaLabel(viewRow).name || "—"}</dd></div>
                <div>
                  <dt>Status</dt>
                  <dd>
                    <span className={`pill xs ${viewRow.active !== false ? "pill-ok" : "pill-muted"}`}>
                      {viewRow.active !== false ? "Aktif" : "Nonaktif"}
                    </span>
                  </dd>
                </div>
                <div className="kb-span-2"><dt>Keterangan</dt><dd>{viewRow.description || "—"}</dd></div>
              </dl>
            </KbSection>
          </div>
          <footer className="kb-workspace-form-foot emp-modal-foot">
            <div />
            <div className="emp-actions">
              {canManage && (
                <button type="button" className="ghost" onClick={() => openEdit(viewRow)}>
                  <Pencil size={14} /> Edit
                </button>
              )}
              <button type="button" className="primary" onClick={() => setViewOpen(false)}>Tutup</button>
            </div>
          </footer>
        </div>
      </div>
    );
  }

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="kas-bank-daftar-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Kas & Bank</p>
          <h1 data-testid="kas-bank-daftar-title">Daftar Kas & Bank</h1>
          <p className="subhead">Satu Kas/Bank terhubung ke tepat satu Akun Perkiraan</p>
        </div>
        {canManage && (
          <button type="button" className="primary" onClick={openCreate} data-testid="kas-bank-daftar-add">
            <Plus size={14} /> Tambah Kas/Bank
          </button>
        )}
      </div>

      {loadError && (
        <div className="signal-strip" role="alert" data-testid="kas-bank-daftar-error">
          <div><AlertCircle size={16} /><span>{loadError}</span></div>
        </div>
      )}

      <div className="kb-toolbar" data-testid="kas-bank-daftar-toolbar">
        <form className="search-inline" onSubmit={doSearch}>
          <Search size={14} />
          <input
            type="text"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Cari nama atau akun"
            data-testid="kas-bank-daftar-search"
            aria-label="Cari Kas/Bank"
          />
          {q && (
            <button type="button" className="ghost sm" onClick={clearQuery} aria-label="Bersihkan">
              <X size={12} />
            </button>
          )}
        </form>
        <select
          className="kb-toolbar-status"
          value={activeFilter}
          onChange={(e) => setActiveFilter(e.target.value)}
          aria-label="Filter status"
          data-testid="kas-bank-daftar-status-filter"
        >
          <option value="all">Semua status</option>
          <option value="active">Aktif</option>
          <option value="inactive">Nonaktif</option>
        </select>
        <span className="kb-toolbar-count" data-testid="kas-bank-daftar-count">
          {filtered.length} / {items.length}
        </span>
        <div className="kb-toolbar-spacer" />
        {canManage && (
          <button type="button" className="primary" onClick={openCreate} data-testid="kas-bank-daftar-toolbar-add">
            <Plus size={14} /> Tambah
          </button>
        )}
      </div>

      <div className="prod-table-wrap kb-workspace-table-wrap">
        <table className="prod-table kas-bank-table" data-testid="kas-bank-daftar-table">
          <thead>
            <tr>
              <th className="col-text">Nama Kas/Bank</th>
              <th className="col-text">Akun Perkiraan</th>
              <th className="col-fit kb-col-status">Status</th>
              <th className="col-text">Keterangan</th>
              <th className="col-fit kb-col-actions">Aksi</th>
            </tr>
          </thead>
          <tbody data-search-state={tableView}>
            {tableView === "loading" && (
              <tr data-testid="kas-bank-daftar-loading">
                <td colSpan={5} className="muted small">Memuat daftar Kas & Bank…</td>
              </tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={5}>
                  <div className="empty-state" data-testid="kas-bank-daftar-empty">
                    Belum ada master Kas/Bank. Tambah akun kas atau bank untuk mulai transaksi.
                  </div>
                </td>
              </tr>
            )}
            {tableView === "results" && filtered.map((row) => {
              const { code, name: an } = coaLabel(row);
              const isActive = row.active !== false;
              return (
                <tr key={row.id} data-testid={`kas-bank-row-${row.id}`}>
                  <td className="col-text">
                    <span className="kb-primary-text">{row.name}</span>
                  </td>
                  <td className="col-text">
                    <div className="kb-coa-stack">
                      <span className="mono kb-coa-code">{code}</span>
                      <span className="kb-coa-name">{an || "—"}</span>
                    </div>
                  </td>
                  <td className="col-fit kb-col-status">
                    <span className={`pill xs ${isActive ? "pill-ok" : "pill-muted"}`}>
                      {isActive ? "Aktif" : "Nonaktif"}
                    </span>
                  </td>
                  <td className="col-text kb-ellipsis">{row.description || "—"}</td>
                  <td className="col-fit kb-col-actions">
                    <div className="kb-actions">
                      <button type="button" className="ghost sm" onClick={() => openView(row)} data-testid={`kas-bank-view-${row.id}`}>
                        <Eye size={12} /> Lihat
                      </button>
                      {canManage && (
                        <button type="button" className="ghost sm" onClick={() => openEdit(row)} data-testid={`kas-bank-edit-${row.id}`}>
                          <Pencil size={12} /> Edit
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
