import React, { useEffect, useRef, useState } from "react";
import { Alert, Image, StyleSheet, Text, TextInput, View } from "react-native";
import { eternalApi } from "../api/eternal";
import { colors, space, typography, ui } from "../theme";
import { apiErrorMessage, newActionId } from "../utils/format";
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
  isSignatureFrozen,
  MIN_DELIVERY_PROOFS,
  normalizeRecipientName,
  recipientSignatureFormFields,
  signatureReadinessLabel,
} from "../utils/deliverySignature";
import {
  applySavedSignature,
  signatureCollapsedState,
  signatureModalTitle,
  signatureOpenButtonTitle,
  type SignatureSlot,
} from "../utils/signatureCapture";
import { GhostButton, PrimaryButton } from "./Ui";
import { SectionCardHeader } from "./SectionCardHeader";
import SignatureCaptureModal from "./SignatureCaptureModal";

type Props = {
  order: any;
  token: string | null;
  user?: { name?: string } | null;
  hasPerm: (p: string) => boolean;
  busy: boolean;
  onPickProof: () => void;
  onOpenSj: (fileId: string) => void;
  onUpdated: (order: Record<string, unknown>) => void;
  onError: (message: string) => void;
  onDrawingChange?: (drawing: boolean) => void;
};

export default function DriverDeliveryPanel({
  order,
  token,
  user,
  hasPerm,
  busy,
  onPickProof,
  onOpenSj,
  onUpdated,
  onError,
  onDrawingChange,
}: Props) {
  const proofs = order.delivery_proofs || [];
  const sjFiles = Array.isArray(order.surat_jalan_files) ? order.surat_jalan_files : [];
  const delivered = !canEditDeliverySignatures(order);
  const orderSig = order.delivery_recipient_signature;
  const driverSig = order.delivery_driver_signature;
  const driverDisplayName = authoritativeDriverDisplayName(order, user);
  const recipientFrozen = isSignatureFrozen(orderSig);
  const driverFrozen = isSignatureFrozen(driverSig);

  const [recipientName, setRecipientName] = useState(
    () => orderSig?.recipient_name || ""
  );
  const [recipientDataUrl, setRecipientDataUrl] = useState<string | null>(null);
  const [driverDataUrl, setDriverDataUrl] = useState<string | null>(null);
  const [openSlot, setOpenSlot] = useState<SignatureSlot | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState("");
  const submitLock = useRef(false);

  useEffect(() => {
    const n = orderSig?.recipient_name;
    if (n && !recipientName) setRecipientName(n);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderSig?.id]);

  const showSignatureUi = canConfirmDelivered(proofs);
  const locked = delivered || submitting || busy;
  const deliverEnabled = showSignatureUi
    && !delivered
    && canSubmitConfirmDelivered(
      proofs,
      recipientName,
      recipientDataUrl,
      orderSig,
      driverDataUrl,
      driverSig
    );
  const deliverHint = showSignatureUi && !delivered
    ? confirmDeliveredBlockReason(
      proofs,
      recipientName,
      recipientDataUrl,
      orderSig,
      driverDataUrl,
      driverSig
    )
    : null;

  const recipientUi = signatureCollapsedState({
    hasLocal: !!recipientDataUrl,
    hasSaved: hasSavedRecipientSig(orderSig),
    frozen: recipientFrozen || delivered,
  });
  const driverUi = signatureCollapsedState({
    hasLocal: !!driverDataUrl,
    hasSaved: hasSavedDriverSig(driverSig),
    frozen: driverFrozen || delivered,
  });

  const saveModalSignature = (dataUrl: string) => {
    if (!openSlot) return;
    const next = applySavedSignature(
      { recipient: recipientDataUrl, driver: driverDataUrl },
      openSlot,
      dataUrl
    );
    setRecipientDataUrl(next.recipient);
    setDriverDataUrl(next.driver);
    setOpenSlot(null);
    onDrawingChange?.(false);
  };

  const openConfirm = () => {
    if (!deliverEnabled || locked || submitLock.current) return;
    const blocked = confirmDeliveredBlockReason(
      proofs, recipientName, recipientDataUrl, orderSig, driverDataUrl, driverSig
    );
    if (blocked) {
      onError(blocked);
      return;
    }
    const name = normalizeRecipientName(recipientName || orderSig?.recipient_name);
    Alert.alert(
      "KONFIRMASI PENGIRIMAN",
      [
        `Bukti Pengiriman: ${proofs.length} foto`,
        `Penerima: ${name}`,
        `TTD Penerima: ${signatureReadinessLabel(hasSavedRecipientSig(orderSig), !!recipientDataUrl)}`,
        `Pengirim: ${driverDisplayName || "—"}`,
        `TTD Pengirim: ${signatureReadinessLabel(hasSavedDriverSig(driverSig), !!driverDataUrl)}`,
      ].join("\n"),
      [
        { text: "Batal", style: "cancel" },
        { text: "Konfirmasi Terkirim", onPress: () => { void executeConfirm(); } },
      ]
    );
  };

  const executeConfirm = async () => {
    if (submitLock.current || locked) return;
    submitLock.current = true;
    setSubmitting(true);
    onError("");
    const actionId = newActionId();
    try {
      const plan = deliveryConfirmSteps({
        proofs,
        recipientName,
        signatureDataUrl: recipientDataUrl,
        orderSig,
        driverSignatureDataUrl: driverDataUrl,
        driverSig,
      });
      if (!plan.ok) throw new Error(plan.reason || "Belum bisa konfirmasi terkirim.");

      let latest = order;
      for (const step of plan.steps) {
        if (step === "recipient") {
          setStatus("Menyimpan tanda tangan...");
          const fields = recipientSignatureFormFields(recipientName, String(recipientDataUrl));
          const res = await eternalApi.uploadDeliveryRecipientSignature(latest.id, {
            recipientName: fields.recipient_name,
            signatureDataUrl: fields.signature_data_url,
          });
          latest = res.order;
          onUpdated(res.order);
        } else if (step === "driver") {
          setStatus("Menyimpan tanda tangan...");
          const fields = driverSignatureFormFields(String(driverDataUrl));
          const res = await eternalApi.uploadDeliveryDriverSignature(latest.id, {
            signatureDataUrl: fields.signature_data_url,
          });
          latest = res.order;
          onUpdated(res.order);
        } else {
          setStatus("Mengonfirmasi pengiriman...");
          const res = await eternalApi.confirmDelivered(latest.id, actionId);
          latest = res.order;
          onUpdated(res.order);
        }
      }
      setRecipientDataUrl(null);
      setDriverDataUrl(null);
      Alert.alert("Terkirim", "Pengiriman dikonfirmasi.");
    } catch (e) {
      onError(apiErrorMessage(e, "Gagal konfirmasi terkirim"));
    } finally {
      setStatus("");
      setSubmitting(false);
      submitLock.current = false;
    }
  };

  return (
    <View style={ui.cardFlush} testID="driver-panel">
      <SectionCardHeader tone="delivery" title="Pengiriman / Terkirim" />
      <View style={ui.cardBody}>
      <Text style={typography.subtitle}>
        Bukti: {proofs.length} foto (min {MIN_DELIVERY_PROOFS}). Upload tidak otomatis menandai terkirim.
      </Text>

      {hasPerm("delivery_note.view") || hasPerm("delivery_note.view_file") ? (
        <View style={{ marginTop: space.md }}>
          <Text style={typography.label}>Surat Jalan</Text>
          {!sjFiles.length ? (
            <Text style={typography.subtitle}>Belum ada file Surat Jalan.</Text>
          ) : (
            sjFiles.map((f: any) => (
              <GhostButton
                key={f.id || f.storage_path}
                title={f.name || f.original_filename || "Buka Surat Jalan"}
                onPress={() => f.id && onOpenSj(f.id)}
                disabled={!hasPerm("delivery_note.view_file") || !f.id}
                style={{ marginTop: 8 }}
              />
            ))
          )}
        </View>
      ) : null}

      <View style={{ marginTop: space.md }}>
        <Text style={typography.label}>Bukti Pengiriman</Text>
        <View style={styles.proofGrid}>
          {proofs.map((p: any, i: number) => {
            const uri = p.storage_path && token ? eternalApi.fileUrl(p.storage_path, token) : null;
            return (
              <View key={p.id || i} style={styles.proofThumb}>
                {uri ? (
                  <Image source={{ uri }} style={styles.proofImg} />
                ) : (
                  <Text style={typography.subtitle}>Foto {i + 1}</Text>
                )}
              </View>
            );
          })}
        </View>
        {!delivered ? (
          <PrimaryButton
            title="Upload Foto Bukti"
            onPress={onPickProof}
            busy={busy}
            disabled={submitting}
            testID="proof-upload-button"
            style={{ marginTop: space.sm }}
          />
        ) : null}
      </View>

      {showSignatureUi ? (
        <View style={{ marginTop: space.md }} testID="recipient-signature-block">
          <Text style={typography.label}>Penerima</Text>
          <Text style={[typography.body, { marginTop: 8, marginBottom: 6 }]}>Nama Penerima</Text>
          <TextInput
            testID="recipient-name-input"
            value={recipientName}
            onChangeText={setRecipientName}
            placeholder="Nama penerima barang"
            placeholderTextColor={colors.muted}
            autoComplete="name"
            editable={!locked}
            maxLength={120}
            style={ui.input}
          />
          <Text style={[typography.subtitle, { marginTop: 8 }]} testID="recipient-signature-status">
            {recipientUi.status}
          </Text>
          {recipientDataUrl ? (
            <Image source={{ uri: recipientDataUrl }} style={styles.sigPreview} resizeMode="contain" testID="recipient-signature-preview" />
          ) : null}
          {recipientUi.action !== "none" ? (
            <GhostButton
              title={signatureOpenButtonTitle("recipient", recipientUi.action)}
              onPress={() => setOpenSlot("recipient")}
              disabled={locked}
              testID="recipient-signature-open"
              style={{ marginTop: 8 }}
            />
          ) : (
            <Text style={[typography.subtitle, { marginTop: 6 }]} testID="recipient-signature-frozen">
              Tanda tangan penerima terkunci
            </Text>
          )}

          <View style={{ marginTop: space.md }} testID="driver-signature-block">
            <Text style={typography.label}>Driver</Text>
            <Text style={[typography.subtitle, { marginTop: 6 }]} testID="driver-name-display">
              Nama driver: <Text style={typography.mono}>{driverDisplayName || "—"}</Text>
            </Text>
            <Text style={[typography.subtitle, { marginTop: 8 }]} testID="driver-signature-status">
              {driverUi.status}
            </Text>
            {driverDataUrl ? (
              <Image source={{ uri: driverDataUrl }} style={styles.sigPreview} resizeMode="contain" testID="driver-signature-preview" />
            ) : null}
            {driverUi.action !== "none" ? (
              <GhostButton
                title={signatureOpenButtonTitle("driver", driverUi.action)}
                onPress={() => setOpenSlot("driver")}
                disabled={locked}
                testID="driver-signature-open"
                style={{ marginTop: 8 }}
              />
            ) : (
              <Text style={[typography.subtitle, { marginTop: 6 }]} testID="driver-signature-frozen">
                Tanda tangan driver terkunci
              </Text>
            )}
          </View>

          <SignatureCaptureModal
            visible={openSlot != null}
            title={openSlot ? signatureModalTitle(openSlot) : ""}
            onSave={saveModalSignature}
            onCancel={() => {
              setOpenSlot(null);
              onDrawingChange?.(false);
            }}
          />
        </View>
      ) : null}

      {status ? (
        <Text style={[typography.subtitle, { marginTop: space.sm }]} testID="delivery-submit-status">
          {status}
        </Text>
      ) : null}

      {deliverHint ? (
        <Text style={[typography.subtitle, { marginTop: space.sm }]} testID="confirm-delivered-hint">
          {deliverHint}
        </Text>
      ) : null}

      {!delivered && showSignatureUi ? (
        <PrimaryButton
          title={submitting ? (status || "Memproses...") : "Konfirmasi Terkirim"}
          onPress={openConfirm}
          busy={submitting || busy}
          disabled={!deliverEnabled || submitting}
          testID="confirm-delivered-button"
          style={{ marginTop: space.md }}
        />
      ) : !delivered ? (
        <Text style={[typography.subtitle, { marginTop: space.md }]} testID="confirm-delivered-locked">
          Minimal {MIN_DELIVERY_PROOFS} foto bukti sebelum tanda tangan dan Konfirmasi Terkirim.
        </Text>
      ) : (
        <View style={[ui.chip, { backgroundColor: colors.okBg, marginTop: space.md }]} testID="delivery-complete-chip">
          <Text style={[ui.chipText, { color: colors.ok }]}>Sudah Terkirim</Text>
        </View>
      )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  proofGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 8,
  },
  proofThumb: {
    width: 88,
    height: 88,
    borderRadius: 10,
    backgroundColor: colors.bg,
    overflow: "hidden",
    alignItems: "center",
    justifyContent: "center",
  },
  proofImg: { width: "100%", height: "100%" },
  sigPreview: {
    width: 160,
    height: 64,
    marginTop: 8,
    borderRadius: 8,
    backgroundColor: "#FDFAF3",
    borderWidth: 1,
    borderColor: colors.line,
  },
});
