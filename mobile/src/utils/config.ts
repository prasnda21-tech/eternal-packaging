/**
 * Runtime config. Never put secrets here — only public API origin.
 */
import Constants from "expo-constants";

function trimSlash(url: string) {
  return url.replace(/\/+$/, "");
}

export function getApiBaseUrl(): string {
  const fromEnv = (process.env.EXPO_PUBLIC_API_URL || "").trim();
  if (fromEnv) return trimSlash(fromEnv);
  const extra = Constants.expoConfig?.extra as { apiUrl?: string } | undefined;
  if (extra?.apiUrl) return trimSlash(String(extra.apiUrl));
  return "http://localhost:8000";
}

export function getApiRoot(): string {
  return `${getApiBaseUrl()}/api`;
}
