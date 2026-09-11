import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Filter } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatApiError } from "../lib/errors";
import useProductionSheetZoom from "../hooks/useProductionSheetZoom";
import ProductionScheduleSheetCells from "../components/ProductionScheduleSheetCells";
import ProductionScheduleRingkasan, { ProductionScheduleSearchCluster } from "../components/ProductionScheduleRingkasan";
import {
  FILTER_DEFAULTS,
  applyProductionFilters,
  isScheduledToday,
  productionFiltersAreActive,
} from "../lib/productionScheduleFilters";
import { buildProductionScheduleDisplayRows } from "../lib/productionScheduleDisplayRows";
import { operatorQtyCetakProgress, poTotalQtyDisplay } from "../lib/productionScheduleQty";
import { buildProductionScheduleSummary } from "../lib/productionScheduleSummary";
import { productionSheetColumnStyle } from "../lib/productionScheduleTable";
import { MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS } from "../lib/mobileProductionScheduleCols";
import { MOBILE_LOAD_TIMEOUT_MS, withTimeout } from "../lib/mobileRequest";
import {
  canViewMobileSchedule,
  mobileProductionDashboardParams,
  rowsFromProductionDashboard,
} from "../lib/mobileSchedule";
import { mobileScheduleWorkHref } from "../lib/mobileWorkspaces";
import {
  OPS_QUICK_FILTERS,
  productionOpsBadge,
  productionQuickMatch,
  sortByOpsQueue,
  productionQueueRank,
} from "../lib/mobileOpsQueue";
import {
  MOBILE_PROD_FILTER_KEY,
  MOBILE_PROD_SCROLL_KEY,
  readJsonSession,
  readScrollSession,
  scopedOpsKey,
  writeJsonSession,
  writeScrollSession,
} from "../lib/mobileOpsSession";
import {
  capturePoOpenFromList,
  poNavStateFromList,
  storageUserKey,
} from "../lib/poListReturn";
import { productionSheetZoomStyle } from "../lib/productionSheetZoom";
import MobileTopBar from "./MobileTopBar";

export function scheduleLoadErrorMessage(err) {
  const status = err?.response?.status;
  if (err?.code === "ETIMEDOUT" || err?.code === "ECONNABORTED") {
    return "Gagal memuat jadwal produksi.";
  }
  const msg = formatApiError(err, "Gagal memuat jadwal produksi.");
  if (status === 403) return msg || "Anda tidak punya akses untuk melihat jadwal produksi.";
  if (status === 401) return msg || "Sesi berakhir. Silakan masuk kembali.";
  return msg || "Gagal memuat jadwal produksi.";
}

