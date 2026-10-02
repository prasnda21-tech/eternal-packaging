import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import api from "../lib/api";
import { formatSalesDate } from "../lib/salesPwa";
import { STATUS_PO_WORKFLOW_STAGES } from "../lib/statusPoWorkflow";
import SalesPwaDesignApproval, { orderExpectsDesignLink } from "./SalesPwaDesignApproval";
import SalesPwaDocumentSheet from "./SalesPwaDocumentSheet";

const DOCUMENTS = [
  { id: "po", label: "PO" },
  { id: "sj", label: "Surat Jalan" },
  { id: "invoice", label: "Invoice" },
  { id: "proof", label: "Bukti Kirim" },
];

function activeStageLabel(order) {
  const key = order?.active_stage;
  const known = STATUS_PO_WORKFLOW_STAGES.find((stage) => stage.key === key);
  if (known) return known.full;
  const workflow = order?.workflow || {};
  const active = STATUS_PO_WORKFLOW_STAGES.find((stage) => workflow[stage.key] === "active" || workflow[stage.key] === "hold");
  return active?.full || "";
}

function processLabel(value) {
  const key = String(value || "").trim().toLowerCase();
  if (!key) return "";
  if (key === "print") return "Cetak";
  if (key === "non_print") return "Tidak Cetak";
  return String(value).trim();
}

function documentTargets(docs, kind) {
  if (!docs) return [];
  if (kind === "po") return docs.po?.available ? [{ id: "po", kind: "po" }] : [];
  if (kind === "sj") {
    return (docs.sj || []).filter((row) => row?.available && row.id).map((row) => ({ id: row.id, kind: "sj" }));
  }
  if (kind === "invoice") {
    return (docs.invoices || []).filter((row) => row?.available !== false && row.id).map((row) => ({ id: row.id, kind: "invoice" }));
  }
  return (docs.bukti_kirim || []).filter((row) => row?.storage_path && row.id).map((row) => ({ id: row.id, kind: "proof" }));
}

function documentCount(order, kind) {
  const counts = order?.documents || {};
  if (kind === "po") return Number(counts.po) || 0;
  if (kind === "sj") return Number(counts.sj) || 0;
  if (kind === "invoice") return Number(counts.invoice) || 0;
  return Number(counts.bukti_kirim) || 0;
}

function viewerPath(orderId, target) {
  return `/sales/po/${encodeURIComponent(orderId)}/dokumen/${encodeURIComponent(target.kind)}/${encodeURIComponent(target.id)}`;
}

