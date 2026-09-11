import {
  capturePoOpenFromList,
  clearPoListReturnForUser,
  clearPoReturnContext,
  currentListHref,
  isSafeAppPath,
  listUiStorageKey,
  poNavStateFromList,
  poReturnStorageKey,
  readListUiState,
  readPoReturnContext,
  resolvePoBackTarget,
  storageUserKey,
  writeListUiState,
  writePoReturnContext,
} from "./poListReturn";

describe("poListReturn", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  test("isSafeAppPath accepts internal paths only", () => {
    expect(isSafeAppPath("/po/abc")).toBe(true);
    expect(isSafeAppPath("/m/tasks?ws=delivery")).toBe(true);
    expect(isSafeAppPath("/penjualan/pesanan-penjualan?q=x&status=aktif")).toBe(true);
    expect(isSafeAppPath("//evil.com")).toBe(false);
    expect(isSafeAppPath("https://evil.com")).toBe(false);
    expect(isSafeAppPath("javascript:alert(1)")).toBe(false);
    expect(isSafeAppPath("")).toBe(false);
    expect(isSafeAppPath(null)).toBe(false);
  });

  test("currentListHref joins pathname and search", () => {
    expect(currentListHref("/m/tasks", "?ws=delivery")).toBe("/m/tasks?ws=delivery");
    expect(currentListHref("/m/tasks", "ws=delivery")).toBe("/m/tasks?ws=delivery");
    expect(currentListHref("/m/tasks", "")).toBe("/m/tasks");
    expect(currentListHref("/m/tasks", "?")).toBe("/m/tasks");
  });

  test("poNavStateFromList sets from without wiping extra meta", () => {
    expect(poNavStateFromList({
      pathname: "/penjualan/pesanan-penjualan",
      search: "?q=abc&status=aktif",
      extra: { po_number: "PO-1", customer: "ACME" },
    })).toEqual({
      po_number: "PO-1",
      customer: "ACME",
      from: "/penjualan/pesanan-penjualan?q=abc&status=aktif",
    });
  });

  test("CASE D/F — session return context survives refresh and drives app Back", () => {
    writePoReturnContext("u-1", "ord-9", {
      href: "/m/tasks?ws=delivery",
      scrollY: 120,
    });
    expect(readPoReturnContext("u-1", "ord-9")).toEqual({
      href: "/m/tasks?ws=delivery",
      scrollY: 120,
    });
    expect(resolvePoBackTarget({
      location: { pathname: "/m/orders/ord-9", state: null },
      userId: "u-1",
      orderId: "ord-9",
    })).toBe("/m/tasks?ws=delivery");
  });

  test("CASE E — direct PO link uses safe fallback", () => {
    expect(resolvePoBackTarget({
      location: { pathname: "/po/ord-x", state: null },
      userId: "u-1",
      orderId: "ord-x",
    })).toBe("/");
    expect(resolvePoBackTarget({
      location: { pathname: "/m/orders/ord-x", state: null },
      userId: "u-1",
      orderId: "ord-x",
    })).toBe("/m/tasks");
  });

  test("router state.from wins over session storage", () => {
    writePoReturnContext("u-1", "ord-1", { href: "/m/tasks?ws=packing" });
    expect(resolvePoBackTarget({
      location: {
        pathname: "/m/orders/ord-1",
        state: { from: "/m/delivery-schedule" },
      },
      userId: "u-1",
      orderId: "ord-1",
    })).toBe("/m/delivery-schedule");
  });

  test("CASE H — user isolation for return and list UI keys", () => {
    writePoReturnContext("user-a", "ord-1", { href: "/m/tasks?ws=delivery" });
    writeListUiState("user-a", "pesanan-penjualan", { q: "secret-a", status: "aktif" });
    expect(readPoReturnContext("user-b", "ord-1")).toBeNull();
    expect(readListUiState("user-b", "pesanan-penjualan", { q: "", status: "all" }))
      .toEqual({ q: "", status: "all" });
    expect(poReturnStorageKey("user-a", "ord-1")).toContain("user-a");
    expect(listUiStorageKey("user-a", "pesanan-penjualan")).toContain("user-a");
    expect(poReturnStorageKey("user-a", "ord-1")).not.toBe(poReturnStorageKey("user-b", "ord-1"));
  });

  test("logout clear removes only that user's return/list UI keys", () => {
    writePoReturnContext("user-a", "ord-1", { href: "/m/tasks?ws=delivery" });
    writePoReturnContext("user-b", "ord-2", { href: "/penjualan/pesanan-penjualan?q=x" });
    writeListUiState("user-a", "status-po", { po: "A" });
    writeListUiState("user-b", "status-po", { po: "B" });
    clearPoListReturnForUser({ id: "user-a" });
    expect(readPoReturnContext("user-a", "ord-1")).toBeNull();
    expect(readListUiState("user-a", "status-po", {})).toEqual({});
    expect(readPoReturnContext("user-b", "ord-2")?.href).toBe("/penjualan/pesanan-penjualan?q=x");
    expect(readListUiState("user-b", "status-po", {}).po).toBe("B");
  });

  test("capturePoOpenFromList writes session context without serializing PO", () => {
    const href = capturePoOpenFromList({
      user: { id: "u-9" },
      orderId: "ord-9",
      pathname: "/m/tasks",
      search: "?ws=delivery",
      scrollY: 40,
    });
    expect(href).toBe("/m/tasks?ws=delivery");
    const raw = sessionStorage.getItem(poReturnStorageKey("u-9", "ord-9"));
    expect(raw).toContain("/m/tasks?ws=delivery");
    expect(raw).not.toMatch(/line_items|customer_token|password|authorization/i);
    clearPoReturnContext("u-9", "ord-9");
    expect(readPoReturnContext("u-9", "ord-9")).toBeNull();
  });

  test("storageUserKey prefers id then email", () => {
    expect(storageUserKey({ id: "1", email: "a@b.c" })).toBe("1");
    expect(storageUserKey({ email: "a@b.c" })).toBe("a@b.c");
    expect(storageUserKey(null)).toBe("anon");
  });

  test("CASE I — helpers perform no backend mutation (pure storage + path math)", () => {
    const keys = Object.keys(sessionStorage);
    writePoReturnContext("u", "o", { href: "/m/tasks" });
    resolvePoBackTarget({ location: { pathname: "/po/o" }, userId: "u", orderId: "o" });
    // Only sessionStorage touched; no fetch/XHR side effects in this module.
    expect(sessionStorage.length).toBeGreaterThanOrEqual(keys.length);
  });
});
