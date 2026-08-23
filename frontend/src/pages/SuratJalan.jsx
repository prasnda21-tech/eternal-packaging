import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Printer, Download, Save, CheckCircle2, XCircle } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate, formatDeadline } from "../lib/constants";
import { formatApiError } from "../lib/errors";
import DeliveryProofPanel from "../components/DeliveryProofPanel";

export default function SuratJalan() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user, hasPerm } = useAuth();
  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
  // Pisahkan error state:
  //   `loadErr`   -> gagal load awal halaman (boleh replace halaman)
  //   `actionErr` -> gagal action (Simpan checklist / Tandai Terkirim). Tampil inline,
  //                  jangan unmount halaman supaya konteks user tidak hilang.
  const [loadErr, setLoadErr] = useState("");
  const [actionErr, setActionErr] = useState("");
  const [busy, setBusy] = useState(false);

  // Checklist form (pre-filled from server data)
  const [vehiclePlate, setVehiclePlate] = useState("");
  const [driverName, setDriverName] = useState("");
  const [itemsLoaded, setItemsLoaded] = useState(false);
  const [sealOk, setSealOk] = useState(false);
  const [notes, setNotes] = useState("");

  useEffect(() => {
    api.getOrder(id).then((d) => {
      setOrder(d.order);
      const c = d.order.loading_checklist;
      if (c) {
        setVehiclePlate(c.vehicle_plate || "");
        setDriverName(c.driver_name || "");
        setItemsLoaded(!!c.items_loaded);
        setSealOk(!!c.seal_ok);
        setNotes(c.notes || "");
      } else if (user?.role === "driver") {
        setDriverName(user.name || "");
      }
    }).catch((e) => setLoadErr(formatApiError(e, "Gagal memuat"))).finally(() => setLoading(false));
  }, [id, user]);

  const timelineEvent = (stage) => order?.timeline?.filter((t) => t.stage === stage).slice(-1)[0] || null;

  const packedEvent = useMemo(() => timelineEvent(11), [order]);
  const loadingEvent = useMemo(() => timelineEvent(13), [order]);
  const deliveredEvent = useMemo(() => timelineEvent(15), [order]);

  // Auth gating via Effective Permission (bukan role name).
  //   `loading.update` = izin update checklist loading (kendaraan/plat/driver/dsb.)
  //   `delivery.update` = izin menandai terkirim
  const canEditChecklist = !!user && hasPerm("loading.update");
  const canMarkDelivered = !!user && hasPerm("delivery.update");

  const saveChecklist = async () => {
    setBusy(true);
    setActionErr("");
    try {
      const r = await api.updateLoading(order.id, {
        vehicle_plate: vehiclePlate,
        driver_name: driverName,
        items_loaded: itemsLoaded,
        seal_ok: sealOk,
        notes,
      });
      setOrder(r.order);
      setActionErr("");
    } catch (e) {
      setActionErr(formatApiError(e, "Gagal menyimpan checklist"));
    } finally {
      setBusy(false);
    }
  };

  const markDelivered = async () => {
    setBusy(true);
    setActionErr("");
    try {
      const r = await api.markDelivered(order.id);
      setOrder(r.order);
      setActionErr("");
    } catch (e) {
      setActionErr(formatApiError(e, "Gagal menandai terkirim"));
    } finally {
      setBusy(false);
    }
  };

  const doPrint = () => window.print();

  if (loading) return <div className="loading">Memuat Surat Jalan...</div>;
  // Hanya loadErr yang boleh menggantikan halaman (data awal tidak ada).
  if (loadErr) return <div className="error-page" style={{whiteSpace:"pre-line"}} data-testid="sj-load-error">{String(loadErr)}</div>;
  if (!order) return null;

  const sjNumber = order.delivery_note_number || `SJ-${order.po_number.replace("PO-", "")}`;
  const isDelivered = order.delivery_status === "Terkirim";

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
            <p><b>Deadline PO:</b> {formatDeadline(order.deadline)}</p>
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

        {/* Loading Checklist (interactive on screen, static-look on print) */}
        <section className="sj-checklist" data-testid="sj-checklist">
          <h4>Checklist Loading &amp; Pengiriman</h4>
          <div className="sj-form">
            <label className="sj-field">
              <span>No. Kendaraan</span>
              <input
                type="text"
                value={vehiclePlate}
                onChange={(e) => setVehiclePlate(e.target.value)}
                disabled={!canEditChecklist || busy}
                data-testid="sj-vehicle-input"
                placeholder="B 1234 XYZ"
              />
            </label>
            <label className="sj-field">
              <span>Nama Driver</span>
              <input
                type="text"
                value={driverName}
                onChange={(e) => setDriverName(e.target.value)}
                disabled={!canEditChecklist || busy}
                data-testid="sj-driver-input"
                placeholder="Nama pengemudi"
              />
            </label>
            <label className="sj-check">
              <input
                type="checkbox"
                checked={itemsLoaded}
                onChange={(e) => setItemsLoaded(e.target.checked)}
                disabled={!canEditChecklist || busy}
                data-testid="sj-items-loaded"
              />
              <span>Barang sudah dimuat sesuai qty</span>
            </label>
            <label className="sj-check">
              <input
                type="checkbox"
                checked={sealOk}
                onChange={(e) => setSealOk(e.target.checked)}
                disabled={!canEditChecklist || busy}
                data-testid="sj-seal-ok"
              />
              <span>Segel / seal bagus &amp; utuh</span>
            </label>
            <label className="sj-field sj-notes">
              <span>Catatan loading</span>
              <textarea
                rows={2}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                disabled={!canEditChecklist || busy}
                data-testid="sj-notes-input"
                placeholder="Kondisi khusus, retur, dst."
              />
            </label>
          </div>

          {canEditChecklist && (
            <div className="no-print sj-actions">
              <button className="primary" onClick={saveChecklist} disabled={busy} data-testid="sj-save-checklist">
                <Save size={14} /> Simpan checklist
              </button>
              {canMarkDelivered && !isDelivered && (
                <button className="ghost" onClick={markDelivered} disabled={busy} data-testid="sj-mark-delivered">
                  <CheckCircle2 size={14} /> Tandai Terkirim
                </button>
              )}
            </div>
          )}

          {/* Inline action error banner - halaman tetap ter-render walau action gagal */}
          {actionErr && (
            <div
              className="no-print"
              role="alert"
              data-testid="sj-action-error"
              style={{
                marginTop: 10,
                padding: "10px 14px",
                border: "1px solid #f5c2c7",
                background: "#f8d7da",
                color: "#842029",
                borderRadius: 8,
                whiteSpace: "pre-line",
                display: "flex",
                alignItems: "flex-start",
                gap: 10,
              }}
            >
              <XCircle size={16} style={{ flexShrink: 0, marginTop: 2 }} />
              <div style={{ flex: 1 }}>{String(actionErr)}</div>
              <button
                type="button"
                className="ghost sm"
                onClick={() => setActionErr("")}
                data-testid="sj-action-error-dismiss"
                style={{ marginLeft: 8 }}
              >
                Tutup
              </button>
            </div>
          )}

          <div className="sj-timestamps">
            <div><b>Waktu packing:</b> {packedEvent ? formatDate(packedEvent.at) : "-"}{packedEvent ? <span className="muted small"> · oleh {packedEvent.by_name}</span> : null}</div>
            <div><b>Waktu loading:</b> {loadingEvent ? formatDate(loadingEvent.at) : (order.loading_checklist ? formatDate(order.loading_checklist.updated_at) : "-")}{order.loading_checklist?.updated_by_name ? <span className="muted small"> · oleh {order.loading_checklist.updated_by_name}</span> : null}</div>
            <div>
              <b>Status pengiriman:</b> {isDelivered ? (
                <span className="status-ok"><CheckCircle2 size={12} /> Terkirim</span>
              ) : (
                <span className="status-wait"><XCircle size={12} /> Belum Terkirim</span>
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
