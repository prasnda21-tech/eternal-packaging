import fs from "fs";
import path from "path";
import { parsePoDetailViewParam, parsePoDetailStageParam } from "../lib/poDetailStageNav";

const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
const rail = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoContextRail.jsx"), "utf8");
const menu = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoGlobalCommandBar.jsx"), "utf8");
const shell = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoDetailShell.jsx"), "utf8");
const page = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoContextPage.jsx"), "utf8");
const ui = [detail, menu, rail, shell, page].join("\n");

function countTestId(src, id) {
  return (src.match(new RegExp(`data-testid="${id}"`, "g")) || []).length;
}

describe("Detail PO compact Perencanaan menu", () => {
  test("Operasional is gone from the rail and Perencanaan sits under Informasi PO", () => {
    expect(rail).not.toMatch(/operasional/);
    expect(detail).not.toMatch(/view="operasional"/);
    expect(detail).not.toMatch(/testid="po-actions-panel"/);
    expect(rail.indexOf("informasi-po")).toBeLessThan(rail.indexOf("perencanaan"));
    expect(rail.indexOf("perencanaan")).toBeLessThan(rail.indexOf("dokumen-pengecualian"));
    expect(rail.indexOf("dokumen-pengecualian")).toBeLessThan(rail.indexOf("alur-pekerjaan"));
    expect(rail).toMatch(/kind: "menu"/);
    expect(rail).toMatch(/data-testid=\{isMenu \? "po-plan-actions"/);
  });

  test("Perencanaan opens a popover, not a context page", () => {
    expect(rail).toMatch(/aria-haspopup=\{isMenu \? "menu"/);
    expect(rail).toMatch(/onTogglePlan/);
    expect(menu).toMatch(/po-plan-actions-menu/);
    expect(menu).toMatch(/createPortal/);
    expect(menu).toMatch(/document\.body/);
    expect(detail).not.toMatch(/setContextView\(["']perencanaan["']\)/);
    expect(detail).not.toMatch(/view="perencanaan"/);
    expect(page).not.toMatch(/perencanaan/);
  });

  test("horizontal command bar and Lainnya are not rendered", () => {
    expect(shell).not.toMatch(/commandBar/);
    expect(menu).not.toMatch(/po-command-bar/);
    expect(detail).not.toMatch(/po-command-bar/);
    expect(ui).not.toMatch(/Lainnya/);
    expect(ui).not.toMatch(/po-more-actions-menu/);
    expect(detail).not.toMatch(/setMoreActionsOpen/);
    expect(countTestId(ui, "po-refresh-button")).toBe(0);
    expect(countTestId(ui, "btn-po-summary")).toBe(0);
    expect(countTestId(ui, "back-button")).toBe(0);
  });

  test("each moved action appears once inside Perencanaan", () => {
    [
      "po-edit-info",
      "po-atur-lokasi",
      "btn-print-po",
      "btn-buat-faktur-penjualan",
      "btn-repeat-order-detail",
      "toggle-urgent-button",
      "toggle-buffer-stock-button",
      "copy-tracking-link",
      "btn-proses-pengiriman-pesanan",
      "cancel-po-button",
      "delete-po-button",
    ].forEach((id) => {
      expect(countTestId(ui, id)).toBe(1);
      expect(countTestId(menu, id)).toBe(1);
      expect(countTestId(detail, id)).toBe(0);
    });
  });

  test("dangerous actions keep original RBAC and confirm-before-mutation", () => {
    expect(detail).toMatch(/hasPerm\("po\.cancel"\)/);
    expect(detail).toMatch(/hasPerm\("po\.delete"\)/);
    expect(detail).toMatch(/hasPerm\("workflow\.rollback"\)/);
    expect(detail).toMatch(/window\.prompt\(`Batalkan PO/);
    expect(detail).toMatch(/window\.confirm\(`Anda akan membatalkan PO/);
    expect(detail).toMatch(/setShowDeleteModal\(true\)/);
    expect(detail).toMatch(/delete-po-modal/);
    expect(menu).toMatch(/closeThen\(onCancelPo\)/);
    expect(menu).toMatch(/closeThen\(onDeletePo\)/);
    expect(menu).toMatch(/Menyimpan histori dan audit\./);
    expect(menu).toMatch(/Khusus PO test\/duplicate\./);
    expect(menu).toMatch(/Aksi Berbahaya \/ Pengecualian/);
    expect(menu).toMatch(/canCancel && !cancelled/);
    expect(menu).toMatch(/canDelete &&/);
    expect(menu).not.toMatch(/api\./);
    expect(menu).not.toMatch(/cancelPO\(/);
    expect(menu).not.toMatch(/deleteImpact\(/);
  });

  test("legacy view=operasional falls back to the last workflow workspace", () => {
    expect(parsePoDetailViewParam("?view=operasional")).toBe(null);
    expect(parsePoDetailViewParam("?view=operational")).toBe(null);
    expect(parsePoDetailStageParam("?stage=packing&view=operasional")).toBe(11);
    expect(detail).toMatch(/hasRetiredPoDetailViewParam\(window\.location\.search\)/);
    expect(detail).toMatch(/syncPoDetailQuery\(\{ stageIndex: selectedWorkflowStage, view: contextView, push: false \}\)/);
    expect(detail).not.toMatch(/doCancelPO\(\)/);
    expect(detail).not.toMatch(/openDeleteModal\(\)/);
  });

  test("dropdown closes on Escape, outside click, and before opening a modal", () => {
    expect(menu).toMatch(/event\.key === "Escape"/);
    expect(menu).toMatch(/mousedown/);
    expect(menu).toMatch(/closeThen/);
    expect(menu).toMatch(/triggerRef\?\.current\?\.focus/);
    expect(detail).toMatch(/setPlanActionsOpen\(false\)/);
    expect(detail).toMatch(/\[id, selectedWorkflowStage, contextView\]/);
    expect(detail).toMatch(/onTogglePlan=\{\(\) => setPlanActionsOpen/);
    expect(detail).not.toMatch(/setContextView\(.*onTogglePlan/);
  });

  test("opening the dropdown does not mutate or change viewed stage", () => {
    const toggle = detail.slice(detail.indexOf("onTogglePlan="), detail.indexOf("onTogglePlan=") + 80);
    expect(toggle).toMatch(/setPlanActionsOpen/);
    expect(toggle).not.toMatch(/api\./);
    expect(toggle).not.toMatch(/setSelectedWorkflowStage/);
    expect(toggle).not.toMatch(/setContextView/);
    expect(menu).not.toMatch(/api\./);
    expect(detail).toMatch(/canEditPoInfo=\{canEditPoInfo\}/);
    expect(detail).toMatch(/canSetLocation=\{canSetLocation\}/);
    expect(detail).toMatch(/canManageUrgent=\{canManageUrgent\}/);
    expect(detail).toMatch(/canManageBufferStock=\{canManageBufferStock\}/);
    expect(detail).toMatch(/canCopyTrackingLink=\{canCopyTrackingLink\}/);
    expect(detail).toMatch(/canProcessSalesDelivery=\{canProcessSalesDelivery\}/);
    expect(detail).toMatch(/canCancel=\{canCancel\}/);
    expect(detail).toMatch(/canDelete=\{canDelete\}/);
  });

  test("Informasi PO, Dokumen, and Alur Pekerjaan still open workspace pages", () => {
    expect(detail).toMatch(/testid="detail-info"/);
    expect(detail).toMatch(/testid="po-documents-page"/);
    expect(detail).toMatch(/testid="po-workflow-page"/);
    expect(rail).toMatch(/po-rail-\$\{railId\}/);
    expect(css).toMatch(/\.app-shell\.po-detail-route\{/);
    expect(css).toMatch(/overflow:hidden/);
    expect(css).toMatch(/\.po-plan-menu\{[\s\S]*overflow:visible/);
    expect(css).toMatch(/min-width:380px/);
    expect(css).toMatch(/max-width:440px/);
  });
});
