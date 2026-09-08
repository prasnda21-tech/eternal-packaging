import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, HelpCircle, Info, Lock, ShieldAlert } from "lucide-react";
import api from "../lib/api";
import { formatDate } from "../lib/constants";
import { errDetail } from "../lib/purchaseUi";

export const STOCK_IN_OPTIONS = [
  {
    value: "goods_receipt",
    label: "Penerimaan Barang",
    desc: "Stok bertambah ketika dokumen Penerimaan Barang dicatat/diterima.",
  },
  {
    value: "purchase_order",
    label: "Pesanan Pembelian",
    desc: "Stok bertambah langsung saat Pesanan Pembelian dibuat/disetujui.",
  },
];

export const STOCK_OUT_OPTIONS = [
  {
    value: "loading_confirmation",
    label: "Loading Dikonfirmasi",
    desc: "Stok berkurang ketika barang selesai dimuat dan dikonfirmasi di tahap Loading.",
  },
  {
    value: "delivery_confirmed",
    label: "Pengiriman Dikonfirmasi",
    desc: "Stok berkurang saat barang telah diterima oleh customer (Terkirim).",
  },
  {
    value: "packing_completed",
    label: "Packing Selesai",
    desc: "Stok berkurang saat barang selesai dikemas di tahap Packing.",
  },
];

