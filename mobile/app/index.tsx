import { Redirect } from "expo-router";
import { useAuth } from "../src/auth/AuthContext";
import { LoadingState } from "../src/components/Ui";
import { View } from "react-native";
import { ui } from "../src/theme";

export default function Index() {
  const { ready, user } = useAuth();
  if (!ready) {
    return (
      <View style={ui.screen}>
        <LoadingState label="Menyiapkan Eternal…" />
      </View>
    );
  }
  if (!user) return <Redirect href="/login" />;
  return <Redirect href="/(app)/schedule" />;
}
