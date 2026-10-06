import fs from "fs";
import path from "path";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const pageSrc = fs.readFileSync(path.join(__dirname, "DesignCetakanSettings.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
const navSrc = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");

describe("Design Cetakan settings wiring", () => {
  test("Settings hub exposes Design Cetakan card and panel", () => {
    expect(settingsSrc).toMatch(/id:\s*"design-cetakan"/);
    expect(settingsSrc).toMatch(/settings-tab-design-cetakan/);
    expect(settingsSrc).toMatch(/canViewDesignCetakan/);
    expect(settingsSrc).toMatch(/canEditDesignCetakan/);
    expect(settingsSrc).toMatch(/tab === "design-cetakan" && canViewDesignCetakan/);
    expect(settingsSrc).toMatch(/DesignCetakanSettings/);
    expect(settingsSrc).toMatch(/settings\.design_cetakan\.view/);
    expect(settingsSrc).toMatch(/settings\.design_cetakan\.manage/);
  });

  test("page keeps text fields and Phase 3 asset slots", () => {
    expect(pageSrc).toMatch(/data-testid="design-cetakan-settings"/);
    expect(pageSrc).toMatch(/design-cetakan-tab-umum/);
    expect(pageSrc).toMatch(/design-cetakan-tab-po/);
    expect(pageSrc).toMatch(/design-cetakan-tab-invoice/);
    expect(pageSrc).toMatch(/design-cetakan-tab-sj/);
    expect(pageSrc).toMatch(/design-cetakan-save/);
    expect(pageSrc).toMatch(/design-cetakan-company-name/);
    expect(pageSrc).toMatch(/design-cetakan-prepared-label/);
    expect(pageSrc).toMatch(/design-cetakan-approved-label/);

    expect(pageSrc).toMatch(/testIdPrefix="design-cetakan-logo"/);
    expect(pageSrc).toMatch(/testIdPrefix="design-cetakan-prepared-sig"/);
    expect(pageSrc).toMatch(/testIdPrefix="design-cetakan-approved-sig"/);
    expect(pageSrc).toMatch(/\$\{testIdPrefix\}-slot/);
    expect(pageSrc).toMatch(/\$\{testIdPrefix\}-upload/);
    expect(pageSrc).toMatch(/\$\{testIdPrefix\}-remove/);
    expect(pageSrc).toMatch(/Ganti Logo/);
    expect(pageSrc).toMatch(/Gunakan Logo Default/);
    expect(pageSrc).toMatch(/type=["']file["']/);
    expect(pageSrc).toMatch(/URL\.revokeObjectURL/);
    expect(pageSrc).toMatch(/disabled=\{!canEdit \|\| busy\}/);
    expect(pageSrc).not.toMatch(/onDrop|dragover|draggable/);
  });

  test("api exposes asset upload preview remove helpers", () => {
    expect(apiSrc).toMatch(/designCetakanSettings:\s*\(\)\s*=>\s*client\.get\(`\/settings\/design-cetakan`/);
    expect(apiSrc).toMatch(/updateDesignCetakanSettings:/);
    expect(apiSrc).toMatch(/uploadDesignCetakanAsset:/);
    expect(apiSrc).toMatch(/designCetakanAssetPreview:/);
    expect(apiSrc).toMatch(/removeDesignCetakanAsset:/);
    expect(apiSrc).toMatch(/\/settings\/design-cetakan\/assets\/\$\{encodeURIComponent\(slot\)\}/);
    expect(apiSrc).toMatch(/new FormData\(\)/);
    expect(navSrc).toMatch(/"design-cetakan"/);
    expect(navSrc).toMatch(/settings\.design_cetakan\.view/);
    expect(navSrc).toMatch(/"Design Cetakan"/);
  });

  test("PO and SJ static signature uploads are not present", () => {
    expect(pageSrc).not.toMatch(/po_signature|sj_signature|gudang_signature|kurir_static/);
    expect(pageSrc).toMatch(/Tanda tangan PO dan POD Surat Jalan tidak diubah/);
  });
});
