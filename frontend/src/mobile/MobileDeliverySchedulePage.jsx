import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Filter, RefreshCw, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { formatApiError } from "../lib/errors";
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
import {
  MOBILE_DELIVERY_SCHEDULE_COLS,
  canEditMobileDeliveryScheduleLocation,
  canOptimizeFromDeliverySchedule,
  canViewMobileDeliverySchedule,
  mobileDeliverySheetColumnStyle,
  mobileScheduleReviewHref,
  optimizationStatusMeta,
} from "../lib/mobileDeliverySchedule";
import { ROUTE_PERMS } from "../lib/independentRoute";import { mobileScheduleWorkHref } from "../lib/mobileWorkspaces";
import {
  OPS_QUICK_FILTERS,
  deliveryOpsBadge,
  deliveryQuickMatch,
  deliveryQueueRank,
  packingProgressText,
  sortByOpsQueue,
} from "../lib/mobileOpsQueue";
import {
  MOBILE_DELIVERY_FILTER_KEY,
  MOBILE_DELIVERY_SCROLL_KEY,
  readJsonSession,
  readScrollSession,
  writeJsonSession,
  writeScrollSession,
} from "../lib/mobileOpsSession";
import { formatIdQty } from "../lib/productionScheduleQty";
import { MOBILE_LOAD_TIMEOUT_MS, withTimeout } from "../lib/mobileRequest";
import useProductionSheetZoom from "../hooks/useProductionSheetZoom";
import { productionSheetZoomStyle } from "../lib/productionSheetZoom";
import SuratJalanViewButton from "../components/SuratJalanViewButton";
import OutletLocationModal from "../components/OutletLocationModal";
import MobileTopBar from "./MobileTopBar";

function ProgressCell({ pct, tone, testid, title, primaryLabel, contextLabel }) {
  const p = clampPercent(pct);
  return (
    <div className={"ship-bar-stack"} data-testid={testid} title={title}>
      <div className={"ship-bar ship-bar-" + tone}>
        <div className="ship-bar-fill" style={{ width: p + "%" }} />
        <span className="ship-bar-label">{primaryLabel != null ? primaryLabel : `${p}%`}</span>
      </div>
      {contextLabel ? <span className="mop-qty-context">{contextLabel}</span> : null}
    </div>
  );
}

function PackingCell({ row }) {
  const done = Number(row.qty_packed ?? 0);
  const target = Number(row.qty_total ?? 0);
  const pct = target > 0 ? Math.min(100, Math.round((done / target) * 100)) : 0;
  return (
    <span className="mop-qty-progress" data-testid={`mds-packing-${row.order_number}`} title={packingProgressText(row)}>
      <span>{packingProgressText(row)}</span>
      {target > 0 ? (
        <span className="mop-qty-progress-bar" aria-hidden>
          <span className="mop-qty-progress-fill" style={{ width: `${pct}%` }} />
        </span>
      ) : null}
    </span>
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
    /* list is optional */
  }
  return row;
}

export function deliveryScheduleLoadErrorMessage(err) {
  const status = err?.response?.status;
  if (err?.code === "ETIMEDOUT" || err?.code === "ECONNABORTED") {
    return "Gagal memuat Jadwal Pengiriman.";
  }
  const msg = formatApiError(err, "Gagal memuat Jadwal Pengiriman.");
  if (status === 403) return msg || "Anda tidak punya akses untuk melihat Jadwal Pengiriman.";
  if (status === 401) return msg || "Sesi berakhir. Silakan masuk kembali.";
  return msg || "Gagal memuat Jadwal Pengiriman.";
}

