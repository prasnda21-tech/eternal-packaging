import { nextSidebarMenu, placeSidebarFlyout } from "./sidebarFlyout";

describe("sidebar flyout placement", () => {
  test("click parent opens, same parent closes, other parent switches", () => {
    expect(nextSidebarMenu(null, "produksi")).toBe("produksi");
    expect(nextSidebarMenu("produksi", "produksi")).toBe(null);
    expect(nextSidebarMenu("produksi", "reports")).toBe("reports");
  });

  test("icon rail flyout starts at the sidebar right edge and aligns with the clicked item", () => {
    const pos = placeSidebarFlyout(
      { left: 0, right: 64, top: 0, bottom: 800, width: 64, height: 800 },
      { left: 8, right: 56, top: 220, bottom: 264, width: 48, height: 44 },
      { width: 260, height: 280 },
      { width: 1280, height: 800 },
    );
    expect(pos.placement).toBe("right");
    expect(pos.left).toBe(64);
    expect(pos.top).toBe(220);
    expect(pos.left).toBeGreaterThanOrEqual(64);
    expect(pos.scrimLeft).toBe(64);
  });

  test("flyout never starts over the icon rail", () => {
    const pos = placeSidebarFlyout(
      { left: 0, right: 72, top: 0, bottom: 900, width: 72, height: 900 },
      { top: 40, bottom: 84, left: 12, right: 60 },
      { width: 260, height: 120 },
      { width: 1440, height: 900 },
    );
    expect(pos.left).toBe(72);
    expect(pos.left).toBeGreaterThanOrEqual(72);
  });

  test("near the bottom of the viewport the panel shifts up so it stays fully visible", () => {
    const pos = placeSidebarFlyout(
      { left: 0, right: 64, top: 0, bottom: 800, width: 64, height: 800 },
      { left: 8, right: 56, top: 720, bottom: 764, width: 48, height: 44 },
      { width: 260, height: 240 },
      { width: 1280, height: 800 },
    );
    expect(pos.top).toBe(552);
    expect(pos.top + 240).toBeLessThanOrEqual(800 - 8);
    expect(pos.top).toBeGreaterThanOrEqual(8);
  });

  test("expanded vertical rail still places the panel to the right", () => {
    const pos = placeSidebarFlyout(
      { left: 0, right: 240, top: 0, bottom: 768, width: 240, height: 768 },
      { left: 8, right: 232, top: 180, bottom: 220, width: 224, height: 40 },
      { width: 280, height: 320 },
      { width: 1366, height: 768 },
    );
    expect(pos.placement).toBe("right");
    expect(pos.left).toBe(240);
    expect(pos.top).toBe(180);
    expect(pos.scrimLeft).toBe(240);
    expect(pos.left).toBeGreaterThanOrEqual(240);
  });

  test("horizontal mobile rail places the panel below the rail, not over it", () => {
    const pos = placeSidebarFlyout(
      { left: 0, right: 390, top: 0, bottom: 56, width: 390, height: 56 },
      { left: 80, right: 124, top: 6, bottom: 50, width: 44, height: 44 },
      { width: 260, height: 200 },
      { width: 390, height: 800 },
    );
    expect(pos.placement).toBe("below");
    expect(pos.top).toBe(56);
    expect(pos.scrimTop).toBe(56);
    expect(pos.scrimLeft).toBe(0);
  });
});
