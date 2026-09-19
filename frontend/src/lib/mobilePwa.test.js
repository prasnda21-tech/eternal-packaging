import fs from "fs";
import path from "path";
import { installAffordance, isValidMobileManifest, shouldRegisterServiceWorker } from "./mobilePwa";

describe("PWA config", () => {
  test("manifest is valid operational mobile config", () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "../../public/manifest.json"), "utf8"));
    expect(isValidMobileManifest(manifest)).toBe(true);
    expect(manifest.short_name).toBeTruthy();
    expect(manifest.icons[0].src).toMatch(/eternal-favicon/);
  });

  test("service worker does not queue business mutations or cache API", () => {
    const sw = fs.readFileSync(path.join(__dirname, "../../public/service-worker.js"), "utf8");
    expect(sw).toMatch(/network-first|network first|NetworkFirst/i);
    expect(sw).toMatch(/\/api\//);
    expect(sw).toMatch(/skipWaiting|clients\.claim/);
    expect(sw).toMatch(/isHashedStaticAsset/);
    expect(sw).toMatch(/\/static\//);
    expect(sw).not.toMatch(/background.?sync|offline.?queue|replay/i);
    expect(sw).not.toMatch(/packing-info|confirm-delivered|operator-progress/);
  });

  test("install affordance is user-triggered", () => {
    expect(installAffordance({ isStandalone: true }).show).toBe(false);
    expect(installAffordance({ deferredPrompt: {} }).kind).toBe("prompt");
    expect(installAffordance({ isIos: true }).kind).toBe("ios-hint");
    expect(shouldRegisterServiceWorker({ nodeEnv: "production" })).toBe(true);
    expect(shouldRegisterServiceWorker({ nodeEnv: "development", hostname: "localhost" })).toBe(true);
  });
});
