import { act } from "react";
import { createRoot } from "react-dom/client";
import MasterItemSearchSelect, { MASTER_ITEM_SEARCH_DEBOUNCE_MS } from "./MasterItemSearchSelect";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    masterItemsPicker: jest.fn(() => Promise.resolve({ items: [] })),
  },
}));

const mockApi = require("../lib/api").default;

global.IS_REACT_ACT_ENVIRONMENT = true;

const TYPE_SEQUENCE = "ABCDE123";

describe("MasterItemSearchSelect fetchItems + typing", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockApi.masterItemsPicker.mockClear();
    Element.prototype.scrollIntoView = jest.fn();
    Element.prototype.getBoundingClientRect = jest.fn(() => ({
      top: 80, bottom: 120, left: 40, right: 320, width: 280, height: 40,
    }));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("custom fetchItems is used and continuous typing keeps focus character-by-character", async () => {
    const fetchItems = jest.fn(async ({ q }) => ({
      items: q
        ? [{ id: "p1", item_code: "CUP", item_name: `Cup ${q}`, accurate_item_id: "a1" }]
        : [],
    }));
    await act(async () => {
      root.render(
        <MasterItemSearchSelect
          testId="prq-product-search"
          fetchItems={fetchItems}
          onChange={() => {}}
        />,
      );
    });
    const input = container.querySelector('[data-testid="prq-product-search-input"]');
    expect(input).toBeTruthy();
    const inputNode = input;
    await act(async () => {
      input.focus();
      input.dispatchEvent(new FocusEvent("focus", { bubbles: true }));
    });
    expect(document.activeElement).toBe(inputNode);

    for (let i = 0; i < TYPE_SEQUENCE.length; i += 1) {
      const ch = TYPE_SEQUENCE[i];
      await act(async () => {
        const next = `${input.value}${ch}`;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
        setter?.call(input, next);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      // Same DOM node + focus after every character (no remount / reset).
      expect(container.querySelector('[data-testid="prq-product-search-input"]')).toBe(inputNode);
      expect(document.activeElement).toBe(inputNode);
      expect(input.value).toBe(TYPE_SEQUENCE.slice(0, i + 1));
      expect(input.value.endsWith(ch)).toBe(true);
    }

    expect(input.value).toBe(TYPE_SEQUENCE);
    expect(input.value).not.toBe("");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, MASTER_ITEM_SEARCH_DEBOUNCE_MS + 30));
    });
    expect(fetchItems).toHaveBeenCalled();
    expect(mockApi.masterItemsPicker).not.toHaveBeenCalled();
    expect(fetchItems.mock.calls.some((call) => call[0]?.q === TYPE_SEQUENCE)).toBe(true);
  });
});
