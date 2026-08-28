import {
  mapPersistedSplits,
  patchScheduleRow,
  shouldApplyRemoteSplits,
  splitsAfterMutation,
  splitsFingerprint,
} from "./scheduleSplits";

describe("Penjadwalan sequential Set persistence", () => {
  const itemA = { line_idx: 0, operator_email: "", operator_name: "", quantity: 100 };
  const itemAOpB = {
    line_idx: 0,
    operator_email: "b@x.com",
    operator_name: "B",
    quantity: 100,
  };

  test("selecting item then operator keeps both fields", () => {
    let draft = mapPersistedSplits([]);
    draft = [...draft, { operator_email: "", operator_name: "", quantity: 0, line_idx: "", work_date: "" }];
    draft = patchScheduleRow(draft, 0, { line_idx: 0, quantity: 100 });
    expect(draft[0].line_idx).toBe(0);
    draft = patchScheduleRow(draft, 0, { operator_email: "b@x.com", operator_name: "B" });
    expect(draft[0].line_idx).toBe(0);
    expect(draft[0].operator_email).toBe("b@x.com");
  });

  test("selecting a new operator does not clear item", () => {
    const next = patchScheduleRow([itemAOpB], 0, { operator_email: "c@x.com", operator_name: "C" });
    expect(next[0].line_idx).toBe(0);
    expect(next[0].quantity).toBe(100);
    expect(next[0].operator_email).toBe("c@x.com");
  });

  test("selecting a new item does not clear operator", () => {
    const next = patchScheduleRow([itemAOpB], 0, { line_idx: 1 });
    expect(next[0].operator_email).toBe("b@x.com");
    expect(next[0].line_idx).toBe(1);
  });

  test("refetch of the same saved payload does not wipe draft fingerprint", () => {
    const saved = mapPersistedSplits([{ ...itemAOpB, id: "s1" }]);
    const fp = splitsFingerprint(saved);
    expect(shouldApplyRemoteSplits({
      dirty: false,
      incomingFingerprint: splitsFingerprint([{ ...itemAOpB, id: "s1" }]),
      savedFingerprint: fp,
      incomingUpdatedAt: "2026-08-28T10:00:00Z",
      savedUpdatedAt: "2026-08-28T10:00:00Z",
    })).toBe(false);
  });

  test("auto-refresh does not reset unsaved item/operator (dirty draft)", () => {
    const local = patchScheduleRow([itemA], 0, { operator_email: "b@x.com", operator_name: "B" });
    expect(shouldApplyRemoteSplits({
      dirty: true,
      incomingFingerprint: splitsFingerprint([]),
      savedFingerprint: splitsFingerprint([]),
      incomingUpdatedAt: "2026-08-28T10:01:00Z",
      savedUpdatedAt: "",
    })).toBe(false);
    expect(local[0].line_idx).toBe(0);
    expect(local[0].operator_email).toBe("b@x.com");
  });

  test("stale poll older than last save is ignored", () => {
    expect(shouldApplyRemoteSplits({
      dirty: false,
      incomingFingerprint: splitsFingerprint([]),
      savedFingerprint: splitsFingerprint([{ ...itemAOpB, id: "s1" }]),
      incomingUpdatedAt: "2026-08-28T09:00:00Z",
      savedUpdatedAt: "2026-08-28T10:00:00Z",
    })).toBe(false);
  });

  test("failed mutation keeps local draft", () => {
    const local = [itemAOpB];
    expect(splitsAfterMutation(false, local, [])).toEqual(local);
  });

  test("successful mutation hydrates from server including both fields", () => {
    const local = [itemAOpB];
    const remote = [{ ...itemAOpB, id: "s1", qty_completed: 0 }];
    const next = splitsAfterMutation(true, local, remote);
    expect(next[0].id).toBe("s1");
    expect(next[0].line_idx).toBe(0);
    expect(next[0].operator_email).toBe("b@x.com");
  });
});
