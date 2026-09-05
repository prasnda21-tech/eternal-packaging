import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, Plus, Search, Trash2, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";

const errText = (ex) => {
  const det = ex?.response?.data?.detail;
  if (typeof det === "string") return det;
  if (det && typeof det === "object" && det.message) return det.message;
  return ex?.message || "Permintaan gagal";
};

export default function AkunPerkiraan() {
  const { hasPerm } = useAuth();
  const canManage = hasPerm("ledger.accounts.manage");
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const data = await api.ledgerAccounts();
      setItems(Array.isArray(data?.items) ? data.items : []);
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
    if (!needle) return items;
    return items.filter((row) => {
      const codeS = String(row?.code || "").toLowerCase();
      const nameS = String(row?.name || "").toLowerCase();
      return codeS.includes(needle) || nameS.includes(needle);
    });
  }, [items, q]);

  const tableView = liveSearchTableView({
    pending: searchPending,
    loading,
    itemCount: filtered.length,
  });

  const openForm = () => {
    setCode("");
    setName("");
    setFormError("");
    setFormOpen(true);
  };

  const submitCreate = async (e) => {
    e?.preventDefault?.();
    if (!canManage || saving) return;
    setSaving(true);
    setFormError("");
    try {
      await api.createLedgerAccount({ code, name });
      setFormOpen(false);
      await load();
    } catch (ex) {
      setFormError(errText(ex));
    } finally {
      setSaving(false);
    }
  };

  const confirmDelete = async () => {
    if (!canManage || !pendingDelete || deleting) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await api.deleteLedgerAccount(pendingDelete.id);
      setPendingDelete(null);
      await load();
    } catch (ex) {
      setDeleteError(errText(ex));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="prod-page" data-testid="akun-perkiraan-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Buku Besar</p>
          <h1 data-testid="akun-perkiraan-title">Akun Perkiraan</h1>
          <p className="subhead">Master kode dan nama akun</p>
        </div>
        {canManage && (
          <button type="button" className="primary" onClick={openForm} data-testid="akun-perkiraan-add">
            <Plus size={14} /> Tambah Akun
          </button>
        )}
      </div>

      {loadError && (
        <div className="signal-strip" role="alert" data-testid="akun-perkiraan-error">
          <div><AlertCircle size={16} /><span>{loadError}</span></div>
        </div>
      )}

      <div className="prod-filters" data-testid="akun-perkiraan-filters">
        <div className="prod-filter-row">
          <form className="search-inline" onSubmit={doSearch}>
            <Search size={14} />
            <input
              type="text"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Cari kode atau nama (live)"
              data-testid="akun-perkiraan-search"
              aria-label="Cari akun perkiraan"
            />
            {q && (
              <button type="button" className="ghost sm" onClick={clearQuery} aria-label="Bersihkan">
                <X size={12} />
              </button>
            )}
          </form>
          <span className="muted small" data-testid="akun-perkiraan-count">
            {filtered.length} / {items.length} akun
          </span>
        </div>
      </div>

      <div className="prod-table-wrap">
        <table className="prod-table" data-testid="akun-perkiraan-table">
          <thead>
            <tr>
              <th className="col-fit">Kode Perkiraan</th>
              <th className="col-text">Nama Akun</th>
              <th className="col-fit">Aksi</th>
            </tr>
          </thead>
          <tbody data-testid="akun-perkiraan-tbody" data-search-state={tableView}>
            {tableView === "loading" && (
              <tr data-testid="akun-perkiraan-loading"><td colSpan={3} className="muted small">Memuat…</td></tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={3} className="muted small" data-testid="akun-perkiraan-empty">
                  Tidak ada akun yang cocok.
                </td>
              </tr>
            )}
            {tableView === "results" && filtered.map((row) => (
              <tr key={row.id} data-testid={`akun-row-${row.code}`}>
                <td className="mono">{row.code}</td>
                <td>{row.name}</td>
                <td>
                  {canManage ? (
                    <button
                      type="button"
                      className="ghost sm danger"
                      onClick={() => { setDeleteError(""); setPendingDelete(row); }}
                      data-testid={`akun-delete-${row.code}`}
                    >
                      <Trash2 size={12} /> Hapus
                    </button>
                  ) : (
                    <span className="muted small">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {formOpen && (
        <div className="modal" role="dialog" aria-modal="true" data-testid="akun-perkiraan-form-modal" onClick={() => !saving && setFormOpen(false)}>
          <div className="modal-inner" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <h3>Tambah Akun</h3>
              <button type="button" className="icon-button" onClick={() => !saving && setFormOpen(false)} aria-label="Tutup">
                <X size={16} />
              </button>
            </header>
            <form className="emp-form" onSubmit={submitCreate} data-testid="akun-perkiraan-form">
              <label>
                Kode Perkiraan
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  required
                  autoFocus
                  data-testid="akun-form-code"
                />
              </label>
              <label>
                Nama Akun
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  data-testid="akun-form-name"
                />
              </label>
              {formError && <p className="error" data-testid="akun-form-error">{formError}</p>}
              <div className="delete-modal-actions">
                <button type="button" className="ghost" disabled={saving} onClick={() => setFormOpen(false)}>Batal</button>
                <button type="submit" className="primary" disabled={saving} data-testid="akun-form-submit">
                  {saving ? "Menyimpan…" : "Simpan"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {pendingDelete && (
        <div className="modal" role="dialog" aria-modal="true" data-testid="akun-perkiraan-delete-modal" onClick={() => !deleting && setPendingDelete(null)}>
          <div className="modal-inner" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head"><h3>Hapus Akun</h3></header>
            <div className="confirm-action-body">
              <p>Hapus akun <strong className="mono">{pendingDelete.code}</strong> — {pendingDelete.name}?</p>
              {deleteError && <p className="error" data-testid="akun-delete-error">{deleteError}</p>}
            </div>
            <div className="delete-modal-actions">
              <button type="button" className="ghost" disabled={deleting} onClick={() => setPendingDelete(null)} data-testid="akun-delete-cancel">Batal</button>
              <button type="button" className="primary danger" disabled={deleting} onClick={confirmDelete} data-testid="akun-delete-confirm">
                {deleting ? "Menghapus…" : "Hapus"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
