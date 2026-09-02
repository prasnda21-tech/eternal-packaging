import {
  DASHBOARD_LOAD_FAILED,
  DASHBOARD_LOGIN_REQUIRED,
  DASHBOARD_SESSION_EXPIRED,
  dashboardFetchErrorMessage,
  dashboardPageView,
} from "./dashboardAuth";

const user = {
  email: "ops@eternalpackaging.com",
  name: "Ops",
  role: "head_production",
  permissions: ["nav.dashboard", "dashboard.production.view"],
};

function httpErr(status, detail) {
  return { response: { status, data: { detail } } };
}

describe("dashboardPageView", () => {
  test("auth loading is loading, not unauthenticated", () => {
    expect(dashboardPageView({ loading: true, user: null, data: null, err: "" })).toBe("loading");
    expect(dashboardPageView({ loading: true, user, data: null, err: "" })).toBe("loading");
  });

  test("no user after auth settled is unauthenticated", () => {
    expect(dashboardPageView({ loading: false, user: null, data: null, err: "" })).toBe("unauthenticated");
  });

  test("authenticated with payload is ready", () => {
    expect(dashboardPageView({
      loading: false,
      user,
      data: { kpi: { total_active: 1 } },
      err: "",
    })).toBe("ready");
  });

  test("authenticated fetch error is error, not unauthenticated", () => {
    expect(dashboardPageView({
      loading: false,
      user,
      data: null,
      err: DASHBOARD_LOAD_FAILED,
    })).toBe("error");
  });
});

describe("dashboardFetchErrorMessage", () => {
  test("TEST 1/2 — auth loading never yields login-required copy", () => {
    expect(dashboardFetchErrorMessage(httpErr(401, DASHBOARD_LOGIN_REQUIRED), {
      loading: true,
      user: null,
    })).toBe(null);
    expect(dashboardFetchErrorMessage(httpErr(401, DASHBOARD_LOGIN_REQUIRED), {
      loading: true,
      user,
    })).toBe(null);
  });

  test("TEST 3 — unauthenticated defers to existing app redirect (no dashboard login copy)", () => {
    expect(dashboardFetchErrorMessage(httpErr(401, DASHBOARD_LOGIN_REQUIRED), {
      loading: false,
      user: null,
    })).toBe(null);
  });

  test("TEST 4 — authenticated 401 is a data error, not logged-out", () => {
    expect(dashboardFetchErrorMessage(httpErr(401, DASHBOARD_LOGIN_REQUIRED), {
      loading: false,
      user,
    })).toBe(DASHBOARD_LOAD_FAILED);
    expect(dashboardFetchErrorMessage(httpErr(401, DASHBOARD_SESSION_EXPIRED), {
      loading: false,
      user,
    })).toBe(DASHBOARD_LOAD_FAILED);
  });

  test("TEST 4 — authenticated non-auth failure keeps dashboard error semantics", () => {
    expect(dashboardFetchErrorMessage(httpErr(500, "Internal Server Error"), {
      loading: false,
      user,
    })).toBe("Internal Server Error");
    expect(dashboardFetchErrorMessage(new Error("network down"), {
      loading: false,
      user,
    })).toBe("network down");
  });

  test("TEST 5 — 403 stays a permission problem, not logged-out", () => {
    const msg = dashboardFetchErrorMessage(
      httpErr(403, "Permission 'dashboard.production.view' diperlukan"),
      { loading: false, user },
    );
    expect(msg).toBe("Permission 'dashboard.production.view' diperlukan");
    expect(msg).not.toBe(DASHBOARD_LOGIN_REQUIRED);
  });
});
