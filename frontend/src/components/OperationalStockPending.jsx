import { useCallback, useEffect, useRef, useState } from "react";
import api from "../lib/api";

const stages = {operator: "Cetak", packing: "Packing", loading: "Loading", delivery: "Terkirim", sales_delivery: "Surat Jalan"};
export default function OperationalStockPending({canRetry, onApplied}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef(0);
  const load = useCallback(async () => {
    const seq = ++request.current;
    setBusy(true); setError("");
    try {
      const result = await api.operationalStockPending();
      if (seq === request.current) setItems(result.items || []);
    } catch (ex) {
      if (seq === request.current) setError(ex.response?.status === 403 ? "Akses ditolak." : "Antrean stok belum dapat dimuat.");
    } finally {
      if (seq === request.current) setBusy(false);
    }
  }, []);
  useEffect(() => { if (open) load(); return () => { request.current += 1; }; }, [open, load]);
  const retry = async (row) => {
    setBusy(true); setError("");
    try {
      const result = await api.retryOperationalStock(row.order_id, row.intent_id, row.source);
      await load();
      if (result.status === "applied") onApplied?.();
    } catch (ex) {
      setError(typeof ex.response?.data?.detail === "string" ? ex.response.data.detail : "Pencatatan belum berhasil. Catatan tertunda tetap disimpan.");
      setBusy(false);
    }
  };
  return <section aria-label="Pencatatan stok tertunda" style={{marginBottom: 16}}>
    <button className="ghost" type="button" onClick={() => setOpen(v => !v)} aria-expanded={open}>Pencatatan stok tertunda</button>
    {open && <div style={{padding: 16, border: "1px solid #e2e8f0", borderRadius: 8, marginTop: 8}}>
      <p>Progres operasional tetap tersimpan. Antrean ini bukan saldo atau jurnal baru. Menampilkan paling banyak 100 pekerjaan tertunda.</p>
      <button type="button" className="ghost" disabled={busy} onClick={load}>Muat ulang</button>
      {error && <p role="alert">{error}</p>}
      {busy && <p role="status">Memproses…</p>}
      {!busy && !error && !items.length && <p>Tidak ada pekerjaan tertunda pada antrean ini.</p>}
      <div style={{overflowX: "auto"}}><table style={{width: "100%"}}>
        <thead><tr><th>PO</th><th>Tahap</th><th>Catatan stok</th>{canRetry && <th>Aksi</th>}</tr></thead>
        <tbody>{items.map(row => <tr key={row.intent_id}>
          <td>{row.po_number || row.order_id}</td><td>{stages[row.stage] || row.stage}</td><td>{row.reason}
            {row.next_action && <small style={{display: "block", marginTop: 4}}>{row.next_action}</small>}
          </td>
          {canRetry && <td>{row.status !== "review_required" && <button type="button" className="ghost" disabled={busy} onClick={() => retry(row)}>Coba catat ulang</button>}</td>}
        </tr>)}</tbody>
      </table></div>
    </div>}
  </section>;
}
