import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { eternalApi, MeUser } from "../api/eternal";
import { clearStoredToken, getStoredToken, setStoredToken } from "./session";
import { makeHasPerm, PermCheck } from "../utils/permissions";

type AuthState = {
  ready: boolean;
  user: MeUser | null;
  token: string | null;
  hasPerm: PermCheck;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<MeUser | null>(null);
  const [token, setToken] = useState<string | null>(null);

  const bootstrap = useCallback(async () => {
    try {
      const stored = await getStoredToken();
      if (!stored) {
        setUser(null);
        setToken(null);
        return;
      }
      const me = await eternalApi.me(stored);
      setToken(stored);
      setUser(me);
    } catch {
      await clearStoredToken();
      setUser(null);
      setToken(null);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await eternalApi.login(email.trim().toLowerCase(), password);
    if (!res.access_token) {
      throw new Error("Server tidak mengembalikan token mobile. Perbarui backend.");
    }
    await setStoredToken(res.access_token);
    setToken(res.access_token);
    const me = await eternalApi.me(res.access_token);
    setUser(me);
  }, []);

  const logout = useCallback(async () => {
    try {
      await eternalApi.logout();
    } catch {
      /* ignore network logout errors */
    }
    await clearStoredToken();
    setToken(null);
    setUser(null);
  }, []);

  const refreshMe = useCallback(async () => {
    const me = await eternalApi.me();
    setUser(me);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      ready,
      user,
      token,
      hasPerm: makeHasPerm(user?.permissions),
      login,
      logout,
      refreshMe,
    }),
    [ready, user, token, login, logout, refreshMe]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
