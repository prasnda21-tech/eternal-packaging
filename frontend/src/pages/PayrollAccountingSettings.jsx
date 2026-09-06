import { useEffect, useMemo, useState } from "react";
import { AlertCircle } from "lucide-react";
import api from "../lib/api";
import { errDetail } from "../lib/purchaseUi";

const TREATMENTS = [
  { id: "expense_debit", label: "Beban / Cost", kinds: ["earning"] },
  { id: "expense_credit", label: "Pengurang Beban", kinds: ["deduction"] },
  { id: "liability_credit", label: "Kewajiban", kinds: ["deduction"] },
  { id: "asset_credit", label: "Pengurang Aset", kinds: ["deduction"] },
];

function accountLabel(account) {
  if (!account) return "";
  return `${account.code || ""} · ${account.name || ""}`.trim();
}

function isExpenseEligible(account) {
  return account?.statement === "INCOME_STATEMENT" && account?.normal_balance === "debit";
}

function isLiabilityEligible(account) {
  return account?.statement === "BALANCE_SHEET" && account?.report_group === "liability";
}

function isAssetEligible(account) {
  return account?.statement === "BALANCE_SHEET" && account?.report_group === "asset";
}

function accountsForTreatment(accounts, treatment) {
  if (treatment === "expense_debit" || treatment === "expense_credit") return accounts.filter(isExpenseEligible);
  if (treatment === "liability_credit") return accounts.filter(isLiabilityEligible);
  if (treatment === "asset_credit") return accounts.filter(isAssetEligible);
  return [];
}

function treatmentsForKind(kind) {
  if (kind === "earning") return TREATMENTS.filter((t) => t.kinds.includes("earning"));
  if (kind === "deduction") return TREATMENTS.filter((t) => t.kinds.includes("deduction"));
  return TREATMENTS;
}

export default function PayrollAccountingSettings({ notify, canEdit }) {
  const [settings, setSettings] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [payableId, setPayableId] = useState("");
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = () => {
    setBusy(true);
    setErr("");
    Promise.all([api.payrollAccountingSettings(), api.ledgerAccounts()])
      .then(([settingsRes, accountsRes]) => {
        const s = settingsRes.settings || {};
        setSettings(s);
        setPayableId(s.payroll_payable_account_id || "");
        setAccounts(accountsRes.items || accountsRes.accounts || []);
        setRows((s.catalog || []).map((row) => ({
          source_key: row.source_key,
          label: row.label,
          kind: row.kind,
          source: row.source,
          code: row.code,
          treatment: row.treatment || "",
          account_id: row.account_id || "",
          mapped: Boolean(row.mapped),
        })));
      })
      .catch((e) => setErr(errDetail(e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => {
    load();
  }, []);

  const payableOptions = useMemo(
    () => (Array.isArray(accounts) ? accounts.filter(isLiabilityEligible) : []),
    [accounts]
  );

  const updateRow = (sourceKey, patch) => {
    setRows((cur) => cur.map((row) => (row.source_key === sourceKey ? { ...row, ...patch } : row)));
  };

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const component_mappings = {};
      rows.forEach((row) => {
        if (row.treatment && row.account_id) {
          component_mappings[row.source_key] = { treatment: row.treatment, account_id: row.account_id };
        }
      });
      const res = await api.updatePayrollAccountingSettings({
        payroll_payable_account_id: payableId,
        component_mappings,
      });
      setSettings(res.settings);
      notify?.(res.message || "Pengaturan Akuntansi Payroll disimpan");
      load();
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-card" data-testid="payroll-accounting-settings">
      <h2>Akuntansi Payroll</h2>
      <p className="muted small">
        Pemetaan akun untuk kesiapan jurnal payroll. Hanya ID COA exact yang disimpan.
        Tidak membuat jurnal, tidak memilih akun otomatis, dan tidak menafsirkan PPh21/BPJS.
      </p>
      {err && (
        <div className="signal-strip" role="alert">
          <AlertCircle size={16} />
          <span>{err}</span>
        </div>
      )}
      <div className="settings-grid">
        <label>
          Payroll Payable
          <select
            value={payableId}
            onChange={(e) => setPayableId(e.target.value)}
            disabled={!canEdit}
            data-testid="payroll-accounting-payable"
          >
            <option value="">Pilih akun liabilitas</option>
            {payableOptions.map((a) => (
              <option key={a.id} value={a.id}>{accountLabel(a)}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="table-wrap">
        <table className="ship-table" data-testid="payroll-accounting-mapping-table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Jenis</th>
              <th>Treatment</th>
              <th>Akun</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const treatments = treatmentsForKind(row.kind);
              const accountOptions = accountsForTreatment(accounts, row.treatment);
              return (
                <tr key={row.source_key} data-source-key={row.source_key}>
                  <td>
                    <div>{row.label}</div>
                    <div className="muted small">{row.source_key}</div>
                  </td>
                  <td>{row.kind || row.source}</td>
                  <td>
                    <select
                      value={row.treatment}
                      disabled={!canEdit}
                      onChange={(e) => updateRow(row.source_key, { treatment: e.target.value, account_id: "" })}
                    >
                      <option value="">Pilih</option>
                      {treatments.map((t) => (
                        <option key={t.id} value={t.id}>{t.label}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select
                      value={row.account_id}
                      disabled={!canEdit || !row.treatment}
                      onChange={(e) => updateRow(row.source_key, { account_id: e.target.value })}
                    >
                      <option value="">Pilih akun</option>
                      {accountOptions.map((a) => (
                        <option key={a.id} value={a.id}>{accountLabel(a)}</option>
                      ))}
                    </select>
                  </td>
                  <td>{row.treatment && row.account_id ? "Terisi" : "Belum"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small" data-testid="payroll-accounting-configured">
        {settings?.payroll_payable_valid ? "Utang gaji terisi" : "Belum dikonfigurasi"}
      </p>
      {canEdit && (
        <button className="primary" disabled={busy} onClick={save} data-testid="payroll-accounting-save">
          Simpan
        </button>
      )}
    </div>
  );
}
