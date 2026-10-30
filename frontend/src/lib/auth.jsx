import { createContext, useContext, useEffect, useState, useCallback, useRef } from "react";
import api from "./api";
import { loginThenLoadUser } from "./authSession";
import { clearCashBankWorkspaceTabs } from "./cashBankWorkspace";
import { clearCrmDailyDraftsForUser } from "./crmDailyReport";
import { clearGlobalWorkspaceTabs } from "./globalWorkspace";
import { clearMobileOpsSessionForUser } from "./mobileOpsSession";
import { clearProductionSummaryCache } from "./productionSummaryCache";
import { clearPoListReturnForUser, storageUserKey } from "./poListReturn";
import { clearSalesPwaPoListState } from "./salesPwaPoListState";
import { clearSalesInvoiceListCache } from "./salesInvoiceListCache";
import { clearSalesOrderListCache } from "./salesOrderListCache";
import { clearStockSaatIniPageCache } from "./stockSaatIniPageCache";
import { clearAllSmartListCaches } from "./smartListCaches";
import { clearSalesWorkspaceTabs } from "./salesWorkspace";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const accessFingerprint = useRef("");

  const refresh = useCallback(async () => {
    try {
      const me = await api.me();
      const fingerprint = JSON.stringify([me.id, me.role, me.permissions, me.branch_ids, me.primary_branch_id]);
      if (accessFingerprint.current && accessFingerprint.current !== fingerprint) {
        clearAllSmartListCaches();
        clearSalesInvoiceListCache();
        clearSalesOrderListCache();
        clearProductionSummaryCache();
        clearStockSaatIniPageCache();
      }
      accessFingerprint.current = fingerprint;
      setUser(me);
    } catch (err) {
      if (err.response?.status === 401 || err.response?.status === 403) setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const onFocus = () => { if (document.visibilityState === "visible") refresh(); };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refresh]);

  const login = async (email, password) => {
    try {
      const me = await loginThenLoadUser(
        () => api.login(email, password),
        () => api.me(),
      );
      setUser(me);
      return me;
    } catch (err) {
      setUser(null);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const logout = async () => {
    const previous = user;
    try {
      await api.logout();
    } finally {
      clearGlobalWorkspaceTabs(previous);
      clearGlobalWorkspaceTabs(null);
      clearCashBankWorkspaceTabs(previous);
      clearCashBankWorkspaceTabs(null);
      clearSalesWorkspaceTabs(previous);
      clearSalesWorkspaceTabs(null);
      clearCrmDailyDraftsForUser(previous);
      clearCrmDailyDraftsForUser(null);
      clearSalesInvoiceListCache();
      clearSalesOrderListCache();
      clearStockSaatIniPageCache();
      clearAllSmartListCaches();
      clearSalesPwaPoListState();
      clearPoListReturnForUser(previous);
      clearMobileOpsSessionForUser(storageUserKey(previous));
      clearProductionSummaryCache();
      setUser(null);
    }
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh, hasPerm: (p) => (user?.permissions || []).includes(p) }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
