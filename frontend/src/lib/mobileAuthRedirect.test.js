import { isSafeMobileNextPath, loginPathForLocation, postLoginPath } from "./mobileAuthRedirect";

describe("mobile auth redirect", () => {
  test("preserves /m and in-app Optimasi Rute paths after login", () => {
    expect(isSafeMobileNextPath("/m/tasks")).toBe(true);
    expect(isSafeMobileNextPath("/m/orders/1?ws=packing")).toBe(true);
    expect(isSafeMobileNextPath("/optimasi-rute")).toBe(true);
    expect(isSafeMobileNextPath("/optimasi-rute/iro-1")).toBe(true);
    expect(isSafeMobileNextPath("/")).toBe(false);
    expect(isSafeMobileNextPath("/po/1")).toBe(false);
    expect(isSafeMobileNextPath("//evil")).toBe(false);
    expect(isSafeMobileNextPath("https://evil.example/m")).toBe(false);
  });

  test("unauthenticated mobile route stores next", () => {
    expect(loginPathForLocation("/m/tasks")).toBe("/login?next=%2Fm%2Ftasks");
    expect(loginPathForLocation("/optimasi-rute/iro-1")).toBe("/login?next=%2Foptimasi-rute%2Firo-1");
    expect(loginPathForLocation("/dashboard")).toBe("/login");
  });

  test("desktop login still lands on /", () => {
    expect(postLoginPath(null)).toBe("/");
    expect(postLoginPath("/po/1")).toBe("/");
    expect(postLoginPath("/m/tasks")).toBe("/m/tasks");
    expect(postLoginPath("/optimasi-rute/iro-1")).toBe("/optimasi-rute/iro-1");
  });
});
