import { FileText } from "lucide-react";
import { publicApiUrl } from "../lib/api";

function Row({ label, available, href, meta }) {
  return (
    <div className="spwa-doc-row">
      <div>
        <strong>{label}</strong>
        {meta ? <div className="spwa-muted" style={{ padding: 0 }}>{meta}</div> : null}
      </div>
      {available && href ? (
        <a href={href} target="_blank" rel="noreferrer">Buka</a>
      ) : (
        <span className="spwa-muted" style={{ padding: 0 }}>Belum ada</span>
      )}
    </div>
  );
}

export default function SalesPwaDocumentSheet({ orderId, data, onClose }) {
  if (!data) return null;
  const base = `/api/sales-pwa/orders/${encodeURIComponent(orderId)}/files`;
  return (
    <>
      <button type="button" className="spwa-sheet-backdrop" aria-label="Tutup" onClick={onClose} />
      <aside className="spwa-sheet" role="dialog" aria-label="Dokumen PO" data-testid="sales-doc-sheet">
        <h2>Dokumen {data.po_number}</h2>
        <p className="spwa-muted" style={{ padding: 0, marginTop: 0 }}>{data.customer}</p>
        <Row
          label="Purchase Order"
          available={data.po?.available}
          href={data.po?.available ? publicApiUrl(`${base}/po`) : null}
          meta={data.po?.filename}
        />
        {(data.sj || []).map((sj) => (
          <Row
            key={sj.id}
            label={`Surat Jalan ${sj.number || ""}`.trim()}
            available={sj.available}
            href={sj.available ? publicApiUrl(`${base}/sj/${encodeURIComponent(sj.id)}`) : null}
            meta={sj.date}
          />
        ))}
        {(data.invoices || []).map((inv) => (
          <Row
            key={inv.id}
            label={`Invoice ${inv.number || ""}`.trim()}
            available={inv.available}
            href={inv.available ? publicApiUrl(`${base}/invoices/${encodeURIComponent(inv.id)}`) : null}
            meta={inv.date}
          />
        ))}
        {(data.bukti_kirim || []).map((p) => (
          <Row
            key={p.id}
            label="Bukti kirim"
            available={Boolean(p.storage_path)}
            href={p.storage_path ? publicApiUrl(`${base}/proofs/${encodeURIComponent(p.id)}`) : null}
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
