import {
  headerCheckState,
  pruneSelection,
  removeSucceeded,
  selectAllVisible,
  toggleId,
  visibleRowIds,
} from "./rowSelection";

describe("rowSelection visible-only scope", () => {
  const rows = [{ id: "a" }, { id: "b" }, { id: "c" }];

  test("visible ids skip blank", () => {
    expect(visibleRowIds([{ id: "a" }, { id: "" }, {}])).toEqual(["a"]);
  });

  test("select individual and deselect", () => {
    let s = toggleId(new Set(), "a");
    expect([...s]).toEqual(["a"]);
    s = toggleId(s, "a");
    expect([...s]).toEqual([]);
  });

  test("select all only current visible ids", () => {
    const visible = visibleRowIds(rows);
    const all = selectAllVisible(visible, new Set());
    expect(all).toEqual(new Set(["a", "b", "c"]));
    expect(selectAllVisible(visible, all)).toEqual(new Set());
  });

  test("filter change prunes hidden selection", () => {
    const selected = new Set(["a", "b", "hidden"]);
    expect(pruneSelection(selected, ["a"])).toEqual(new Set(["a"]));
  });

  test("header state for partial / all / none", () => {
    expect(headerCheckState(new Set(), ["a", "b"])).toEqual({ checked: false, indeterminate: false });
    expect(headerCheckState(new Set(["a"]), ["a", "b"])).toEqual({ checked: false, indeterminate: true });
    expect(headerCheckState(new Set(["a", "b"]), ["a", "b"])).toEqual({ checked: true, indeterminate: false });
  });

  test("partial bulk success keeps failed ids selected", () => {
    const left = removeSucceeded(new Set(["a", "b", "c"]), ["a", "c"]);
    expect(left).toEqual(new Set(["b"]));
  });
});
