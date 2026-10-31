import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, Columns3, RefreshCw, RotateCcw, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import DeliveryPlanAdvancedTable from "../components/deliveryPlan/DeliveryPlanAdvancedTable";
import SalesDashboardDateInput from "../components/widgetDashboard/SalesDashboardDateInput";
import {
  buildKpiCards,
  formatCellValue,
  formatIdDateTime,
  formatSalesDashboardRange,
  isIdentityKey,
  periodLabelId,
  presentationKind,
} from "../lib/dashboardRincianPresentation";
import "./DashboardRincianPage.css";

const DASH_BACK = {
  produksi: "/dashboard",
  penjualan: "/dashboard/penjualan",
  keuangan: "/dashboard/keuangan",
};

export default function DashboardRincianPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const dashboard = (params.get("dashboard") || "produksi").toLowerCase();
  const widget = params.get("widget") || "";
  const startDate = params.get("start_date") || "";
  const endDate = params.get("end_date") || "";
  const unit = params.get("unit") || "";
  const status = params.get("status") || "";
  const basis = params.get("basis") || "";
  const cashBankId = params.get("cash_bank_id") || "";
  const page = Math.max(1, Number(params.get("page") || 1) || 1);
  const pageSize = Math.min(200, Math.max(1, Number(params.get("page_size") || 50) || 50));
  const [q, setQ] = useState(() => params.get("q") || "");
  const [draftQ, setDraftQ] = useState(() => params.get("q") || "");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [forbidden, setForbidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sortBy, setSortBy] = useState("");
  const [sortDir, setSortDir] = useState("asc");
  const [filters, setFilters] = useState({});
  const [colOpen, setColOpen] = useState(false);
  const [hiddenCols, setHiddenCols] = useState(() => new Set());

  const backPath = DASH_BACK[dashboard] || "/dashboard";

  const load = useCallback(async () => {
    if (!widget) {
      setErr("Widget rincian tidak ditentukan");
      setData(null);
      return;
    }
    setBusy(true);
    setErr("");
    setForbidden(false);
    try {
      const payload = await api.dashboardRincian({
        dashboard,
        widget,
        q: q || undefined,
        page,
        page_size: pageSize,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        unit: unit || undefined,
        status: status || undefined,
        basis: basis || undefined,
        cash_bank_id: cashBankId || undefined,
      });
      setData(payload);
    } catch (e) {
      const statusCode = e?.response?.status;
      const detail = e?.response?.data?.detail || e?.message || "Gagal memuat rincian";
      if (statusCode === 403) {
        setForbidden(true);
        setErr(typeof detail === "string" ? detail : "Tidak ada izin");
      } else {
        setErr(typeof detail === "string" ? detail : "Gagal memuat rincian");
      }
      setData(null);
    } finally {
      setBusy(false);
    }
  }, [widget, dashboard, q, page, pageSize, startDate, endDate, unit, status, basis, cashBankId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    setDraftQ(q);
  }, [q]);

  const patchParams = (patch, { replace = true } = {}) => {
    const next = new URLSearchParams(params);
    Object.entries(patch).forEach(([key, value]) => {
      if (value == null || value === "") next.delete(key);
      else next.set(key, String(value));
    });
    setParams(next, { replace });
  };

  const applySearch = (event) => {
    event?.preventDefault?.();
    const nextQ = draftQ.trim();
    setQ(nextQ);
    patchParams({ q: nextQ, page: "1" });
  };

  const resetFilters = () => {
    setDraftQ("");
    setQ("");
    setFilters({});
    setSortBy("");
    setSortDir("asc");
    patchParams({ q: "", page: "1" });
  };

  const columns = useMemo(() => {
    const defs = (data?.columns || []).map((col) => {
      const key = col.key;
      const kind = presentationKind(col);
      const identity = isIdentityKey(key);
      const numeric = kind === "currency" || kind === "quantity" || kind === "number";
      return {
        key,
        label: col.label,
        type: col.type || "text",
        align: numeric ? "right" : "left",
        sortable: key !== "row_no",
        filterable: key !== "row_no",
        minWidth: key === "row_no" ? 48 : undefined,
        render: (raw, row) => {
          if (identity && row?.detail_href) {
            return (
              <button
                type="button"
                className="dr-id-link"
                data-testid={`dr-row-id-${row.row_no || key}`}
                onClick={(e) => {
                  e.stopPropagation();
                  navigate(row.detail_href);
                }}
              >
                {raw == null || raw === "" ? "—" : String(raw)}
              </button>
            );
          }
          const unitHint = kind === "quantity"
            ? (row?.qty_unit || row?.unit || data?.kpi_summary?.unit || unit || "")
            : "";
          return formatCellValue(kind, raw, { unit: unitHint });
        },
      };
    }).filter((col) => !hiddenCols.has(col.key));
    return defs;
  }, [data, hiddenCols, navigate, unit]);

  const allColumnKeys = data?.columns || [];
  const kpiCards = useMemo(
    () => buildKpiCards(widget, data?.kpi_summary || {}, data?.total),
    [widget, data],
  );
  const totalPages = data?.total_pages || 1;

  const openDetail = (row) => {
    if (row?.detail_href) navigate(row.detail_href);
  };

  if (!widget) {
    return (
      <div className="dr-page" data-testid="dashboard-rincian-page">
        <header className="dr-head">
          <Link className="dr-back" to={backPath} data-testid="dr-back">
            <ArrowLeft size={16} aria-hidden /> Kembali
          </Link>
          <h1>Rincian Dashboard</h1>
        </header>
        <p className="dr-error" data-testid="dr-error">Parameter widget wajib diisi.</p>
      </div>
    );
  }

  return (
    <div className="dr-page" data-testid="dashboard-rincian-page">
      <header className="dr-head">
        <div className="dr-head-main">
          <Link className="dr-back" to={backPath} data-testid="dr-back">
            <ArrowLeft size={16} aria-hidden /> Kembali ke dashboard
          </Link>
          <div className="dr-titles">
            <h1 data-testid="dr-title">Rincian Dashboard</h1>
            <p className="dr-card-name" data-testid="dr-card-name">{data?.title || widget}</p>
          </div>
        </div>
        <div className="dr-meta" data-testid="dr-meta">
          <span data-testid="dr-period">
            {dashboard === "penjualan"
              ? (formatSalesDashboardRange(data?.period?.start_date, data?.period?.end_date) || periodLabelId(data?.period))
              : periodLabelId(data?.period)}
          </span>
          <span data-testid="dr-generated">
            {data?.generated_at ? `Pembaruan ${formatIdDateTime(data.generated_at)}` : "—"}
          </span>
          <span data-testid="dr-dashboard-label">{dashboard}</span>
        </div>
      </header>

      <section className="dr-summary" data-testid="dr-summary">
        <section className="dr-kpi" data-testid="dr-kpi-summary">
          {kpiCards.map((card) => (
            <div key={card.key} data-kind={card.kind}>
              <span>{card.label}</span>
              <strong data-testid={card.testId}>{card.value}</strong>
            </div>
          ))}
        </section>
        {data?.kpi_summary?.detail ? (
          <p className="dr-kpi-detail" data-testid="dr-kpi-detail" role="status">{data.kpi_summary.detail}</p>
        ) : null}
      </section>

      <section className="dr-toolbar" data-drill-ignore="true">
        {dashboard === "penjualan" ? (
          <div className="dr-period-filter">
            <SalesDashboardDateInput
              ariaLabel="Tanggal mulai"
              testId="dr-period-start"
              valueIso={startDate}
              onChange={(iso) => patchParams({ start_date: iso, page: "1" })}
            />
            <SalesDashboardDateInput
              ariaLabel="Tanggal akhir"
              testId="dr-period-end"
              valueIso={endDate}
              onChange={(iso) => patchParams({ end_date: iso, page: "1" })}
            />
          </div>
        ) : null}
        <form className="dr-search" onSubmit={applySearch}>
          <Search size={14} aria-hidden />
          <input
            value={draftQ}
            onChange={(e) => setDraftQ(e.target.value)}
            placeholder="Cari nomor / nama…"
            data-testid="dr-search"
          />
          <button type="submit" className="ghost sm" data-testid="dr-search-apply">Cari</button>
        </form>
        <div className="dr-toolbar-actions">
          <button type="button" className="ghost sm" onClick={resetFilters} data-testid="dr-reset">
            <RotateCcw size={14} aria-hidden /> Reset
          </button>
          <button type="button" className="ghost sm" onClick={() => setColOpen((v) => !v)} data-testid="dr-cols">
            <Columns3 size={14} aria-hidden /> Kolom
          </button>
          <button type="button" className="ghost sm" onClick={load} disabled={busy} data-testid="dr-refresh">
            <RefreshCw size={14} aria-hidden /> Muat ulang
          </button>
        </div>
        {colOpen ? (
          <div className="dr-col-pop" data-testid="dr-col-pop">
            {allColumnKeys.map((col) => (
              <label key={col.key}>
                <input
                  type="checkbox"
                  checked={!hiddenCols.has(col.key)}
                  disabled={col.key === "row_no"}
                  onChange={() => {
                    setHiddenCols((prev) => {
                      const next = new Set(prev);
                      if (next.has(col.key)) next.delete(col.key);
                      else next.add(col.key);
                      return next;
                    });
                  }}
                />
                {col.label}
              </label>
            ))}
          </div>
        ) : null}
      </section>

      <section className="dr-table-wrap" data-testid="dr-table-wrap">
        {busy && !data ? <p className="dr-loading" data-testid="dr-loading">Memuat rincian…</p> : null}
        {forbidden ? <p className="dr-error" data-testid="dr-forbidden">{err || "Tidak ada izin"}</p> : null}
        {!forbidden && err ? <p className="dr-error" data-testid="dr-error">{err}</p> : null}
        {!forbidden && !err ? (
          <DeliveryPlanAdvancedTable
            tableId={`dashboard-rincian-${widget}`}
            userId={user?.id || user?.email || "anon"}
            columns={columns}
            rows={data?.rows || []}
            rowKey={(row) => `${row.id || row.po_number || row.invoice_number || ""}-${row.row_no}`}
            mode="client"
            sortBy={sortBy}
            sortDir={sortDir}
            filters={filters}
            onSortChange={(key, dir) => {
              setSortBy(key);
              setSortDir(dir);
            }}
            onFiltersChange={setFilters}
            onRowClick={openDetail}
            emptyLabel="Tidak ada baris untuk filter ini"
            testId="dr-spreadsheet"
            rowTestId={(row) => `dr-row-${row.row_no}`}
          />
        ) : null}
      </section>

      <footer className="dr-pager" data-testid="dr-pager">
        <button
          type="button"
          className="ghost sm"
          disabled={!data?.has_prev || busy}
          onClick={() => patchParams({ page: String(page - 1) })}
          data-testid="dr-prev"
        >
          Sebelumnya
        </button>
        <span data-testid="dr-page-label">
          Halaman {data?.page || page} / {totalPages}
        </span>
        <button
          type="button"
          className="ghost sm"
          disabled={!data?.has_next || busy}
          onClick={() => patchParams({ page: String(page + 1) })}
          data-testid="dr-next"
        >
          Berikutnya
        </button>
        <span className="dr-readonly" data-testid="dr-readonly">Hanya baca · tanpa edit sel</span>
      </footer>
    </div>
  );
}
