import {
  canRetainPoDetail,
  clearPoDetailRetention,
  poDetailRetentionKey,
  readPoDetailRetention,
  writePoDetailRetention,
} from "./poDetailRetention";

const user = { id: "user-1", email: "ops@example.com" };
const allowed = (permission) => permission === "loading.update";
const denied = () => false;

beforeEach(() => clearPoDetailRetention());

test("retains one PO only for an authorized principal", () => {
  writePoDetailRetention({
    user,
    orderId: "po-1",
    hasPerm: allowed,
    order: { id: "po-1", po_number: "PO-1" },
  });

  expect(readPoDetailRetention({ user, orderId: "po-1", hasPerm: allowed }))
    .toEqual({ id: "po-1", po_number: "PO-1" });
  expect(readPoDetailRetention({ user, orderId: "po-1", hasPerm: denied }))
    .toBeNull();
});

test("never reuses a previous user's PO snapshot", () => {
  writePoDetailRetention({
    user,
    orderId: "po-1",
    hasPerm: allowed,
    order: { id: "po-1", customer: "Customer A" },
  });

  expect(readPoDetailRetention({
    user: { id: "user-2", email: "other@example.com" },
    orderId: "po-1",
    hasPerm: allowed,
  })).toBeNull();

  expect(readPoDetailRetention({ user, orderId: "po-1", hasPerm: allowed }))
    .toBeNull();
});

test("requires a stable user and order identity", () => {
  expect(canRetainPoDetail(allowed)).toBe(true);
  expect(poDetailRetentionKey(null, "po-1")).toBe("");
  expect(poDetailRetentionKey(user, "")).toBe("");
});
