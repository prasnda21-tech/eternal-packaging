import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bell, MessageCircle } from "lucide-react";
import api from "../../lib/api";
import { MOBILE_HEADER_CHAT, MOBILE_HEADER_NOTIFICATIONS } from "../../lib/mobileWorkspaces";

/** Compact Chat + Notifikasi actions for the shared PWA page header. */
export default function MobileHeaderActions() {
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const load = async () => {
      try {
        const res = await api.chatUnreadSummary();
        if (!cancelled) setUnread(Number(res?.total_unread || 0));
      } catch {
        if (!cancelled) setUnread(0);
      }
    };
    const startPoll = () => {
      if (timer != null) return;
      timer = setInterval(() => {
        if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
        load();
      }, 30000);
    };
    const stopPoll = () => {
      if (timer == null) return;
      clearInterval(timer);
      timer = null;
    };
    const onVisibility = () => {
      if (typeof document !== "undefined" && document.visibilityState === "visible") {
        load();
        startPoll();
        return;
      }
      stopPoll();
    };
    load();
    if (typeof document === "undefined" || document.visibilityState === "visible") startPoll();
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
    }
    return () => {
      cancelled = true;
      stopPoll();
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    };
  }, []);

  return (
    <div className="mop-header-actions" data-testid="mobile-header-actions">
      <Link
        to={MOBILE_HEADER_CHAT.to}
        className="mop-header-action"
        aria-label={MOBILE_HEADER_CHAT.label}
        data-testid="mobile-header-chat"
      >
        <MessageCircle size={20} aria-hidden />
        {unread > 0 ? (
          <span className="mop-header-action-badge" data-testid="mobile-menu-chat-unread">
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </Link>
      <Link
        to={MOBILE_HEADER_NOTIFICATIONS.to}
        className="mop-header-action"
        aria-label={MOBILE_HEADER_NOTIFICATIONS.label}
        data-testid="mobile-header-notifications"
      >
        <Bell size={20} aria-hidden />
      </Link>
    </div>
  );
}
