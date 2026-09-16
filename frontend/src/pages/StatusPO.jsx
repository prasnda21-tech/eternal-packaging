import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { RefreshCw, Trash2, XCircle } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import ConfirmActionModal from "../components/ConfirmActionModal";
import StatusPoWorkflowTable from "../components/StatusPoWorkflowTable";
import "./StatusPO.css";
import {
  headerCheckState,
  pruneSelection,
  removeSucceeded,
  visibleRowIds,
} from "../lib/rowSelection";
import {
  EMPTY_STATUS_PO_FILTERS,
  matchesStatusPoFilters,
  statusPoFilterOptions,
} from "../lib/statusPo";
import { isStatusPoMatrixPreviewEnabled, buildStatusPoMatrixPreviewRows } from "../lib/statusPoPreview";
import {
  readListUiState,
  storageUserKey,
  writeListUiState,
} from "../lib/poListReturn";

export default function StatusPO() {
  const { user, hasPerm } = useAuth();
  const location = useLocation();
  const [params] = useSearchParams();
  const deliveryTodayPage = location.pathname.startsWith("/produksi/pengiriman-hari-ini");
  const view = deliveryTodayPage ? "delivery_today" : (params.get("view") || "");
  const routeKey = deliveryTodayPage ? "pengiriman-hari-ini" : `status-po.${view || "default"}`;
  const userKey = storageUserKey(user);
  const savedFilters = readListUiState(userKey, routeKey, EMPTY_STATUS_PO_FILTERS);
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [filters, setFilters] = useState({ ...EMPTY_STATUS_PO_FILTERS, ...savedFilters });
  const [selected, setSelected] = useState(new Set());
  const [bulkAction, setBulkAction] = useState(null);
  const [bulkReason, setBulkReason] = useState("");
  const [bulkConfirm, setBulkConfirm] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkErr, setBulkErr] = useState("");
  const inFlight = useRef(false);
  const loadInFlight = useRef(false);
  const headerRef = useRef(null);

  const canCancel = hasPerm("po.cancel");
  const canDelete = hasPerm("po.delete");
  const canSelect = canCancel || canDelete;

  const load = useCallback(async () => {
    if (loadInFlight.current) return;
    loadInFlight.current = true;
    setBusy(true);
    setErr("");
    try {
      if (isStatusPoMatrixPreviewEnabled(location.search)) {
        setRows(buildStatusPoMatrixPreviewRows());
        return;
      }
      const d = await api.statusPo(view ? { view } : {});
      setRows(d.rows || []);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Status PO"));
    } finally {
      setBusy(false);
      loadInFlight.current = false;
    }
  }, [view, location.search]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    writeListUiState(userKey, routeKey, filters);
  }, [userKey, routeKey, filters]);

  const visible = useMemo(
    () => (rows || []).filter((r) => matchesStatusPoFilters(r, filters)),
    [rows, filters],
  );
  const visibleIds = useMemo(() => visibleRowIds(visible), [visible]);
  const visibleIdKey = visibleIds.join(",");

  useEffect(() => {
    setSelected((prev) => pruneSelection(prev, visibleIds));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleIdKey]);

  const headerState = headerCheckState(selected, visibleIds);
  useEffect(() => {
    if (headerRef.current) headerRef.current.indeterminate = headerState.indeterminate;
  }, [headerState.indeterminate]);

  const statusOptions = useMemo(
    () => statusPoFilterOptions(rows, filters.status),
    [rows, filters.status],
  );

  const setFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));

  const openBulk = (action) => {
    if (bulkBusy || inFlight.current || !selected.size) return;
    setBulkErr("");
    setBulkReason("");
    setBulkConfirm("");
    setBulkAction(action);
  };

  const closeBulk = () => {
    if (bulkBusy) return;
    setBulkAction(null);
    setBulkErr("");
    setBulkReason("");
    setBulkConfirm("");
  };

  const runBulk = async () => {
    if (inFlight.current || bulkBusy || !bulkAction) return;
    const ids = [...selected];
    if (!ids.length) return;
    const reason = bulkReason.trim();
    if (!reason) {
      setBulkErr("Alasan wajib diisi.");
      return;
    }
    if (bulkAction === "delete" && (bulkConfirm || "").trim().toUpperCase() !== "HAPUS") {
      setBulkErr("Ketik HAPUS untuk konfirmasi.");
      return;
    }
    inFlight.current = true;
    setBulkBusy(true);
    setBulkErr("");
    try {
      const d = bulkAction === "delete"
        ? await api.bulkDelete(ids, reason, "HAPUS")
        : await api.bulkCancel(ids, reason);
      const okIds = (d.succeeded || []).map((s) => s.id).filter(Boolean);
      if (okIds.length) {
        setSelected((prev) => removeSucceeded(prev, okIds));
      } else if (!(d.failed || []).length) {
        setSelected(new Set());
      }
      const failed = d.failed || [];
      if (failed.length) {
        const nOk = d.cancelled_count ?? d.deleted_count ?? okIds.length;
        setBulkErr(`${nOk} berhasil, ${failed.length} gagal. ${failed.map((f) => f.reason || f.id).join("; ")}`);
      } else {
        setBulkAction(null);
        setBulkReason("");
        setBulkConfirm("");
      }
      await load();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setBulkErr(typeof det === "object" ? (det.message || "Gagal") : (det || "Gagal memproses PO terpilih"));
    } finally {
      setBulkBusy(false);
      inFlight.current = false;
    }
  };

  const title = deliveryTodayPage ? "Pengiriman Hari Ini" : "Status PO";

  return (
    <div className="ship-status-page status-po-page" data-testid="status-po-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading spo-heading">
          <h1>{title}</h1>
          {!deliveryTodayPage && (
            <p className="spo-sub" data-testid="status-po-subtitle">
              Pantau progress setiap PO dari order masuk hingga pengiriman.
            </p>
          )}
        </header>
        <div className="ship-status-toolbar" data-testid="status-po-filter-bar">
          <label className="ship-filter-field spo-filter-po">
            <span>No PO</span>
            <input
              type="text"
              value={filters.po}
              onChange={(e) => setFilter("po", e.target.value)}
              placeholder="Cari No PO"
              data-testid="status-po-filter-po"
            />
          </label>
          <label className="ship-filter-field spo-filter-customer">
            <span>Customer</span>
            <input
              type="text"
              value={filters.customer}
              onChange={(e) => setFilter("customer", e.target.value)}
              placeholder="Cari customer"
              data-testid="status-po-filter-customer"
            />
          </label>
          <label className="ship-filter-field spo-filter-status">
            <span>Status</span>
            <select
              value={filters.status}
              onChange={(e) => setFilter("status", e.target.value)}
              aria-label="Status"
              data-testid="status-po-filter-status"
            >
              <option value="">Semua</option>
              {statusOptions.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
          <label className="ship-filter-field spo-filter-date">
            <span>Tanggal Mulai</span>
            <input
              type="date"
              value={filters.dateFrom}
              onChange={(e) => setFilter("dateFrom", e.target.value)}
              data-testid="status-po-filter-date-from"
            />
          </label>
          <label className="ship-filter-field spo-filter-date">
            <span>Tanggal Akhir</span>
            <input
              type="date"
              value={filters.dateTo}
              onChange={(e) => setFilter("dateTo", e.target.value)}
              data-testid="status-po-filter-date-to"
            />
          </label>
          <button type="button" className="ship-icon-btn" onClick={load} title="Muat ulang" data-testid="status-po-refresh" disabled={busy}>
            <RefreshCw size={14} className={busy ? "spin" : ""} />
          </button>
          <button
            type="button"
            className="ship-reset-btn"
            onClick={() => setFilters(EMPTY_STATUS_PO_FILTERS)}
            data-testid="status-po-filter-reset"
          >
            Reset
          </button>
        </div>
        {canSelect && (
          <p className="muted small" data-testid="status-po-select-scope">
            Pilih Semua hanya untuk baris yang sedang tampil (filter aktif).
          </p>
        )}
        {canSelect && selected.size > 0 && (
          <div className="bulk-bar" data-testid="status-po-bulk-bar">
            <span data-testid="status-po-selected-count"><b>{selected.size}</b> PO dipilih</span>
            <div className="bulk-actions">
              {canCancel && (
                <button
                  type="button"
                  className="ghost sm"
                  onClick={() => openBulk("cancel")}
                  disabled={bulkBusy}
                  data-testid="status-po-bulk-cancel"
                >
                  <XCircle size={13} /> Batalkan Terpilih
                </button>
              )}
              {canDelete && (
                <button
                  type="button"
                  className="ghost sm danger"
                  onClick={() => openBulk("delete")}
                  disabled={bulkBusy}
                  data-testid="status-po-bulk-delete"
                >
                  <Trash2 size={13} /> Hapus Permanen Terpilih
                </button>
              )}
              <button type="button" className="ghost sm" onClick={() => setSelected(new Set())} data-testid="status-po-bulk-clear">
                Batal pilih
              </button>
            </div>
          </div>
        )}
        {err && <div className="error" data-testid="status-po-error">{err}</div>}
      </div>
      <div className="ship-status-grid" data-testid="status-po-table">
        {!busy || visible.length ? (
          <StatusPoWorkflowTable
            visible={visible}
            canSelect={canSelect}
            selected={selected}
            setSelected={setSelected}
            visibleIds={visibleIds}
            headerState={headerState}
            headerRef={headerRef}
            user={user}
            location={location}
          />
        ) : null}
        {busy && !visible.length ? (
          <p className="muted small" style={{ padding: "12px" }}>Memuat…</p>
        ) : null}
      </div>
      <div className="spo-footer" data-testid="status-po-footer">
        <span data-testid="status-po-count">Menampilkan {visible.length} dari {rows.length} data</span>
      </div>
      <div className="spo-legend" data-testid="status-po-legend">
        <span className="spo-legend-item"><span className="spo-legend-mark is-done" aria-hidden="true">✓</span> Selesai</span>
        <span className="spo-legend-item"><span className="spo-legend-mark is-active" aria-hidden="true">◉</span> Sedang Berjalan</span>
        <span className="spo-legend-item"><span className="spo-legend-mark is-pending" aria-hidden="true">—</span> Belum Dimulai</span>
        <span className="spo-legend-item"><span className="spo-legend-mark is-hold" aria-hidden="true">!</span> Tertahan / Perlu Perhatian</span>
        <span className="spo-legend-item"><span className="spo-legend-mark is-unknown" aria-hidden="true">?</span> Tidak dapat ditentukan</span>
        <span className="spo-legend-item"><span className="spo-legend-mark is-cancel" aria-hidden="true">✕</span> Dibatalkan</span>
      </div>
      <p className="spo-help" data-testid="status-po-scroll-hint">
        Geser tabel ke kanan untuk melihat seluruh tahapan workflow.
        Lebar kolom dapat diubah dengan menarik garis header.
      </p>
      <ConfirmActionModal
        open={!!bulkAction}
        title={bulkAction === "delete" ? "Hapus Permanen PO Terpilih" : "Batalkan PO Terpilih"}
        stage="Status PO"
        action={bulkAction === "delete"
          ? `Hapus permanen ${selected.size} PO terpilih`
          : `Batalkan ${selected.size} PO terpilih`}
        busy={bulkBusy}
        error={bulkErr}
        confirmLabel={bulkAction === "delete" ? "Hapus Permanen" : "Batalkan"}
        onCancel={closeBulk}
        onConfirm={runBulk}
        extra={(
          <div className="status-po-bulk-fields" data-testid="status-po-bulk-fields">
            <p data-testid="status-po-bulk-count">{selected.size} PO akan diproses.</p>
            <label>
              <span>Alasan (wajib)</span>
              <textarea
                value={bulkReason}
                onChange={(e) => setBulkReason(e.target.value)}
                rows={3}
                disabled={bulkBusy}
                data-testid="status-po-bulk-reason"
              />
            </label>
            {bulkAction === "delete" && (
              <label>
                <span>Ketik HAPUS untuk konfirmasi</span>
                <input
                  type="text"
                  value={bulkConfirm}
                  onChange={(e) => setBulkConfirm(e.target.value)}
                  disabled={bulkBusy}
                  data-testid="status-po-bulk-confirm-text"
                />
              </label>
            )}
          </div>
        )}
      />
    </div>
  );
}
