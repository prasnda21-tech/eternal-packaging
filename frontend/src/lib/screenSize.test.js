import {
  lineItemScreenSize,
  normalizeScreenSize,
  screenSizeLabel,
  SCREEN_SIZE_LARGE,
  SCREEN_SIZE_SMALL,
} from "./screenSize";

describe("screenSize planning labels", () => {
  test("CASE 1 — large → Screen Besar", () => {
    expect(normalizeScreenSize("large")).toBe(SCREEN_SIZE_LARGE);
    expect(screenSizeLabel("large")).toBe("Screen Besar");
  });

  test("CASE 2 — small → Screen Kecil", () => {
    expect(normalizeScreenSize("small")).toBe(SCREEN_SIZE_SMALL);
    expect(screenSizeLabel("small")).toBe("Screen Kecil");
  });

  test("CASE 5 — missing/legacy is em dash", () => {
    expect(normalizeScreenSize(null)).toBe(null);
    expect(normalizeScreenSize("")).toBe(null);
    expect(screenSizeLabel(undefined)).toBe("—");
    expect(screenSizeLabel("huge")).toBe("—");
  });

  test("CASE 3 — same-name lines stay independent by index", () => {
    const order = {
      line_items: [
        { id: "L1", name: "Cup 16oz", screen_size: "large" },
        { id: "L2", name: "Cup 16oz", screen_size: "small" },
      ],
    };
    expect(lineItemScreenSize(order, 0)).toBe("large");
    expect(lineItemScreenSize(order, 1)).toBe("small");
    expect(screenSizeLabel(lineItemScreenSize(order, 0))).toBe("Screen Besar");
    expect(screenSizeLabel(lineItemScreenSize(order, 1))).toBe("Screen Kecil");
  });
});
