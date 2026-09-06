import {
  formatMasterItemLabel,
  isDuplicateAccurateItem,
  listboxViewState,
  masterItemOptionKey,
  MASTER_ITEM_LIST_VISIBLE_ROWS,
  MASTER_ITEM_SEARCH_DEBOUNCE_MS,
  shouldApplySearchResult,
} from "./MasterItemSearchSelect";
import { filterFacetOptions, listboxViewState as facetViewState } from "./FacetSearchSelect";
import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "MasterItemSearchSelect.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "masterItemSearchSelect.css"), "utf8");

describe("MasterItemSearchSelect helpers", () => {
  test("formats label with name and code without using name as identity", () => {
    expect(formatMasterItemLabel({
      item_name: "Cup 16oz",
      item_code: "CUP-16",
      accurate_item_id: "AID-1",
    })).toBe("Cup 16oz — CUP-16");
    expect(masterItemOptionKey({
      item_name: "Cup 16oz",
      accurate_item_id: "AID-1",
    })).toBe("AID-1");
    expect(masterItemOptionKey({
      item_name: "Cup 16oz",
      accurate_item_id: "AID-2",
    })).toBe("AID-2");
  });

  test("same display name with different accurate_item_id stays separate", () => {
    const a = { item_name: "Cup Sama", accurate_item_id: "A1", id: "m1" };
    const b = { item_name: "Cup Sama", accurate_item_id: "A2", id: "m2" };
    expect(masterItemOptionKey(a)).not.toBe(masterItemOptionKey(b));
    expect(isDuplicateAccurateItem([a], b)).toBe(false);
    expect(isDuplicateAccurateItem([a], a)).toBe(true);
  });

  test("stale search responses are ignored", () => {
    expect(shouldApplySearchResult(3, 3)).toBe(true);
    expect(shouldApplySearchResult(2, 3)).toBe(false);
  });

  test("listbox never shows empty or results while pending/loading", () => {
    expect(listboxViewState({ pending: true, loading: false, error: "", itemCount: 5 })).toBe("loading");
    expect(listboxViewState({ pending: false, loading: true, error: "", itemCount: 5 })).toBe("loading");
    expect(listboxViewState({ pending: false, loading: false, error: "", itemCount: 0 })).toBe("empty");
    expect(listboxViewState({ pending: false, loading: false, error: "", itemCount: 2 })).toBe("results");
    expect(listboxViewState({ pending: true, loading: false, error: "", itemCount: 0 })).toBe("loading");
  });

  test("query A then B: A results must not remain selectable as B results", () => {
    // Simulate race: request A (id=1) then B (id=2) — only B applies.
    let latest = 0;
    let shown = [];
    latest = 1;
    const applyA = (items) => {
      if (shouldApplySearchResult(1, latest)) shown = items;
    };
    latest = 2;
    const applyB = (items) => {
      if (shouldApplySearchResult(2, latest)) shown = items;
    };
    applyA([{ item_name: "Cup", accurate_item_id: "A" }]);
    expect(shown).toEqual([]);
    applyB([{ item_name: "Box", accurate_item_id: "B" }]);
    expect(shown).toEqual([{ item_name: "Box", accurate_item_id: "B" }]);
    // Late A after B settled still ignored
    applyA([{ item_name: "Cup", accurate_item_id: "A" }]);
    expect(shown).toEqual([{ item_name: "Box", accurate_item_id: "B" }]);
  });

  test("component clears stale options and gates empty behind settled search", () => {
    expect(MASTER_ITEM_SEARCH_DEBOUNCE_MS).toBeGreaterThanOrEqual(250);
    expect(MASTER_ITEM_SEARCH_DEBOUNCE_MS).toBeLessThanOrEqual(400);
    expect(src).toMatch(/api\.masterItems/);
    expect(src).toMatch(/Produk tidak ditemukan/);
    expect(src).toMatch(/accurate_item_id/);
    expect(src).toMatch(/listboxViewState/);
    expect(src).toMatch(/setPending\(true\)/);
    expect(src).toMatch(/setItems\(\[\]\)/);
    expect(src).toMatch(/view === "empty"/);
    expect(src).toMatch(/view === "results"/);
    expect(src).toMatch(/pending \|\| loading/);
  });
});

describe("MasterItemSearchSelect 5-row list capacity", () => {
  test("visible capacity is 5 existing option rows, not a fixed 5-row height", () => {
    expect(MASTER_ITEM_LIST_VISIBLE_ROWS).toBe(5);
    expect(css).toMatch(/--mis-option-height:/);
    expect(css).toMatch(/--mis-list-max:\s*calc\(\(var\(--mis-option-height\) \* 5\)/);
    expect(css).not.toMatch(/height:\s*calc\(\(var\(--mis-option-height\) \* 5\)/);
    expect(css).toMatch(/overflow-y:\s*auto/);
    expect(src).toMatch(/createPortal/);
    expect(src).toMatch(/data-visible-rows=\{MASTER_ITEM_LIST_VISIBLE_ROWS\}/);
  });

  test("1–4 results stay content-sized; 5+ share the same max-height cap", () => {
    expect(src).toMatch(/view === "results" && items\.map/);
    expect(src).not.toMatch(/Array\.from\(\s*\{\s*length:\s*5/);
    expect(src).not.toMatch(/fake empty/);
    expect(css).not.toMatch(/min-height:\s*var\(--mis-list-max\)/);
  });

  test("click still selects the exact option identity; same name stays separate", () => {
    expect(src).toMatch(/onClick=\{\(\) => selectItem\(item\)\}/);
    expect(src).toMatch(/masterItemOptionKey\(item\)/);
    const a = { item_name: "Cup Sama", accurate_item_id: "A1" };
    const b = { item_name: "Cup Sama", accurate_item_id: "A2" };
    expect(masterItemOptionKey(a)).not.toBe(masterItemOptionKey(b));
  });
});

describe("FacetSearchSelect local product filter", () => {
  test("filters by code/name without merging same labels", () => {
    const opts = [
      { item_code: "A", item_name: "Cup", accurate_item_id: "1" },
      { item_code: "B", item_name: "Cup", accurate_item_id: "2" },
      { item_code: "C", item_name: "Box", accurate_item_id: "3" },
    ];
    const found = filterFacetOptions(opts, "cup", (o) => `${o.item_code} ${o.item_name}`);
    expect(found).toHaveLength(2);
    expect(found.map((o) => o.accurate_item_id)).toEqual(["1", "2"]);
  });

  test("pending state hides results", () => {
    expect(facetViewState({ pending: true, loading: false, error: "", itemCount: 3 })).toBe("loading");
  });
});
