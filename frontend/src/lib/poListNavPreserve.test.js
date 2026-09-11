import fs from "fs";
import path from "path";
import {
  capturePoOpenFromList,
  currentListHref,
  poNavStateFromList,
  readListUiState,
  resolvePoBackTarget,
  storageUserKey,
  writeListUiState,
  writePoReturnContext,
} from "./poListReturn";

const root = path.join(__dirname, "..");

describe("preserve PO list navigation state (integration contracts)", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  test("CASE A — web PesananPenjualan syncs q/status into URL and PO links carry from", () => {
    const src = fs.readFileSync(path.join(root, "pages/PesananPenjualan.jsx"), "utf8");
    expect(src).toMatch(/useSearchParams/);
    expect(src).toMatch(/setSearchParams\(next,\s*\{\s*replace:\s*true\s*\}\)/);
    expect(src).toMatch(/poNavStateFromList/);
    expect(src).toMatch(/capturePoOpenFromList/);
    expect(src).toMatch(/searchParams\.get\("q"\)/);
    expect(src).toMatch(/searchParams\.get\("status"\)/);
  });

  test("CASE B — PWA driver Pengiriman workspace is URL-backed on /m/tasks", () => {
    const tasks = fs.readFileSync(path.join(root, "mobile/MobileTasksPage.jsx"), "utf8");
    const card = fs.readFileSync(path.join(root, "mobile/components/MobileTaskCard.jsx"), "utf8");
    expect(tasks).toMatch(/useSearchParams/);
    expect(tasks).toMatch(/next\.set\("ws"/);
    expect(tasks).toMatch(/replace:\s*true/);
    expect(card).toMatch(/fromTask:\s*true/);
    expect(card).toMatch(/capturePoOpenFromList/);
    expect(card).toMatch(/poNavStateFromList/);
  });

  test("CASE C/D — app Back resolves originating list; never navigate(-1) on PO detail", () => {
    const web = fs.readFileSync(path.join(root, "pages/PODetail.jsx"), "utf8");
    const mobile = fs.readFileSync(path.join(root, "mobile/MobileOrderDetailPage.jsx"), "utf8");
    expect(web).toMatch(/resolvePoBackTarget/);
    expect(web).not.toMatch(/navigate\(-1\)/);
    expect(mobile).toMatch(/resolvePoBackTarget/);
    expect(mobile).not.toMatch(/navigate\(-1\)/);

    const from = "/penjualan/pesanan-penjualan?q=ACME&status=aktif";
    expect(resolvePoBackTarget({
      location: { pathname: "/po/ord-1", state: { from } },
      userId: "u1",
      orderId: "ord-1",
    })).toBe(from);
  });

  test("CASE E — direct PO link safe fallback", () => {
    expect(resolvePoBackTarget({
      location: { pathname: "/po/x", state: null },
      userId: "u1",
      orderId: "x",
    })).toBe("/");
    expect(resolvePoBackTarget({
      location: { pathname: "/m/orders/x", state: null },
      userId: "u1",
      orderId: "x",
    })).toBe("/m/tasks");
  });

  test("CASE F — refresh detail keeps session return href", () => {
    writePoReturnContext("u1", "ord-9", { href: "/m/tasks?ws=delivery" });
    expect(resolvePoBackTarget({
      location: { pathname: "/m/orders/ord-9", state: null },
      userId: "u1",
      orderId: "ord-9",
    })).toBe("/m/tasks?ws=delivery");
  });

  test("CASE G — return context stores href only, not API list payload", () => {
    const href = capturePoOpenFromList({
      user: { id: "u1" },
      orderId: "ord-1",
      pathname: "/m/delivery-schedule",
      search: "",
    });
    expect(href).toBe("/m/delivery-schedule");
    const raw = sessionStorage.getItem(`ep.poReturn.u1.ord-1`);
    expect(raw).toContain("/m/delivery-schedule");
    expect(raw).not.toMatch(/"orders"|line_items|qty_delivered|driver_name/);
  });

  test("CASE H — list UI session is user-scoped", () => {
    writeListUiState("alice", "delivery-schedule", { filters: { po: "A" } });
    expect(readListUiState("bob", "delivery-schedule", { filters: {} }).filters).toEqual({});
    expect(storageUserKey({ id: "alice" })).toBe("alice");
  });

  test("CASE I — auth logout clears return + mobile ops session (source)", () => {
    const auth = fs.readFileSync(path.join(root, "lib/auth.jsx"), "utf8");
    expect(auth).toMatch(/clearPoListReturnForUser/);
    expect(auth).toMatch(/clearMobileOpsSessionForUser/);
  });

  test("browser-back friendly: URL href preserves filters without replace on PO open", () => {
    expect(currentListHref("/m/tasks", "?ws=delivery")).toBe("/m/tasks?ws=delivery");
    expect(poNavStateFromList({
      pathname: "/m/tasks",
      search: "?ws=delivery",
    }).from).toBe("/m/tasks?ws=delivery");
    const delivery = fs.readFileSync(path.join(root, "mobile/MobileDeliverySchedulePage.jsx"), "utf8");
    expect(delivery).toMatch(/writeJsonSession\(filterKey/);
    expect(delivery).toMatch(/scopedOpsKey\(MOBILE_DELIVERY_FILTER_KEY/);
    expect(delivery).toMatch(/sort/);
  });
});
