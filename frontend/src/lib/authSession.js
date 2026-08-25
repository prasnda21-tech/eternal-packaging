/**
 * POST /auth/login can return 200 while the session cookie is dropped
 * (cross-site localhost vs 127.0.0.1). Auth context is only authenticated
 * after /auth/me succeeds with that cookie.
 */
export async function loginThenLoadUser(loginRequest, meRequest) {
  await loginRequest();
  return meRequest();
}
