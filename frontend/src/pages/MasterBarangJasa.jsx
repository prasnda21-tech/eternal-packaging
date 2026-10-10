import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertCircle, CheckCircle2, Plus, RefreshCw, Search, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";
import { nextMasterSyncBanner } from "./masterItemsSyncBanner";

const TYPE_LABEL = {
  INVENTORY: "Barang",
  SERVICE: "Jasa",
  NON_INVENTORY: "Non-Persediaan",
  GROUP: "Grup",
};

const CREATE_TYPES = [
  { value: "INVENTORY", label: "Barang" },
  { value: "SERVICE", label: "Jasa" },
  { value: "NON_INVENTORY", label: "Non-Persediaan" },
];

const EMPTY_CREATE = {
  item_code: "",
  item_name: "",
  item_type: "INVENTORY",
  unit: "pcs",
  is_active: true,
  production_process: "print",
};

function processLabel(raw) {
  if (raw === "non_print") return "Tidak cetak";
  if (raw === "print") return "Cetak";
  return "Belum diatur";
}

function typeLabel(raw) {
  if (!raw) return "—";
  return TYPE_LABEL[raw] || raw;
}

function originLabel(it) {
  if (it?.origin === "local") return "Lokal";
  if (it?.origin === "accurate") return "Accurate";
  if (it?.accurate_item_id) return "Accurate";
  return "Lokal";
}

