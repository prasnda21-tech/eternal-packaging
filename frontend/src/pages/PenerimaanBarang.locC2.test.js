import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "PenerimaanBarang.jsx"), "utf8");
const ui = fs.readFileSync(path.join(__dirname, "../lib/purchaseUi.js"), "utf8");

describe("LOC-C2 PenerimaanBarang recovery safety contracts", () => {
  test("filters warehouses by PO.branch_id via purchase order fetch", () => {
    expect(page).toMatch(/goodsReceiptWarehouseChoiceForPo/);
    expect(page).toMatch(/api\.getPurchaseOrder\(recvPoId\)/);
    expect(page).toMatch(/po\?\.branch_id/);
    expect(page).not.toMatch(/goodsReceiptWarehouseChoice\(/);
  });

  test("freezes payload before request; retry uses frozen body not live form", () => {
    expect(page).toMatch(/freezePurchaseGoodsReceiptRequest/);
    expect(page).toMatch(/buildRetryPurchaseGoodsReceiptBody\(recvFrozenRef\.current\)/);
    expect(page).toMatch(/buildRetryPurchaseGoodsReceiptBody\(frozen\)/);
    expect(page).toMatch(/recvFrozenRef/);
    expect(page).toMatch(/recvSubmitLockRef/);
    expect(ui).toMatch(/freezePurchaseGoodsReceiptRequest/);
    expect(ui).toMatch(/buildRetryPurchaseGoodsReceiptBody/);
  });

  test("close modal while hold kinds retains identity via saveGrRecoveryIdentity", () => {
    expect(page).toMatch(/shouldHoldGrRecoveryIdentity\(outcome\)/);
    expect(page).toMatch(/saveGrRecoveryIdentity/);
    expect(page).toMatch(/loadGrRecoveryIdentity/);
    expect(page).toMatch(/data-testid="gr-recovery-hold-banner"/);
    expect(page).toMatch(/data-testid="gr-recovery-resume"/);
    expect(page).toMatch(/Lanjutkan pemulihan/);
    expect(ui).toMatch(/sessionStorage/);
    expect(ui).not.toMatch(/localStorage\.setItem\(GR_RECOVERY/);
  });

  test("held recovery blocks new-key create for other PO / direct create", () => {
    expect(page).toMatch(/Ada pemulihan penerimaan yang belum selesai/);
    expect(page).toMatch(/Selesaikan pemulihan penerimaan tertunda/);
    expect(page).toMatch(/Jangan buat penerimaan baru dengan kunci berbeda|jangan buat penerimaan baru/i);
  });

  test("cancel requires live GET + can_cancel gate; no receipt_id-only cancel", () => {
    expect(page).toMatch(/probeCancelFromRecovery/);
    expect(page).toMatch(/api\.getPurchaseGoodsReceipt\(receiptId\)/);
    expect(page).toMatch(/evaluateLiveReceiptCancelEligibility/);
    expect(page).toMatch(/data-testid="po-receive-probe-cancel"/);
    expect(page).toMatch(/Periksa Pembatalan/);
    expect(page).not.toMatch(/openCancelReceipt\(\{\s*id: recvRecovery\.receipt_id/);
    expect(page).toMatch(/Re-validate live can_cancel|evaluateLiveReceiptCancelEligibility\(live\)/);
  });

  test("double-submit lock on save/retry", () => {
    expect(page).toMatch(/if \(recvBusy \|\| recvSubmitLockRef\.current \|\| !recvPreview\) return/);
    expect(page).toMatch(/recvSubmitLockRef\.current = true/);
    expect(page).toMatch(/disabled=\{recvBusy/);
  });

  test("uncertain\/ambiguous keep same-key retry; never invite new key", () => {
    expect(ui).toMatch(/inviteNewKey: false/);
    expect(ui).toMatch(/kind: "uncertain"/);
    expect(ui).toMatch(/showRetrySameKey: true/);
    expect(page).toMatch(/data-testid="po-receive-retry-same-key"/);
  });

  test("existing GR layout hooks preserved", () => {
    expect(page).toMatch(/data-testid="po-receive-modal"/);
    expect(page).toMatch(/data-testid="receive-warehouse"/);
    expect(page).toMatch(/data-testid="receive-qty-input"/);
    expect(page).toMatch(/data-testid="save-po-receipt"/);
    expect(page).toMatch(/createPurchaseGoodsReceipt/);
    expect(page).toMatch(/remaining_receivable_qty/);
  });

  test("IDEMPOTENCY_CANCELLED still fail-closed for new create", () => {
    expect(ui).toMatch(/IDEMPOTENCY_CANCELLED/);
    expect(page).toMatch(/idempotency_cancelled/);
  });
});
