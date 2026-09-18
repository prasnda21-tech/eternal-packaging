import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { STAGES } from "../lib/constants";
import {
  accordionIndexForFocus,
  parsePoDetailStageParam,
  poDetailStagePanelTitle,
} from "../lib/poDetailStageNav";
import StageBar from "../components/StageBar";
import PoSummaryDrawer from "../components/poDetail/PoSummaryDrawer";

global.IS_REACT_ACT_ENVIRONMENT = true;

const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
const nav = fs.readFileSync(path.join(__dirname, "../lib/poDetailStageNav.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
const bar = fs.readFileSync(path.join(__dirname, "../components/StageBar.jsx"), "utf8");
const shell = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoDetailShell.jsx"), "utf8");
const commandBar = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoGlobalCommandBar.jsx"), "utf8");
const workspace = fs.readFileSync(path.join(__dirname, "../components/poDetail/PoStageWorkspace.jsx"), "utf8");
const ui = detail + "\n" + commandBar + "\n" + workspace + "\n" + shell;

function mount(ui) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => root.render(ui));
  return {
    container,
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

const printActive = {
  requires_printing: true,
  current_stage: 12,
  hold: false,
  cancelled: false,
  design_status: "Approved",
  payment_status: "Approved",
  design_files: [{}],
  payment_files: [{}],
};

describe("Detail PO stage-focused workspace", () => {
  test("default panel is the actual stage; viewed and actual stay distinct", () => {
    expect(detail).toMatch(/clampWorkflowStageIndex\(resolveOperationalStage\(order, order\.current_stage\)/);
    expect(ui).toMatch(/data-actual-stage=\{actualStage\}/);
    expect(ui).toMatch(/data-viewed-stage=\{viewedStage\}/);
    expect(ui).toMatch(/po-actual-stage-label/);
    expect(ui).toMatch(/po-viewed-stage-title/);
    expect(ui).toMatch(/btn-back-to-active-stage/);
    expect(parsePoDetailStageParam("?stage=packing")).toBe(11);
    expect(poDetailStagePanelTitle(5)).toBe("Workspace 6 — Penjadwalan");
  });

  test("each canonical stage has a hidden-not-unmounted workspace slot", () => {
    for (let i = 0; i < 15; i += 1) {
      expect(detail).toMatch(new RegExp(`data-workflow-stage="${i}"`));
    }
    expect(detail).toMatch(/hidden=\{\!deskPanelOwnsStage/);
    expect(detail).toMatch(/PoStageAccordionProvider openStageIndex=\{accordionOpenIndex\}/);
    expect(css).not.toMatch(/po-stage-ws-stack \[data-workflow-stage\]\{[^}]*display:none/);
    expect(accordionIndexForFocus(4)).toBe(3);
  });

  test("Order Masuk workspace is line items; context pages stay global across stages", () => {
    const slot0 = detail.slice(detail.indexOf("workflow-slot-0"), detail.indexOf("workflow-slot-1"));
    expect(slot0).toMatch(/line-items-panel/);
    expect(slot0).not.toMatch(/detail-info/);
    expect(detail).toMatch(/PoContextPage/);
    expect(detail).not.toMatch(/PoContextDrawer/);
    expect(detail).toMatch(/PoGlobalCommandBar/);
    expect(detail).toMatch(/PoContextRail/);
    expect(detail).toMatch(/hidden=\{\!deskPanelOwnsStage\("0"/);
    expect(detail).toMatch(/hidden=\{\!deskPanelOwnsStage\("2"/);
    expect(detail).toMatch(/hidden=\{\!deskPanelOwnsStage\("5"/);
    expect(detail).toMatch(/hidden=\{\!deskPanelOwnsStage\("11"/);
    expect(detail).toMatch(/hidden=\{\!deskPanelOwnsStage\("12"/);
    expect(detail).toMatch(/hidden=\{\!deskPanelOwnsStage\("13"/);
    expect(detail).toMatch(/hidden=\{\!deskPanelOwnsStage\("14"/);
    expect(detail).toMatch(/hidden=\{viewedStage !== 3\}/);
    expect(detail).toMatch(/hidden=\{viewedStage !== 4\}/);
    expect(detail).toMatch(/hidden=\{viewedStage !== 8\}/);
    expect(detail).toMatch(/hidden=\{viewedStage !== 9\}/);
  });

  test("stage click is display-only and does not mutate workflow or call APIs", () => {
    const selectBody = detail.slice(detail.indexOf("const selectWorkflowStage"));
    expect(selectBody.slice(0, 500)).toMatch(/setSelectedWorkflowStage/);
    expect(selectBody.slice(0, 500)).toMatch(/syncPoDetailStageQuery/);
    expect(selectBody.slice(0, 500)).not.toMatch(/api\./);
    expect(selectBody.slice(0, 500)).not.toMatch(/setOrder/);
    expect(nav).not.toMatch(/api\./);
  });

  test("deep-link and refresh preserve viewed stage", () => {
    expect(detail).toMatch(/parsePoDetailStageParam/);
    expect(detail).toMatch(/if \(!switched && prev != null\) return prev/);
    expect(detail).toMatch(/popstate/);
    expect(detail).toMatch(/syncPoDetailQuery\(\{ stageIndex: selectedWorkflowStage, view: contextView, push: false \}\)/);
    expect(nav).toMatch(/searchParams\.set\("stage"/);
  });

  test("Ringkasan PO drawer does not change the viewed stage", () => {
    expect(ui).not.toMatch(/btn-po-summary/);
    expect(detail).toMatch(/PoSummaryDrawer/);
    const drawer = fs.readFileSync(
      path.join(__dirname, "../components/poDetail/PoSummaryDrawer.jsx"),
      "utf8"
    );
    expect(drawer).not.toMatch(/api\./);
    expect(drawer).not.toMatch(/setSelectedWorkflowStage/);
    const order = {
      customer: "ES TEH",
      po_number: "PO-1",
      total_quantity: 10,
      item_count: 2,
      current_stage: 0,
    };
    const { container, unmount } = mount(
      <PoSummaryDrawer open order={order} actualStage={0} viewedStage={0} onClose={() => {}} />
    );
    expect(container.querySelector('[data-testid="po-summary-drawer"]')).toBeTruthy();
    unmount();
    const closed = mount(
      <PoSummaryDrawer open={false} order={order} actualStage={0} viewedStage={0} onClose={() => {}} />
    );
    expect(closed.container.querySelector('[data-testid="po-summary-drawer-root"]').hidden).toBe(true);
    closed.unmount();
  });

  test("mobile chrome and 15-stage sheet exist without forcing a tiny 15-up row", () => {
    expect(detail).toMatch(/po-mobile-stage-chrome/);
    expect(detail).toMatch(/Tahap \{viewedStage \+ 1\} dari 15/);
    expect(detail).toMatch(/btn-open-stage-sheet/);
    expect(detail).toMatch(/PoStagePickerSheet/);
    expect(css).toMatch(/po-mobile-stage-chrome\{display:none\}/);
    expect(css).toMatch(/@media \(max-width:640px\)/);
    expect(css).toMatch(/overflow-x:hidden/);
    expect(css).toMatch(/po-stage-ws-stack \[hidden\]\{display:none!important\}/);
    expect(css).toMatch(/po-hero-title-row\{[\s\S]*flex-direction:column/);
  });

  test("permissions and mutation buttons remain wired", () => {
    [
      "approve-design-button",
      "approve-payment-button",
      "rollback-button",
      "cancel-po-button",
      "delete-po-button",
      "upload-payment-input",
      "hold-banner",
      "crm-readonly-note",
    ].forEach((id) => {
      expect(ui).toMatch(new RegExp(`data-testid="${id}"`));
    });
    expect(detail).toMatch(/hasPerm\("design\.approve"\)/);
    expect(detail).toMatch(/hasPerm\("payment\.approve"\)/);
    expect(detail).toMatch(/hasPerm\("packing\.update"\)/);
    expect(detail).toMatch(/disabled=\{busy\}/);
  });

  test("StageBar click only invokes the display callback", () => {
    const onStageClick = jest.fn();
    const { container, unmount } = mount(
      <StageBar stage={12} order={printActive} showLabels viewedStage={0} onStageClick={onStageClick} />
    );
    const actual = container.querySelector('[data-testid="stage-node-12"]');
    const viewed = container.querySelector('[data-testid="stage-node-0"]');
    expect(actual.className).toMatch(/is-actual/);
    expect(viewed.className).toMatch(/is-viewed/);
    act(() => {
      viewed.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onStageClick).toHaveBeenCalledWith(0);
    unmount();
  });

  test("empty optional fields do not crash the drawer", () => {
    const { unmount } = mount(
      <PoSummaryDrawer open order={{}} actualStage={0} viewedStage={0} onClose={() => {}} />
    );
    unmount();
  });

  test("15-stage order is unchanged", () => {
    expect(STAGES).toHaveLength(15);
    expect(STAGES[11]).toBe("Packing");
    expect(bar).toMatch(/viewedStage/);
  });
});
