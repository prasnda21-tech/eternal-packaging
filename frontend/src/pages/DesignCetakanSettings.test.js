import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import api from "../lib/api";
import DesignCetakanSettings from "./DesignCetakanSettings";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {},
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

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

  test("Phase 1 invoice header editor uses whitelist sources only", () => {
    expect(pageSrc).toMatch(/data-testid="design-cetakan-header-section"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-header-add"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-header-list"/);
    expect(pageSrc).toMatch(/HEADER_SOURCE_OPTIONS/);
    expect(pageSrc).toMatch(/company\.logo/);
    expect(pageSrc).toMatch(/company\.name/);
    expect(pageSrc).toMatch(/customer\.name/);
    expect(pageSrc).toMatch(/document\.number/);
    expect(pageSrc).toMatch(/sales\.name/);
    expect(pageSrc).toMatch(/static\.text/);
    expect(pageSrc).toMatch(/header_elements/);
    expect(pageSrc).toMatch(/design-cetakan-global-only-header/);
    expect(pageSrc).toMatch(/optgroup/);
    expect(pageSrc).not.toMatch(/hpp\.|margin\.|arbitrary/);
    expect(pageSrc).toMatch(/normalizeHeaderElements/);
    expect(pageSrc).toMatch(/moveHeaderElement/);
    expect(pageSrc).toMatch(/Tambah Elemen/);
  });

  test("Phase 2 invoice footer editor uses whitelist sources and section tabs", () => {
    expect(pageSrc).toMatch(/data-testid="design-cetakan-invoice-section-header"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-invoice-section-footer"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-footer-section"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-footer-add"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-footer-list"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-footer-load-default"/);
    expect(pageSrc).toMatch(/FOOTER_SOURCE_OPTIONS/);
    expect(pageSrc).toMatch(/signature\.prepared/);
    expect(pageSrc).toMatch(/signature\.approved/);
    expect(pageSrc).toMatch(/estimasi\.block/);
    expect(pageSrc).toMatch(/footer_elements/);
    expect(pageSrc).toMatch(/design-cetakan-global-only-footer/);
    expect(pageSrc).toMatch(/normalizeFooterElements/);
    expect(pageSrc).toMatch(/moveFooterElement/);
    expect(pageSrc).toMatch(/Isi Default Footer/);
    expect(pageSrc).toMatch(/Footer Mengikuti Global/);
    expect(pageSrc).not.toMatch(/free-form|mm coordinates|arbitrary\.css/i);
  });

  test("Phase 3 PO and SJ editors reuse controlled element model", () => {
    expect(pageSrc).toMatch(/PO_HEADER_SOURCE_OPTIONS/);
    expect(pageSrc).toMatch(/PO_FOOTER_SOURCE_OPTIONS/);
    expect(pageSrc).toMatch(/SJ_HEADER_SOURCE_OPTIONS/);
    expect(pageSrc).toMatch(/SJ_FOOTER_SOURCE_OPTIONS/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-po-section-header"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-po-section-footer"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-sj-section-header"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-sj-section-footer"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-po-header-list"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-po-footer-list"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-sj-header-list"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-sj-footer-list"/);
    expect(pageSrc).toMatch(/document\.delivery_date/);
    expect(pageSrc).toMatch(/document\.payment_terms/);
    expect(pageSrc).toMatch(/document\.expedition/);
    expect(pageSrc).toMatch(/user\.created_by/);
    expect(pageSrc).toMatch(/po\.signatures_block/);
    expect(pageSrc).toMatch(/normalizePoHeaderElements/);
    expect(pageSrc).toMatch(/normalizeSjFooterElements/);
    expect(pageSrc).toMatch(/loadDefaultPoHeaderElements/);
    expect(pageSrc).toMatch(/loadDefaultSjFooterElements/);
    expect(pageSrc).toMatch(/Kosongkan \(pakai Legacy\)/);
    const poHeaderBlock = pageSrc.slice(
      pageSrc.indexOf("const PO_HEADER_SOURCE_OPTIONS"),
      pageSrc.indexOf("const PO_FOOTER_SOURCE_OPTIONS"),
    );
    const sjFooterBlock = pageSrc.slice(
      pageSrc.indexOf("const SJ_FOOTER_SOURCE_OPTIONS"),
      pageSrc.indexOf("const PO_HEADER_SOURCE_FLAT"),
    );
    expect(poHeaderBlock).not.toMatch(/document\.due_date|signature\.prepared|estimasi\.block/);
    expect(sjFooterBlock).not.toMatch(/signature\.prepared|po\.signatures_block|document\.due_date/);
    expect(pageSrc).not.toMatch(/pod\.|driver\.evidence|hpp\.|margin\./i);
  });

  test("Phase 3 legacy settings collapsed with section status badges", () => {
    expect(pageSrc).toMatch(/Pengaturan Legacy/);
    expect(pageSrc).toMatch(/Digunakan sebagai fallback jika bagian Header\/Footer baru belum dikonfigurasi/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-invoice-legacy-toggle"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-po-legacy-toggle"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-sj-legacy-toggle"/);
    expect(pageSrc).toMatch(/legacyOpen, setLegacyOpen/);
    expect(pageSrc).toMatch(/invoice: false, po: false, sj: false/);
    expect(pageSrc).toMatch(/Generic Aktif/);
    expect(pageSrc).toMatch(/Legacy Fallback/);
    expect(pageSrc).toMatch(/sectionModeLabel/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-invoice-header-status"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-invoice-footer-status"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-po-header-status"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-po-footer-status"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-sj-header-status"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-sj-footer-status"/);
    expect(pageSrc).toMatch(/Header Mengikuti Global/);
    expect(pageSrc).toMatch(/Footer Mengikuti Global/);
    expect(pageSrc).toMatch(/po\.header_elements/);
    expect(pageSrc).toMatch(/surat_jalan\.footer_elements/);
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
    expect(pageSrc).toMatch(/data-testid="design-cetakan-preview-open"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-preview-menu"/);
    expect(pageSrc).toMatch(/role="menu"/);
    expect(pageSrc).toMatch(/openPreview\("po"\)/);
    expect(pageSrc).toMatch(/openPreview\("invoice"\)/);
    expect(pageSrc).toMatch(/openPreview\("proforma"\)/);
    expect(pageSrc).toMatch(/openPreview\("delivery"\)/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-command"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-savebar"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-save-state"/);
    expect(pageSrc).toMatch(/Perubahan belum disimpan/);
    expect(pageSrc).toMatch(/"Tersimpan"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-preview-po"[^>]*disabled=\{previewLocked\}/);
    expect(apiSrc).toMatch(/previewDesignCetakan:/);
    expect(apiSrc).toMatch(/\/settings\/design-cetakan\/preview\/\$\{encodeURIComponent\(documentType\)\}/);
    expect(apiSrc).toMatch(/if \(branchId\) params\.branch_id = branchId/);
    expect(apiSrc).toMatch(/responseType: ["']blob["']/);
  });

  test("history modal is compact read-only reuse of catalog", () => {
    expect(pageSrc).toMatch(/Riwayat Perubahan/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-history-open"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-history-modal"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-history-close"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-history-loading"/);
    expect(pageSrc).toMatch(/Belum ada riwayat perubahan Design Cetakan\./);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-history-error"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-history-row"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-history-actor"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-history-operation"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-history-changes"/);
    expect(pageSrc).toMatch(/row\.changed_fields\.map/);
    expect(pageSrc).toMatch(/field\.label \|\| field\.key/);
    expect(pageSrc).toMatch(/<option value="all">Semua<\/option>/);
    expect(pageSrc).toMatch(/<option value="global">Default Semua Cabang<\/option>/);
    expect(pageSrc).toMatch(/<option value="branch">Cabang<\/option>/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-history-branch"/);
    expect(pageSrc).toMatch(/historyScope === "branch"/);
    expect(pageSrc).toMatch(/getDesignCetakanHistory\(params\)/);
    expect(pageSrc).toMatch(/setHistoryItems\(\[\]\)/);
    expect(pageSrc).toMatch(/\[historyOpen, historyScope, historyBranchId\]/);
    expect(pageSrc).toMatch(/page: next/);
    expect(pageSrc).toMatch(/setHistoryItems\(\(prev\) => \[\.\.\.prev/);
    expect(pageSrc).toMatch(/if \(historyBusy \|\| !historyHasMore\) return/);
    expect(pageSrc).toMatch(/Muat lebih banyak/);
    expect(pageSrc).toMatch(/historyHasMore \?/);
    expect(pageSrc).toMatch(/disabled=\{historyBusy\}/);
    expect(pageSrc).toMatch(/row\.branch_name \|\| ["']Global["']/);
    expect(pageSrc).toMatch(/row\.actor_name \|\| ["']-["']/);
    expect(pageSrc).toMatch(/row\.operation_label \|\| row\.summary/);
    expect(pageSrc).not.toMatch(/JSON\.stringify\(row/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-history-open"/);
    expect(pageSrc).not.toMatch(/design-cetakan-history-open"[^>]*disabled=\{!canEdit/);
    const historyFn = pageSrc.slice(pageSrc.indexOf("const openHistory"), pageSrc.indexOf("const loadMoreHistory"));
    expect(historyFn).not.toMatch(/updateDesignCetakanSettings|updateDesignCetakanBranch|uploadDesignCetakan|resetDesignCetakanBranch/);
    expect(historyFn).not.toMatch(/\bload\(|setDraft|setOverrideOn|setScope\(/);
    const moreFn = pageSrc.slice(pageSrc.indexOf("const loadMoreHistory"), pageSrc.indexOf("const openCopy"));
    expect(moreFn).not.toMatch(/updateDesignCetakanSettings|uploadDesignCetakan|resetDesignCetakanBranch/);
    expect(moreFn).not.toMatch(/removeDesignCetakan|previewDesignCetakan/);
    const closeIdx = pageSrc.indexOf('data-testid="design-cetakan-history-close"');
    const closeFn = pageSrc.slice(closeIdx, closeIdx + 120);
    expect(closeFn).toMatch(/setHistoryOpen\(false\)/);
    expect(closeFn).not.toMatch(/load\(|setDraft|setOverrideOn|setScope\(/);
    const historyEffect = pageSrc.slice(pageSrc.indexOf("if (!historyOpen) return undefined"), pageSrc.indexOf("const openHistory"));
    expect(historyEffect).toMatch(/getDesignCetakanHistory/);
    expect(historyEffect).not.toMatch(/updateDesignCetakanSettings|updateDesignCetakanBranch|uploadDesignCetakan|resetDesignCetakanBranch|removeDesignCetakan/);
    expect(historyEffect).not.toMatch(/\bload\(|setDraft|setOverrideOn|setScope\(/);
    expect(pageSrc).toMatch(/setHistoryOpen\(false\)/);
    expect(pageSrc).toMatch(/previewDesignCetakan/);
    expect(pageSrc).toMatch(/function buildBranchOverridePayload/);
    expect(pageSrc).not.toMatch(/cloneDesignCetakan/);
    expect(apiSrc).toMatch(/getDesignCetakanHistory:/);
    expect(apiSrc).toMatch(/\/settings\/design-cetakan\/history/);
    expect(apiSrc).toMatch(/copyDesignCetakan:/);
  });

  test("copy modal copies sparse overrides without mutating save/preview", () => {
    expect(pageSrc).toMatch(/Salin Pengaturan/);
    expect(pageSrc).toMatch(/Salin Pengaturan Khusus/);
    expect(pageSrc).not.toMatch(/Clone Desain/);
    expect(pageSrc).toMatch(/\{canEdit \? \(/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-copy-open"/);
    expect(pageSrc).not.toMatch(/design-cetakan-copy-open"[^>]*disabled=\{!canEdit/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-copy-modal"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-copy-source"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-copy-target"/);
    expect(pageSrc).toMatch(/<option value="global">Default Semua Cabang<\/option>/);
    const copyModal = pageSrc.slice(pageSrc.indexOf('data-testid="design-cetakan-copy-modal"'));
    const targetBlock = copyModal.slice(copyModal.indexOf('data-testid="design-cetakan-copy-target"'), copyModal.indexOf("Pilih cabang") + 80);
    expect(targetBlock).toMatch(/Pilih cabang/);
    expect(targetBlock).not.toMatch(/value=["']global["']/);
    expect(pageSrc).toMatch(/copySource !== "global" && String\(copySource\) === String\(copyTarget\)/);
    expect(pageSrc).toMatch(/Salin menggunakan pengaturan yang sudah disimpan\./);
    expect(pageSrc).toMatch(/Perubahan yang belum disimpan akan dibuang\. Lanjutkan\?/);
    expect(pageSrc).toMatch(/Perubahan yang belum disimpan juga akan dibuang\./);
    expect(pageSrc).toMatch(/Nilai yang disalin menjadi pengaturan khusus cabang dan tidak lagi mengikuti perubahan Global\./);
    expect(pageSrc).toMatch(/Hanya pengaturan khusus/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-copy-summary"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-copy-fields"/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-copy-replaced"/);
    expect(pageSrc).toMatch(/Akan diganti di tujuan/);
    expect(pageSrc).toMatch(/data-testid="design-cetakan-copy-dropped"/);
    expect(pageSrc).toMatch(/Akan berhenti di-override \/ kembali ke Global/);
    expect(pageSrc).toMatch(/Cabang ini belum memiliki pengaturan khusus/);
    expect(pageSrc).toMatch(/Salin ke Cabang/);
    expect(pageSrc).toMatch(/disabled=\{copyBlocked\}/);
    const submitFn = pageSrc.slice(pageSrc.indexOf("const submitCopy"), pageSrc.indexOf("const updatedByLabel"));
    expect(submitFn).toMatch(/source_scope: copySource === "global" \? "global" : "branch"/);
    expect(submitFn).toMatch(/target_branch_id: copyTarget/);
    expect(submitFn).toMatch(/payload\.source_branch_id = copySource/);
    expect(submitFn).toMatch(/copyDesignCetakan\(payload\)/);
    expect(submitFn).not.toMatch(/mode:|settings:|override:/);
    expect(submitFn).not.toMatch(/updateDesignCetakanSettings|updateDesignCetakanBranch/);
    expect(submitFn).not.toMatch(/uploadDesignCetakan|removeDesignCetakan|resetDesignCetakanBranch/);
    expect(submitFn).toMatch(/if \(scope === copyTarget\)/);
    expect(submitFn).toMatch(/applyBranchView\(res\?\.branch/);
    expect(submitFn).toMatch(/setCopyErr\(errDetail\(ex\)\)/);
    expect(submitFn).not.toMatch(/\bload\(|setDraft|setOverrideOn|setScope\(/);
    expect(pageSrc).toMatch(/row\.operation_label \|\| row\.summary/);
    expect(pageSrc).toMatch(/previewDesignCetakan\(documentType, branchMode \? scope : undefined\)/);
    expect(pageSrc).toMatch(/function buildBranchOverridePayload/);
    expect(apiSrc).toMatch(/copyDesignCetakan:/);
    expect(apiSrc).toMatch(/\/settings\/design-cetakan\/copy/);
    expect(apiSrc).not.toMatch(/cloneDesignCetakan/);
  });
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const settingsCalls = [];
const branchCalls = [];

function branchPayload(name) {
  return {
    effective: { company_name: name },
    inherited: { company_name: { overridden: true, inherited: false } },
    override: { company_name: name },
  };
}

function installApi() {
  settingsCalls.length = 0;
  branchCalls.length = 0;
  Object.assign(api, {
    designCetakanBranches: jest.fn(() => Promise.resolve({
      branches: [
        { branch_id: "50", name: "Cabang 50" },
        { branch_id: "77", name: "Cabang 77" },
      ],
    })),
    designCetakanSettings: jest.fn(() => {
      const pending = deferred();
      settingsCalls.push(pending);
      return pending.promise;
    }),
    designCetakanBranch: jest.fn((id) => {
      const pending = deferred();
      branchCalls.push({ id: String(id), pending });
      return pending.promise;
    }),
    updateDesignCetakanSettings: jest.fn(async (payload) => ({
      message: "Design Cetakan berhasil disimpan",
      settings: { company_name: payload.company_name },
    })),
    resetDesignCetakanBranch: jest.fn(async () => ({
      message: "Cabang kembali mengikuti pengaturan global",
      branch: branchPayload("GLOBAL"),
    })),
    getDesignCetakanHistory: jest.fn(async () => ({ items: [], page: 1, has_more: false })),
    previewDesignCetakan: jest.fn(async () => new Blob(["pdf"])),
    designCetakanBranchAssetPreview: jest.fn(),
    designCetakanAssetPreview: jest.fn(),
  });
}

function setNativeValue(el, value) {
  const proto = el.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

async function mountPage(canEdit = true) {
  installApi();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<DesignCetakanSettings notify={jest.fn()} canEdit={canEdit} />);
  });
  return { container, root };
}

async function settleGlobal(container, name = "GLOBAL") {
  await act(async () => {
    settingsCalls[0].resolve({ settings: { company_name: name } });
  });
  const input = container.querySelector('[data-testid="design-cetakan-company-name"]');
  expect(input.value).toBe(name);
  return input;
}

async function chooseScope(container, value) {
  const select = container.querySelector('[data-testid="design-cetakan-scope"]');
  await act(async () => {
    setNativeValue(select, value);
  });
  return select;
}

async function typeName(input, value) {
  await act(async () => {
    setNativeValue(input, value);
  });
}

describe("Design Cetakan hardening behavior", () => {
  let root;
  let container;

  afterEach(() => {
    if (root) {
      act(() => {
        root.unmount();
      });
    }
    if (container) container.remove();
    root = undefined;
    container = undefined;
    window.confirm = undefined;
  });

  test("late branch 50 response cannot replace branch 77", async () => {
    ({ container, root } = await mountPage());
    await settleGlobal(container);
    await chooseScope(container, "50");
    expect(settingsCalls).toHaveLength(2);
    await chooseScope(container, "77");
    expect(settingsCalls).toHaveLength(3);
    await act(async () => {
      settingsCalls[2].resolve({ settings: { company_name: "GLOBAL" } });
    });
    expect(branchCalls.map((row) => row.id)).toEqual(["77"]);
    await act(async () => {
      branchCalls[0].pending.resolve(branchPayload("CABANG 77"));
    });
    const input = container.querySelector('[data-testid="design-cetakan-company-name"]');
    const select = container.querySelector('[data-testid="design-cetakan-scope"]');
    expect(input.value).toBe("CABANG 77");
    expect(select.value).toBe("77");
    await act(async () => {
      settingsCalls[1].resolve({ settings: { company_name: "SHOULD NOT APPLY" } });
    });
    expect(branchCalls.map((row) => row.id)).toEqual(["77"]);
    expect(input.value).toBe("CABANG 77");
    expect(select.value).toBe("77");
    expect(container.querySelector('[data-testid="design-cetakan-error"]')).toBeNull();
  });

  test("stale branch 50 failure does not replace branch 77 state or clear its loading", async () => {
    ({ container, root } = await mountPage());
    await settleGlobal(container);
    await chooseScope(container, "50");
    await chooseScope(container, "77");
    const save = container.querySelector('[data-testid="design-cetakan-save"]');
    expect(save.textContent).toMatch(/Menyimpan/);
    await act(async () => {
      settingsCalls[1].reject({ response: { data: { detail: "cabang 50 gagal" } } });
    });
    expect(save.textContent).toMatch(/Menyimpan/);
    expect(container.querySelector('[data-testid="design-cetakan-error"]')).toBeNull();
    await act(async () => {
      settingsCalls[2].resolve({ settings: { company_name: "GLOBAL" } });
    });
    await act(async () => {
      branchCalls[0].pending.resolve(branchPayload("CABANG 77"));
    });
    expect(container.querySelector('[data-testid="design-cetakan-company-name"]').value).toBe("CABANG 77");
    expect(container.querySelector('[data-testid="design-cetakan-scope"]').value).toBe("77");
    expect(save.textContent).toMatch(/Simpan/);
    expect(save.textContent).not.toMatch(/Menyimpan/);
    expect(container.querySelector('[data-testid="design-cetakan-error"]')).toBeNull();
  });

  test("dirty scope switch confirms before changing selector", async () => {
    ({ container, root } = await mountPage());
    const input = await settleGlobal(container);
    window.confirm = jest.fn(() => false);
    await typeName(input, "DRAFT 50");
    expect(container.querySelector('[data-testid="design-cetakan-save-state"]').textContent).toMatch(/Perubahan belum disimpan/);
    await chooseScope(container, "50");
    expect(window.confirm).toHaveBeenCalledWith("Perubahan yang belum disimpan akan dibuang. Lanjutkan?");
    expect(container.querySelector('[data-testid="design-cetakan-scope"]').value).toBe("global");
    expect(input.value).toBe("DRAFT 50");
    expect(settingsCalls).toHaveLength(1);

    window.confirm.mockReturnValue(true);
    await chooseScope(container, "50");
    expect(settingsCalls).toHaveLength(2);
    await act(async () => {
      settingsCalls[1].resolve({ settings: { company_name: "GLOBAL" } });
    });
    await act(async () => {
      branchCalls[0].pending.resolve(branchPayload("CABANG 50"));
    });
    expect(container.querySelector('[data-testid="design-cetakan-scope"]').value).toBe("50");
    expect(input.value).toBe("CABANG 50");

    window.confirm.mockReturnValue(false);
    await typeName(input, "DRAFT GLOBAL");
    await chooseScope(container, "global");
    expect(window.confirm).toHaveBeenCalledWith("Perubahan yang belum disimpan akan dibuang. Lanjutkan?");
    expect(container.querySelector('[data-testid="design-cetakan-scope"]').value).toBe("50");
    expect(input.value).toBe("DRAFT GLOBAL");

    window.confirm.mockReturnValue(true);
    await chooseScope(container, "global");
    await act(async () => {
      settingsCalls[settingsCalls.length - 1].resolve({ settings: { company_name: "GLOBAL CLEAN" } });
    });
    expect(container.querySelector('[data-testid="design-cetakan-scope"]').value).toBe("global");
    expect(input.value).toBe("GLOBAL CLEAN");
  });

  test("successful save clears dirty so the next scope change does not confirm", async () => {
    ({ container, root } = await mountPage());
    const input = await settleGlobal(container);
    await typeName(input, "SAVED NAME");
    await act(async () => {
      container.querySelector('[data-testid="design-cetakan-save"]').click();
    });
    expect(input.value).toBe("SAVED NAME");
    window.confirm = jest.fn(() => false);
    const before = settingsCalls.length;
    await chooseScope(container, "77");
    expect(window.confirm).not.toHaveBeenCalled();
    expect(settingsCalls).toHaveLength(before + 1);
    expect(container.querySelector('[data-testid="design-cetakan-scope"]').value).toBe("77");
  });

  test("copy warns that saved settings are used and history cannot stack with copy", async () => {
    ({ container, root } = await mountPage());
    await settleGlobal(container);
    await act(async () => {
      container.querySelector('[data-testid="design-cetakan-copy-open"]').click();
    });
    expect(container.querySelector('[data-testid="design-cetakan-copy-modal"]').textContent).toMatch(
      /Salin menggunakan pengaturan yang sudah disimpan\./
    );
    await act(async () => {
      container.querySelector('[data-testid="design-cetakan-history-open"]').click();
    });
    expect(container.querySelector('[data-testid="design-cetakan-copy-modal"]')).toBeNull();
    expect(container.querySelector('[data-testid="design-cetakan-history-modal"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="design-cetakan-copy-open"]').click();
    });
    expect(container.querySelector('[data-testid="design-cetakan-history-modal"]')).toBeNull();
    expect(container.querySelector('[data-testid="design-cetakan-copy-modal"]')).toBeTruthy();
  });

  test("preview menu calls the existing document preview and view-only hides mutations", async () => {
    const createObjectURL = jest.fn(() => "blob:preview");
    const revokeObjectURL = jest.fn();
    const open = jest.fn(() => ({ closed: false }));
    const prevCreate = URL.createObjectURL;
    const prevRevoke = URL.revokeObjectURL;
    const prevOpen = window.open;
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    window.open = open;
    ({ container, root } = await mountPage());
    await settleGlobal(container);
    expect(container.querySelector('[data-testid="design-cetakan-save-state"]').textContent).toMatch(/Tersimpan/);
    expect(container.querySelector('[data-testid="design-cetakan-savebar"]')).toBeTruthy();
    expect(container.querySelectorAll('[data-testid="design-cetakan-preview-open"]')).toHaveLength(1);
    await act(async () => {
      container.querySelector('[data-testid="design-cetakan-preview-open"]').click();
    });
    const menu = container.querySelector('[data-testid="design-cetakan-preview-menu"]');
    expect(menu.querySelector('[data-testid="design-cetakan-preview-po"]')).toBeTruthy();
    expect(menu.querySelector('[data-testid="design-cetakan-preview-invoice"]')).toBeTruthy();
    expect(menu.querySelector('[data-testid="design-cetakan-preview-proforma"]')).toBeTruthy();
    expect(menu.querySelector('[data-testid="design-cetakan-preview-delivery"]')).toBeTruthy();
    await act(async () => {
      menu.querySelector('[data-testid="design-cetakan-preview-po"]').click();
    });
    expect(api.previewDesignCetakan).toHaveBeenCalledWith("po", undefined);
    URL.createObjectURL = prevCreate;
    URL.revokeObjectURL = prevRevoke;
    window.open = prevOpen;

    act(() => root.unmount());
    container.remove();
    ({ container, root } = await mountPage(false));
    await settleGlobal(container);
    expect(container.querySelector('[data-testid="design-cetakan-copy-open"]')).toBeNull();
    expect(container.querySelector('[data-testid="design-cetakan-history-open"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="design-cetakan-save"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="design-cetakan-logo-upload"]').disabled).toBe(true);
  });

  test("branch override enable keeps global-only fields locked", async () => {
    ({ container, root } = await mountPage());
    await settleGlobal(container);
    await chooseScope(container, "50");
    await act(async () => {
      settingsCalls[1].resolve({ settings: { company_name: "GLOBAL" } });
    });
    await act(async () => {
      branchCalls[0].pending.resolve({
        effective: { company_name: "DARI GLOBAL" },
        inherited: { company_name: { overridden: false, inherited: true } },
        override: {},
      });
    });
    const input = container.querySelector('[data-testid="design-cetakan-company-name"]');
    const accent = container.querySelector('[data-testid="design-cetakan-accent"]');
    expect(input.disabled).toBe(true);
    expect(accent.disabled).toBe(true);
    expect(container.querySelector('[data-testid="design-cetakan-global-only-accent"]').textContent).toMatch(/Mengikuti Global/);
    await act(async () => {
      container.querySelector('[data-testid="design-cetakan-override-company_name"]').click();
    });
    expect(input.disabled).toBe(false);
    expect(accent.disabled).toBe(true);
    expect(container.querySelector('[data-testid="design-cetakan-follow-company_name"]').textContent).toMatch(/Ikuti Global/);
  });

  test("dirty branch reset confirms unsaved discard and then clears dirty", async () => {
    ({ container, root } = await mountPage());
    const input = await settleGlobal(container);
    await chooseScope(container, "50");
    await act(async () => {
      settingsCalls[1].resolve({ settings: { company_name: "GLOBAL" } });
    });
    await act(async () => {
      branchCalls[0].pending.resolve(branchPayload("CABANG 50"));
    });
    expect(input.value).toBe("CABANG 50");
    await typeName(input, "DRAFT RESET");
    window.confirm = jest.fn(() => true);
    await act(async () => {
      container.querySelector('[data-testid="design-cetakan-reset-branch"]').click();
    });
    expect(window.confirm).toHaveBeenCalledWith(
      "Pengaturan khusus cabang akan dihapus dan cabang kembali mengikuti Global. Perubahan yang belum disimpan juga akan dibuang."
    );
    expect(input.value).toBe("GLOBAL");
    window.confirm.mockClear();
    window.confirm.mockReturnValue(false);
    await chooseScope(container, "global");
    expect(window.confirm).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="design-cetakan-scope"]').value).toBe("global");
  });
});
