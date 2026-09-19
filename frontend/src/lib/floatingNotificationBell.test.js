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
  sortNotificationsUnreadFirst,
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

  test("sortNotificationsUnreadFirst: 2 unread + 2 read keeps unread above newer read", () => {
    const mixed = [
      { id: "C", read_at: "2026-09-11T12:31:00Z", created_at: "2026-09-11T12:30:00Z" },
      { id: "A", read_at: null, created_at: "2026-09-11T12:00:00Z" },
      { id: "B", read_at: null, created_at: "2026-09-11T11:00:00Z" },
      { id: "D", read_at: "2026-09-11T10:01:00Z", created_at: "2026-09-11T10:00:00Z" },
    ];
    expect(sortNotificationsUnreadFirst(mixed).map((n) => n.id)).toEqual(["A", "B", "C", "D"]);
    expect(countUnread(mixed)).toBe(2);
  });

  test("sortNotificationsUnreadFirst: unread newest then older", () => {
    const unread = [
      { id: "old", read_at: null, created_at: "2026-09-11T11:00:00Z" },
      { id: "new", read_at: "", created_at: "2026-09-11T12:00:00Z" },
    ];
    expect(sortNotificationsUnreadFirst(unread).map((n) => n.id)).toEqual(["new", "old"]);
  });

  test("sortNotificationsUnreadFirst: read newest then older", () => {
    const read = [
      { id: "old", read_at: "x", created_at: "2026-09-11T10:00:00Z" },
      { id: "new", read_at: "y", created_at: "2026-09-11T12:30:00Z" },
    ];
    expect(sortNotificationsUnreadFirst(read).map((n) => n.id)).toEqual(["new", "old"]);
  });

  test("sortNotificationsUnreadFirst: mark unread as read moves into read group by created_at", () => {
    const before = [
      { id: "A", read_at: null, created_at: "2026-09-11T12:00:00Z" },
      { id: "B", read_at: null, created_at: "2026-09-11T11:00:00Z" },
      { id: "C", read_at: "z", created_at: "2026-09-11T12:30:00Z" },
      { id: "D", read_at: "z", created_at: "2026-09-11T10:00:00Z" },
    ];
    const afterMarkA = before.map((row) =>
      row.id === "A" ? { ...row, is_read: true, read_at: "2026-09-11T13:00:00Z" } : row
    );
    expect(sortNotificationsUnreadFirst(afterMarkA).map((n) => n.id)).toEqual(["B", "C", "A", "D"]);
    expect(countUnread(afterMarkA)).toBe(1);
  });

  test("sortNotificationsUnreadFirst: mark all read keeps created_at DESC and unread count 0", () => {
    const allRead = [
      { id: "A", read_at: "r", created_at: "2026-09-11T12:00:00Z" },
      { id: "B", read_at: "r", created_at: "2026-09-11T11:00:00Z" },
      { id: "C", read_at: "r", created_at: "2026-09-11T12:30:00Z" },
      { id: "D", read_at: "r", created_at: "2026-09-11T10:00:00Z" },
    ];
    expect(sortNotificationsUnreadFirst(allRead).map((n) => n.id)).toEqual(["C", "A", "B", "D"]);
    expect(countUnread(allRead)).toBe(0);
  });

  test("sortNotificationsUnreadFirst: refetch created_at DESC still unread-first", () => {
    const refetch = [
      { id: "C", read_at: "z", created_at: "2026-09-11T12:30:00Z" },
      { id: "A", read_at: "z", created_at: "2026-09-11T12:00:00Z" },
      { id: "B", read_at: null, created_at: "2026-09-11T11:00:00Z" },
      { id: "D", read_at: "z", created_at: "2026-09-11T10:00:00Z" },
    ];
    expect(sortNotificationsUnreadFirst(refetch).map((n) => n.id)).toEqual(["B", "C", "A", "D"]);
  });

  test("sortNotificationsUnreadFirst: does not mutate input and ignores is_read when read_at disagrees", () => {
    const input = [
      { id: "read-newer", is_read: false, read_at: "2026-09-11T12:30:00Z", created_at: "2026-09-11T12:30:00Z" },
      { id: "unread-older", is_read: true, read_at: null, created_at: "2026-09-11T11:00:00Z" },
    ];
    const snapshot = input.map((row) => ({ ...row }));
    expect(sortNotificationsUnreadFirst(input).map((n) => n.id)).toEqual(["unread-older", "read-newer"]);
    expect(input).toEqual(snapshot);
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
    expect(POLL_INTERVAL_MS).toBe(30000);
  });
});
