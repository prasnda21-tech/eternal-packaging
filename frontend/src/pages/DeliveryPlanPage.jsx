import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { RefreshCw, RotateCcw, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { legacyEstimateNotice } from "../lib/legacyEstimateNotice";
import DeliveryPlanAdvancedTable from "../components/deliveryPlan/DeliveryPlanAdvancedTable";
import DeliveryPlanDateRangeField from "../components/deliveryPlan/DeliveryPlanDateRangeField";
import { encodeHeaderFilters } from "../components/deliveryPlan/deliveryPlanTableLayout";
import PoIdentityBadges from "../components/PoIdentityBadges";
import "./DeliveryPlanPage.css";

const EMPTY_FILTERS = {
  q: "",
  source: "",
  status: "",
  date_from: "",
  date_to: "",
  unit: "",
};

const STATUS_VALUES = new Set(["", "hari_ini", "terlambat", "terjadwal"]);

function filtersFromSearchParams(params) {
  const statusRaw = String(params.get("status") || "").trim().toLowerCase();
  const status = STATUS_VALUES.has(statusRaw) ? statusRaw : "";
  const dateFrom = String(params.get("date_from") || "").trim();
  const dateTo = String(params.get("date_to") || "").trim();
  const unit = String(params.get("unit") || "").trim();
  const ymd = /^\d{4}-\d{2}-\d{2}$/;
  return {
    ...EMPTY_FILTERS,
    status,
    date_from: ymd.test(dateFrom) ? dateFrom : "",
    date_to: ymd.test(dateTo) ? dateTo : "",
    unit,
  };
}

const STATUS_OPTIONS = [
  { value: "", label: "Semua status" },
  { value: "hari_ini", label: "Hari Ini" },
  { value: "terlambat", label: "Terlambat" },
  { value: "terjadwal", label: "Akan Datang" },
];

const SOURCE_OPTIONS = [
  { value: "", label: "Semua sumber" },
  { value: "manual", label: "Manual" },
  { value: "automatic", label: "Otomatis" },
];

function sourceLabel(source, row) {
  if (source === "manual") return "Manual";
  if (row?.delivery_sla_effective?.contract === "legacy_working_days"
    || row?.delivery_sla_effective?.contract === "legacy_calendar") {
    return "Otomatis (Legacy · Hari Kerja)";
  }
  if (source === "automatic") return "Otomatis";
  return source || "—";
}

/** Plan-page display only — backend code stays terjadwal / late-status untouched. */
function statusDisplay(code, label) {
  if (code === "terjadwal") return "Akan Datang";
  if (code === "hari_ini") return "Hari Ini";
  if (code === "terlambat") return "Terlambat";
  return label || code || "—";
}

function statusClass(status) {
  if (status === "hari_ini") return "is-today";
  if (status === "terlambat") return "is-late";
  if (status === "terjadwal") return "is-upcoming";
  return "";
}

function fmtQty(n) {
  const v = Number(n) || 0;
  return v.toLocaleString("id-ID");
}

function cellOrDash(value) {
  const text = String(value || "").trim();
  return text || "—";
}

export default function DeliveryPlanPage() {
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const userId = user?.id || user?.email || "anon";
  const seedKey = searchParams.toString();
  const seeded = useMemo(() => filtersFromSearchParams(searchParams), [seedKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const [filters, setFilters] = useState(seeded);
  const [draft, setDraft] = useState(seeded);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sortBy, setSortBy] = useState("");
  const [sortDir, setSortDir] = useState("asc");
  const [colFilters, setColFilters] = useState({});
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState({ total: 0, total_pages: 1, counts: {}, totals: null });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    setFilters(seeded);
    setDraft(seeded);
    setPage(1);
  }, [seedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const params = {
        page,
        page_size: pageSize,
      };
      if (filters.q.trim()) params.q = filters.q.trim();
      if (filters.source) params.source = filters.source;
      if (filters.status) params.status = filters.status;
      if (filters.date_from) params.date_from = filters.date_from;
      if (filters.date_to) params.date_to = filters.date_to;
      if (filters.unit) params.unit = filters.unit;
      if (sortBy) {
        params.sort_by = sortBy;
        params.sort_dir = sortDir || "asc";
      }
      const hf = encodeHeaderFilters(colFilters);
      if (hf.length) params.hf = hf;
      const data = await api.deliveryPlan(params);
      setRows(data.rows || []);
      setMeta({
        total: data.total || 0,
        total_pages: data.total_pages || 1,
        page: data.page || page,
        page_size: data.page_size || pageSize,
        counts: data.counts || {},
        totals: data.totals || null,
      });
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Rencana Jadwal Kirim"));
      setRows([]);
    } finally {
      setBusy(false);
    }
  }, [filters, page, pageSize, sortBy, sortDir, colFilters]);

  useEffect(() => {
    load();
  }, [load]);

  const applyFilters = (e) => {
    e?.preventDefault?.();
    setPage(1);
    setFilters({ ...draft });
  };

  const resetFilters = useCallback(() => {
    setDraft(EMPTY_FILTERS);
    setFilters(EMPTY_FILTERS);
    setColFilters({});
    setSortBy("");
    setSortDir("asc");
    setPage(1);
  }, []);

  const totalPages = meta.total_pages || 1;
  const totals = meta.totals;
  const byStatus = meta.counts?.by_status || {};

  const columns = useMemo(() => [
    {
      key: "tanggal_pengiriman",
      label: "Tanggal Kirim",
      type: "date",
      minWidth: 110,
      render: (v, row) => {
        const notice = legacyEstimateNotice(row?.delivery_sla_effective);
        return (
          <span className="dp-date-cell">
            <span>{formatDate(v, { dateOnly: true })}</span>
            {notice ? (
              <span
                className="dp-estimate-notice"
                data-testid={`dp-estimate-notice-${row.po_number}`}
                title={notice.title}
              >
                {notice.text}
              </span>
            ) : null}
          </span>
        );
      },
    },
    {
      key: "po_number",
      label: "Nomor PO",
      type: "text",
      minWidth: 200,
      render: (v, row) => (
        <span className="dp-po-identity">
          <Link className="dp-link-po" to={`/po/${encodeURIComponent(row.id || row.order_id)}`}>
            {v || "—"}
          </Link>
          <PoIdentityBadges order={row} testIdPrefix={`dp-po-${row.po_number || row.id}`} />
        </span>
      ),
    },
    { key: "customer_name", label: "Customer", type: "text", minWidth: 140 },
    { key: "branch", label: "Cabang", type: "text", minWidth: 100 },
    {
      key: "delivery_zone",
      label: "Zona",
      type: "text",
      minWidth: 88,
      maxWidth: 140,
      render: (v) => (
        <span className="dp-zone-cell" title={String(v || "").trim() || undefined}>
          {cellOrDash(v)}
        </span>
      ),
    },
    {
      key: "notes",
      label: "Keterangan",
      type: "text",
      minWidth: 140,
      maxWidth: 220,
      render: (v) => {
        const text = String(v || "").trim();
        return (
          <span
            className="dp-notes-cell"
            title={text || undefined}
            data-testid="dp-notes-cell"
          >
            {text || "—"}
          </span>
        );
      },
    },
    {
      key: "tanggal_pengiriman_source",
      label: "Sumber Tanggal",
      type: "enum",
      minWidth: 136,
      filterOptions: [
        { value: "manual", label: "Manual" },
        { value: "automatic", label: "Otomatis" },
      ],
      render: (v, row) => (
        <span
          className={`dp-badge ${v === "manual" ? "source-manual" : "source-auto"}`}
          data-testid={`dp-source-${row.po_number}`}
          title={row?.delivery_sla_effective?.contract === "legacy_working_days"
            || row?.delivery_sla_effective?.contract === "legacy_calendar"
            ? "Estimasi hari kerja Legacy. Belum SLA terkunci."
            : sourceLabel(v, row)}
        >
          {sourceLabel(v, row)}
        </span>
      ),
    },
    {
      key: "qty_total",
      label: "Qty Pesanan",
      type: "number",
      align: "right",
      minWidth: 88,
      render: (v) => fmtQty(v),
    },
    {
      key: "qty_delivered",
      label: "Qty Terkirim",
      type: "number",
      align: "right",
      minWidth: 88,
      render: (v) => fmtQty(v),
    },
    {
      key: "qty_remaining",
      label: "Sisa Qty",
      type: "number",
      align: "right",
      minWidth: 80,
      render: (v, row) => (
        <span data-testid={`dp-remaining-${row.po_number}`}>{fmtQty(v)}</span>
      ),
    },
    {
      key: "delivery_schedule_status",
      label: "Status",
      type: "enum",
      minWidth: 100,
      filterOptions: [
        { value: "hari_ini", label: "Hari Ini" },
        { value: "terlambat", label: "Terlambat" },
        { value: "terjadwal", label: "Akan Datang" },
      ],
      render: (v, row) => (
        <span
          className={`dp-badge ${statusClass(v)}`}
          data-testid={`dp-status-${row.po_number}`}
        >
          {statusDisplay(v, row.delivery_schedule_status_label)}
        </span>
      ),
    },
    {
      key: "aksi",
      label: "Aksi",
      type: "text",
      sortable: false,
      filterable: false,
      minWidth: 88,
      render: (_v, row) => (
        <Link
          className="dp-btn-view"
          to={`/po/${encodeURIComponent(row.id || row.order_id)}`}
          data-testid={`dp-open-po-${row.po_number}`}
        >
          Lihat PO
        </Link>
      ),
    },
  ], []);

  return (
    <div className="dp-page" data-testid="delivery-plan-page">
      <header className="dp-header">
        <div>
          <p className="eyebrow">Produksi · Pengiriman</p>
          <h1>Rencana Jadwal Kirim</h1>
          <p className="subhead">
            Daftar PO dengan tanggal pengiriman valid, manual atau otomatis.
          </p>
          {filters.unit ? (
            <p className="muted small" data-testid="dp-unit-filter-note">
              Filter satuan dari Qty Rencana Pengiriman: <strong>{filters.unit}</strong>
              {" "}(hanya sisa qty &gt; 0, selaras kartu dashboard).
            </p>
          ) : null}
        </div>
        <div className="dp-header-actions">
          <button
            type="button"
            className="ghost sm"
            onClick={load}
            disabled={busy}
            data-testid="dp-refresh"
            aria-label="Muat ulang"
          >
            <RefreshCw size={14} aria-hidden="true" />
          </button>
        </div>
      </header>

      <form className="dp-filters" data-testid="delivery-plan-filters" onSubmit={applyFilters}>
        <div className="dp-search">
          <Search size={14} aria-hidden />
          <input
            id="dp-filter-q"
            type="search"
            value={draft.q}
            onChange={(e) => setDraft((p) => ({ ...p, q: e.target.value }))}
            placeholder="Cari PO / customer…"
            data-testid="dp-filter-q"
            aria-label="Cari nomor PO atau customer"
          />
        </div>
        <DeliveryPlanDateRangeField
          dateFrom={filters.date_from}
          dateTo={filters.date_to}
          onApply={(from, to) => {
            setDraft((p) => ({ ...p, date_from: from, date_to: to }));
            setPage(1);
            setFilters((p) => ({
              ...p,
              date_from: from,
              date_to: to,
              q: draft.q,
              source: draft.source,
              status: draft.status,
            }));
          }}
        />
        <select
          value={draft.source}
          onChange={(e) => setDraft((p) => ({ ...p, source: e.target.value }))}
          data-testid="dp-filter-source"
          aria-label="Sumber tanggal"
        >
          {SOURCE_OPTIONS.map((o) => (
            <option key={o.value || "all"} value={o.value}>{o.label}</option>
          ))}
        </select>
        <select
          value={draft.status}
          onChange={(e) => setDraft((p) => ({ ...p, status: e.target.value }))}
          data-testid="dp-filter-status"
          aria-label="Status pengiriman"
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value || "all"} value={o.value}>{o.label}</option>
          ))}
        </select>
        <div className="dp-filter-actions">
          <button type="submit" className="dp-btn-primary" data-testid="dp-filter-apply">Terapkan</button>
          <button type="button" className="ghost sm" onClick={resetFilters} data-testid="dp-filter-reset">
            <RotateCcw size={12} aria-hidden /> Reset
          </button>
        </div>
      </form>

      {totals ? (
        <div className="dp-kpi-row" data-testid="delivery-plan-kpi">
          <div className="dp-kpi">
            <span className="label">Total PO</span>
            <span className="value" data-testid="dp-kpi-total-po">{fmtQty(totals.total_po)}</span>
          </div>
          <div className="dp-kpi">
            <span className="label">Qty Pesanan</span>
            <span className="value" data-testid="dp-kpi-qty-total">{fmtQty(totals.qty_total)}</span>
          </div>
          <div className="dp-kpi">
            <span className="label">Qty Terkirim</span>
            <span className="value" data-testid="dp-kpi-qty-delivered">{fmtQty(totals.qty_delivered)}</span>
          </div>
          <div className="dp-kpi">
            <span className="label">Sisa Qty</span>
            <span className="value" data-testid="dp-kpi-qty-remaining">{fmtQty(totals.qty_remaining)}</span>
          </div>
        </div>
      ) : null}

      {meta.counts?.by_status ? (
        <div className="dp-status-legend" data-testid="delivery-plan-legend">
          <span><i className="dp-dot today" /> Hari Ini {byStatus.hari_ini || 0}</span>
          <span><i className="dp-dot late" /> Terlambat {byStatus.terlambat || 0}</span>
          <span><i className="dp-dot upcoming" /> Akan Datang {byStatus.terjadwal || 0}</span>
        </div>
      ) : null}

      {err ? <p className="error" data-testid="delivery-plan-error">{err}</p> : null}
      {busy ? <p className="muted" data-testid="delivery-plan-loading">Memuat…</p> : null}

      <div data-testid="delivery-plan-table-wrap">
        <DeliveryPlanAdvancedTable
          tableId="delivery-plan"
          userId={userId}
          columns={columns}
          rows={rows}
          rowKey={(row) => row.id || row.order_id || row.po_number}
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
          emptyLabel="Tidak ada PO dengan tanggal pengiriman pada filter ini."
          testId="delivery-plan-table"
        />
      </div>

      <div className="dp-pager" data-testid="delivery-plan-pager">
        <span className="muted" data-testid="delivery-plan-total">
          {meta.total || 0} PO
        </span>
        <div className="dp-pager-actions">
          <label className="muted">
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value) || 50);
                setPage(1);
              }}
              data-testid="dp-page-size"
              aria-label="Jumlah per halaman"
            >
              {[25, 50, 100].map((n) => (
                <option key={n} value={n}>{n} / halaman</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="ghost sm"
            disabled={page <= 1 || busy}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            data-testid="dp-page-prev"
          >
            Sebelumnya
          </button>
          <span className="muted" data-testid="dp-page-indicator">
            Halaman {meta.page || page} / {totalPages}
          </span>
          <button
            type="button"
            className="ghost sm"
            disabled={page >= totalPages || busy}
            onClick={() => setPage((p) => p + 1)}
            data-testid="dp-page-next"
          >
            Berikutnya
          </button>
        </div>
      </div>
    </div>
  );
}
