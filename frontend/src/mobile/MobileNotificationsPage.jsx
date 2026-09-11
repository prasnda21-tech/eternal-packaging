import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";
import { formatDate } from "../lib/constants";
import MobileTopBar from "./MobileTopBar";

function decisionLabel(decision) {
  if (decision === "approved") return "Disetujui";
  if (decision === "rejected") return "Ditolak";
  return decision || "";
}

export default function MobileNotificationsPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listNotifications({ limit: 50 });
      setItems(Array.isArray(res?.notifications) ? res.notifications : []);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Gagal memuat notifikasi");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openItem = async (item) => {
    if (!item?.id || busyId) return;
    setBusyId(item.id);
    try {
      if (!item.is_read) {
        const res = await api.markNotificationRead(item.id);
        const updated = res?.notification;
        if (updated) {
          setItems((prev) => prev.map((row) => (row.id === item.id ? { ...row, ...updated } : row)));
        } else {
          setItems((prev) => prev.map((row) => (
            row.id === item.id ? { ...row, is_read: true, read_at: new Date().toISOString() } : row
          )));
        }
      }
      const link = item.deep_link || (item.order_id ? `/m/orders/${encodeURIComponent(item.order_id)}` : "");
      if (link) navigate(link);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Gagal membuka notifikasi");
    } finally {
      setBusyId("");
    }
  };

  return (
    <>
      <MobileTopBar kicker="ETERNAL PACKAGING" title="Notifikasi" />
      <main className="mop-content" data-testid="mobile-notifications-page">
        {loading && (
          <p className="mop-empty" data-testid="mobile-notifications-loading">Memuat notifikasi...</p>
        )}
        {!loading && error ? (
          <p className="mop-empty" data-testid="mobile-notifications-error" style={{ color: "#8f1f1f" }}>{error}</p>
        ) : null}
        {!loading && !error && items.length === 0 ? (
          <p className="mop-empty" data-testid="mobile-notifications-empty">Belum ada notifikasi.</p>
        ) : null}
        {!loading && items.length > 0 ? (
          <ul className="mop-notif-list" data-testid="mobile-notifications-list">
            {items.map((item) => {
              const unread = !item.is_read;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`mop-notif-item${unread ? " is-unread" : ""}`}
                    data-testid={`mobile-notification-${item.id}`}
                    data-decision={item.decision || ""}
                    data-unread={unread ? "1" : "0"}
                    onClick={() => openItem(item)}
                    disabled={busyId === item.id}
                  >
                    <div className="mop-notif-item-top">
                      <span className={`mop-notif-badge mop-notif-badge--${item.decision || "info"}`}>
                        {decisionLabel(item.decision)}
                      </span>
                      <span className="mop-notif-time">{formatDate(item.created_at)}</span>
                    </div>
                    <div className="mop-notif-title">{item.title}</div>
                    <div className="mop-notif-body">{item.body}</div>
                    {item.po_number ? <div className="mop-notif-meta">PO {item.po_number}</div> : null}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
      </main>
    </>
  );
}
