import fs from "fs";
import path from "path";

const appendQty = fs.readFileSync(path.join(__dirname, "AppendQtyEntries.jsx"), "utf8");
const operatorSplit = fs.readFileSync(path.join(__dirname, "OperatorSplitPanel.jsx"), "utf8");

describe("Qty chain guard and Penjadwalan draft retention", () => {
  test("qty confirmation is blocked when additional qty exceeds upstream remaining", () => {
    expect(appendQty).toMatch(/const exceedsRemaining = remainingQty != null && draftQty > remainingQty/);
    expect(appendQty).toMatch(/disabled=\{busy \|\| draftQty <= 0 \|\| exceedsRemaining\}/);
    expect(appendQty).toMatch(/Maksimal tambahan/);
  });

  test("auto refresh does not overwrite a dirty Penjadwalan draft", () => {
    expect(operatorSplit).toMatch(/const scheduleDraftDirtyRef = useRef\(false\)/);
    expect(operatorSplit).toMatch(/if \(scheduleDraftDirtyRef\.current && hydratedScheduleKeyRef\.current\) return/);
    expect(operatorSplit).toMatch(/scheduleDraftDirtyRef\.current = true/);
    expect(operatorSplit).toMatch(/scheduleDraftDirtyRef\.current = false;\s+onUpdated\(res\.order\)/);
  });
});
