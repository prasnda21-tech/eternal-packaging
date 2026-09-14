const { createProxyMiddleware } = require("http-proxy-middleware");

/**
 * Local preview: browser only needs the frontend port forwarded.
 * /api (and chat WebSocket) proxy to uvicorn on 127.0.0.1:8000.
 */
module.exports = function (app) {
  app.use(
    "/api",
    createProxyMiddleware({
      target: "http://127.0.0.1:8000",
      changeOrigin: true,
      ws: true,
    }),
  );
};
