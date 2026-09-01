import { useEffect, useState } from "react";
import { AlertCircle } from "lucide-react";
import api from "../lib/api";
import { errDetail } from "../lib/purchaseUi";
import { formatDate } from "../lib/constants";

export default function HppSettings({ notify, canEdit }) {
  const [settings, setSettings] = useState(null);
  const [method, setMethod] = useState("FIFO");
  const [recognition, setRecognition] = useState("goods_receipt");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const load = () => {
    setBusy(true);
    api.hppSettings().then((d) => {
      const s = d.settings || {};
      setSettings(s);
      setMethod(s.method || "FIFO");
      setRecognition(s.recognition_point || "goods_receipt");
    }).catch((e) => setErr(errDetail(e))).finally(() => setBusy(false));
  };

  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy(true); setErr("");
    try {
      const res = await api.updateHppSettings({ method, recognition_point: recognition });
      setSettings(res.settings);
      setConfirmOpen(false);
      notify?.(res.message || "Pengaturan HPP disimpan");
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-card" data-testid="hpp-settings">
      <h2>Pengaturan HPP / Valuasi Persediaan</h2>
      <p className="muted small">Sumber nilai HPP selalu harga satuan exact line Pesanan Pembelian. Perubahan metode atau titik pengakuan menghitung ulang histori biaya, tanpa mengubah stok fisik atau dokumen sumber.</p>
      {err && <div className="signal-strip" role="alert"><AlertCircle size={16} /><span>{err}</span></div>}
      <div className="settings-grid">
        <label>Metode HPP
          <select value={method} onChange={(e) => setMethod(e.target.value)} disabled={!canEdit} data-testid="hpp-method">
            <option value="FIFO">FIFO</option>
            <option value="RATA-RATA">RATA-RATA</option>
          </select>
        </label>
        <label>Pengakuan HPP
          <select value={recognition} onChange={(e) => setRecognition(e.target.value)} disabled={!canEdit} data-testid="hpp-recognition">
            <option value="purchase_order">Saat Pesanan Pembelian</option>
            <option value="goods_receipt">Saat Penerimaan Barang</option>
          </select>
        </label>
      </div>
      <dl className="confirm-action-dl" data-testid="hpp-recalc-status">
        <div><dt>Metode aktif</dt><dd>{settings?.method || "—"}</dd></div>
        <div><dt>Pengakuan aktif</dt><dd>{settings?.recognition_label || "—"}</dd></div>
        <div><dt>Status hitung ulang</dt><dd data-testid="hpp-last-status">{settings?.last_recalculation_status || "BELUM"}</dd></div>
        <div><dt>Terakhir dihitung</dt><dd>{settings?.last_recalculation_at ? formatDate(settings.last_recalculation_at) : "—"}</dd></div>
        {settings?.last_recalculation_error && <div><dt>Error</dt><dd>{settings.last_recalculation_error}</dd></div>}
      </dl>
      {canEdit && (
        <button className="primary" disabled={busy} onClick={() => setConfirmOpen(true)} data-testid="hpp-save">
          Simpan &amp; hitung ulang histori
        </button>
      )}
      {confirmOpen && (
        <div className="modal" role="dialog" data-testid="hpp-confirm-modal">
          <div className="modal-inner">
            <h3>Hitung ulang histori HPP?</h3>
            <ul>
              <li>Histori HPP akan dihitung ulang secara deterministik.</li>
              <li>Kuantitas stok fisik tidak berubah.</li>
              <li>Dokumen sumber (Permintaan, PO, penerimaan, faktur, pembayaran) tidak diubah.</li>
              <li>Hasil costing untuk laporan dapat berubah.</li>
            </ul>
            <div className="delete-modal-actions">
              <button className="ghost" type="button" disabled={busy} onClick={() => setConfirmOpen(false)}>Batal</button>
              <button className="primary" type="button" disabled={busy} onClick={save} data-testid="hpp-confirm-ok">
                {busy ? "Menghitung…" : "Ya, hitung ulang"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
