import { buildChatWsUrl, createChatRealtime } from "./chatRealtime";

function mockWebSocketFactory() {
  const instances = [];
  class MockWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;

    constructor(url) {
      this.url = url;
      this.readyState = MockWebSocket.CONNECTING;
      this.sent = [];
      this.onopen = null;
      this.onmessage = null;
      this.onclose = null;
      this.onerror = null;
      instances.push(this);
    }

    send(data) {
      this.sent.push(data);
    }

    close(code = 1000) {
      this.readyState = MockWebSocket.CLOSED;
      if (typeof this.onclose === "function") {
        this.onclose({ code });
      }
    }

    open() {
      this.readyState = MockWebSocket.OPEN;
      if (typeof this.onopen === "function") this.onopen();
    }
  }
  return { MockWebSocket, instances };
}

describe("buildChatWsUrl", () => {
  const originalEnv = process.env.REACT_APP_BACKEND_URL;

  afterEach(() => {
    process.env.REACT_APP_BACKEND_URL = originalEnv;
  });

  test("HTTPS page with empty backend env uses page origin and wss /api/chat/ws", () => {
    process.env.REACT_APP_BACKEND_URL = "";
    expect(
      buildChatWsUrl({
        configured: "",
        pageHostname: "produksi.eternalpackaging.id",
        pageOrigin: "https://produksi.eternalpackaging.id",
        pageProtocol: "https:",
      })
    ).toBe("wss://produksi.eternalpackaging.id/api/chat/ws");
  });

  test("HTTPS configured origin maps to wss /api/chat/ws without trailing slash", () => {
    expect(
      buildChatWsUrl({
        configured: "https://produksi.eternalpackaging.id",
        pageHostname: "produksi.eternalpackaging.id",
        pageOrigin: "https://produksi.eternalpackaging.id",
        pageProtocol: "https:",
      })
    ).toBe("wss://produksi.eternalpackaging.id/api/chat/ws");
  });

  test("localhost http maps to ws://localhost:8000/api/chat/ws", () => {
    expect(
      buildChatWsUrl({
        configured: "http://127.0.0.1:8000",
        pageHostname: "localhost",
        pageOrigin: "http://localhost:3002",
        pageProtocol: "http:",
      })
    ).toBe("ws://localhost:8000/api/chat/ws");
  });

  test("does not double /api prefix or append query token by default", () => {
    const url = buildChatWsUrl({
      configured: "https://produksi.eternalpackaging.id/",
      pageHostname: "produksi.eternalpackaging.id",
      pageOrigin: "https://produksi.eternalpackaging.id",
      pageProtocol: "https:",
    });
    expect(url).toBe("wss://produksi.eternalpackaging.id/api/chat/ws");
    expect(url).not.toMatch(/\/api\/api\//);
    expect(url).not.toMatch(/\?/);
    expect(url.endsWith("/")).toBe(false);
  });

  test("HTTPS page forces wss even if configured backend is http", () => {
    expect(
      buildChatWsUrl({
        configured: "http://produksi.eternalpackaging.id",
        pageHostname: "produksi.eternalpackaging.id",
        pageOrigin: "https://produksi.eternalpackaging.id",
        pageProtocol: "https:",
      })
    ).toBe("wss://produksi.eternalpackaging.id/api/chat/ws");
  });
});

describe("createChatRealtime", () => {
  let MockWebSocket;
  let instances;
  let originalWebSocket;
  let statuses;

  beforeEach(() => {
    jest.useFakeTimers();
    ({ MockWebSocket, instances } = mockWebSocketFactory());
    originalWebSocket = global.WebSocket;
    global.WebSocket = MockWebSocket;
    statuses = [];
    process.env.REACT_APP_BACKEND_URL = "https://produksi.eternalpackaging.id";
  });

  afterEach(() => {
    global.WebSocket = originalWebSocket;
    jest.useRealTimers();
  });

  test("status transitions connecting -> online", () => {
    const rt = createChatRealtime({ onStatus: (s) => statuses.push(s) });
    rt.start();
    expect(statuses).toContain("connecting");
    expect(instances).toHaveLength(1);
    expect(instances[0].url).toBe("wss://produksi.eternalpackaging.id/api/chat/ws");
    instances[0].open();
    expect(statuses[statuses.length - 1]).toBe("online");
    rt.stop();
    expect(statuses[statuses.length - 1]).toBe("offline");
  });

  test("reconnects after normal network close with reconnecting status", () => {
    const rt = createChatRealtime({ onStatus: (s) => statuses.push(s) });
    rt.start();
    instances[0].open();
    instances[0].close(1006);
    expect(statuses).toContain("reconnecting");
    jest.advanceTimersByTime(1000);
    expect(instances.length).toBeGreaterThan(1);
    rt.stop();
  });

  test("auth rejection close codes do not reconnect aggressively", () => {
    const rt = createChatRealtime({ onStatus: (s) => statuses.push(s) });
    rt.start();
    instances[0].close(4401);
    expect(statuses[statuses.length - 1]).toBe("offline");
    const afterAuth = instances.length;
    jest.advanceTimersByTime(60000);
    expect(instances.length).toBe(afterAuth);
    rt.stop();
  });

  test("start does not leave duplicate live sockets", () => {
    const rt = createChatRealtime({ onStatus: (s) => statuses.push(s) });
    rt.start();
    const first = instances[0];
    first.open();
    rt.start();
    expect(first.readyState).toBe(MockWebSocket.CLOSED);
    expect(instances).toHaveLength(2);
    instances[1].open();
    expect(rt.status()).toBe("online");
    rt.stop();
  });

  test("does not use REST polling as a hidden transport", () => {
    const rt = createChatRealtime({ onStatus: (s) => statuses.push(s) });
    rt.start();
    instances[0].open();
    expect(typeof rt.poll).toBe("undefined");
    expect(typeof rt.fetch).toBe("undefined");
    rt.stop();
  });
});
