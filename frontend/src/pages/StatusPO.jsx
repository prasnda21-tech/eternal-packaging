import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { CalendarDays, RefreshCw, Search, Trash2, XCircle } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import ConfirmActionModal from "../components/ConfirmActionModal";
import StatusPoWorkflowTable from "../components/StatusPoWorkflowTable";
/* Shared ops-list tokens: ship-table ship-check ship-cust is-selected empty-state */
import "./StatusPO.css";
import {
  headerCheckState,
  pruneSelection,
  removeSucceeded,
  visibleRowIds,
} from "../lib/rowSelection";
import {
  EMPTY_STATUS_PO_FILTERS,
  formatStatusPoDate,
  formatStatusPoUpdated,
  parseStatusPoDateInput,
  resolveStatusPoTab,
  STATUS_PO_PAGE_SIZE,
  STATUS_PO_TABS,
  statusPoFilterOptions,
} from "../lib/statusPo";
import { isStatusPoMatrixPreviewEnabled, buildStatusPoMatrixPreviewRows } from "../lib/statusPoPreview";
import {
  readListUiState,
  storageUserKey,
  writeListUiState,
} from "../lib/poListReturn";

function StatusPoDateInput({ value, onChange, ariaLabel, testId }) {
  const [text, setText] = useState(() => (value ? formatStatusPoDate(value) : ""));
  useEffect(() => {
    setText(value ? formatStatusPoDate(value) : "");
  }, [value]);
  return (
    <input
      type="text"
      inputMode="numeric"
      placeholder="dd/mm/yyyy"
      autoComplete="off"
      spellCheck={false}
      value={text === "-" ? "" : text}
      onChange={(e) => {
        const raw = e.target.value;
        setText(raw);
        if (!raw.trim()) {
          onChange("");
          return;
        }
        const iso = parseStatusPoDateInput(raw);
        if (iso) onChange(iso);
      }}
      onBlur={() => setText(value ? formatStatusPoDate(value) : "")}
      aria-label={ariaLabel}
      data-testid={testId}
    />
  );
}

