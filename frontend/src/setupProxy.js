const { createProxyMiddleware } = require("http-proxy-middleware");

/**
 * Local preview: browser only needs the frontend port forwarded.
 * /api (and chat WebSocket) proxy to uvicorn.
 * Default remains 8000 so the 3002 preview is unchanged.
 * PORT=3003 preview: set BACKEND_PROXY_TARGET=http://127.0.0.1:8003
 */
function proxyTarget() {
  const raw = String(process.env.BACKEND_PROXY_TARGET || "").trim();
  if (raw) return raw.replace(/\/$/, "");
  return "http://127.0.0.1:8000";
}

module.exports = function (app) {
  app.use(
    "/api",
    createProxyMiddleware({
      target: proxyTarget(),
      changeOrigin: true,
      ws: true,
    }),
  );
};
