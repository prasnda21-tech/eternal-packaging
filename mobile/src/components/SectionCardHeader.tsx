import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { cardHeader, space } from "../theme";

export type CardTone = keyof typeof cardHeader;

export function workContextHeader(division?: string): { tone: CardTone; title: string } {
  switch (division) {
    case "screener":
      return { tone: "screener", title: "Screener" };
    case "helper":
      return { tone: "helper", title: "Persiapan Cup" };
    case "operator":
      return { tone: "operator", title: "Cetak" };
    case "packing":
      return { tone: "packing", title: "Packing" };
    case "loading":
      return { tone: "loading", title: "Loading" };
    case "terkirim":
      return { tone: "delivery", title: "Pengiriman" };
    case "sample_upload":
      return { tone: "sample", title: "Upload Sampel Cetak" };
    case "design_approve":
      return { tone: "designApprove", title: "Approve Design" };
    default:
      return { tone: "generic", title: "Tugas" };
  }
}

export function SectionCardHeader({
  tone,
  title,
}: {
  tone: CardTone;
  title: string;
}) {
  const bg = cardHeader[tone] || cardHeader.generic;
  return (
    <View style={[styles.header, { backgroundColor: bg }]} accessibilityRole="header">
      <View style={styles.mark} />
      <Text style={styles.title} numberOfLines={1}>
        {title}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    minHeight: 44,
    paddingHorizontal: space.md,
    paddingVertical: 10,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  mark: {
    width: 4,
    height: 16,
    borderRadius: 2,
    backgroundColor: "rgba(255,255,255,0.85)",
  },
  title: {
    flex: 1,
    color: "#FFFFFF",
    fontSize: 16,
    fontWeight: "700",
  },
});
