import React from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  ViewStyle,
} from "react-native";
import { colors, space, typography, ui } from "../theme";

export function LoadingState({ label = "Memuat…" }: { label?: string }) {
  return (
    <View style={styles.center} testID="loading-state">
      <ActivityIndicator size="large" color={colors.primary} />
      <Text style={[typography.subtitle, { marginTop: space.sm }]}>{label}</Text>
    </View>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <View style={ui.empty} testID="empty-state">
      <Text style={ui.emptyText}>{message}</Text>
    </View>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <View style={[ui.pad, styles.center]} testID="error-state">
      <View style={ui.errorBox}>
        <Text style={ui.errorText}>{message}</Text>
      </View>
      {onRetry ? (
        <Pressable style={[ui.btnGhost, { marginTop: space.md, minWidth: 160 }]} onPress={onRetry}>
          <Text style={ui.btnGhostText}>Coba lagi</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

export function PrimaryButton({
  title,
  onPress,
  busy,
  disabled,
  style,
  testID,
}: {
  title: string;
  onPress: () => void;
  busy?: boolean;
  disabled?: boolean;
  style?: ViewStyle;
  testID?: string;
}) {
  const inactive = disabled || busy;
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      disabled={inactive}
      onPress={onPress}
      style={[ui.btnPrimary, inactive && { opacity: 0.55 }, style]}
    >
      {busy ? (
        <ActivityIndicator color="#fff" />
      ) : (
        <Text style={ui.btnPrimaryText}>{title}</Text>
      )}
    </Pressable>
  );
}

export function GhostButton({
  title,
  onPress,
  disabled,
  style,
  testID,
}: {
  title: string;
  onPress: () => void;
  disabled?: boolean;
  style?: ViewStyle;
  testID?: string;
}) {
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={[ui.btnGhost, disabled && { opacity: 0.5 }, style]}
    >
      <Text style={ui.btnGhostText}>{title}</Text>
    </Pressable>
  );
}

export function QtyField({
  label,
  value,
  onChange,
  unit = "pcs",
  editable = true,
  placeholder = "0",
  testID,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  unit?: string;
  editable?: boolean;
  placeholder?: string;
  testID?: string;
}) {
  return (
    <View style={{ marginTop: space.sm }}>
      <Text style={typography.label}>{label}</Text>
      <View style={styles.qtyRow}>
        <TextInput
          testID={testID}
          value={value}
          onChangeText={(t) => onChange(t.replace(/[^\d]/g, ""))}
          keyboardType="number-pad"
          editable={editable}
          style={[ui.input, { flex: 1 }, !editable && { opacity: 0.6 }]}
          placeholder={placeholder}
          placeholderTextColor={colors.muted}
        />
        <Text style={[typography.mono, { marginLeft: space.sm }]}>{unit}</Text>
      </View>
    </View>
  );
}

export function MetricStrip({
  target,
  done,
  remain,
  status,
  showRemain = true,
}: {
  target: number | string;
  done: number | string;
  remain: number | string;
  status: string;
  showRemain?: boolean;
}) {
  return (
    <View style={styles.metrics} testID="metric-strip">
      <Metric label="Target" value={target} />
      <Metric label="Selesai" value={done} />
      {showRemain ? <Metric label="Sisa" value={remain} /> : null}
      <Metric label="Status" value={status} />
    </View>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <View style={styles.metric}>
      <Text style={typography.label}>{label}</Text>
      <Text style={typography.mono}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: space.lg,
  },
  qtyRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 6,
  },
  metrics: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginVertical: space.sm,
  },
  metric: {
    minWidth: "45%",
    flexGrow: 1,
    backgroundColor: colors.bg,
    borderRadius: 10,
    padding: space.sm,
  },
});
