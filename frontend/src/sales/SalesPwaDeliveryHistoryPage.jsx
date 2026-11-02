import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, Truck, X } from "lucide-react";
import api from "../lib/api";
import DeliveryPlanDateRangeField from "../components/deliveryPlan/DeliveryPlanDateRangeField";
import {
  HISTORY_PAGE_SIZE_OPTIONS,
  HISTORY_SEARCH_DEBOUNCE_MS,
  defaultHistoryFilters,
  formatHistoryDay,
  formatHistoryQty,
  historyActiveDatePreset,
  historyDatePresetRange,
  historyFiltersActive,
  historyRequestParams,
} from "../lib/salesPwaDeliveryHistory";

const TEXT_FILTER_KEYS = ["customer_q", "po_q", "sj_q", "item_q"];
const PRESETS = [
  { id: "today", label: "Hari Ini" },
  { id: "7d", label: "7 Hari" },
  { id: "30d", label: "30 Hari" },
  { id: "month", label: "Bulan Ini" },
];

function ClearableInput({ value, onChange, onClear, placeholder, ariaLabel, testId }) {
  return (
    <label className="spwa-hist-field">
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
        data-testid={testId}
      />
      {value ? (
        <button type="button" className="spwa-hist-clear" aria-label={`Hapus ${ariaLabel}`} onClick={onClear}>
          <X size={12} />
        </button>
      ) : null}
    </label>
  );
}

