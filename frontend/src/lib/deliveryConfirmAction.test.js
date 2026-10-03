import {
  CONFIRM_ACTION_STORAGE,
  clearConfirmAction,
  confirmActionKey,
  confirmPayloadToken,
  markConfirmActionUnknown,
  readConfirmAction,
  resetConfirmActionStateForTests,
  storeConfirmAction,
  unknownConfirmActionKeys,
} from "./deliveryConfirmAction";

const shipment = { id: "s1", lines: [{ line_idx: 0, qty: 10 }] };

function token(name, lines) {
  return confirmPayloadToken(name, { lines });
}

beforeEach(() => {
  sessionStorage.clear();
  resetConfirmActionStateForTests();
});

test("the same user, order, shipment, and payload reuse one action id", () => {
  const first = storeConfirmAction("driver-a", "po-1", "s1", token("Budi", shipment.lines), "act-1");
  const second = storeConfirmAction("driver-a", "po-1", "s1", token("Budi", shipment.lines), "act-2");
  expect(second.actionId).toBe("act-1");
  expect(second.key).toBe(first.key);
  const reloaded = readConfirmAction("driver-a", "po-1", "s1", token("Budi", shipment.lines));
  expect(reloaded.actionId).toBe("act-1");
});

test("a different shipment, user, or payload does not reuse the previous action", () => {
  storeConfirmAction("driver-a", "po-1", "s1", token("Budi", shipment.lines), "act-1");
  expect(storeConfirmAction("driver-a", "po-1", "s2", token("Budi", shipment.lines), "act-sj").actionId).toBe("act-sj");
  expect(storeConfirmAction("driver-b", "po-1", "s1", token("Budi", shipment.lines), "act-user").actionId).toBe("act-user");
  expect(storeConfirmAction("driver-a", "po-1", "s1", token("Siti", shipment.lines), "act-name").actionId).toBe("act-name");
  expect(storeConfirmAction("driver-a", "po-1", "s1", token("Budi", [{ line_idx: 0, qty: 12 }]), "act-qty").actionId).toBe("act-qty");
});

test("a legacy order-shipment token is not reused", () => {
  sessionStorage.setItem(CONFIRM_ACTION_STORAGE, JSON.stringify({ "po-1|s1": "global-token" }));
  const minted = storeConfirmAction("driver-a", "po-1", "s1", token("Budi", shipment.lines), "act-fresh");
  expect(minted.actionId).toBe("act-fresh");
  const stored = JSON.parse(sessionStorage.getItem(CONFIRM_ACTION_STORAGE));
  expect(stored.version).toBe(2);
  expect(JSON.stringify(stored)).not.toContain("global-token");
  expect(confirmActionKey("driver-a", "po-1", "s1", token("Budi", shipment.lines))).not.toBe("po-1|s1");
});

test("an unknown result stays on that operation until it is cleared", () => {
  const pending = storeConfirmAction("driver-a", "po-1", "s1", token("Budi", shipment.lines), "act-1");
  markConfirmActionUnknown(pending.key);
  expect(unknownConfirmActionKeys("driver-a", "po-1", "s1")).toEqual([pending.key]);
  expect(unknownConfirmActionKeys("driver-b", "po-1", "s1")).toEqual([]);
  expect(unknownConfirmActionKeys("driver-a", "po-1", "s2")).toEqual([]);
  clearConfirmAction(pending.key);
  expect(readConfirmAction("driver-a", "po-1", "s1", token("Budi", shipment.lines))).toBeNull();
});
