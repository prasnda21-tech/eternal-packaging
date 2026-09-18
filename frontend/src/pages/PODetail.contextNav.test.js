import fs from "fs";
import path from "path";
import {
  parsePoDetailStageParam,
  parsePoDetailViewParam,
  syncPoDetailQuery,
} from "../lib/poDetailStageNav";

const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
const rail = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoContextRail.jsx"), "utf8");
const page = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoContextPage.jsx"), "utf8");
const shell = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoDetailShell.jsx"), "utf8");
const commandBar = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoGlobalCommandBar.jsx"), "utf8");
const workspace = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoStageWorkspace.jsx"), "utf8");
const appShell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
const ui = [detail, commandBar, rail, page, workspace, shell].join("\n");

function countTestId(src, id) {
  return (src.match(new RegExp(`data-testid="${id}"`, "g")) || []).length;
}

describe("Detail PO context navigation pages", () => {
  test("rail navigates to same-PO views instead of drawers", () => {
    expect(rail).toMatch(/data-testid=\{isMenu \? "po-plan-actions" : `po-rail-\$\{railId\}`\}/);
    expect(rail).toMatch(/aria-current=\{isMenu \? undefined : \(isActive \? "page" : undefined\)\}/);
    expect(rail).toMatch(/informasi-po/);
    expect(rail).toMatch(/perencanaan/);
    expect(rail).toMatch(/dokumen-pengecualian/);
    expect(rail).toMatch(/alur-pekerjaan/);
    expect(rail).not.toMatch(/operasional/);
    expect(rail).not.toMatch(/active === id \? null/);
    expect(detail).toMatch(/selectContextView/);
    expect(detail).toMatch(/parsePoDetailViewParam/);
    expect(detail).toMatch(/onBackToWorkflow=\{backToWorkflow\}/);
    expect(workspace).toMatch(/data-testid="btn-back-to-workflow"/);
    expect(detail).not.toMatch(/PoContextDrawer/);
    expect(detail).not.toMatch(/po-utility-scrim/);
    expect(shell).not.toMatch(/drawerOpen/);
    expect(page).not.toMatch(/aria-modal/);
    expect(page).not.toMatch(/role=\{as === "aside" \? "dialog"/);
  });

  test("URL view slugs are deep-linkable and refresh-safe", () => {
    expect(parsePoDetailViewParam("?view=informasi-po")).toBe("informasi-po");
    expect(parsePoDetailViewParam("?view=operasional")).toBe(null);
    expect(parsePoDetailViewParam("?view=operational")).toBe(null);
    expect(parsePoDetailViewParam("?view=dokumen-pengecualian")).toBe("dokumen-pengecualian");
    expect(parsePoDetailViewParam("?view=alur-pekerjaan")).toBe("alur-pekerjaan");
    expect(parsePoDetailViewParam("?view=timeline")).toBe("alur-pekerjaan");
    expect(parsePoDetailViewParam("?view=info")).toBe("informasi-po");
    expect(parsePoDetailViewParam("?view=documents")).toBe("dokumen-pengecualian");
    expect(parsePoDetailViewParam("?stage=order-masuk")).toBe(null);
    expect(parsePoDetailStageParam("?stage=order-masuk&view=informasi-po")).toBe(0);
    expect(parsePoDetailStageParam("?stage=loading&view=operasional")).toBe(13);
    const push = jest.spyOn(window.history, "pushState");
    syncPoDetailQuery({ stageIndex: 0, view: "informasi-po", push: true });
    expect(String(push.mock.calls[0][2])).toMatch(/stage=order-masuk/);
    expect(String(push.mock.calls[0][2])).toMatch(/view=informasi-po/);
    push.mockRestore();
    expect(detail).toMatch(/popstate/);
    expect(detail).toMatch(/setContextView\(parsePoDetailViewParam/);
  });

  test("context pages replace workspace and keep existing handlers unique", () => {
    expect(detail).toMatch(/po-context-page/);
    expect(detail).toMatch(/testid="detail-info"/);
    expect(detail).not.toMatch(/testid="po-actions-panel"/);
    expect(detail).toMatch(/testid="po-documents-page"/);
    expect(detail).toMatch(/testid="po-workflow-page"/);
    expect(detail).toMatch(/hidden=\{\!\!contextView\}/);
    expect(workspace).toMatch(/Kembali ke Workflow/);
    expect(countTestId(ui, "po-edit-info")).toBe(1);
    expect(countTestId(ui, "po-atur-lokasi")).toBe(1);
    expect(page).not.toMatch(/Edit Informasi/);
    expect(page).not.toMatch(/Edit Lokasi/);
  });

  test("line items have no Status column and keep Cetak segmented controls", () => {
    const tableStart = detail.indexOf("line-items-table");
    const table = detail.slice(tableStart, detail.indexOf("</thead>", tableStart));
    expect(table).toMatch(/>No</);
    expect(table).toMatch(/>Item</);
    expect(table).toMatch(/>Kode</);
    expect(table).toMatch(/>Qty</);
    expect(table).toMatch(/>Warna</);
    expect(table).toMatch(/Cetak \/ Non-Cetak/);
    expect(table).toMatch(/>Aksi Item</);
    expect(table).not.toMatch(/>Status</);
    expect(detail).toMatch(/LineRouteEditor/);
    expect(detail).toMatch(/LineProductReplaceControl/);
    expect(detail).toMatch(/LineItemOverflow/);
  });

  test("planning popover groups stay unique and overflow is not a workflow action", () => {
    expect(commandBar).toMatch(/po-plan-group-info/);
    expect(commandBar).toMatch(/po-plan-group-plan/);
    expect(commandBar).toMatch(/po-plan-group-docs/);
    expect(commandBar).toMatch(/Aksi Berbahaya \/ Pengecualian/);
    expect(commandBar).not.toMatch(/Lainnya/);
    expect(commandBar).not.toMatch(/approve-design-button/);
    expect(commandBar).not.toMatch(/approve-payment-button/);
    expect(commandBar).not.toMatch(/screener-save-button/);
    expect(detail).toMatch(/PoStageActionFooter/);
  });

  test("Perencanaan is a portaled compact dropdown from the rail", () => {
    expect(commandBar).toMatch(/createPortal/);
    expect(commandBar).toMatch(/po-plan-actions-menu/);
    expect(commandBar).toMatch(/document\.body/);
    expect(commandBar).not.toMatch(/po-more-actions-menu/);
    expect(commandBar).not.toMatch(/po-command-bar/);
    expect(css).toMatch(/\.po-plan-menu\{[\s\S]*position:fixed/);
    expect(css).toMatch(/\.po-plan-menu\{[\s\S]*overflow:visible/);
    expect(detail).toMatch(/onClosePlan=\{\(\) => setPlanActionsOpen\(false\)\}/);
    expect(detail).not.toMatch(/setMoreActionsOpen/);
    expect(page).not.toMatch(/aria-modal/);
    expect(rail).not.toMatch(/createPortal/);
    expect(rail).toMatch(/data-testid=\{isMenu \? "po-plan-actions"/);
    expect(rail).toMatch(/aria-haspopup=\{isMenu \? "menu"/);
    expect(shell).not.toMatch(/commandBar/);
  });

  test("scroll lock is scoped to the Detail PO route", () => {
    expect(appShell).toMatch(/po-detail-route/);
    expect(css).toMatch(/\.app-shell\.po-detail-route\{/);
    expect(css).toMatch(/\.po-detail-shell\{/);
    expect(css).toMatch(/min-height:0/);
    expect(css).toMatch(/min-width:0/);
    expect(css).not.toMatch(/html:has\(\.po-detail-shell\)/);
    expect(css).not.toMatch(/po-utility-scrim\{display:block/);
    expect(shell).toMatch(/po-detail-shell-main/);
  });

  test("PO history tabs stay in normal flow and HOLD is a one-line banner", () => {
    expect(css).toMatch(/\.app-shell\.po-detail-route \.content > \.kb-workspace-tabs\{[\s\S]*?position:relative/);
    expect(css).toMatch(/\.app-shell\.po-detail-route \.content > \.kb-workspace-tabs\{[\s\S]*?top:auto/);
    expect(detail).toMatch(/po-hero-hold/);
    expect(detail).toMatch(/<strong>PO HOLD<\/strong>/);
    expect(detail).not.toMatch(/<ul>\{(\s*order\.hold_reasons)/);
    expect(css).toMatch(/po-hero-hold\{/);
  });

  test("Perencanaan is a portaled dropdown with unique planning actions", () => {
    expect(commandBar).toMatch(/po-plan-actions-menu/);
    expect(commandBar).toMatch(/Repeat Order/);
    expect(commandBar).toMatch(/Live Tracking/);
    expect(commandBar).toMatch(/Buat Pengiriman Pesanan/);
    expect(commandBar).toMatch(/copy-tracking-link/);
    expect(commandBar).toMatch(/btn-proses-pengiriman-pesanan/);
    expect(countTestId(ui, "copy-tracking-link")).toBe(1);
    expect(countTestId(ui, "btn-proses-pengiriman-pesanan")).toBe(1);
    expect(countTestId(detail, "copy-tracking-link")).toBe(0);
    expect(countTestId(detail, "btn-proses-pengiriman-pesanan")).toBe(0);
    expect(detail).toMatch(/onCopyTracking=\{copyTrackingLink\}/);
    expect(detail).toMatch(/canProcessSalesDelivery=\{canProcessSalesDelivery\}/);
    expect(detail).toMatch(/onClosePlan=\{\(\) => setPlanActionsOpen\(false\)\}/);
  });

  test("PO HOLD is a single header label and Alur Pekerjaan is a workspace page", () => {
    expect((detail.match(/data-testid="hold-banner"/g) || []).length).toBe(1);
    expect(detail).toMatch(/po-hero-customer-row/);
    expect(detail).toMatch(/po-hero-hold/);
    expect(detail).not.toMatch(/className="hold-banner"/);
    expect(rail).toMatch(/Alur<br \/>Pekerjaan|Alur Pekerjaan/);
    expect(rail.indexOf("informasi-po")).toBeLessThan(rail.indexOf("perencanaan"));
    expect(rail.indexOf("perencanaan")).toBeLessThan(rail.indexOf("dokumen-pengecualian"));
    expect(rail.indexOf("dokumen-pengecualian")).toBeLessThan(rail.indexOf("alur-pekerjaan"));
    expect(detail).toMatch(/testid="po-workflow-page"/);
    expect(detail).toMatch(/<Timeline timeline=\{order\.timeline\}/);
    expect((detail.match(/<Timeline /g) || []).length).toBe(1);
    expect(page).not.toMatch(/aria-modal/);
    const docsBlock = detail.slice(detail.indexOf("testid=\"po-documents-page\""), detail.indexOf("testid=\"po-workflow-page\""));
    expect(docsBlock).not.toMatch(/<Timeline /);
  });

  test("notification bell stays docked in the topbar on Detail PO", () => {
    expect(appShell).toMatch(/poDetailActive && <FloatingNotificationBell docked \/>/);
    expect(appShell).toMatch(/!poDetailActive && <FloatingNotificationBell \/>/);
  });
});
