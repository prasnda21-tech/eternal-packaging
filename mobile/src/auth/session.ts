const TOKEN_KEY = "eternal_access_token";

/** SecureStore is preferred; AsyncStorage is a rare fallback (e.g. some web builds). */
async function getSecureStore() {
  try {
    return await import("expo-secure-store");
  } catch {
    return null;
  }
}

async function getAsyncStorage() {
  try {
    return await import("@react-native-async-storage/async-storage");
  } catch {
    return null;
  }
}

export async function getStoredToken(): Promise<string | null> {
  const secure = await getSecureStore();
  if (secure) {
    try {
      return await secure.getItemAsync(TOKEN_KEY);
    } catch {
      /* fall through */
    }
  }
  const asyncStorage = await getAsyncStorage();
  if (asyncStorage) {
    return asyncStorage.default.getItem(TOKEN_KEY);
  }
  return null;
}

export async function setStoredToken(token: string): Promise<void> {
  const secure = await getSecureStore();
  if (secure) {
    try {
      await secure.setItemAsync(TOKEN_KEY, token);
      return;
    } catch {
      /* fall through */
    }
  }
  const asyncStorage = await getAsyncStorage();
  if (asyncStorage) {
    await asyncStorage.default.setItem(TOKEN_KEY, token);
  }
}

export async function clearStoredToken(): Promise<void> {
  const secure = await getSecureStore();
  if (secure) {
    try {
      await secure.deleteItemAsync(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  }
  const asyncStorage = await getAsyncStorage();
  if (asyncStorage) {
    await asyncStorage.default.removeItem(TOKEN_KEY);
  }
}
