import { act } from "react";
import { createRoot } from "react-dom/client";
import PoStageSection from "./PoStageSection";
import { PoStageAccordionProvider } from "./PoStageAccordionContext";

global.IS_REACT_ACT_ENVIRONMENT = true;

function mount(ui) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(ui);
  });
  return {
    container,
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

const order = {
  current_stage: 10,
  requires_printing: true,
  design_status: "Approved",
  payment_status: "Approved",
};

describe("PoStageSection accordion behavior", () => {
  test("all stage cards start collapsed with aria-expanded false and body hidden", () => {
    const { container, unmount } = mount(
      <PoStageAccordionProvider>
        <PoStageSection stageIndex={1} order={order} title="File Design">
          <input data-testid="design-input" defaultValue="draft-a" />
        </PoStageSection>
        <PoStageSection stageIndex={10} order={order} title="Qty Selesai Cetak">
          <input data-testid="cetak-input" defaultValue="draft-b" />
        </PoStageSection>
        <PoStageSection stageIndex={14} order={order} title="Pengiriman">
          <div data-testid="ship-body">ship body</div>
        </PoStageSection>
      </PoStageAccordionProvider>
    );

    const toggles = container.querySelectorAll("[data-testid^='po-stage-toggle-']");
    expect(toggles.length).toBe(3);
    toggles.forEach((btn) => {
      expect(btn.getAttribute("aria-expanded")).toBe("false");
      expect(btn.tagName).toBe("BUTTON");
    });
    container.querySelectorAll(".po-stage-section-body").forEach((body) => {
      expect(body.hidden).toBe(true);
    });
    // Active stage (10) must also stay collapsed on first paint
    expect(container.querySelector('[data-testid="po-stage-toggle-10"]').getAttribute("aria-expanded")).toBe(
      "false"
    );
    unmount();
  });

  test("click opens a card; click again closes it; Enter/Space toggle", () => {
    const { container, unmount } = mount(
      <PoStageAccordionProvider>
        <PoStageSection stageIndex={2} order={order} title="Approval Design">
          <div data-testid="approval-body">approval</div>
        </PoStageSection>
      </PoStageAccordionProvider>
    );
    const btn = container.querySelector('[data-testid="po-stage-toggle-2"]');
    const body = container.querySelector(".po-stage-section-body");
    act(() => {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    expect(body.hidden).toBe(false);
    expect(container.querySelector(".po-stage-chevron.is-open")).toBeTruthy();

    act(() => {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(body.hidden).toBe(true);

    act(() => {
      btn.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    act(() => {
      btn.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
    });
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    unmount();
  });

  test("opening another card closes the previous; unsaved input is preserved", () => {
    const { container, unmount } = mount(
      <PoStageAccordionProvider>
        <PoStageSection stageIndex={1} order={order} title="File Design">
          <input data-testid="design-input" defaultValue="keep-me" />
        </PoStageSection>
        <PoStageSection stageIndex={11} order={order} title="Packing">
          <input data-testid="packing-input" defaultValue="pack-draft" />
        </PoStageSection>
      </PoStageAccordionProvider>
    );
    const a = container.querySelector('[data-testid="po-stage-toggle-1"]');
    const d = container.querySelector('[data-testid="po-stage-toggle-11"]');
    act(() => {
      a.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const designInput = container.querySelector('[data-testid="design-input"]');
    act(() => {
      designInput.value = "unsaved-design";
      designInput.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(a.getAttribute("aria-expanded")).toBe("true");

    act(() => {
      d.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(a.getAttribute("aria-expanded")).toBe("false");
    expect(d.getAttribute("aria-expanded")).toBe("true");
    // Still mounted with prior value
    expect(container.querySelector('[data-testid="design-input"]').value).toBe("unsaved-design");
    expect(container.querySelector('[data-testid="po-stage-section-1"] .po-stage-section-body').hidden).toBe(
      true
    );
    unmount();
  });
});
