import { act } from "react";
import { createRoot } from "react-dom/client";
import {
  UrgentRequestSubmitModal,
  UrgentRequestReviewModal,
  UrgentRequestResultModal,
  UrgentPriorityBadge,
  UrgentPoStatusBanner,
  UrgentHistoryDetail,
} from "./UrgentPoApprovalUi";
import { buildPreviewRequest, previewScenarioSeed, applyPreviewDecision } from "../../lib/urgentPoUi";

global.IS_REACT_ACT_ENVIRONMENT = true;

function setTextareaValue(el, value) {
  const proto = HTMLTextAreaElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function setInputValue(el, value) {
  const proto = HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

async function typeContinuous(el, text) {
  let acc = "";
  for (const ch of text) {
    acc += ch;
    // eslint-disable-next-line no-await-in-loop
    await act(async () => {
      el.focus();
      setTextareaValue(el, acc);
    });
    expect(document.activeElement).toBe(el);
    expect(el.value).toBe(acc);
  }
}

describe("UrgentPoApprovalUi Phase A polish", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("priority badge stays NORMAL while pending", async () => {
    const pending = previewScenarioSeed("pending");
    await act(async () => {
      root.render(
        <UrgentPriorityBadge priority={pending.order.priority} request={pending.request} />,
      );
    });
    const badge = container.querySelector('[data-testid="urgent-po-priority-badge"]');
    expect(badge.textContent).toBe("NORMAL");
    expect(badge.getAttribute("data-urgent-display")).toBe("PENDING_REQUEST");
  });

  test("legacy urgent badge is URGENT and banner uses Ditandai Urgent", async () => {
    const legacy = previewScenarioSeed("legacy_urgent");
    await act(async () => {
      root.render(
        <>
          <UrgentPriorityBadge priority={legacy.order.priority} request={legacy.request} />
          <UrgentPoStatusBanner order={legacy.order} request={legacy.request} />
        </>,
      );
    });
    expect(container.querySelector('[data-testid="urgent-po-priority-badge"]').textContent).toBe("URGENT");
    const banner = container.querySelector('[data-testid="urgent-po-banner-legacy"]');
    expect(banner.textContent).toMatch(/Ditandai Urgent oleh/);
    expect(banner.textContent).not.toMatch(/Disetujui oleh/);
  });

  test("submit modal validates, continuous typing (≥8), focus retention, ID date hint", async () => {
    const onSubmit = jest.fn();
    await act(async () => {
      root.render(
        <UrgentRequestSubmitModal open poNumber="PO-1" onCancel={() => {}} onSubmit={onSubmit} />,
      );
    });

    const reason = container.querySelector('[data-testid="urgent-submit-reason"]');
    expect(document.activeElement).toBe(reason);
    const reasonNode = reason;
    await typeContinuous(reason, "Delapan+"); // 8 chars

    await act(async () => {
      container.querySelector('[data-testid="urgent-submit-send"]').click();
    });
    expect(onSubmit).not.toHaveBeenCalled();

    const target = container.querySelector('[data-testid="urgent-submit-target"]');
    await act(async () => {
      setInputValue(target, "2026-10-28");
    });
    expect(container.querySelector('[data-testid="urgent-submit-target-id"]')?.textContent).toMatch(/28 Okt 2026/);
    // Same textarea node still mounted (no remount)
    expect(container.querySelector('[data-testid="urgent-submit-reason"]')).toBe(reasonNode);

    await act(async () => {
      container.querySelector('[data-testid="urgent-submit-send"]').click();
    });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0].targetDate).toBe("2026-10-28");
  });

  test("review selected states + reject requires reason + confirm follows decision", async () => {
    const onConfirm = jest.fn();
    const request = buildPreviewRequest({
      reason: "Butuh cepat",
      targetDate: "2026-10-28",
    });
    await act(async () => {
      root.render(
        <UrgentRequestReviewModal open request={request} onCancel={() => {}} onConfirm={onConfirm} />,
      );
    });

    const confirm = container.querySelector('[data-testid="urgent-review-confirm"]');
    expect(confirm.disabled).toBe(true);

    await act(async () => {
      container.querySelector('[data-testid="urgent-review-approve"]').click();
    });
    expect(container.querySelector('[data-testid="urgent-review-approve-card"]').className).toMatch(/is-selected is-approve/);
    expect(container.querySelector('[data-testid="urgent-review-reject-card"]').className).not.toMatch(/is-selected/);
    expect(confirm.disabled).toBe(false);
    expect(confirm.getAttribute("data-decision")).toBe("approved");
    expect(confirm.textContent).toMatch(/Persetujuan/);

    await act(async () => {
      container.querySelector('[data-testid="urgent-review-reject"]').click();
    });
    expect(container.querySelector('[data-testid="urgent-review-reject-card"]').className).toMatch(/is-selected is-reject/);
    expect(container.querySelector('[data-testid="urgent-review-approve-card"]').className).not.toMatch(/is-selected/);
    expect(confirm.getAttribute("data-decision")).toBe("rejected");
    expect(confirm.textContent).toMatch(/Penolakan/);

    await act(async () => {
      confirm.click();
    });
    expect(onConfirm).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="urgent-review-notes-err"]')?.textContent).toMatch(/wajib/i);

    const notes = container.querySelector('[data-testid="urgent-review-notes"]');
    await typeContinuous(notes, "Kapasitas");
    await act(async () => {
      confirm.click();
    });
    expect(onConfirm).toHaveBeenCalledWith(
      expect.objectContaining({ decision: "rejected", decisionNotes: "Kapasitas" }),
    );
  });

  test("history detail shows reject reason for rejected requests", async () => {
    const pending = previewScenarioSeed("pending").request;
    const rejected = applyPreviewDecision(pending, {
      decision: "rejected",
      decisionNotes: "Kapasitas minggu ini penuh.",
    });
    await act(async () => {
      root.render(<UrgentHistoryDetail request={rejected} />);
    });
    expect(container.querySelector('[data-testid="urgent-history-reject-reason"]')?.textContent)
      .toMatch(/Kapasitas minggu ini penuh/);
    expect(container.querySelector('[data-testid="urgent-history-requester"]')?.textContent).toMatch(/CRM Preview/);
    expect(container.querySelector('[data-testid="urgent-history-target"]')?.textContent).toMatch(/28 Okt 2026/);
  });

  test("result modal for rejected exposes history reject reason", async () => {
    const rejected = previewScenarioSeed("rejected").request;
    await act(async () => {
      root.render(
        <UrgentRequestResultModal open request={rejected} onClose={() => {}} />,
      );
    });
    expect(container.querySelector('[data-testid="urgent-history-reject-reason"]')?.textContent).toBeTruthy();
    expect(container.querySelector('[data-testid="urgent-request-result-modal"]')).toBeTruthy();
  });

  test("source does not import api mutation helpers", () => {
    const fs = require("fs");
    const path = require("path");
    const ui = fs.readFileSync(path.join(__dirname, "UrgentPoApprovalUi.jsx"), "utf8");
    const page = fs.readFileSync(path.join(__dirname, "../../pages/UrgentPoApprovalPreview.jsx"), "utf8");
    expect(ui).not.toMatch(/setUrgent/);
    expect(page).not.toMatch(/api\.setUrgent/);
    expect(page).not.toMatch(/from ["'].*lib\/api["']/);
  });
});
