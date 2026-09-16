import {
  MOBILE_ACTIVE_ROLE_KEY,
  MOBILE_DELIVERY_FILTER_KEY,
  MOBILE_PROD_FILTER_KEY,
  clearMobileOpsSessionForUser,
  clearSessionKey,
  readActiveRoleId,
  readJsonSession,
  scopedOpsKey,
  writeActiveRoleId,
  writeJsonSession,
} from "./mobileOpsSession";
import {
  clearAllMobileTaskListRetention,
  readMobileTaskListRetention,
  writeMobileTaskListRetention,
} from "./mobileTaskListRetention";

describe("mobileOpsSession user scoping", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    clearAllMobileTaskListRetention();
  });

  test("scopedOpsKey appends user id", () => {
    expect(scopedOpsKey(MOBILE_DELIVERY_FILTER_KEY, "u-1"))
      .toBe(`${MOBILE_DELIVERY_FILTER_KEY}.u-1`);
    expect(scopedOpsKey(MOBILE_DELIVERY_FILTER_KEY, "")).toBe(MOBILE_DELIVERY_FILTER_KEY);
  });

  test("CASE H — user A filters do not apply to user B", () => {
    writeJsonSession(scopedOpsKey(MOBILE_DELIVERY_FILTER_KEY, "user-a"), {
      filters: { po: "SECRET" },
      quick: "urgent",
    });
    const forB = readJsonSession(scopedOpsKey(MOBILE_DELIVERY_FILTER_KEY, "user-b"), {
      filters: {},
      quick: "semua_aktif",
    });
    expect(forB.quick).toBe("semua_aktif");
    expect(forB.filters).toEqual({});
  });

  test("logout clears scoped and legacy unscoped ops keys", () => {
    writeJsonSession(MOBILE_DELIVERY_FILTER_KEY, { quick: "legacy" });
    writeJsonSession(scopedOpsKey(MOBILE_DELIVERY_FILTER_KEY, "u-1"), { quick: "scoped" });
    writeJsonSession(scopedOpsKey(MOBILE_PROD_FILTER_KEY, "u-1"), { q: "x" });
    clearMobileOpsSessionForUser("u-1");
    expect(sessionStorage.getItem(MOBILE_DELIVERY_FILTER_KEY)).toBeNull();
    expect(sessionStorage.getItem(scopedOpsKey(MOBILE_DELIVERY_FILTER_KEY, "u-1"))).toBeNull();
    expect(sessionStorage.getItem(scopedOpsKey(MOBILE_PROD_FILTER_KEY, "u-1"))).toBeNull();
    clearSessionKey("noop");
  });

  test("active role is user-scoped localStorage and logout clears it", () => {
    writeActiveRoleId("u-1", "packing");
    writeActiveRoleId("u-2", "loading");
    expect(readActiveRoleId("u-1")).toBe("packing");
    expect(readActiveRoleId("u-2")).toBe("loading");
    expect(localStorage.getItem(scopedOpsKey(MOBILE_ACTIVE_ROLE_KEY, "u-1"))).toBe("packing");
    clearMobileOpsSessionForUser("u-1");
    expect(readActiveRoleId("u-1")).toBe("");
    expect(readActiveRoleId("u-2")).toBe("loading");
  });

  test("logout also drops in-memory task list snapshots", () => {
    const user = { id: "u-1", name: "Ada" };
    writeMobileTaskListRetention({
      user,
      division: "terkirim",
      permission: "delivery.update",
      hasPerm: (p) => p === "delivery.update",
      payload: { tasks: [{ task_id: "D1", order_number: "PO-DEL" }] },
    });
    expect(readMobileTaskListRetention({
      user,
      division: "terkirim",
      permission: "delivery.update",
      hasPerm: (p) => p === "delivery.update",
    }).tasks).toHaveLength(1);
    clearMobileOpsSessionForUser("u-1");
    expect(readMobileTaskListRetention({ user, division: "terkirim" })).toBeNull();
  });
});