export default function MobileDeliverySchedulePage() {
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const allowed = canViewMobileDeliverySchedule(hasPerm);
  const canSetLocation = canEditMobileDeliveryScheduleLocation(hasPerm);
  const canOptimize = canOptimizeFromDeliverySchedule(hasPerm);
  const saved = readJsonSession(MOBILE_DELIVERY_FILTER_KEY, {
    filters: EMPTY_DELIVERY_FILTERS,
    quick: "semua_aktif",
  });
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [filters, setFilters] = useState({ ...EMPTY_DELIVERY_FILTERS, ...(saved.filters || {}) });
  const [sort, setSort] = useState(EMPTY_DELIVERY_SORT);
  const [quick, setQuick] = useState(saved.quick || "semua_aktif");
  const [filterOpen, setFilterOpen] = useState(false);
  const [draftFilters, setDraftFilters] = useState(EMPTY_DELIVERY_FILTERS);
  const [locRow, setLocRow] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const sheetZoom = useProductionSheetZoom();
  const scrollRestored = useRef(false);

  const load = useCallback(async () => {
    if (!allowed) {
      setBusy(false);
      setRows([]);
      setErr("");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const d = await withTimeout(api.deliverySchedule(), MOBILE_LOAD_TIMEOUT_MS);
      setRows(d.rows || []);
    } catch (e) {
      setRows([]);
      setErr(deliveryScheduleLoadErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }, [allowed]);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  useEffect(() => {
    writeJsonSession(MOBILE_DELIVERY_FILTER_KEY, { filters, quick });
  }, [filters, quick]);

  const filteredRows = useMemo(
    () => (rows || [])
      .filter((r) => matchesDeliveryScheduleFilters(r, filters))
      .filter((r) => deliveryQuickMatch(r, quick)),
    [rows, filters, quick],
  );
  const sorted = useMemo(() => sortDeliveryRows(filteredRows, sort), [filteredRows, sort]);
  const visible = useMemo(() => sortByOpsQueue(sorted, deliveryQueueRank), [sorted]);
  const activeFilterCount = useMemo(() => countActiveDeliveryFilters(filters), [filters]);

  useLayoutEffect(() => {
    if (busy || scrollRestored.current) return;
    const el = sheetZoom.viewportRef?.current;
    const savedScroll = readScrollSession(MOBILE_DELIVERY_SCROLL_KEY);
    if (el && savedScroll) {
      el.scrollLeft = savedScroll.left;
      el.scrollTop = savedScroll.top;
    }
    scrollRestored.current = true;
  }, [busy, sheetZoom.viewportRef, visible.length]);

  const persistScroll = () => {
    const el = sheetZoom.viewportRef?.current;
    if (!el) return;
    writeScrollSession(MOBILE_DELIVERY_SCROLL_KEY, el.scrollLeft, el.scrollTop);
  };

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
    setQuick("semua_aktif");
  };

  const openRow = (r) => {
    if (!r?.id) return;
    persistScroll();
    navigate(mobileScheduleWorkHref(r.id, hasPerm, "delivery"));
  };

  return (
    <div className="mop-schedule-root mop-delivery-schedule-root">
      <MobileTopBar kicker="ETERNAL PACKAGING" title="Jadwal Pengiriman" />
      <main className="mop-content mop-schedule-page" data-testid="mobile-delivery-schedule-page">
        {!allowed && (
          <div className="mop-error" data-testid="mobile-delivery-schedule-forbidden">
            <p>Anda tidak punya akses untuk melihat Jadwal Pengiriman.</p>
          </div>
        )}
        {allowed && (
          <>
            <div className="mop-schedule-toolbar mop-ds-toolbar" data-testid="mobile-delivery-schedule-toolbar">
              <div className="mop-ops-quick" data-testid="mds-quick-filters">
                {OPS_QUICK_FILTERS.map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    className={`mop-ops-chip${quick === f.key ? " is-active" : ""}`}
                    data-testid={`mds-quick-${f.key}`}
                    onClick={() => setQuick(f.key)}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
              <div className="mop-schedule-filter-actions" data-testid="mds-filter-actions">
                <button
                  type="button"
                  className={"mop-btn-ghost mop-ds-filter-btn" + (activeFilterCount > 0 ? " is-active" : "")}
                  onClick={openFilterPanel}
                  data-testid="mds-filter-btn"
                  aria-label="Filter"
                  aria-expanded={filterOpen}
                >
                  <Filter size={13} aria-hidden />
                  <span>Filter{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""}</span>
                </button>
                <button
                  type="button"
                  className="mop-btn-ghost mop-schedule-reset"
                  onClick={resetFilters}
                  disabled={activeFilterCount === 0 && quick === "semua_aktif"}
                  data-testid="mds-filter-reset"
                >
                  Reset
                </button>
              </div>
              <span className="mop-schedule-count" data-testid="mds-count">
                {visible.length} / {rows.length} entri
              </span>
              <button
                type="button"
                className="mop-btn-ghost"
                onClick={() => setReloadKey((n) => n + 1)}
                title="Muat ulang"
                data-testid="mds-refresh"
                disabled={busy}
                aria-label="Muat ulang"
              >
                <RefreshCw size={14} className={busy ? "spin" : ""} aria-hidden />
              </button>
              {canOptimize && (
                <button
                  type="button"
                  className="mop-btn-primary"
                  data-testid="mds-optimasi-rute"
                  disabled={busy || visible.length === 0}
                  onClick={() => {
                    persistScroll();
                    const ids = visible.map((r) => r.id).filter(Boolean);
                    navigate(mobileScheduleReviewHref(ids));
                  }}
                >
                  Optimasi Rute
                </button>
              )}
            </div>

            {busy && (
              <p className="mop-empty" data-testid="mds-loading">Memuat Jadwal Pengiriman...</p>
            )}
            {!busy && err && (
              <div className="mop-error" data-testid="mds-error">
                <p>Gagal memuat Jadwal Pengiriman.</p>
                {err !== "Gagal memuat Jadwal Pengiriman." ? <p className="mop-schedule-error-detail">{err}</p> : null}
                <button
                  type="button"
                  className="mop-btn-primary"
                  onClick={() => setReloadKey((n) => n + 1)}
                  data-testid="mds-retry"
                >
                  Coba Lagi
                </button>
              </div>
            )}
            {!busy && !err && rows.length === 0 && (
              <p className="mop-empty" data-testid="mds-empty">Tidak ada jadwal pengiriman.</p>
            )}
            {!busy && !err && rows.length > 0 && (
              <div
                className="mop-schedule-scroll production-sheet production-sheet-viewport"
                data-testid="mds-scroll"
                ref={sheetZoom.setViewportRef}
                style={productionSheetZoomStyle(sheetZoom.zoom)}
                onScroll={persistScroll}
              >
                <table className="production-sheet-table" data-testid="mds-table">
                  <colgroup>
                    {MOBILE_DELIVERY_SCHEDULE_COLS.map((col) => (
                      <col key={col.key} style={mobileDeliverySheetColumnStyle(col)} />
                    ))}
                  </colgroup>
                  <thead className="production-sheet-header" data-testid="mds-thead">
                    <tr>
                      {MOBILE_DELIVERY_SCHEDULE_COLS.map((col) => (
                        <th
                          key={col.key}
                          scope="col"
                          className={col.num ? "num" : ""}
                          style={mobileDeliverySheetColumnStyle(col)}
                        >
                          {col.sort ? (
                            <button
                              type="button"
                              className="production-sheet-th-sort mop-schedule-th-sort"
                              onClick={() => onHeaderSort(DELIVERY_SORT_KEYS[col.sort] || col.sort)}
                              data-testid={`mds-sort-${col.key}`}
                              aria-label={`Urutkan ${col.label}`}
                            >
                              {col.label}
                            </button>
                          ) : col.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((r) => {
                      const badge = deliveryOpsBadge(r);
                      const workHref = mobileScheduleWorkHref(r.id, hasPerm, "delivery");
                      return (
                        <tr
                          key={r.id || r.order_number}
                          className="production-sheet-row mop-schedule-row"
                          data-testid={`mds-row-${r.order_number}`}
                          data-to={workHref}
                          tabIndex={0}
                          onClick={() => {
                            if (sheetZoom.consumeClickSuppression()) return;
                            openRow(r);
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              if (sheetZoom.consumeClickSuppression()) return;
                              openRow(r);
                            }
                          }}
                        >
                          <td className="production-sheet-cell">
                            {badge ? (
                              <span className={`mop-ops-badge mop-ops-badge-${badge.tone}`}>{badge.label}</span>
                            ) : "—"}
                          </td>
                          <td className="mono production-sheet-cell">
                            {formatDate(r.effective_delivery_date, { dateOnly: true })}
                          </td>
                          <td className="mono production-sheet-cell">{r.order_number || "-"}</td>
                          <td className="production-sheet-cell">{r.customer_name || "-"}</td>
                          <td className="production-sheet-cell mono" data-testid={`mds-sj-${r.order_number}`}>
                            {r.sj_label || "—"}
                          </td>
                          <td className="production-sheet-cell num">
                            <PackingCell row={r} />
                          </td>
                          <td className="production-sheet-cell">
                            <ProgressCell
                              pct={r.percent_delivered_display ?? r.percent_delivered}
                              tone={terkirimTone(r.percent_delivered_display ?? r.percent_delivered)}
                              testid={`mds-terkirim-${r.order_number}`}
                              title={`Terkirim ${formatIdQty(r.qty_delivered ?? 0)} / ${formatIdQty(r.qty_total ?? 0)}`}
                              primaryLabel={formatIdQty(r.qty_delivered ?? 0)}
                              contextLabel={`dari ${formatIdQty(r.qty_total ?? 0)} qty PO`}
                            />
                          </td>
                          <td className="production-sheet-cell">
                            <ProgressCell
                              pct={r.percent_ready_to_ship_display ?? r.percent_ready_to_ship}
                              tone="ready"
                              testid={`mds-dapat-${r.order_number}`}
                              title={`Dicetak operator ${formatIdQty(r.qty_printed_operator ?? 0)} / ${formatIdQty(r.print_qty_total ?? 0)}`}
                              primaryLabel={formatIdQty(r.qty_printed_operator ?? 0)}
                              contextLabel={`dari ${formatIdQty(r.print_qty_total ?? 0)} qty cetak`}
                            />
                          </td>
                          <td className="production-sheet-cell">{r.driver_name || "—"}</td>
                          <td className="production-sheet-cell" data-testid={`mds-opt-${r.order_number}`}>
                            {(() => {
                              const meta = optimizationStatusMeta(r);
                              return (
                                <span className={`mds-opt-badge is-${meta.code}`} title={meta.label}>
                                  {meta.label}
                                  {meta.code === "sudah_dioptimasi" && meta.routeId && hasPerm(ROUTE_PERMS.view) ? (
                                    <Link
                                      className="mds-opt-link"
                                      to={`/optimasi-rute/${encodeURIComponent(meta.routeId)}`}
                                      data-testid={`mds-lihat-rute-${r.order_number}`}
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        persistScroll();
                                      }}
                                    >
                                      Lihat Rute
                                    </Link>
                                  ) : null}
                                </span>
                              );
                            })()}
                          </td>
                          <td className="production-sheet-cell" data-testid={`mds-status-${r.order_number}`}>
                            {r.status_label}
                          </td>
                          <td className="mono production-sheet-cell" data-testid={`mds-deadline-${r.order_number}`}>
                            {formatDate(r.deadline, { dateOnly: true })}
                          </td>
                          <td className="mono production-sheet-cell" data-testid={`mds-manual-${r.order_number}`}>
                            {r.jadwal_manual ? formatDate(r.jadwal_manual, { dateOnly: true }) : "—"}
                          </td>
                          <td className="production-sheet-cell mop-ds-actions" onClick={(e) => e.stopPropagation()}>
                            {r.id && (
                              <Link
                                className="ghost sm"
                                to={workHref}
                                data-testid={`mds-kerja-${r.order_number}`}
                                onClick={(e) => {
                                  if (sheetZoom.consumeClickSuppression()) e.preventDefault();
                                  else persistScroll();
                                }}
                              >
                                Kerjakan
                              </Link>
                            )}
                            <SuratJalanViewButton
                              order={{ id: r.id, po_number: r.order_number, customer_name: r.customer_name, sj_files: r.sj_files }}
                              entries={r.sj_files}
                              label={r.sj_file_count > 1 ? `SJ (${r.sj_file_count})` : "SJ"}
                              testid={`mds-sj-lihat-${r.order_number}`}
                            />
                            {canSetLocation && r.id && (
                              <button
                                type="button"
                                className="ghost sm"
                                data-testid={`mds-atur-lokasi-${r.order_number}`}
                                onClick={async () => {
                                  if (sheetZoom.consumeClickSuppression()) return;
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
                    {!visible.length && (
                      <tr>
                        <td
                          colSpan={MOBILE_DELIVERY_SCHEDULE_COLS.length}
                          className="production-sheet-empty mop-schedule-filter-empty"
                          data-testid="mds-filter-empty"
                        >
                          Tidak ada jadwal pengiriman.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </main>

      <OutletLocationModal
        open={!!locRow}
        row={locRow}
        canEdit={canSetLocation}
        onClose={() => setLocRow(null)}
        onSaved={() => load()}
      />

      {filterOpen && (
        <div
          className="mop-ds-filter-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Filter Jadwal Pengiriman"
          data-testid="mds-filter-panel"
          onClick={(e) => e.target === e.currentTarget && closeFilterPanel()}
        >
          <div className="mop-ds-filter-panel" onClick={(e) => e.stopPropagation()} data-testid="mds-filter-panel-body">
            <header className="mop-ds-filter-head">
              <h3>Filter</h3>
              <button
                type="button"
                className="mop-menu-close"
                onClick={closeFilterPanel}
                aria-label="Tutup"
                data-testid="mds-filter-close"
              >
                <X size={16} aria-hidden />
              </button>
            </header>
            <div className="mop-ds-filter-fields">
              <label className="mop-ds-filter-field">
                <span>Nama Customer</span>
                <input type="text" value={draftFilters.customer} onChange={(e) => setDraft("customer", e.target.value)} placeholder="Cari customer" data-testid="mds-adv-customer" />
              </label>
              <label className="mop-ds-filter-field">
                <span>No PO</span>
                <input type="text" value={draftFilters.po} onChange={(e) => setDraft("po", e.target.value)} placeholder="Cari No PO" data-testid="mds-adv-po" />
              </label>
              <label className="mop-ds-filter-field">
                <span>Tanggal Mulai</span>
                <input type="date" value={draftFilters.dateFrom} onChange={(e) => setDraft("dateFrom", e.target.value)} data-testid="mds-adv-from" />
              </label>
              <label className="mop-ds-filter-field">
                <span>Tanggal Akhir</span>
                <input type="date" value={draftFilters.dateTo} onChange={(e) => setDraft("dateTo", e.target.value)} data-testid="mds-adv-to" />
              </label>
              <label className="mop-ds-filter-field">
                <span>Status</span>
                <select value={draftFilters.status} onChange={(e) => setDraft("status", e.target.value)} data-testid="mds-adv-status">
                  {DELIVERY_SCHEDULE_STATUS_OPTIONS.map((o) => (
                    <option key={o.key} value={o.key}>{o.label}</option>
                  ))}
                </select>
              </label>
              <label className="mop-ds-filter-field">
                <span>Driver</span>
                <input type="text" value={draftFilters.driver} onChange={(e) => setDraft("driver", e.target.value)} placeholder="Cari driver" data-testid="mds-adv-driver" />
              </label>
              <label className="mop-ds-filter-field">
                <span>Terkirim</span>
                <select value={draftFilters.terkirim || "all"} onChange={(e) => setDraft("terkirim", e.target.value)} data-testid="mds-adv-terkirim">
                  <option value="all">Semua</option>
                  <option value="none">Belum Terkirim</option>
                  <option value="partial">Terkirim Sebagian</option>
                  <option value="full">Terkirim 100%</option>
                </select>
              </label>
              <label className="mop-ds-filter-field">
                <span>Dapat Dikirim</span>
                <select value={draftFilters.dapatDikirim || "all"} onChange={(e) => setDraft("dapatDikirim", e.target.value)} data-testid="mds-adv-dapat">
                  <option value="all">Semua</option>
                  <option value="none">Tidak Dapat Dikirim</option>
                  <option value="partial">Dapat Dikirim Sebagian</option>
                  <option value="full">Dapat Dikirim 100%</option>
                </select>
              </label>
            </div>
            <footer className="mop-ds-filter-foot" data-testid="mds-filter-panel-foot">
              <button type="button" className="mop-btn-ghost" onClick={resetFilters} data-testid="mds-adv-reset">
                Reset Filter
              </button>
              <button type="button" className="mop-btn-primary" onClick={applyDraftFilters} data-testid="mds-filter-apply">
                Terapkan Filter
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
