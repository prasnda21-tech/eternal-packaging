import { shouldRegisterServiceWorker } from "../lib/mobilePwa";

export function registerServiceWorker() {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
  const hostname = window.location.hostname;
  if (!shouldRegisterServiceWorker({ nodeEnv: process.env.NODE_ENV, hostname })) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register(`${process.env.PUBLIC_URL || ""}/service-worker.js`).catch(() => {});
  });
}
