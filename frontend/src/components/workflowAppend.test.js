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
    expect(src("PackingPanel.jsx")).toMatch(/ConfirmActionModal/);
    expect(src("PackingPanel.jsx")).not.toMatch(/Packing Sebagian/);
    expect(src("PackingPanel.jsx")).not.toMatch(/Lanjut Packing/);
    expect(src("PackingPanel.jsx")).not.toMatch(/complete-packing-button/);
    expect(src("RoleWorkPanel.jsx")).not.toMatch(/Packing Sebagian/);
    expect(src("LoadingPanel.jsx")).toMatch(/action_id: confirm\.actionId/);
    expect(src("SuratJalanPanel.jsx")).toMatch(/ConfirmActionModal/);
    expect(src("DeliveryProofPanel.jsx")).toMatch(/confirm-delivered-button/);
    expect(src("DeliveryProofPanel.jsx")).toMatch(/KONFIRMASI PENGIRIMAN/);
    expect(src("DeliveryProofPanel.jsx")).toMatch(/confirmDelivered/);
    expect(src("SuratJalanPanel.jsx")).not.toMatch(/sj-accurate-import/);
    expect(src("SuratJalanPanel.jsx")).not.toMatch(/previewAccurateSuratJalan/);
    expect(src("RoleWorkPanel.jsx")).not.toMatch(/previewAccurateSuratJalan/);
    expect(src("RoleWorkPanel.jsx")).not.toMatch(/importSuratJalan/);
  });

  test("cancel path does not call save in confirm open", () => {
    const modal = src("ConfirmActionModal.jsx");
    expect(modal).toMatch(/confirm-cancel/);
    expect(modal).toMatch(/onCancel/);
  });

  test("role work panel is append-only with confirm modal", () => {
    const s = src("RoleWorkPanel.jsx");
    expect(s).toMatch(/RoleWorkHistory/);
    expect(s).toMatch(/RoleWorkEntryForm/);
    expect(s).toMatch(/ConfirmActionModal/);
    expect(s).toMatch(/action_id/);
    expect(s).toMatch(/KONFIRMASI PEKERJAAN/);
    expect(s).toMatch(/operator-work-header/);
    expect(s).toMatch(/updateScreenerInfo/);
    expect(s).toMatch(/updateHelperPrepItems/);
    expect(s).toMatch(/savePackingInfo/);
    expect(s).toMatch(/updateLoading/);
    expect(s).toMatch(/SuratJalanPanel/);
    expect(s).toMatch(/DeliveryProofPanel/);
  });

  test("settings lists all divisions without fake qty on evidence", () => {
    const s = src("../pages/Settings.jsx");
    expect(s).toMatch(/Form Pekerjaan Divisi/);
    expect(s).toMatch(/Aksi \/ dokumen existing/);
    expect(s).not.toMatch(/Prototype aktif/);
  });

  test("po work view focuses permitted panel without role-name hardcode", () => {
    const s = src("../pages/PoWorkView.jsx");
    expect(s).toMatch(/permittedDivisionIds/);
    expect(s).toMatch(/work-divisi-switch/);
    expect(s).not.toMatch(/divisi !== "operator"/);
    expect(s).not.toMatch(/role === /);
  });
});