export default function PhysicalStockSettings({ notify, canEdit }) {
  const [settings, setSettings] = useState(null);
  const [enabled, setEnabled] = useState(false);
  const [stockInTrigger, setStockInTrigger] = useState("goods_receipt");
  const [stockOutTrigger, setStockOutTrigger] = useState("loading_confirmation");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = () => {
    setBusy(true);
    setErr("");
    api.physicalStockSettings()
      .then((res) => {
        const s = res?.settings || {};
        setSettings(s);
        setEnabled(Boolean(s.enabled));
        setStockInTrigger(s.stock_in_trigger || "goods_receipt");
        setStockOutTrigger(s.stock_out_trigger || "loading_confirmation");
      })
      .catch((e) => {
        setErr(errDetail(e));
      })
      .finally(() => {
        setBusy(false);
      });
  };

  useEffect(() => {
    load();
  }, []);

  const save = async (e) => {
    e?.preventDefault();
    if (!canEdit || busy) return;
    setBusy(true);
    setErr("");
    try {
      const payload = {
        enabled: Boolean(enabled),
        stock_in_trigger: stockInTrigger,
        stock_out_trigger: stockOutTrigger,
      };
      const res = await api.updatePhysicalStockSettings(payload);
      setSettings(res.settings);
      notify?.(res.message || "Pengaturan stok fisik berhasil disimpan");
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const activeInOption = STOCK_IN_OPTIONS.find((o) => o.value === stockInTrigger) || STOCK_IN_OPTIONS[0];
  const activeOutOption = STOCK_OUT_OPTIONS.find((o) => o.value === stockOutTrigger) || STOCK_OUT_OPTIONS[0];

  return (
    <div className="settings-card" data-testid="physical-stock-settings">
      <div className="settings-card-header" style={{ marginBottom: 16 }}>
        <h2>Pengaturan Stok Fisik</h2>
        <p className="muted small" style={{ marginTop: 4 }}>
          Konfigurasi otomatisasi pengakuan penambahan dan pengurangan stok fisik gudang Eternal.
        </p>
      </div>

      <div className="settings-info" data-testid="physical-stock-disclaimer" style={{ marginBottom: 16 }}>
        <Info size={16} />
        <div>
          <p><strong>Pengaturan stok fisik terpisah dari Pengakuan HPP.</strong></p>
          <p className="muted small" style={{ marginTop: 2 }}>
            Perubahan pengaturan tidak menghitung ulang transaksi lama. Transaksi sebelumnya tetap mengikuti snapshot pengakuan saat transaksi diproses.
          </p>
        </div>
      </div>

      {err && (
        <div className="signal-strip" role="alert" data-testid="physical-stock-error" style={{ marginBottom: 16 }}>
          <AlertCircle size={16} />
          <span>{err}</span>
        </div>
      )}

      <form onSubmit={save}>
        <div className="settings-grid" style={{ marginBottom: 20 }}>
          <div className="settings-field-group">
            <label htmlFor="physical-stock-automation" style={{ fontWeight: 600 }}>
              Otomatisasi Stok Fisik
            </label>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 6 }}>
              <select
                id="physical-stock-automation"
                data-testid="physical-stock-enabled"
                value={enabled ? "active" : "inactive"}
                onChange={(e) => setEnabled(e.target.value === "active")}
                disabled={!canEdit || busy}
                style={{ width: "auto", minWidth: 160 }}
              >
                <option value="inactive">Nonaktif</option>
                <option value="active">Aktif</option>
              </select>
              <span
                data-testid="physical-stock-status-badge"
                className={`badge ${enabled ? "badge-success" : "badge-neutral"}`}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "4px 8px",
                  borderRadius: 4,
                  fontSize: 12,
                  fontWeight: 600,
                  backgroundColor: enabled ? "#ecfdf5" : "#f1f5f9",
                  color: enabled ? "#065f46" : "#475569",
                  border: `1px solid ${enabled ? "#a7f3d0" : "#cbd5e1"}`,
                }}
              >
                {enabled ? <CheckCircle2 size={12} /> : <Lock size={12} />}
                {enabled ? "Aktif" : "Nonaktif"}
              </span>
            </div>
            <p className="muted small" style={{ marginTop: 6 }}>
              {enabled
                ? "Otomatisasi aktif. Mutasi fisik akan dicatat sesuai trigger event yang dipilih."
                : "Otomatisasi dinonaktifkan. Belum ada mutasi stok fisik otomatis yang terjadi ke ledger."}
            </p>
          </div>
        </div>

        <div className="settings-grid" style={{ marginBottom: 20 }}>
          <div className="settings-field-group">
            <label htmlFor="stock-in-trigger" style={{ fontWeight: 600 }}>
              Stok Bertambah Ketika
            </label>
            <select
              id="stock-in-trigger"
              data-testid="stock-in-trigger-select"
              value={stockInTrigger}
              onChange={(e) => setStockInTrigger(e.target.value)}
              disabled={!canEdit || busy}
              style={{ marginTop: 6 }}
            >
              {STOCK_IN_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            <p className="muted small" data-testid="stock-in-trigger-desc" style={{ marginTop: 6 }}>
              {activeInOption.desc}
            </p>
          </div>

          <div className="settings-field-group">
            <label htmlFor="stock-out-trigger" style={{ fontWeight: 600 }}>
              Stok Berkurang Ketika
            </label>
            <select
              id="stock-out-trigger"
              data-testid="stock-out-trigger-select"
              value={stockOutTrigger}
              onChange={(e) => setStockOutTrigger(e.target.value)}
              disabled={!canEdit || busy}
              style={{ marginTop: 6 }}
            >
              {STOCK_OUT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            <p className="muted small" data-testid="stock-out-trigger-desc" style={{ marginTop: 6 }}>
              {activeOutOption.desc}
            </p>
          </div>
        </div>

        <div
          className="settings-card-sub"
          data-testid="status-cetak-rule-card"
          style={{
            marginTop: 20,
            padding: 16,
            background: "#f8fafc",
            borderRadius: 6,
            border: "1px solid #e2e8f0",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Perubahan Status Cetak</h3>
            <span
              style={{
                fontSize: 11,
                padding: "2px 6px",
                borderRadius: 4,
                backgroundColor: "#e2e8f0",
                color: "#475569",
                fontWeight: 600,
              }}
            >
              Aturan Baku (Read-only)
            </span>
          </div>
          <p className="muted small" style={{ marginBottom: 12 }}>
            Proses cetak mengubah status inventaris cup dari Belum Dicetak menjadi Sudah Dicetak tanpa mengubah total persediaan fisik.
          </p>

          <table className="prod-table" data-testid="status-cetak-table" style={{ fontSize: 13, width: "100%", background: "#ffffff" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", width: "35%" }}>Tahap Pemicu</th>
                <th style={{ textAlign: "left", width: "30%" }}>Perubahan Status</th>
                <th style={{ textAlign: "left", width: "35%" }}>Dampak Fisik</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style={{ fontWeight: 600 }}>Operator Selesai</td>
                <td>
                  <span style={{ color: "#b91c1c", fontWeight: 600 }}>Belum Dicetak</span>
                  {" → "}
                  <span style={{ color: "#047857", fontWeight: 600 }}>Sudah Dicetak</span>
                </td>
                <td className="small mono">
                  Total: <strong>0</strong> | Belum: <strong>-Qty</strong> | Sudah: <strong>+Qty</strong>
                </td>
              </tr>
            </tbody>
          </table>
          <p className="muted small" style={{ marginTop: 8, fontSize: 11 }}>
            * Item NON CETAK / TIDAK CETAK tidak pernah mengalami perubahan status cetak.
          </p>
        </div>

        <dl className="confirm-action-dl" data-testid="physical-stock-audit" style={{ marginTop: 20 }}>
          <div>
            <dt>Status otomatisasi aktif</dt>
            <dd>{settings?.enabled ? "Aktif" : "Nonaktif"}</dd>
          </div>
          <div>
            <dt>Trigger masuk aktif</dt>
            <dd>{settings?.stock_in_label || settings?.stock_in_trigger || "—"}</dd>
          </div>
          <div>
            <dt>Trigger keluar aktif</dt>
            <dd>{settings?.stock_out_label || settings?.stock_out_trigger || "—"}</dd>
          </div>
          <div>
            <dt>Terakhir disimpan</dt>
            <dd>{settings?.updated_at ? formatDate(settings.updated_at) : "Belum pernah disimpan"}</dd>
          </div>
          {settings?.updated_by?.name && (
            <div>
              <dt>Disimpan oleh</dt>
              <dd>{settings.updated_by.name} ({settings.updated_by.email || settings.updated_by.role || "User"})</dd>
            </div>
          )}
        </dl>

        {canEdit && (
          <div style={{ marginTop: 20 }}>
            <button
              type="submit"
              className="primary"
              disabled={busy}
              data-testid="physical-stock-save"
            >
              {busy ? "Menyimpan…" : "Simpan Pengaturan"}
            </button>
          </div>
        )}
      </form>
    </div>
  );
}
