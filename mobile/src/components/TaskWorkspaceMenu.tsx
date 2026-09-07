import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { cardHeader, colors, space } from "../theme";
import { workContextHeader } from "./SectionCardHeader";
import type { TaskWorkspace } from "../utils/taskWorkspaces";

export default function TaskWorkspaceMenu({
  workspaces,
  selectedId,
  onSelect,
}: {
  workspaces: TaskWorkspace[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  if (!workspaces.length) return null;
  return (
    <View testID="task-workspace-menu" style={{ marginBottom: space.sm }}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator
        contentContainerStyle={styles.row}
      >
        {workspaces.map((w) => {
          const header = workContextHeader(w.id);
          const active = selectedId === w.id;
          const tone = cardHeader[header.tone] || cardHeader.generic;
          return (
            <Pressable
              key={w.id}
              testID={`task-workspace-${w.id}`}
              onPress={() => onSelect(w.id)}
              style={[
                styles.chip,
                { borderColor: tone },
                active ? { backgroundColor: tone } : { backgroundColor: colors.surface },
              ]}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
              accessibilityLabel={w.label}
            >
              <Text
                style={[styles.text, active ? styles.textActive : { color: tone }]}
                numberOfLines={1}
              >
                {w.label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    gap: 8,
    paddingVertical: 2,
    paddingRight: space.md,
  },
  chip: {
    minHeight: 44,
    paddingHorizontal: 14,
    borderRadius: 10,
    borderWidth: 2,
    justifyContent: "center",
    maxWidth: 220,
  },
  text: {
    fontWeight: "800",
    fontSize: 14,
  },
  textActive: {
    color: "#FFFFFF",
  },
});
