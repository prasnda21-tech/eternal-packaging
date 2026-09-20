import { useEffect, useMemo, useState } from "react";
import { AlertCircle } from "lucide-react";
import api from "../lib/api";
import { errDetail } from "../lib/purchaseUi";

function accountLabel(account) {
  if (!account) return "";
  const code = account.code || "";
  const name = account.name || "";
  return `${code} · ${name}`.trim();
}

export default function PurchaseAccountingSettings({ notify, canEdit }) {
  const [settings, setSettings] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [payableId, setPayableId] = useState("");
  const [clearingId, setClearingId] = useState("");
  const [inventoryId, setInventoryId] = useState("");
  const [cogsId, setCogsId] = useState("");
  const [effectiveDate, setEffectiveDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = () => {
    setBusy(true);
    setErr("");
    Promise.all([api.purchaseAccountingSettings(), api.ledgerAccounts()])
      .then(([settingsRes, accountsRes]) => {
        const s = settingsRes.settings || {};
        setSettings(s);
        setPayableId(s.payable_account_id || "");
        setClearingId(s.purchase_clearing_account_id || "");
        setInventoryId(s.inventory_account_id || "");
        setCogsId(s.cogs_account_id || "");
        setEffectiveDate(s.inventory_accounting_effective_date || "");
        setAccounts(accountsRes.items || accountsRes.accounts || []);
      })
      .catch((e) => setErr(errDetail(e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => {
    load();
  }, []);

  const payableOptions = useMemo(
    () => (Array.isArray(accounts) ? accounts.filter((a) => a.account_type === "APAY") : []),
    [accounts]
  );
  const clearingOptions = useMemo(
    () => (Array.isArray(accounts) ? accounts.filter((a) => a.account_type === "OCLY" || a.account_type === "OASS") : []),
    [accounts]
  );
  const inventoryOptions = useMemo(
    () => (Array.isArray(accounts) ? accounts.filter((a) => a.account_type === "INTR") : []),
    [accounts]
  );
  const cogsOptions = useMemo(
    () => (Array.isArray(accounts) ? accounts.filter((a) => a.account_type === "COGS") : []),
    [accounts]
  );

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const payload = {
        payable_account_id: payableId,
        purchase_clearing_account_id: clearingId,
      };
      if (inventoryId || cogsId || effectiveDate) {
        payload.inventory_account_id = inventoryId;
        payload.cogs_account_id = cogsId;
        payload.inventory_accounting_effective_date = effectiveDate;
      }
      const res = await api.updatePurchaseAccountingSettings(payload);
      setSettings(res.settings);
      notify?.(res.message || "Pengaturan Akuntansi Pembelian disimpan");
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-card" data-testid="purchase-accounting-settings">
      <h2>Akuntansi Pembelian</h2>
      <p className="muted small">
        Pemetaan akun untuk jurnal Faktur Pembelian (Dr Purchase Clearing / GRNI, Cr Hutang Usaha).
        Penyimpanan hanya menyimpan ID COA exact. Tidak membuat jurnal otomatis.
      </p>
      {err && (
        <div className="signal-strip" role="alert">
          <AlertCircle size={16} />
          <span>{err}</span>
        </div>
      )}
      <div data-testid="purchase-accounting-ap-section">
        <h3>Hutang Pembelian</h3>
        <div className="settings-grid">
          <label>
            Hutang Usaha
            <select
              value={payableId}
              onChange={(e) => setPayableId(e.target.value)}
              disabled={!canEdit}
              data-testid="purchase-accounting-payable"
            >
              <option value="">Pilih akun APAY</option>
              {payableOptions.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Purchase Clearing / GRNI
            <select
              value={clearingId}
              onChange={(e) => setClearingId(e.target.value)}
              disabled={!canEdit}
              data-testid="purchase-accounting-clearing"
            >
              <option value="">Pilih akun OCLY / OASS</option>
              {clearingOptions.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a)}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <div data-testid="purchase-accounting-inventory-section">
        <h3>Persediaan &amp; HPP</h3>
        <div className="settings-grid">
          <label>
            Persediaan
            <select
              value={inventoryId}
              onChange={(e) => setInventoryId(e.target.value)}
              disabled={!canEdit}
              data-testid="purchase-accounting-inventory"
            >
              <option value="">Pilih akun INTR</option>
              {inventoryOptions.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a)}
                </option>
              ))}
            </select>
          </label>
          <label>
            HPP / COGS
            <select
              value={cogsId}
              onChange={(e) => setCogsId(e.target.value)}
              disabled={!canEdit}
              data-testid="purchase-accounting-cogs"
            >
              <option value="">Pilih akun COGS</option>
              {cogsOptions.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Tanggal Mulai Accounting Persediaan
            <input
              type="date"
              value={effectiveDate}
              onChange={(e) => setEffectiveDate(e.target.value)}
              disabled={!canEdit}
              data-testid="purchase-accounting-effective-date"
            />
          </label>
        </div>
        <p className="muted small" data-testid="purchase-accounting-effective-date-note">
          Tanggal mulai mencegah jurnal Inventory/COGS mundur sebelum tanggal yang dipilih.
          Tidak mengisi otomatis. Accounting Persediaan diakui dari Penerimaan Barang, bukan dari stok fisik.
        </p>
      </div>
      <dl className="confirm-action-dl" data-testid="purchase-accounting-rules">
        <div>
          <dt>Jurnal Faktur</dt>
          <dd>Dr Clearing / Cr Hutang Usaha</dd>
        </div>
        <div>
          <dt>Status pemetaan</dt>
          <dd data-testid="purchase-accounting-configured">
            {settings?.configured ? "Terkonfigurasi" : "Belum dikonfigurasi"}
          </dd>
        </div>
        <div>
          <dt>Status persediaan</dt>
          <dd data-testid="purchase-accounting-inventory-configured">
            {settings?.inventory_configured ? "Terkonfigurasi" : "Belum dikonfigurasi"}
          </dd>
        </div>
      </dl>
      {canEdit && (
        <button className="primary" disabled={busy} onClick={save} data-testid="purchase-accounting-save">
          Simpan pemetaan akun
        </button>
      )}
    </div>
  );
}
