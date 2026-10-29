import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowUp, RefreshCw, RotateCcw, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  CATEGORY_OPTIONS,
  categoryClass,
  formatIdDate,
  poLateTrackingPath,
} from "../lib/poLateTracking";
import {
  buildPoLateVisualFixturePayload,
  isPoLateVisualFixtureEnabled,
} from "../lib/poLateTrackingVisualFixture";
import "./PoLateTrackingPage.css";

const EMPTY = { q: "", category: "", branch_id: "" };
const CATEGORY_SET = new Set(["", "lewat_1_hari", "terlambat", "kritis"]);

function filtersFromParams(params) {
  const categoryRaw = String(params.get("category") || "").trim().toLowerCase();
  return {
    q: String(params.get("q") || "").trim(),
    category: CATEGORY_SET.has(categoryRaw) ? categoryRaw : "",
    branch_id: String(params.get("branch_id") || "").trim(),
  };
}

function writeParams(navigate, filters, { visualFixture = false } = {}) {
  navigate(poLateTrackingPath({
    late: true,
    category: filters.category,
    branchId: filters.branch_id,
    q: filters.q,
    visualFixture,
  }));
}

function fmtInt(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  return v.toLocaleString("id-ID");
}

function SortBtn({ label, field, sortBy, sortDir, onSort }) {
  const active = sortBy === field;
  return (
    <button
      type="button"
      className={`plt-sort${active ? " is-active" : ""}`}
      onClick={() => onSort(field, active && sortDir === "desc" ? "asc" : "desc")}
    >
      <span>{label}</span>
      {active ? (sortDir === "asc" ? <ArrowUp size={11} aria-hidden="true" /> : <ArrowDown size={11} aria-hidden="true" />) : null}
    </button>
  );
}

