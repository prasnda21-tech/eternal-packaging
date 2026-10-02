import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarDays, ChevronRight, ClipboardList, MapPin, TrendingUp, WalletCards } from "lucide-react";
import api from "../lib/api";
import { currentSalesMonth, formatSalesDate, formatSalesMoney, salesMonthBounds, salesWibParts } from "../lib/salesPwa";
import SalesPwaDesignApproval, { salesDesignApprovalPath } from "./SalesPwaDesignApproval";

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
  if (item?.kind === "schedule") return "Jadwal";
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
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(salesWibParts(value)?.day || "");
  if (!match) return "";
  const monthName = VISIT_MONTHS[Number(match[2]) - 1];
  if (!monthName) return "";
  return `${match[3]} ${monthName} ${match[1]}`;
}

const AGENDA_SECTION_LIMIT = 30;

function classifyReminders(rows, today) {
  const agenda = [];
  const overdue = [];
  const upcoming = [];
  for (const item of rows || []) {
    const day = String(item?.date || "").slice(0, 10);
    if (!day) continue;
    if (day === today) agenda.push(item);
    else if (day < today || item?.overdue) overdue.push(item);
    else upcoming.push(item);
  }
  return {
    agenda: agenda.slice(0, AGENDA_SECTION_LIMIT),
    overdue: overdue.slice(0, AGENDA_SECTION_LIMIT),
    upcoming: upcoming.slice(0, AGENDA_SECTION_LIMIT),
  };
}

function latestVisit(rows) {
  if (!Array.isArray(rows) || !rows.length) return null;
  return rows.reduce((best, row) => {
    if (!best) return row;
    return String(row?.activity_at || "") > String(best.activity_at || "") ? row : best;
  }, null);
}

function sameStageLabel(status, title) {
  return String(status || "").trim().toLowerCase() === String(title || "").trim().toLowerCase();
}

