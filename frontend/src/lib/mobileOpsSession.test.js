import {
  MOBILE_DELIVERY_FILTER_KEY,
  MOBILE_PROD_FILTER_KEY,
  clearMobileOpsSessionForUser,
  clearSessionKey,
  readJsonSession,
  scopedOpsKey,
  writeJsonSession,
} from "./mobileOpsSession";

describe("mobileOpsSession user scoping", () => {
  beforeEach(() => {
    sessionStorage.clear();
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
});
