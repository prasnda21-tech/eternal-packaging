import { formatMoney } from "../../lib/financialDashboard";

export default function CashAvailabilityBody({ model, onRetry }) {
  const updated = model.generated_at && Number.isFinite(Date.parse(model.generated_at))
    ? new Intl.DateTimeFormat("id-ID", { timeZone: "Asia/Jakarta", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(model.generated_at)) + " WIB" : "—";
  return <div className="kb-kpi">
    <p className="kb-metric">{model.total == null ? "—" : formatMoney(model.total)}</p>
    <p className="kb-kicker">Saldo seluruh kas dan bank saat ini</p>
    {model.status === "incomplete" ? <p role="status" className="kb-kicker">{model.detail}{model.known_total == null ? "" : ` Subtotal rekening terhubung: ${formatMoney(model.known_total)}`}</p> : null}
    <div className="kb-lines">
      {(model.rows || []).map((row, index) => <div key={row.account_id || row.cash_bank_id || index}>
        <span>{row.name || row.code}</span><strong>{row.balance == null ? "Belum terhubung" : formatMoney(row.balance)}</strong>
      </div>)}
    </div>
    <p className="kb-kicker">Diperbarui {updated}</p>
    <button type="button" className="kb-btn" onClick={onRetry}>Muat ulang saldo</button>
  </div>;
}
