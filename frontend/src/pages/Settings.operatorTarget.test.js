import fs from "fs";
import path from "path";

describe("Target Output Operator settings layout", () => {
  const src = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const tab = src.slice(src.indexOf("function OperatorTargetSettingsTab"), src.indexOf("function WorkPanelSettingsTab"));

  test("table uses full available width without prod-table or fixed max-width", () => {
    expect(tab).toMatch(/op-target-settings/);
    expect(tab).toMatch(/op-target-table-wrap/);
    expect(tab).toMatch(/op-target-table/);
    expect(tab).not.toMatch(/prod-table/);
    expect(tab).not.toMatch(/maxWidth:\s*720/);
    expect(tab).not.toMatch(/1200px|1400px|1600px|2480px/);
    expect(css).toMatch(/\.op-target-settings\{[^}]*max-width:100%/);
    expect(css).toMatch(/\.op-target-table-wrap\{[^}]*overflow-x:auto/);
    expect(css).toMatch(/\.op-target-table\{[^}]*width:100%/);
    expect(css).not.toMatch(/\.op-target-table\{[^}]*min-width:2480px/);
  });

  test("manual qty input stays in cell and is distinct when disabled", () => {
    expect(tab).toMatch(/op-target-qty-input/);
    expect(css).toMatch(/\.op-target-qty-input\{[^}]*max-width:140px/);
    expect(css).toMatch(/\.op-target-qty-input\{[^}]*box-sizing:border-box/);
    expect(css).toMatch(/\.op-target-qty-input:disabled\{/);
    expect(css).toMatch(/\.op-target-qty-input\{[^}]*background:#FBF4DE/);
  });
});
