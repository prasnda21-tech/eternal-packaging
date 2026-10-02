import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { ChevronRight, FileText, Search, SlidersHorizontal, X } from "lucide-react";
import api from "../lib/api";
import { documentTotal, formatSalesDate } from "../lib/salesPwa";
import { SALES_PWA_PO_PAGE_SIZE, readSalesPwaPoListState, salesPwaPoFiltersFromContext, writeSalesPwaPoListState } from "../lib/salesPwaPoListState";
import SalesPwaDesignApproval, { orderExpectsDesignLink } from "./SalesPwaDesignApproval";
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
const STAGE_GROUPS = [
  { id: "semua", label: "", options: [{ value: "", label: "Semua Tahap" }] },
  {
    id: "approval",
    label: "Approval",
    options: [
      { value: "Approval Design", label: "Approval Design" },
      { value: "Approval Payment", label: "Approval Payment" },
    ],
  },
  {
    id: "produksi",
    label: "Produksi",
    options: [
      { value: "Design", label: "Design" },
      { value: "Produksi", label: "Produksi" },
      { value: "Packing", label: "Packing" },
    ],
  },
  {
    id: "delivery",
    label: "Delivery",
    options: [
      { value: "Pengiriman", label: "Pengiriman" },
      { value: "Selesai", label: "Selesai" },
    ],
  },
];
const STAGE_OPTIONS = STAGE_GROUPS.flatMap((group) => group.options);