function FieldStagePreview({ testKey, title, orders, total, stage, payment, showApproval }) {
  const rows = Array.isArray(orders) ? orders : [];
  const count = Number(total);
  const shownTotal = Number.isFinite(count) ? count : rows.length;
  if (shownTotal <= 0 && rows.length === 0) return null;
  return (
    <div className="spwa-stage-block" data-testid={`sales-action-${testKey}-block`}>
      <p className="spwa-stage-total" data-testid={`sales-action-${testKey}-total`}>{shownTotal} PO {title}</p>
      {rows.map((order) => {
        const hasApprovalPath = (order.design_approvals || []).some((row) => salesDesignApprovalPath(row));
        return (
          <article
            key={`${testKey}-${order.id}`}
            className={`spwa-action-card${payment ? " is-payment" : ""}`}
            data-testid={`sales-action-${testKey}-${order.id}`}
          >
            <strong>{order.customer}</strong>
            <span>{order.po_number}</span>
            {!sameStageLabel(order.status, title) && order.status ? <small>Status: {order.status}</small> : null}
            {order.tanggal_kirim_overdue ? <small className="is-urgent">Kirim terlambat</small> : null}
            {showApproval ? <SalesPwaDesignApproval order={order} expectMissing /> : null}
            <Link
              className={hasApprovalPath ? "spwa-action-secondary" : undefined}
              to={`/sales/po/${encodeURIComponent(order.id)}`}
            >
              Lihat PO
            </Link>
          </article>
        );
      })}
      {shownTotal > rows.length && (
        <Link
          className="spwa-stage-all"
          to="/sales/po"
          state={{ salesPwaPoContext: { stage } }}
          data-testid={`sales-action-${testKey}-all`}
        >
          Lihat semua
        </Link>
      )}
    </div>
  );
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
  const classified = classifyReminders(visible?.upcoming_visits, today);
  const agendaToday = Array.isArray(visible?.agenda_today) ? visible.agenda_today.slice(0, AGENDA_SECTION_LIMIT) : classified.agenda;
  const agendaTotal = Number(visible?.agenda_today_total);
  const agendaCount = Number.isFinite(agendaTotal) ? agendaTotal : agendaToday.length;
  const overdueFollowUps = Array.isArray(visible?.follow_up_overdue) ? visible.follow_up_overdue.slice(0, AGENDA_SECTION_LIMIT) : classified.overdue;
  const upcomingFollowUps = Array.isArray(visible?.follow_up_upcoming) ? visible.follow_up_upcoming.slice(0, AGENDA_SECTION_LIMIT) : classified.upcoming;
  const visitHistory = visits && visitsMonth === month ? visits : null;
  const lastVisit = latestVisit(visitHistory);
  const [priority, setPriority] = useState(null);
  const [priorityError, setPriorityError] = useState(false);

  useEffect(() => {
    if (typeof api.salesPwaIntelligence !== "function") {
      setPriority({ items: [], counts: {}, incomplete: false });
      setPriorityError(false);
      return undefined;
    }
    let cancelled = false;
    setPriority(null);
    setPriorityError(false);
    api.salesPwaIntelligence()
      .then((result) => {
        if (!cancelled) setPriority(result || { items: [], counts: {}, incomplete: false });
      })
      .catch(() => {
        if (!cancelled) setPriorityError(true);
      });
    return () => { cancelled = true; };
  }, []);
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
                <FieldStagePreview
                  testKey="design"
                  title="Approval Design"
                  orders={actionVisible.design}
                  total={actionVisible.designTotal}
                  stage="Approval Design"
                  showApproval
                />
                <FieldStagePreview
                  testKey="payment"
                  title="Approval Payment"
                  orders={actionVisible.payment}
                  total={actionVisible.paymentTotal}
                  stage="Approval Payment"
                  payment
                />
              </section>
            )}

            <PriorityToday data={priority} failed={priorityError} />

            <section className="spwa-panel" data-testid="sales-dashboard-agenda">
              <div className="spwa-section-head">
                <div><p className="spwa-kicker">AGENDA</p><h2>{agendaCount ? `Agenda hari ini (${agendaCount})` : "Agenda hari ini"}</h2></div>
                <Link to="/sales/visit">Kalender <ChevronRight size={15} /></Link>
              </div>
              {agendaToday.length === 0 && <p className="spwa-empty compact">Belum ada agenda</p>}
              <div className="spwa-agenda">
                {agendaToday.map((item) => {
                  const clock = clockLabel(item.at);
                  const kind = reminderKind(item);
                  const address = String(item.address || "").trim();
                  const note = reminderNote(item);
                  return (
                    <Link key={item.id} to="/sales/visit" className={item.overdue ? "is-overdue" : ""}>
                      {clock ? <em className="spwa-agenda-time">{clock}</em> : <CalendarDays size={16} />}
                      <span>
                        <strong>{item.outlet}</strong>
                        <small>{[kind, item.overdue ? "Terlambat" : ""].filter(Boolean).join(" · ") || formatSalesDate(item.date)}</small>
                        {address ? <small className="spwa-agenda-place"><MapPin size={12} /> {address}</small> : null}
                        {note ? <small>{note}</small> : null}
                      </span>
                    </Link>
                  );
                })}
              </div>
            </section>

            <section className="spwa-panel" data-testid="sales-dashboard-followup">
              <div className="spwa-section-head">
                <div><p className="spwa-kicker">FOLLOW UP</p><h2>Follow up customer</h2></div>
                <Link to="/sales/visit">Visit <ChevronRight size={15} /></Link>
              </div>
              <div data-testid="sales-dashboard-overdue">
                <h3 className="spwa-subhead">Follow-up terlambat</h3>
                {overdueFollowUps.length === 0 && <p className="spwa-empty compact">Tidak ada follow-up terlambat.</p>}
                <div className="spwa-agenda">
                  {overdueFollowUps.map((item) => {
                    const note = reminderNote(item);
                    const address = String(item.address || "").trim();
                    return (
                      <Link key={item.id} to="/sales/visit" className="is-overdue">
                        <span>
                          <strong>{item.outlet}</strong>
                          <small>Terlambat: {formatSalesDate(item.date)}</small>
                          {address ? <small className="spwa-agenda-place"><MapPin size={12} /> {address}</small> : null}
                          {note ? <small>{note}</small> : null}
                        </span>
                      </Link>
                    );
                  })}
                </div>
              </div>
              <div data-testid="sales-dashboard-upcoming">
                <h3 className="spwa-subhead">Follow-up mendatang</h3>
                {upcomingFollowUps.length === 0 && <p className="spwa-empty compact">Tidak ada follow-up mendatang.</p>}
                <div className="spwa-agenda">
                  {upcomingFollowUps.map((item) => {
                    const note = reminderNote(item);
                    const address = String(item.address || "").trim();
                    const clock = clockLabel(item.at);
                    return (
                      <Link key={item.id} to="/sales/visit">
                        <span>
                          <strong>{item.outlet}</strong>
                          <small>Follow up: {formatSalesDate(item.date)}{clock ? ` ${clock}` : ""}</small>
                          {address ? <small className="spwa-agenda-place"><MapPin size={12} /> {address}</small> : null}
                          {note ? <small>{note}</small> : null}
                        </span>
                      </Link>
                    );
                  })}
                </div>
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