export default function PoLateTrackingPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const seedKey = searchParams.toString();
  const seeded = useMemo(() => filtersFromParams(searchParams), [seedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const [filters, setFilters] = useState(seeded);
  const [draft, setDraft] = useState(seeded);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sortBy, setSortBy] = useState("days_late");
  const [sortDir, setSortDir] = useState("desc");
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState({
    total: 0,
    filtered_total: 0,
    active_late_total: 0,
    total_pages: 1,
    active_late_counts: {},
    branch_options: [],
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    setFilters(seeded);
    setDraft(seeded);
    setPage(1);
  }, [seedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const visualFixture = isPoLateVisualFixtureEnabled(searchParams);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    setForbidden(false);
    try {
      const params = {
        page,
        page_size: pageSize,
        sort_by: sortBy || "days_late",
        sort_dir: sortDir || "desc",
      };
      if (filters.q.trim()) params.q = filters.q.trim();
      if (filters.category) params.category = filters.category;
      if (filters.branch_id) params.branch_id = filters.branch_id;
      const data = visualFixture
        ? buildPoLateVisualFixturePayload({
          page,
          pageSize,
          q: filters.q,
          category: filters.category,
          branchId: filters.branch_id,
          sortBy,
          sortDir,
        })
        : await api.poLateTracking(params);
      const pageNum = data.page || page;
      const size = data.page_size || pageSize;
      const base = (pageNum - 1) * size;
      setRows((data.rows || []).map((row, index) => ({ ...row, row_no: base + index + 1 })));
      setMeta({
        total: data.total || 0,
        filtered_total: data.filtered_total ?? data.total ?? 0,
        active_late_total: data.active_late_total ?? data.total ?? 0,
        total_pages: data.total_pages || 1,
        page: pageNum,
        page_size: size,
        active_late_counts: data.active_late_counts || {},
        counts: data.counts || {},
        branch_options: Array.isArray(data.branch_options) ? data.branch_options : [],
        visual_fixture: Boolean(data.visual_fixture),
      });
    } catch (e) {
      const status = e?.response?.status;
      const det = e?.response?.data?.detail;
      if (status === 403) {
        setForbidden(true);
        setErr("Anda tidak memiliki akses ke Tracking PO Terlambat Kirim.");
      } else {
        setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Tracking PO"));
      }
      setRows([]);
    } finally {
      setBusy(false);
    }
  }, [filters, page, pageSize, sortBy, sortDir, visualFixture]);

  useEffect(() => { load(); }, [load]);

  const applyFilters = (e) => {
    e?.preventDefault?.();
    const next = { ...draft, q: draft.q.trim() };
    setPage(1);
    setFilters(next);
    writeParams(navigate, next, { visualFixture });
  };

  const resetFilters = () => {
    setDraft(EMPTY);
    setFilters(EMPTY);
    setSortBy("days_late");
    setSortDir("desc");
    setPage(1);
    writeParams(navigate, EMPTY, { visualFixture });
  };

  const onSort = (field, dir) => {
    setSortBy(field);
    setSortDir(dir);
    setPage(1);
  };

  const extrasActive = Boolean(filters.q || filters.category || filters.branch_id);
  const counts = meta.active_late_counts || {};

  return (
    <div className="plt-page" data-testid="po-late-tracking-page">
      <header className="plt-head">
        <div>
          <p className="plt-kicker">Produksi · Monitoring</p>
          <h1>Tracking PO Terlambat Kirim</h1>
          <p className="plt-sub">
            PO aktif melewati tanggal pengiriman (satu PO = satu baris). Asia/Jakarta.
            {meta.visual_fixture ? " · Visual fixture (dev only, not production data)." : ""}
          </p>
        </div>
        <div className="plt-head-actions">
          <button type="button" className="plt-btn" data-testid="plt-refresh" onClick={load} disabled={busy}>
            <RefreshCw size={16} aria-hidden="true" /> Muat ulang
          </button>
        </div>
      </header>

      <section className="plt-summary" data-testid="plt-summary">
        <div className="plt-summary-total">
          <span>PO terlambat aktif</span>
          <strong data-testid="plt-active-total">{fmtInt(meta.active_late_total)}</strong>
        </div>
        <div className="plt-summary-cats">
          <span data-testid="plt-count-1">Lewat 1 Hari: {fmtInt(counts.lewat_1_hari)}</span>
          <span data-testid="plt-count-23">Terlambat: {fmtInt(counts.terlambat)}</span>
          <span data-testid="plt-count-4">Kritis: {fmtInt(counts.kritis)}</span>
        </div>
        {extrasActive ? (
          <p className="plt-filtered" data-testid="plt-filtered-total">
            Hasil filter saat ini: <strong>{fmtInt(meta.filtered_total)}</strong> PO
            {" "}(tidak mengubah total aktif di atas).
          </p>
        ) : null}
      </section>

      <form className="plt-filters" onSubmit={applyFilters} data-testid="plt-filters">
        <label className="plt-field plt-field-search">
          <span>Cari</span>
          <span className="plt-search">
            <Search size={14} aria-hidden="true" />
            <input
              data-testid="plt-search"
              value={draft.q}
              onChange={(e) => setDraft((prev) => ({ ...prev, q: e.target.value }))}
              placeholder="Nomor PO atau pelanggan"
            />
          </span>
        </label>
        <label className="plt-field">
          <span>Kategori</span>
          <select
            data-testid="plt-category"
            value={draft.category}
            onChange={(e) => setDraft((prev) => ({ ...prev, category: e.target.value }))}
          >
            {CATEGORY_OPTIONS.map((opt) => (
              <option key={opt.value || "all"} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>
        <label className="plt-field">
          <span>Cabang</span>
          <select
            data-testid="plt-branch"
            value={draft.branch_id}
            onChange={(e) => setDraft((prev) => ({ ...prev, branch_id: e.target.value }))}
          >
            <option value="">Semua cabang (scope)</option>
            {(meta.branch_options || []).map((opt) => (
              <option key={opt.id} value={opt.id}>{opt.name || opt.id}</option>
            ))}
          </select>
        </label>
        <div className="plt-filter-actions">
          <button type="submit" className="plt-btn is-primary" data-testid="plt-apply">Terapkan</button>
          <button type="button" className="plt-btn" data-testid="plt-reset" onClick={resetFilters}>
            <RotateCcw size={14} aria-hidden="true" /> Reset
          </button>
        </div>
      </form>

      {forbidden ? (
        <p className="plt-error" data-testid="plt-forbidden">{err}</p>
      ) : err ? (
        <p className="plt-error" data-testid="plt-error">{err}</p>
      ) : null}

      <div className="plt-table-wrap" data-testid="plt-table">
        <table className="plt-table">
          <thead>
            <tr>
              <th className="plt-col-no">No.</th>
              <th className="plt-col-po"><SortBtn label="No PO" field="po_number" sortBy={sortBy} sortDir={sortDir} onSort={onSort} /></th>
              <th><SortBtn label="Tgl PO Masuk" field="order_date" sortBy={sortBy} sortDir={sortDir} onSort={onSort} /></th>
              <th><SortBtn label="Nama Pelanggan" field="customer_name" sortBy={sortBy} sortDir={sortDir} onSort={onSort} /></th>
              <th>Status Saat Ini</th>
              <th><SortBtn label="Umur PO" field="umur_po_days" sortBy={sortBy} sortDir={sortDir} onSort={onSort} /></th>
              <th><SortBtn label="Estimasi Tgl Kirim" field="tanggal_pengiriman" sortBy={sortBy} sortDir={sortDir} onSort={onSort} /></th>
              <th><SortBtn label="Hari Terlambat" field="days_late" sortBy={sortBy} sortDir={sortDir} onSort={onSort} /></th>
              <th><SortBtn label="Kategori" field="category" sortBy={sortBy} sortDir={sortDir} onSort={onSort} /></th>
              <th><SortBtn label="Cabang" field="branch" sortBy={sortBy} sortDir={sortDir} onSort={onSort} /></th>
              <th><SortBtn label="Qty Sisa" field="qty_remaining" sortBy={sortBy} sortDir={sortDir} onSort={onSort} /></th>
            </tr>
          </thead>
          <tbody>
            {!rows.length ? (
              <tr>
                <td colSpan={11} className="plt-empty">
                  {busy ? "Memuat…" : "Tidak ada PO terlambat pada filter ini."}
                </td>
              </tr>
            ) : rows.map((row) => (
              <tr
                key={row.id || row.order_id}
                className={categoryClass(row.category)}
                data-testid={`plt-row-${row.po_number || row.id}`}
                onClick={() => {
                  const id = row.id || row.order_id;
                  if (id) navigate(`/po/${encodeURIComponent(id)}`);
                }}
              >
                <td className="plt-col-no">{row.row_no}</td>
                <td className="plt-col-po">
                  <Link
                    className="plt-link-po"
                    to={`/po/${encodeURIComponent(row.id || row.order_id)}`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    {row.po_number || "—"}
                  </Link>
                </td>
                <td>{formatIdDate(row.order_date)}</td>
                <td>{row.customer_name || "—"}</td>
                <td>{row.status_saat_ini || "—"}</td>
                <td>{row.umur_po_days == null ? "—" : `${fmtInt(row.umur_po_days)} hr`}</td>
                <td>{formatIdDate(row.tanggal_pengiriman)}</td>
                <td>{row.days_late == null ? "—" : fmtInt(row.days_late)}</td>
                <td><span className={`plt-pill ${categoryClass(row.category)}`}>{row.category_label || "—"}</span></td>
                <td>{row.branch || "—"}</td>
                <td>{fmtInt(row.qty_remaining)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <footer className="plt-pager" data-testid="plt-pager">
        <span>
          Halaman {meta.page || page} / {meta.total_pages || 1}
          {" · "}
          {fmtInt(meta.filtered_total)} baris
          {user?.email ? ` · ${user.email}` : ""}
        </span>
        <div className="plt-pager-actions">
          <button
            type="button"
            className="plt-btn"
            disabled={busy || (meta.page || page) <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Sebelumnya
          </button>
          <button
            type="button"
            className="plt-btn"
            disabled={busy || (meta.page || page) >= (meta.total_pages || 1)}
            onClick={() => setPage((p) => p + 1)}
          >
            Berikutnya
          </button>
          <select
            aria-label="Ukuran halaman"
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value) || 50);
              setPage(1);
            }}
          >
            {[25, 50, 100].map((n) => <option key={n} value={n}>{n}/halaman</option>)}
          </select>
        </div>
      </footer>
    </div>
  );
}
