import React from "react";
import { Redirect, Stack } from "expo-router";
import { useAuth } from "../../src/auth/AuthContext";
import { LoadingState } from "../../src/components/Ui";
import { View } from "react-native";
import { colors, ui } from "../../src/theme";

export default function AppGroupLayout() {
  const { ready, user } = useAuth();

  if (!ready) {
    return (
      <View style={ui.screen}>
        <LoadingState />
      </View>
    );
  }
  if (!user) return <Redirect href="/login" />;

  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: colors.header },
        headerTintColor: "#fff",
        headerTitleStyle: { fontWeight: "700" },
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      <Stack.Screen name="schedule" options={{ title: "Jadwal Produksi" }} />
      <Stack.Screen name="po/[id]" options={{ title: "Pekerjaan PO" }} />
    </Stack>
  );
}
