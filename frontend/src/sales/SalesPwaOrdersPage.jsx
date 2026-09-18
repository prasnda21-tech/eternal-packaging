import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FileText, Search, SlidersHorizontal } from "lucide-react";
import api from "../lib/api";
import { documentTotal, formatSalesDate } from "../lib/salesPwa";
import SalesPwaDocumentSheet from "./SalesPwaDocumentSheet";
import SalesPwaProgress from "./SalesPwaProgress";

const FILTERS = [
  { id: "semua", label: "Semua" },
  { id: "berjalan", label: "Berjalan" },
  { id: "hold", label: "HOLD" },
  { id: "parsial", label: "Parsial" },
  { id: "selesai", label: "Selesai" },
];

export default function SalesPwaOrdersPage() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("semua");
  const [columns, setColumns] = useState({
    customer_q: "",
    stage: "",
    date_from: "",
    date_to: "",
    progress_min: "",
    buffer: "",
  });
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [sheet, setSheet] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setErr("");
    api.salesPwaOrders({
        q,
        status,
        customer_q: columns.customer_q,
        po_q: columns.customer_q,
        stage: columns.stage,
        date_from: columns.date_from,
        date_to: columns.date_to,
        progress_min: columns.progress_min === "" ? undefined : Number(columns.progress_min),
        buffer: columns.buffer,
        sort: "order_date",
        direction: "desc",
        limit: 120,
      })
      .then((res) => { if (!cancelled) setData(res); })
      .catch((e) => { if (!cancelled) setErr(e.response?.data?.detail || e.message || "Gagal memuat PO"); });
    return () => { cancelled = true; };
  }, [q, status, columns]);

  const setColumn = (key, value) => setColumns((current) => ({ ...current, [key]: value }));

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

  const orders = data?.orders || [];

  return (
    <>
      <header className="spwa-top">
        <p className="spwa-kicker">PWA SALES</p>
        <h1>PO Saya</h1>
      </header>
      <main className="spwa-main" data-testid="sales-pwa-orders">
        <div className="spwa-filters">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className={`spwa-chip${status === f.id ? " is-on" : ""}`}
              onClick={() => setStatus(f.id)}
              data-testid={`sales-filter-${f.id}`}
            >
              {f.label}
              {data?.counts?.[f.id] != null ? ` ${data.counts[f.id]}` : ""}
            </button>
          ))}
        </div>
        <label className="spwa-search">
          <Search size={14} />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Cari pelanggan / PO"
            data-testid="sales-orders-search"
          />
        </label>
        <p className="spwa-filter-hint"><SlidersHorizontal size={13} /> Filter rinci tersedia langsung pada header tabel.</p>
        {err && <div className="spwa-err">{err}</div>}
        {!data && !err && <div className="spwa-muted">Memuat...</div>}
        {data && !orders.length && <div className="spwa-empty">Tidak ada PO pada filter ini.</div>}
        {data?.source_truncated && <div className="spwa-err">Sebagian sumber data belum termuat. Muat ulang sebelum mengambil keputusan.</div>}
        {orders.length > 0 && (
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
      {sheet && (
        <SalesPwaDocumentSheet
          orderId={sheet.orderId}
          data={sheet.docs}
          onClose={() => setSheet(null)}
        />
      )}
    </>
  );
}
