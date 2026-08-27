import React, { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Redirect, router } from "expo-router";
import { useAuth } from "../src/auth/AuthContext";
import { PrimaryButton } from "../src/components/Ui";
import { colors, space, typography, ui } from "../src/theme";
import { apiErrorMessage } from "../src/utils/format";
import { getApiBaseUrl } from "../src/utils/config";

export default function LoginScreen() {
  const { user, ready, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  if (ready && user) return <Redirect href="/(app)/schedule" />;

  const onSubmit = async () => {
    setErr("");
    if (!email.trim() || !password) {
      setErr("Email dan kata sandi wajib diisi.");
      return;
    }
    setBusy(true);
    try {
      await login(email, password);
      router.replace("/(app)/schedule");
    } catch (e) {
      setErr(apiErrorMessage(e, "Email atau kata sandi salah"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={ui.screen}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <View style={styles.hero}>
        <Text style={styles.brand}>ETERNAL</Text>
        <Text style={styles.tag}>Packaging · Kerja Produksi</Text>
      </View>

      <View style={styles.form}>
        <Text style={typography.title}>Masuk</Text>
        <Text style={[typography.subtitle, { marginBottom: space.md }]}>
          Gunakan akun Eternal yang sama dengan web.
        </Text>

        <Text style={typography.label}>Email</Text>
        <TextInput
          testID="login-email"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          value={email}
          onChangeText={setEmail}
          style={[ui.input, { marginBottom: space.md, marginTop: 6 }]}
          placeholder="nama@perusahaan.com"
          placeholderTextColor={colors.muted}
        />

        <Text style={typography.label}>Kata sandi</Text>
        <TextInput
          testID="login-password"
          secureTextEntry
          value={password}
          onChangeText={setPassword}
          style={[ui.input, { marginBottom: space.md, marginTop: 6 }]}
          placeholder="••••••••"
          placeholderTextColor={colors.muted}
          onSubmitEditing={onSubmit}
        />

        {err ? (
          <View style={[ui.errorBox, { marginBottom: space.md }]}>
            <Text style={ui.errorText}>{err}</Text>
          </View>
        ) : null}

        <PrimaryButton title="Masuk" onPress={onSubmit} busy={busy} testID="login-submit" />

        <Text style={styles.apiHint}>API: {getApiBaseUrl()}</Text>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  hero: {
    backgroundColor: colors.header,
    paddingTop: 72,
    paddingBottom: 36,
    paddingHorizontal: space.lg,
  },
  brand: {
    fontSize: 36,
    fontWeight: "800",
    color: "#fff",
    letterSpacing: 2,
  },
  tag: {
    marginTop: 8,
    color: "#94A3B8",
    fontSize: 15,
  },
  form: {
    flex: 1,
    padding: space.lg,
    marginTop: -12,
    backgroundColor: colors.bg,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
  },
  apiHint: {
    marginTop: space.lg,
    fontSize: 12,
    color: colors.muted,
    textAlign: "center",
  },
});
