import { LIVE_SEARCH_DEBOUNCE_MS, useDebouncedLiveSearch } from "./useDebouncedLiveSearch";

describe("useDebouncedLiveSearch", () => {
  test("exports 250–400ms debounce constant", () => {
    expect(LIVE_SEARCH_DEBOUNCE_MS).toBeGreaterThanOrEqual(250);
    expect(LIVE_SEARCH_DEBOUNCE_MS).toBeLessThanOrEqual(400);
  });

  test("module exports hook and constant", () => {
    expect(typeof useDebouncedLiveSearch).toBe("function");
  });
});
