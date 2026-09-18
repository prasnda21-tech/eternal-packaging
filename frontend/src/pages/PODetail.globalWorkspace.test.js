import fs from "fs";
import path from "path";
import { STAGES } from "../lib/constants";

const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
const shell = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoDetailShell.jsx"), "utf8");
const commandBar = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoGlobalCommandBar.jsx"), "utf8");
const rail = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoContextRail.jsx"), "utf8");
const page = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoContextPage.jsx"), "utf8");
const workspace = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoStageWorkspace.jsx"), "utf8");
const footer = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoStageActionFooter.jsx"), "utf8");
const ui = [detail, shell, commandBar, rail, page, workspace, footer].join("\n");

function countTestId(src, id) {
  const re = new RegExp(`data-testid="${id}"`, "g");
  return (src.match(re) || []).length;
}

describe("Detail PO global one-screen workspace", () => {
  test("planning menu and rail are not gated to Order Masuk", () => {
    expect(detail).not.toMatch(/viewedStage === 0 && \(/);
    expect(detail).toMatch(/<PoGlobalCommandBar/);
    expect(detail).toMatch(/<PoContextRail/);
    expect(detail).toMatch(/<PoDetailShell/);
    expect(commandBar).toMatch(/data-testid="po-plan-actions-menu"/);
    expect(rail).toMatch(/data-testid="po-context-rail"/);
    expect(css).not.toMatch(/\[data-viewed-stage="0"\] \.po-command-bar/);
    expect(shell).not.toMatch(/commandBar/);
  });

  test("all 15 stages stay mounted and only the viewed workspace is shown", () => {
    expect(STAGES).toHaveLength(15);
    for (let i = 0; i < 15; i += 1) {
      expect(detail).toMatch(new RegExp(`data-workflow-stage="${i}"`));
    }
    expect(detail).toMatch(/hidden=\{\!deskPanelOwnsStage/);
    expect(workspace).toMatch(/po-stage-workspace-body/);
    expect(css).toMatch(/po-stage-workspace-scroll/);
    expect(css).toMatch(/po-stage-action-footer/);
  });

  test("global buttons are unique and workflow actions live in stage footers", () => {
    [
      "po-edit-info",
      "po-atur-lokasi",
      "btn-repeat-order-detail",
      "toggle-urgent-button",
      "toggle-buffer-stock-button",
      "btn-print-po",
      "btn-buat-faktur-penjualan",
      "btn-proses-pengiriman-pesanan",
      "copy-tracking-link",
      "approve-design-button",
      "approve-payment-button",
      "upload-payment-input",
    ].forEach((id) => {
      expect(countTestId(ui, id)).toBe(1);
    });
    expect(detail).toMatch(/confirmTestid="screener-save-button"/);
    expect(ui).not.toMatch(/btn-proses-pengiriman-pesanan-focus/);
    expect(detail).toMatch(/PoStageActionFooter testid="approval-design-actions"/);
    expect(detail).toMatch(/PoStageActionFooter testid="approval-payment-actions"/);
    expect(detail).toMatch(/PoStageActionFooter testid="sample-actions"/);
    expect(footer).not.toMatch(/api\./);
    expect(shell).not.toMatch(/api\./);
    expect(commandBar).not.toMatch(/api\./);
  });

  test("context pages stay global and keep drafts mounted", () => {
    expect(detail).toMatch(/testid="detail-info"/);
    expect(detail).not.toMatch(/testid="po-actions-panel"/);
    expect(detail).toMatch(/testid="po-documents-page"/);
    expect(detail).toMatch(/testid="po-workflow-page"/);
    expect(detail).toMatch(/note-input/);
    expect(detail).toMatch(/save-note-button/);
    expect(page).toMatch(/po-context-page-body/);
    expect(page).not.toMatch(/aria-modal/);
    expect(detail).not.toMatch(/PoContextDrawer/);
    expect(detail).not.toMatch(/po-utility-scrim/);
    expect(css).not.toMatch(/po-detail-shell\.is-drawer-open/);
  });

  test("desktop shell locks document scroll; mobile uses stage sheet", () => {
    expect(css).toMatch(/\.app-shell\.po-detail-route/);
    expect(css).toMatch(/overflow:hidden/);
    expect(css).not.toMatch(/html:has\(\.po-detail-shell\)/);
    expect(detail).toMatch(/po-mobile-stage-chrome/);
    expect(detail).toMatch(/PoStagePickerSheet/);
    expect(css).toMatch(/@media \(max-width:800px\)/);
  });
});
