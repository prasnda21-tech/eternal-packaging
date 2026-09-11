import {
  isApprovalDesignComplete,
  isCustomerDesignApproved,
  resolveApprovalDesignChip,
} from "./designEvidence";

const uploaded = {
  id: "df-1",
  name: "art.pdf",
  data_url: "data:application/pdf;base64,eA==",
  uploaded_at: "2026-09-11T01:00:00+00:00",
};

describe("Approval Design — customer approval is the same gate", () => {
  test("screenshot split: CA approved + design_status Waiting completes Approval Design", () => {
    const order = {
      design_status: "Waiting",
      design_bypassed: false,
      design_files: [uploaded],
      customer_approval: { status: "approved" },
      current_stage: 2,
    };
    expect(isCustomerDesignApproved(order)).toBe(true);
    expect(isApprovalDesignComplete(order)).toBe(true);
    expect(resolveApprovalDesignChip(order, 2)).toEqual({ state: "done", label: "Selesai" });
  });

  test("reload of the same payload stays complete", () => {
    const order = {
      design_status: "Waiting",
      design_files: [uploaded],
      customer_approval: { status: "approved" },
    };
    const first = resolveApprovalDesignChip(order, 2);
    const second = resolveApprovalDesignChip({ ...order }, 2);
    expect(second).toEqual(first);
    expect(second.state).toBe("done");
  });

  test("customer reject does not complete Approval Design", () => {
    const order = {
      design_status: "Waiting",
      design_files: [uploaded],
      customer_approval: { status: "rejected", reject_reason: "revisi" },
    };
    expect(isApprovalDesignComplete(order)).toBe(false);
    expect(resolveApprovalDesignChip(order, 2)).toEqual({
      state: "current",
      label: "Menunggu approval",
    });
  });

  test("internal approve still completes", () => {
    const order = {
      design_status: "Approved",
      design_files: [uploaded],
      customer_approval: { status: "active" },
    };
    expect(resolveApprovalDesignChip(order, 3)).toEqual({ state: "done", label: "Selesai" });
  });

  test("bypass still marks bypass", () => {
    const order = {
      design_status: "Waiting",
      design_bypassed: true,
      design_files: [uploaded],
    };
    expect(isApprovalDesignComplete(order)).toBe(true);
    expect(resolveApprovalDesignChip(order, 3)).toEqual({ state: "bypass", label: "BYPASS" });
  });
});
