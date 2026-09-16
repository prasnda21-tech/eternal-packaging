import {
  clearAllMobileTaskListRetention,
  deleteMobileTaskListRetention,
  httpStatusFromError,
  isAuthSessionExpiredError,
  isMobileTaskListRetentionAuthorized,
  mobileTaskListRetentionKey,
  readMobileTaskListRetention,
  writeMobileTaskListRetention,
} from "./mobileTaskListRetention";

const userA = { id: "u-a", name: "Ada" };
const userB = { id: "u-b", name: "Budi" };
const packing = { tasks: [{ task_id: "P1", order_number: "PO-PACK" }] };
const delivery = { tasks: [{ task_id: "D1", order_number: "PO-DEL" }] };
const allowDelivery = {
  hasPerm: (p) => p === "delivery.update",
  permission: "delivery.update",
};
const allowPacking = {
  hasPerm: (p) => p === "packing.update",
  permission: "packing.update",
};
const allowBoth = {
  hasPerm: (p) => ["delivery.update", "packing.update"].includes(p),
};

describe("mobileTaskListRetention", () => {
  beforeEach(() => {
    sessionStorage.clear();
    clearAllMobileTaskListRetention();
  });

  test("keys by authenticated user + division + mine scope + workspace permission", () => {
    expect(mobileTaskListRetentionKey(userA, "terkirim", "mine", "delivery.update"))
      .toBe("u-a::terkirim::mine::delivery.update");
    expect(mobileTaskListRetentionKey(null, "terkirim", "mine", "delivery.update")).toBe("");
    expect(mobileTaskListRetentionKey(userA, "terkirim", "mine", "")).toBe("");
    expect(mobileTaskListRetentionKey(userA, "", "mine", "delivery.update")).toBe("");
  });

  test("current hasPerm must still authorize the workspace permission", () => {
    expect(isMobileTaskListRetentionAuthorized({
      user: userA,
      division: "terkirim",
      ...allowDelivery,
    })).toBe(true);
    expect(isMobileTaskListRetentionAuthorized({
      user: userA,
      division: "terkirim",
      permission: "delivery.update",
      hasPerm: () => false,
    })).toBe(false);
    expect(isMobileTaskListRetentionAuthorized({
      user: userA,
      division: "terkirim",
      permission: "delivery.update",
    })).toBe(false);
  });

  test("same user + authorized same workspace reads last successful payload", () => {
    writeMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      payload: delivery,
      ...allowDelivery,
    });
    expect(readMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      ...allowDelivery,
    }).tasks).toEqual(delivery.tasks);
  });

  test("workspace switch does not return the other division's rows", () => {
    writeMobileTaskListRetention({
      user: userA,
      division: "packing",
      payload: packing,
      ...allowPacking,
      hasPerm: allowBoth.hasPerm,
    });
    writeMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      payload: delivery,
      ...allowDelivery,
      hasPerm: allowBoth.hasPerm,
    });
    expect(readMobileTaskListRetention({
      user: userA,
      division: "packing",
      ...allowPacking,
      hasPerm: allowBoth.hasPerm,
    }).tasks[0].task_id).toBe("P1");
    expect(readMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      ...allowDelivery,
      hasPerm: allowBoth.hasPerm,
    }).tasks[0].task_id).toBe("D1");
  });

  test("local permission loss does not return the retained snapshot", () => {
    writeMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      payload: delivery,
      ...allowDelivery,
    });
    expect(readMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      permission: "delivery.update",
      hasPerm: () => false,
    })).toBeNull();
    expect(readMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      permission: "packing.update",
      hasPerm: (p) => p === "packing.update",
    })).toBeNull();
  });

  test("reading as another user drops the prior user's snapshots", () => {
    writeMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      payload: delivery,
      ...allowDelivery,
    });
    expect(readMobileTaskListRetention({
      user: userB,
      division: "terkirim",
      ...allowDelivery,
    })).toBeNull();
    expect(readMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      ...allowDelivery,
    })).toBeNull();
  });

  test("delete drops one key; clearAll drops every snapshot", () => {
    writeMobileTaskListRetention({
      user: userA,
      division: "packing",
      payload: packing,
      ...allowPacking,
      hasPerm: allowBoth.hasPerm,
    });
    writeMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      payload: delivery,
      ...allowDelivery,
      hasPerm: allowBoth.hasPerm,
    });
    deleteMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      permission: "delivery.update",
    });
    expect(readMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      ...allowDelivery,
      hasPerm: allowBoth.hasPerm,
    })).toBeNull();
    expect(readMobileTaskListRetention({
      user: userA,
      division: "packing",
      ...allowPacking,
      hasPerm: allowBoth.hasPerm,
    }).tasks[0].task_id).toBe("P1");
    clearAllMobileTaskListRetention();
    expect(readMobileTaskListRetention({
      user: userA,
      division: "packing",
      ...allowPacking,
      hasPerm: allowBoth.hasPerm,
    })).toBeNull();
  });

  test("never writes task payloads to sessionStorage", () => {
    writeMobileTaskListRetention({
      user: userA,
      division: "terkirim",
      payload: delivery,
      ...allowDelivery,
    });
    expect(sessionStorage.length).toBe(0);
    expect(JSON.stringify(sessionStorage)).not.toContain("PO-DEL");
  });

  test("401 is session expiry; 403 is not", () => {
    expect(httpStatusFromError({ response: { status: 401 } })).toBe(401);
    expect(isAuthSessionExpiredError({ response: { status: 401 } })).toBe(true);
    expect(isAuthSessionExpiredError({ response: { status: 403 } })).toBe(false);
    expect(isAuthSessionExpiredError({ message: "Gagal memuat tugas." })).toBe(false);
    expect(isAuthSessionExpiredError({ response: { status: 500 } })).toBe(false);
  });
});
