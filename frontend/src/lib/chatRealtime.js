/** Authenticated chat WebSocket client with bounded reconnect + resync hooks. */

import { resolveBackendOrigin } from "./backendOrigin";

const MAX_BACKOFF_MS = 15000;
const BASE_BACKOFF_MS = 800;
const AUTH_REJECT_CODES = new Set([4401, 4403]);

/**
 * Build the chat WebSocket URL.
 *
 * Production often leaves REACT_APP_BACKEND_URL empty and serves the SPA + /api
 * behind the same nginx host. HTTP clients then use relative `/api` and work;
 * WebSocket must fall back to the page origin or it never connects.
 */
export function buildChatWsUrl({
  configured = process.env.REACT_APP_BACKEND_URL,
  pageHostname,
  pageOrigin,
  pageProtocol,
} = {}) {
  const host =
    pageHostname != null
      ? pageHostname
      : typeof window !== "undefined"
        ? window.location.hostname
        : "";
  const originFromConfig = resolveBackendOrigin(configured, host);
  const origin =
    originFromConfig ||
    pageOrigin ||
    (typeof window !== "undefined" ? window.location.origin : "");
  if (!origin) return "";

  let u;
  try {
    u = new URL(origin);
  } catch {
    return "";
  }

  // Prefer the page protocol so an HTTPS SPA never emits ws:// (mixed content).
  const pageProto =
    pageProtocol ||
    (typeof window !== "undefined" ? window.location.protocol : "");
  if (pageProto === "https:" || pageProto === "wss:") {
    u.protocol = "wss:";
  } else if (u.protocol === "https:" || u.protocol === "wss:") {
    u.protocol = "wss:";
  } else {
    u.protocol = "ws:";
  }
  // Exact path for nginx `location = /api/chat/ws` (no trailing slash).
  u.pathname = "/api/chat/ws";
  u.search = "";
  u.hash = "";
  return u.toString();
}

function wsUrl() {
  return buildChatWsUrl();
}

export function createChatRealtime({ onEvent, onStatus, onReconnect } = {}) {
  let ws = null;
  let closed = false;
  let attempt = 0;
  let timer = null;
  let subscribed = new Set();
  let authRejected = false;

  const setStatus = (status) => {
    if (typeof onStatus === "function") onStatus(status);
  };

  const clearTimer = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };

  const schedule = () => {
    if (closed || authRejected) return;
    clearTimer();
    const delay = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * (2 ** Math.min(attempt, 4)));
    attempt += 1;
    setStatus("reconnecting");
    timer = setTimeout(connect, delay);
  };

  const connect = () => {
    if (closed || authRejected) return;
    const url = wsUrl();
    if (!url || typeof WebSocket === "undefined") {
      setStatus("offline");
      return;
    }
    setStatus(attempt === 0 ? "connecting" : "reconnecting");
    try {
      if (ws) {
        try {
          ws.onopen = null;
          ws.onmessage = null;
          ws.onclose = null;
          ws.onerror = null;
          ws.close();
        } catch {
          /* ignore */
        }
        ws = null;
      }
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

    ws.onclose = (ev) => {
      ws = null;
      if (closed) {
        setStatus("offline");
        return;
      }
      const code = ev && typeof ev.code === "number" ? ev.code : 0;
      if (AUTH_REJECT_CODES.has(code)) {
        authRejected = true;
        clearTimer();
        setStatus("offline");
        return;
      }
      schedule();
    };

    ws.onerror = () => {
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
    };
  };

  return {
    start() {
      closed = false;
      authRejected = false;
      connect();
    },
    stop() {
      closed = true;
      clearTimer();
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
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
