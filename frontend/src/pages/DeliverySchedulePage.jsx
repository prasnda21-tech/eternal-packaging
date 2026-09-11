import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowDown, ArrowUp, ArrowUpDown, Filter, RefreshCw, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { clampPercent, terkirimTone } from "../lib/shipmentStatus";
import {
  DELIVERY_SCHEDULE_STATUS_OPTIONS,
  DELIVERY_SORT_KEYS,
  EMPTY_DELIVERY_FILTERS,
  EMPTY_DELIVERY_SORT,
  countActiveDeliveryFilters,
  cycleDeliverySort,
  matchesDeliveryScheduleFilters,
  sortDeliveryRows,
} from "../lib/deliverySchedule";
import { locationSetupStatusLabel } from "../lib/deliveryRouteBridge";
import {
  canOptimizeFromDeliverySchedule,
  desktopScheduleReviewHref,
} from "../lib/mobileDeliverySchedule";
import {
  headerCheckState,
  pruneSelection,
  selectAllVisible,
  toggleId,
  visibleRowIds,
} from "../lib/rowSelection";
import {
  capturePoOpenFromList,
  poNavStateFromList,
  readListUiState,
  storageUserKey,
  writeListUiState,
} from "../lib/poListReturn";
import SuratJalanViewButton from "../components/SuratJalanViewButton";
import OutletLocationModal from "../components/OutletLocationModal";

function ProgressCell({ pct, tone, testid, title }) {
  const p = clampPercent(pct);
  return (
    <div className={"ship-bar ship-bar-" + tone} data-testid={testid} title={title}>
      <div className="ship-bar-fill" style={{ width: p + "%" }} />
      <span className="ship-bar-label">{p}%</span>
    </div>
  );
}

function SortableTh({ label, sortKey, sort, onCycle, className = "", testid }) {
  const active = sort?.key === sortKey && !!sort?.direction;
  const ariaSort = !active ? "none" : (sort.direction === "asc" ? "ascending" : "descending");
  const Icon = !active ? ArrowUpDown : (sort.direction === "asc" ? ArrowUp : ArrowDown);
  return (
    <th className={className} aria-sort={ariaSort}>
      <button
        type="button"
        className={"ship-th-sort" + (active ? " is-active" : "")}
        onClick={() => onCycle(sortKey)}
        title={`Urutkan ${label}`}
        aria-label={`Urutkan ${label}`}
        data-testid={testid}
      >
        <span>{label}</span>
        <Icon size={12} aria-hidden="true" />
      </button>
    </th>
  );
}

async function outletRowForOrder({ orderId, customer, outlet }) {
  const row = {
    order_id: orderId,
    customer: customer || "",
    outlet: outlet || "",
    outlet_name: outlet || "",
  };
  if (!customer) return row;
  try {
    const d = await api.listDeliveryOutlets(customer);
    const o = (d.outlets || [])[0];
    if (o) {
      row.outlet_id = o.id;
      row.outlet_name = o.name || outlet || "";
      row.lat = o.lat;
      row.lng = o.lng;
      row.place_id = o.place_id || o.google_place_id;
      row.formatted_address = o.formatted_address;
    }
  } catch {
    /* list is optional; modal still opens for a new pin */
  }
  return row;
}

