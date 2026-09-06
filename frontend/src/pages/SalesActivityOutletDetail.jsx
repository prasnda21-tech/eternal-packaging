import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { AUTOMATION_LABELS, KIND_LABELS, automationPausedMigration, canManageOwnership } from "../lib/salesActivity";
import { SaField, SaSection, fmtSaDate } from "../components/SalesActivityUi";
import { HistoricalEvidence, VisitPhotoList } from "../components/SalesActivityVisitPhotos";
import { compactVisitIndicator, isVisitActivityType } from "../lib/salesActivity";

function errText(e) {
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  return d?.message || e.message || "Gagal";
}

export default function SalesActivityOutletDetail() {
  const { id } = useParams();
  const { hasPerm } = useAuth();
  const [data, setData] = useState(null);
  const [toast, setToast] = useState("");
  const [reason, setReason] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const [users, setUsers] = useState([]);
  const [picUsers, setPicUsers] = useState([]);
  const notify = (m) => { setToast(m); setTimeout(() => setToast(""), 4000); };

  const load = () => api.salesActivityOutlet(id).then(setData).catch((e) => notify(errText(e)));
  useEffect(() => {
    load();
    if (canManageOwnership(hasPerm)) {
      Promise.all([
        api.salesActivityUsers().catch(() => ({ users: [] })),
        api.salesActivityProspectPicUsers().catch(() => ({ users: [] })),
      ]).then(([all, pics]) => {
        setUsers(all.users || []);
        setPicUsers(pics.users || []);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    const current = data?.ownership?.current_owner_id;
    if (current) {
      setOwnerId((prev) => prev || current);
    }
  }, [data?.ownership?.current_owner_id]);

  if (!data) return <div className="loading">Memuat...</div>;
  const { outlet, ownership = {}, activities = [], history = [], last_valid_order } = data;
  const isProspect = String(ownership.customer_kind || outlet.customer_kind || "").toUpperCase() === "PROSPECT";
  const reassignOptions = (() => {
    if (!isProspect) return users;
    const map = new Map((picUsers || []).map((u) => [u.id, u]));
    const currentId = ownership.current_owner_id;
    if (currentId && !map.has(currentId)) {
      map.set(currentId, {
        id: currentId,
        name: ownership.current_owner_name || currentId,
        status: "historical",
      });
    }
    return Array.from(map.values());
  })();

  const act = async (fn) => {
    if (!reason.trim()) { notify("Alasan wajib"); return; }
    try {
      await fn();
      setReason("");
      load();
    } catch (e) { notify(errText(e)); }
  };

  return (
    <div className="sa-detail po-detail penjualan-page penjualan-workspace" data-testid="sa-outlet-detail">
      <div className="sa-page-head page-heading">
        <div className="sa-detail-title">
          <Link className="ghost sm" to="/penjualan/aktivitas-sales">Kembali</Link>
          <p className="eyebrow">Outlet</p>
          <h1>{outlet.name}</h1>
          <p className="muted">{outlet.address}</p>
        </div>
      </div>
      <div className="sa-kpi-strip">
        <div className="sa-kpi"><span className="muted">Owner</span><strong>{ownership.current_owner_name || "-"}</strong></div>
        <div className="sa-kpi"><span className="muted">Status</span><strong>{KIND_LABELS[ownership.customer_kind] || ownership.customer_kind}</strong></div>
        <div className="sa-kpi"><span className="muted">Automation</span><strong data-testid="sa-detail-automation">{AUTOMATION_LABELS[ownership.automation_status] || ownership.automation_status}</strong></div>
        <div className="sa-kpi"><span className="muted">Eligible</span><strong>{fmtSaDate(ownership.eligible_at)}</strong></div>
      </div>
      {automationPausedMigration(ownership.automation_status) && (
        <p className="sa-import-banner" data-testid="sa-detail-paused">
          IMPORT TIDAK MENJALANKAN AUTOMATION.
          <span className="sa-import-banner-sub">Import tidak menjalankan automation. Data migrasi menunggu aktivasi eksplisit.</span>
        </p>
      )}
      <SaSection title="Master outlet">
        <div className="sa-readonly">
          <span className="sa-field-label">WhatsApp</span>
          <p className="sa-readonly-value">{outlet.whatsapp || "-"}</p>
        </div>
        <div className="sa-readonly">
          <span className="sa-field-label">PIC</span>
          <p className="sa-readonly-value">{outlet.pic_name || "-"}</p>
        </div>
        <div className="sa-readonly">
          <span className="sa-field-label">Sumber</span>
          <p className="sa-readonly-value">{outlet.lead_source || "-"}</p>
        </div>
        <div className="sa-readonly">
          <span className="sa-field-label">Source</span>
          <p className="sa-readonly-value">{outlet.source || "-"}</p>
        </div>
      </SaSection>
      <SaSection title="Pesanan Penjualan terkait">
        <div className="sa-readonly sa-field-wide">
          <span className="sa-field-label">Order valid terakhir</span>
          <p className="sa-readonly-value">{last_valid_order ? `${last_valid_order.po_number} · ${last_valid_order.order_date}` : "Tidak ada order valid"}</p>
        </div>
      </SaSection>
      {canManageOwnership(hasPerm) && (
        <div data-testid="sa-supervisor-controls">
          <SaSection title="Intervensi supervisor">
            <SaField label="Alasan" required wide>
              <input className="sa-control" value={reason} onChange={(e) => setReason(e.target.value)} data-testid="sa-reason" />
            </SaField>
            <SaField label={isProspect ? "Owner / PIC Sales" : "Sales"}>
              <select
                className="sa-control"
                value={ownerId}
                onChange={(e) => setOwnerId(e.target.value)}
                data-testid="sa-detail-owner-select"
              >
                <option value="">Pilih</option>
                {reassignOptions.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}{u.status === "historical" ? " (saat ini)" : ""}
                  </option>
                ))}
              </select>
            </SaField>
            <div className="sa-action-row sa-field-wide">
              <button type="button" className="ghost" onClick={() => act(() => api.salesActivityReassign(id, { owner_id: ownerId, reason }))}>Reassign</button>
              <button type="button" className="ghost" onClick={() => act(() => api.salesActivityPause(id, { reason }))}>Pause</button>
              <button type="button" className="ghost" onClick={() => act(() => api.salesActivityResume(id, { reason }))}>Resume</button>
              <button type="button" className="ghost" onClick={() => act(() => api.salesActivitySendToPool(id, { reason }))}>Kirim ke Pool</button>
              <button type="button" className="ghost" onClick={() => act(() => api.salesActivityExtend(id, { reason, extra_days: 7 }))}>Perpanjang 7 hari</button>
            </div>
          </SaSection>
        </div>
      )}
      <SaSection title="Aktivitas">
        <div className="sa-table-wrap table-wrap sa-field-wide">
          <table className="ship-table">
            <thead><tr><th>Waktu</th><th>Sales</th><th>Jenis</th><th>Hasil</th><th>Follow up</th><th>Visit</th></tr></thead>
            <tbody>
              {activities.length === 0 && (
                <tr><td colSpan={6}><p className="sa-empty">Belum ada aktivitas.</p></td></tr>
              )}
              {activities.map((a) => {
                const visit = compactVisitIndicator(a);
                return (
                  <tr key={a.id}>
                    <td>{fmtSaDate(a.activity_at)}</td>
                    <td>{a.sales_name}</td>
                    <td>{a.activity_type}</td>
                    <td>{a.result}</td>
                    <td>{fmtSaDate(a.next_follow_up_at)}</td>
                    <td>{isVisitActivityType(a.activity_type) ? `${visit.photoLabel} · ${visit.locationLabel}` : "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {activities.map((a) => (
          <div key={`photos-${a.id}`} className="sa-field-wide" style={{ marginTop: 12 }}>
            <VisitPhotoList photos={a.visit_photos || []} showUploader />
            <HistoricalEvidence activity={a} />
          </div>
        ))}
      </SaSection>
      <SaSection title="Riwayat ownership (append-only)">
        <div className="sa-table-wrap table-wrap sa-field-wide">
          <table className="ship-table" data-testid="sa-history-table">
            <thead><tr><th>Waktu</th><th>Event</th><th>Dari</th><th>Ke</th><th>Alasan</th></tr></thead>
            <tbody>
              {history.length === 0 && (
                <tr><td colSpan={5}><p className="sa-empty">Belum ada riwayat.</p></td></tr>
              )}
              {history.map((h) => (
                <tr key={h.event_id}><td>{fmtSaDate(h.occurred_at)}</td><td>{h.event_type}</td><td>{h.previous_owner_id || "-"}</td><td>{h.new_owner_id || "-"}</td><td>{h.reason || "-"}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </SaSection>
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