function priorityGroups(items) {
  const groups = [];
  for (const item of items || []) {
    const label = item.group || "Perlu dihubungi";
    const last = groups[groups.length - 1];
    if (!last || last.label !== label) groups.push({ label, items: [item] });
    else last.items.push(item);
  }
  return groups;
}

function PriorityToday({ data, failed }) {
  const counts = data?.counts || {};
  const items = Array.isArray(data?.items) ? data.items : [];
  return (
    <section className="spwa-panel" data-testid="sales-intelligence">
      <div className="spwa-section-head">
        <div><p className="spwa-kicker">HARI INI</p><h2>Prioritas hari ini</h2></div>
      </div>
      {failed && <p className="spwa-empty compact" data-testid="sales-intelligence-error">Prioritas belum dapat dimuat.</p>}
      {!failed && !data && <p className="spwa-muted" data-testid="sales-intelligence-loading">Memuat prioritas...</p>}
      {!failed && data && (
        <>
          <p className="spwa-priority-summary" data-testid="sales-intelligence-summary">
            {Number(counts.need_contact) || 0} perlu dihubungi
            {" · "}
            {Number(counts.inactive) || 0} tidak aktif
            {" · "}
            {Number(counts.overdue_follow_up) || 0} follow-up terlambat
          </p>
          {data.incomplete && (
            <p className="spwa-priority-note" data-testid="sales-intelligence-incomplete">
              Sebagian data tidak dimuat. Prioritas mungkin belum lengkap.
            </p>
          )}
          {items.length === 0 && (
            <p className="spwa-empty compact" data-testid="sales-intelligence-empty">Tidak ada customer yang perlu dihubungi hari ini.</p>
          )}
          {priorityGroups(items).map((group) => (
            <div key={group.label} className="spwa-priority-group" data-testid={`sales-intelligence-group-${group.label}`}>
              <h3>{group.label}</h3>
              {group.items.map((item) => {
                const href = item.action?.href || (item.action?.kind === "visit" ? "/sales/visit" : "");
                const label = item.action?.kind === "visit" ? "Buka visit" : "Buka prediksi";
                return (
                  <article key={`${item.type}-${item.customer_key || item.customer_name}`} className="spwa-priority-row" data-testid={`sales-priority-${item.type}`}>
                    <strong>{item.customer_name}</strong>
                    <p>{item.reason}</p>
                    {href ? <Link to={href}>{label}</Link> : null}
                  </article>
                );
              })}
            </div>
          ))}
        </>
      )}
    </section>
  );
}
