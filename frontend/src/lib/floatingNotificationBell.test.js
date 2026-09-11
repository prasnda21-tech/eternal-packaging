import {
  CLICK_DRAG_THRESHOLD_PX,
  POLL_INTERVAL_MS,
  clampBellPosition,
  countUnread,
  defaultBellPosition,
  decisionLabel,
  movementExceededThreshold,
  panelPlacement,
  readStoredBellPosition,
  storageKeyForUser,
  writeStoredBellPosition,
} from "./floatingNotificationBell";

describe("floatingNotificationBell helpers", () => {
  test("countUnread: zero when empty or all read", () => {
    expect(countUnread([])).toBe(0);
    expect(countUnread(null)).toBe(0);
    expect(
      countUnread([
        { id: "1", is_read: true, read_at: "2026-01-01T00:00:00Z" },
        { id: "2", is_read: true, read_at: "2026-01-01T00:00:00Z" },
      ])
    ).toBe(0);
  });

  test("countUnread: exact unread for one, two, and mixed", () => {
    expect(countUnread([{ id: "1", is_read: false, read_at: null }])).toBe(1);
    expect(
      countUnread([
        { id: "1", is_read: false, read_at: null },
        { id: "2", is_read: false },
      ])
    ).toBe(2);
    expect(
      countUnread([
        { id: "1", is_read: false },
        { id: "2", is_read: true, read_at: "x" },
        { id: "3", is_read: false },
        { id: "4", is_read: true, read_at: "y" },
        { id: "5", is_read: true, read_at: "z" },
      ])
    ).toBe(2);
  });

  test("countUnread: canonical source is read_at (is_read is API-normalized convenience)", () => {
    expect(countUnread([{ id: "1", is_read: false, read_at: "2026-01-01T00:00:00Z" }])).toBe(0);
    expect(countUnread([{ id: "2", is_read: true, read_at: null }])).toBe(1);
  });

  test("default position is bottom-right safe area", () => {
    const pos = defaultBellPosition({ width: 1000, height: 800 });
    expect(pos.x).toBeGreaterThan(800);
    expect(pos.y).toBeGreaterThan(700);
  });

  test("clampBellPosition keeps bell viewport-reachable", () => {
    const clamped = clampBellPosition({ x: -40, y: 9999 }, { width: 500, height: 400 });
    expect(clamped.x).toBeGreaterThanOrEqual(12);
    expect(clamped.y).toBeLessThanOrEqual(400 - 12);
    expect(clampBellPosition({ x: "bad", y: null }, { width: 500, height: 400 })).toEqual(
      defaultBellPosition({ width: 500, height: 400 })
    );
  });

  test("click vs drag threshold", () => {
    expect(
      movementExceededThreshold({ x: 10, y: 10 }, { x: 12, y: 11 }, CLICK_DRAG_THRESHOLD_PX)
    ).toBe(false);
    expect(
      movementExceededThreshold({ x: 10, y: 10 }, { x: 20, y: 10 }, CLICK_DRAG_THRESHOLD_PX)
    ).toBe(true);
  });

  test("position persistence is localStorage presentation only", () => {
    const store = {
      data: {},
      getItem(k) {
        return this.data[k] ?? null;
      },
      setItem(k, v) {
        this.data[k] = String(v);
      },
    };
    const key = storageKeyForUser("user-a");
    expect(key).toContain("ep.webNotifBell.pos");
    expect(key).toContain("user-a");
    writeStoredBellPosition(store, "user-a", { x: 120, y: 80 }, { width: 800, height: 600 });
    expect(store.data[key]).toContain("120");
    expect(readStoredBellPosition(store, "user-a", { width: 800, height: 600 })).toEqual({
      x: 120,
      y: 80,
    });
    const clamped = readStoredBellPosition(
      { getItem: () => JSON.stringify({ x: 9000, y: -20 }) },
      "user-a",
      { width: 400, height: 300 }
    );
    expect(clamped.x).toBeLessThan(400);
    expect(clamped.y).toBeGreaterThanOrEqual(12);
  });

  test("panelPlacement stays viewport-safe near right/bottom edges", () => {
    const nearCorner = panelPlacement(
      { x: 740, y: 520 },
      { width: 320, height: 380 },
      { width: 800, height: 600 }
    );
    expect(nearCorner.left).toBeGreaterThanOrEqual(12);
    expect(nearCorner.top).toBeGreaterThanOrEqual(12);
    expect(nearCorner.left + 320).toBeLessThanOrEqual(800 - 12);
    expect(nearCorner.top + 380).toBeLessThanOrEqual(600 - 12);
  });

  test("decision labels and poll interval constant", () => {
    expect(decisionLabel("approved")).toBe("Disetujui");
    expect(decisionLabel("rejected")).toBe("Ditolak");
    expect(POLL_INTERVAL_MS).toBe(15000);
  });
});
