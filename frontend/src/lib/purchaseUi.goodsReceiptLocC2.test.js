import {
  LEGACY_PO_BRANCH_REQUIRED_MSG,
  __resetGrRecoveryIdentityForTests,
  buildRetryPurchaseGoodsReceiptBody,
  clearGrRecoveryIdentity,
  evaluateLiveReceiptCancelEligibility,
  freezePurchaseGoodsReceiptRequest,
  goodsReceiptWarehouseBlockMessage,
  goodsReceiptWarehouseChoiceForPo,
  loadGrRecoveryIdentity,
  parsePurchaseGoodsReceiptCreateError,
  purchaseGoodsReceiptPayloadSignature,
  purchaseGoodsReceiptRecoveryPanelMessage,
  saveGrRecoveryIdentity,
  shouldHoldGrRecoveryIdentity,
} from "./purchaseUi";

const ygy = { id: "branch-ygy", code: "YGY", name: "Yogyakarta", active: true };
const smg = { id: "branch-smg", code: "SMG", name: "Semarang", active: true };

const whYgyMain = {
  id: "wh-ygy-main", branch_id: "branch-ygy", code: "MAIN", name: "Gudang YGY", active: true, is_operational: true,
};
const whYgyB = {
  id: "wh-ygy-b", branch_id: "branch-ygy", code: "WHB", name: "Gudang YGY B", active: true, is_operational: true,
};
const whSmg = {
  id: "wh-smg", branch_id: "branch-smg", code: "SMG1", name: "Gudang SMG", active: true, is_operational: true,
};

beforeEach(() => {
  __resetGrRecoveryIdentityForTests();
});

describe("LOC-C2 goods receipt warehouse by PO.branch_id", () => {
  test("YGY PO → only YGY warehouses selectable", () => {
    const choice = goodsReceiptWarehouseChoiceForPo(
      [ygy, smg],
      [whYgyMain, whYgyB, whSmg],
      "branch-ygy",
    );
    expect(choice.blocked).toBe(false);
    expect(choice.explicit).toBe(true);
    expect(choice.rows.map((r) => r.id)).toEqual(["wh-ygy-main", "wh-ygy-b"]);
    expect(choice.rows.every((r) => r.branch_id === "branch-ygy")).toBe(true);
  });

  test("wrong-branch warehouse is not in choice rows", () => {
    const choice = goodsReceiptWarehouseChoiceForPo([ygy, smg], [whYgyMain, whSmg], "branch-ygy");
    expect(choice.rows.map((r) => r.id)).toEqual(["wh-ygy-main"]);
    expect(choice.rows.find((r) => r.id === "wh-smg")).toBeUndefined();
  });

  test("legacy PO missing branch → blocked; never invents YGY", () => {
    const choice = goodsReceiptWarehouseChoiceForPo([ygy], [whYgyMain], "");
    expect(choice.blocked).toBe(true);
    expect(choice.reason).toBe("legacy_branch_missing");
    expect(choice.rows).toEqual([]);
    expect(choice.warehouseId).toBe("");
    expect(goodsReceiptWarehouseBlockMessage(choice)).toBe(LEGACY_PO_BRANCH_REQUIRED_MSG);
  });

  test("PO branch not in access-scoped branches → forbidden", () => {
    const choice = goodsReceiptWarehouseChoiceForPo([smg], [whYgyMain, whSmg], "branch-ygy");
    expect(choice.blocked).toBe(true);
    expect(choice.reason).toBe("branch_forbidden");
  });

  test("single matching warehouse auto-selects", () => {
    const choice = goodsReceiptWarehouseChoiceForPo([ygy], [whYgyMain, whSmg], "branch-ygy");
    expect(choice).toMatchObject({
      warehouseId: "wh-ygy-main",
      blocked: false,
      explicit: false,
    });
  });
});