export default function DeliverySchedulePage() {
  const { user, hasPerm } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const canSetLocation = hasPerm("delivery.location.manage");
  const canOptimize = canOptimizeFromDeliverySchedule(hasPerm);
  const userKey = storageUserKey(user);
  const saved = readListUiState(userKey, "delivery-schedule", {
    filters: EMPTY_DELIVERY_FILTERS,
    sort: EMPTY_DELIVERY_SORT,
  });
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [filters, setFilters] = useState({ ...EMPTY_DELIVERY_FILTERS, ...(saved.filters || {}) });
  const [sort, setSort] = useState({ ...EMPTY_DELIVERY_SORT, ...(saved.sort || {}) });
  const [filterOpen, setFilterOpen] = useState(false);
  const [draftFilters, setDraftFilters] = useState(EMPTY_DELIVERY_FILTERS);
  const [locRow, setLocRow] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const selectAllRef = useRef(null);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.deliverySchedule();
      setRows(d.rows || []);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Jadwal Pengiriman"));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    writeListUiState(userKey, "delivery-schedule", { filters, sort });
  }, [userKey, filters, sort]);

  const listNavState = poNavStateFromList({
    pathname: location.pathname,
    search: location.search,
  });
  const onOpenPo = (orderId) => {
    capturePoOpenFromList({
      user,
      orderId,
      pathname: location.pathname,
      search: location.search,
    });
  };

  const filteredRows = useMemo(
    () => (rows || []).filter((r) => matchesDeliveryScheduleFilters(r, filters)),
    [rows, filters],
  );
  const visible = useMemo(
    () => sortDeliveryRows(filteredRows, sort),
    [filteredRows, sort],
  );
  const activeFilterCount = useMemo(() => countActiveDeliveryFilters(filters), [filters]);
  const visibleIds = useMemo(() => visibleRowIds(visible), [visible]);
  const headerCheck = useMemo(() => headerCheckState(selected, visibleIds), [selected, visibleIds]);

  useEffect(() => {
    setSelected((prev) => pruneSelection(prev, visibleIds));
  }, [visibleIds]);

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = headerCheck.indeterminate;
    }
  }, [headerCheck.indeterminate]);

  const setFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));
  const setDraft = (key, value) => setDraftFilters((prev) => ({ ...prev, [key]: value }));
  const onHeaderSort = (key) => setSort((prev) => cycleDeliverySort(prev, key));

  const openFilterPanel = () => {
    setDraftFilters({ ...EMPTY_DELIVERY_FILTERS, ...filters });
    setFilterOpen(true);
  };
  const closeFilterPanel = () => setFilterOpen(false);
  const applyDraftFilters = () => {
    setFilters({ ...EMPTY_DELIVERY_FILTERS, ...draftFilters });
    setFilterOpen(false);
  };
  const resetFilters = () => {
    const empty = { ...EMPTY_DELIVERY_FILTERS };
    setDraftFilters(empty);
    setFilters(empty);
  };

  const onOptimasiRute = () => {
    const ids = Array.from(selected).filter(Boolean).map(String);
    if (!ids.length) return;
    navigate(desktopScheduleReviewHref(ids));
  };

  const colCount = canOptimize ? 13 : 12;

  return (
    <div className="ship-status-page" data-testid="delivery-schedule-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <p className="eyebrow">Produksi</p>
          <h1>Jadwal Pengiriman</h1>
        </header>
        <div className="ship-status-toolbar" data-testid="delivery-schedule-filters">
          <label className="ship-filter-field">
            <span>Tanggal Mulai</span>
            <input type="date" value={filters.dateFrom} onChange={(e) => setFilter("dateFrom", e.target.value)} data-testid="ds-filter-from" />
          </label>
          <label className="ship-filter-field">
            <span>Tanggal Akhir</span>
            <input type="date" value={filters.dateTo} onChange={(e) => setFilter("dateTo", e.target.value)} data-testid="ds-filter-to" />
          </label>
          <label className="ship-filter-field">
            <span>No PO</span>
            <input type="text" value={filters.po} onChange={(e) => setFilter("po", e.target.value)} placeholder="Cari No PO" data-testid="ds-filter-po" />
          </label>
          <label className="ship-filter-field">
            <span>Customer</span>
            <input type="text" value={filters.customer} onChange={(e) => setFilter("customer", e.target.value)} placeholder="Cari customer" data-testid="ds-filter-customer" />
          </label>
          <label className="ship-filter-field">
            <span>Status</span>
            <select value={filters.status} onChange={(e) => setFilter("status", e.target.value)} data-testid="ds-filter-status">
              {DELIVERY_SCHEDULE_STATUS_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>{o.label}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className={"ship-tool-btn" + (activeFilterCount > 0 ? " is-active" : "")}
            onClick={openFilterPanel}
            data-testid="ds-filter-btn"
            aria-label="Filter"
          >
            <Filter size={13} />
            <span>Filter{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""}</span>
          </button>
          <button type="button" className="ghost sm" onClick={resetFilters} data-testid="ds-filter-reset">Reset</button>
          <button type="button" className="ship-icon-btn" onClick={load} title="Muat ulang" data-testid="ds-refresh" disabled={busy}>
            <RefreshCw size={14} className={busy ? "spin" : ""} />
          </button>
          {canOptimize && (
            <button
              type="button"
              className="primary sm"
              data-testid="ds-optimasi-rute"
              disabled={busy || selected.size === 0}
              title={selected.size === 0 ? "Pilih PO terlebih dahulu" : `Optimasi Rute untuk ${selected.size} PO terpilih`}
              onClick={onOptimasiRute}
            >
              Optimasi Rute{selected.size > 0 ? ` (${selected.size})` : ""}
            </button>
          )}
        </div>
        {canOptimize && selected.size > 0 && (
          <div className="bulk-bar" data-testid="ds-bulk-bar">
            <span data-testid="ds-selected-count"><b>{selected.size}</b> PO dipilih</span>
            <div className="bulk-actions">
              <button type="button" className="ghost sm" onClick={() => setSelected(new Set())} data-testid="ds-bulk-clear">
                Batal pilih
              </button>
            </div>
          </div>
        )}
        {err && <div className="error" data-testid="ds-error">{err}</div>}
      </div>
      <div className="ship-status-grid">
        <table className="ship-table" data-testid="delivery-schedule-table">
          <thead>
            <tr>
              {canOptimize && (
                <th className="ship-th-check">
                  <input
                    ref={selectAllRef}
                    type="checkbox"
                    checked={headerCheck.checked}
                    onChange={() => setSelected(selectAllVisible(visibleIds, selected))}
                    aria-label="Pilih semua baris tampilan"
                    data-testid="ds-select-all"
                  />
                </th>
              )}
              <SortableTh
                label="Tanggal Pengiriman"
                sortKey={DELIVERY_SORT_KEYS.effective_delivery_date}
                sort={sort}
                onCycle={onHeaderSort}
                testid="ds-sort-delivery-date"
              />
              <SortableTh
                label="No PO"
                sortKey={DELIVERY_SORT_KEYS.po_number}
                sort={sort}
                onCycle={onHeaderSort}
                testid="ds-sort-po"
              />
              <SortableTh
                label="Customer"
                sortKey={DELIVERY_SORT_KEYS.customer}
                sort={sort}
                onCycle={onHeaderSort}
                testid="ds-sort-customer"
              />
              <SortableTh
                label="Deadline"
                sortKey={DELIVERY_SORT_KEYS.deadline}
                sort={sort}
                onCycle={onHeaderSort}
                testid="ds-sort-deadline"
              />
              <SortableTh
                label="Jadwal Manual"
                sortKey={DELIVERY_SORT_KEYS.jadwal_manual}
                sort={sort}
                onCycle={onHeaderSort}
                testid="ds-sort-manual"
              />
              <SortableTh
                label="Status"
                sortKey={DELIVERY_SORT_KEYS.status}
                sort={sort}
                onCycle={onHeaderSort}
                testid="ds-sort-status"
              />
              <th data-testid="ds-col-status-lokasi">Status Lokasi</th>
              <SortableTh
                label="Driver"
                sortKey={DELIVERY_SORT_KEYS.driver}
                sort={sort}
                onCycle={onHeaderSort}
                testid="ds-sort-driver"
              />
              <SortableTh
                label="Terkirim"
                sortKey={DELIVERY_SORT_KEYS.shipped}
                sort={sort}
                onCycle={onHeaderSort}
                className="num"
                testid="ds-sort-terkirim"
              />
              <SortableTh
                label="Dapat Dikirim"
                sortKey={DELIVERY_SORT_KEYS.shippable}
                sort={sort}
                onCycle={onHeaderSort}
                className="num"
                testid="ds-sort-dapat"
              />
              <th>Surat Jalan</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => {
              const locationStatus = locationSetupStatusLabel(r);
              const locationOk = locationStatus === "Sudah Diatur";
              return (
                <tr
                  key={r.id}
                  data-testid={`ds-row-${r.order_number}`}
                  className={canOptimize && r.id && selected.has(r.id) ? "is-selected" : undefined}
                >
                  {canOptimize && (
                    <td>
                      {r.id ? (
                        <input
                          type="checkbox"
                          checked={selected.has(r.id)}
                          onChange={() => setSelected(toggleId(selected, r.id))}
                          aria-label={`Pilih ${r.order_number}`}
                          data-testid={`ds-checkbox-${r.order_number}`}
                        />
                      ) : null}
                    </td>
                  )}
                  <td className="mono">{formatDate(r.effective_delivery_date, { dateOnly: true })}</td>
                  <td className="mono">
                    {r.id ? (
                      <Link to={`/po/${r.id}`} state={listNavState} onClick={() => onOpenPo(r.id)}>
                        {r.order_number}
                      </Link>
                    ) : (r.order_number || "-")}
                  </td>
                  <td>{r.customer_name || "-"}</td>
                  <td className="mono" data-testid={`ds-deadline-${r.order_number}`}>{formatDate(r.deadline, { dateOnly: true })}</td>
                  <td className="mono" data-testid={`ds-manual-${r.order_number}`}>{r.jadwal_manual ? formatDate(r.jadwal_manual, { dateOnly: true }) : "—"}</td>
                  <td data-testid={`ds-status-${r.order_number}`}>{r.status_label}</td>
                  <td data-testid={`ds-status-lokasi-${r.order_number}`}>
                    <span className={"pill sm " + (locationOk ? "pill-ok" : "pill-warn")}>
                      {locationStatus}
                    </span>
                  </td>
                  <td>{r.driver_name || "—"}</td>
                  <td>
                    <ProgressCell
                      pct={r.percent_delivered_display ?? r.percent_delivered}
                      tone={terkirimTone(r.percent_delivered_display ?? r.percent_delivered)}
                      testid={`ds-terkirim-${r.order_number}`}
                      title={`Terkirim ${r.qty_delivered ?? 0} / ${r.qty_total ?? 0}`}
                    />
                  </td>
                  <td>
                    <ProgressCell
                      pct={r.percent_ready_to_ship_display ?? r.percent_ready_to_ship}
                      tone="ready"
                      testid={`ds-dapat-${r.order_number}`}
                      title={`Dicetak operator ${r.qty_printed_operator ?? 0} / ${r.print_qty_total ?? 0}`}
                    />
                  </td>
                  <td>
                    <SuratJalanViewButton
                      order={{ id: r.id, po_number: r.order_number, customer_name: r.customer_name, sj_files: r.sj_files }}
                      entries={r.sj_files}
                      label={r.sj_file_count > 1 ? `Lihat Surat Jalan (${r.sj_file_count})` : "Lihat Surat Jalan"}
                      testid={`ds-sj-lihat-${r.order_number}`}
                    />
                  </td>
                  <td>
                    {r.id && (
                      <Link
                        className="ghost sm"
                        to={`/po/${r.id}/kerja?divisi=terkirim`}
                        state={listNavState}
                        onClick={() => onOpenPo(r.id)}
                      >
                        Kerjakan
                      </Link>
                    )}
                    {canSetLocation && r.id && (
                      <button
                        type="button"
                        className="ghost sm"
                        data-testid={`ds-atur-lokasi-${r.order_number}`}
                        onClick={async () => {
                          setLocRow(await outletRowForOrder({
                            orderId: r.id,
                            customer: r.customer_name,
                          }));
                        }}
                      >
                        Atur Lokasi
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
            {!visible.length && !busy && (
              <tr><td colSpan={colCount} className="empty-state">Tidak ada jadwal pengiriman.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <OutletLocationModal
        open={!!locRow}
        row={locRow}
        canEdit={canSetLocation}
        onClose={() => setLocRow(null)}
        onSaved={() => load()}
      />

      {filterOpen && (
        <div
          className="modal modal-viewport-safe-wrap"
          role="dialog"
          aria-modal="true"
          data-testid="ds-filter-panel"
          onClick={(e) => e.target === e.currentTarget && closeFilterPanel()}
        >
          <div className="modal-inner modal-viewport-safe ship-filter-modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <h3>Filter Jadwal Pengiriman</h3>
              <button type="button" className="icon-button" onClick={closeFilterPanel} aria-label="Tutup" data-testid="ds-filter-close">
                <X size={16} />
              </button>
            </header>
            <div className="ship-filter-panel-body" data-testid="ds-filter-panel-body">
              <label className="ship-filter-field">
                <span>Nama Customer</span>
                <input type="text" value={draftFilters.customer} onChange={(e) => setDraft("customer", e.target.value)} placeholder="Cari customer" data-testid="ds-adv-customer" />
              </label>
              <label className="ship-filter-field">
                <span>No PO</span>
                <input type="text" value={draftFilters.po} onChange={(e) => setDraft("po", e.target.value)} placeholder="Cari No PO" data-testid="ds-adv-po" />
              </label>
              <div className="ship-filter-range">
                <span className="ship-filter-range-label">Tanggal Pengiriman</span>
                <div className="ship-filter-range-row">
                  <label className="ship-filter-field">
                    <span>Dari</span>
                    <input type="date" value={draftFilters.dateFrom} onChange={(e) => setDraft("dateFrom", e.target.value)} data-testid="ds-adv-from" />
                  </label>
                  <label className="ship-filter-field">
                    <span>Sampai</span>
                    <input type="date" value={draftFilters.dateTo} onChange={(e) => setDraft("dateTo", e.target.value)} data-testid="ds-adv-to" />
                  </label>
                </div>
              </div>
              <label className="ship-filter-field">
                <span>Status</span>
                <select value={draftFilters.status} onChange={(e) => setDraft("status", e.target.value)} data-testid="ds-adv-status">
                  {DELIVERY_SCHEDULE_STATUS_OPTIONS.map((o) => (
                    <option key={o.key} value={o.key}>{o.label}</option>
                  ))}
                </select>
              </label>
              <label className="ship-filter-field">
                <span>Driver</span>
                <input type="text" value={draftFilters.driver} onChange={(e) => setDraft("driver", e.target.value)} placeholder="Cari driver" data-testid="ds-adv-driver" />
              </label>
              <label className="ship-filter-field">
                <span>Terkirim</span>
                <select value={draftFilters.terkirim || "all"} onChange={(e) => setDraft("terkirim", e.target.value)} data-testid="ds-adv-terkirim">
                  <option value="all">Semua</option>
                  <option value="none">Belum Terkirim</option>
                  <option value="partial">Terkirim Sebagian</option>
                  <option value="full">Terkirim 100%</option>
                </select>
              </label>
              <label className="ship-filter-field">
                <span>Dapat Dikirim</span>
                <select value={draftFilters.dapatDikirim || "all"} onChange={(e) => setDraft("dapatDikirim", e.target.value)} data-testid="ds-adv-dapat">
                  <option value="all">Semua</option>
                  <option value="none">Tidak Dapat Dikirim</option>
                  <option value="partial">Dapat Dikirim Sebagian</option>
                  <option value="full">Dapat Dikirim 100%</option>
                </select>
              </label>
            </div>
            <footer className="ship-filter-panel-foot" data-testid="ds-filter-panel-foot">
              <button type="button" className="ship-reset-btn" onClick={resetFilters} data-testid="ds-adv-reset">
                Reset Filter
              </button>
              <button type="button" className="primary sm" onClick={applyDraftFilters} data-testid="ds-filter-apply">
                Terapkan Filter
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
