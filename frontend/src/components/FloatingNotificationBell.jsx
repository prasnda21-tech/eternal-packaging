import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Bell } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  BELL_SIZE_PX,
  CLICK_DRAG_THRESHOLD_PX,
  POLL_INTERVAL_MS,
  clampBellPosition,
  countUnread,
  decisionLabel,
  defaultBellPosition,
  movementExceededThreshold,
  panelPlacement,
  readStoredBellPosition,
  writeStoredBellPosition,
} from "../lib/floatingNotificationBell";
import { productionSchedulePoHref } from "../lib/workPanel";
import "./floatingNotificationBell.css";

const PANEL_WIDTH = 340;
const PANEL_HEIGHT = 420;

function formatWhen(value) {
  if (!value) return "—";
  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString("id-ID", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return String(value);
  }
}

function viewportSize() {
  return {
    width: typeof window !== "undefined" ? window.innerWidth : 1280,
    height: typeof window !== "undefined" ? window.innerHeight : 800,
  };
}

export default function FloatingNotificationBell() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const userId = user?.id || user?.user_id || "";

  const [pos, setPos] = useState(() => defaultBellPosition(viewportSize()));
  const [panelOpen, setPanelOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [actingId, setActingId] = useState("");

  const rootRef = useRef(null);
  const dragRef = useRef({
    active: false,
    moved: false,
    pointerId: null,
    startX: 0,
    startY: 0,
    originLeft: 0,
    originTop: 0,
  });
  const posRef = useRef(pos);
  const fetchGen = useRef(0);
  const inFlight = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useLayoutEffect(() => {
    posRef.current = pos;
  }, [pos]);

  useEffect(() => {
    const storage = typeof window !== "undefined" ? window.localStorage : null;
    setPos(readStoredBellPosition(storage, userId, viewportSize()));
  }, [userId]);

  useEffect(() => {
    const onResize = () => setPos((prev) => clampBellPosition(prev, viewportSize()));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    setPanelOpen(false);
  }, [location.pathname, location.search]);

  const refresh = useCallback(async ({ silent = true } = {}) => {
    if (inFlight.current) return;
    inFlight.current = true;
    const gen = ++fetchGen.current;
    if (!silent && mounted.current) {
      setLoading(true);
      setError("");
    }
    try {
      const data = await api.listNotifications({ limit: 50 });
      if (!mounted.current || gen !== fetchGen.current) return;
      setItems(Array.isArray(data?.notifications) ? data.notifications : []);
      setError("");
    } catch (err) {
      if (!mounted.current || gen !== fetchGen.current) return;
      if (!silent) {
        setError(err?.response?.data?.detail || err?.message || "Gagal memuat notifikasi");
      }
    } finally {
      inFlight.current = false;
      if (mounted.current && gen === fetchGen.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let timer = null;
    const startPoll = () => {
      if (timer != null) return;
      timer = window.setInterval(() => {
        if (document.visibilityState !== "visible") return;
        refresh({ silent: true });
      }, POLL_INTERVAL_MS);
    };
    const stopPoll = () => {
      if (timer == null) return;
      window.clearInterval(timer);
      timer = null;
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        refresh({ silent: true });
        startPoll();
        return;
      }
      stopPoll();
    };
    const onFocus = () => refresh({ silent: true });
    refresh({ silent: false });
    if (document.visibilityState === "visible") startPoll();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onFocus);
    return () => {
      stopPoll();
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onFocus);
      fetchGen.current += 1;
      inFlight.current = false;
    };
  }, [refresh]);

  useEffect(() => {
    if (!panelOpen) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") setPanelOpen(false);
    };
    const onPointerDownDoc = (event) => {
      if (!rootRef.current?.contains(event.target)) setPanelOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointerDownDoc);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointerDownDoc);
    };
  }, [panelOpen]);

  const unreadCount = countUnread(items);
  const panel = panelPlacement(pos, { width: PANEL_WIDTH, height: PANEL_HEIGHT }, viewportSize());

  const onPointerDown = (event) => {
    if (event.button != null && event.button !== 0) return;
    const drag = dragRef.current;
    drag.active = true;
    drag.moved = false;
    drag.pointerId = event.pointerId;
    drag.startX = event.clientX;
    drag.startY = event.clientY;
    drag.originLeft = posRef.current.x;
    drag.originTop = posRef.current.y;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const onPointerMove = (event) => {
    const drag = dragRef.current;
    if (!drag.active || drag.pointerId !== event.pointerId) return;
    const exceeded = movementExceededThreshold(
      { x: drag.startX, y: drag.startY },
      { x: event.clientX, y: event.clientY },
      CLICK_DRAG_THRESHOLD_PX
    );
    if (!drag.moved && exceeded) {
      drag.moved = true;
      setDragging(true);
      setPanelOpen(false);
    }
    if (!drag.moved) return;
    setPos(
      clampBellPosition(
        {
          x: drag.originLeft + (event.clientX - drag.startX),
          y: drag.originTop + (event.clientY - drag.startY),
        },
        viewportSize()
      )
    );
  };

  const endPointer = (event) => {
    const drag = dragRef.current;
    if (!drag.active || drag.pointerId !== event.pointerId) return;
    const wasDrag = drag.moved;
    drag.active = false;
    drag.pointerId = null;
    setDragging(false);
    try {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    } catch {
      /* ignore */
    }
    if (wasDrag) {
      const storage = typeof window !== "undefined" ? window.localStorage : null;
      const next = clampBellPosition(posRef.current, viewportSize());
      setPos(next);
      writeStoredBellPosition(storage, userId, next, viewportSize());
      return;
    }
    setPanelOpen((open) => !open);
  };

  const onItemClick = async (item) => {
    if (!item?.id || actingId) return;
    setActingId(item.id);
    try {
      // Canonical unread persistence is read_at; is_read is API-normalized from it.
      if (!item.read_at) {
        const res = await api.markNotificationRead(item.id);
        const updated = res?.notification;
        if (updated) {
          setItems((prev) => prev.map((row) => (row.id === item.id ? { ...row, ...updated } : row)));
        } else {
          setItems((prev) =>
            prev.map((row) =>
              row.id === item.id
                ? { ...row, is_read: true, read_at: new Date().toISOString() }
                : row
            )
          );
        }
      }
      setPanelOpen(false);
      // WEB shell must open desktop Detail PO (/po/:id), not mobile deep_link (/m/orders/:id).
      // order_id is the entity identity; productionSchedulePoHref is the existing desktop path builder.
      const target = productionSchedulePoHref(item.order_id);
      if (target) navigate(target);
    } catch {
      /* keep unread; do not navigate — same as mobile inbox */
    } finally {
      setActingId("");
    }
  };

  return (
    <div
      ref={rootRef}
      className="ep-web-notif-root"
      style={{ left: pos.x, top: pos.y }}
      data-testid="web-floating-notification-bell"
      data-x={pos.x}
      data-y={pos.y}
    >
      <button
        type="button"
        className={`ep-web-notif-bell${dragging ? " is-dragging" : ""}`}
        aria-label="Notifikasi"
        aria-expanded={panelOpen}
        data-testid="web-notif-bell-button"
        style={{ width: BELL_SIZE_PX, height: BELL_SIZE_PX }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endPointer}
        onPointerCancel={endPointer}
      >
        <Bell size={22} strokeWidth={2.25} aria-hidden />
        {unreadCount > 0 ? (
          <span className="ep-web-notif-badge" data-testid="web-notif-badge">
            {unreadCount}
          </span>
        ) : null}
      </button>

      {panelOpen ? (
        <div
          className="ep-web-notif-panel"
          role="dialog"
          aria-label="Notifikasi"
          data-testid="web-notif-panel"
          style={{
            left: panel.left - pos.x,
            top: panel.top - pos.y,
            width: PANEL_WIDTH,
            maxHeight: PANEL_HEIGHT,
          }}
        >
          <div className="ep-web-notif-panel-head">
            <strong>Notifikasi</strong>
            {unreadCount > 0 ? (
              <span className="ep-web-notif-panel-unread">{unreadCount} belum dibaca</span>
            ) : (
              <span className="ep-web-notif-panel-unread is-muted">Semua terbaca</span>
            )}
          </div>
          <div className="ep-web-notif-panel-body">
            {loading && items.length === 0 ? <div className="ep-web-notif-empty">Memuat…</div> : null}
            {error && items.length === 0 ? <div className="ep-web-notif-empty is-error">{error}</div> : null}
            {!loading && !error && items.length === 0 ? (
              <div className="ep-web-notif-empty">Belum ada notifikasi.</div>
            ) : null}
            {items.map((item) => {
              const unread = !item.read_at;
              const decision = decisionLabel(item.decision);
              const rejected =
                String(item.decision || "").toLowerCase() === "rejected" ||
                String(item.event_type || "").includes("rejected");
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`ep-web-notif-item${unread ? " is-unread" : ""}`}
                  data-testid={`web-notif-item-${item.id}`}
                  data-unread={unread ? "1" : "0"}
                  disabled={actingId === item.id}
                  onClick={() => onItemClick(item)}
                >
                  <div className="ep-web-notif-item-top">
                    <span className="ep-web-notif-item-title">{item.title || "Notifikasi"}</span>
                    {decision ? (
                      <span className={`ep-web-notif-decision is-${rejected ? "rejected" : "approved"}`}>
                        {decision}
                      </span>
                    ) : null}
                  </div>
                  {item.body ? <div className="ep-web-notif-item-body">{item.body}</div> : null}
                  <div className="ep-web-notif-item-meta">
                    {item.po_number ? <span>PO {item.po_number}</span> : null}
                    <span>{formatWhen(item.created_at)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
