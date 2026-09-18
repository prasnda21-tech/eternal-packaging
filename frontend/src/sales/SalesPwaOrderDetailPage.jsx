import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, FileText } from "lucide-react";
import api from "../lib/api";
import { documentTotal, formatSalesDate } from "../lib/salesPwa";
import SalesPwaDocumentSheet from "./SalesPwaDocumentSheet";
import SalesPwaProgress from "./SalesPwaProgress";

export default function SalesPwaOrderDetailPage() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [sheet, setSheet] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api.salesPwaOrder(id)
      .then((res) => { if (!cancelled) setData(res); })
      .catch((e) => { if (!cancelled) setErr(e.response?.status === 404 ? "PO tidak ditemukan" : (e.response?.data?.detail || e.message)); });
    return () => { cancelled = true; };
  }, [id]);

  const openDocs = async () => {
    const docs = await api.salesPwaDocuments(id);
    setSheet({ orderId: id, docs });
  };

  const order = data?.order;

  return (
    <>
      <header className="spwa-top">
        <Link to="/sales/po" className="spwa-back"><ChevronLeft size={14} /> PO Saya</Link>
        <p className="spwa-kicker">DETAIL PO</p>
        <h1>{order?.po_number || "PO"}</h1>
      </header>
      <main className="spwa-main" data-testid="sales-pwa-order-detail">
        {err && <div className="spwa-err">{err}</div>}
        {!order && !err && <div className="spwa-muted">Memuat...</div>}
        {order && (
          <>
            <section className="spwa-card">
              <h2>{order.customer}</h2>
              <dl className="spwa-kv">
                <dt>Tahap</dt><dd>{order.status}</dd>
                <dt>Tanggal PO</dt><dd>{formatSalesDate(order.order_date)}</dd>
                <dt>Buffer</dt><dd>{order.buffer_stock ? "Ya — Buffer Stock" : "Tidak"}</dd>
                <dt>Progress</dt><dd>{order.progress_percent}%</dd>
                <dt>Sales</dt><dd>{order.sales || "Perlu verifikasi"}</dd>
                <dt>Alamat</dt><dd>{order.address || "—"}</dd>
              </dl>
              <SalesPwaProgress order={order} />
              <button type="button" className="spwa-docbtn" style={{ marginTop: 10 }} onClick={openDocs} data-testid="sales-detail-docs">
                <FileText size={14} /> {documentTotal(order)} dokumen
              </button>
            </section>
            <section className="spwa-card">
              <h2>Item</h2>
              {(order.line_items || []).map((li) => (
                <div key={li.idx} className="spwa-doc-row">
                  <span>{li.name}</span>
                  <span>{li.quantity} {li.unit || ""}</span>
                </div>
              ))}
            </section>
          </>
        )}
      </main>
      {sheet && (
        <SalesPwaDocumentSheet orderId={sheet.orderId} data={sheet.docs} onClose={() => setSheet(null)} />
      )}
    </>
  );
}
