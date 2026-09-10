/** Authenticated chat WebSocket client with bounded reconnect + resync hooks. */

import { resolveBackendOrigin } from "./backendOrigin";

const MAX_BACKOFF_MS = 15000;
const BASE_BACKOFF_MS = 800;

function wsUrl() {
  const pageHost = typeof window !== "undefined" ? window.location.hostname : "";
  const origin = resolveBackendOrigin(process.env.REACT_APP_BACKEND_URL, pageHost);
  if (!origin) return "";
  const u = new URL(origin);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  u.pathname = "/api/chat/ws";
  u.search = "";
  return u.toString();
}

export function createChatRealtime({ onEvent, onStatus, onReconnect } = {}) {
  let ws = null;
  let closed = false;
  let attempt = 0;
  let timer = null;
  let subscribed = new Set();

  const setStatus = (status) => {
    if (typeof onStatus === "function") onStatus(status);
  };

  const schedule = () => {
    if (closed) return;
    const delay = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * (2 ** Math.min(attempt, 4)));
    attempt += 1;
    setStatus("reconnecting");
    timer = setTimeout(connect, delay);
  };

  const connect = () => {
    if (closed) return;
    const url = wsUrl();
    if (!url || typeof WebSocket === "undefined") {
      setStatus("offline");
      return;
    }
    setStatus(attempt === 0 ? "connecting" : "reconnecting");
    try {
      ws = new WebSocket(url);
    } catch (_) {
      schedule();
      return;
    }

    ws.onopen = () => {
      attempt = 0;
      setStatus("online");
      if (subscribed.size) {
        ws.send(JSON.stringify({ op: "subscribe", room_ids: Array.from(subscribed) }));
      }
      if (typeof onReconnect === "function" && attempt === 0) {
        /* first open handled below */
      }
      if (typeof onReconnect === "function") onReconnect();
    };

    ws.onmessage = (ev) => {
      let data = null;
      try {
        data = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (data && typeof onEvent === "function") onEvent(data);
    };

    ws.onclose = () => {
      ws = null;
      if (!closed) schedule();
      else setStatus("offline");
    };

    ws.onerror = () => {
      try { ws?.close(); } catch { /* ignore */ }
    };
  };

  return {
    start() {
      closed = false;
      connect();
    },
    stop() {
      closed = true;
      if (timer) clearTimeout(timer);
      timer = null;
      try { ws?.close(); } catch { /* ignore */ }
      ws = null;
      setStatus("offline");
    },
    subscribe(roomIds) {
      const next = new Set((roomIds || []).filter(Boolean).map(String));
      subscribed = next;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ op: "subscribe", room_ids: Array.from(next) }));
      }
    },
    status() {
      return ws && ws.readyState === WebSocket.OPEN ? "online" : "offline";
    },
  };
}
