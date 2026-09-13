import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { RefreshCw, Trash2, XCircle } from "lucide-react";
import api from "../lib/api";
import { formatDisplayDate } from "../lib/constants";
import { useAuth } from "../lib/auth";
import ConfirmActionModal from "../components/ConfirmActionModal";
import {
  headerCheckState,
  pruneSelection,
  removeSucceeded,
  selectAllVisible,
  toggleId,
  visibleRowIds,
} from "../lib/rowSelection";
import {
  EMPTY_STATUS_PO_FILTERS,
  deliveredPercentLabel,
  matchesStatusPoFilters,
  statusPoFilterOptions,
  statusPoViewLabel,
} from "../lib/statusPo";
import {
  capturePoOpenFromList,
  poNavStateFromList,
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
      const d = await api.statusPo(view ? { view } : {});
      setRows(d.rows || []);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Status PO"));
    } finally {
      setBusy(false);
      loadInFlight.current = false;
    }
  }, [view]);

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

  const colCount = canSelect ? 6 : 5;
  const title = deliveryTodayPage ? "Pengiriman Hari Ini" : "Status PO";

  return (
    <div className="ship-status-page status-po-page" data-testid="status-po-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <h1>{title}</h1>
          {!deliveryTodayPage && view ? (
            <p className="muted small" data-testid="status-po-view-label">{statusPoViewLabel(view)}</p>
          ) : null}
        </header>
        <div className="ship-status-toolbar" data-testid="status-po-filter-bar">
          <label className="ship-filter-field">
            <span>No PO</span>
            <input
              type="text"
              value={filters.po}
              onChange={(e) => setFilter("po", e.target.value)}
              placeholder="Cari No PO"
              data-testid="status-po-filter-po"
            />
          </label>
          <label className="ship-filter-field">
            <span>Customer</span>
            <input
              type="text"
              value={filters.customer}
              onChange={(e) => setFilter("customer", e.target.value)}
              placeholder="Cari customer"
              data-testid="status-po-filter-customer"
            />
          </label>
          <label className="ship-filter-field">
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
          <label className="ship-filter-field">
            <span>Tanggal Mulai</span>
            <input
              type="date"
              value={filters.dateFrom}
              onChange={(e) => setFilter("dateFrom", e.target.value)}
              data-testid="status-po-filter-date-from"
            />
          </label>
          <label className="ship-filter-field">
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
            Reset Filter
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
        <table className="ship-table">
          <thead>
            <tr>
              {canSelect && (
                <th className="ship-check">
                  <input
                    ref={headerRef}
                    type="checkbox"
                    checked={headerState.checked}
                    disabled={!visibleIds.length}
                    onChange={() => setSelected(selectAllVisible(visibleIds, selected))}
                    aria-label="Pilih Semua (baris tampil)"
                    data-testid="status-po-select-all"
                  />
                </th>
              )}
              <th>No PO</th>
              <th>Tanggal</th>
              <th>Pelanggan</th>
              <th>Status</th>
              <th className="num">Status Terkirim</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr key={r.id} data-testid={`status-po-row-${r.po_number}`} className={selected.has(r.id) ? "is-selected" : ""}>
                {canSelect && (
                  <td className="ship-check">
                    <input
                      type="checkbox"
                      checked={selected.has(r.id)}
                      onChange={() => setSelected(toggleId(selected, r.id))}
                      aria-label={`Pilih ${r.po_number}`}
                      data-testid={`status-po-check-${r.po_number}`}
                    />
                  </td>
                )}
                <td className="mono">
                  {r.id ? (
                    <Link
                      to={`/po/${r.id}`}
                      state={poNavStateFromList({ pathname: location.pathname, search: location.search })}
                      onClick={() => capturePoOpenFromList({
                        user,
                        orderId: r.id,
                        pathname: location.pathname,
                        search: location.search,
                      })}
                      data-testid={`status-po-link-${r.po_number}`}
                    >
                      {r.po_number}
                    </Link>
                  ) : (r.po_number || "-")}
                </td>
                <td className="mono">{formatDisplayDate(r.order_date)}</td>
                <td className="ship-cust" title={r.customer || undefined}>{r.customer || "-"}</td>
                <td data-testid={`status-po-status-${r.po_number}`}>{r.status || "-"}</td>
                <td className="num mono" data-testid={`status-po-delivered-${r.po_number}`}>
                  {deliveredPercentLabel(r.percent_delivered)}
                </td>
              </tr>
            ))}
            {!visible.length && !busy && (
              <tr><td colSpan={colCount} className="empty-state">Tidak ada PO.</td></tr>
            )}
          </tbody>
        </table>
      </div>
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
