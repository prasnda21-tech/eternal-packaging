import { StyleSheet } from "react-native";

/** Factory-floor operational palette — clean, high contrast, no purple glow. */
export const colors = {
  bg: "#F1F5F9",
  surface: "#FFFFFF",
  ink: "#0F172A",
  muted: "#64748B",
  line: "#E2E8F0",
  primary: "#0F766E",
  primaryInk: "#FFFFFF",
  danger: "#B91C1C",
  dangerBg: "#FEF2F2",
  warn: "#B45309",
  warnBg: "#FFFBEB",
  ok: "#15803D",
  okBg: "#F0FDF4",
  header: "#0F172A",
};

/** Solid card-header tones — dark enough for white title text. Not status colors. */
export const cardHeader = {
  po: "#6D28D9",
  design: "#1D4ED8",
  designApprove: "#7C3AED",
  customer: "#15803D",
  item: "#C2410C",
  screener: "#0E7490",
  helper: "#0F766E",
  operator: "#C2410C",
  sample: "#B45309",
  packing: "#0D9488",
  loading: "#4338CA",
  delivery: "#0369A1",
  generic: "#334155",
} as const;

export const space = {
  xs: 6,
  sm: 10,
  md: 16,
  lg: 24,
  xl: 32,
};

export const typography = StyleSheet.create({
  brand: {
    fontSize: 28,
    fontWeight: "800",
    letterSpacing: 0.5,
    color: colors.ink,
  },
  title: {
    fontSize: 22,
    fontWeight: "700",
    color: colors.ink,
  },
  subtitle: {
    fontSize: 15,
    color: colors.muted,
    lineHeight: 22,
  },
  label: {
    fontSize: 13,
    fontWeight: "600",
    color: colors.muted,
    textTransform: "uppercase",
    letterSpacing: 0.6,
  },
  body: {
    fontSize: 16,
    color: colors.ink,
    lineHeight: 22,
  },
  mono: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.ink,
    fontVariant: ["tabular-nums"],
  },
});

export const ui = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  pad: {
    paddingHorizontal: space.md,
    paddingVertical: space.md,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.line,
    padding: space.md,
    marginBottom: space.sm,
  },
  cardFlush: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.line,
    marginBottom: space.sm,
    overflow: "hidden",
  },
  cardBody: {
    padding: space.md,
  },
  btnPrimary: {
    backgroundColor: colors.primary,
    minHeight: 52,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: space.lg,
  },
  btnPrimaryText: {
    color: colors.primaryInk,
    fontSize: 17,
    fontWeight: "700",
  },
  btnGhost: {
    minHeight: 48,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.line,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: space.md,
    backgroundColor: colors.surface,
  },
  btnGhostText: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "600",
  },
  btnDanger: {
    backgroundColor: colors.danger,
    minHeight: 52,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: space.lg,
  },
  input: {
    minHeight: 52,
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: 12,
    paddingHorizontal: space.md,
    fontSize: 16,
    color: colors.ink,
    backgroundColor: colors.surface,
  },
  errorBox: {
    backgroundColor: colors.dangerBg,
    borderRadius: 10,
    padding: space.sm,
    borderWidth: 1,
    borderColor: "#FECACA",
  },
  errorText: {
    color: colors.danger,
    fontSize: 14,
  },
  empty: {
    alignItems: "center",
    paddingVertical: space.xl,
    paddingHorizontal: space.lg,
  },
  emptyText: {
    color: colors.muted,
    fontSize: 16,
    textAlign: "center",
    lineHeight: 24,
  },
  chip: {
    alignSelf: "flex-start",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 8,
    backgroundColor: colors.bg,
  },
  chipText: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.ink,
  },
});
