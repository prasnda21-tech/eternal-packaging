const fs = require("fs") as typeof import("fs");
const path = require("path") as typeof import("path");
import {
  authoritativeDriverDisplayName,
  canConfirmDelivered,
  canEditDeliverySignatures,
  canSubmitConfirmDelivered,
  confirmDeliveredBlockReason,
  deliveryConfirmSteps,
  driverSignatureFormFields,
  hasSavedDriverSig,
  hasSavedRecipientSig,
  isDeliveryComplete,
  isSignatureFrozen,
  MIN_DELIVERY_PROOFS,
  recipientSignatureFormFields,
  shouldUploadDriverSignature,
  shouldUploadRecipientSignature,
} from "./deliverySignature";
import { strokesToPngDataUrl } from "./signaturePng";

function assertEqual(actual: unknown, expected: unknown, label: string) {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

function assert(cond: unknown, label: string) {
  if (!cond) throw new Error(label);
}

const twoProofs = [{ id: "a" }, { id: "b" }];
const recipSaved = {
  id: "sig",
  recipient_name: "Budi",
  signature_storage_path: "x",
  recipient_signed_at: "t",
  frozen: false,
};
const driverSaved = {
  id: "dsig",
  driver_signature_storage_path: "y",
  driver_signed_at: "t",
  frozen: false,
};
const canvas = "data:image/png;base64,abc";

{
  assertEqual(MIN_DELIVERY_PROOFS, 2, "1 min proofs");
  assertEqual(canConfirmDelivered([{ id: "a" }]), false, "1 one proof hidden");
  assertEqual(
    confirmDeliveredBlockReason([{ id: "a" }], "Budi", canvas, null, canvas, null)?.includes("foto") || false,
    true,
    "1 gated by photos"
  );
  assertEqual(
    canSubmitConfirmDelivered([{ id: "a" }], "Budi", canvas, null, canvas, null),
    false,
    "1 cannot submit"
  );
}

{
  assertEqual(canConfirmDelivered(twoProofs), true, "2 proofs unlock section");
  assert(
    confirmDeliveredBlockReason(twoProofs, "", null, null, null, null),
    "2 block without name/sig"
  );
}

{
  const reason = confirmDeliveredBlockReason(twoProofs, "", null, null, canvas, driverSaved);
  assertEqual((reason || "").toLowerCase().includes("nama"), true, "3 name required");
  assertEqual(
    canSubmitConfirmDelivered(twoProofs, "B", canvas, null, canvas, null),
    false,
    "3 name too short"
  );
}

{
  const reason = confirmDeliveredBlockReason(twoProofs, "Budi", null, null, canvas, null);
  assertEqual((reason || "").includes("penerima"), true, "4 recipient sig required");
}

{
  const reason = confirmDeliveredBlockReason(twoProofs, "Budi", canvas, null, null, null);
  assertEqual((reason || "").includes("driver"), true, "5 driver sig required");
}

{
  const a = { id: "r1", recipient_name: "Ali", signature_storage_path: "p", frozen: false, recipient_signed_at: "t" };
  const b = { id: "d1", driver_signature_storage_path: "q", frozen: false, driver_signed_at: "t" };
  assertEqual(hasSavedRecipientSig(a), true, "6 recip independent");
  assertEqual(hasSavedDriverSig(b), true, "6 driver independent");
  assertEqual(a.id === b.id, false, "6 distinct ids");
  assertEqual(hasSavedRecipientSig(null), false, "6 missing recip");
  assertEqual(hasSavedDriverSig(null), false, "6 missing driver");
}

{
  assertEqual(hasSavedRecipientSig(recipSaved), true, "7 saved recipient");
  assertEqual(
    canSubmitConfirmDelivered(twoProofs, "Budi", null, recipSaved, canvas, null),
    true,
    "7 saved recipient + canvas driver"
  );
}

{
  assertEqual(hasSavedDriverSig(driverSaved), true, "8 saved driver");
  assertEqual(
    canSubmitConfirmDelivered(twoProofs, "Budi", canvas, null, null, driverSaved),
    true,
    "8 canvas recipient + saved driver"
  );
}

{
  const frozenRecip = { ...recipSaved, frozen: true };
  const frozenDriver = { ...driverSaved, frozen: true };
  assertEqual(isSignatureFrozen(frozenRecip), true, "9 recip frozen");
  assertEqual(isSignatureFrozen(frozenDriver), true, "9 driver frozen");
  assertEqual(hasSavedRecipientSig(frozenRecip), false, "9 frozen not active");
  assertEqual(isDeliveryComplete({ delivery_status: "Terkirim" }), true, "9 terkirim");
  assertEqual(canEditDeliverySignatures({ delivery_status: "Terkirim" }), false, "9 readonly");
  assertEqual(canEditDeliverySignatures({ delivery_status: "Belum Terkirim" }), true, "9 editable");
}

{
  const planNew = deliveryConfirmSteps({
    proofs: twoProofs,
    recipientName: "Budi",
    signatureDataUrl: canvas,
    orderSig: null,
    driverSignatureDataUrl: canvas,
    driverSig: null,
  });
  assertEqual(planNew.ok, true, "10 plan ok");
  assertEqual(planNew.steps.join(","), "recipient,driver,confirm", "10 upload both then confirm");

  const planSaved = deliveryConfirmSteps({
    proofs: twoProofs,
    recipientName: "Budi",
    signatureDataUrl: null,
    orderSig: recipSaved,
    driverSignatureDataUrl: null,
    driverSig: driverSaved,
  });
  assertEqual(planSaved.steps.join(","), "confirm", "10 saved skip reupload");
}

{
  assertEqual(shouldUploadRecipientSignature({
    recipientName: "Budi",
    signatureDataUrl: null,
    orderSig: recipSaved,
  }), false, "11 no remount upload recipient");
  assertEqual(shouldUploadDriverSignature({
    signatureDataUrl: null,
    driverSig: driverSaved,
  }), false, "11 no remount upload driver");
}

{
  const fields = driverSignatureFormFields(canvas);
  assertEqual("driver_name" in fields, false, "12 no driver_name field");
  assertEqual(fields.signature_data_url, canvas, "12 driver data url only");
  const recip = recipientSignatureFormFields("Budi Penerima", canvas);
  assertEqual(recip.recipient_name, "Budi Penerima", "12 recipient name");
  assertEqual(
    authoritativeDriverDisplayName({
      loading_checklist: { driver_name: "Supir Assigned" },
    }, { name: "Login User" }),
    "Supir Assigned",
    "12 assignment wins over login"
  );
}

{
  const reason = confirmDeliveredBlockReason(twoProofs, "Budi", null, null, null, null);
  assert(reason, "13 client shows backend-equivalent message");
}

{
  assertEqual(canConfirmDelivered(twoProofs), true, "14 proof helper unchanged");
  assertEqual(canConfirmDelivered([{ id: "a" }]), false, "14 still min 2");
}

{
  const apiSrc = fs.readFileSync(path.join(__dirname, "../api/eternal.ts"), "utf8");
  const panelSrc = fs.readFileSync(path.join(__dirname, "../components/DriverDeliveryPanel.tsx"), "utf8");
  const poSrc = fs.readFileSync(path.join(__dirname, "../../app/(app)/po/[id].tsx"), "utf8");
  assert(apiSrc.includes("/confirm-delivered"), "15 confirm-delivered exists");
  assert(!/\/orders\/\$\{[^}]+}\/delivered(?!-)/.test(apiSrc), "15 no legacy delivered endpoint");
  assert(apiSrc.includes("delivery-recipient-signature"), "15 recipient endpoint");
  assert(apiSrc.includes("delivery-driver-signature"), "15 driver endpoint");
  assert(panelSrc.includes("confirmDelivered"), "15 panel uses confirm");
  assert(!/\/orders\/.+\bdelivered\b/.test(panelSrc.replace(/confirm-delivered/g, "")), "15 panel no legacy");
  assert(poSrc.includes("DriverDeliveryPanel"), "15 po uses panel");
}

{
  const png = strokesToPngDataUrl(
    [[{ x: 4, y: 4 }, { x: 20, y: 18 }, { x: 36, y: 8 }]],
    48,
    32
  );
  assert(png && png.startsWith("data:image/png;base64,"), "png data url");
  const b64 = png!.slice("data:image/png;base64,".length);
  const buf = Buffer.from(b64, "base64");
  assertEqual(buf[0], 0x89, "png magic0");
  assertEqual(buf[1], 0x50, "png magic1");
  assert(buf.length > 64, "png larger than empty threshold");
}

console.log("deliverySignature tests passed");