export default function SalesPwaOrderDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [docs, setDocs] = useState(null);
  const [err, setErr] = useState("");
  const [sheet, setSheet] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setDocs(null);
    setErr("");
    api.salesPwaOrder(id)
      .then((res) => { if (!cancelled) setData(res); })
      .catch((e) => { if (!cancelled) setErr(e.response?.status === 404 ? "PO tidak ditemukan" : (e.response?.data?.detail || e.message)); });
    api.salesPwaDocuments(id)
      .then((res) => { if (!cancelled) setDocs(res); })
      .catch(() => { if (!cancelled) setDocs(false); });
    return () => { cancelled = true; };
  }, [id]);

  const order = data?.order;
  const viewerState = { salesDocumentFrom: `/sales/po/${encodeURIComponent(id)}` };
  const stageLabel = activeStageLabel(order);
  const note = String(order?.notes || "").trim();
  const recipient = String(order?.recipient || "").trim();
  const address = String(order?.address || "").trim();

  const openKind = async (kind) => {
    let payload = docs && docs !== false ? docs : null;
    if (!payload) {
      try {
        payload = await api.salesPwaDocuments(id);
        setDocs(payload);
      } catch (e) {
        setErr(e.response?.data?.detail || "Dokumen tidak tersedia");
        return;
      }
    }
    const targets = documentTargets(payload, kind);
    if (targets.length === 1) {
      navigate(viewerPath(id, targets[0]), { state: viewerState });
      return;
    }
    if (targets.length > 1) setSheet({ orderId: id, docs: payload });
  };

  return (
    <>
      <header className="spwa-top spwa-detail-top">
        <Link to="/sales/po" className="spwa-back" data-testid="sales-detail-back"><ChevronLeft size={14} /> PO Saya</Link>
        {order ? (
          <div className="spwa-detail-identity">
            <h1 className="spwa-top-customer">{order.customer}</h1>
            <p className="spwa-top-meta">{order.po_number}</p>
            <p className="spwa-top-meta">{formatSalesDate(order.order_date)}</p>
          </div>
        ) : <h1>Detail PO</h1>}
      </header>
      <main className="spwa-main" data-testid="sales-pwa-order-detail">
        {err && <div className="spwa-err">{err}</div>}
        {!order && !err && <div className="spwa-muted">Memuat...</div>}
        {order && (
          <>
            <section className="spwa-card spwa-status-card" data-testid="sales-detail-status">
              <p className="spwa-kicker">STATUS PO</p>
              <h2>{order.status}</h2>
              <p className="spwa-status-progress">Progress {order.progress_percent ?? 0}%</p>
              {stageLabel ? <p className="spwa-status-field"><span>Tahap aktif</span>{stageLabel}</p> : null}
              {order.tanggal_kirim ? <p className="spwa-status-field"><span>Pengiriman</span>{formatSalesDate(order.tanggal_kirim)}</p> : null}
              {order.tanggal_kirim_overdue ? <p className="spwa-po-card-urgency">Kirim terlambat</p> : null}
              {order.buffer_stock ? <span className="spwa-buffer-badge spwa-buffer-status">Buffer Stock</span> : null}
              <SalesPwaDesignApproval order={order} expectMissing={orderExpectsDesignLink(order)} />
            </section>
            {address ? (
              <section className="spwa-card" data-testid="sales-detail-customer">
                <h2>Informasi Customer</h2>
                <p className="spwa-status-meta">{address}</p>
              </section>
            ) : null}
            {(order.line_items || []).length > 0 && (
              <section className="spwa-card" data-testid="sales-detail-items">
                <h2>Item</h2>
                {order.line_items.map((li) => {
                  const color = String(li.color || "").trim();
                  const process = processLabel(li.production_process);
                  const qty = [li.quantity, li.unit].filter((part) => part !== undefined && part !== null && String(part).trim() !== "").join(" ");
                  return (
                    <article key={li.idx} className="spwa-item-card">
                      <strong>{li.name}</strong>
                      {qty ? <p className="spwa-item-qty">{qty}</p> : null}
                      {(color || process) ? (
                        <div className="spwa-item-detail">
                          <span>Detail</span>
                          {color ? <small>Warna: {color}</small> : null}
                          {process ? <small>{process}</small> : null}
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </section>
            )}
            <section className="spwa-card" data-testid="sales-detail-docs">
              <h2>Dokumen</h2>
              {DOCUMENTS.map((doc) => {
                const targets = docs && docs !== false ? documentTargets(docs, doc.id) : null;
                const available = targets ? targets.length > 0 : documentCount(order, doc.id) > 0;
                const direct = targets && targets.length === 1 ? targets[0] : null;
                return (
                  <div key={doc.id} className="spwa-doc-line" data-testid={`sales-doc-row-${doc.id}`}>
                    <div>
                      <strong>{doc.label}</strong>
                      <span className={available ? "is-ready" : "spwa-doc-missing"}>{available ? "Tersedia" : "Belum ada"}</span>
                    </div>
                    {direct ? (
                      <Link className="spwa-detail-btn spwa-doc-action" to={viewerPath(id, direct)} state={viewerState} data-testid={`sales-doc-open-${doc.id}`}>Buka Dokumen</Link>
                    ) : available ? (
                      <button type="button" className="spwa-detail-btn spwa-doc-action" data-testid={`sales-doc-open-${doc.id}`} onClick={() => openKind(doc.id)}>Buka Dokumen</button>
                    ) : null}
                  </div>
                );
              })}
              {DOCUMENTS.every((doc) => {
                const targets = docs && docs !== false ? documentTargets(docs, doc.id) : null;
                const available = targets ? targets.length > 0 : documentCount(order, doc.id) > 0;
                return !available;
              }) ? <p className="spwa-doc-empty">Belum ada dokumen tersedia</p> : null}
            </section>
            {(note || recipient) && (
              <section className="spwa-card" data-testid="sales-detail-notes">
                <h2>Catatan</h2>
                {recipient ? <p className="spwa-status-meta">Penerima: {recipient}</p> : null}
                {note ? <p className="spwa-status-meta">{note}</p> : null}
              </section>
            )}
          </>
        )}
      </main>
      {sheet && (
        <SalesPwaDocumentSheet orderId={sheet.orderId} data={sheet.docs} onClose={() => setSheet(null)} />
      )}
    </>
  );
}
