import { canEditDeliverySignatures, confirmDeliveredBlockReason } from "./deliverySignature";
import {
  applySavedSignature,
  canDismissSignatureModal,
  cancelKeepsSavedPair,
  clearModalDraft,
  emptySignatureCannotSave,
  signatureCollapsedState,
  signatureModalTitle,
  signatureOpenButtonTitle,
  type SignaturePair,
} from "./signatureCapture";

function assertEqual(actual: unknown, expected: unknown, label: string) {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

const recip = "data:image/png;base64,rec";
const driver = "data:image/png;base64,drv";
const twoProofs = [{ id: "a" }, { id: "b" }];

{
  const empty = signatureCollapsedState({ hasLocal: false, hasSaved: false, frozen: false });
  assertEqual(empty.action, "create", "1 collapsed create");
  assertEqual(signatureOpenButtonTitle("recipient", "create"), "Buat TTD Penerima", "1 recipient button");
  assertEqual(signatureOpenButtonTitle("driver", "create"), "Buat TTD Driver", "1 driver button");
}

{
  let pair: SignaturePair = { recipient: null, driver: null };
  pair = applySavedSignature(pair, "recipient", recip);
  assertEqual(pair.recipient, recip, "2 recipient saved");
  assertEqual(pair.driver, null, "2 driver untouched");
  pair = applySavedSignature(pair, "driver", driver);
  assertEqual(pair.recipient, recip, "2 recipient still");
  assertEqual(pair.driver, driver, "5 driver saved independently");
}

{
  const saved: SignaturePair = { recipient: recip, driver: driver };
  const afterCancel = cancelKeepsSavedPair(saved);
  assertEqual(afterCancel.recipient, recip, "3 cancel keeps recipient");
  assertEqual(afterCancel.driver, driver, "3 cancel keeps driver");
}

{
  assertEqual(clearModalDraft(), null, "4 hapus draft only");
  const pair: SignaturePair = { recipient: recip, driver: driver };
  assertEqual(applySavedSignature(pair, "recipient", recip).driver, driver, "4 hapus modal does not wipe other");
}

{
  assertEqual(emptySignatureCannotSave(null), true, "6 empty blocked");
  assertEqual(emptySignatureCannotSave("not-an-image"), true, "6 invalid blocked");
  assertEqual(emptySignatureCannotSave(recip), false, "6 canvas ok");
}

{
  assertEqual(canEditDeliverySignatures({ delivery_status: "Terkirim" }), false, "7 frozen no edit");
  const frozen = signatureCollapsedState({ hasLocal: true, hasSaved: true, frozen: true });
  assertEqual(frozen.action, "none", "7 frozen no button");
  assertEqual(signatureCollapsedState({ hasLocal: true, hasSaved: false, frozen: false }).action, "edit", "7 local editable");
}

{
  const reason = confirmDeliveredBlockReason(twoProofs, "Budi", recip, null, null, null);
  assertEqual((reason || "").includes("driver"), true, "8 validation unchanged");
  assertEqual(signatureModalTitle("recipient"), "Tanda Tangan Penerima", "modal title recip");
  assertEqual(signatureModalTitle("driver"), "Tanda Tangan Driver", "modal title driver");
  assertEqual(canDismissSignatureModal(true), false, "back blocked with draft");
  assertEqual(canDismissSignatureModal(false), true, "back ok empty");
}

console.log("signatureCapture tests ok");
