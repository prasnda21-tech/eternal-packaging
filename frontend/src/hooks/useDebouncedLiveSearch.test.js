import {
  LIVE_SEARCH_DEBOUNCE_MS,
  isDebouncedSearchPending,
  liveSearchTableView,
  useDebouncedLiveSearch,
} from "./useDebouncedLiveSearch";

describe("useDebouncedLiveSearch", () => {
  test("exports 250–400ms debounce constant", () => {
    expect(LIVE_SEARCH_DEBOUNCE_MS).toBeGreaterThanOrEqual(250);
    expect(LIVE_SEARCH_DEBOUNCE_MS).toBeLessThanOrEqual(400);
  });

  test("module exports hook and constant", () => {
    expect(typeof useDebouncedLiveSearch).toBe("function");
  });

  test("pending when typed input differs from settled query", () => {
    expect(isDebouncedSearchPending("PC-12", "PC-12")).toBe(false);
    expect(isDebouncedSearchPending("PC-12", "cup")).toBe(true);
    expect(isDebouncedSearchPending("  101  ", "101")).toBe(false);
    expect(isDebouncedSearchPending("101", "")).toBe(true);
  });

  test("table view hides results while pending or loading; empty only after settle", () => {
    expect(liveSearchTableView({ pending: true, loading: false, itemCount: 5 })).toBe("loading");
    expect(liveSearchTableView({ pending: false, loading: true, itemCount: 5 })).toBe("loading");
    expect(liveSearchTableView({ pending: false, loading: false, itemCount: 0 })).toBe("empty");
    expect(liveSearchTableView({ pending: false, loading: false, itemCount: 2 })).toBe("results");
  });
});
