import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Filter, RefreshCw, RotateCcw, Search, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { clampPercent, terkirimTone } from "../lib/shipmentStatus";
import {
  DELIVERY_SCHEDULE_STATUS_OPTIONS,
  EMPTY_DELIVERY_FILTERS,
  EMPTY_DELIVERY_SORT,
  buildDeliveryDetailsSummary,
  countActiveDeliveryFilters,
  dateKey,
  matchesDeliveryScheduleFilters,
  normalizeDeliveryScheduleFilters,
  normalizeDeliveryScheduleStatus,
  orderIdsForSelectedScheduleKeys,
  scheduleRowSelectionKey,
  scheduleVisibleSelectionKeys,
} from "../lib/deliverySchedule";
import { locationSetupSortValue, locationSetupStatusLabel } from "../lib/deliveryRouteBridge";
import {
  canOptimizeFromDeliverySchedule,
  desktopScheduleReviewHref,
} from "../lib/mobileDeliverySchedule";
import {
  headerCheckState,
  pruneSelection,
  selectAllVisible,
  toggleId,
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
import PoIdentityBadges from "../components/PoIdentityBadges";
import DeliveryPlanDateRangeField from "../components/deliveryPlan/DeliveryPlanDateRangeField";
import DeliveryPlanAdvancedTable from "../components/deliveryPlan/DeliveryPlanAdvancedTable";
import {
  clientFilterRows,
  clientSortRows,
} from "../components/deliveryPlan/deliveryPlanTableLayout";
import "./DeliveryPlanPage.css";
import "./DeliverySchedulePage.css";

const DS_TABLE_ID = "delivery-schedule";
const DS_PAGE_SIZES = [25, 50, 100];

/** Shipment-scoped SJ number from GET /delivery/schedule — never PO-level latest SJ. */
export function scheduleRowSjNumber(row) {
  const label = String(row?.sj_label || "").trim();
  if (label) return label;
  const latest = String(row?.latest_sj_number || "").trim();
  if (latest) return latest;
  const first = Array.isArray(row?.sj_numbers) ? String(row.sj_numbers[0] || "").trim() : "";
  return first;
}

/** Desktop PO detail href. Preserve exact row shipment_id; never invent one. */
export function desktopDeliveryPoHref(orderId, shipmentId) {
  const id = String(orderId || "").trim();
  if (!id) return "";
  const sid = String(shipmentId || "").trim();
  const base = `/po/${encodeURIComponent(id)}`;
  return sid ? `${base}?shipment=${encodeURIComponent(sid)}` : base;
}

/** Desktop Kerjakan Pengiriman href. Preserve exact row shipment_id; never invent one. */
export function desktopDeliveryKerjaHref(orderId, shipmentId) {
  const id = String(orderId || "").trim();
  if (!id) return "";
  const sid = String(shipmentId || "").trim();
  const base = `/po/${encodeURIComponent(id)}/kerja?divisi=terkirim`;
  return sid ? `${base}&shipment=${encodeURIComponent(sid)}` : base;
}

function sjCellTestId(row) {
  const po = row?.order_number || "row";
  return row?.shipment_id ? `ds-sj-${po}-${row.shipment_id}` : `ds-sj-${po}`;
}

function ProgressCell({ pct, tone, testid, title }) {
  const p = clampPercent(pct);
  return (
    <div className={"ship-bar ship-bar-" + tone} data-testid={testid} title={title}>
      <div className="ship-bar-fill" style={{ width: p + "%" }} />
      <span className="ship-bar-label">{p}%</span>
    </div>
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
  const userId = user?.id || user?.email || "anon";
  const saved = readListUiState(userKey, "delivery-schedule", {
    filters: EMPTY_DELIVERY_FILTERS,
    sort: EMPTY_DELIVERY_SORT,
  });
  const initialFilters = normalizeDeliveryScheduleFilters(saved.filters || {});
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  // Applied filters drive visible rows + session persistence. Draft is toolbar/modal edits until Terapkan.
  const [filters, setFilters] = useState(initialFilters);
  const [draftFilters, setDraftFilters] = useState(initialFilters);
  const [sortBy, setSortBy] = useState(saved.sort?.key || "");
  const [sortDir, setSortDir] = useState(saved.sort?.direction || "asc");
  const [colFilters, setColFilters] = useState({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [filterOpen, setFilterOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [locRow, setLocRow] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const selectAllRef = useRef(null);
  const selectedRef = useRef(selected);
  const pageVisibleIdsRef = useRef([]);
  const headerCheckRef = useRef({ checked: false, indeterminate: false });
  const resetColumnsRef = useRef(null);
  selectedRef.current = selected;

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.deliverySchedule();
      let nextRows = d.rows || [];
      // Local visual preview only — dead-code-eliminated from production builds.
      if (process.env.NODE_ENV === "development") {
        const params = new URLSearchParams(location.search || "");
        if (params.get("previewStatusLokasi") === "1") {
          // eslint-disable-next-line global-require
          const {
            STATUS_LOKASI_SORT_DUMMY_ROWS,
            TABLE_UI_PREVIEW_DUMMY_ROWS,
            BATCH2_UI_PREVIEW_DUMMY_ROWS,
          } = require("../lib/deliveryScheduleStatusLokasiFixtures");
          nextRows = [
            ...STATUS_LOKASI_SORT_DUMMY_ROWS,
            ...TABLE_UI_PREVIEW_DUMMY_ROWS,
            ...BATCH2_UI_PREVIEW_DUMMY_ROWS,
            ...nextRows,
          ];
        }
      }
      setRows(nextRows);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Jadwal Pengiriman"));
    } finally {
      setBusy(false);
    }
  }, [location.search]);

  useEffect(() => { load(); }, [load]);

  // Safe bookmark: /produksi/pengiriman-hari-ini → ?status=hari_ini on Jadwal Pengiriman.
  // Legacy ?status=belum_dijadwalkan normalizes to semua (and is cleared from the URL).
  useEffect(() => {
    const params = new URLSearchParams(location.search || "");
    const raw = (params.get("status") || "").trim().toLowerCase();
    if (!raw) return;
    const status = normalizeDeliveryScheduleStatus(raw);
    if (raw !== status) {
      params.delete("status");
      const q = params.toString();
      navigate({ pathname: location.pathname, search: q ? `?${q}` : "" }, { replace: true });
      return;
    }
    if (status === "semua") return;
    setFilters((prev) => (prev.status === status ? prev : { ...prev, status }));
    setDraftFilters((prev) => (prev.status === status ? prev : { ...prev, status }));
  }, [location.search, location.pathname, navigate]);

  useEffect(() => {
    writeListUiState(userKey, "delivery-schedule", {
      filters: normalizeDeliveryScheduleFilters(filters),
      sort: sortBy ? { key: sortBy, direction: sortDir || "asc" } : { ...EMPTY_DELIVERY_SORT },
    });
  }, [userKey, filters, sortBy, sortDir]);

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

  // Pipeline: API rows → toolbar filters → column filters → sort → pagination.
  const toolbarFiltered = useMemo(
    () => (rows || []).filter((r) => matchesDeliveryScheduleFilters(r, filters)),
    [rows, filters],
  );

  const tableRows = useMemo(() => toolbarFiltered.map((r) => {
    const eff = dateKey(r.sj_delivery_date || r.effective_delivery_date);
    const pctDelivered = Number(r.percent_delivered_display ?? r.percent_delivered) || 0;
    const pctReady = Number(r.percent_ready_to_ship_display ?? r.percent_ready_to_ship) || 0;
    return {
      ...r,
      effective_delivery_date: eff,
      order_number: r.order_number || "",
      customer_name: r.customer_name || "",
      status: r.status || "",
      status_label: r.status_label || "",
      location_status: locationSetupStatusLabel(r),
      location_status_sort: locationSetupSortValue(r),
      percent_delivered: pctDelivered,
      percent_ready_to_ship: pctReady,
      sj_display: scheduleRowSjNumber(r),
    };
  }), [toolbarFiltered]);

  const columnFiltered = useMemo(
    () => clientFilterRows(tableRows, colFilters),
    [tableRows, colFilters],
  );

  const sortedRows = useMemo(
    () => clientSortRows(columnFiltered, sortBy, sortDir, "order_number"),
    [columnFiltered, sortBy, sortDir],
  );

  const totalFiltered = sortedRows.length;
  const totalPages = Math.max(1, Math.ceil(totalFiltered / pageSize) || 1);
  const safePage = Math.min(page, totalPages);

  useEffect(() => {
    if (page !== safePage) setPage(safePage);
  }, [page, safePage]);

  const pageRows = useMemo(() => {
    const start = (safePage - 1) * pageSize;
    return sortedRows.slice(start, start + pageSize);
  }, [sortedRows, safePage, pageSize]);

  const activeFilterCount = useMemo(() => countActiveDeliveryFilters(filters), [filters]);
  // Eligible selection universe = full filtered SJ set (survives pagination). Select-all = current page only.
  // Keys are shipment_id (SJ), not order.id — multi-SJ on one PO stay independent.
  const eligibleIds = useMemo(() => scheduleVisibleSelectionKeys(sortedRows), [sortedRows]);
  const pageVisibleIds = useMemo(() => scheduleVisibleSelectionKeys(pageRows), [pageRows]);
  const headerCheck = useMemo(
    () => headerCheckState(selected, pageVisibleIds),
    [selected, pageVisibleIds],
  );
  pageVisibleIdsRef.current = pageVisibleIds;
  headerCheckRef.current = headerCheck;

  const detailsSummary = useMemo(
    () => buildDeliveryDetailsSummary(columnFiltered, {
      dateFrom: filters.dateFrom,
      dateTo: filters.dateTo,
    }),
    [columnFiltered, filters.dateFrom, filters.dateTo],
  );

  useEffect(() => {
    setSelected((prev) => pruneSelection(prev, eligibleIds));
  }, [eligibleIds]);

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = headerCheck.indeterminate;
      selectAllRef.current.checked = headerCheck.checked;
    }
  }, [headerCheck.indeterminate, headerCheck.checked]);

  useEffect(() => {
    if (!detailsOpen) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") setDetailsOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detailsOpen]);

  const setDraft = (key, value) => setDraftFilters((prev) => ({ ...prev, [key]: value }));

  const openFilterPanel = () => {
    setFilterOpen(true);
  };
  const closeFilterPanel = () => setFilterOpen(false);
  const openDetails = () => setDetailsOpen(true);
  const closeDetails = () => setDetailsOpen(false);
  const applyDraftFilters = (e) => {
    e?.preventDefault?.();
    const next = normalizeDeliveryScheduleFilters({
      ...draftFilters,
      // Combined search owns q; clear stale separate po/customer from older sessions.
      po: "",
      customer: "",
    });
    setDraftFilters(next);
    setFilters(next);
    setPage(1);
    setFilterOpen(false);
  };
  const resetFilters = () => {
    const empty = { ...EMPTY_DELIVERY_FILTERS };
    setDraftFilters(empty);
    setFilters(empty);
    setPage(1);
    const params = new URLSearchParams(location.search || "");
    if (params.has("status")) {
      params.delete("status");
      const qs = params.toString();
      navigate({ pathname: location.pathname, search: qs ? `?${qs}` : "" }, { replace: true });
    }
  };
  const onDraftDateRange = (from, to) => {
    setDraftFilters((prev) => ({ ...prev, dateFrom: from, dateTo: to }));
  };
  const onDraftSearch = (value) => {
    setDraftFilters((prev) => ({ ...prev, q: value, po: "", customer: "" }));
  };

  const onOptimasiRute = () => {
    // Review API remains order-id based; derive unique orders from selected SJ keys only.
    const ids = orderIdsForSelectedScheduleKeys(sortedRows, selected);
    if (!ids.length) return;
    navigate(desktopScheduleReviewHref(ids));
  };

  const columns = useMemo(() => {
    const cols = [];
    if (canOptimize) {
      cols.push({
        key: "_select",
        label: "",
        type: "text",
        sortable: false,
        filterable: false,
        reorderable: false,
        minWidth: 44,
        maxWidth: 52,
        headerRender: () => (
          <input
            ref={selectAllRef}
            type="checkbox"
            defaultChecked={false}
            onChange={() => setSelected(selectAllVisible(pageVisibleIdsRef.current, selectedRef.current))}
            aria-label="Pilih semua baris tampilan"
            data-testid="ds-select-all"
          />
        ),
        render: (_v, r) => {
          const selKey = scheduleRowSelectionKey(r);
          if (!selKey) return null;
          const boxId = r.shipment_id
            ? `ds-checkbox-${r.order_number}-${r.shipment_id}`
            : `ds-checkbox-${r.order_number}`;
          return (
            <input
              type="checkbox"
              checked={selectedRef.current.has(selKey)}
              onChange={() => setSelected(toggleId(selectedRef.current, selKey))}
              aria-label={`Pilih ${r.order_number}${r.shipment_id ? ` ${r.sj_display || r.shipment_id}` : ""}`}
              data-testid={boxId}
            />
          );
        },
      });
    }
    cols.push(
      {
        key: "effective_delivery_date",
        label: "Tanggal Pengiriman",
        type: "date",
        minWidth: 148,
        render: (v, r) => (
          <span className="ds-date-cell">
            <span className="mono">{formatDate(v, { dateOnly: true })}</span>
            {r.sj_delivery_date && r.po_sla_date && r.sj_delivery_date !== r.po_sla_date ? (
              <span className="ds-sla-note muted" data-testid={`ds-sla-${r.order_number}`}>
                SLA PO {formatDate(r.po_sla_date, { dateOnly: true })}
              </span>
            ) : null}
          </span>
        ),
      },
      {
        key: "order_number",
        label: "No PO",
        type: "text",
        minWidth: 220,
        render: (v, r) => (
          <span className="dp-po-identity">
            {r.id ? (
              <Link
                className="dp-link-po"
                to={desktopDeliveryPoHref(r.id, r.shipment_id)}
                state={listNavState}
                onClick={() => onOpenPo(r.id)}
                title={v || ""}
                data-testid={r.shipment_id ? `ds-po-link-${r.order_number}-${r.shipment_id}` : `ds-po-link-${r.order_number}`}
              >
                {v || "—"}
              </Link>
            ) : (v || "—")}
            <PoIdentityBadges
              order={{ priority: r.priority, urgent: r.urgent, buffer_stock: r.buffer_stock }}
              testIdPrefix={`ds-po-${r.order_number}`}
            />
          </span>
        ),
      },
      {
        key: "customer_name",
        label: "Customer",
        type: "text",
        minWidth: 140,
      },
      {
        key: "status",
        label: "Status",
        type: "enum",
        minWidth: 110,
        filterOptions: DELIVERY_SCHEDULE_STATUS_OPTIONS
          .filter((o) => o.key !== "semua")
          .map((o) => ({ value: o.key, label: o.label })),
        render: (_v, r) => (
          <span data-testid={`ds-status-${r.order_number}`}>{r.status_label || r.status || "—"}</span>
        ),
      },
      {
        key: "location_status_sort",
        label: "Status Lokasi",
        type: "enum",
        minWidth: 120,
        filterOptions: [
          { value: "0", label: "Belum Diatur" },
          { value: "1", label: "Sudah Diatur" },
        ],
        render: (_v, r) => {
          const ok = r.location_status === "Sudah Diatur";
          return (
            <span
              className={"pill sm " + (ok ? "pill-ok" : "pill-warn")}
              data-testid={`ds-status-lokasi-${r.order_number}`}
            >
              {r.location_status}
            </span>
          );
        },
      },
      {
        key: "percent_delivered",
        label: "Terkirim",
        type: "number",
        align: "right",
        minWidth: 100,
        render: (v, r) => (
          <ProgressCell
            pct={v}
            tone={terkirimTone(v)}
            testid={`ds-terkirim-${r.order_number}`}
            title={`Terkirim ${r.qty_delivered ?? 0} / ${r.qty_total ?? 0}`}
          />
        ),
      },
      {
        key: "percent_ready_to_ship",
        label: "Dapat Dikirim",
        type: "number",
        align: "right",
        minWidth: 110,
        render: (v, r) => (
          <ProgressCell
            pct={v}
            tone="ready"
            testid={`ds-dapat-${r.order_number}`}
            title={`Dicetak operator ${r.qty_printed_operator ?? 0} / ${r.print_qty_total ?? 0}`}
          />
        ),
      },
      {
        key: "sj_display",
        label: "Surat Jalan",
        type: "text",
        minWidth: 140,
        render: (_v, r) => (
          <span className="ds-sj-cell">
            <span className="mono" data-testid={sjCellTestId(r)}>
              {r.sj_display || "—"}
            </span>
            {" "}
            <SuratJalanViewButton
              order={{ id: r.id, po_number: r.order_number, customer_name: r.customer_name, sj_files: r.sj_files }}
              entries={r.sj_files}
              label={r.sj_file_count > 1 ? `Lihat (${r.sj_file_count})` : "Lihat"}
              testid={`ds-sj-lihat-${r.order_number}`}
            />
          </span>
        ),
      },
      {
        key: "aksi",
        label: "Aksi",
        type: "text",
        sortable: false,
        filterable: false,
        minWidth: 160,
        render: (_v, r) => (
          <span className="ds-aksi-cell">
            {r.id && (
              <Link
                className="ghost sm"
                to={desktopDeliveryKerjaHref(r.id, r.shipment_id)}
                state={listNavState}
                onClick={() => onOpenPo(r.id)}
                data-testid={r.shipment_id ? `ds-kerja-link-${r.order_number}-${r.shipment_id}` : `ds-kerja-link-${r.order_number}`}
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
          </span>
        ),
      },
    );
    return cols;
  }, [canOptimize, canSetLocation, listNavState]);

  return (
    <div className="ship-status-page dp-page" data-testid="delivery-schedule-page">
      <div className="ship-status-chrome">
        <header className="dp-header">
          <div>
            <p className="eyebrow">Produksi · Pengiriman</p>
            <h1>Jadwal Pengiriman</h1>
            <p className="subhead">
              Daftar surat jalan aktif dengan tanggal pengiriman terjadwal.
            </p>
          </div>
          <div className="dp-header-actions">
            <button
              type="button"
              className="ghost sm"
              data-testid="ds-rincian-pengiriman"
              onClick={openDetails}
              disabled={busy}
              title="Rincian kuantitas pengiriman sesuai filter aktif"
            >
              Rincian Pengiriman
            </button>
            {canOptimize && (
              <button
                type="button"
                className="dp-btn-primary"
                data-testid="ds-optimasi-rute"
                disabled={busy || selected.size === 0}
                title={selected.size === 0 ? "Pilih SJ terlebih dahulu" : `Optimasi Rute untuk ${selected.size} SJ terpilih`}
                onClick={onOptimasiRute}
              >
                Optimasi Rute{selected.size > 0 ? ` (${selected.size})` : ""}
              </button>
            )}
            <button
              type="button"
              className="ghost sm"
              onClick={load}
              disabled={busy}
              title="Muat ulang"
              data-testid="ds-refresh"
              aria-label="Muat ulang"
            >
              <RefreshCw size={14} className={busy ? "spin" : ""} aria-hidden="true" />
            </button>
          </div>
        </header>
        <form
          className="dp-filters"
          data-testid="delivery-schedule-filters"
          onSubmit={applyDraftFilters}
        >
          <div className="dp-search">
            <Search size={14} aria-hidden />
            <input
              type="search"
              value={draftFilters.q || ""}
              onChange={(e) => onDraftSearch(e.target.value)}
              placeholder="Cari PO / customer…"
              data-testid="ds-filter-q"
              aria-label="Cari nomor PO atau customer"
            />
          </div>
          <DeliveryPlanDateRangeField
            label="Tanggal Pengiriman"
            testIdPrefix="ds-period"
            dateFrom={draftFilters.dateFrom}
            dateTo={draftFilters.dateTo}
            onApply={onDraftDateRange}
          />
          <select
            value={draftFilters.status}
            onChange={(e) => setDraft("status", e.target.value)}
            data-testid="ds-filter-status"
            aria-label="Status pengiriman"
          >
            {DELIVERY_SCHEDULE_STATUS_OPTIONS.map((o) => (
              <option key={o.key} value={o.key}>{o.label}</option>
            ))}
          </select>
          <button
            type="button"
            className={"ds-adv-filter-btn" + (activeFilterCount > 0 ? " is-active" : "")}
            onClick={openFilterPanel}
            data-testid="ds-filter-btn"
            aria-label="Filter lanjutan"
          >
            <Filter size={13} />
            <span>Filter{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""}</span>
          </button>
          <div className="dp-filter-actions">
            <button type="submit" className="dp-btn-primary" data-testid="ds-filter-apply" title="Terapkan filter">
              Terapkan
            </button>
            <button type="button" className="ghost sm" onClick={resetFilters} data-testid="ds-filter-reset" title="Reset filter">
              <RotateCcw size={12} aria-hidden /> Reset
            </button>
            <button
              type="button"
              className="ghost sm ds-reset-cols-btn"
              onClick={() => resetColumnsRef.current?.()}
              data-testid="dp-reset-cols-delivery-schedule"
              title="Reset layout kolom tabel (bukan filter bisnis)"
            >
              <RotateCcw size={12} aria-hidden /> Reset Kolom
            </button>
          </div>
        </form>
        {canOptimize && selected.size > 0 && (
          <div className="bulk-bar" data-testid="ds-bulk-bar">
            <span data-testid="ds-selected-count"><b>{selected.size}</b> SJ dipilih</span>
            <div className="bulk-actions">
              <button type="button" className="ghost sm" onClick={() => setSelected(new Set())} data-testid="ds-bulk-clear">
                Batal pilih
              </button>
            </div>
          </div>
        )}
        {err && <div className="error" data-testid="ds-error">{err}</div>}
        {busy ? <p className="muted" data-testid="ds-loading">Memuat…</p> : null}
      </div>

      <div data-testid="delivery-schedule-table-wrap">
        <DeliveryPlanAdvancedTable
          tableId={DS_TABLE_ID}
          userId={userId}
          columns={columns}
          rows={pageRows}
          rowKey={(row) => (row.shipment_id ? `${row.id}:${row.shipment_id}` : row.id || row.order_number)}
          mode="server"
          sortBy={sortBy}
          sortDir={sortDir}
          filters={colFilters}
          onSortChange={(by, dir) => {
            setSortBy(by);
            setSortDir(dir);
            setPage(1);
          }}
          onFiltersChange={(next) => {
            setColFilters(next);
            setPage(1);
          }}
          emptyLabel="Tidak ada jadwal pengiriman."
          testId="delivery-schedule-table"
          hideTools
          resetLayoutRef={resetColumnsRef}
          enforceColMinWidth
          rowClassName={(r) => {
            const key = scheduleRowSelectionKey(r);
            return canOptimize && key && selected.has(key) ? "is-selected" : "";
          }}
          rowTestId={(r) => (r.shipment_id
            ? `ds-row-${r.order_number}-${r.shipment_id}`
            : `ds-row-${r.order_number}`)}
        />
      </div>

      <div className="dp-pager" data-testid="delivery-schedule-pager">
        <span className="muted" data-testid="ds-total-count">
          {totalFiltered} SJ
        </span>
        <div className="dp-pager-actions">
          <label className="muted">
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value) || 50);
                setPage(1);
              }}
              data-testid="ds-page-size"
              aria-label="Jumlah per halaman"
            >
              {DS_PAGE_SIZES.map((n) => (
                <option key={n} value={n}>{n} / halaman</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="ghost sm"
            disabled={safePage <= 1 || busy}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            data-testid="ds-page-prev"
          >
            Sebelumnya
          </button>
          <span className="muted" data-testid="ds-page-indicator">
            Halaman {safePage} / {totalPages}
          </span>
          <button
            type="button"
            className="ghost sm"
            disabled={safePage >= totalPages || busy}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            data-testid="ds-page-next"
          >
            Berikutnya
          </button>
        </div>
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
                <span>Cari PO / Customer</span>
                <input
                  type="search"
                  value={draftFilters.q || ""}
                  onChange={(e) => onDraftSearch(e.target.value)}
                  placeholder="Cari PO / customer…"
                  data-testid="ds-adv-q"
                />
              </label>
              <div className="ship-filter-range" data-testid="ds-adv-period-wrap">
                <DeliveryPlanDateRangeField
                  label="Tanggal Pengiriman"
                  testIdPrefix="ds-adv-period"
                  dateFrom={draftFilters.dateFrom}
                  dateTo={draftFilters.dateTo}
                  onApply={onDraftDateRange}
                />
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
              <button type="button" className="primary sm" onClick={applyDraftFilters} data-testid="ds-adv-apply">
                Terapkan Filter
              </button>
            </footer>
          </div>
        </div>
      )}

      {detailsOpen && (
        <div
          className="modal modal-viewport-safe-wrap"
          role="dialog"
          aria-modal="true"
          aria-labelledby="ds-rincian-title"
          data-testid="ds-rincian-modal"
          onClick={(e) => e.target === e.currentTarget && closeDetails()}
        >
          <div className="modal-inner modal-viewport-safe ds-rincian-modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <h3 id="ds-rincian-title">Rincian Pengiriman</h3>
              <button type="button" className="icon-button" onClick={closeDetails} aria-label="Tutup" data-testid="ds-rincian-close">
                <X size={16} />
              </button>
            </header>
            <div className="ds-rincian-summary" data-testid="ds-rincian-summary">
              <div>
                <span className="muted">Periode</span>
                <strong data-testid="ds-rincian-period">{detailsSummary.periodLabel}</strong>
              </div>
              <div>
                <span className="muted">Total SJ</span>
                <strong data-testid="ds-rincian-sj-count">{detailsSummary.sjCount}</strong>
              </div>
              <div>
                <span className="muted">Total Qty Dijadwalkan</span>
                <strong data-testid="ds-rincian-total-qty">{detailsSummary.grandTotalLabel}</strong>
              </div>
            </div>
            <div className="ds-rincian-body" data-testid="ds-rincian-body">
              {detailsSummary.empty ? (
                <p className="muted" data-testid="ds-rincian-empty">
                  Tidak ada surat jalan pada filter aktif. Total qty = 0.
                </p>
              ) : (
                detailsSummary.days.map((day) => (
                  <section key={day.date || "none"} className="ds-rincian-day" data-testid={`ds-rincian-day-${day.date || "none"}`}>
                    <header className="ds-rincian-day-head">
                      <strong>{day.dateLabel}</strong>
                      <span data-testid={`ds-rincian-day-total-${day.date || "none"}`}>{day.totalLabel}</span>
                    </header>
                    <div className="ds-rincian-table-wrap" data-testid="ds-rincian-table-wrap">
                      <table className="ds-rincian-table">
                        <thead>
                          <tr>
                            <th>Tanggal Pengiriman</th>
                            <th>Nomor SJ</th>
                            <th>Nomor PO</th>
                            <th>Customer</th>
                            <th className="num">Qty Pengiriman</th>
                            <th>Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {day.rows.map((r) => (
                            <tr key={r.key} data-testid={`ds-rincian-row-${r.key}`}>
                              <td>{r.date ? formatDate(r.date, { dateOnly: true }) : "—"}</td>
                              <td>{r.sjNumber}</td>
                              <td>{r.orderNumber}</td>
                              <td>{r.customerName}</td>
                              <td className="num">{r.qtyLabel}</td>
                              <td>{r.status}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </section>
                ))
              )}
            </div>
            <footer className="ds-rincian-foot" data-testid="ds-rincian-foot">
              <span className="muted">Grand Total</span>
              <strong data-testid="ds-rincian-grand-total">{detailsSummary.grandTotalLabel}</strong>
              <button type="button" className="ghost sm" onClick={closeDetails} data-testid="ds-rincian-tutup">
                Tutup
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
