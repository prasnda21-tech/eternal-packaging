import fs from "fs";
import path from "path";
import {
  getRouteButtonClass,
  shouldDispatchRouteUpdate,
} from "./ProductionRoutePanel";

describe("ProductionRoutePanel - Strict Binary Classification & Neutral Initial State", () => {
  const panelSrc = fs.readFileSync(path.join(__dirname, "ProductionRoutePanel.jsx"), "utf8");

  describe("1. Unclassified initial state (undefined, null, empty)", () => {
    test("unclassified line items render both Cetak and Tidak Cetak in neutral ghost sm style", () => {
      expect(getRouteButtonClass(undefined, "print")).toBe("ghost sm");
      expect(getRouteButtonClass(undefined, "non_print")).toBe("ghost sm");
      expect(getRouteButtonClass(null, "print")).toBe("ghost sm");
      expect(getRouteButtonClass(null, "non_print")).toBe("ghost sm");
      expect(getRouteButtonClass("", "print")).toBe("ghost sm");
      expect(getRouteButtonClass("", "non_print")).toBe("ghost sm");
    });

    test("source code removes false visual fallback (no defaulting unclassified to print in styling)", () => {
      expect(panelSrc).not.toMatch(/\(li\.production_process\s*\|\|\s*["']print["']\)/);
    });
  });

  describe("2. Explicit print state", () => {
    test("explicit print marks Cetak active (primary sm) and Tidak Cetak neutral (ghost sm)", () => {
      expect(getRouteButtonClass("print", "print")).toBe("primary sm");
      expect(getRouteButtonClass("print", "non_print")).toBe("ghost sm");
      expect(getRouteButtonClass("PRINT", "print")).toBe("primary sm");
    });
  });

  describe("3. Explicit non_print state", () => {
    test("explicit non_print marks Tidak Cetak active (primary sm) and Cetak neutral (ghost sm)", () => {
      expect(getRouteButtonClass("non_print", "non_print")).toBe("primary sm");
      expect(getRouteButtonClass("non_print", "print")).toBe("ghost sm");
      expect(getRouteButtonClass("NON_PRINT", "non_print")).toBe("primary sm");
    });
  });

  describe("4. First click Cetak from unclassified", () => {
    test("first click Cetak dispatches route update to print", () => {
      expect(shouldDispatchRouteUpdate(undefined, "print")).toBe(true);
      expect(shouldDispatchRouteUpdate(null, "print")).toBe(true);
      expect(shouldDispatchRouteUpdate("", "print")).toBe(true);
    });
  });

  describe("5. First click Tidak Cetak from unclassified", () => {
    test("first click Tidak Cetak dispatches route update to non_print", () => {
      expect(shouldDispatchRouteUpdate(undefined, "non_print")).toBe(true);
      expect(shouldDispatchRouteUpdate(null, "non_print")).toBe(true);
      expect(shouldDispatchRouteUpdate("", "non_print")).toBe(true);
    });
  });

  describe("6. Same active Cetak clicked again (NO CLEAR / NO-OP)", () => {
    test("same active Cetak button does not dispatch redundant API call", () => {
      expect(shouldDispatchRouteUpdate("print", "print")).toBe(false);
      expect(shouldDispatchRouteUpdate("PRINT", "print")).toBe(false);
    });

    test("Cetak remains active and is not cleared or unset", () => {
      expect(getRouteButtonClass("print", "print")).toBe("primary sm");
      expect(getRouteButtonClass("print", "non_print")).toBe("ghost sm");
    });
  });

  describe("7. Same active Tidak Cetak clicked again (NO CLEAR / NO-OP)", () => {
    test("same active Tidak Cetak button does not dispatch redundant API call", () => {
      expect(shouldDispatchRouteUpdate("non_print", "non_print")).toBe(false);
      expect(shouldDispatchRouteUpdate("NON_PRINT", "non_print")).toBe(false);
    });

    test("Tidak Cetak remains active and is not cleared or unset", () => {
      expect(getRouteButtonClass("non_print", "non_print")).toBe("primary sm");
      expect(getRouteButtonClass("non_print", "print")).toBe("ghost sm");
    });
  });

  describe("8. Switching Cetak -> Tidak Cetak", () => {
    test("clicking Tidak Cetak when currently print dispatches non_print update", () => {
      expect(shouldDispatchRouteUpdate("print", "non_print")).toBe(true);
      // After update, style switches
      expect(getRouteButtonClass("non_print", "non_print")).toBe("primary sm");
      expect(getRouteButtonClass("non_print", "print")).toBe("ghost sm");
    });
  });

  describe("9. Switching Tidak Cetak -> Cetak", () => {
    test("clicking Cetak when currently non_print dispatches print update", () => {
      expect(shouldDispatchRouteUpdate("non_print", "print")).toBe(true);
      // After update, style switches
      expect(getRouteButtonClass("print", "print")).toBe("primary sm");
      expect(getRouteButtonClass("print", "non_print")).toBe("ghost sm");
    });
  });

  describe("10. Exact line_idx independent", () => {
    test("modifying one line item route does not alter other line items or duplicate named lines", () => {
      const orderLines = [
        { name: "Cup 16oz", quantity: 1000, production_process: "print" },
        { name: "Cup 16oz", quantity: 2000, production_process: undefined },
        { name: "Sedotan", quantity: 500, production_process: "non_print" },
      ];

      // Line 0 is print, Line 1 is unclassified, Line 2 is non_print
      expect(getRouteButtonClass(orderLines[0].production_process, "print")).toBe("primary sm");
      expect(getRouteButtonClass(orderLines[1].production_process, "print")).toBe("ghost sm");
      expect(getRouteButtonClass(orderLines[1].production_process, "non_print")).toBe("ghost sm");
      expect(getRouteButtonClass(orderLines[2].production_process, "non_print")).toBe("primary sm");

      // Line 1 is clicked with Cetak -> becomes print
      const line1NewProcess = "print";
      expect(getRouteButtonClass(line1NewProcess, "print")).toBe("primary sm");

      // Line 0 and Line 2 remain completely unchanged
      expect(getRouteButtonClass(orderLines[0].production_process, "print")).toBe("primary sm");
      expect(getRouteButtonClass(orderLines[2].production_process, "non_print")).toBe("primary sm");
    });
  });

  describe("10b. Per-line color is line-specific", () => {
    test("panel renders each line color independently from persisted line.color", () => {
      expect(panelSrc).toMatch(/lis\.map\(\(li, idx\) =>/);
      expect(panelSrc).toMatch(/\{li\.color && <div className="muted small">\{li\.color\}<\/div>\}/);
      expect(panelSrc).not.toMatch(/order\.color/);
      expect(panelSrc).not.toMatch(/line_items\[0\]\.color/);
    });
  });

  describe("11. Disabled and busy behavior preserved", () => {
    test("buttons have proper test IDs, handlers, and disabled guarding", () => {
      expect(panelSrc).toMatch(/data-testid=\{`route-print-\$\{idx\}`\}/);
      expect(panelSrc).toMatch(/data-testid=\{`route-nonprint-\$\{idx\}`\}/);
      expect(panelSrc).toMatch(/disabled=\{!canEdit \|\| busy\}/);
      expect(panelSrc).toMatch(/onSelect=\{handleSelectRoute\}/);
      expect(panelSrc).toMatch(/onSelect\(idx, "print"\)/);
      expect(panelSrc).toMatch(/onSelect\(idx, "non_print"\)/);
    });
  });
});
