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

function isOutputTaxEligible(account) {
  return account?.statement === "BALANCE_SHEET" && account?.report_group === "liability";
}

export default function SalesAccountingSettings({ notify, canEdit }) {
  const [settings, setSettings] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [receivableId, setReceivableId] = useState("");
  const [revenueId, setRevenueId] = useState("");
  const [outputTaxId, setOutputTaxId] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = () => {
    setBusy(true);
    setErr("");
    Promise.all([api.salesAccountingSettings(), api.ledgerAccounts()])
      .then(([settingsRes, accountsRes]) => {
        const s = settingsRes.settings || {};
        setSettings(s);
        setReceivableId(s.receivable_account_id || "");
        setRevenueId(s.revenue_account_id || "");
        setOutputTaxId(s.output_tax_account_id || "");
        setAccounts(accountsRes.items || accountsRes.accounts || []);
      })
      .catch((e) => setErr(errDetail(e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => {
    load();
  }, []);

  const receivableOptions = useMemo(
    () => (Array.isArray(accounts) ? accounts.filter((a) => a.account_type === "AREC") : []),
    [accounts]
  );
  const revenueOptions = useMemo(
    () => (Array.isArray(accounts) ? accounts.filter((a) => a.account_type === "REVE") : []),
    [accounts]
  );
  const outputTaxOptions = useMemo(
    () => (Array.isArray(accounts) ? accounts.filter(isOutputTaxEligible) : []),
    [accounts]
  );

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const res = await api.updateSalesAccountingSettings({
        receivable_account_id: receivableId,
        revenue_account_id: revenueId,
        output_tax_account_id: outputTaxId,
      });
      setSettings(res.settings);
      notify?.(res.message || "Pengaturan Akuntansi Penjualan disimpan");
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-card" data-testid="sales-accounting-settings">
      <h2>Akuntansi Penjualan</h2>
      <p className="muted small">
        Pemetaan akun untuk kesiapan jurnal penjualan. Penyimpanan hanya menyimpan ID COA exact.
        Tidak membuat jurnal dan tidak memilih akun otomatis.
      </p>
      {err && (
        <div className="signal-strip" role="alert">
          <AlertCircle size={16} />
          <span>{err}</span>
        </div>
      )}
      <div className="settings-grid">
        <label>
          Piutang Usaha
          <select
            value={receivableId}
            onChange={(e) => setReceivableId(e.target.value)}
            disabled={!canEdit}
            data-testid="sales-accounting-receivable"
          >
            <option value="">Pilih akun AREC</option>
            {receivableOptions.map((a) => (
              <option key={a.id} value={a.id}>
                {accountLabel(a)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Pendapatan Penjualan
          <select
            value={revenueId}
            onChange={(e) => setRevenueId(e.target.value)}
            disabled={!canEdit}
            data-testid="sales-accounting-revenue"
          >
            <option value="">Pilih akun REVE</option>
            {revenueOptions.map((a) => (
              <option key={a.id} value={a.id}>
                {accountLabel(a)}
              </option>
            ))}
          </select>
        </label>
        <label>
          PPN Keluaran
          <select
            value={outputTaxId}
            onChange={(e) => setOutputTaxId(e.target.value)}
            disabled={!canEdit}
            data-testid="sales-accounting-output-tax"
          >
            <option value="">Pilih akun liabilitas Neraca</option>
            {outputTaxOptions.map((a) => (
              <option key={a.id} value={a.id}>
                {accountLabel(a)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <dl className="confirm-action-dl" data-testid="sales-accounting-rules">
        <div>
          <dt>Diskon</dt>
          <dd>Net ke Pendapatan</dd>
        </div>
        <div>
          <dt>Status pemetaan</dt>
          <dd data-testid="sales-accounting-configured">
            {settings?.configured ? "Terkonfigurasi" : "Belum dikonfigurasi"}
          </dd>
        </div>
      </dl>
      {canEdit && (
        <button className="primary" disabled={busy} onClick={save} data-testid="sales-accounting-save">
          Simpan pemetaan akun
        </button>
      )}
    </div>
  );
}