export default function StatusPO() {
  const { user, hasPerm } = useAuth();
  const location = useLocation();
  const [params, setSearchParams] = useSearchParams();
  const deliveryTodayPage = location.pathname.startsWith("/produksi/pengiriman-hari-ini");
  const view = deliveryTodayPage ? "delivery_today" : (params.get("view") || "");
  const activeTab = deliveryTodayPage ? null : resolveStatusPoTab(view);
  const routeKey = deliveryTodayPage ? "pengiriman-hari-ini" : `status-po.${view || "default"}`;
  const userKey = storageUserKey(user);
  const savedFilters = readListUiState(userKey, routeKey, EMPTY_STATUS_PO_FILTERS);
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [filters, setFilters] = useState({ ...EMPTY_STATUS_PO_FILTERS, ...savedFilters });
  const [debouncedFilters, setDebouncedFilters] = useState(() => ({ ...EMPTY_STATUS_PO_FILTERS, ...savedFilters }));
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [hasPrev, setHasPrev] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [bulkAction, setBulkAction] = useState(null);
  const [bulkReason, setBulkReason] = useState("");
  const [bulkConfirm, setBulkConfirm] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkErr, setBulkErr] = useState("");
  const [updatedAt, setUpdatedAt] = useState(null);
  const [serverStatusOptions, setServerStatusOptions] = useState([]);
  const inFlight = useRef(false);
  const loadSeq = useRef(0);
  const headerRef = useRef(null);
  const filterKey = useMemo(() => JSON.stringify(debouncedFilters), [debouncedFilters]);

  const canCancel = hasPerm("po.cancel");
  const canDelete = hasPerm("po.delete");
  const canSelect = canCancel || canDelete;

  useEffect(() => {
    const t = setTimeout(() => setDebouncedFilters(filters), 300);
    return () => clearTimeout(t);
  }, [filters]);

  useEffect(() => {
    setPage((prev) => (prev === 1 ? prev : 1));
  }, [view, filterKey]);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setBusy(true);
    setErr("");
    try {
      if (isStatusPoMatrixPreviewEnabled(location.search)) {
        if (seq !== loadSeq.current) return;
        setRows(buildStatusPoMatrixPreviewRows());
        setTotalCount(0);
        setTotalPages(0);
        setHasNext(false);
        setHasPrev(false);
        setUpdatedAt(new Date());
        return;
      }
      const req = {
        page,
        page_size: STATUS_PO_PAGE_SIZE,
      };
      if (view) req.view = view;
      const po = (debouncedFilters.po || "").trim();
      const customer = (debouncedFilters.customer || "").trim();
      const status = (debouncedFilters.status || "").trim();
      const dateFrom = (debouncedFilters.dateFrom || "").trim();
      const dateTo = (debouncedFilters.dateTo || "").trim();
      if (po) req.po = po;
      if (customer) req.customer = customer;
      if (status) req.status = status;
      if (dateFrom) req.date_from = dateFrom;
      if (dateTo) req.date_to = dateTo;
      const d = await api.statusPo(req);
      if (seq !== loadSeq.current) return;
      setRows(d.rows || []);
      const total = Number(d.total_count ?? d.total ?? 0) || 0;
      setTotalCount(total);
      setTotalPages(Number(d.total_pages ?? 0) || 0);
      setHasNext(Boolean(d.has_next));
      setHasPrev(Boolean(d.has_prev));
      setServerStatusOptions(Array.isArray(d.status_options) ? d.status_options : []);
      if (d.page && Number(d.page) !== page) {
        setPage(Number(d.page));
      }
      setUpdatedAt(new Date());
    } catch (e) {
      if (seq !== loadSeq.current) return;
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Status PO"));
    } finally {
      if (seq === loadSeq.current) setBusy(false);
    }
  }, [view, page, debouncedFilters, location.search]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    writeListUiState(userKey, routeKey, filters);
  }, [userKey, routeKey, filters]);

  const visible = useMemo(() => rows || [], [rows]);
  const visibleIds = useMemo(() => visibleRowIds(visible), [visible]);
  const visibleIdKey = useMemo(() => visibleIds.join(","), [visibleIds]);

  useEffect(() => {
    setSelected((prev) => pruneSelection(prev, visibleIds));
  }, [visibleIdKey, visibleIds]);

  const headerState = headerCheckState(selected, visibleIds);
  useEffect(() => {
    if (headerRef.current) headerRef.current.indeterminate = headerState.indeterminate;
  }, [headerState.indeterminate]);

  const statusOptions = useMemo(
    () => statusPoFilterOptions(
      (serverStatusOptions || []).map((status) => ({ status })),
      filters.status,
    ),
    [serverStatusOptions, filters.status],
  );

  const setFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));

  const selectTab = (tabKey) => {
    if (deliveryTodayPage) return;
    const next = new URLSearchParams(params);
    if (tabKey === "all") next.delete("view");
    else next.set("view", tabKey);
    setSearchParams(next, { replace: false });
    setPage(1);
  };

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
  const updatedLabel = formatStatusPoUpdated(updatedAt);
  const pageLabel = totalPages > 0
    ? `Halaman ${page} dari ${totalPages}`
    : (busy ? "Memuat…" : "Halaman 0 dari 0");

  return (
    <div className="ship-status-page status-po-page" data-testid="status-po-page" data-density="rapat">
      <div className="ship-status-chrome">
        <header className="ship-status-heading spo-heading">
          <div className="spo-heading-copy">
            <div className="spo-heading-title-row">
              <h1>{title}</h1>
              {!deliveryTodayPage && (
                <span className="spo-po-count" data-testid="status-po-heading-count">
                  {totalCount} PO
                </span>
              )}
            </div>
          </div>
          <p className="spo-updated muted small" data-testid="status-po-updated">
            {updatedLabel ? `Diperbarui ${updatedLabel}` : (busy ? "Memuat…" : "\u00a0")}
          </p>
        </header>
        <div className="ship-status-toolbar" data-testid="status-po-filter-bar">
          {!deliveryTodayPage && (
            <div className="spo-status-tabs" role="tablist" aria-label="Filter status PO" data-testid="status-po-tabs">
              {STATUS_PO_TABS.map((tab) => {
                const selectedTab = activeTab === tab.key;
                return (
                  <button
                    key={tab.key}
                    type="button"
                    role="tab"
                    aria-selected={selectedTab}
                    className={`spo-status-tab${selectedTab ? " is-active" : ""}`}
                    onClick={() => selectTab(tab.key)}
                    data-testid={`status-po-tab-${tab.key}`}
                    disabled={busy && selectedTab}
                  >
                    {tab.label}
                  </button>
                );
              })}
            </div>
          )}
          <label className="ship-filter-field spo-filter-search">
            <span className="spo-sr-only">Cari</span>
            <span className="spo-search-wrap">
              <Search size={15} aria-hidden="true" />
              <input
                type="search"
                value={filters.po}
                onChange={(e) => setFilter("po", e.target.value)}
                placeholder="Cari No PO, pelanggan, atau produk..."
                aria-label="Cari No PO, pelanggan, atau produk"
                data-testid="status-po-filter-po"
              />
            </span>
          </label>
          <label className="ship-filter-field spo-filter-customer">
            <span className="spo-sr-only">Pelanggan</span>
            <input
              type="text"
              value={filters.customer}
              onChange={(e) => setFilter("customer", e.target.value)}
              placeholder="Pelanggan"
              aria-label="Pelanggan"
              data-testid="status-po-filter-customer"
            />
          </label>
          <label className="ship-filter-field spo-filter-status">
            <span className="spo-sr-only">Status</span>
            <select
              value={filters.status}
              onChange={(e) => setFilter("status", e.target.value)}
              aria-label="Status"
              data-testid="status-po-filter-status"
            >
              <option value="">Semua Status</option>
              {statusOptions.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
          <div className="spo-date-range" data-testid="status-po-date-range" lang="id">
            <CalendarDays size={15} aria-hidden="true" />
            <label className="ship-filter-field spo-filter-date">
              <span className="spo-sr-only">Tanggal Mulai</span>
              <StatusPoDateInput
                value={filters.dateFrom}
                onChange={(next) => setFilter("dateFrom", next)}
                ariaLabel="Tanggal mulai"
                testId="status-po-filter-date-from"
              />
            </label>
            <span className="spo-date-sep" aria-hidden="true">–</span>
            <label className="ship-filter-field spo-filter-date">
              <span className="spo-sr-only">Tanggal Akhir</span>
              <StatusPoDateInput
                value={filters.dateTo}
                onChange={(next) => setFilter("dateTo", next)}
                ariaLabel="Tanggal akhir"
                testId="status-po-filter-date-to"
              />
            </label>
          </div>
          <button
            type="button"
            className="ship-reset-btn"
            onClick={() => setFilters(EMPTY_STATUS_PO_FILTERS)}
            data-testid="status-po-filter-reset"
          >
            <RefreshCw size={14} aria-hidden="true" />
            Reset
          </button>
          <button type="button" className="ship-icon-btn" onClick={load} title="Muat ulang" aria-label="Refresh" data-testid="status-po-refresh" disabled={busy}>
            <RefreshCw size={14} className={busy ? "spin" : ""} aria-hidden="true" />
            <span>Refresh</span>
          </button>
        </div>
        {canSelect && (
          <p className="muted small spo-sr-only" data-testid="status-po-select-scope">
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
            density="rapat"
          />
        ) : null}
        {busy && !visible.length ? (
          <p className="muted small" style={{ padding: "12px" }}>Memuat…</p>
        ) : null}
      </div>
      <div className="spo-footer" data-testid="status-po-footer">
        <div className="spo-footer-meta">
          <span data-testid="status-po-count">
            Menampilkan {visible.length} dari {totalCount} PO
          </span>
          <div className="spo-pager" data-testid="status-po-pager">
            <button
              type="button"
              className="spo-pager-btn"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={busy || !hasPrev}
              data-testid="status-po-page-prev"
            >
              Sebelumnya
            </button>
            <span className="spo-pager-label" data-testid="status-po-page-label">{pageLabel}</span>
            <button
              type="button"
              className="spo-pager-btn"
              onClick={() => setPage((p) => p + 1)}
              disabled={busy || !hasNext}
              data-testid="status-po-page-next"
            >
              Berikutnya
            </button>
          </div>
        </div>
        <div className="spo-legend" data-testid="status-po-legend">
          <span className="spo-legend-item"><span className="spo-legend-dot is-done" aria-hidden="true" /> Selesai</span>
          <span className="spo-legend-item"><span className="spo-legend-dot is-active" aria-hidden="true" /> Tahap Aktif</span>
          <span className="spo-legend-item"><span className="spo-legend-dot is-skipped" aria-hidden="true" /> Dilewati (data lama)</span>
          <span className="spo-legend-item"><span className="spo-legend-dot is-pending" aria-hidden="true" /> Belum Dimulai</span>
          <span className="spo-legend-item"><span className="spo-legend-dot is-problem" aria-hidden="true" /> Tertahan / Bermasalah</span>
        </div>
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
