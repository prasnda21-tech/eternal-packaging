import { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Printer, Download, CheckCircle2, XCircle } from "lucide-react";
import api from "../lib/api";
import { formatDate, formatDeadline } from "../lib/constants";
import { formatApiError } from "../lib/errors";
import DeliveryProofPanel from "../components/DeliveryProofPanel";

export default function SuratJalan() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState("");

  useEffect(() => {
    api.getOrder(id).then((d) => {
      setOrder(d.order);
    }).catch((e) => setLoadErr(formatApiError(e, "Gagal memuat"))).finally(() => setLoading(false));
  }, [id]);

  const timelineEvent = (stage) => order?.timeline?.filter((t) => t.stage === stage).slice(-1)[0] || null;

  const packedEvent = useMemo(() => timelineEvent(11), [order]);
  const loadingEvent = useMemo(() => timelineEvent(13), [order]);
  const deliveredEvent = useMemo(() => timelineEvent(15), [order]);

  const doPrint = () => window.print();

  if (loading) return <div className="loading">Memuat Surat Jalan...</div>;
  // Hanya loadErr yang boleh menggantikan halaman (data awal tidak ada).
  if (loadErr) return <div className="error-page" style={{whiteSpace:"pre-line"}} data-testid="sj-load-error">{String(loadErr)}</div>;
  if (!order) return null;

  const sjNumber = order.delivery_note_number || `SJ-${order.po_number.replace("PO-", "")}`;
  const isDelivered = order.delivery_status === "Terkirim";
  const lc = order.loading_checklist || {};
  const driverName = lc.driver_name || "";

  return (
    <div className="surat-jalan-wrap">
      {/* Action bar - hidden on print */}
      <div className="sj-actionbar no-print">
        <button className="ghost sm" onClick={() => navigate(-1)} data-testid="sj-back-button">
          <ArrowLeft size={16} /> Kembali
        </button>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="ghost" onClick={doPrint} data-testid="sj-print-button">
            <Printer size={16} /> Cetak / Simpan PDF
          </button>
          <button className="ghost" onClick={doPrint} data-testid="sj-download-button" title="Cetak → Save as PDF">
            <Download size={16} /> Unduh PDF
          </button>
        </div>
      </div>

      {/* Printable A4 document */}
      <article className="surat-jalan" data-testid="surat-jalan-doc">
        <header className="sj-head">
          <div>
            <div className="sj-brand"><span>EP</span> ETERNAL PACKAGING</div>
            <p className="sj-subline">Screen-printing packaging · Produksi &amp; pengiriman</p>
          </div>
          <div className="sj-meta">
            <h1>SURAT JALAN</h1>
            <p><b>No. SJ:</b> {sjNumber}</p>
            <p><b>Ref. PO:</b> {order.po_number}</p>
            <p><b>Dicetak:</b> {formatDate(new Date().toISOString())}</p>
          </div>
        </header>

        <section className="sj-parties">
          <div>
            <h4>Dikirim kepada</h4>
            <p><strong>{order.customer}</strong></p>
            <p>{order.outlet}</p>
            <p>a.n. {order.recipient}</p>
            <p>{order.address}</p>
            <p className="muted small">Zona: {order.delivery_zone}</p>
          </div>
          <div>
            <h4>Info Pengiriman</h4>
            <p><b>Ekspedisi:</b> {order.expedition}</p>
            <p><b>Sales:</b> {order.sales || order.sales_user || <em style={{ color: "#9ea6ab" }}>Perlu verifikasi</em>}</p>
            <p><b>User / Pembuat PO:</b> {order.created_by_user || <em style={{ color: "#9ea6ab" }}>Perlu verifikasi</em>}</p>
            <p><b>Termin bayar:</b> {order.payment_terms}</p>
            <p><b>Tanggal Pengiriman:</b> {formatDeadline(
              order.tanggal_pengiriman
              || order.effective_delivery_date
              || order.delivery_deadline_effective
              || order.scheduled_delivery_date
              || order.delivery_deadline_manual
            )}</p>
            {order.priority === "urgent" && <p className="urgent-badge inline">URGENT</p>}
          </div>
        </section>

        <section className="sj-items">
          <h4>Rincian Barang</h4>
          <table>
            <thead>
              <tr><th>#</th><th>Kode</th><th>Nama Barang</th><th>Warna</th><th className="num">Qty</th><th>Satuan</th></tr>
            </thead>
            <tbody>
              {order.line_items.map((li, i) => (
                <tr key={i} data-testid={`sj-item-${i}`}>
                  <td>{i + 1}</td>
                  <td className="mono">{li.item_code}</td>
                  <td>{li.name}{li.notes ? <div className="small muted">Catatan: {li.notes}</div> : null}</td>
                  <td>{li.color}</td>
                  <td className="num mono">{li.quantity.toLocaleString("id-ID")}</td>
                  <td>{li.unit}</td>
                </tr>
              ))}
              <tr className="total"><td colSpan="4">Total</td><td className="num mono">{order.total_quantity.toLocaleString("id-ID")}</td><td>pcs</td></tr>
            </tbody>
          </table>
        </section>

        <section className="sj-checklist" data-testid="sj-checklist">
          <h4>Pengiriman</h4>
          {(lc.vehicle_plate || lc.driver_name || lc.notes) && (
            <div className="sj-form" data-testid="sj-loading-history">
              {lc.vehicle_plate && <p><b>No. Kendaraan:</b> {lc.vehicle_plate}</p>}
              {lc.driver_name && <p><b>Nama Driver:</b> {lc.driver_name}</p>}
              {lc.notes && <p><b>Catatan:</b> {lc.notes}</p>}
            </div>
          )}
          <div className="sj-timestamps">
            <div><b>Waktu packing:</b> {packedEvent ? formatDate(packedEvent.at) : "-"}{packedEvent ? <span className="muted small"> · oleh {packedEvent.by_name}</span> : null}</div>
            {(loadingEvent || lc.updated_at) && (
              <div><b>Riwayat Loading:</b> {loadingEvent ? formatDate(loadingEvent.at) : formatDate(lc.updated_at)}{lc.updated_by_name ? <span className="muted small"> · oleh {lc.updated_by_name}</span> : null}</div>
            )}
            <div>
              <b>Status pengiriman:</b> {isDelivered ? (
                <span className="status-ok"><CheckCircle2 size={12} /> Terkirim</span>
              ) : order.delivery_status === "Sebagian Terkirim" ? (
                <span className="status-wait"><CheckCircle2 size={12} /> Sebagian Terkirim</span>
              ) : (
                <span className="status-wait"><XCircle size={12} /> {order.delivery_status || "Belum Terkirim"}</span>
              )}
              {deliveredEvent ? <span className="muted small"> · {formatDate(deliveredEvent.at)} oleh {deliveredEvent.by_name}</span> : null}
            </div>
          </div>
        </section>

        <section className="sj-signatures">
          <div>
            <p>Diserahkan oleh</p>
            <div className="sig-box" />
            <p className="small">Petugas Loading</p>
          </div>
          <div>
            <p>Diangkut oleh</p>
            <div className="sig-box" />
            <p className="small">{driverName || "Driver"}</p>
          </div>
          <div>
            <p>Diterima oleh</p>
            <div className="sig-box" />
            <p className="small">{order.recipient}</p>
          </div>
        </section>

        <div className="no-print sj-proof-slot">
          <DeliveryProofPanel order={order} onUpdated={setOrder} />
        </div>

        <footer className="sj-foot">
          <p>Dokumen ini menggunakan data PO {order.po_number} · dibuat otomatis oleh sistem Eternal Packaging</p>
        </footer>
      </article>
    </div>
  );
}