function poCardStatusTone(order) {
  const status = String(order?.status || "");
  if (order?.hold || status === "HOLD") return "is-hold";
  if (order?.partial_delivery || /parsial/i.test(status)) return "is-partial";
  const progress = Number(order?.progress_percent);
  if (status === "Terkirim" || (Number.isFinite(progress) && progress >= 100)) return "is-done";
  return "is-running";
}

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
  const location = useLocation();
  const stored = readSalesPwaPoListState();
  const contextFilters = salesPwaPoFiltersFromContext(location.state?.salesPwaPoContext);
  const initial = contextFilters ? { filters: contextFilters, scrollY: 0 } : stored;
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
  const [stageOpen, setStageOpen] = useState(false);
  const [stageDraft, setStageDraft] = useState(initial.filters.stage);
  const [data, setData] = useState(null);
  const [dataKey, setDataKey] = useState("");
  const [err, setErr] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [sheet, setSheet] = useState(null);
  const filtersRef = useRef(initial.filters);
  const scrollYRef = useRef(initial.scrollY);
  const restoredRef = useRef(false);
  const requestGen = useRef(0);
  const offsetRef = useRef(0);
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

  const listParamsRef = useRef((skip) => ({ skip, limit: SALES_PWA_PO_PAGE_SIZE }));
  listParamsRef.current = (skip) => ({
    q: keyword,
    status,
    stage: columns.stage,
    date_from: columns.date_from,
    date_to: columns.date_to,
    progress_min: columns.progress_min === "" ? undefined : Number(columns.progress_min),
    buffer: columns.buffer,
    sort: "order_date",
    direction: "desc",
    skip,
    limit: SALES_PWA_PO_PAGE_SIZE,
  });

  useEffect(() => {
    const generation = requestGen.current + 1;
    requestGen.current = generation;
    offsetRef.current = 0;
    setLoadingMore(false);
    setErr("");
    let cancelled = false;
    api.salesPwaOrders(listParamsRef.current(0))
      .then((res) => {
        if (cancelled || generation !== requestGen.current) return;
        const rows = Array.isArray(res?.orders) ? res.orders : [];
        offsetRef.current = rows.length;
        setData({ ...res, orders: rows });
        setDataKey(requestKey);
      })
      .catch((e) => { if (!cancelled && generation === requestGen.current) setErr(e.response?.data?.detail || e.message || "Gagal memuat PO"); });
    return () => { cancelled = true; };
  }, [keyword, status, columns, requestKey]);

  const visible = !searchPending && dataKey === requestKey ? data : null;
  const orders = visible?.orders || [];
  const detailCount = [columns.stage, columns.date_from, columns.date_to, columns.progress_min, columns.buffer].filter(Boolean).length;
  const remaining = visible ? Math.max(0, Number(visible.total || 0) - orders.length) : 0;

  const loadMore = () => {
    if (loadingMore || !visible || remaining <= 0) return;
    const generation = requestGen.current;
    const skip = offsetRef.current;
    setLoadingMore(true);
    api.salesPwaOrders(listParamsRef.current(skip))
      .then((res) => {
        if (generation !== requestGen.current) return;
        const incoming = Array.isArray(res?.orders) ? res.orders : [];
        setData((current) => {
          const prev = Array.isArray(current?.orders) ? current.orders : [];
          const seen = new Set(prev.map((row) => row?.id).filter(Boolean));
          const merged = prev.slice();
          incoming.forEach((row) => {
            if (row?.id && seen.has(row.id)) return;
            if (row?.id) seen.add(row.id);
            merged.push(row);
          });
          return { ...(current || {}), ...res, orders: merged };
        });
        offsetRef.current = incoming.length === 0 ? Math.max(skip, Number(res?.total) || skip) : skip + incoming.length;
      })
      .catch((e) => {
        if (generation === requestGen.current) setErr(e.response?.data?.detail || e.message || "Gagal memuat PO");
      })
      .finally(() => {
        if (generation === requestGen.current) setLoadingMore(false);
      });
  };

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
        {orders.length > 0 && narrow && (
          <ul className="spwa-po-cards" data-testid="sales-po-cards">
            {orders.map((o) => (
              <li key={o.id} className="spwa-po-card" data-testid={`sales-po-card-${o.po_number}`}>
                <div className="spwa-po-card-customer">{o.customer}</div>
                <div className="spwa-po-card-number">{o.po_number}</div>
                {String(o.priority || "").trim() ? <div className="spwa-po-card-urgency">{String(o.priority).trim()}</div> : null}
                <div className={`spwa-po-card-status ${poCardStatusTone(o)}`}>{o.status}</div>
                {o.partial_delivery && !/parsial/i.test(String(o.status || "")) ? <div className="spwa-po-card-line"><span>Partial terkirim</span></div> : null}
                {o.tanggal_kirim_overdue ? <div className="spwa-po-card-urgency">Kirim terlambat</div> : null}
                <div className="spwa-po-card-line"><span>Tanggal</span><strong>{formatSalesDate(o.order_date)}</strong></div>
                <div className="spwa-po-card-line"><span>Progress {o.progress_percent ?? 0}%</span></div>
                <SalesPwaProgress order={o} />
                <div className="spwa-po-card-line">
                  {o.buffer_stock
                    ? <span className="spwa-buffer-badge">Buffer Stock</span>
                    : <span className="spwa-buffer-plain">Bukan buffer stock</span>}
                </div>
                <SalesPwaDesignApproval order={o} expectMissing={orderExpectsDesignLink(o)} />
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
                    Detail <ChevronRight size={14} />
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
                      <div className="spwa-row-approval">
                        <SalesPwaDesignApproval order={o} expectMissing={orderExpectsDesignLink(o)} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {visible && remaining > 0 && (
          <div className="spwa-page-more">
            <p className="spwa-muted" data-testid="sales-po-remainder">
              Menampilkan {orders.length} dari {visible.total} PO. {remaining} PO belum ditampilkan.
            </p>
            <button
              type="button"
              className="spwa-load-more"
              data-testid="sales-po-load-more"
              onClick={loadMore}
              disabled={loadingMore}
            >
              {loadingMore ? "Memuat..." : "Muat lagi"}
            </button>
          </div>
        )}
      </main>
      {narrow && filterOpen && (
        <>
          <button type="button" className="spwa-sheet-backdrop" aria-label="Tutup filter" onClick={() => setFilterOpen(false)} />
          <aside className="spwa-sheet spwa-filter-sheet" role="dialog" aria-label="Filter PO" data-testid="sales-filter-sheet">
            <h2>Filter</h2>
            <div className="spwa-filter-fields">
              <div className="spwa-stage-picker">
                <span>Tahap</span>
                <button
                  type="button"
                  className="spwa-stage-picker-btn"
                  aria-label="Filter tahap"
                  data-testid="sales-stage-open"
                  onClick={() => { setStageDraft(columns.stage); setStageOpen(true); }}
                >
                  {STAGE_OPTIONS.find((option) => option.value === columns.stage)?.label || columns.stage || "Semua Tahap"}
                </button>
              </div>
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
      {narrow && stageOpen && (
        <>
          <button type="button" className="spwa-sheet-backdrop spwa-stage-backdrop" aria-label="Tutup tahap" onClick={() => setStageOpen(false)} />
          <aside className="spwa-sheet spwa-stage-sheet" role="dialog" aria-label="Pilih tahap" data-testid="sales-stage-sheet">
            <h2>Filter tahap</h2>
            <div className="spwa-stage-options">
              {STAGE_GROUPS.map((group) => (
                <div key={group.id} className="spwa-stage-group">
                  {group.label ? <h3>{group.label}</h3> : null}
                  {group.options.map((option) => (
                    <label key={option.label}>
                      <input
                        type="radio"
                        name="sales-pwa-stage"
                        checked={stageDraft === option.value}
                        onChange={() => setStageDraft(option.value)}
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
              ))}
            </div>
            <div className="spwa-filter-actions">
              <button type="button" className="spwa-filter-reset" onClick={() => setStageDraft("")}>Reset</button>
              <button
                type="button"
                className="spwa-filter-apply"
                data-testid="sales-stage-apply"
                onClick={() => { setColumn("stage", stageDraft); setStageOpen(false); }}
              >
                Terapkan
              </button>
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
