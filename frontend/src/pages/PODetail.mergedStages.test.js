import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import StageBar from "../components/StageBar";
import { getGroupedStagePresentation, getStagePresentation } from "../lib/poDetailPresentation";
import { PO_DETAIL_STEPPER_OMIT } from "../lib/poDetailStageNav";

global.IS_REACT_ACT_ENVIRONMENT = true;

const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
const attachments = fs.readFileSync(path.join(__dirname, "../components/AttachmentsPanel.jsx"), "utf8");
const customerApproval = fs.readFileSync(path.join(__dirname, "../components/CustomerApprovalPanel.jsx"), "utf8");

const EXISTING_DESIGN_ACTIONS = [
  "upload-design-button",
  "approve-design-button",
  "revoke-design-button",
  "bypass-design-button",
  "ca-generate-button",
];

const EXISTING_PAYMENT_ACTIONS = [
  "upload-payment-label",
  "upload-payment-input",
  "approve-payment-button",
  "revoke-payment-button",
  "bypass-payment-button",
];

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

describe("Detail PO merged Design and Payment stages", () => {
  test("reuses existing Design and Payment actions without adding new ones", () => {
    expect(attachments).toMatch(/data-testid="upload-design-button"/);
    expect(attachments.split('data-testid="upload-design-button"').length - 1).toBe(1);
    expect(detail.split('data-testid="approve-design-button"').length - 1).toBe(1);
    expect(detail.split('data-testid="revoke-design-button"').length - 1).toBe(1);
    expect(detail.split('data-testid="bypass-design-button"').length - 1).toBe(1);
    expect(detail).toMatch(/<CustomerApprovalPanel/);
    expect(customerApproval).toMatch(/data-testid="ca-generate-button"/);
    EXISTING_PAYMENT_ACTIONS.forEach((id) => {
      expect(detail.split(`data-testid="${id}"`).length - 1).toBe(1);
    });
    expect(detail).not.toMatch(/Save Design/);
    expect(detail).not.toMatch(/Submit Design/);
    expect(detail).not.toMatch(/btn-next-design/);
    expect(detail).not.toMatch(/btn-submit-payment/);
    expect(EXISTING_DESIGN_ACTIONS.length).toBeGreaterThan(0);
  });

  test("stepper omits Approval Design and Approval Payment as separate steps", () => {
    expect(detail).toMatch(/omitStageIndexes=\{PO_DETAIL_STEPPER_OMIT\}/);
    expect(detail).toMatch(/groupedPresentation/);
    expect(PO_DETAIL_STEPPER_OMIT).toEqual([2, 4]);
    const order = {
      requires_printing: true,
      current_stage: 2,
      design_files: [{ name: "a.png", uploaded_at: "2026-09-01T00:00:00Z", url: "https://x/a.png" }],
      design_status: "Waiting",
    };
    const { container, unmount } = mount(
      <StageBar
        stage={2}
        order={order}
        showLabels
        viewedStage={1}
        onStageClick={() => {}}
        omitStageIndexes={PO_DETAIL_STEPPER_OMIT}
        groupedPresentation
      />
    );
    expect(container.querySelector('[data-testid="stage-node-1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stage-node-2"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="stage-node-3"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stage-node-4"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="stage-bar-col-1"] .stage-bar-label').textContent).toBe("Design");
    expect(container.textContent).not.toMatch(/Approval Design/);
    expect(container.textContent).not.toMatch(/Approval Payment/);
    unmount();
  });

  test("grouped Design presentation uses existing approval chip when waiting", () => {
    const order = {
      current_stage: 2,
      design_files: [{ name: "a.png", uploaded_at: "2026-09-01T00:00:00Z", url: "https://x/a.png" }],
      design_status: "Waiting",
    };
    const designOnly = getStagePresentation(1, order, 2);
    const grouped = getGroupedStagePresentation(1, order, 2);
    expect(designOnly.label).toBe("Selesai");
    expect(grouped.label).toBe("Menunggu approval");
    expect(grouped.tone).toBe("active");
  });

  test("grouped Payment presentation uses existing approval chip when waiting", () => {
    const order = {
      current_stage: 4,
      payment_files: [{ name: "b.pdf" }],
      payment_status: "Waiting",
    };
    const payOnly = getStagePresentation(3, order, 4);
    const grouped = getGroupedStagePresentation(3, order, 4);
    expect(payOnly.label).toBe("Selesai");
    expect(grouped.label).toBe("Menunggu approval");
  });
});