function HistoryDetailDrawer({ row, onClose }) {
  const [thumbs, setThumbs] = useState([]);

  useEffect(() => {
    let cancelled = false;
    const proofs = Array.isArray(row?.proofs) ? row.proofs.filter((p) => p?.available && p.id) : [];
    if (!row?.order_id || !proofs.length) {
      setThumbs([]);
      return undefined;
    }
    Promise.all(
      proofs.slice(0, 6).map(async (proof) => {
        try {
          const file = await api.salesPwaDocumentFile(row.order_id, "proof", proof.id);
          const url = URL.createObjectURL(file.blob);
          return { id: proof.id, url };
        } catch {
          return null;
        }
      }),
    ).then((items) => {
      if (cancelled) {
        items.forEach((item) => item?.url && URL.revokeObjectURL(item.url));
        return;
      }
      setThumbs(items.filter(Boolean));
    });
    return () => {
      cancelled = true;
      setThumbs((current) => {
        current.forEach((item) => item?.url && URL.revokeObjectURL(item.url));
        return [];
      });
    };
  }, [row]);

  if (!row) return null;
  return (
    <div className="spwa-drawer-backdrop" role="presentation" onClick={onClose} data-testid="sales-hist-detail-backdrop">
      <aside
        className="spwa-drawer spwa-hist-detail"
        role="dialog"
        aria-modal="true"
        aria-label="Detail Pengiriman"
        data-testid="sales-hist-detail"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="spwa-drawer-head">
          <strong><Truck size={16} aria-hidden /> Detail Pengiriman</strong>
          <button type="button" aria-label="Tutup" onClick={onClose} data-testid="sales-hist-detail-close"><X size={20} /></button>
        </div>
        <div className="spwa-hist-detail-grid">
          <div><span>Tanggal Pengiriman</span><strong>{formatHistoryDay(row.confirmed_day || row.confirmed_at)}</strong></div>
          <div><span>No. Surat Jalan</span><strong>{row.sj_number || "—"}</strong></div>
          <div><span>Customer</span><strong>{row.customer || "—"}{row.outlet ? ` · ${row.outlet}` : ""}</strong></div>
          <div><span>Nama Barang</span><strong>{row.item_name || "—"}</strong></div>
          <div><span>No. PO</span><strong>{row.po_number || "—"}</strong></div>
          <div><span>Qty Terkirim</span><strong>{formatHistoryQty(row.qty, row.unit)}</strong></div>
          <div><span>Status</span><strong className="spwa-hist-status">{row.status_label || row.status || "Terkirim"}</strong></div>
          {row.recipient_name ? <div><span>Penerima</span><strong>{row.recipient_name}</strong></div> : null}
        </div>
        <div className="spwa-hist-proofs" data-testid="sales-hist-proofs">
          <p className="spwa-kicker">BUKTI PENERIMAAN</p>
          {!thumbs.length && <p className="spwa-muted">Bukti foto tidak tersedia pada baris ini.</p>}
          {thumbs.length > 0 && (
            <div className="spwa-hist-proof-grid">
              {thumbs.map((thumb) => (
                <a key={thumb.id} href={thumb.url} target="_blank" rel="noreferrer" data-testid={`sales-hist-proof-${thumb.id}`}>
                  <img src={thumb.url} alt="Bukti pengiriman" />
                </a>
              ))}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

function ColumnFilters({ draft, setDraftField, variant }) {
  const prefix = variant === "mobile" ? "mobile-" : "";
  return (
    <>
      <ClearableInput
        value={draft.customer_q}
        onChange={(v) => setDraftField("customer_q", v)}
        onClear={() => setDraftField("customer_q", "")}
        placeholder="Cari customer..."
        ariaLabel="Filter customer"
        testId={`sales-hist-filter-${prefix}customer`}
      />
      <ClearableInput
        value={draft.po_q}
        onChange={(v) => setDraftField("po_q", v)}
        onClear={() => setDraftField("po_q", "")}
        placeholder="Cari PO..."
        ariaLabel="Filter nomor PO"
        testId={`sales-hist-filter-${prefix}po`}
      />
      <ClearableInput
        value={draft.sj_q}
        onChange={(v) => setDraftField("sj_q", v)}
        onClear={() => setDraftField("sj_q", "")}
        placeholder="Cari SJ..."
        ariaLabel="Filter nomor SJ"
        testId={`sales-hist-filter-${prefix}sj`}
      />
      <ClearableInput
        value={draft.item_q}
        onChange={(v) => setDraftField("item_q", v)}
        onClear={() => setDraftField("item_q", "")}
        placeholder="Cari barang..."
        ariaLabel="Filter nama barang"
        testId={`sales-hist-filter-${prefix}item`}
      />
      <div className="spwa-hist-qty-filter">
        <input
          inputMode="numeric"
          aria-label="Qty minimum"
          placeholder="Min"
          value={draft.qty_min}
          onChange={(e) => setDraftField("qty_min", e.target.value.replace(/[^\d]/g, ""))}
          data-testid={`sales-hist-filter-${prefix}qty-min`}
        />
        <span>–</span>
        <input
          inputMode="numeric"
          aria-label="Qty maksimum"
          placeholder="Max"
          value={draft.qty_max}
          onChange={(e) => setDraftField("qty_max", e.target.value.replace(/[^\d]/g, ""))}
          data-testid={`sales-hist-filter-${prefix}qty-max`}
        />
      </div>
      <select
        aria-label="Filter status"
        value={draft.status}
        onChange={(e) => setDraftField("status", e.target.value)}
        data-testid={`sales-hist-filter-${prefix}status`}
      >
        <option value="">Semua Status</option>
        <option value="Terkirim">Terkirim</option>
      </select>
    </>
  );
}

export default function SalesPwaDeliveryHistoryPage() {
  const [draft, setDraft] = useState(() => defaultHistoryFilters());
  const [filters, setFilters] = useState(() => defaultHistoryFilters());
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(0);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState(null);
  const requestGen = useRef(0);

  const setDraftField = (key, value) => setDraft((current) => ({ ...current, [key]: value }));
  const activePreset = historyActiveDatePreset(draft);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setFilters((current) => {
        const next = { ...current };
        let changed = false;
        TEXT_FILTER_KEYS.forEach((key) => {
          const value = String(draft[key] || "").trim();
          if (String(current[key] || "") !== value) {
            next[key] = value;
            changed = true;
          }
        });
        ["qty_min", "qty_max", "status"].forEach((key) => {
          const value = String(draft[key] || "").trim();
          if (String(current[key] || "") !== value) {
            next[key] = value;
            changed = true;
          }
        });
        return changed ? next : current;
      });
    }, HISTORY_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [draft]);

  useEffect(() => {
    setPage(0);
  }, [filters, pageSize]);

  useEffect(() => {
    const gen = ++requestGen.current;
    setLoading(true);
    setErr("");
    const params = historyRequestParams(filters, { skip: page * pageSize, limit: pageSize });
    api.salesPwaDeliveryHistory(params)
      .then((result) => {
        if (gen !== requestGen.current) return;
        setData(result || { rows: [], total: 0 });
        setLoading(false);
      })
      .catch((error) => {
        if (gen !== requestGen.current) return;
        setData(null);
        setErr(error?.response?.data?.detail || error?.message || "Riwayat pengiriman gagal dimuat");
        setLoading(false);
      });
  }, [filters, page, pageSize]);

  const rows = data?.rows || [];
  const total = Number(data?.total) || 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize) || 1);
  const fromIdx = total === 0 ? 0 : page * pageSize + 1;
  const toIdx = Math.min(total, (page + 1) * pageSize);
  const filtersOn = historyFiltersActive(filters) || historyFiltersActive(draft);

  const applyDateRange = (from, to) => {
    setDraft((current) => ({ ...current, date_from: from || "", date_to: to || "" }));
    setFilters((current) => ({ ...current, date_from: from || "", date_to: to || "" }));
  };

  const applyPreset = (preset) => {
    const range = historyDatePresetRange(preset);
    applyDateRange(range.date_from, range.date_to);
  };

  const resetFilters = () => {
    const next = defaultHistoryFilters();
    setDraft(next);
    setFilters(next);
    setPage(0);
  };

  const pageNumbers = useMemo(() => {
    const maxButtons = 5;
    const start = Math.max(0, Math.min(page - 2, totalPages - maxButtons));
    const end = Math.min(totalPages, start + maxButtons);
    return Array.from({ length: Math.max(0, end - start) }, (_, i) => start + i);
  }, [page, totalPages]);

  return (
    <>
      <header className="spwa-top">
        <p className="spwa-kicker">LAINNYA</p>
        <h1 className="spwa-hist-title"><Truck size={18} aria-hidden /> Riwayat Pengiriman</h1>
        <p className="spwa-top-meta">Daftar barang yang telah dikonfirmasi terkirim</p>
      </header>
      <main className="spwa-main" data-testid="sales-pwa-delivery-history">
        <div className="spwa-hist-toolbar" data-testid="sales-hist-toolbar">
          <div className="spwa-hist-toolbar-row spwa-hist-toolbar-row-primary">
            <DeliveryPlanDateRangeField
              dateFrom={draft.date_from}
              dateTo={draft.date_to}
              onApply={applyDateRange}
              label="Tanggal Pengiriman"
              testIdPrefix="sales-hist-date"
            />
            {filtersOn ? (
              <button type="button" className="spwa-hist-reset" data-testid="sales-hist-reset" onClick={resetFilters}>
                Reset Filter
              </button>
            ) : null}
          </div>
          <div className="spwa-hist-toolbar-row spwa-hist-presets" data-testid="sales-hist-presets">
            {PRESETS.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className={activePreset === preset.id ? "is-on" : ""}
                data-testid={`sales-hist-preset-${preset.id}`}
                aria-pressed={activePreset === preset.id}
                onClick={() => applyPreset(preset.id)}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>

        <div className="spwa-hist-mobile-filters" data-testid="sales-hist-mobile-filters">
          <ColumnFilters draft={draft} setDraftField={setDraftField} variant="mobile" />
        </div>

        {err && <div className="spwa-err" data-testid="sales-hist-error">{err}</div>}
        {loading && !data && <div className="spwa-muted" data-testid="sales-hist-loading">Memuat riwayat...</div>}

        <div className="spwa-hist-scroll" data-testid="sales-hist-scroll">
          <table className="spwa-hist-table" data-testid="sales-hist-table">
            <colgroup>
              <col className="spwa-hist-col-date" />
              <col className="spwa-hist-col-customer" />
              <col className="spwa-hist-col-po" />
              <col className="spwa-hist-col-sj" />
              <col className="spwa-hist-col-item" />
              <col className="spwa-hist-col-qty" />
              <col className="spwa-hist-col-status" />
              <col className="spwa-hist-col-action" />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">Tanggal</th>
                <th scope="col">Customer</th>
                <th scope="col">No. PO</th>
                <th scope="col">No. SJ</th>
                <th scope="col">Nama Barang</th>
                <th scope="col">Qty</th>
                <th scope="col">Status</th>
                <th scope="col">Aksi</th>
              </tr>
              <tr className="spwa-column-filters spwa-hist-desktop-filters">
                <th>
                  <span className="spwa-hist-date-hint">
                    {draft.date_from || draft.date_to
                      ? `${formatHistoryDay(draft.date_from)} – ${formatHistoryDay(draft.date_to)}`
                      : "Semua tanggal"}
                  </span>
                </th>
                <th>
                  <ClearableInput
                    value={draft.customer_q}
                    onChange={(v) => setDraftField("customer_q", v)}
                    onClear={() => setDraftField("customer_q", "")}
                    placeholder="Cari customer..."
                    ariaLabel="Filter customer"
                    testId="sales-hist-filter-customer"
                  />
                </th>
                <th>
                  <ClearableInput
                    value={draft.po_q}
                    onChange={(v) => setDraftField("po_q", v)}
                    onClear={() => setDraftField("po_q", "")}
                    placeholder="Cari PO..."
                    ariaLabel="Filter nomor PO"
                    testId="sales-hist-filter-po"
                  />
                </th>
                <th>
                  <ClearableInput
                    value={draft.sj_q}
                    onChange={(v) => setDraftField("sj_q", v)}
                    onClear={() => setDraftField("sj_q", "")}
                    placeholder="Cari SJ..."
                    ariaLabel="Filter nomor SJ"
                    testId="sales-hist-filter-sj"
                  />
                </th>
                <th>
                  <ClearableInput
                    value={draft.item_q}
                    onChange={(v) => setDraftField("item_q", v)}
                    onClear={() => setDraftField("item_q", "")}
                    placeholder="Cari barang..."
                    ariaLabel="Filter nama barang"
                    testId="sales-hist-filter-item"
                  />
                </th>
                <th>
                  <div className="spwa-hist-qty-filter">
                    <input
                      inputMode="numeric"
                      aria-label="Qty minimum"
                      placeholder="Min"
                      value={draft.qty_min}
                      onChange={(e) => setDraftField("qty_min", e.target.value.replace(/[^\d]/g, ""))}
                      data-testid="sales-hist-filter-qty-min"
                    />
                    <span>–</span>
                    <input
                      inputMode="numeric"
                      aria-label="Qty maksimum"
                      placeholder="Max"
                      value={draft.qty_max}
                      onChange={(e) => setDraftField("qty_max", e.target.value.replace(/[^\d]/g, ""))}
                      data-testid="sales-hist-filter-qty-max"
                    />
                  </div>
                </th>
                <th>
                  <select
                    aria-label="Filter status"
                    value={draft.status}
                    onChange={(e) => setDraftField("status", e.target.value)}
                    data-testid="sales-hist-filter-status"
                  >
                    <option value="">Semua Status</option>
                    <option value="Terkirim">Terkirim</option>
                  </select>
                </th>
                <th />
              </tr>
            </thead>
            <tbody>
              {!loading && !err && rows.length === 0 && (
                <tr>
                  <td colSpan={8}>
                    <div className="spwa-empty" data-testid="sales-hist-empty">Belum ada pengiriman terkonfirmasi pada filter ini.</div>
                  </td>
                </tr>
              )}
              {rows.map((row) => (
                <tr key={row.id} data-testid={`sales-hist-row-${row.id}`}>
                  <td className="spwa-hist-cell-text">{formatHistoryDay(row.confirmed_day || row.confirmed_at)}</td>
                  <td className="spwa-hist-cell-text">
                    <div className="spwa-cust">{row.customer || "—"}</div>
                    {row.outlet ? <div className="spwa-po">{row.outlet}</div> : null}
                  </td>
                  <td className="mono spwa-hist-cell-text">{row.po_number || "—"}</td>
                  <td className="mono spwa-hist-cell-text">{row.sj_number || "—"}</td>
                  <td className="spwa-hist-cell-text">{row.item_name || "—"}</td>
                  <td className="mono spwa-hist-cell-text">{formatHistoryQty(row.qty, row.unit)}</td>
                  <td><span className="spwa-hist-status" data-testid={`sales-hist-status-${row.id}`}>{row.status_label || "Terkirim"}</span></td>
                  <td className="spwa-hist-col-action">
                    <button
                      type="button"
                      className="spwa-hist-action"
                      aria-label="Lihat detail"
                      data-testid={`sales-hist-detail-${row.id}`}
                      onClick={() => setDetail(row)}
                    >
                      <Eye size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="spwa-hist-pager" data-testid="sales-hist-pager">
          <span data-testid="sales-hist-range">
            Menampilkan {fromIdx} - {toIdx} dari {total} data
          </span>
          <div className="spwa-hist-pager-controls">
            <button type="button" disabled={page <= 0 || loading} onClick={() => setPage((p) => Math.max(0, p - 1))} data-testid="sales-hist-prev">‹</button>
            {pageNumbers.map((n) => (
              <button
                key={n}
                type="button"
                className={n === page ? "is-on" : ""}
                onClick={() => setPage(n)}
                data-testid={`sales-hist-page-${n + 1}`}
              >
                {n + 1}
              </button>
            ))}
            <button type="button" disabled={page >= totalPages - 1 || loading || total === 0} onClick={() => setPage((p) => p + 1)} data-testid="sales-hist-next">›</button>
            <select
              aria-label="Ukuran halaman"
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value) || 25)}
              data-testid="sales-hist-page-size"
            >
              {HISTORY_PAGE_SIZE_OPTIONS.map((size) => (
                <option key={size} value={size}>{size}</option>
              ))}
            </select>
          </div>
        </div>
      </main>
      {detail ? <HistoryDetailDrawer row={detail} onClose={() => setDetail(null)} /> : null}
    </>
  );
}
