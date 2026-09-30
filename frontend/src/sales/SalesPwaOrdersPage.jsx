import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { FileText, Search, SlidersHorizontal, X } from "lucide-react";
import api from "../lib/api";
import { documentTotal, formatSalesDate } from "../lib/salesPwa";
import { readSalesPwaPoListState, writeSalesPwaPoListState } from "../lib/salesPwaPoListState";
import SalesPwaDocumentSheet from "./SalesPwaDocumentSheet";
import SalesPwaProgress from "./SalesPwaProgress";

const FILTERS = [
  { id: "semua", label: "Semua" },
  { id: "berjalan", label: "Berjalan" },
  { id: "hold", label: "HOLD" },
  { id: "parsial", label: "Parsial" },
  { id: "selesai", label: "Selesai" },
];

const SEARCH_DEBOUNCE_MS = 300;
const NARROW_QUERY = "(max-width: 767px)";

function useNarrowScreen() {
  const [narrow, setNarrow] = useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
    return window.matchMedia(NARROW_QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
    const media = window.matchMedia(NARROW_QUERY);
    const onChange = () => setNarrow(media.matches);
    onChange();
    if (media.addEventListener) media.addEventListener("change", onChange);
    else if (media.addListener) media.addListener(onChange);
    return () => {
      if (media.removeEventListener) media.removeEventListener("change", onChange);
      else if (media.removeListener) media.removeListener(onChange);
    };
  }, []);

  return narrow;
}

function currentScrollY() {
  return window.scrollY || document.documentElement?.scrollTop || 0;
}

function searchKeyword(qValue, customerQ) {
  return [qValue, customerQ]
    .map((value) => String(value || "").trim())
    .filter((value, index, all) => value && all.indexOf(value) === index)
    .join("\n");
}

