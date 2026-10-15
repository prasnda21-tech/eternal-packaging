import {
  SIDEBAR_COMPACT_BREAKPOINT,
  SIDEBAR_EXPANDED_WIDTH,
  SIDEBAR_MODE_COLLAPSED,
  SIDEBAR_MODE_EXPANDED,
  SIDEBAR_MODE_STORAGE_PREFIX,
  defaultSidebarModeForWidth,
  normalizeSidebarMode,
  readSidebarMode,
  resolveSidebarMode,
  sidebarModeStorageKey,
  sidebarRailWidth,
  toggleSidebarMode,
  writeSidebarMode,
} from "./sidebarMode";
import { SIDEBAR_RAIL_WIDTH } from "./sidebarPresentation";

function memoryStorage(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
    _map: map,
  };
}

describe("sidebarMode preference helper", () => {
  test("widths and breakpoint match Navigation 9.0 contract", () => {
    expect(SIDEBAR_RAIL_WIDTH).toBe(64);
    expect(SIDEBAR_EXPANDED_WIDTH).toBe(220);
    expect(SIDEBAR_COMPACT_BREAKPOINT).toBe(1366);
    expect(sidebarRailWidth(SIDEBAR_MODE_EXPANDED)).toBe(220);
    expect(sidebarRailWidth(SIDEBAR_MODE_COLLAPSED)).toBe(64);
    expect(sidebarRailWidth("nope")).toBe(64);
  });

  test("viewport defaults: expanded at 1366+, collapsed below", () => {
    expect(defaultSidebarModeForWidth(1440)).toBe(SIDEBAR_MODE_EXPANDED);
    expect(defaultSidebarModeForWidth(1366)).toBe(SIDEBAR_MODE_EXPANDED);
    expect(defaultSidebarModeForWidth(1365)).toBe(SIDEBAR_MODE_COLLAPSED);
    expect(defaultSidebarModeForWidth(1280)).toBe(SIDEBAR_MODE_COLLAPSED);
    expect(defaultSidebarModeForWidth(NaN)).toBe(SIDEBAR_MODE_COLLAPSED);
  });

  test("normalize and toggle only allow expanded/collapsed", () => {
    expect(normalizeSidebarMode("expanded")).toBe(SIDEBAR_MODE_EXPANDED);
    expect(normalizeSidebarMode("collapsed")).toBe(SIDEBAR_MODE_COLLAPSED);
    expect(normalizeSidebarMode("wide")).toBeNull();
    expect(toggleSidebarMode(SIDEBAR_MODE_EXPANDED)).toBe(SIDEBAR_MODE_COLLAPSED);
    expect(toggleSidebarMode(SIDEBAR_MODE_COLLAPSED)).toBe(SIDEBAR_MODE_EXPANDED);
  });

  test("storage is user-scoped and stores only mode string", () => {
    expect(sidebarModeStorageKey("user-a")).toBe(`${SIDEBAR_MODE_STORAGE_PREFIX}.user-a`);
    expect(sidebarModeStorageKey("user-b")).toBe(`${SIDEBAR_MODE_STORAGE_PREFIX}.user-b`);
    expect(sidebarModeStorageKey("")).toBe(`${SIDEBAR_MODE_STORAGE_PREFIX}.anon`);
    const storage = memoryStorage();
    expect(writeSidebarMode(storage, "user-a", SIDEBAR_MODE_EXPANDED)).toBe(SIDEBAR_MODE_EXPANDED);
    expect(storage.getItem(sidebarModeStorageKey("user-a"))).toBe("expanded");
    expect(readSidebarMode(storage, "user-a")).toBe(SIDEBAR_MODE_EXPANDED);
    expect(readSidebarMode(storage, "user-b")).toBeNull();
    writeSidebarMode(storage, "user-b", SIDEBAR_MODE_COLLAPSED);
    expect(readSidebarMode(storage, "user-a")).toBe(SIDEBAR_MODE_EXPANDED);
    expect(readSidebarMode(storage, "user-b")).toBe(SIDEBAR_MODE_COLLAPSED);
    expect(writeSidebarMode(storage, "user-a", "token")).toBeNull();
  });

  test("resolve prefers stored user choice over viewport default", () => {
    const storage = memoryStorage({
      [sidebarModeStorageKey("u1")]: SIDEBAR_MODE_COLLAPSED,
    });
    expect(resolveSidebarMode({ storage, userId: "u1", viewportWidth: 1440 })).toEqual({
      mode: SIDEBAR_MODE_COLLAPSED,
      userSet: true,
    });
    expect(resolveSidebarMode({ storage, userId: "u2", viewportWidth: 1440 })).toEqual({
      mode: SIDEBAR_MODE_EXPANDED,
      userSet: false,
    });
    expect(resolveSidebarMode({ storage, userId: "u2", viewportWidth: 1200 })).toEqual({
      mode: SIDEBAR_MODE_COLLAPSED,
      userSet: false,
    });
  });

  test("rejects non-mode stored values", () => {
    const storage = memoryStorage({
      [sidebarModeStorageKey("u1")]: JSON.stringify({ token: "x" }),
    });
    expect(readSidebarMode(storage, "u1")).toBeNull();
    expect(resolveSidebarMode({ storage, userId: "u1", viewportWidth: 1400 }).userSet).toBe(false);
  });
});
