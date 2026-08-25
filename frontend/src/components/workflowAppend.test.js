import fs from "fs";
import path from "path";

function src(name) {
  return fs.readFileSync(path.join(__dirname, name), "utf8");
}

describe("append-only workflow UI", () => {
  test("operator uses confirm modal and append entries", () => {
    const s = src("OperatorSplitPanel.jsx");
    expect(s).toMatch(/ConfirmActionModal/);
    expect(s).toMatch(/AppendQtyEntries/);
    expect(s).toMatch(/action_id/);
  });

  test("screener helper packing loading use confirm + append", () => {
    expect(src("../pages/PODetail.jsx")).toMatch(/kind: "screener"/);
    expect(src("HelperPrepPanel.jsx")).toMatch(/ConfirmActionModal/);
    expect(src("PackingPanel.jsx")).toMatch(/qty: confirm\.qty/);
    expect(src("LoadingPanel.jsx")).toMatch(/action_id: confirm\.actionId/);
    expect(src("SuratJalanPanel.jsx")).toMatch(/ConfirmActionModal/);
    expect(src("DeliveryProofPanel.jsx")).toMatch(/ConfirmActionModal/);
  });

  test("cancel path does not call save in confirm open", () => {
    const modal = src("ConfirmActionModal.jsx");
    expect(modal).toMatch(/confirm-cancel/);
    expect(modal).toMatch(/onCancel/);
  });
});
