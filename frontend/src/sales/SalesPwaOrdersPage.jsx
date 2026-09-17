import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FileText, Search } from "lucide-react";
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
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [sheet, setSheet] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setErr("");
    api.salesPwaOrders({ q, status, limit: 80 })
      .then((res) => { if (!cancelled) setData(res); })
      .catch((e) => { if (!cancelled) setErr(e.response?.data?.detail || e.message || "Gagal memuat PO"); });
    return () => { cancelled = true; };
  }, [q, status]);

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
        {err && <div className="spwa-err">{err}</div>}
        {!data && !err && <div className="spwa-muted">Memuat...</div>}
        {data && !orders.length && <div className="spwa-empty">Tidak ada PO pada filter ini.</div>}
        {orders.length > 0 && (
          <div className="spwa-table-wrap">
            <table className="spwa-table" data-testid="sales-po-table">
              <thead>
                <tr>
                  <th style={{ width: "38%" }}>PELANGGAN / PO</th>
                  <th style={{ width: "18%" }}>TAHAP</th>
                  <th style={{ width: "18%" }}>TANGGAL KIRIM</th>
                  <th style={{ width: "12%" }}>%</th>
                  <th style={{ width: "14%" }} aria-label="Dokumen" />
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id} className={o.tanggal_kirim_overdue ? "is-overdue" : ""} data-testid={`sales-po-row-${o.po_number}`}>
                    <td colSpan={5} style={{ padding: 0 }}>
                      <Link to={`/sales/po/${encodeURIComponent(o.id)}`} className="spwa-linkrow" style={{ display: "grid", gridTemplateColumns: "38% 18% 18% 12% 14%" }}>
                        <span style={{ padding: "5px 6px" }}>
                          <span className="spwa-cust">{o.customer}</span>
                          <div className="spwa-po">{o.po_number}</div>
                          <SalesPwaProgress order={o} />
                        </span>
                        <span className={`spwa-stage${o.hold ? " is-hold" : ""}`} style={{ padding: "5px 6px" }}>{o.status}</span>
                        <span className={`spwa-date${o.tanggal_kirim_overdue ? " is-overdue" : ""}`} style={{ padding: "5px 6px" }}>
                          {formatSalesDate(o.tanggal_kirim)}
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