export default function SalesPwaOrdersPage() {
  const initial = readSalesPwaPoListState();
  const narrow = useNarrowScreen();
  const [q, setQ] = useState(initial.filters.q);
  const [debouncedQ, setDebouncedQ] = useState(initial.filters.q.trim());
  const [status, setStatus] = useState(initial.filters.status);
  const [columns, setColumns] = useState({
    customer_q: initial.filters.customer_q,
    stage: initial.filters.stage,
    date_from: initial.filters.date_from,
    date_to: initial.filters.date_to,
    progress_min: initial.filters.progress_min,
    buffer: initial.filters.buffer,
  });
  const [filterOpen, setFilterOpen] = useState(false);
  const [data, setData] = useState(null);
  const [dataKey, setDataKey] = useState("");
  const [err, setErr] = useState("");
  const [sheet, setSheet] = useState(null);
  const filtersRef = useRef(initial.filters);
  const scrollYRef = useRef(initial.scrollY);
  const restoredRef = useRef(false);
  const keyword = searchKeyword(debouncedQ, columns.customer_q);
  const requestKey = JSON.stringify({
    keyword,
    status,
    stage: columns.stage,
    date_from: columns.date_from,
    date_to: columns.date_to,
    progress_min: columns.progress_min,
    buffer: columns.buffer,
  });
  const searchPending = q.trim() !== debouncedQ;
  filtersRef.current = {
    q,
    customer_q: columns.customer_q,
    status,
    stage: columns.stage,
    date_from: columns.date_from,
    date_to: columns.date_to,
    progress_min: columns.progress_min,
    buffer: columns.buffer,
  };

  const rememberPosition = () => {
    const y = currentScrollY();
    scrollYRef.current = y;
    writeSalesPwaPoListState(filtersRef.current, y);
  };

  const setColumn = (key, value) => setColumns((current) => ({ ...current, [key]: value }));

  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedQ(q.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [q]);

  useEffect(() => {
    writeSalesPwaPoListState(filtersRef.current, scrollYRef.current);
  }, [q, status, columns]);

  useEffect(() => {
    const onScroll = () => {
      scrollYRef.current = currentScrollY();
      writeSalesPwaPoListState(filtersRef.current, scrollYRef.current);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setErr("");
    api.salesPwaOrders({
      q: keyword,
      status,
      stage: columns.stage,
      date_from: columns.date_from,
      date_to: columns.date_to,
      progress_min: columns.progress_min === "" ? undefined : Number(columns.progress_min),
      buffer: columns.buffer,
      sort: "order_date",
      direction: "desc",
      limit: 120,
    })
      .then((res) => {
        if (!cancelled) {
          setData(res);
          setDataKey(requestKey);
        }
      })
      .catch((e) => { if (!cancelled) setErr(e.response?.data?.detail || e.message || "Gagal memuat PO"); });
    return () => { cancelled = true; };
  }, [keyword, status, columns, requestKey]);

  const visible = !searchPending && dataKey === requestKey ? data : null;
  const orders = visible?.orders || [];
  const detailCount = [columns.stage, columns.date_from, columns.date_to, columns.progress_min, columns.buffer].filter(Boolean).length;

  useLayoutEffect(() => {
    if (!visible || restoredRef.current) return;
    restoredRef.current = true;
    if (scrollYRef.current > 0) window.scrollTo(0, scrollYRef.current);
  }, [visible]);

  const openDocs = async (event, order) => {
    event.preventDefault();
    event.stopPropagation();
    try {
      const docs = await api.salesPwaDocuments(order.id);
      setSheet({ orderId: order.id, docs });
    } catch (e) {
      setErr(e.response?.data?.detail || "Dokumen tidak tersedia");
    }
  };

  const resetDetailFilters = () => {
    setColumns((current) => ({
      ...current,
      stage: "",
      date_from: "",
      date_to: "",
      progress_min: "",
      buffer: "",
    }));
  };

  const onNavigateAway = (event) => {
    if (event.target.closest?.("a")) rememberPosition();
  };

  return (
    <div onClickCapture={onNavigateAway}>
      <header className="spwa-top">
        <p className="spwa-kicker">PWA SALES</p>
        <h1>PO Saya</h1>
      </header>
      <main className="spwa-main" data-testid="sales-pwa-orders">
        <div className="spwa-filters" data-testid="sales-status-chips">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className={`spwa-chip${status === f.id ? " is-on" : ""}`}
              onClick={() => setStatus(f.id)}
              data-testid={`sales-filter-${f.id}`}
            >
              {f.label}
              {visible?.counts?.[f.id] != null ? ` ${visible.counts[f.id]}` : ""}
            </button>
          ))}
        </div>
        <label className={`spwa-search${searchPending ? " is-loading" : ""}`}>
          <Search size={14} />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Cari pelanggan / PO"
            aria-label="Cari pelanggan atau PO"
            data-testid="sales-orders-search"
          />
          {q ? (
            <button
              type="button"
              className="spwa-search-clear"
              aria-label="Hapus pencarian"
              data-testid="sales-orders-search-clear"
              onClick={() => setQ("")}
            >
              <X size={14} />
            </button>
          ) : null}
        </label>
        {!narrow && (
          <p className="spwa-filter-hint"><SlidersHorizontal size={13} /> Filter rinci tersedia langsung pada header tabel.</p>
        )}
        {narrow && (
          <div className="spwa-toolbar">
            <button
              type="button"
              className="spwa-filter-btn"
              data-testid="sales-filter-open"
              onClick={() => setFilterOpen(true)}
            >
              <SlidersHorizontal size={14} /> Filter{detailCount ? ` ${detailCount}` : ""}
            </button>
          </div>
        )}
        {err && <div className="spwa-err">{err}</div>}
        {!visible && !err && (
          <div data-testid="sales-po-loading" aria-busy="true">
            <div className="spwa-muted">Memuat...</div>
            <div className="spwa-skeleton" />
            <div className="spwa-skeleton" />
            <div className="spwa-skeleton" />
          </div>
        )}
        {visible && !orders.length && <div className="spwa-empty">Tidak ada PO pada filter ini.</div>}
        {visible?.source_truncated && <div className="spwa-err">Sebagian sumber data belum termuat. Muat ulang sebelum mengambil keputusan.</div>}
        {visible && Number(visible.total) > orders.length && (
          <p className="spwa-muted" data-testid="sales-po-remainder">
            Menampilkan {orders.length} dari {visible.total} PO. {visible.total - orders.length} PO belum ditampilkan.
          </p>
        )}
        {orders.length > 0 && narrow && (
          <ul className="spwa-po-cards" data-testid="sales-po-cards">
            {orders.map((o) => (
              <li key={o.id} className="spwa-po-card" data-testid={`sales-po-card-${o.po_number}`}>
                <div className="spwa-po-card-customer">{o.customer}</div>
                <div className="spwa-po-card-number">{o.po_number}</div>
                <div className="spwa-po-card-meta">
                  <span className={`spwa-stage${o.hold ? " is-hold" : ""}`}>{o.status}</span>
                  <span className="spwa-date">{formatSalesDate(o.order_date)}</span>
                  <span className="spwa-pct">Progress {o.progress_percent ?? 0}%</span>
                  {o.buffer_stock
                    ? <span className="spwa-buffer-badge">Buffer Stock</span>
                    : <span className="spwa-buffer-plain">Bukan buffer stock</span>}
                </div>
                <SalesPwaProgress order={o} />
                <div className="spwa-po-card-actions">
                  <button
                    type="button"
                    className="spwa-docbtn"
                    data-testid={`sales-docs-${o.id}`}
                    onClick={(e) => openDocs(e, o)}
                  >
                    <FileText size={14} /> {documentTotal(o)} dokumen
                  </button>
                  <Link
                    to={`/sales/po/${encodeURIComponent(o.id)}`}
                    className="spwa-detail-btn"
                    data-testid={`sales-po-detail-${o.id}`}
                  >
                    Detail
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        )}
        {orders.length > 0 && !narrow && (
          <div className="spwa-table-wrap">
            <table className="spwa-table" data-testid="sales-po-table">
              <thead>
                <tr>
                  <th style={{ width: "34%" }}>PELANGGAN / PO</th>
                  <th style={{ width: "18%" }}>TAHAP</th>
                  <th style={{ width: "18%" }}>TANGGAL PO</th>
                  <th style={{ width: "12%" }}>%</th>
                  <th style={{ width: "18%" }}>BUFFER / DOK.</th>
                </tr>
                <tr className="spwa-column-filters">
                  <th><input aria-label="Filter pelanggan atau PO" value={columns.customer_q} onChange={(e) => setColumn("customer_q", e.target.value)} placeholder="Cari pelanggan / PO" /></th>
                  <th><input aria-label="Filter tahap" value={columns.stage} onChange={(e) => setColumn("stage", e.target.value)} placeholder="Tahap" /></th>
                  <th>
                    <div className="spwa-date-filter">
                      <input aria-label="Tanggal PO mulai" type="date" value={columns.date_from} onChange={(e) => setColumn("date_from", e.target.value)} />
                      <input aria-label="Tanggal PO akhir" type="date" value={columns.date_to} onChange={(e) => setColumn("date_to", e.target.value)} />
                    </div>
                  </th>
                  <th>
                    <select aria-label="Filter progres minimum" value={columns.progress_min} onChange={(e) => setColumn("progress_min", e.target.value)}>
                      <option value="">Semua</option>
                      <option value="25">≥25%</option>
                      <option value="50">≥50%</option>
                      <option value="75">≥75%</option>
                      <option value="100">100%</option>
                    </select>
                  </th>
                  <th>
                    <select aria-label="Filter buffer stock" value={columns.buffer} onChange={(e) => setColumn("buffer", e.target.value)}>
                      <option value="">Semua</option>
                      <option value="ya">Buffer</option>
                      <option value="tidak">Non-buffer</option>
                    </select>
                  </th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id} data-testid={`sales-po-row-${o.po_number}`}>
                    <td colSpan={5} style={{ padding: 0 }}>
                      <Link to={`/sales/po/${encodeURIComponent(o.id)}`} className="spwa-linkrow" style={{ display: "grid", gridTemplateColumns: "34% 18% 18% 12% 18%" }}>
                        <span style={{ padding: "5px 6px" }}>
                          <span className="spwa-cust">{o.customer}</span>
                          <div className="spwa-po">{o.po_number}</div>
                          {o.buffer_stock && <span className="spwa-buffer-badge">BUFFER</span>}
                          <SalesPwaProgress order={o} />
                        </span>
                        <span className={`spwa-stage${o.hold ? " is-hold" : ""}`} style={{ padding: "5px 6px" }}>{o.status}</span>
                        <span className="spwa-date" style={{ padding: "5px 6px" }}>
                          {formatSalesDate(o.order_date)}
                        </span>
                        <span className="spwa-pct" style={{ padding: "5px 6px" }}>{o.progress_percent ?? 0}%</span>
                        <span style={{ padding: "5px 6px" }}>
                          <button
                            type="button"
                            className="spwa-docbtn"
                            data-testid={`sales-docs-${o.id}`}
                            onClick={(e) => openDocs(e, o)}
                          >
                            <FileText size={14} /> {documentTotal(o)}
                          </button>
                        </span>
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
      {narrow && filterOpen && (
        <>
          <button type="button" className="spwa-sheet-backdrop" aria-label="Tutup filter" onClick={() => setFilterOpen(false)} />
          <aside className="spwa-sheet spwa-filter-sheet" role="dialog" aria-label="Filter PO" data-testid="sales-filter-sheet">
            <h2>Filter</h2>
            <div className="spwa-filter-fields">
              <label>
                Tahap
                <input aria-label="Filter tahap" value={columns.stage} onChange={(e) => setColumn("stage", e.target.value)} placeholder="Tahap" />
              </label>
              <div className="spwa-filter-dates">
                <span>Tanggal PO</span>
                <div className="spwa-filter-date-row">
                  <label>
                    Mulai
                    <input aria-label="Tanggal PO mulai" type="date" value={columns.date_from} onChange={(e) => setColumn("date_from", e.target.value)} />
                  </label>
                  <label>
                    Akhir
                    <input aria-label="Tanggal PO akhir" type="date" value={columns.date_to} onChange={(e) => setColumn("date_to", e.target.value)} />
                  </label>
                </div>
              </div>
              <label>
                Progres minimum
                <select aria-label="Filter progres minimum" value={columns.progress_min} onChange={(e) => setColumn("progress_min", e.target.value)}>
                  <option value="">Semua</option>
                  <option value="25">≥25%</option>
                  <option value="50">≥50%</option>
                  <option value="75">≥75%</option>
                  <option value="100">100%</option>
                </select>
              </label>
              <label>
                Buffer stock
                <select aria-label="Filter buffer stock" value={columns.buffer} onChange={(e) => setColumn("buffer", e.target.value)}>
                  <option value="">Semua</option>
                  <option value="ya">Buffer</option>
                  <option value="tidak">Non-buffer</option>
                </select>
              </label>
            </div>
            <div className="spwa-filter-actions">
              <button type="button" className="spwa-filter-reset" onClick={resetDetailFilters}>Reset filter</button>
              <button type="button" className="spwa-filter-apply" data-testid="sales-filter-apply" onClick={() => setFilterOpen(false)}>Terapkan</button>
            </div>
          </aside>
        </>
      )}
      {sheet && (
        <SalesPwaDocumentSheet
          orderId={sheet.orderId}
          data={sheet.docs}
          onClose={() => setSheet(null)}
        />
      )}
    </div>
  );
}