describe("LOC-C2 frozen recovery payload + identity", () => {
  const original = {
    purchase_order_id: "po-1",
    receipt_date: "2026-10-23",
    warehouse_id: "wh-1",
    lines: [
      { purchase_order_line_id: "l1", received_qty: 1 },
      { purchase_order_line_id: "l2", received_qty: 2 },
    ],
  };

  test("retry POST body identical to frozen original payload + key", () => {
    const frozen = freezePurchaseGoodsReceiptRequest({
      idempotency_key: "key-abc",
      payload: original,
    });
    const mutatedForm = {
      purchase_order_id: "po-OTHER",
      receipt_date: "2099-01-01",
      warehouse_id: "wh-OTHER",
      lines: [{ purchase_order_line_id: "l1", received_qty: 99 }],
    };
    const retryBody = buildRetryPurchaseGoodsReceiptBody(frozen);
    expect(retryBody.idempotency_key).toBe("key-abc");
    expect(retryBody.purchase_order_id).toBe("po-1");
    expect(retryBody.warehouse_id).toBe("wh-1");
    expect(retryBody.receipt_date).toBe("2026-10-23");
    expect(retryBody.lines).toEqual([
      { purchase_order_line_id: "l1", received_qty: 1 },
      { purchase_order_line_id: "l2", received_qty: 2 },
    ]);
    expect(retryBody).not.toMatchObject(mutatedForm);
    expect(purchaseGoodsReceiptPayloadSignature(retryBody)).toBe(frozen.signature);
  });

  test("retry key identical; freeze does not mint a second key", () => {
    const frozen = freezePurchaseGoodsReceiptRequest({
      idempotency_key: "same-key-1",
      payload: original,
    });
    const a = buildRetryPurchaseGoodsReceiptBody(frozen);
    const b = buildRetryPurchaseGoodsReceiptBody(frozen);
    expect(a.idempotency_key).toBe("same-key-1");
    expect(b.idempotency_key).toBe(a.idempotency_key);
  });

  test("close modal recoverable/uncertain/ambiguous → identity survives in session", () => {
    const frozen = freezePurchaseGoodsReceiptRequest({
      idempotency_key: "hold-key",
      payload: original,
    });
    for (const kind of ["recoverable", "uncertain", "ambiguous"]) {
      __resetGrRecoveryIdentityForTests();
      const outcome = { kind, inviteNewKey: false, showRetrySameKey: true, receipt_id: kind === "uncertain" ? "" : "r1" };
      expect(shouldHoldGrRecoveryIdentity(outcome)).toBe(true);
      saveGrRecoveryIdentity({ frozen, outcome, message: kind });
      const held = loadGrRecoveryIdentity();
      expect(held.frozen.idempotency_key).toBe("hold-key");
      expect(held.frozen.payload.warehouse_id).toBe("wh-1");
      expect(held.outcome.kind).toBe(kind);
      expect(held.outcome.inviteNewKey).toBe(false);
    }
  });

  test("reload/navigation does not auto new-key; held identity restores fail-closed", () => {
    const frozen = freezePurchaseGoodsReceiptRequest({
      idempotency_key: "persist-key",
      payload: original,
    });
    saveGrRecoveryIdentity({
      frozen,
      outcome: { kind: "uncertain", inviteNewKey: false, showRetrySameKey: true },
      message: "timeout",
    });
    // Simulate module remount: clear memory only, keep sessionStorage.
    const raw = sessionStorage.getItem("eternal.pur.gr_receive_recovery.v1");
    expect(raw).toBeTruthy();
    __resetGrRecoveryIdentityForTests();
    sessionStorage.setItem("eternal.pur.gr_receive_recovery.v1", raw);
    const restored = loadGrRecoveryIdentity();
    expect(restored.frozen.idempotency_key).toBe("persist-key");
    const retry = buildRetryPurchaseGoodsReceiptBody(restored.frozen);
    expect(retry.idempotency_key).toBe("persist-key");
    expect(retry.inviteNewKey).toBeUndefined();
  });

  test("network timeout / uncertain does not invite new-key", () => {
    const parsed = parsePurchaseGoodsReceiptCreateError({ message: "Network Error" });
    expect(parsed.kind).toBe("uncertain");
    expect(parsed.inviteNewKey).toBe(false);
    expect(parsed.showRetrySameKey).toBe(true);
    expect(parsed.message).toMatch(/Jangan buat penerimaan baru/);
    expect(parsed.message).not.toMatch(/stok belum/i);
  });

  test("recoverable + cancelled contracts", () => {
    const recoverable = parsePurchaseGoodsReceiptCreateError({
      response: {
        status: 500,
        data: { detail: { code: "FAILED", message: "HPP gagal", receipt_id: "rcpt-1", recoverable: true } },
      },
    });
    expect(recoverable.kind).toBe("recoverable");
    expect(recoverable.showRetrySameKey).toBe(true);
    expect(recoverable.mayProbeCancel).toBe(true);
    expect(purchaseGoodsReceiptRecoveryPanelMessage(recoverable)).toMatch(/rcpt-1/);

    const cancelled = parsePurchaseGoodsReceiptCreateError({
      response: {
        status: 409,
        data: { detail: { code: "IDEMPOTENCY_CANCELLED", message: "sudah dibatalkan", receipt_id: "rcpt-c" } },
      },
    });
    expect(cancelled.kind).toBe("idempotency_cancelled");
    expect(cancelled.showRetrySameKey).toBe(false);
    expect(cancelled.inviteNewKey).toBe(false);
  });
});

describe("LOC-C2 safe cancel eligibility", () => {
  test("cancel requires live receipt can_cancel=true", () => {
    expect(evaluateLiveReceiptCancelEligibility(null).allowed).toBe(false);
    expect(evaluateLiveReceiptCancelEligibility({ id: "r1" }).allowed).toBe(false);
    expect(evaluateLiveReceiptCancelEligibility({ id: "r1", can_cancel: false, cancel_block_reason: "faktur" }).allowed).toBe(false);
    expect(evaluateLiveReceiptCancelEligibility({ id: "r1", can_cancel: false }).reason).toMatch(/tidak dapat dibatalkan|faktur/i);
  });

  test("can_cancel=false → cancel unavailable", () => {
    const gate = evaluateLiveReceiptCancelEligibility({
      id: "r2",
      status: "ACTIVE",
      can_cancel: false,
      cancel_block_reason: "Reversal stock belum aman.",
    });
    expect(gate.allowed).toBe(false);
    expect(gate.reason).toMatch(/Reversal stock/);
  });

  test("receipt status cancelled / accounting posted → blocked", () => {
    expect(evaluateLiveReceiptCancelEligibility({
      id: "r3", status: "CANCELLED", can_cancel: true,
    }).allowed).toBe(false);
    expect(evaluateLiveReceiptCancelEligibility({
      id: "r4", status: "ACTIVE", can_cancel: true, accounting_status: "posted",
    }).allowed).toBe(false);
  });

  test("can_cancel=true active → allowed", () => {
    const gate = evaluateLiveReceiptCancelEligibility({
      id: "r5", status: "ACTIVE", can_cancel: true, accounting_status: "not_posted",
    });
    expect(gate.allowed).toBe(true);
    expect(gate.receipt.id).toBe("r5");
  });

  test("clear identity after resolve", () => {
    saveGrRecoveryIdentity({
      frozen: freezePurchaseGoodsReceiptRequest({ idempotency_key: "x", payload: { purchase_order_id: "p", lines: [] } }),
      outcome: { kind: "recoverable" },
    });
    clearGrRecoveryIdentity();
    expect(loadGrRecoveryIdentity()).toBeNull();
  });
});
