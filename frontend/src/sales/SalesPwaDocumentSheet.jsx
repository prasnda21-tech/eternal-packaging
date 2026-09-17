import { FileText } from "lucide-react";
import { Link } from "react-router-dom";

function Row({ label, available, to, meta }) {
  return (
    <div className="spwa-doc-row">
      <div>
        <strong>{label}</strong>
        {meta ? <div className="spwa-muted" style={{ padding: 0 }}>{meta}</div> : null}
      </div>
      {available && to ? (
        <Link to={to}>Buka</Link>
      ) : (
        <span className="spwa-muted" style={{ padding: 0 }}>Belum ada</span>
      )}
    </div>
  );
}

function viewerPath(orderId, kind, documentId) {
  return `/sales/po/${encodeURIComponent(orderId)}/dokumen/${encodeURIComponent(kind)}/${encodeURIComponent(documentId)}`;
}

export default function SalesPwaDocumentSheet({ orderId, data, onClose }) {
  if (!data) return null;
  return (
    <>
      <button type="button" className="spwa-sheet-backdrop" aria-label="Tutup" onClick={onClose} />
      <aside className="spwa-sheet" role="dialog" aria-label="Dokumen PO" data-testid="sales-doc-sheet">
        <h2>Dokumen {data.po_number}</h2>
        <p className="spwa-muted" style={{ padding: 0, marginTop: 0 }}>{data.customer}</p>
        <Row
          label="Purchase Order"
          available={data.po?.available}
          to={data.po?.available ? viewerPath(orderId, "po", "po") : null}
          meta={data.po?.filename}
        />
        {(data.sj || []).map((sj) => (
          <Row
            key={sj.id}
            label={`Surat Jalan ${sj.number || ""}`.trim()}
            available={sj.available}
            to={sj.available ? viewerPath(orderId, "sj", sj.id) : null}
            meta={sj.date}
          />
        ))}
        {(data.invoices || []).map((inv) => (
          <Row
            key={inv.id}
            label={`Invoice ${inv.number || ""}`.trim()}
            available={inv.available}
            to={inv.available ? viewerPath(orderId, "invoice", inv.id) : null}
            meta={inv.date}
          />
        ))}
        {(data.bukti_kirim || []).map((p) => (
          <Row
            key={p.id}
            label="Bukti kirim"
            available={Boolean(p.storage_path)}
            to={p.storage_path ? viewerPath(orderId, "proof", p.id) : null}
            meta={p.uploaded_at}
          />
        ))}
        {!data.po?.available && !(data.sj || []).length && !(data.invoices || []).length && !(data.bukti_kirim || []).length && (
          <p className="spwa-muted" style={{ padding: "8px 0" }}>Belum ada dokumen.</p>
        )}
        <p className="spwa-muted" style={{ padding: "12px 0 0", display: "flex", alignItems: "center", gap: 6 }}>
          <FileText size={14} /> {data.counts?.total || 0} dokumen
        </p>
      </aside>
    </>
  );
}
