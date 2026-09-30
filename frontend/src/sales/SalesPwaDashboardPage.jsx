import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarDays, ChevronRight, ClipboardList, MapPin, TrendingUp, WalletCards } from "lucide-react";
import api from "../lib/api";
import { currentSalesMonth, formatSalesDate, formatSalesMoney, salesMonthBounds } from "../lib/salesPwa";

const VISIT_MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"];
const RECEIVABLE_INFO = "Saldo invoice yang masih belum lunas, termasuk periode sebelumnya.";

function localDay(date = new Date()) {
  const monthNumber = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${monthNumber}-${day}`;
}

function clockLabel(value) {
  const text = String(value || "");
  const match = /T(\d{2}):(\d{2})/.exec(text);
  return match ? `${match[1]}:${match[2]}` : "";
}

function reminderKind(item) {
  if (item?.kind === "follow_up") return "Follow up";
  const kind = String(item?.kind || "").trim();
  return kind;
}

function reminderNote(item) {
  return String(item?.reason || item?.next_action || "").trim();
}

function listedTotal(payload) {
  const total = Number(payload?.total);
  if (Number.isFinite(total)) return total;
  return Array.isArray(payload?.orders) ? payload.orders.length : 0;
}

function visitDateLabel(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
  if (!match) return "";
  const monthName = VISIT_MONTHS[Number(match[2]) - 1];
  if (!monthName) return "";
  return `${match[3]} ${monthName} ${match[1]}`;
}

function latestVisit(rows) {
  if (!Array.isArray(rows) || !rows.length) return null;
  return rows.reduce((best, row) => {
    if (!best) return row;
    return String(row?.activity_at || "") > String(best.activity_at || "") ? row : best;
  }, null);
}

export default function SalesPwaDashboardPage() {
  const [month, setMonth] = useState(currentSalesMonth());
  const [data, setData] = useState(null);
  const [dataMonth, setDataMonth] = useState("");
  const [error, setError] = useState("");
  const [actions, setActions] = useState(null);
  const [actionMonth, setActionMonth] = useState("");
  const [visits, setVisits] = useState(null);
  const [visitsMonth, setVisitsMonth] = useState("");
  const [visitsError, setVisitsError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError("");
    api.salesPwaDashboard({ month })
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setDataMonth(month);
        }
      })
      .catch((err) => { if (!cancelled) setError(err.response?.data?.detail || "Dashboard gagal dimuat"); });
    return () => { cancelled = true; };
  }, [month]);

  useEffect(() => {
    if (typeof api.salesPwaOrders !== "function") return undefined;
    let cancelled = false;
    const bounds = salesMonthBounds(month);
    const list = {
      status: "semua",
      sort: "order_date",
      direction: "desc",
      limit: 4,
    };
    Promise.all([
      api.salesPwaOrders({ ...list, stage: "Approval Design" }),
      api.salesPwaOrders({ ...list, stage: "Approval Payment" }),
      api.salesPwaOrders({ ...list, status: "selesai", stage: "", date_from: bounds.date_from, date_to: bounds.date_to, limit: 1 }),
    ]).then(([design, payment, done]) => {
      if (cancelled) return;
      setActions({
        design: design?.orders || [],
        payment: payment?.orders || [],
        designTotal: listedTotal(design),
        paymentTotal: listedTotal(payment),
        selesai: done?.counts?.selesai ?? done?.total ?? 0,
        ready: true,
      });
      setActionMonth(month);
    }).catch(() => {
      if (!cancelled) {
        setActions({ design: [], payment: [], designTotal: 0, paymentTotal: 0, selesai: null, ready: false });
        setActionMonth(month);
      }
    });
    return () => { cancelled = true; };
  }, [month]);

  useEffect(() => {
    if (typeof api.salesPwaVisits !== "function") {
      setVisits([]);
      setVisitsError(false);
      setVisitsMonth(month);
      return undefined;
    }
    let cancelled = false;
    setVisitsError(false);
    api.salesPwaVisits({ month })
      .then((result) => {
        if (!cancelled) {
          setVisits(Array.isArray(result?.visits) ? result.visits : []);
          setVisitsError(false);
          setVisitsMonth(month);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setVisits([]);
          setVisitsError(true);
          setVisitsMonth(month);
        }
      });
    return () => { cancelled = true; };
  }, [month]);

  const visible = data && dataMonth === month && (!data.month || data.month === month) ? data : null;
  const metrics = visible?.metrics || {};
  const actionVisible = actions && actionMonth === month ? actions : null;
  const actionCount = actionVisible?.ready
    ? Number(actionVisible.designTotal || 0) + Number(actionVisible.paymentTotal || 0)
    : 0;
  const today = localDay();
  const reminders = visible?.upcoming_visits || [];
  const agendaToday = reminders.filter((item) => String(item.date || "").slice(0, 10) === today);
  const followUps = reminders.filter((item) => String(item.date || "").slice(0, 10) !== today);
  const visitHistory = visits && visitsMonth === month ? visits : null;
  const lastVisit = latestVisit(visitHistory);
  return (
    <>
      <header className="spwa-top spwa-top-inline">
        <div><p className="spwa-kicker">PWA SALES</p><h1>Dashboard Saya</h1></div>
        <input aria-label="Bulan dashboard" type="month" value={month} onChange={(event) => { setMonth(event.target.value); setError(""); }} />
      </header>
      <main className="spwa-main" data-testid="sales-pwa-dashboard">
        {error && <div className="spwa-err">{error}</div>}
        {!visible && !error && <div className="spwa-muted">Memuat data penjualan...</div>}
        {visible && (
          <>
            {actionVisible && (
              <section className="spwa-panel spwa-panel-first" data-testid="sales-dashboard-actions">
                <div className="spwa-section-head">
                  <div>
                    <p className="spwa-kicker">LAPANGAN</p>
                    <h2>Butuh aksi</h2>
                    {actionVisible.ready && actionCount > 0 && (
                      <p className="spwa-action-count" data-testid="sales-actions-count">{actionCount} PO perlu tindakan</p>
                    )}
                  </div>
                </div>
                {actionVisible.ready && actionCount === 0 && (
                  <div className="spwa-action-empty" data-testid="sales-actions-empty">
                    <p>Tidak ada PO yang membutuhkan tindakan</p>
                    <p>Semua PO berjalan normal</p>
                  </div>
                )}
                {actionVisible.ready === false && (
                  <p className="spwa-empty compact">Tindakan belum dapat dimuat.</p>
                )}
                {(actionVisible.design || []).map((order) => (
                  <article key={`design-${order.id}`} className="spwa-action-card" data-testid={`sales-action-design-${order.id}`}>
                    <p>Approval Design</p>
                    <strong>{order.customer}</strong>
                    <span>{order.po_number}</span>
                    <small>Status: {order.status}</small>
                    {order.tanggal_kirim_overdue ? <small className="is-urgent">Kirim terlambat</small> : null}
                    <Link to={`/sales/po/${encodeURIComponent(order.id)}`}>Lihat PO Approval Design</Link>
                  </article>
                ))}
                {(actionVisible.payment || []).map((order) => (
                  <article key={`payment-${order.id}`} className="spwa-action-card is-payment" data-testid={`sales-action-payment-${order.id}`}>
                    <p>Approval Payment</p>
                    <strong>{order.customer}</strong>
                    <span>{order.po_number}</span>
                    <small>Status: {order.status}</small>
                    {order.tanggal_kirim_overdue ? <small className="is-urgent">Kirim terlambat</small> : null}
                    <Link to={`/sales/po/${encodeURIComponent(order.id)}`}>Lihat PO Approval Payment</Link>
                  </article>
                ))}
              </section>
            )}

            <section className="spwa-panel" data-testid="sales-dashboard-agenda">
              <div className="spwa-section-head">
                <div><p className="spwa-kicker">AGENDA</p><h2>{agendaToday.length ? `Agenda hari ini (${agendaToday.length})` : "Agenda hari ini"}</h2></div>
                <Link to="/sales/visit">Kalender <ChevronRight size={15} /></Link>
              </div>
              {agendaToday.length === 0 && <p className="spwa-empty compact">Belum ada agenda</p>}
              <div className="spwa-agenda">
                {agendaToday.map((item) => {
                  const clock = clockLabel(item.at);
                  const kind = reminderKind(item);
                  const address = String(item.address || "").trim();
                  return (
                    <Link key={item.id} to="/sales/visit" className={item.overdue ? "is-overdue" : ""}>
                      {clock ? <em className="spwa-agenda-time">{clock}</em> : <CalendarDays size={16} />}
                      <span>
                        <strong>{item.outlet}</strong>
                        <small>{[kind, item.overdue ? "Terlambat" : ""].filter(Boolean).join(" · ") || formatSalesDate(item.date)}</small>
                        {address ? <small className="spwa-agenda-place"><MapPin size={12} /> {address}</small> : null}
                      </span>
                    </Link>
                  );
                })}
              </div>
            </section>

            <section className="spwa-panel" data-testid="sales-dashboard-last-visit">
              <div className="spwa-section-head">
                <div><p className="spwa-kicker">RIWAYAT</p><h2>Visit terakhir</h2></div>
                <Link to="/sales/visit">Visit <ChevronRight size={15} /></Link>
              </div>
              {visitHistory == null && <p className="spwa-empty compact">Memuat riwayat visit...</p>}
              {visitHistory && visitsError && <p className="spwa-empty compact">Riwayat visit belum dapat dimuat.</p>}
              {visitHistory && !visitsError && !lastVisit && <p className="spwa-empty compact" data-testid="sales-last-visit-empty">Tidak ada riwayat visit</p>}
              {visitHistory && !visitsError && lastVisit && (
                <div className="spwa-last-visit" data-testid="sales-last-visit">
                  <span>Customer</span>
                  <strong>{lastVisit.outlet || "Outlet"}</strong>
                  <span>Tanggal</span>
                  <strong>{visitDateLabel(lastVisit.activity_at) || formatSalesDate(lastVisit.activity_at)}</strong>
                  <span>Hasil</span>
                  <strong>{String(lastVisit.result || "").trim() || "—"}</strong>
                </div>
              )}
            </section>

            <section className="spwa-panel spwa-performance" data-testid="sales-dashboard-performance" aria-label="Performance">
              <div className="spwa-section-head">
                <div><p className="spwa-kicker">PERFORMANCE</p><h2>Performance</h2></div>
              </div>
              <div className="spwa-metric-stack">
                <Link
                  to={`/sales/invoices?month=${encodeURIComponent(month)}`}
                  className="spwa-metric is-primary is-revenue spwa-metric-link"
                  data-testid="sales-dashboard-revenue"
                >
                  <TrendingUp size={17} />
                  <span>Omzet bulan ini <ChevronRight size={14} /></span>
                  <strong>{formatSalesMoney(metrics.revenue)}</strong>
                  <small>{metrics.invoice_count || 0} faktur final</small>
                </Link>
                <Link
                  to="/sales/receivables"
                  className="spwa-metric is-debt spwa-metric-link"
                  data-testid="sales-dashboard-receivables"
                  title={RECEIVABLE_INFO}
                >
                  <WalletCards size={17} />
                  <span>Piutang berjalan <ChevronRight size={14} /></span>
                  <strong>{formatSalesMoney(metrics.receivables)}</strong>
                  <small>{metrics.receivable_invoice_count || 0} invoice belum lunas</small>
                  <small>Semua periode</small>
                  <small className="spwa-metric-info">{RECEIVABLE_INFO}</small>
                </Link>
                <div className="spwa-metric-grid">
                  <Link
                    to="/sales/po"
                    state={{ salesPwaPoContext: { active: true } }}
                    className="spwa-metric spwa-metric-link"
                    data-testid="sales-dashboard-active"
                  >
                    <ClipboardList size={17} />
                    <span>PO aktif <ChevronRight size={14} /></span>
                    <strong>{metrics.active_po || 0} PO</strong>
                    <small>{metrics.buffer_po || 0} buffer stock</small>
                  </Link>
                  <Link
                    to="/sales/po"
                    state={{ salesPwaPoContext: { status: "selesai", periode: month } }}
                    className="spwa-metric is-done spwa-metric-link"
                    data-testid="sales-dashboard-done-card"
                  >
                    <ClipboardList size={17} />
                    <span>PO selesai <ChevronRight size={14} /></span>
                    <strong data-testid="sales-dashboard-done">{actionVisible ? (actionVisible.selesai == null ? "—" : `${actionVisible.selesai} PO`) : "…"}</strong>
                    <small>bulan ini</small>
                  </Link>
                </div>
                <Link to="/sales/visit" className="spwa-metric spwa-metric-link" data-testid="sales-dashboard-visits">
                  <MapPin size={17} />
                  <span>Visit bulan ini <ChevronRight size={14} /></span>
                  <strong>{metrics.visit_month || 0}</strong>
                  <small>{metrics.visit_overdue || 0} reminder terlambat</small>
                </Link>
              </div>
            </section>

            <section className="spwa-panel" data-testid="sales-dashboard-followup">
              <div className="spwa-section-head">
                <div><p className="spwa-kicker">FOLLOW UP</p><h2>Follow up customer</h2></div>
                <Link to="/sales/visit">Visit <ChevronRight size={15} /></Link>
              </div>
              {followUps.length === 0 && <p className="spwa-empty compact">Belum ada follow up pada bulan ini.</p>}
              <div className="spwa-agenda">
                {followUps.map((item) => {
                  const note = reminderNote(item);
                  return (
                    <Link key={item.id} to="/sales/visit" className={item.overdue ? "is-overdue" : ""}>
                      <span>
                        <strong>{item.outlet}</strong>
                        <small>Follow up: {formatSalesDate(item.date)}{item.overdue ? " · Terlambat" : ""}</small>
                        {note ? <small>{note}</small> : null}
                      </span>
                    </Link>
                  );
                })}
              </div>
            </section>

            <section className="spwa-source-note">
              <strong>Sumber angka</strong>
              <span>Omzet: faktur final berdasarkan tanggal faktur. Piutang berjalan: faktur final dikurangi penerimaan tercatat, semua periode. Semua data dibatasi oleh sales_user_id akun ini.</span>
            </section>
          </>
        )}
      </main>
    </>
  );
}
