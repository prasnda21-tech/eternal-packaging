export function isValidMobileManifest(manifest) {
  if (!manifest || typeof manifest !== "object") return false;
  if (manifest.name !== "Eternal Packaging") return false;
  if (manifest.display !== "standalone") return false;
  if (manifest.start_url !== "/m/tasks") return false;
  if (!Array.isArray(manifest.icons) || !manifest.icons.length) return false;
  return true;
}

export function shouldRegisterServiceWorker({ nodeEnv, hostname } = {}) {
  if (nodeEnv === "production") return true;
  return hostname === "localhost";
}

export function installAffordance({ deferredPrompt, isStandalone, isIos } = {}) {
  if (isStandalone) return { kind: "installed", show: false };
  if (deferredPrompt) return { kind: "prompt", show: true };
  if (isIos) return { kind: "ios-hint", show: true };
  return { kind: "guidance", show: true };
}

export function isIosUserAgent(ua = "") {
  return /iphone|ipad|ipod/i.test(ua);
}

export function isStandaloneDisplay(win = typeof window !== "undefined" ? window : null) {
  if (!win) return false;
  return !!(win.matchMedia?.("(display-mode: standalone)")?.matches || win.navigator?.standalone);
}