export default function MobileProductionSchedulePage() {
  const navigate = useNavigate();
  const { user, hasPerm } = useAuth();
  const allowed = canViewMobileSchedule(hasPerm);
  const userKey = storageUserKey(user);
  const filterKey = scopedOpsKey(MOBILE_PROD_FILTER_KEY, userKey);
  const scrollKey = scopedOpsKey(MOBILE_PROD_SCROLL_KEY, userKey);
  const saved = readJsonSession(filterKey, {
    q: FILTER_DEFAULTS.q,
    includeCancelled: FILTER_DEFAULTS.includeCancelled,
    includeCompleted: FILTER_DEFAULTS.includeCompleted,
    quick: "semua_aktif",
  });
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(allowed);
  const [reloadKey, setReloadKey] = useState(0);
  const [q, setQ] = useState(saved.q ?? FILTER_DEFAULTS.q);
  const [includeCancelled, setIncludeCancelled] = useState(Boolean(saved.includeCancelled));
  const [includeCompleted, setIncludeCompleted] = useState(
    saved.includeCompleted == null ? FILTER_DEFAULTS.includeCompleted : Boolean(saved.includeCompleted),
  );
  const [quick, setQuick] = useState(saved.quick || "semua_aktif");
  const [filterOpen, setFilterOpen] = useState(false);
  const sheetZoom = useProductionSheetZoom();
  const scrollRestored = useRef(false);

  const filterState = {
    ...FILTER_DEFAULTS,
    q,
    sortBy: FILTER_DEFAULTS.sortBy,
    includeCancelled,
    includeCompleted,
  };

  useEffect(() => {
    writeJsonSession(filterKey, {
      q,
      includeCancelled,
      includeCompleted,
      quick,
    });
  }, [filterKey, q, includeCancelled, includeCompleted, quick]);

  useEffect(() => {
    if (!allowed) {
      setLoading(false);
      setRows([]);
      setErr("");
      return undefined;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr("");
      try {
        const payload = await withTimeout(
          api.productionDashboard(mobileProductionDashboardParams({ includeCancelled, includeCompleted })),
          MOBILE_LOAD_TIMEOUT_MS,
        );
        if (cancelled) return;
        setRows(rowsFromProductionDashboard(payload));
      } catch (e) {
        if (cancelled) return;
        setRows([]);
        setErr(scheduleLoadErrorMessage(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [allowed, includeCancelled, includeCompleted, reloadKey]);

  const filtered = useMemo(
    () => applyProductionFilters(rows, filterState).filter((r) => productionQuickMatch(r, quick)),
    [rows, q, includeCancelled, includeCompleted, quick],
  );
  const sorted = useMemo(() => sortByOpsQueue(filtered, productionQueueRank), [filtered]);
  const displayRows = useMemo(() => buildProductionScheduleDisplayRows(sorted), [sorted]);
  const summaryMetrics = useMemo(() => buildProductionScheduleSummary(filtered), [filtered]);
  const filtersActive = productionFiltersAreActive(filterState) || quick !== "semua_aktif";

  useLayoutEffect(() => {
    if (loading || scrollRestored.current) return;
    const el = sheetZoom.viewportRef?.current;
    const savedScroll = readScrollSession(scrollKey);
    if (el && savedScroll) {
      el.scrollLeft = savedScroll.left;
      el.scrollTop = savedScroll.top;
    }
    scrollRestored.current = true;
  }, [loading, sheetZoom.viewportRef, displayRows.length, scrollKey]);

  const persistScroll = () => {
    const el = sheetZoom.viewportRef?.current;
    if (!el) return;
    writeScrollSession(scrollKey, el.scrollLeft, el.scrollTop);
  };

  const resetFilters = () => {
    setQ(FILTER_DEFAULTS.q);
    setIncludeCancelled(FILTER_DEFAULTS.includeCancelled);
    setIncludeCompleted(FILTER_DEFAULTS.includeCompleted);
    setQuick("semua_aktif");
  };

  const openRow = (row) => {
    persistScroll();
    const href = mobileScheduleWorkHref(row.id, hasPerm, "production");
    if (!href || !row?.id) return;
    const state = poNavStateFromList({
      pathname: typeof window !== "undefined" ? window.location.pathname : "/m/schedule",
      search: typeof window !== "undefined" ? window.location.search : "",
    });
    capturePoOpenFromList({
      user,
      orderId: row.id,
      pathname: typeof window !== "undefined" ? window.location.pathname : "/m/schedule",
      search: typeof window !== "undefined" ? window.location.search : "",
    });
    navigate(href, { state });
  };

  return (
    <div className="mop-schedule-root">
      <MobileTopBar kicker="ETERNAL PACKAGING" title="Jadwal Produksi" />
      <main className="mop-content mop-schedule-page" data-testid="mobile-schedule-page">
        {!allowed && (
          <div className="mop-error" data-testid="mobile-schedule-forbidden">
            <p>Anda tidak punya akses untuk melihat jadwal produksi.</p>
          </div>
        )}
        {allowed && (
          <>
            <div className="mop-schedule-toolbar" data-testid="mobile-schedule-toolbar">
              <div className="mop-ops-quick" data-testid="mobile-schedule-quick-filters">
                {OPS_QUICK_FILTERS.map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    className={`mop-ops-chip${quick === f.key ? " is-active" : ""}`}
                    data-testid={`mobile-schedule-quick-${f.key}`}
                    onClick={() => setQuick(f.key)}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
              <ProductionScheduleSearchCluster
                value={q}
                onChange={(e) => setQ(e.target.value)}
                inputTestId="mobile-schedule-search"
                searchButtonTestId="mobile-schedule-search-btn"
              />
              <ProductionScheduleRingkasan metrics={summaryMetrics} testidPrefix="mobile-schedule" />
              <span className="mop-schedule-count" data-testid="mobile-schedule-count">
                {filtered.length} / {rows.length} entri
              </span>
              <label className="mop-schedule-check">
                <input
                  type="checkbox"
                  checked={includeCompleted}
                  onChange={(e) => setIncludeCompleted(e.target.checked)}
                  data-testid="mobile-schedule-include-completed"
                />
                Selesai
              </label>
              <label className="mop-schedule-check">
                <input
                  type="checkbox"
                  checked={includeCancelled}
                  onChange={(e) => setIncludeCancelled(e.target.checked)}
                  data-testid="mobile-schedule-include-cancelled"
                />
                Cancelled
              </label>
              <div className="mop-schedule-filter-actions" data-testid="mobile-schedule-filter-actions">
                <button
                  type="button"
                  className={"mop-btn-ghost" + (filterOpen ? " is-active" : "")}
                  onClick={() => setFilterOpen((v) => !v)}
                  data-testid="mobile-schedule-filter-btn"
                >
                  <Filter size={12} /> Filter
                </button>
                <button
                  type="button"
                  className="mop-btn-ghost mop-schedule-reset"
                  onClick={resetFilters}
                  disabled={!filtersActive}
                  data-testid="mobile-schedule-reset"
                >
                  Reset
                </button>
              </div>
            </div>
            {filterOpen && (
              <div className="mop-ds-filter-panel" data-testid="mobile-schedule-filter-panel" style={{ marginBottom: 6 }}>
                <p className="mop-topbar-sub" style={{ color: "var(--mop-muted)", margin: 0 }}>
                  Filter lanjutan memakai pencarian + chip operasional di atas. Checkbox Selesai/Cancelled tetap di toolbar.
                </p>
              </div>
            )}
            {loading && (
              <p className="mop-empty" data-testid="mobile-schedule-loading">Memuat jadwal produksi...</p>
            )}
            {!loading && err && (
              <div className="mop-error" data-testid="mobile-schedule-error">
                <p>Gagal memuat jadwal produksi.</p>
                {err !== "Gagal memuat jadwal produksi." ? <p className="mop-schedule-error-detail">{err}</p> : null}
                <button
                  type="button"
                  className="mop-btn-primary"
                  onClick={() => setReloadKey((n) => n + 1)}
                  data-testid="mobile-schedule-retry"
                >
                  Coba Lagi
                </button>
              </div>
            )}
            {!loading && !err && rows.length === 0 && (
              <p className="mop-empty" data-testid="mobile-schedule-empty">Belum ada jadwal produksi.</p>
            )}
            {!loading && !err && rows.length > 0 && (
              <div
                className="mop-schedule-scroll production-sheet production-sheet-viewport"
                data-testid="mobile-schedule-scroll"
                ref={sheetZoom.setViewportRef}
                style={productionSheetZoomStyle(sheetZoom.zoom)}
                onScroll={persistScroll}
              >
                <table
                  className="production-sheet-table"
                  data-testid="mobile-schedule-table"
                >
                  <colgroup>
                    {MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((col) => (
                      <col key={col.key} style={productionSheetColumnStyle(col)} />
                    ))}
                  </colgroup>
                  <thead className="production-sheet-header" data-testid="mobile-schedule-thead">
                    <tr>
                      {MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((col) => (
                        <th
                          key={col.key}
                          scope="col"
                          className={col.num || col.align === "right" || col.type === "qty" ? "num" : ""}
                          style={productionSheetColumnStyle(col)}
                        >
                          {col.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {displayRows.map((row) => {
                      const qtyProg = poTotalQtyDisplay(row);
                      const cetakProg = operatorQtyCetakProgress(row, row.displayOperator?.splits);
                      const isToday = isScheduledToday(row);
                      const href = mobileScheduleWorkHref(row.id, hasPerm, "production");
                      const badge = productionOpsBadge(row);
                      return (
                        <tr
                          key={row.displayRowKey}
                          className={`production-sheet-row mop-schedule-row${row.hold ? " hold" : ""}${row.cancelled ? " cancelled" : ""}`}
                          data-testid={`mobile-schedule-row-${row.po_number}`}
                          data-row-key={row.displayRowKey}
                          data-to={href}
                          tabIndex={0}
                          onClick={() => {
                            if (sheetZoom.consumeClickSuppression()) return;
                            openRow(row);
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              if (sheetZoom.consumeClickSuppression()) return;
                              openRow(row);
                            }
                          }}
                        >
                          <ProductionScheduleSheetCells
                            row={row}
                            variant="mobile"
                            qtyProg={qtyProg}
                            cetakProg={cetakProg}
                            isToday={isToday}
                            opsBadge={badge}
                            columns={MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS}
                          />
                        </tr>
                      );
                    })}
                    {!sorted.length && (
                      <tr>
                        <td colSpan={MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.length} className="production-sheet-empty mop-schedule-filter-empty">
                          Tidak ada PO cocok dengan filter.
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
    </div>
  );
}
