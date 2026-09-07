import React, { useEffect, useState } from "react";
import {
  Dimensions,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { colors, space, typography, ui } from "../theme";
import {
  canDismissSignatureModal,
  emptySignatureCannotSave,
} from "../utils/signatureCapture";
import { GhostButton, PrimaryButton } from "./Ui";
import SignaturePad from "./SignaturePad";

export default function SignatureCaptureModal({
  visible,
  title,
  onSave,
  onCancel,
}: {
  visible: boolean;
  title: string;
  onSave: (dataUrl: string) => void;
  onCancel: () => void;
}) {
  const [padKey, setPadKey] = useState(0);
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState("");
  const canvasH = Math.max(280, Math.round(Dimensions.get("window").height * 0.58));

  useEffect(() => {
    if (!visible) return;
    setDraft(null);
    setError("");
    setPadKey((k) => k + 1);
  }, [visible]);

  const tryDismiss = () => {
    if (!canDismissSignatureModal(!!draft)) {
      setError("Tekan Batal untuk membuang coretan, atau Simpan.");
      return;
    }
    onCancel();
  };

  const onClear = () => {
    setDraft(null);
    setError("");
    setPadKey((k) => k + 1);
  };

  const onKeep = () => {
    if (emptySignatureCannotSave(draft) || !draft) {
      setError("Tanda tangan masih kosong.");
      return;
    }
    onSave(draft);
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="fullScreen"
      onRequestClose={tryDismiss}
    >
      <SafeAreaView style={styles.safe} edges={["top", "bottom", "left", "right"]}>
        <View style={styles.head}>
          <Text style={typography.title} testID="signature-modal-title" numberOfLines={2}>
            {title}
          </Text>
          <Pressable onPress={onCancel} hitSlop={8} testID="signature-modal-close">
            <Text style={styles.close}>Batal</Text>
          </Pressable>
        </View>

        <View style={styles.canvasWrap} testID="signature-modal">
          <SignaturePad
            key={padKey}
            testID="signature-modal-pad"
            height={canvasH}
            onChange={setDraft}
            showClear={false}
          />
        </View>

        {error ? (
          <View style={[ui.errorBox, { marginHorizontal: space.md }]}>
            <Text style={ui.errorText} testID="signature-modal-error">
              {error}
            </Text>
          </View>
        ) : null}

        <View style={styles.actions}>
          <GhostButton title="Hapus" onPress={onClear} testID="signature-modal-clear" style={styles.btn} />
          <GhostButton title="Batal" onPress={onCancel} testID="signature-modal-cancel" style={styles.btn} />
          <PrimaryButton title="Simpan" onPress={onKeep} testID="signature-modal-save" style={styles.btn} />
        </View>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  head: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
    gap: 12,
  },
  close: {
    color: colors.primary,
    fontWeight: "700",
    fontSize: 16,
  },
  canvasWrap: {
    flex: 1,
    paddingHorizontal: space.md,
    paddingTop: space.md,
  },
  actions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    padding: space.md,
  },
  btn: {
    flexGrow: 1,
    minWidth: 100,
  },
});
