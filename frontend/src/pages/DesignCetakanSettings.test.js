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

  test("branch selector uses catalog names and empty state", () => {
    expect(pageSrc).toMatch(/Terapkan Untuk/);
    expect(pageSrc).toMatch(/Default Semua Cabang/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-scope"/);
    expect(pageSrc).toMatch(/designCetakanBranches/);
    expect(pageSrc).toMatch(/row\.name \|\| `Cabang \$\{row\.branch_id\}`/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-branch-empty"/);
    expect(pageSrc).not.toMatch(/Yogyakarta|Semarang/);
  });

  test("branch mode tracks sparse overrides separately from the global form", () => {
    expect(pageSrc).toMatch(/Mengikuti Global/);
    expect(pageSrc).toMatch(/Khusus Cabang/);
    expect(pageSrc).toMatch(/Atur khusus cabang/);
    expect(pageSrc).toMatch(/Ikuti Global/);
    expect(pageSrc).toMatch(/Gunakan Semua Pengaturan Global/);
    expect(pageSrc).toMatch(/Ganti khusus cabang/);
    expect(pageSrc).toMatch(/function buildBranchOverridePayload/);
    const fn = pageSrc.slice(
      pageSrc.indexOf("function buildBranchOverridePayload"),
      pageSrc.indexOf("function restoreFieldFromGlobal"),
    );
    expect(fn).toMatch(/on\.company_name/);
    expect(fn).toMatch(/on\.bank_accounts/);
    expect(fn).not.toMatch(/accent_color|estimasi_|signature_labels|surat_jalan|company_logo/);
    expect(pageSrc).toMatch(/updateDesignCetakanBranch\(scope, payload\)/);
    expect(pageSrc).toMatch(/resetDesignCetakanBranch/);
    expect(pageSrc).toMatch(/window\.confirm/);
    expect(pageSrc).toMatch(/flagsFromInherited/);
    expect(pageSrc).toMatch(/design-cetakan-global-only-accent/);
    expect(pageSrc).toMatch(/design-cetakan-global-only-po/);
    expect(pageSrc).toMatch(/design-cetakan-global-only-sj/);
    expect(pageSrc).toMatch(/design-cetakan-global-only-estimasi/);
    expect(pageSrc).toMatch(/fieldLocked\("bank_accounts"\)/);
    expect(pageSrc).toMatch(/overrideOn\.company_logo/);
    expect(pageSrc).toMatch(/overrideOn\.prepared_signature_image/);
    expect(pageSrc).toMatch(/overrideOn\.approved_signature_image/);
    expect(pageSrc).toMatch(/disabled=\{!canEdit \|\| busy\}/);
    expect(pageSrc).toMatch(/useEffect\(\(\) => \{\s*load\(scope\)/s);
    expect(pageSrc).toMatch(/if \(target === "global"\)/);
    expect(apiSrc).toMatch(/designCetakanBranch:/);
    expect(apiSrc).toMatch(/updateDesignCetakanBranch:/);
    expect(apiSrc).toMatch(/resetDesignCetakanBranch:/);
    expect(apiSrc).toMatch(/uploadDesignCetakanBranchAsset:/);
    expect(apiSrc).toMatch(/designCetakanBranchAssetPreview:/);
    expect(apiSrc).toMatch(/removeDesignCetakanBranchAsset:/);
    expect(apiSrc).toMatch(/\/settings\/design-cetakan\/branches\/\$\{encodeURIComponent\(branchId\)\}/);
  });

  test("preview actions reuse saved config and window.open", () => {
    expect(pageSrc).toMatch(/Preview PO/);
    expect(pageSrc).toMatch(/Preview Faktur/);
    expect(pageSrc).toMatch(/Preview Proforma/);
    expect(pageSrc).toMatch(/Preview Surat Jalan/);
    expect(pageSrc).toMatch(/Preview mengikuti pengaturan yang sudah disimpan\./);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-preview-po"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-preview-invoice"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-preview-proforma"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-preview-delivery"/);
    expect(pageSrc).toMatch(/previewDesignCetakan\(documentType, branchMode \? scope : undefined\)/);
    expect(pageSrc).toMatch(/window\.open\(url, ["']_blank["']\)/);
    expect(pageSrc).toMatch(/URL\.createObjectURL\(blob\)/);
    expect(pageSrc).toMatch(/URL\.revokeObjectURL\(url\)/);
    expect(pageSrc).toMatch(/previewBusy/);
    expect(pageSrc).toMatch(/disabled=\{previewLocked\}/);
    expect(pageSrc).toMatch(/Popup diblokir/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-error"/);
    const previewFn = pageSrc.slice(
      pageSrc.indexOf("const openPreview"),
      pageSrc.indexOf('data-testid="design-cetakan-settings"'),
    );
    expect(previewFn).toMatch(/previewDesignCetakan/);
    expect(previewFn).not.toMatch(/updateDesignCetakanSettings|updateDesignCetakanBranch/);
    expect(previewFn).not.toMatch(/uploadDesignCetakan|resetDesignCetakanBranch/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-preview-po"[^>]*disabled=\{previewLocked\}/);
    expect(apiSrc).toMatch(/previewDesignCetakan:/);
    expect(apiSrc).toMatch(/\/settings\/design-cetakan\/preview\/\$\{encodeURIComponent\(documentType\)\}/);
    expect(apiSrc).toMatch(/if \(branchId\) params\.branch_id = branchId/);
    expect(apiSrc).toMatch(/responseType: ["']blob["']/);
  });
});