export default function MasterBarangJasa() {
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const canSync = hasPerm("master_items.sync");
  const canManage = hasPerm("master_items.manage");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [syncError, setSyncError] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();
  const [itemType, setItemType] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const loadSeq = useRef(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState(EMPTY_CREATE);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [processBusyId, setProcessBusyId] = useState("");
  const [processError, setProcessError] = useState("");

  useEffect(() => { setPage(1); }, [q]);

  useEffect(() => {
    if (!searchPending) return;
    setLoading(true);
    setData((prev) => (prev ? { ...prev, items: [] } : prev));
  }, [searchPending]);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setLoadError("");
    setData((prev) => (prev ? { ...prev, items: [] } : prev));
    try {
      const d = await api.masterItems({ q, item_type: itemType, status, page, page_size: pageSize });
      if (seq !== loadSeq.current) return;
      setData(d);
    } catch (ex) {
      if (seq !== loadSeq.current) return;
      const det = ex.response?.data?.detail;
      setLoadError(typeof det === "object" ? det.message : (det || "Gagal memuat Barang/Jasa"));
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [q, itemType, status, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  const clearSearch = () => {
    clearQuery();
    setPage(1);
  };

  const doSync = async () => {
    if (!canSync || syncing) return;
    setSyncing(true);
    const started = nextMasterSyncBanner("start");
    setSyncError(started.error);
    setSyncResult(started.result);
    try {
      const r = await api.masterItemsSync();
      const ok = nextMasterSyncBanner("success", r);
      setSyncError(ok.error);
      setSyncResult(ok.result);
      setPage(1);
      await load();
    } catch (ex) {
      const fail = nextMasterSyncBanner("fail");
      setSyncError(fail.error);
      setSyncResult(fail.result);
    } finally {
      setSyncing(false);
    }
  };

  const openCreate = () => {
    if (!canManage) return;
    setCreateForm(EMPTY_CREATE);
    setCreateError("");
    setCreateOpen(true);
  };

  const submitCreate = async (e) => {
    e.preventDefault();
    if (!canManage || creating) return;
    setCreating(true);
    setCreateError("");
    try {
      await api.masterItemsCreate({
        item_code: createForm.item_code.trim(),
        item_name: createForm.item_name.trim(),
        item_type: createForm.item_type,
        unit: createForm.unit.trim(),
        is_active: Boolean(createForm.is_active),
        production_process: createForm.production_process === "non_print" ? "non_print" : "print",
      });
      setCreateOpen(false);
      setCreateForm(EMPTY_CREATE);
      setPage(1);
      await load();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setCreateError(typeof det === "object" ? (det.message || JSON.stringify(det)) : (det || "Gagal menambah Barang/Jasa"));
    } finally {
      setCreating(false);
    }
  };

  const saveProductionProcess = async (itemId, production_process) => {
    if (!canManage || !itemId || processBusyId) return;
    setProcessBusyId(itemId);
    setProcessError("");
    try {
      const updated = await api.masterItemsSetProductionProcess(itemId, production_process);
      setData((prev) => {
        if (!prev?.items) return prev;
        return {
          ...prev,
          items: prev.items.map((row) => (
            row.id === itemId || row.accurate_item_id === itemId
              ? { ...row, production_process: updated.production_process }
              : row
          )),
        };
      });
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setProcessError(typeof det === "object" ? (det.message || JSON.stringify(det)) : (det || "Gagal menyimpan default Cetak/Tidak cetak"));
    } finally {
      setProcessBusyId("");
    }
  };

  const bannerError = syncError || loadError || processError;

  const summary = data?.summary || {};
  const items = data?.items || [];
  const types = data?.item_types || [];
  const pageCount = data?.page_count || 1;
  const tableBusy = searchPending || loading;

  const tableView = liveSearchTableView({
    pending: searchPending,
    loading,
    itemCount: items.length,
  });

  const typeOptions = types.length
    ? types
    : CREATE_TYPES.map((t) => t.value);

  return (
    <div className="prod-page master-items-page" data-testid="master-barang-jasa-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1 data-testid="master-items-title">Barang/Jasa</h1>
          <p className="subhead">Referensi item Eternal Packaging (Accurate dan lokal)</p>
        </div>
        <div className="prod-heading-actions" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {canManage && (
            <button
              type="button"
              className="primary"
              onClick={openCreate}
              data-testid="master-items-create-button"
            >
              <Plus size={14} />
              Tambah Barang/Jasa
            </button>
          )}
          {canSync && (
            <button
              className="primary"
              onClick={doSync}
              disabled={syncing}
              data-testid="master-items-sync-button"
            >
              <RefreshCw size={14} className={syncing ? "spin" : ""} />
              {syncing ? "Menyinkronkan…" : "Sinkronkan dari Accurate"}
            </button>
          )}
        </div>
      </div>

      {bannerError && (
        <div className="signal-strip" data-testid="master-items-error" role="alert">
          <div><AlertCircle size={16} /><span>{bannerError}</span></div>
        </div>
      )}

      {syncResult && !syncError && (
        <section className="master-sync-result" data-testid="master-items-sync-result">
          <CheckCircle2 size={16} />
          <div>
            <strong>{syncResult.message}</strong>
            <p>
              Baru: {syncResult.created ?? 0}
              {" · "}Diperbarui: {syncResult.updated ?? 0}
              {" · "}Tidak Berubah: {syncResult.unchanged ?? 0}
              {" · "}Gagal: {syncResult.failed ?? 0}
            </p>
          </div>
        </section>
      )}

      {createOpen && (
        <section className="master-create-panel" data-testid="master-items-create-panel" style={{ marginBottom: 16, padding: 16, border: "1px solid var(--border, #d5dde5)", borderRadius: 8 }}>
          <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>Tambah Barang/Jasa lokal</h2>
          <form onSubmit={submitCreate} data-testid="master-items-create-form">
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
              <label>
                <span className="muted small">Kode</span>
                <input
                  required
                  value={createForm.item_code}
                  onChange={(e) => setCreateForm((p) => ({ ...p, item_code: e.target.value }))}
                  data-testid="master-create-code"
                />
              </label>
              <label>
                <span className="muted small">Nama</span>
                <input
                  required
                  value={createForm.item_name}
                  onChange={(e) => setCreateForm((p) => ({ ...p, item_name: e.target.value }))}
                  data-testid="master-create-name"
                />
              </label>
              <label>
                <span className="muted small">Jenis</span>
                <select
                  value={createForm.item_type}
                  onChange={(e) => setCreateForm((p) => ({ ...p, item_type: e.target.value }))}
                  data-testid="master-create-type"
                >
                  {CREATE_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </label>
              <label>
                <span className="muted small">Satuan</span>
                <input
                  required
                  value={createForm.unit}
                  onChange={(e) => setCreateForm((p) => ({ ...p, unit: e.target.value }))}
                  data-testid="master-create-unit"
                />
              </label>
              <label>
                <span className="muted small">Status</span>
                <select
                  value={createForm.is_active ? "active" : "inactive"}
                  onChange={(e) => setCreateForm((p) => ({ ...p, is_active: e.target.value === "active" }))}
                  data-testid="master-create-status"
                >
                  <option value="active">Aktif</option>
                  <option value="inactive">Nonaktif</option>
                </select>
              </label>
              <label>
                <span className="muted small">Default Cetak / Tidak cetak</span>
                <select
                  value={createForm.production_process === "non_print" ? "non_print" : "print"}
                  onChange={(e) => setCreateForm((p) => ({ ...p, production_process: e.target.value }))}
                  data-testid="master-create-production-process"
                >
                  <option value="print">Cetak — ikuti tahap produksi hingga kirim</option>
                  <option value="non_print">Tidak cetak — siap kirim (packing dilewati)</option>
                </select>
              </label>
            </div>
            <p className="muted small" style={{ marginTop: 8 }} data-testid="master-create-process-hint">
              Default ini disalin ke baris pesanan baru. Pilihan di transaksi tetap bisa diubah. Sync Accurate tidak menimpa pengaturan ini.
            </p>
            {createError && (
              <p role="alert" data-testid="master-create-error" className="muted small" style={{ color: "#b42318", marginTop: 8 }}>{createError}</p>
            )}
            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button type="submit" className="primary" disabled={creating} data-testid="master-create-submit">
                {creating ? "Menyimpan…" : "Simpan"}
              </button>
              <button type="button" className="ghost" disabled={creating} onClick={() => setCreateOpen(false)} data-testid="master-create-cancel">
                Batal
              </button>
            </div>
          </form>
        </section>
      )}

      <section className="dash-kpi-row master-kpi-row" data-testid="master-items-summary">
        <div className="dash-kpi"><span>Total Item</span><strong data-testid="master-kpi-total">{summary.total ?? 0}</strong></div>
        {summary.has_active_status && (
          <>
            <div className="dash-kpi"><span>Aktif</span><strong data-testid="master-kpi-active">{summary.active ?? 0}</strong></div>
            <div className="dash-kpi"><span>Nonaktif</span><strong data-testid="master-kpi-inactive">{summary.inactive ?? 0}</strong></div>
          </>
        )}
        <div className="dash-kpi">
          <span>Terakhir Sinkronisasi</span>
          <strong className="master-kpi-time" data-testid="master-kpi-last-sync">
            {summary.last_synced_at ? formatDate(summary.last_synced_at) : "—"}
          </strong>
        </div>
      </section>

      <div className="prod-filters" data-testid="master-items-filters">
        <div className="prod-filter-row">
          <form className="search-inline" onSubmit={doSearch}>
            <Search size={14} />
            <input
              type="text"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Cari kode / nama / Accurate ID (live)"
              data-testid="master-items-search"
              aria-label="Cari barang/jasa live"
            />
            {q && (
              <button type="button" className="ghost sm" onClick={clearSearch} aria-label="Bersihkan">
                <X size={12} />
              </button>
            )}
          </form>
          {typeOptions.length > 0 && (
            <select
              value={itemType}
              onChange={(e) => { setItemType(e.target.value); setPage(1); }}
              data-testid="master-items-filter-type"
            >
              <option value="">Semua jenis</option>
              {typeOptions.map((t) => (
                <option key={t} value={t}>{typeLabel(t)}</option>
              ))}
            </select>
          )}
          {summary.has_active_status && (
            <select
              value={status}
              onChange={(e) => { setStatus(e.target.value); setPage(1); }}
              data-testid="master-items-filter-status"
            >
              <option value="">Semua status</option>
              <option value="active">Aktif</option>
              <option value="inactive">Nonaktif</option>
            </select>
          )}
          <select
            value={pageSize}
            onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
            data-testid="master-items-page-size"
          >
            <option value={25}>25 / halaman</option>
            <option value={50}>50 / halaman</option>
            <option value={100}>100 / halaman</option>
          </select>
        </div>
      </div>

      <div className="prod-table-wrap master-table-wrap">
        <table className="prod-table master-items-table" data-testid="master-items-table">
          <thead>
            <tr>
              <th className="col-fit">Kode</th>
              <th className="col-text">Nama Item</th>
              <th className="col-fit">Jenis</th>
              <th className="col-fit">Satuan</th>
              <th className="col-fit">Cetak / Tidak cetak</th>
              <th className="col-fit">Status</th>
              <th className="col-fit">Sumber</th>
              <th className="col-fit">Accurate ID</th>
              <th className="col-fit">Terakhir Sync</th>
            </tr>
          </thead>
          <tbody data-testid="master-items-tbody" data-search-state={tableView}>
            {tableView === "loading" && (
              <tr data-testid="master-items-loading"><td colSpan={9} className="muted small">Memuat…</td></tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={9} className="muted small" data-testid="master-items-empty">
                  Belum ada Barang/Jasa.
                  {canManage ? " Klik Tambah Barang/Jasa." : ""}
                  {canSync ? " Atau Sinkronkan dari Accurate." : (!canManage ? " Hubungi Admin." : "")}
                </td>
              </tr>
            )}
            {tableView === "results" && items.map((it) => (
              <tr
                key={it.id}
                data-testid={`master-item-${it.id || it.accurate_item_id}`}
                data-origin={originLabel(it)}
                onClick={() => navigate(`/persediaan/master-barang-jasa/${it.id || it.accurate_item_id}`)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    navigate(`/persediaan/master-barang-jasa/${it.id || it.accurate_item_id}`);
                  }
                }}
                tabIndex={0}
                style={{ cursor: "pointer" }}
                role="button"
                aria-label={`Lihat detail ${it.item_name || it.item_code}`}
              >
                <td className="col-fit mono">{it.item_code || "—"}</td>
                <td className="col-text">{it.item_name || "—"}</td>
                <td className="col-fit">{typeLabel(it.item_type)}</td>
                <td className="col-fit">{it.unit || "—"}</td>
                <td className="col-fit" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                  {canManage ? (
                    <select
                      value={it.production_process === "non_print" ? "non_print" : it.production_process === "print" ? "print" : ""}
                      disabled={processBusyId === it.id}
                      onChange={(e) => {
                        const next = e.target.value;
                        if (!next) return;
                        saveProductionProcess(it.id, next);
                      }}
                      data-testid={`master-item-process-${it.id}`}
                      aria-label={`Default Cetak/Tidak cetak ${it.item_name || it.item_code}`}
                    >
                      {!it.production_process && <option value="">Belum diatur</option>}
                      <option value="print">Cetak</option>
                      <option value="non_print">Tidak cetak</option>
                    </select>
                  ) : (
                    <span data-testid={`master-item-process-label-${it.id}`}>{processLabel(it.production_process)}</span>
                  )}
                </td>
                <td className="col-fit">
                  {it.is_active === true ? "Aktif" : it.is_active === false ? "Nonaktif" : "—"}
                </td>
                <td className="col-fit" data-testid={`master-item-origin-${it.id}`}>{originLabel(it)}</td>
                <td className="col-fit mono">{it.accurate_item_id || "—"}</td>
                <td className="col-fit small nowrap">{it.last_synced_at ? formatDate(it.last_synced_at) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <div className="master-pager" data-testid="master-items-pager">
          <button className="ghost sm" disabled={page <= 1 || tableBusy} onClick={() => setPage((p) => p - 1)}>Sebelumnya</button>
          <span className="muted small">Halaman {data?.page || page} dari {pageCount} · {data?.total || 0} item</span>
          <button className="ghost sm" disabled={page >= pageCount || tableBusy} onClick={() => setPage((p) => p + 1)}>Berikutnya</button>
        </div>
      )}
    </div>
  );
}
