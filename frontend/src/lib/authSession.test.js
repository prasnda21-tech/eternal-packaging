import { loginThenLoadUser } from "./authSession";

describe("loginThenLoadUser", () => {
  test("returns /auth/me payload after login succeeds", async () => {
    const loginRequest = jest.fn().mockResolvedValue({ email: "admin@eternalpackaging.com" });
    const meRequest = jest.fn().mockResolvedValue({
      email: "admin@eternalpackaging.com",
      role: "admin",
      permissions: ["po.view"],
    });
    const me = await loginThenLoadUser(loginRequest, meRequest);
    expect(loginRequest).toHaveBeenCalledTimes(1);
    expect(meRequest).toHaveBeenCalledTimes(1);
    expect(me.role).toBe("admin");
  });

  test("rejects when login is 200 but /auth/me fails (cookie not stored)", async () => {
    const loginRequest = jest.fn().mockResolvedValue({ email: "admin@eternalpackaging.com" });
    const meRequest = jest.fn().mockRejectedValue({ response: { status: 401 } });
    await expect(loginThenLoadUser(loginRequest, meRequest)).rejects.toMatchObject({
      response: { status: 401 },
    });
  });
});
