import { reconViewState, shouldApplyResponse } from "./cashBankReconView";

describe("reconciliation workspace view state", () => {
  test("error, forbidden, and conflict are not an empty success", () => {
    expect(reconViewState({ error: "gagal", count: 0 })).toBe("error");
    expect(reconViewState({ forbidden: true, count: 0 })).toBe("forbidden");
    expect(reconViewState({ conflict: true, error: "bentrok" })).toBe("conflict");
    expect(reconViewState({ loading: true, count: 0 })).toBe("loading");
    expect(reconViewState({ count: 0 })).toBe("empty");
    expect(reconViewState({ count: 2 })).toBe("results");
  });

  test("an older response does not replace the latest filter", () => {
    expect(shouldApplyResponse(1, 2)).toBe(false);
    expect(shouldApplyResponse(2, 2)).toBe(true);
  });
});
