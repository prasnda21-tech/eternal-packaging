import { act } from "react";
import { createRoot } from "react-dom/client";
import { STAGES } from "./constants";
import StageBar from "../components/StageBar";
import {
  PO_DETAIL_VISUAL_STEPS,
  accordionIndexForFocus,
  parsePoDetailStageParam,
  poDetailStagePanelTitle,
  shouldShowPoActiveStagePanel,
  visualCanonicalIndex,
  visualStepForInternalIndex,
  visualStepPresentationIndex,
  isVisualStepActive,
  isVisualStepCompleted,
} from "./poDetailStageNav";

global.IS_REACT_ACT_ENVIRONMENT = true;

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

function orderAt(currentStage) {
  return {
    requires_printing: true,
    current_stage: currentStage,
    hold: false,
    cancelled: false,
    design_status: currentStage > 2 ? "Approved" : "Waiting",
    payment_status: currentStage > 4 ? "Approved" : "Waiting",
    design_files: currentStage >= 1 ? [{}] : [],
    payment_files: currentStage >= 3 ? [{}] : [],
  };
}

describe("Detail PO visual workflow grouping", () => {
  test("internal STAGES stay 15; visual stepper is 13 explicit numbers", () => {
    expect(STAGES).toHaveLength(15);
    expect(STAGES[1]).toBe("Design");
    expect(STAGES[2]).toBe("Approval Design");
    expect(STAGES[3]).toBe("Payment");
    expect(STAGES[4]).toBe("Approval Payment");
    expect(STAGES[5]).toBe("Penjadwalan");
    expect(STAGES[14]).toBe("Pengiriman");
    expect(PO_DETAIL_VISUAL_STEPS.map((s) => s.displayNumber)).toEqual([
      "1", "2&3", "4&5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15",
    ]);
    expect(PO_DETAIL_VISUAL_STEPS.map((s) => s.label)).toEqual([
      "Order Masuk",
      "Design",
      "Payment",
      "Penjadwalan",
      "Screener",
      "Persiapan Helper",
      "Operator",
      "Konfirmasi Sample",
      "Cetak",
      "Packing",
      "Surat Jalan",
      "Loading",
      "Pengiriman",
    ]);
  });

  test("CASE A/B: internal 2 or 3 maps to visual Design 2&3 and is active", () => {
    expect(visualStepForInternalIndex(1).displayNumber).toBe("2&3");
    expect(visualStepForInternalIndex(2).displayNumber).toBe("2&3");
    expect(visualCanonicalIndex(1)).toBe(1);
    expect(visualCanonicalIndex(2)).toBe(1);
    expect(isVisualStepActive(visualStepForInternalIndex(1), 1)).toBe(true);
    expect(isVisualStepActive(visualStepForInternalIndex(2), 2)).toBe(true);
    expect(isVisualStepCompleted(visualStepForInternalIndex(1), 1)).toBe(false);
    expect(isVisualStepCompleted(visualStepForInternalIndex(2), 2)).toBe(false);
  });

  test("CASE C/D: internal 4 or 5 maps to visual Payment 4&5 and is active", () => {
    expect(visualStepForInternalIndex(3).displayNumber).toBe("4&5");
    expect(visualStepForInternalIndex(4).displayNumber).toBe("4&5");
    expect(visualCanonicalIndex(3)).toBe(3);
    expect(visualCanonicalIndex(4)).toBe(3);
    expect(isVisualStepActive(visualStepForInternalIndex(3), 3)).toBe(true);
    expect(isVisualStepActive(visualStepForInternalIndex(4), 4)).toBe(true);
  });

  test("CASE E/F: Penjadwalan stays 6 and Pengiriman stays 15", () => {
    expect(visualStepForInternalIndex(5).displayNumber).toBe("6");
    expect(visualStepForInternalIndex(5).label).toBe("Penjadwalan");
    expect(visualStepForInternalIndex(14).displayNumber).toBe("15");
    expect(visualStepForInternalIndex(14).label).toBe("Pengiriman");
    expect(poDetailStagePanelTitle(5)).toBe("Workspace 6 — Penjadwalan");
    expect(poDetailStagePanelTitle(14)).toBe("Workspace 15 — Pengiriman");
  });

  test("CASE I: Order Masuk stays visual 1", () => {
    expect(visualStepForInternalIndex(0).displayNumber).toBe("1");
    expect(visualStepForInternalIndex(0).label).toBe("Order Masuk");
    expect(poDetailStagePanelTitle(0)).toBe("Workspace 1 — Order Masuk");
  });

  test("CASE J: active panel visible only on Order Masuk", () => {
    expect(shouldShowPoActiveStagePanel(0)).toBe(true);
    for (let i = 1; i <= 14; i += 1) {
      expect(shouldShowPoActiveStagePanel(i)).toBe(false);
    }
  });

  test("grouped titles and query compatibility keep internal indexes", () => {
    expect(poDetailStagePanelTitle(1)).toBe("DESIGN — 2&3");
    expect(poDetailStagePanelTitle(2)).toBe("DESIGN — 2&3");
    expect(poDetailStagePanelTitle(3)).toBe("PAYMENT — 4&5");
    expect(poDetailStagePanelTitle(4)).toBe("PAYMENT — 4&5");
    expect(parsePoDetailStageParam("?stage=approval-design")).toBe(2);
    expect(parsePoDetailStageParam("?stage=approval-payment")).toBe(4);
    expect(parsePoDetailStageParam("?stage=design")).toBe(1);
    expect(parsePoDetailStageParam("?stage=payment")).toBe(3);
    expect(accordionIndexForFocus(2)).toBe(1);
    expect(accordionIndexForFocus(4)).toBe(3);
  });

  test("completed mapping uses visual pass of last grouped internal stage", () => {
    const design = visualStepForInternalIndex(1);
    const payment = visualStepForInternalIndex(3);
    expect(isVisualStepCompleted(design, 3)).toBe(true);
    expect(isVisualStepCompleted(payment, 5)).toBe(true);
    expect(isVisualStepCompleted(design, 2)).toBe(false);
    expect(isVisualStepCompleted(payment, 4)).toBe(false);
    expect(visualStepPresentationIndex(design, 1)).toBe(1);
    expect(visualStepPresentationIndex(design, 2)).toBe(2);
    expect(visualStepPresentationIndex(design, 5)).toBe(2);
    expect(visualStepPresentationIndex(payment, 3)).toBe(3);
    expect(visualStepPresentationIndex(payment, 4)).toBe(4);
    expect(visualStepPresentationIndex(payment, 6)).toBe(4);
  });

  test("CASE G/H: StageBar visual nodes hide Approval Design and Approval Payment", () => {
    const onStageClick = jest.fn();
    const { container, unmount } = mount(
      <StageBar
        stage={2}
        order={orderAt(2)}
        showLabels
        viewedStage={2}
        steps={PO_DETAIL_VISUAL_STEPS}
        onStageClick={onStageClick}
      />
    );
    const nums = [...container.querySelectorAll(".stage-bar-step-num")].map((n) => n.textContent);
    const labels = [...container.querySelectorAll(".stage-bar-label")].map((n) => n.textContent);
    expect(nums).toEqual(["1", "2&3", "4&5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"]);
    expect(labels).not.toContain("Approval Design");
    expect(labels).not.toContain("Approval Payment");
    expect(container.querySelector('[data-testid="stage-node-2"]')).toBeNull();
    expect(container.querySelector('[data-testid="stage-node-4"]')).toBeNull();
    expect(container.querySelector('[data-testid="stage-node-1"]').className).toMatch(/is-actual/);
    expect(container.querySelector('[data-testid="order-stage-progress"]').getAttribute("data-visual-step-count")).toBe("13");
    act(() => {
      container.querySelector('[data-testid="stage-node-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onStageClick).toHaveBeenCalledWith(1);
    unmount();
  });

  test("CASE A–D StageBar active mapping for Design and Payment groups", () => {
    [
      { current: 1, actualTestId: "stage-node-1" },
      { current: 2, actualTestId: "stage-node-1" },
      { current: 3, actualTestId: "stage-node-3" },
      { current: 4, actualTestId: "stage-node-3" },
    ].forEach(({ current, actualTestId }) => {
      const { container, unmount } = mount(
        <StageBar
          stage={current}
          order={orderAt(current)}
          showLabels
          viewedStage={current}
          steps={PO_DETAIL_VISUAL_STEPS}
        />
      );
      const actual = container.querySelector("[data-stage-actual='true'] [data-testid^='stage-node-']")
        || container.querySelector(`${actualTestId.startsWith("[") ? actualTestId : `[data-testid="${actualTestId}"]`}`);
      expect(container.querySelector(`[data-testid="${actualTestId}"]`).className).toMatch(/is-actual/);
      expect(actual).toBeTruthy();
      unmount();
    });
  });

  test("CASE E/F StageBar keeps Penjadwalan 6 and Pengiriman 15", () => {
    const { container, unmount } = mount(
      <StageBar
        stage={5}
        order={orderAt(5)}
        showLabels
        viewedStage={14}
        steps={PO_DETAIL_VISUAL_STEPS}
      />
    );
    expect(container.querySelector('[data-testid="stage-node-5"] .stage-bar-step-num').textContent).toBe("6");
    expect(container.querySelector('[data-testid="stage-node-5"] .stage-bar-label').textContent).toBe("Penjadwalan");
    expect(container.querySelector('[data-testid="stage-node-5"]').className).toMatch(/is-actual/);
    expect(container.querySelector('[data-testid="stage-node-14"] .stage-bar-step-num').textContent).toBe("15");
    expect(container.querySelector('[data-testid="stage-node-14"] .stage-bar-label').textContent).toBe("Pengiriman");
    expect(container.querySelector('[data-testid="stage-node-14"]').className).toMatch(/is-viewed/);
    unmount();
  });

  test("default StageBar without steps still exposes 15 internal nodes", () => {
    const { container, unmount } = mount(
      <StageBar stage={12} order={orderAt(12)} showLabels viewedStage={0} />
    );
    expect(container.querySelector('[data-testid="stage-node-2"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stage-node-4"]')).toBeTruthy();
    expect(container.querySelectorAll("[data-testid^='stage-node-']").length).toBe(15);
    unmount();
  });
});
