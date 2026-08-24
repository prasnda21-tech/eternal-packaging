import { useCallback, useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, RefreshCw, Search, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";

const TYPE_LABEL = {
  INVENTORY: "Barang",
  SERVICE: "Jasa",
  NON_INVENTORY: "Non-Persediaan",
  GROUP: "Grup",
};

function typeLabel(raw) {
  if (!raw) return "—";
  return TYPE_LABEL[raw] || raw;
}

export default function MasterBarangJasa() {
  const { hasPerm } = useAuth();
  const canSync = hasPerm("master_items.sync");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [itemType, setItemType] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const d = await api.masterItems({ q, item_type: itemType, status, page, page_size: pageSize });
      setData(d);
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Master Barang/Jasa"));
    } finally {
      setLoading(false);
    }
  }, [q, itemType, status, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  const doSearch = (e) => {
    e?.preventDefault?.();
    setPage(1);
    setQ(qInput.trim());
  };
  const clearSearch = () => {
    setQInput("");
    setQ("");
    setPage(1);
  };

  const doSync = async () => {
    if (!canSync || syncing) return;
    setSyncing(true);
    setErr("");
    setSyncResult(null);
    try {
      const r = await api.masterItemsSync();
      setSyncResult(r);
      setPage(1);
      await load();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setErr(
        (typeof det === "object" ? (det.message || det.detail) : det)
        || "Gagal sinkronisasi dari Accurate. Data existing tidak diubah."
      );
    } finally {
      setSyncing(false);
    }
  };

  const summary = data?.summary || {};
  const items = data?.items || [];
  const types = data?.item_types || [];
  const pageCount = data?.page_count || 1;

  return (
    <div className="prod-page master-items-page" data-testid="master-barang-jasa-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1 data-testid="master-items-title">Master Barang/Jasa</h1>
          <p className="subhead">Referensi resmi item Eternal Packaging dari Accurate</p>
        </div>
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

      {err && (
        <div className="signal-strip" data-testid="master-items-error" role="alert">
          <div><AlertCircle size={16} /><span>{err}</span></div>
        </div>
      )}

      {syncResult && (
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
              placeholder="Cari kode / nama"
              data-testid="master-items-search"
            />
            {q && (
              <button type="button" className="ghost sm" onClick={clearSearch} aria-label="Bersihkan">
                <X size={12} />
              </button>
            )}
          </form>
          {types.length > 0 && (
            <select
              value={itemType}
              onChange={(e) => { setItemType(e.target.value); setPage(1); }}
              data-testid="master-items-filter-type"
            >
              <option value="">Semua jenis</option>
              {types.map((t) => (
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
              {types.length > 0 && <th className="col-fit">Jenis</th>}
              <th className="col-fit">Satuan</th>
              {summary.has_active_status && <th className="col-fit">Status</th>}
              <th className="col-fit">Accurate ID</th>
              <th className="col-fit">Terakhir Sync</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={7} className="muted small">Memuat…</td></tr>
            )}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={7} className="muted small" data-testid="master-items-empty">
                  Belum ada Master Barang/Jasa. {canSync ? "Klik Sinkronkan dari Accurate." : "Hubungi Admin untuk sinkronisasi."}
                </td>
              </tr>
            )}
            {!loading && items.map((it) => (
              <tr key={it.id} data-testid={`master-item-${it.accurate_item_id}`}>
                <td className="col-fit mono">{it.item_code || "—"}</td>
                <td className="col-text">{it.item_name || "—"}</td>
                {types.length > 0 && <td className="col-fit">{typeLabel(it.item_type)}</td>}
                <td className="col-fit">{it.unit || "—"}</td>
                {summary.has_active_status && (
                  <td className="col-fit">
                    {it.is_active === true ? "Aktif" : it.is_active === false ? "Nonaktif" : "—"}
                  </td>
                )}
                <td className="col-fit mono">{it.accurate_item_id}</td>
                <td className="col-fit small nowrap">{it.last_synced_at ? formatDate(it.last_synced_at) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <div className="master-pager" data-testid="master-items-pager">
          <button className="ghost sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>Sebelumnya</button>
          <span className="muted small">Halaman {data?.page || page} dari {pageCount} · {data?.total || 0} item</span>
          <button className="ghost sm" disabled={page >= pageCount || loading} onClick={() => setPage((p) => p + 1)}>Berikutnya</button>
        </div>
      )}
    </div>
  );
}
